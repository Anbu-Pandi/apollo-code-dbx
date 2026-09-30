import hashlib
import io
import re
import threading
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable, WriterProperties, write_deltalake

VERSION = 'v2'
TABLE = 'cdx_research_daily_cache'
_LOCK = threading.RLock()
SCHEMA = pa.schema([(name, pa.string()) for name in ('cache_key', 'asof_date', 'payload', 'fingerprint', 'saved_at')])


def storage_key(key):

    return VERSION + '_' + re.sub(r'[^A-Za-z0-9_-]', '_', key)[:80] + '_' + hashlib.sha256(key.encode()).hexdigest()[:12]


def ranges(dates, max_days=60):
    result = []
    for day in sorted(set(pd.Timestamp(x).normalize() for x in dates)):
        if result and day == result[-1][1] + pd.Timedelta(days=1) and (day - result[-1][0]).days < max_days:
            result[-1] = result[-1][0], day
        else:
            result.append((day, day))
    return result


def fingerprint(frame):
    return hashlib.sha256(frame.to_json(orient='split', date_format='iso').encode()).hexdigest()


class DailyCache:
    def __init__(self, root=None):
        if root is None:
            from data_access.spark.azuredb import AzureDB
            root = AzureDB.get_local_dir()
        if not root:
            raise RuntimeError('AzureDB local directory is not configured')
        self.path = Path(root) / TABLE
        self.activity = []

    def read(self, key, start, end, columns=None):
        with _LOCK:
            if not (self.path / '_delta_log').exists():
                return {}
            predicate = ((ds.field('cache_key') == storage_key(key)) &
                         (ds.field('asof_date') >= str(pd.Timestamp(start).date())) &
                         (ds.field('asof_date') <= str(pd.Timestamp(end).date())))
            rows = DeltaTable(str(self.path)).to_pyarrow_dataset().scanner(
                columns=columns, filter=predicate, batch_size=16, batch_readahead=1,
                fragment_readahead=1, use_threads=False).to_table().to_pandas()
            return {r['asof_date']: r for r in rows.to_dict('records')}

    def save(self, key, frames):
        if not frames:
            return
        records = [{'cache_key': storage_key(key), 'asof_date': str(pd.Timestamp(day).date()),
                    'payload': frame.to_json(orient='table', date_format='iso', index=False, double_precision=15),
                    'fingerprint': sig, 'saved_at': pd.Timestamp.now(tz='UTC').isoformat()}
                   for day, (frame, sig) in frames.items()]
        source = pa.Table.from_pylist(records, schema=SCHEMA)
        with _LOCK:
            if not (self.path / '_delta_log').exists():
                write_deltalake(str(self.path), source, partition_by=['cache_key'],
                                min_rows_per_group=1, max_rows_per_group=1)
            else:
                days = [record['asof_date'] for record in records]
                predicate = (f"t.cache_key = '{storage_key(key)}' AND s.cache_key = t.cache_key "
                             f"AND t.asof_date >= '{min(days)}' AND t.asof_date <= '{max(days)}' "
                             "AND s.asof_date = t.asof_date")
                DeltaTable(str(self.path)).merge(source, predicate=predicate,
                    source_alias='s', target_alias='t', writer_properties=WriterProperties(max_row_group_size=1)
                ).when_matched_update_all().when_not_matched_insert_all().execute()

    @staticmethod
    def unpack(rows):
        frames = [pd.read_json(io.StringIO(row['payload']), orient='table') for _, row in sorted(rows.items())]
        frames = [frame for frame in frames if not frame.empty]
        for frame in frames:
            for column in frame.select_dtypes('object'):
                if pd.api.types.is_string_dtype(frame[column]):
                    frame[column] = frame[column].astype('string[pyarrow]')
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def load(self, key, start, end, loader, rebuild=False, transform=None):
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        existing = {} if rebuild else self.read(key, start, end, columns=['asof_date'])
        all_days = pd.date_range(start, end)
        refresh_start = max(start, end - pd.offsets.BDay(4))
        dirty = [day for day in all_days if str(day.date()) not in existing or day >= refresh_start]
        for lo, hi in ranges(dirty):
            updates = {}
            fresh = loader(str(lo.date()), str(hi.date()))
            if not fresh.empty:
                fresh = fresh.copy()
                fresh['asof_date'] = pd.to_datetime(fresh.asof_date).dt.normalize()
            groups = {day: group.reset_index(drop=True) for day, group in fresh.groupby('asof_date')} if not fresh.empty else {}
            for day in pd.date_range(lo, hi):
                one = groups.get(day, pd.DataFrame())
                updates[day] = one, fingerprint(one)
            self.save(key, updates)
            del fresh, groups, updates, one
        frames = []
        for lo, hi in ranges(all_days):
            frame = self.unpack(self.read(key, lo, hi))
            if not frame.empty:
                frames.append(transform(frame) if transform else frame)
        self.activity.append({'stage': key, 'reused_dates': len(all_days) - len(dirty), 'refreshed_dates': len(dirty)})
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def calculate(self, key, frame, function, rebuild=False):
        if frame.empty:
            return pd.DataFrame()
        grouped = frame.groupby('asof_date')
        days = sorted(pd.Timestamp(day) for day in grouped.groups)
        existing = {} if rebuild else self.read(key, min(days), max(days), columns=['asof_date', 'fingerprint'])
        updates = {}
        for day, group in grouped:
            day = pd.Timestamp(day)
            group = group.reset_index(drop=True)
            sig = fingerprint(group)
            if existing.get(str(day.date()), {}).get('fingerprint') != sig:
                value = function(group)
                result = value.to_frame().T if isinstance(value, pd.Series) else value.copy()
                result['asof_date'] = day
                updates[day] = result.reset_index(drop=True), sig
        self.save(key, updates)
        wanted = {str(day.date()) for day in days}
        rows = {day: row for day, row in self.read(key, min(days), max(days)).items() if day in wanted}
        self.activity.append({'stage': key, 'reused_dates': len(grouped) - len(updates), 'refreshed_dates': len(updates)})
        return self.unpack(rows).set_index('asof_date').sort_index()

    def rolling(self, key, frame, window, function, rebuild=False):
        if frame.empty:
            return function(frame)
        existing = {} if rebuild else self.read(key, frame.index.min(), frame.index.max())
        hashes = pd.util.hash_pandas_object(frame, index=True).to_numpy()
        columns = str(list(frame.columns)).encode()
        signatures = {pd.Timestamp(day): hashlib.sha256(columns + hashes[max(0, i-window+1):i+1].tobytes()).hexdigest()
                      for i, day in enumerate(frame.index)}
        dirty = [i for i, day in enumerate(frame.index)
                 if existing.get(str(pd.Timestamp(day).date()), {}).get('fingerprint') != signatures[pd.Timestamp(day)]]
        updates = {}
        if dirty:

            lo, hi = max(0, min(dirty) - window + 1), max(dirty) + 1
            calculated = function(frame.iloc[lo:hi])
            for i in dirty:
                day = pd.Timestamp(frame.index[i])
                result = calculated.loc[[day]].copy()
                result.index.name = 'asof_date'
                updates[day] = result.reset_index(), signatures[day]
        self.save(key, updates)
        rows = self.read(key, frame.index.min(), frame.index.max())
        self.activity.append({'stage': key, 'reused_dates': len(frame) - len(dirty), 'refreshed_dates': len(dirty)})
        return self.unpack(rows).set_index('asof_date').reindex(frame.index)
