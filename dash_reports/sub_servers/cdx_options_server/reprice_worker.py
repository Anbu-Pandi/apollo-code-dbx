import logging
import os
import time

import pandas as pd

from dash_reports.cdx_options.data.data_loader import CDXOptionsDataLoader
from dash_reports.cdx_options.data.pricing_grid import get_grid_cache_dir

OUT_DIRNAME = 'reprice_out'


def reprice_output_dir(todt):
    return os.path.join(get_grid_cache_dir(todt), OUT_DIRNAME)


def _parquet_safe(df):
    out = df.copy()
    for col in out.select_dtypes(include=['object']).columns:
        try:
            converted = pd.to_datetime(out[col], format='mixed', dayfirst=False, errors='coerce')
            if converted.notna().sum() > 0.5 * out[col].notna().sum():
                out[col] = converted
                continue
        except Exception:
            pass
        out[col] = out[col].astype(str)
    return out


def _write_frame(df, path):
    from data_access.storage import atomic_file
    atomic_file.write_parquet(_parquet_safe(df), path, index=False)


def run_reprice(settings):
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    t0 = time.time()
    ldr = CDXOptionsDataLoader(tickers=settings.get('tickers'), todt=settings.get('todt'),
                               lookback=settings.get('lookback', '6m'))
    ldr.mark_pos = settings.get('mark_pos', True)
    ldr.mark_time_override = settings.get('mark_time', '')
    ldr.thresh_time_override = settings.get('thresh_time', '4h')
    ldr._grid_repair_backoff.update(settings.get('grid_repair_backoff', {}))
    restored_at = time.time()
    logging.info('Reprice worker restore elapsed_s=%.3f', restored_at - t0)
    ldr.refresh_heavy_from_loaded_state(skip_prewarm=True)
    refreshed_at = time.time()
    logging.info('Reprice worker heavy refresh elapsed_s=%.3f', refreshed_at - restored_at)

    out = reprice_output_dir(ldr.todt)
    os.makedirs(out, exist_ok=True)
    from data_access.storage import atomic_file
    _write_frame(ldr.curr_mark.reset_index(drop=True), os.path.join(out, 'curr_mark.parquet'))
    if not ldr.grid_pos_df.empty:
        _write_frame(ldr.grid_pos_df.reset_index(drop=True), os.path.join(out, 'grid_pos.parquet'))
    if not ldr.sender_mark.empty:
        _write_frame(ldr.sender_mark.reset_index(drop=True), os.path.join(out, 'sender_mark.parquet'))
    if ldr.product_aggs:
        frames = []
        for prod, agg in ldr.product_aggs.items():
            q = agg.quotes.copy()
            q['_agg_product'] = prod
            frames.append(q)
        _write_frame(pd.concat(frames, ignore_index=True), os.path.join(out, 'agg_quotes.parquet'))
    meta = {
        'ok': True,
        'todt': str(ldr.todt),
        'elapsed_s': round(time.time() - t0, 1),
        'restore_s': round(restored_at - t0, 3),
        'refresh_s': round(refreshed_at - restored_at, 3),
        'publish_s': round(time.time() - refreshed_at, 3),
        'grid_repair_backoff': ldr._grid_repair_backoff,
        'spot_generation': getattr(ldr, '_spot_refresh_generation', 0),
        'products': sorted(ldr.curr_mark['product'].unique().tolist()) if not ldr.curr_mark.empty else [],
    }
    atomic_file.write_json(meta, os.path.join(out, 'done.json'))
    logging.info(f'Reprice worker done in {meta["elapsed_s"]}s: {len(meta["products"])} products')
    return meta
