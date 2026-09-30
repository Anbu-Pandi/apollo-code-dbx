import logging
from functools import reduce

import numpy as np
import pandas as pd

from common.utils import sqlutil, dtutil
from data_access.dbtables import TRADEWEB_APP
from data_access.spark.azuredb import AzureDB

GBY_COLS = ['security_qdlr', 'security_strmtyp', 'security_twid', 'security_redidmat']
NUM_COLS = ['security_collateral_br', 'security_collateral_ar', 'security_collateral_bp', 'security_collateral_ap',
            'security_collateral_bss', 'security_collateral_ass', 'security_collateral_bq', 'security_collateral_aq']
SPREAD_COLS = ['security_collateral_br', 'security_collateral_ar', 'security_collateral_bss', 'security_collateral_ass']


def read_live_quotes(day, columns=None, twids=None, sql='SELECT * FROM tradeweb_quoteupd'):
    import pyarrow.dataset as ds
    db = AzureDB()
    try:
        dataset = db.get_delta_table('tradeweb_quoteupd').to_pyarrow_dataset()
        predicate = ds.field('date') == dtutil.todate(day).strftime('%Y%m%d')
        if twids:
            predicate = predicate & ds.field('security_twid').isin(twids)
        scanner = dataset.scanner(columns=columns, filter=predicate, batch_size=8192,
                                  batch_readahead=1, fragment_readahead=1, use_threads=False)
        db.duck_conn.execute("SET memory_limit='256MB'; SET threads=2; SET preserve_insertion_order=false")
        db.duck_conn.register('tradeweb_quoteupd', scanner.to_reader())
        return db.duck_conn.sql(sql).df()
    finally:
        db.duck_conn.close()


def latest_venue_sql(day, live=True):
    aggregate = 'arg_max' if live else 'max_by'
    timestamp = "concat(date, ' ', time)"
    valid = lambda col: f'try_cast({col} AS DOUBLE) > 0'
    values = ', '.join(f'{aggregate}({col}, CASE WHEN {valid(col)} THEN {timestamp} END) AS {col}' for col in NUM_COLS)
    valid_price = ' OR '.join(valid(col) for col in NUM_COLS if not col.endswith('q'))
    keys = ', '.join(GBY_COLS)
    return (f"SELECT {keys}, max(CASE WHEN {valid_price} THEN {timestamp} END) AS quote_time, {values} FROM tradeweb_quoteupd "
            f"WHERE date = '{day:%Y%m%d}' GROUP BY {keys}")


def normalize_snapshot(raw):
    if raw.empty:
        return pd.DataFrame(columns=['security_twid', 'mid', 'asof'])
    data = raw.copy()
    for col in NUM_COLS:
        data[col] = pd.to_numeric(data[col], errors='coerce').replace(-999999, np.nan)
    data[SPREAD_COLS] = data[SPREAD_COLS].where(data[SPREAD_COLS] <= 0, data[SPREAD_COLS] * 100)
    for side in ('bid', 'ask'):
        prefix = 'security_collateral_' + side[0]
        cols = [col for col in NUM_COLS if col.startswith(prefix) and not col.endswith('q')]
        data[side] = data[cols].bfill(axis=1).iloc[:, 0]
    data['mid'] = (data['bid'] + data['ask']) / 2
    data['asof'] = pd.to_datetime(data.quote_time, utc=True)
    data = data.dropna(subset=['asof'])
    latest = data.groupby('security_twid')['asof'].transform('max')
    data = data[data['asof'] >= latest - pd.Timedelta(minutes=60)]
    return data.groupby('security_twid').agg(mid=('mid', 'median'), asof=('asof', 'max')).reset_index()


def get_quote_snapshot(day, use_live=False):
    day = dtutil.todate(day)
    query = latest_venue_sql(day, live=use_live)
    if use_live:
        raw = read_live_quotes(day, GBY_COLS + NUM_COLS + ['date', 'time'], sql=query)
    else:
        raw = sqlutil.readdfsql(query, app=TRADEWEB_APP)
    return normalize_snapshot(raw)


def get_quote_universe(start, end):
    start, end = dtutil.todate(start), dtutil.todate(end)
    today = dtutil.today()
    frames = []
    if start <= today <= end:
        frames.append(read_live_quotes(today, ['security_twid'], sql='SELECT DISTINCT security_twid FROM tradeweb_quoteupd'))
    if start < today:
        frames.append(sqlutil.readdfsql(
            f"SELECT DISTINCT security_twid FROM tradeweb_quoteupd WHERE TO_DATE(date, 'yyyyMMdd') BETWEEN '{start}' AND '{end}'",
            app=TRADEWEB_APP))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=['security_twid'])


def resample_dataframe(df, interval_seconds, gby_cols=None):
    if gby_cols is None:
        gby_cols = GBY_COLS

    if interval_seconds <= 0 or df.empty: return df

    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df['msg_time']):
        df['msg_time'] = pd.to_datetime(df['msg_time'])

    result = []
    for key, group in df.groupby(gby_cols):
        key = (key,) if not isinstance(key, tuple) else key
        resampled = group.set_index('msg_time').resample(f'{interval_seconds}S').last().reset_index()
        for i, col in enumerate(gby_cols):
            resampled[col] = key[i]
        result.append(resampled)

    return pd.concat(result, ignore_index=True)[df.columns] if result else pd.DataFrame(columns=df.columns)


def process_quote_data(df):
    df['redid'] = df['security_redidmat'].str[:9]  # Extract '2I666VCQ6'

    df['maturity'] = pd.to_datetime(
        df['security_redidmat'].str[-8:],  # Extract last 8 characters
        format='%Y%m%d'  # Specify the date format
    )

    if df.empty: return df

    df['msg_time'] = dtutil.ds_tz_convert(pd.to_datetime(df['date'] + ' ' + df['time']), 'UTC', 'UTC')
    df['asof_datetime'] = dtutil.ds_tz_convert(pd.to_datetime(pd.to_numeric(df['security_sutm'], errors='coerce'), unit='s'), 'UTC', 'UTC')

    for col in NUM_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df[SPREAD_COLS] = np.where(df[SPREAD_COLS] > 0, df[SPREAD_COLS] * 100, df[SPREAD_COLS])
    df = df.sort_values(GBY_COLS + ['asof_datetime', 'msg_time'])

    fill_cols = [col for col in df.columns if col not in GBY_COLS]
    df[fill_cols] = df.groupby(['security_qdlr', 'security_strmtyp', 'security_twid'])[fill_cols].transform('ffill')

    df = df.replace(-999999, np.nan)
    df = df.groupby(GBY_COLS + ['asof_datetime', 'msg_time']).last().reset_index()
    df = df.set_index(['asof_datetime', 'msg_time'] + list(reversed(GBY_COLS)))[NUM_COLS]

    col_map = {
        'ask': [x for x in df.columns if x.startswith('security_collateral_a') and not x.endswith('q')],
        'bid': [x for x in df.columns if x.startswith('security_collateral_b') and not x.endswith('q')],
        'ask_qty': [x for x in df.columns if x.startswith('security_collateral_a') and x.endswith('q')],
        'bid_qty': [x for x in df.columns if x.startswith('security_collateral_b') and x.endswith('q')]
    }

    for k, v in col_map.items():
        df[k] = reduce(lambda x, y: x.combine_first(df[y]), v[1:], df[v[0]])

    return df.drop(columns=NUM_COLS).groupby(['security_qdlr', 'security_strmtyp', 'security_twid']).transform('ffill')


def get_cdx_prices(from_date, to_date, use_live=False, resample_freq=None, twids=None, compact=False):
    from_date = dtutil.todate(from_date)
    to_date = dtutil.todate(to_date)

    today = dtutil.today()
    df_live, df_hist = pd.DataFrame(), pd.DataFrame()
    # Live dashboards need only quote fields, not every raw message attribute.
    columns = ', '.join(GBY_COLS + NUM_COLS + ['date', 'time', 'security_sutm']) if compact else '*'
    instrument_filter = ''
    if twids:
        quoted = ', '.join("'" + str(t).replace("'", "''") + "'" for t in twids)
        instrument_filter = f' AND security_twid IN ({quoted})'

    if use_live and from_date <= today <= to_date:
        df_live = read_live_quotes(today, GBY_COLS + NUM_COLS + ['date', 'time', 'security_sutm'] if compact else None,
                                   twids=twids)

    if from_date < today or to_date < today:
        query_to_date = min(to_date, today)
        df_hist = sqlutil.readdfsql(
            f"SELECT {columns} FROM dbo.tradeweb_quoteupd WHERE TO_DATE(date, 'yyyyMMdd') BETWEEN '{from_date}' AND '{query_to_date}'{instrument_filter}",
            app=TRADEWEB_APP)

    df = pd.concat([df_hist, df_live], ignore_index=True)

    if df.empty: return df

    df = process_quote_data(df).reset_index()

    if resample_freq:
        return resample_dataframe(df, resample_freq if (resample_freq or 0) > 0 else 300, GBY_COLS)

    return df


if __name__ == '__main__':
    from common.utils import logutil

    logutil.setuplogger()
    from apo_config.config import config

    config.init('dev')

    df = get_cdx_prices('2025-04-16', '2025-04-16', use_live=True, resample_freq=300)
    logging.info(df)
