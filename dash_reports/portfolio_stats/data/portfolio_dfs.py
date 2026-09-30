import json

import pandas as pd

from common.utils import dtutil
from data_access.storage import SharedFrameCache

try:
    from moritz.PF_positioning_email import generate_master_dfs, create_positioning_dicts
except:
    pass
_shared_cache = None


def _cache():
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = SharedFrameCache.local()
    return _shared_cache


def _walk_frames(value, path=()):
    if isinstance(value, pd.DataFrame):
        yield path, value
        return
    if not isinstance(value, dict):
        raise TypeError(f"portfolio_stats cache expects frames or dicts, got {type(value).__name__} at {path}")
    for key, child in value.items():
        yield from _walk_frames(child, path + (str(key),))


def _write_result(run_date, result):
    manifest = []
    entries = []
    for slot, value in enumerate(result):
        for path, frame in _walk_frames(value):
            item_key = f"{slot}:{json.dumps(path)}"
            entries.append(("portfolio_stats", run_date, item_key, "frame", frame))
            manifest.append({"slot": slot, "path": json.dumps(path), "key": item_key, "slots": len(result)})
    entries.append((
        "portfolio_stats",
        run_date,
        "__manifest__",
        "frames",
        pd.DataFrame(manifest),
    ))
    _cache().write_many(entries)


def _read_result(run_date):
    manifest = _cache().read(
        "portfolio_stats",
        run_date,
        "__manifest__",
        "frames",
        max_age_seconds=60 * 60,
    )
    if manifest is None:
        return None
    rows = manifest.to_dict("records")
    entries = _cache().read_entries("portfolio_stats", run_date)
    result = [None] * int(rows[0]["slots"])
    for row in rows:
        slot = int(row["slot"])
        path = tuple(json.loads(row["path"]))
        frame = entries[(row["key"], "frame")]
        if not path:
            result[slot] = frame
            continue
        if result[slot] is None:
            result[slot] = {}
        target = result[slot]
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = frame
    return tuple(result)


def get_dfs_dicts(run_date):
    run_date = dtutil.today(run_date)
    out = _read_result(run_date)
    if out is None:
        run_date_universe, pfolios_positions_dict, bmarks_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict = \
            create_positioning_dicts(run_date)
        master_df_unstacked, master_dist_df_dict_unstacked = generate_master_dfs(pfolios_positions_dict,
                                                                                 bmarks_positions_dict,
                                                                                 pfolios_stats_dict,
                                                                                 pfolios_dist_dict_dict)
        cols = ['YtW', 'DtW', 'Rtg', 'OAS', 'DtS', 'Twst', 'DD_Cov19', 'DD_Rcssn', 'DD_Stgfltn', 'DD_Infltn']
        cols = [x for x in cols if x in master_df_unstacked.columns]
        master_df_output = master_df_unstacked[cols].drop(['PF/BM', 'BM'], axis=1, level=1).rename(
            columns={'PF-BM': u'ΔBM'})
        out = (pfolios_positions_dict, bmarks_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict, \
               master_df_unstacked, master_dist_df_dict_unstacked, master_df_output)
        _write_result(run_date, out)

    return out


if __name__ == '__main__':
    pfolios_positions_dict, bmarks_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict, \
        master_df_unstacked, master_dist_df_dict_unstacked, master_df_output = get_dfs_dicts(dtutil.today())
    pass
