"""Daily parquet cache for cds_cash_basis.

Run via dash_reports.tasks (scheduled in the morning) — pre-computes the heavy
constituent-level basis panel + EW history + name snapshot for every index, so
the dashboard reads parquet on page load instead of running the full Markit
pipeline against live DB connections.

Cache layout (under <local_cache_dir>/cds_cash_basis/<YYYYMMDD>/):
    <index_key>_<lookback>_panel.parquet      # full constituent×date basis panel
    <index_key>_<lookback>_history.parquet    # EW basis time series
    <index_key>_<lookback>_names.parquet      # snapshot per-constituent table
    summary_<lookback>.parquet                # cross-index snapshot table
"""
import logging

from common.utils import dtutil
from common.utils.jobutil import JobExecutor
from dash_reports.cds_cash_basis.constants import INDEX_KEYS, INDEX_META
from dash_reports.cds_cash_basis.data import data_loader
from dash_reports.vol_tracker_common.cache_utils import CacheManager

_cache = CacheManager(cache_subdir='cds_cash_basis')

DATA_KEYS = ('panel', 'history', 'names')
DEFAULT_LOOKBACKS = ('2y',)  # extend if the UI exposes additional lookbacks
PARALLEL_WORKERS = 3          # concurrent indices during cache build


def load_and_cache(index_key, asof_date, lookback='2y'):
    """Compute the index pack for (asof_date, lookback) and write all parts."""
    pack = data_loader.load_index_pack(asof_date, lookback, index_key)
    _cache.write_cache(pack['panel'], asof_date, f'{index_key}_{lookback}', 'panel')
    _cache.write_cache(pack['history'], asof_date, f'{index_key}_{lookback}', 'history')
    _cache.write_cache(pack['names'], asof_date, f'{index_key}_{lookback}', 'names')
    return pack


def read_cached_pack(index_key, asof_date, lookback='2y'):
    """Return a pack assembled from cache, or None if any part is missing."""
    parts = {}
    for k in DATA_KEYS:
        df = _cache.read_cache(asof_date, f'{index_key}_{lookback}', k)
        if df is None:
            return None
        parts[k] = df

    meta = INDEX_META[index_key]
    return {
        'meta': meta, 'key': index_key,
        'history': parts['history'], 'panel': parts['panel'],
        'names': parts['names'],
        'sectors': data_loader.sector_basis(parts['names']),
        'ew_basis_bps': data_loader.equal_weight_basis(parts['names']),
        'coverage_pct': data_loader._coverage_pct(parts['names']),
    }


def get_cached_or_load(index_key, asof_date, lookback='2y'):
    cached = read_cached_pack(index_key, asof_date, lookback)
    if cached is not None:
        return cached
    return load_and_cache(index_key, asof_date, lookback)


def refresh_all_index_caches(asof_date=None, lookbacks=DEFAULT_LOOKBACKS, workers=PARALLEL_WORKERS):
    """Morning entry point — caches every index × lookback in parallel."""
    asof_date = asof_date or dtutil.dateadd(dtutil.today(), '-1b')
    logging.info(f'[cds_cash_basis] refreshing caches for {asof_date} '
                 f'lookbacks={lookbacks} workers={workers}')
    for lookback in lookbacks:
        funcs = [
            (lambda k=k, lb=lookback: load_and_cache(k, asof_date, lb))
            for k in INDEX_KEYS
        ]
        executor = JobExecutor('thread', max_workers=min(len(funcs), workers))
        results = executor.submitjob(funcs, job_name=f'ccb_{lookback}')
        for k, res in zip(INDEX_KEYS, results):
            if res.get('error'):
                logging.warning(f'  cache failed for {k} ({lookback}): {str(res["error"])[:200]}')
            else:
                logging.info(f'  cached {k} ({lookback}) OK')

        # Cross-index summary (summary table + cross-index history) — relatively cheap
        try:
            bundle = data_loader.build_index_summary(asof_date, lookback)
            _cache.write_cache(bundle['summary'], asof_date, 'summary', lookback)
            _cache.write_cache(bundle['history'], asof_date, 'history_all', lookback)
        except Exception:
            logging.exception(f'  summary cache failed ({lookback})')

    _cache.purge_old(keep_days=7)
    logging.info('[cds_cash_basis] cache refresh complete')
