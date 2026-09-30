import copy
import hashlib
import json
import logging
import itertools
import os
import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import duckdb
import numpy as np
import pandas as pd
import pytz


from common.utils import dtutil
from data_access.storage import atomic_file
from credit_strategy.cdx_bbg_msg.cdx_option_quote_agg import CDXOptionQuoteAgg
from credit_strategy.cdx_bbg_msg.vol_mark import VolMark
from credit_strategy.cdx_bbg_msg.vol_mark_intraday import calc_wt_avg
from data_access.dbtables.market_data.credit import cds_index, cds_index_vol
from data_access.dbtables.flow_data import sdr_db
from data_access.utils import tosqllist
from sdr.dtccpy.credit_utils import _is_px_index
from sdr.dtccpy.cdx_marker.sdr_mark_helpers import SDRMarkHelpers
from sdr.dtccpy.cdx_marker.sdr_cdx_mark import SDRCDXMark
from sdr.dtccpy.cdx_marker.sdr_cdx_opt_pos_mark import SDRCDXOptPosMark
from common.utils.dfutil import tolist
from common.utils.jobutil import JobExecutor
from dash_reports.cdx_options.data.pricing_grid import (
    PricingGrid, get_grid_cache_dir, purge_old_grid_cache, is_price_based,
)
from dash_reports.cdx_options.data.grid_engine import GridPricingEngine
from pricing_wrapper.market.curves.cds_curve import cds_curve_using_dict
from pricing_wrapper.utils.date_funcs import tofindate

def _chart_strike_set(strike):
    values = strike if isinstance(strike, (list, tuple)) else [strike]
    out = set()
    for value in values:
        try:
            out.add(round(float(value), 6))
        except (TypeError, ValueError):
            continue
    return out


def _chart_expiry(expiry):
    ts = pd.to_datetime(expiry, errors='coerce')
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize()


def _mask_product_strike_expiry(df, product, strikes, expiry, strike_col, date_col):
    if not isinstance(df, pd.DataFrame) or df.empty or not strikes or expiry is None:
        return pd.DataFrame()
    if strike_col not in df.columns or date_col not in df.columns:
        return pd.DataFrame()
    work = df
    if 'product' in work.columns:
        work = work.loc[work['product'].eq(product)]
    if work.empty:
        return work
    got = pd.to_numeric(work[strike_col], errors='coerce').round(6)
    dates = pd.to_datetime(work[date_col], errors='coerce').dt.normalize()
    return work.loc[got.isin(strikes) & dates.eq(expiry)]


def _chart_cols(df, cols):
    if df.empty:
        return pd.DataFrame(columns=list(cols))
    have = [col for col in cols if col in df.columns]
    out = df.loc[:, have]
    if 'asof_date_time' in out.columns:
        out = out.sort_values('asof_date_time')
    return out


def expiry_chart_frames(vol_mark_df, curr_mark, sender_mark, trades, product, strike, expiry, plot_cols):
    strikes = _chart_strike_set(strike)
    exp = _chart_expiry(expiry)
    key = (strike, expiry)
    vol_cols = ['asof_date_time', 'vol_mark_id', *list(plot_cols or ()), 'spot_price', 'spot_spread']
    vol = _mask_product_strike_expiry(vol_mark_df, product, strikes, exp, 'strike', 'option_end_date')
    curr = _mask_product_strike_expiry(curr_mark, product, strikes, exp, 'strike', 'option_end_date')
    sender = _mask_product_strike_expiry(sender_mark, product, strikes, exp, 'strike', 'option_end_date')
    raw_trades = _mask_product_strike_expiry(trades, product, strikes, exp, 'strike_price', 'expiration_date')
    trade_frame = pd.DataFrame(columns=[
        'execution_timestamp', 'implied_vol', 'vol_bpd', 'vol_ann', 'notional', 'dissemination_identifier',
    ])
    if not raw_trades.empty:
        trade_frame = pd.DataFrame(index=raw_trades.index)
        for col in ('execution_timestamp', 'implied_vol', 'dissemination_identifier'):
            if col in raw_trades.columns:
                trade_frame[col] = raw_trades[col]
        if 'implied_normal_vol' in raw_trades.columns:
            normal = pd.to_numeric(raw_trades['implied_normal_vol'], errors='coerce')
            trade_frame['vol_bpd'] = normal
            trade_frame['vol_ann'] = normal * (252 ** 0.5)
        if 'notional' in raw_trades.columns:
            trade_frame['notional'] = pd.to_numeric(raw_trades['notional'], errors='coerce') / 1e6
    return {
        'vol_ts_df': {key: _chart_cols(vol, vol_cols)},
        'curr_vol_df': {key: _chart_cols(curr, vol_cols)},
        'sender_vol_df': {key: _chart_cols(sender, [*vol_cols, 'sender_bank'])},
        'trades_df': {key: trade_frame},
    }


def _duckq(query, **tables):
    con = duckdb.connect(':memory:')
    for name, df in tables.items():
        if isinstance(df, pd.DataFrame) and df.columns.empty:
            logging.debug(f'_duckq: skipping 0-column table {name}')
            return pd.DataFrame()
        con.register(name, df)
    return con.sql(query).df()

from dash_reports.cdx_options.constants import DEFAULT_TICKERS

PLOT_COLS = ['vol_bid_ann', 'vol_ask_ann', 'vol_mid_ann']
MARK_ID = 'NOW'

TICKER_TZ = {
    'CDX-NAHY': 'America/New_York',
    'CDX-NAIG': 'America/New_York',
    'ITRAXX-Europe': 'Europe/London',
    'ITRAXX-Xover': 'Europe/London',
}
NY_TZ = pytz.timezone('America/New_York')

MIN_QUOTES_FOR_BOOTSTRAP = 5
_GRID_RETRY_INTERVAL_S = 600
_MISSING_GRID_REBUILD_CYCLES = 2

_DT_COLS = ['asof_date_time', 'execution_timestamp', 'received_date_time']

_MARK_NUMERIC_COLS = [
    'strike', 'payer_premium', 'receiver_premium', 'spot_spread', 'spot_price',
    'implied_vol', 'implied_normal_vol', 'vol_mid_ann', 'vol_bid_ann', 'vol_ask_ann',
    'delta', 'vega_ann', 'forward_price', 'forward_spread', 'option_premium_bps',
    'par_spread_fwd_calc', 'forward_risky_pv01', 'gamma', 'vega', 'theta',
]
_MARK_DT_COLS = ['option_end_date', 'asof_date_time']
_MARK_REQUIRED_COLS = ('product', 'option_end_date', 'strike')
_PUBLISHED_MARK_COLS = (
    'payer_premium', 'receiver_premium', 'delta', 'gamma', 'vega', 'vega_ann',
    'par_spread_spot_calc', 'price_spot_calc', 'risky_pv01_spot_calc',
)

_INT_SENTINELS = frozenset([np.iinfo(np.int64).min, np.iinfo(np.int32).min])
_DUCKDB_DT_COLS = frozenset([
    'asof_date', 'asof_date_time', 'event_timestamp', 'execution_timestamp',
    'expiration_date', 'option_end_date', 'received_date_time', 'trade_date',
])

def _sanitize_mark_dtypes(df):
    for c in _MARK_NUMERIC_COLS:
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors='coerce')
        if s.dtype.kind == 'i':
            s = s.astype('float64')
        mask = s.isin(_INT_SENTINELS) | (s.abs() > 1e18)
        if mask.any():
            s = s.where(~mask)
        df[c] = s
    for c in _MARK_DT_COLS:
        if c in df.columns and not pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = pd.to_datetime(df[c], format='mixed', dayfirst=False, errors='coerce')


def _prepare_mark_frame(df, label, product=None):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    missing = [c for c in _MARK_REQUIRED_COLS if c not in out.columns]
    if missing:
        prefix = f'[{product}] ' if product else ''
        logging.warning(f'{prefix}{label} missing required columns {missing}')
        return pd.DataFrame()
    if product is not None:
        out = out[out['product'] == product].copy()
        if out.empty:
            return out
    _sanitize_mark_dtypes(out)
    out['option_end_date'] = pd.to_datetime(out['option_end_date'], format='mixed', dayfirst=False, errors='coerce')
    out['strike'] = pd.to_numeric(out['strike'], errors='coerce')
    out = out[out['product'].notna() & out['option_end_date'].notna() & out['strike'].notna()].copy()
    return out.reset_index(drop=True)


def _normalize_duckdb_datetime_value(value):
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.tz_localize(None) if value.tzinfo is not None else value
    if isinstance(value, datetime):
        ts = pd.Timestamp(value)
        return ts.tz_localize(None) if ts.tzinfo is not None else ts
    if isinstance(value, date):
        return pd.Timestamp(value)
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return value
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _prepare_duckdb_query_frame(df):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or df.columns.empty:
        return df
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if not (
            col in _DUCKDB_DT_COLS
            or 'date' in col.lower()
            or 'time' in col.lower()
            or pd.api.types.is_datetime64_any_dtype(series)
        ):
            continue
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            out[col] = series.dt.tz_localize(None)
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            out[col] = pd.to_datetime(series, format='mixed', errors='coerce')
            continue
        non_null = series.dropna()
        if non_null.empty:
            continue
        sample = non_null.head(10).tolist()
        if not all(isinstance(v, (str, date, datetime, pd.Timestamp, np.datetime64)) for v in sample):
            continue
        normalized = series.map(_normalize_duckdb_datetime_value)
        out[col] = pd.to_datetime(normalized, format='mixed', errors='coerce')
    return out


def _merge_product_frame(current, product, incoming, keep_existing_on_empty=True):
    current = current if isinstance(current, pd.DataFrame) else pd.DataFrame()
    incoming = incoming if isinstance(incoming, pd.DataFrame) else pd.DataFrame()
    if current.empty or 'product' not in current.columns:
        return incoming.reset_index(drop=True) if not incoming.empty else pd.DataFrame()
    if incoming.empty:
        if keep_existing_on_empty:
            return current.reset_index(drop=True)
        return current[current['product'] != product].reset_index(drop=True)
    keep = current[current['product'] != product].copy()
    if keep.empty:
        return incoming.reset_index(drop=True)
    return _safe_concat([keep, incoming])


def _grid_is_usable(grid):
    return (grid is not None
            and bool(getattr(grid, 'grids', None))
            and bool(getattr(grid, 'spot_measures', None)))


def _to_ny(df):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df
    if 'asof_date_time' in df.columns:
        nyc_ts = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')
        if 'asof_date_time_nyc' in df.columns:
            nyc_ts = pd.to_datetime(df['asof_date_time_nyc'], errors='coerce')
            if isinstance(nyc_ts.dtype, pd.DatetimeTZDtype):
                nyc_ts = nyc_ts.dt.tz_convert('America/New_York').dt.tz_localize(None)
        if nyc_ts.notna().any():
            df['asof_date_time'] = nyc_ts
        else:
            df['asof_date_time'] = dtutil.ds_tz_convert(df['asof_date_time'], from_tz='UTC', to_tz='America/New_York')
        df['asof_date_time_adjusted'] = df['asof_date_time'].notna()
    for col in _DT_COLS:
        if col == 'asof_date_time':
            continue
        if col in df.columns:
            df[col] = dtutil.ds_tz_convert(df[col], from_tz='UTC', to_tz='America/New_York')
    return df


class _StubAgg:
    def __init__(self, quotes):
        self.quotes = quotes


HIST_CACHE_FILES = {
    'hist_vols': 'hist_vol_marks.parquet',
    'hist_trades': 'hist_trades.parquet',
}

SNAPSHOT_ATTRS = {
    'curr_mark': 'snap_curr_mark',
    'sender_mark': 'snap_sender_mark',
    'cdx_opt_trades': 'snap_trades',
    'cdx_opt_pos_df': 'snap_positions',
    'grid_pos_df': 'snap_grid_pos',
    'quotes_df': 'snap_quotes',
    'cdx_opt_live_trades': 'snap_live_trades',
    'cdx_opt_hist_trades': 'snap_hist_trades',
    'vol_mark_df': 'snap_vol_marks',
    'prev_mark': 'snap_prev_mark',
    'index_info': 'snap_index_info',
}


def _safe_concat(frames):
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0].reset_index(drop=True)
    try:
        return pd.concat(frames, ignore_index=True)
    except (ValueError, TypeError):
        all_cols = pd.Index([])
        for f in frames:
            all_cols = all_cols.union(f.columns)
        dt_cols = set()
        for f in frames:
            dt_cols.update(f.select_dtypes(include=['datetime64', 'datetimetz']).columns)
        cleaned = []
        for f in frames:
            f2 = f.reindex(columns=all_cols)
            for c in dt_cols:
                if c in f2.columns:
                    f2[c] = f2[c].astype(object)
            cleaned.append(f2)
        result = pd.concat(cleaned, ignore_index=True)
        for c in dt_cols:
            if c in result.columns:
                result[c] = pd.to_datetime(result[c], errors='coerce')
        return result


def _cache_path(key, todt):
    return os.path.join(get_grid_cache_dir(todt), f'{key}.parquet')


def _load_cache(key, todt):
    path = _cache_path(key, todt)
    if os.path.isfile(path):
        try:
            return pd.read_parquet(path)
        except Exception as e:
            logging.warning(f'Corrupted cache {path}: {e} -- deleting')
            try:
                os.remove(path)
            except Exception:
                pass
    return None


def _save_cache_raw(key, todt, df):
    path = _cache_path(key, todt)
    atomic_file.write_parquet(df, path, index=False)


def _save_cache(key, todt, df):
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return
    path = _cache_path(key, todt)
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
    atomic_file.write_parquet(out, path, index=False)


def _load_hist_cache(key, todt):
    return _load_cache(HIST_CACHE_FILES[key].replace('.parquet', ''), todt)


def _save_hist_cache(key, todt, df):
    _save_cache(HIST_CACHE_FILES[key].replace('.parquet', ''), todt, df)

STEPS = [
    ('init', 'Initializing...'),
    ('hist_vols', 'Loading historical vol marks (6M)...'),
    ('hist_trades', 'Loading historical trades...'),
    ('live_trades', 'Loading live trades & quotes...'),
    ('agg_pipeline', 'Vol marks & Greeks (parallel by product)...'),
    ('positions', 'Loading & marking positions (parallel)...'),
    ('done', 'Ready'),
]


class ActivityLog:
    def __init__(self, max_entries=80, event_bus=None):
        self._entries = []
        self._lock = threading.Lock()
        self._max = max_entries
        self._ny_tz = pytz.timezone('America/New_York')
        self._event_bus = event_bus

    def log(self, message, category='info'):
        ts = datetime.now(self._ny_tz).strftime('%H:%M:%S')
        with self._lock:
            self._entries.append((ts, category, message))
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max:]
            entries = list(self._entries[-14:])
        if self._event_bus:
            self._event_bus.broadcast('activity', {
                'entries': entries,
            })

    def broadcast(self, event_type, payload):
        if self._event_bus:
            self._event_bus.broadcast(event_type, payload)

    def get_entries(self, n=15):
        with self._lock:
            return list(self._entries[-n:])


class LoadProgress:
    def __init__(self, event_bus=None):
        self.step = 'init'
        self.step_label = 'Initializing...'
        self.step_num = 0
        self.total_steps = len(STEPS) - 1
        self.error = None
        self.done = False
        self.snap_ready = False
        self.data_ready = {'trades': False, 'agg': False, 'positions': False}
        self.products_ready = set()
        self._lock = threading.Lock()
        self._event_bus = event_bus

    def _broadcast(self):
        if self._event_bus:
            self._event_bus.broadcast('progress', self.to_dict())

    def advance(self, step_key):
        with self._lock:
            if self.snap_ready and step_key != 'done':
                return
            if step_key == 'done' and self.done:
                return
            for i, (key, label) in enumerate(STEPS):
                if key == step_key:
                    self.step = key
                    self.step_label = label
                    self.step_num = i
                    self.done = (key == 'done')
                    break
        self._broadcast()

    def mark_ready(self, key):
        with self._lock:
            self.data_ready[key] = True
        self._broadcast()

    def mark_product_ready(self, product):
        with self._lock:
            self.products_ready.add(product)
            if not self.data_ready['agg']:
                self.data_ready['agg'] = True
        self._broadcast()

    def set_error(self, msg):
        with self._lock:
            self.error = str(msg)
            self.done = True
        self._broadcast()

    def to_dict(self):
        with self._lock:
            return {
                'step': self.step,
                'label': self.step_label,
                'num': self.step_num,
                'total': self.total_steps,
                'pct': int(100 * self.step_num / max(self.total_steps, 1)),
                'error': self.error,
                'done': self.done,
                'data_ready': dict(self.data_ready),
                'products_ready': list(self.products_ready),
            }


class CDXOptionsDataLoader:

    def __init__(self, tickers=None, todt=None, lookback='6m'):
        self.tickers = tickers or DEFAULT_TICKERS
        self.todt = dtutil.adjustdate(todt or dtutil.today(), 'backward')
        self.lookback = lookback
        self.fromdt = dtutil.dateadd(self.todt, f'-{lookback}')
        self.plot_cols = PLOT_COLS

        self.vol_mark_df = pd.DataFrame()
        self.cdx_opt_hist_trades = pd.DataFrame()
        self.cdx_opt_live_trades = pd.DataFrame()
        self.cdx_opt_trades = pd.DataFrame()
        self.quotes_df = pd.DataFrame()
        self.curr_mark = pd.DataFrame()
        self.sender_mark = pd.DataFrame()
        self.prev_mark = pd.DataFrame()
        self.cdx_opt_pos_df = pd.DataFrame()

        self.index_info = None
        self.rate_curves = None
        self.cdx_marker = None
        self.mark_pos = True
        self.pos_scenario_run = False

        self.pricing_grids = {}
        self.grid_engine = GridPricingEngine({})
        self.product_aggs = {}
        self.grid_pos_df = pd.DataFrame()

        self._hist_loaded = False
        self._plot_cache = {}
        self._plot_cache_lock = threading.Lock()
        self._plot_inflight = {}
        self._hist_vol_refresh_lock = threading.Lock()
        self._product_plot_versions = {}
        self._snapshot_plot_version = 0
        self._shared_trade_version = 0
        self._shared_trade_cache = {}
        self._shared_trade_cache_lock = threading.Lock()
        self._shared_trade_inflight = {}
        self._posdf_cache = {}
        self._posdf_cache_lock = threading.Lock()
        self._posdf_inflight = {}
        self._is_plot_prewarming = False
        self._pending_prewarm_products = set()
        self._prewarm_lock = threading.Lock()
        self._is_light_refreshing = False
        self._light_refresh_ready = False
        self._sender_mark_failures = set()
        self._warned_missing_marks = set()
        self.progress = LoadProgress()
        self.activity_log = ActivityLog()
        self.last_light_refresh = None
        self.last_heavy_refresh = None
        self.last_spot_refresh = None
        self._last_snapshot_save = None
        self.mark_time_override = ''
        self.thresh_time_override = '4h'
        self.priority_product = None
        self._session_product_universe = []
        self._force_grid_rebuild = False
        self._grids_carried_forward = False
        self._missing_grid_cycles = {}
        self._missing_grid_rebuild_products = set()
        self._missing_grid_rebuild_requested = False
        self._grid_repair_backoff = {}
        self._spot_refresh_generation = 0
        self._snapshot_loaded = self._load_snapshot()
        if self._snapshot_loaded:
            self._load_grids_from_cache()
            self.set_session_product_universe(self._known_products())

    def _known_products(self):
        products = set(self.pricing_grids.keys()) | set(self.product_aggs.keys())
        if not self.curr_mark.empty and 'product' in self.curr_mark.columns:
            products |= set(self.curr_mark['product'].dropna().unique())
        products |= self._current_quote_products()
        return sorted(products)

    def _current_quote_products(self):
        quotes_df = getattr(self, 'quotes_df', pd.DataFrame())
        if quotes_df.empty or 'product' not in quotes_df.columns:
            return set()
        quotes = quotes_df
        tickers = getattr(self, 'tickers', [])
        if tickers and 'ticker' in quotes.columns:
            quotes = quotes[quotes['ticker'].isin(tickers)]
        if 'asof_date' in quotes.columns:
            asof = pd.to_datetime(quotes['asof_date'], errors='coerce').dt.date
            quotes = quotes[asof >= dtutil.dateadd(getattr(self, 'todt', dtutil.today()), '-1b')]
        return set(quotes['product'].dropna().astype(str).unique().tolist())

    def get_missing_grid_products(self, priority=None):
        cm_products = set()
        if not self.curr_mark.empty and 'product' in self.curr_mark.columns:
            cm_products = set(self.curr_mark['product'].dropna().unique().tolist())
        prio = self.priority_product if priority is None else priority
        products = cm_products | self._current_quote_products()
        missing = [product for product in products if not _grid_is_usable(self.pricing_grids.get(product))]
        return self.sorted_products(missing, priority=prio)

    def get_missing_agg_products(self, priority=None):
        cm_products = set()
        if not self.curr_mark.empty and 'product' in self.curr_mark.columns:
            cm_products = set(self.curr_mark['product'].dropna().unique().tolist())
        prio = self.priority_product if priority is None else priority
        products = cm_products | self._current_quote_products()
        missing = [product for product in products if product not in self.product_aggs]
        return self.sorted_products(missing, priority=prio)

    def _build_live_product_agg(self, product):
        if self.quotes_df.empty or 'product' not in self.quotes_df.columns:
            return None
        product_quotes = self.quotes_df[self.quotes_df['product'] == product].copy()
        quote_fallback_dt = None
        if product_quotes.empty:
            prev_quotes, prev_quote_dt = self._get_prev_day_quotes(product)
            if prev_quotes.empty:
                return None
            if not self._is_otr(product):
                otr_product = self._find_otr_product(product)
                if otr_product:
                    prev_quotes = self._apply_otr_spot_adjustment(product, prev_quotes, otr_product)
            product_quotes = prev_quotes.copy()
            quote_fallback_dt = prev_quote_dt
        if 'asof_date_time' not in product_quotes.columns:
            return None

        mark_time = self._resolve_mark_time()
        thresh = self.thresh_time_override or '4h'
        window_h = int(thresh.rstrip('h')) if thresh.endswith('h') else 4
        if quote_fallback_dt is not None:
            window_h = max(window_h, 24)
            quote_dt = (
                pd.to_datetime(product_quotes['asof_date'], errors='coerce').dropna()
                if 'asof_date' in product_quotes.columns
                else pd.Series(dtype='datetime64[ns]')
            )
            fallback_dt = quote_dt.dt.date.max() if not quote_dt.empty else dtutil.todate(quote_fallback_dt)
            end_time_str = f'{fallback_dt} 23:59'
        else:
            end_time_str = f'{self.todt} {mark_time}'

        agg = CDXOptionQuoteAgg(product_quotes, adjust_tz=False)
        agg.filter_quotes(from_threshold=f'{window_h}h', to_threshold='0h', end_time=end_time_str)
        if self._is_otr(product):
            while len(agg.quotes) < MIN_QUOTES_FOR_BOOTSTRAP and window_h < 24:
                window_h += 6
                agg = CDXOptionQuoteAgg(product_quotes, adjust_tz=False)
                agg.filter_quotes(from_threshold=f'{window_h}h', to_threshold='0h', end_time=end_time_str)
        return agg if not agg.quotes.empty else None

    def ensure_product_agg(self, product):
        agg = self.product_aggs.get(product)
        if agg is not None:
            return agg
        agg = self._build_live_product_agg(product)
        if agg is None:
            logging.warning(f'ensure_product_agg({product}): unable to build quotes agg')
            return None
        next_aggs = dict(self.product_aggs)
        next_aggs[product] = agg
        self.product_aggs = next_aggs
        self._sender_mark_failures.discard(product)
        logging.info(f'ensure_product_agg({product}): built from {len(agg.quotes)} quotes')
        return agg

    def repair_missing_product_aggs(self, products=None, priority=None):
        targets = list(products or self.get_missing_agg_products(priority=priority))
        repaired, failed = [], []
        for product in targets:
            if self.product_aggs.get(product) is not None:
                continue
            agg = self.ensure_product_agg(product)
            if agg is None:
                failed.append(product)
            else:
                repaired.append(product)
        if repaired:
            logging.info(f'missing product_aggs repaired: {repaired}')
        if failed:
            logging.warning(f'missing product_aggs still unavailable: {failed}')
        return {'attempted': targets, 'repaired': repaired, 'failed': failed}

    def _clear_missing_grid_tracking(self, products=None):
        targets = set(products or [])
        if not targets:
            self._missing_grid_cycles.clear()
            self._missing_grid_rebuild_products.clear()
        else:
            for product in targets:
                self._missing_grid_cycles.pop(product, None)
                self._missing_grid_rebuild_products.discard(product)
        if self._missing_grid_rebuild_requested and not self._missing_grid_rebuild_products:
            self._missing_grid_rebuild_requested = False
            self._force_grid_rebuild = False

    def _record_missing_grid_state(self, products):
        missing = set(products or [])
        resolved = set(self._missing_grid_cycles) - missing
        if resolved:
            self._clear_missing_grid_tracking(resolved)
        if not missing:
            return
        armed = []
        first_seen = []
        for product in self.sorted_products(list(missing), priority=self.priority_product):
            count = self._missing_grid_cycles.get(product, 0) + 1
            self._missing_grid_cycles[product] = count
            if count == 1:
                first_seen.append(product)
            if count >= _MISSING_GRID_REBUILD_CYCLES and product not in self._missing_grid_rebuild_products:
                self._missing_grid_rebuild_products.add(product)
                armed.append(product)
        if first_seen:
            logging.warning(f'Missing/incomplete grids detected: {first_seen}')
        if armed:
            self._missing_grid_rebuild_requested = True
            self._force_grid_rebuild = True
            logging.warning(f'Missing/incomplete grids persisted for {armed} -- forcing rebuild')
            self.activity_log.log(f'Missing grids persisted: {armed} -- rebuild scheduled', 'error')

    def _ensure_repair_market_data(self, products):
        targets = set(products or [])
        if not targets:
            return
        self.ensure_static_data()
        shared_markit = getattr(self, '_shared_markit', None)
        has_shared = (getattr(self, '_shared_libor', None) is not None
                      and getattr(self, '_shared_default_pv', None) is not None
                      and isinstance(shared_markit, pd.DataFrame)
                      and 'ticker' in shared_markit.columns
                      and targets.issubset(set(shared_markit['ticker'].dropna().unique().tolist())))
        if has_shared:
            return
        all_quotes = pd.DataFrame()
        if isinstance(self.quotes_df, pd.DataFrame):
            if 'ticker' in self.quotes_df.columns:
                all_quotes = self.quotes_df[self.quotes_df['ticker'].isin(self.tickers)].copy()
            else:
                all_quotes = self.quotes_df.copy()
        if not all_quotes.empty and 'product' in all_quotes.columns:
            all_quotes = all_quotes[all_quotes['product'].isin(targets)].copy()
        logging.info(f'Missing grid repair: prefetch shared market data for {sorted(targets)}')
        self._prefetch_shared_market_data(all_quotes)

    def _publish_product_result(self, product, result, keep_existing_curr_on_empty=True, keep_existing_sender_on_empty=False):
        next_curr = _prepare_mark_frame(result.get('curr_mark'), 'curr_mark', product=product)
        next_sender = _prepare_mark_frame(result.get('sender_mark'), 'sender_mark', product=product)
        published_curr = _merge_product_frame(
            self.curr_mark, product, next_curr, keep_existing_on_empty=keep_existing_curr_on_empty,
        )
        published_sender = _merge_product_frame(
            self.sender_mark, product, next_sender, keep_existing_on_empty=keep_existing_sender_on_empty,
        )
        published_grids = dict(self.pricing_grids)
        next_grid = result.get('grid')
        if _grid_is_usable(next_grid):
            published_grids[product] = next_grid
        next_agg = result.get('agg')
        published_aggs = dict(self.product_aggs)
        if next_agg is None:
            published_aggs.pop(product, None)
        else:
            published_aggs[product] = next_agg
        live_curr = published_curr.copy() if not published_curr.empty else pd.DataFrame()
        for col in _PUBLISHED_MARK_COLS:
            if not live_curr.empty and col not in live_curr.columns:
                live_curr[col] = np.nan
        if not live_curr.empty:
            _sanitize_mark_dtypes(live_curr)
        self.pricing_grids = dict(published_grids)
        self.grid_engine = GridPricingEngine(self.pricing_grids) if self.pricing_grids else GridPricingEngine({})
        self.product_aggs = dict(published_aggs)
        self.curr_mark = live_curr
        self.sender_mark = published_sender.copy() if not published_sender.empty else pd.DataFrame()

    def repair_missing_grids(self):
        missing = self.get_missing_grid_products()
        if not missing:
            self._clear_missing_grid_tracking()
            return {'attempted': [], 'repaired': [], 'failed': [], 'remaining': []}
        all_quotes = pd.DataFrame()
        if isinstance(self.quotes_df, pd.DataFrame):
            if 'ticker' in self.quotes_df.columns:
                all_quotes = self.quotes_df[self.quotes_df['ticker'].isin(self.tickers)].copy()
            else:
                all_quotes = self.quotes_df.copy()
        if not all_quotes.empty and 'product' in all_quotes.columns:
            all_quotes = all_quotes[all_quotes['product'].isin(missing)].copy()
        backoff = getattr(self, '_grid_repair_backoff', {})
        self._grid_repair_backoff = backoff
        signatures, deferred = {}, []
        for product in missing:
            quotes = all_quotes[all_quotes['product'] == product] if 'product' in all_quotes else all_quotes
            digest = hashlib.sha256(pd.util.hash_pandas_object(quotes.astype(str), index=False).values.tobytes()).hexdigest()
            signatures[product] = (str(self.todt), getattr(self, 'mark_time_override', ''),
                                   getattr(self, 'thresh_time_override', '4h'), digest)
            previous = backoff.get(product, {})
            if tuple(previous.get('signature', ())) == signatures[product] and previous.get('retry_at', 0) > time.time():
                deferred.append(product)
        targets = [product for product in missing if product not in deferred]
        if deferred:
            logging.info('Missing grid repair deferred unchanged inputs: %s', deferred)
        if not targets:
            return {'attempted': [], 'repaired': [], 'failed': [], 'remaining': missing, 'deferred': deferred}
        try:
            self._ensure_repair_market_data(targets)
        except Exception:
            logging.exception('Missing grid repair: shared market data prefetch failed')
        mark_time = self._resolve_mark_time()
        attempted, repaired, failed = [], [], []
        logging.warning(f'Missing grid repair: attempting {targets}')
        self.activity_log.log(f'Missing grid repair: {targets}', 'compute')
        for product in targets:
            previous = backoff.get(product, {})
            attempts = previous.get('attempts', 0) + 1 if tuple(previous.get('signature', ())) == signatures[product] else 1
            backoff[product] = {'signature': signatures[product], 'attempts': attempts,
                                'retry_at': time.time() + min(1800, 300 * 2 ** min(attempts - 1, 3))}
            product_quotes = (
                all_quotes[all_quotes['product'] == product].copy()
                if not all_quotes.empty and 'product' in all_quotes.columns
                else pd.DataFrame()
            )
            try:
                result = self._run_product_agg(
                    product, product_quotes, mark_time, self.todt, cached_grid=self.pricing_grids.get(product),
                )
            except Exception:
                logging.exception(f'[{product}] Missing grid repair crashed')
                attempted.append(product)
                failed.append(product)
                continue
            attempted.append(product)
            if not result:
                failed.append(product)
                continue
            self._publish_product_result(product, result, keep_existing_curr_on_empty=True, keep_existing_sender_on_empty=False)
            if _grid_is_usable(self.pricing_grids.get(product)):
                repaired.append(product)
                backoff.pop(product, None)
            else:
                failed.append(product)
        remaining = self.get_missing_grid_products()
        if repaired:
            logging.info(f'Missing grid repair succeeded: {repaired}')
            self.activity_log.log(f'Missing grid repair succeeded: {repaired}', 'data')
        if failed:
            logging.warning(f'Missing grid repair incomplete: failed={failed}, remaining={remaining}')
        return {'attempted': attempted, 'repaired': repaired, 'failed': failed, 'remaining': remaining}

    def _normalize_product_list(self, products, priority=None):
        items = {str(p) for p in (products or []) if p}
        if not items:
            return []
        return self.sorted_products(list(items), priority=priority)

    def set_session_product_universe(self, products, priority=None):
        self._session_product_universe = self._normalize_product_list(products, priority=priority)
        return list(self._session_product_universe)

    def get_session_product_universe(self, priority=None):
        products = set(self._session_product_universe)
        products |= set(self._known_products())
        products |= self._current_quote_products()
        return self._normalize_product_list(products, priority=priority)

    def _evict_plot_cache(self, products=None):
        targets = set(products or [])
        with self._plot_cache_lock:
            if not targets:
                self._plot_cache.clear()
                return
            self._plot_cache = {
                key: val for key, val in self._plot_cache.items()
                if not key or key[0] not in targets
            }

    def _bump_plot_versions(self, products=None, include_snapshot=False):
        targets = set(products or self._known_products())
        for product in targets:
            self._product_plot_versions[product] = self._product_plot_versions.get(product, 0) + 1
        self._shared_trade_version += 1
        if include_snapshot:
            self._snapshot_plot_version += 1
            self._evict_plot_cache()
            with self._posdf_cache_lock:
                self._posdf_cache.clear()
                self._posdf_inflight.clear()
        else:
            self._evict_plot_cache(targets)
            with self._posdf_cache_lock:
                self._posdf_cache = {k: v for k, v in self._posdf_cache.items() if k[0] not in targets}
                self._posdf_inflight = {k: v for k, v in self._posdf_inflight.items() if k[0] not in targets}
        with self._shared_trade_cache_lock:
            self._shared_trade_cache.clear()

    def queue_prewarm_plot_cache(self, products=None):
        targets = {p for p in (products or self.product_aggs.keys()) if p}
        if not targets:
            return
        with self._prewarm_lock:
            self._pending_prewarm_products |= targets
            if self._is_plot_prewarming:
                return
            self._is_plot_prewarming = True

        def _run():
            while True:
                with self._prewarm_lock:
                    batch = sorted(self._pending_prewarm_products)
                    self._pending_prewarm_products.clear()
                if not batch:
                    break
                self.prewarm_plot_cache(products=batch)
            with self._prewarm_lock:
                self._is_plot_prewarming = False
                restart = bool(self._pending_prewarm_products)
                if restart:
                    self._is_plot_prewarming = True
            if restart:
                threading.Thread(target=_run, daemon=True).start()

        threading.Thread(target=_run, daemon=True).start()

    def _load_grids_from_cache(self):
        loaded = self._load_grids_from_dir(get_grid_cache_dir(self.todt))
        source_dt = self.todt
        if not loaded:
            prev_dt = dtutil.dateadd(self.todt, '-1b')
            loaded = self._load_grids_from_dir(get_grid_cache_dir(prev_dt))
            source_dt = prev_dt
        if not loaded:
            return
        self.pricing_grids = loaded
        self.grid_engine = GridPricingEngine(loaded)
        stale = {p for p, g in loaded.items() if g.valuedt != self.todt}
        if stale:
            self._grids_carried_forward = True
            self._stale_grid_products = stale
        logging.info(f'Snapshot: {len(loaded)} pricing grids restored from cache (dt={source_dt}, '
                     f'stale={len(stale)})')

    def _save_snapshot(self):
        t0 = time.time()
        saved = 0
        for attr, key in SNAPSHOT_ATTRS.items():
            df = getattr(self, attr, None)
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                _save_cache(key, self.todt, df)
                saved += 1
        if self.product_aggs:
            frames = []
            for prod, agg in self.product_aggs.items():
                q = agg.quotes.copy()
                q['_agg_product'] = prod
                frames.append(q)
            _save_cache('snap_agg_quotes', self.todt, pd.concat(frames, ignore_index=True))
            saved += 1
        if self.rate_curves:
            rc_path = os.path.join(get_grid_cache_dir(self.todt), 'snap_rate_curves.pkl')
            atomic_file.write_pickle(self.rate_curves, rc_path)
            logging.info(f'Snapshot: rate_curves pickled ({list(self.rate_curves.keys())})')
            saved += 1
        self._last_snapshot_save = datetime.now(NY_TZ)
        meta = {
            'save_time': self._last_snapshot_save.isoformat(),
            'todt': str(self.todt),
            'tickers': self.tickers,
            'session_products': self.get_session_product_universe(),
            'grid_repair_backoff': getattr(self, '_grid_repair_backoff', {}),
        }
        meta_path = os.path.join(get_grid_cache_dir(self.todt), 'snap_meta.json')
        atomic_file.write_json(meta, meta_path)
        cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
        agg_prods = sorted(self.product_aggs.keys()) if self.product_aggs else []
        logging.info(f'Snapshot saved: {saved}/{len(SNAPSHOT_ATTRS)}+extras in {time.time()-t0:.1f}s | '
                     f'curr_mark={len(cm_prods)} {cm_prods} | product_aggs={len(agg_prods)} {agg_prods}')

    def _load_snapshot(self):
        t0 = time.time()
        loaded = 0
        self._snapshot_date = None
        self._snapshot_save_time = None
        for todt in (self.todt, dtutil.dateadd(self.todt, '-1b')):
            for attr, key in SNAPSHOT_ATTRS.items():
                df = _load_cache(key, todt)
                if df is not None and not df.empty:
                    setattr(self, attr, df)
                    loaded += 1
            if loaded:
                self._snapshot_date = todt
                meta_path = os.path.join(get_grid_cache_dir(todt), 'snap_meta.json')
                if os.path.isfile(meta_path):
                    with open(meta_path) as f:
                        meta = json.load(f)
                    self._snapshot_save_time = datetime.fromisoformat(meta['save_time'])
                    self._session_product_universe = self._normalize_product_list(meta.get('session_products', []))
                    self._grid_repair_backoff = meta.get('grid_repair_backoff', {})
                    logging.info(f'Snapshot meta: saved {meta["save_time"]} for todt={meta.get("todt")}')
                self._hist_loaded = True
                agg_df = _load_cache('snap_agg_quotes', todt)
                if agg_df is not None and '_agg_product' in agg_df.columns:
                    for prod, grp in agg_df.groupby('_agg_product'):
                        self.product_aggs[prod] = _StubAgg(grp.drop(columns=['_agg_product']))
                    logging.info(f'Snapshot: {len(self.product_aggs)} product_aggs restored')
                rc_pkl = os.path.join(get_grid_cache_dir(todt), 'snap_rate_curves.pkl')
                if os.path.isfile(rc_pkl):
                    with open(rc_pkl, 'rb') as f:
                        self.rate_curves = pickle.load(f)
                    logging.info(f'Snapshot: rate_curves restored from pickle ({list(self.rate_curves.keys())})')
                else:
                    rc_df = _load_cache('snap_rate_curves', todt)
                    if rc_df is not None and '_ccy' in rc_df.columns:
                        self.rate_curves = {
                            ccy: grp.drop(columns=['_ccy']).reset_index(drop=True)
                            for ccy, grp in rc_df.groupby('_ccy')
                        }
                        logging.info(f'Snapshot: rate_curves restored from parquet ({list(self.rate_curves.keys())})')
                cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
                agg_prods = sorted(self.product_aggs.keys()) if self.product_aggs else []
                grid_prods = sorted(self.pricing_grids.keys()) if self.pricing_grids else []
                logging.info(f'Snapshot restored: {loaded}/{len(SNAPSHOT_ATTRS)} frames from {todt} '
                             f'in {time.time()-t0:.1f}s | '
                             f'curr_mark={len(cm_prods)} {cm_prods} | '
                             f'product_aggs={len(agg_prods)} {agg_prods} | '
                             f'index_info={"loaded" if self.index_info is not None else "None"} | '
                             f'rate_curves={"loaded" if self.rate_curves else "None"}')
                extra_aggs = sorted(set(agg_prods) - set(cm_prods))
                missing_aggs = sorted(set(cm_prods) - set(agg_prods))
                if extra_aggs or missing_aggs:
                    logging.warning(f'Snapshot DRIFT: missing_product_aggs={missing_aggs}, '
                                    f'aggs_not_in_curr_mark={extra_aggs}')
                if not self.curr_mark.empty:
                    _sanitize_mark_dtypes(self.curr_mark)
                if not self._session_product_universe:
                    self.set_session_product_universe(self._known_products())
                self._restore_newer_vol_history()
                return True
        logging.warning('Snapshot: no snapshot found for any date')
        return False

    def _restore_newer_vol_history(self):
        cached = _load_hist_cache('hist_vols', self.todt)
        if cached is None or cached.empty:
            return False

        def latest(frame):
            if frame is None or frame.empty:
                return pd.NaT
            for column in ('asof_date_time', 'asof_date'):
                if column in frame:
                    stamp = pd.to_datetime(frame[column], errors='coerce', utc=True).max()
                    if pd.notna(stamp):
                        return stamp
            return pd.NaT

        snapshot_end, cached_end = latest(self.vol_mark_df), latest(cached)
        if pd.notna(cached_end) and (pd.isna(snapshot_end) or cached_end > snapshot_end):
            self.vol_mark_df = cached
            logging.info('Snapshot vol history upgraded from %s to %s (%s rows)',
                         snapshot_end, cached_end, len(cached))
            return True
        return False

    def should_rebuild_grid(self):
        return self._force_grid_rebuild or not self.pricing_grids

    # ── Data Loading & Refresh ───────────────────────────────

    def _fetch_parallel(self, tasks):
        names = [t[0] for t in tasks]
        funcs = [t[1] for t in tasks]
        self.activity_log.log(f'Parallel fetch ({len(tasks)} threads): {", ".join(names)}', 'query')
        executor = JobExecutor('thread', max_workers=len(tasks))
        results = executor.submitjob(funcs)
        out = {}
        for name, res in zip(names, results):
            if res['error']:
                self.activity_log.log(f'FAILED: {name} -- {str(res["error"])[:200]}', 'error')
                raise RuntimeError(f'{name} failed: {res["error"]}')
            out[name] = res['result']
        return out

    def load_all_with_progress(self, on_product_ready=None):
        t0 = time.time()
        p = self.progress
        al = self.activity_log
        al.log(f'Full load started: {self.tickers}, lookback to {self.fromdt}', 'start')

        p.advance('hist_vols')

        cached_vols = _load_hist_cache('hist_vols', self.todt) if not self._hist_loaded else None
        cached_trades = _load_hist_cache('hist_trades', self.todt) if not self._hist_loaded else None
        logging.info(f'Cache check: {time.time()-t0:.1f}s (vols_cached={cached_vols is not None}, trades_cached={cached_trades is not None})')

        if cached_vols is not None:
            self.vol_mark_df = cached_vols
            al.log(f'Vol marks: {len(self.vol_mark_df):,} rows (from cache)', 'data')
        if cached_trades is not None:
            self.cdx_opt_hist_trades = cached_trades
            al.log(f'Hist trades: {len(self.cdx_opt_hist_trades):,} rows (from cache)', 'data')

        tasks = []
        need_hist_vols = cached_vols is None and not (self._hist_loaded and not self.vol_mark_df.empty)
        need_hist_trades = cached_trades is None and not (self._hist_loaded and not self.cdx_opt_hist_trades.empty)
        if need_hist_vols:
            tasks.append(('hist_vols', lambda: cds_index_vol.get_vol_mark(
                self.fromdt, self.todt, vol_mark_type='median', use_live=True
            )))
        if need_hist_trades:
            tasks.append(('hist_trades', lambda: sdr_db.get_cdx_opt_trades(
                dtutil.dateadd(self.todt, '-6m'), dtutil.dateadd(self.todt, '-1d'), use_live=False
            )))
        tasks.append(('live_trades', lambda: sdr_db.get_cdx_opt_trades(self.todt, self.todt, use_live=True)))
        tasks.append(('quotes', lambda: cds_index_vol.get_quotes(self.todt, self.todt, use_live=True)))

        logging.info(f'Fetch parallel starting ({len(tasks)} tasks): {time.time()-t0:.1f}s since load start')
        r = self._fetch_parallel(tasks)
        logging.info(f'Fetch parallel done: {time.time()-t0:.1f}s since load start')

        if 'hist_vols' in r:
            self.vol_mark_df = _to_ny(r['hist_vols'])
            al.log(f'Vol marks: {len(self.vol_mark_df):,} rows ({self.fromdt} to {self.todt})', 'data')
            _save_hist_cache('hist_vols', self.todt, self.vol_mark_df)
        if 'hist_trades' in r:
            self.cdx_opt_hist_trades = _to_ny(r['hist_trades'])
            al.log(f'Hist trades: {len(self.cdx_opt_hist_trades):,} rows', 'data')
            _save_hist_cache('hist_trades', self.todt, self.cdx_opt_hist_trades)

        self._load_live_data(prefetched={k: r[k] for k in ('live_trades', 'quotes') if k in r})
        al.log(f'Live: {len(self.cdx_opt_live_trades):,} trades, {len(self.quotes_df):,} quotes', 'data')
        p.mark_ready('trades')

        self.ensure_static_data()

        p.advance('live_trades')
        self._run_agg_pipeline(p, on_product_ready=on_product_ready)
        self._hist_loaded = True
        self._save_snapshot()
        al.log('Full load complete', 'done')

    def refresh_with_progress(self, on_product_ready=None):
        p = self.progress
        al = self.activity_log
        al.log('Heavy recompute started', 'start')
        p.advance('live_trades')
        al.log('Refreshing live trades & quotes...', 'query')
        self._load_live_data()
        p.mark_ready('trades')
        al.log(f'Live data: {len(self.cdx_opt_live_trades):,} trades, {len(self.quotes_df):,} quotes', 'data')
        self.ensure_static_data()
        self._run_agg_pipeline(p, on_product_ready=on_product_ready)
        self._save_snapshot()
        al.log('Heavy recompute complete', 'done')

    def remark_with_progress(self, on_product_ready=None):
        p = self.progress
        al = self.activity_log
        mark_time = self._resolve_mark_time()
        thresh = self.thresh_time_override or '4h'
        al.log(f'Re-mark started: mark_time={mark_time} thresh={thresh} (no DB fetch)', 'start')
        if self.quotes_df.empty:
            al.log('No quotes in memory — falling back to full refresh', 'warn')
            return self.refresh_with_progress(on_product_ready=on_product_ready)
        p.advance('live_trades')
        p.mark_ready('trades')
        self.ensure_static_data()
        self._run_agg_pipeline(p, on_product_ready=on_product_ready)
        self._save_snapshot()
        al.log(f'Re-mark complete: mark_time={mark_time}', 'done')

    _FWD_CALC_COLS = [
        'par_spread_fwd_calc', 'par_spread_spot_calc',
        'price_fwd_calc', 'price_spot_calc',
        'clean_price_fwd_calc', 'clean_price_spot_calc',
        'risky_pv01_fwd_calc', 'risky_pv01_spot_calc',
        'full_risky_pv01_fwd_calc', 'full_risky_pv01_spot_calc',
    ]
    _FWD_CALC_JOIN = ['product', 'expiry', 'sender_bank', 'strike_display', 'asof_date_time']
    _QUOTE_VOL_COLS = ['pay_vol_bid_calc', 'pay_vol_ask_calc', 'rec_vol_bid_calc', 'rec_vol_ask_calc']

    def _refresh_product_agg_quotes(self, products=None):
        if not self.product_aggs or self.quotes_df.empty:
            return []
        now = datetime.now(NY_TZ)
        mark_time = self.mark_time_override or now.strftime('%H:%M')
        mark_date = self.todt.strftime('%Y-%m-%d')
        end_time_str = f'{mark_date} {mark_time}'
        thresh = self.thresh_time_override or '4h'
        window_h = int(thresh.rstrip('h')) if thresh.endswith('h') else 4
        time_col = 'asof_date_time'
        targets = set(products or self.product_aggs.keys())
        updated = []
        for product, agg in self.product_aggs.items():
            if product not in targets:
                continue
            pq = self.quotes_df[self.quotes_df['product'] == product].copy()
            if pq.empty or time_col not in pq.columns:
                continue
            try:
                prev_quotes = agg.quotes.copy()
                temp_agg = CDXOptionQuoteAgg(pq, adjust_tz=False)
                temp_agg.filter_quotes(from_threshold=f'{window_h}h', to_threshold='0h', end_time=end_time_str)
                if not temp_agg.quotes.empty:
                    has_grid = _grid_is_usable(self.pricing_grids.get(product))
                    self._enrich_temp_agg_calcs(temp_agg, product, has_grid=has_grid)
                    new_quotes = temp_agg.quotes.reset_index(drop=True)
                    new_quotes = self._merge_prev_calc_cols(new_quotes, prev_quotes, self._FWD_CALC_COLS)
                    self._sync_quote_spot_calc_from_raw(new_quotes, product)
                    self._ensure_quote_strike_calcs(new_quotes)
                    agg.quotes = new_quotes
                    if self.grid_engine and product in self.pricing_grids:
                        self.grid_engine.on_quote_vols(agg.quotes, product)
                    agg.quotes = self._merge_prev_calc_cols(agg.quotes, prev_quotes, self._QUOTE_VOL_COLS)
                    if self._has_usable_quote_vols(agg.quotes):
                        self._sender_mark_failures.discard(product)
                    else:
                        logging.warning(f'[{product}] refreshed agg.quotes lacks usable vol calc cols; '
                                        f'leaving sender_mark_failures unchanged')
                    updated.append(product)
            except Exception as e:
                logging.warning(f'_refresh_product_agg_quotes({product}) failed: {e}')
        if updated:
            logging.info(f'product_agg quotes refreshed: {updated}, window={window_h}h ending {mark_time}')
        return updated

    def _collect_filtered_agg_quotes(self):
        if not self.product_aggs:
            return pd.DataFrame()
        frames = []
        for product, agg in self.product_aggs.items():
            q = getattr(agg, 'quotes', None)
            if q is None or q.empty:
                continue
            frame = q if 'product' in q.columns else q.assign(product=product)
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True, sort=False)
        logging.info(f'_collect_filtered_agg_quotes: {len(out)} rows from {len(frames)} product_aggs '
                     f'(was using self.quotes_df={len(self.quotes_df)} rows)')
        return out

    def _merge_prev_calc_cols(self, new_quotes, prev_quotes, cols):
        if new_quotes is None or new_quotes.empty:
            return new_quotes
        if prev_quotes is None or prev_quotes.empty:
            return new_quotes
        present = [c for c in cols if c in prev_quotes.columns]
        if not present:
            return new_quotes
        keys = [k for k in self._FWD_CALC_JOIN if k in new_quotes.columns and k in prev_quotes.columns]
        if not keys:
            return new_quotes
        left = new_quotes.copy()
        right = prev_quotes[keys + present].copy()
        for dt_key in ('expiry', 'asof_date_time'):
            if dt_key in keys:
                left[dt_key] = pd.to_datetime(left[dt_key], format='mixed', errors='coerce', utc=True).dt.tz_localize(None)
                right[dt_key] = pd.to_datetime(right[dt_key], format='mixed', errors='coerce', utc=True).dt.tz_localize(None)
        right = right.drop_duplicates(subset=keys, keep='last')
        merged = left.merge(right, on=keys, how='left', suffixes=('', '_prev'))
        for col in present:
            prev_col = f'{col}_prev'
            if col not in merged.columns:
                if prev_col in merged.columns:
                    merged = merged.rename(columns={prev_col: col})
                continue
            if prev_col not in merged.columns:
                continue
            missing = merged[col].isna() & merged[prev_col].notna()
            if missing.any():
                merged.loc[missing, col] = merged.loc[missing, prev_col]
            merged = merged.drop(columns=[prev_col])
        return merged

    def _has_usable_quote_vols(self, quotes):
        if quotes is None or quotes.empty:
            return False
        for col in self._QUOTE_VOL_COLS:
            if col in quotes.columns and pd.to_numeric(quotes[col], errors='coerce').notna().any():
                return True
        return False

    def _enrich_temp_agg_calcs(self, temp_agg, product, has_grid):
        calc_spot_adj = getattr(temp_agg, 'calc_spot_adj', None)
        if not callable(calc_spot_adj):
            return False
        spot_value = self._quote_refresh_spot_value(product, temp_agg.quotes)
        if spot_value is None:
            logging.info(f'[{product}] skip calc_spot_adj in refresh: no spot value available')
            return False
        calc_spot_adj(**{product: spot_value}, option_calcs=not has_grid)
        return True

    def _quote_refresh_spot_value(self, product, quotes):
        get_sdr_spot = getattr(self, '_get_sdr_spot', None)
        raw = get_sdr_spot(product) if callable(get_sdr_spot) else np.nan
        if isinstance(raw, dict):
            return raw.get('price')
        if pd.notna(raw):
            return raw
        raw_col = 'spot' if is_price_based(product) else 'spread_spot'
        if raw_col in quotes.columns:
            vals = pd.to_numeric(quotes[raw_col], errors='coerce').dropna()
            if not vals.empty:
                return vals.iloc[-1]
        return None

    def _ensure_quote_strike_calcs(self, quotes):
        if quotes is None or quotes.empty:
            return quotes
        if 'strike_display' not in quotes.columns:
            if 'is_spread_based' in quotes.columns:
                is_spread_based = quotes['is_spread_based'].fillna(False)
            elif 'product' in quotes.columns:
                is_spread_based = ~quotes['product'].astype(str).str.lower().str.contains('hy')
            else:
                is_spread_based = pd.Series(False, index=quotes.index)
            strike = pd.to_numeric(quotes['strike'], errors='coerce') if 'strike' in quotes.columns else pd.Series(np.nan, index=quotes.index)
            spread_strike = pd.to_numeric(quotes['spread_strike'], errors='coerce') if 'spread_strike' in quotes.columns else pd.Series(np.nan, index=quotes.index)
            quotes['strike_display'] = np.where(is_spread_based, spread_strike, strike)
        strike_display = pd.to_numeric(quotes['strike_display'], errors='coerce')
        for col in ('strike_calc', 'spread_strike_calc'):
            if col not in quotes.columns:
                quotes[col] = strike_display
                continue
            existing = pd.to_numeric(quotes[col], errors='coerce')
            quotes[col] = existing.where(existing.notna(), strike_display)
        return quotes

    def _sync_quote_spot_calc_from_raw(self, quotes, product):
        if quotes is None or quotes.empty:
            return quotes
        if 'par_spread_spot_calc' not in quotes.columns:
            quotes['par_spread_spot_calc'] = np.nan

        if is_price_based(product):
            grid = self.pricing_grids.get(product)
            if grid is None or not grid.spot_measures or 'spot' not in quotes.columns:
                return quotes
            spot_series = pd.to_numeric(quotes['spot'], errors='coerce')
            mask = spot_series.notna()
            if mask.any():
                quotes.loc[mask, 'par_spread_spot_calc'] = spot_series.loc[mask].map(
                    lambda s: grid.price_to_spread(float(s))
                )
            return quotes

        if 'spread_spot' not in quotes.columns:
            return quotes
        spot_series = pd.to_numeric(quotes['spread_spot'], errors='coerce')
        mask = spot_series.notna()
        if mask.any():
            quotes.loc[mask, 'par_spread_spot_calc'] = np.where(
                spot_series.loc[mask] > 1,
                spot_series.loc[mask] / 1e4,
                spot_series.loc[mask],
            )
        return quotes

    def _refresh_position_marks(self):
        if self.cdx_opt_pos_df.empty or self.curr_mark.empty:
            return
        updated = 0
        for product in self.cdx_opt_pos_df['ticker'].dropna().unique():
            cm = self.curr_mark[self.curr_mark['product'] == product]
            if cm.empty:
                continue
            mask = self.cdx_opt_pos_df['ticker'] == product
            if 'spot_spread' in cm.columns and cm['spot_spread'].notna().any():
                spot = cm['spot_spread'].dropna().iloc[0]
                self.cdx_opt_pos_df.loc[mask, 'spot_spread'] = spot
                updated += mask.sum()
            if 'spot_price' in cm.columns and cm['spot_price'].notna().any():
                self.cdx_opt_pos_df.loc[mask, 'spot_price'] = cm['spot_price'].dropna().iloc[0]
            grid = self.pricing_grids.get(product)
            if grid and grid.spot_measures and 'spot_spread' in self.cdx_opt_pos_df.columns:
                spot_val = self.cdx_opt_pos_df.loc[mask, 'spot_spread'].iloc[0] if mask.any() else np.nan
                spot_dec = spot_val / 1e4 if pd.notnull(spot_val) and spot_val > 1 else spot_val
                if pd.notnull(spot_dec):
                    self.cdx_opt_pos_df.loc[mask, 'forward_price'] = grid.interp_spot_measure(spot_dec, 'clean_price')
                    self.cdx_opt_pos_df.loc[mask, 'forward_spread'] = spot_dec
                    self.cdx_opt_pos_df.loc[mask, 'risky_pv01'] = grid.interp_spot_measure(spot_dec, 'risky_pv01')
        if updated:
            logging.info(f'Position marks refreshed: {updated} positions updated with latest spots')

    def _refresh_sender_marks(self):
        if not self.product_aggs or self.quotes_df.empty:
            return
        now = datetime.now(NY_TZ)
        mark_time = self.mark_time_override or now.strftime('%H:%M')
        thresh = self.thresh_time_override or '4h'
        window_h = int(thresh.rstrip('h')) if thresh.endswith('h') else 4
        frames = []

        def build_marks(quotes):
            temp_agg = CDXOptionQuoteAgg(quotes.copy(deep=True), adjust_tz=False)
            self._ensure_quote_strike_calcs(temp_agg.quotes)
            temp_agg.set_median_vol_mark_df(
                mark_intervals={MARK_ID: mark_time}, window=f'{window_h}h', calc_risk=False,
                min_quotes=[1], additional_gby=['sender_bank']
            )
            if temp_agg.median_vol_mark_df is None or temp_agg.median_vol_mark_df.empty:
                return pd.DataFrame()
            return temp_agg.median_vol_mark_df.copy()

        for product, agg in self.product_aggs.items():
            if product in self._sender_mark_failures:
                continue
            if agg.quotes.empty:
                continue
            try:
                frame = build_marks(agg.quotes)
                if not frame.empty:
                    frames.append(frame)
                self._sender_mark_failures.discard(product)
            except Exception as e:
                if 'sender_bank' not in agg.quotes.columns or agg.quotes['sender_bank'].nunique(dropna=False) <= 1:
                    logging.warning(f'sender_mark refresh failed for {product}: {e}')
                    self._sender_mark_failures.add(product)
                    continue
                bank_frames = []
                failed_banks = []
                for sender_bank, sender_quotes in agg.quotes.groupby('sender_bank', dropna=False, sort=False):
                    try:
                        frame = build_marks(sender_quotes)
                        if not frame.empty:
                            bank_frames.append(frame)
                    except Exception as bank_error:
                        bank_label = '<missing>' if pd.isna(sender_bank) else str(sender_bank)
                        logging.warning(f'sender_mark refresh failed for {product}/{bank_label}: {bank_error}')
                        failed_banks.append(bank_label)
                if bank_frames:
                    frames.extend(bank_frames)
                    self._sender_mark_failures.discard(product)
                    logging.warning(f'sender_mark refresh salvaged for {product}; dropped {failed_banks}')
                    continue
                logging.warning(f'sender_mark refresh failed for {product}: {e}')
                self._sender_mark_failures.add(product)
        if frames:
            self.sender_mark = _safe_concat(frames)
            logging.info(f'Sender marks refreshed: {len(self.sender_mark)} rows')

    def _load_fresh_hist_trades_cache(self):
        cached = _load_hist_cache('hist_trades', self.todt)
        if cached is None or cached.empty:
            logging.info(f'Hist trades cache missing for {self.todt}')
            return None
        if 'trade_date' not in cached.columns:
            logging.warning(f'Hist trades cache missing trade_date for {self.todt}')
            return None

        trade_dates = pd.to_datetime(cached['trade_date'], errors='coerce').dt.date
        max_dt = trade_dates.max()
        expected = dtutil.dateadd(self.todt, '-1b')
        if pd.isna(max_dt) or max_dt < expected:
            logging.warning(f'Hist trades cache stale for {self.todt}: max_date={max_dt}, expected>={expected}')
            return None

        logging.info(f'Hist trades cache fresh for {self.todt}: rows={len(cached)}, max_date={max_dt}')
        return cached

    def refresh_hist_trades(self):
        t0 = time.time()
        al = self.activity_log
        al.log('Refreshing hist trades...', 'query')
        cached = self._load_fresh_hist_trades_cache()
        if cached is not None:
            self.cdx_opt_hist_trades = cached
            source = 'cache'
        else:
            al.log('Hist trades cache stale/missing; loading from DB...', 'query')
            fresh = sdr_db.get_cdx_opt_trades(
                dtutil.dateadd(self.todt, '-6m'), dtutil.dateadd(self.todt, '-1d'), use_live=False
            )
            self.cdx_opt_hist_trades = _to_ny(fresh)
            _save_hist_cache('hist_trades', self.todt, self.cdx_opt_hist_trades)
            source = 'DB'

        al.log(f'Hist trades refreshed from {source}: {len(self.cdx_opt_hist_trades):,} rows', 'data')
        logging.info(f'Hist trades refresh source={source} rows={len(self.cdx_opt_hist_trades)} elapsed={time.time()-t0:.1f}s')
        self._load_live_data()
        self._bump_plot_versions(include_snapshot=True)

    def apply_hist_trades_from(self, other):
        """Additively transplant freshly-loaded trade frames from `other` onto self.

        Used by the server-side parallel hist refresh: avoids a full loader swap so
        a concurrent grid reprice can swap independently. Only touches trade-related
        state and bumps trade versions; does not disturb vol marks, grids, or curr_mark.
        """
        for attr in ('cdx_opt_hist_trades', 'cdx_opt_live_trades', 'cdx_opt_trades'):
            src = getattr(other, attr, None)
            if src is not None:
                setattr(self, attr, src)
        self._bump_plot_versions(include_snapshot=True)
        logging.info(f'apply_hist_trades_from: copied trade frames '
                     f'(hist={len(self.cdx_opt_hist_trades)}, live={len(self.cdx_opt_live_trades)}, '
                     f'all={len(self.cdx_opt_trades)})')

    def _index_info_missing_curr_mark_products(self):
        if self.index_info is None or 'product' not in self.index_info.columns:
            return True
        if self.curr_mark.empty or 'product' not in self.curr_mark.columns:
            return False
        cm_products = set(self.curr_mark['product'].dropna().unique())
        idx_products = set(self.index_info['product'].dropna().unique())
        missing = cm_products - idx_products
        if missing:
            logging.info(f'ensure_static_data: index_info missing {len(missing)} curr_mark product(s): {sorted(missing)[:5]}')
        return bool(missing)

    def ensure_static_data(self):
        if self.index_info is None or self._index_info_missing_curr_mark_products():
            self.index_info = SDRMarkHelpers.get_index_info()
            logging.info(f'ensure_static_data: index_info loaded ({len(self.index_info)} rows)')
        need_rate_curves = self.rate_curves is None
        if not need_rate_curves and self.rate_curves:
            usd = self.rate_curves.get('USD', {})
            if self.todt not in usd and dtutil.dateadd(self.todt, '-1b') not in usd:
                logging.info(f'ensure_static_data: rate_curves stale (no curve for {self.todt}), refreshing')
                need_rate_curves = True
        if need_rate_curves:
            self.rate_curves = SDRMarkHelpers.get_rate_curves(
                dtutil.dateadd(self.todt, '-3b'), self.todt
            )
            logging.info(f'ensure_static_data: rate_curves loaded ({list(self.rate_curves.keys())})')

    def _hist_vol_mark_max_date(self, product=None):
        df = self.vol_mark_df
        if df is None or df.empty:
            return None
        if product and 'product' in df.columns:
            df = df[df['product'] == product]
            if df.empty:
                return None
        col = 'asof_date_time' if 'asof_date_time' in df.columns else 'asof_date'
        if col not in df.columns:
            return None
        vals = pd.to_datetime(df[col], errors='coerce').dropna()
        return vals.max() if not vals.empty else None

    def hist_vols_stale(self, product=None, max_age='3b'):
        max_dt = self._hist_vol_mark_max_date(product)
        if max_dt is None:
            return True
        stale_before = pd.Timestamp(dtutil.dateadd(self.todt, f'-{max_age}')).date()
        return pd.Timestamp(max_dt).date() < stale_before

    def ensure_hist_vols_current(self, product=None):
        if not self.hist_vols_stale(product=product):
            return False
        lock = getattr(self, '_hist_vol_refresh_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._hist_vol_refresh_lock = lock
        with lock:
            if not self.hist_vols_stale(product=product):
                return False
            self.refresh_hist_vols(use_live=True)
            return True

    def refresh_hist_vols(self, use_live=True):
        al = self.activity_log
        al.log('Refreshing hist vol marks from DB...', 'query')
        fresh = _to_ny(cds_index_vol.get_vol_mark(
            self.fromdt, self.todt, vol_mark_type='median', use_live=use_live
        ))
        self.vol_mark_df = fresh
        _save_hist_cache('hist_vols', self.todt, self.vol_mark_df)
        al.log(f'Hist vol marks refreshed: {len(self.vol_mark_df):,} rows ({self.fromdt} to {self.todt})', 'data')
        self._bump_plot_versions(include_snapshot=True)

    def _clone_progress(self):
        cloned = LoadProgress()
        source = getattr(self, 'progress', None)
        if source is None:
            return cloned
        state = source.to_dict()
        cloned.step = state.get('step', cloned.step)
        cloned.step_label = state.get('label', cloned.step_label)
        cloned.step_num = state.get('num', cloned.step_num)
        cloned.total_steps = state.get('total', cloned.total_steps)
        cloned.error = state.get('error')
        cloned.done = bool(state.get('done', cloned.done))
        cloned.data_ready = dict(state.get('data_ready', cloned.data_ready))
        cloned.products_ready = set(state.get('products_ready', []))
        cloned.snap_ready = bool(getattr(source, 'snap_ready', False))
        return cloned

    def spawn_staged_reprice_loader(self):
        staged = copy.copy(self)
        frame_attrs = [
            'vol_mark_df', 'cdx_opt_hist_trades', 'cdx_opt_live_trades', 'cdx_opt_trades',
            'quotes_df', 'curr_mark', 'sender_mark', 'prev_mark', 'cdx_opt_pos_df', 'grid_pos_df',
        ]
        for attr in frame_attrs:
            value = getattr(self, attr, pd.DataFrame())
            staged.__dict__[attr] = value.copy(deep=True) if isinstance(value, pd.DataFrame) else copy.deepcopy(value)
        if isinstance(getattr(self, 'index_info', None), pd.DataFrame):
            staged.index_info = self.index_info.copy(deep=True)
        staged.rate_curves = copy.deepcopy(self.rate_curves)
        staged.cdx_marker = copy.deepcopy(self.cdx_marker)
        staged._shared_libor = copy.deepcopy(getattr(self, '_shared_libor', None))
        staged._shared_markit = (
            getattr(self, '_shared_markit').copy(deep=True)
            if isinstance(getattr(self, '_shared_markit', None), pd.DataFrame)
            else copy.deepcopy(getattr(self, '_shared_markit', None))
        )
        staged._shared_default_pv = (
            getattr(self, '_shared_default_pv').copy(deep=True)
            if isinstance(getattr(self, '_shared_default_pv', None), pd.DataFrame)
            else copy.deepcopy(getattr(self, '_shared_default_pv', None))
        )
        staged.pricing_grids = dict(self.pricing_grids)
        staged.grid_engine = GridPricingEngine(dict(staged.pricing_grids))
        staged.product_aggs = {
            product: _StubAgg(agg.quotes.copy(deep=True))
            for product, agg in self.product_aggs.items()
            if hasattr(agg, 'quotes')
        }
        staged._plot_cache = {}
        staged._plot_cache_lock = threading.Lock()
        staged._plot_inflight = {}
        staged._product_plot_versions = dict(getattr(self, '_product_plot_versions', {}))
        staged._snapshot_plot_version = getattr(self, '_snapshot_plot_version', 0)
        staged._shared_trade_version = getattr(self, '_shared_trade_version', 0)
        staged._shared_trade_cache = {}
        staged._shared_trade_cache_lock = threading.Lock()
        staged._shared_trade_inflight = {}
        staged._posdf_cache = {}
        staged._posdf_cache_lock = threading.Lock()
        staged._posdf_inflight = {}
        staged._is_plot_prewarming = False
        staged._pending_prewarm_products = set()
        staged._prewarm_lock = threading.Lock()
        staged._is_light_refreshing = False
        staged._light_refresh_ready = True
        staged._sender_mark_failures = set(self._sender_mark_failures)
        staged._warned_missing_marks = set(self._warned_missing_marks)
        staged.progress = self._clone_progress()
        staged.activity_log = ActivityLog()
        staged._missing_grid_cycles = dict(self._missing_grid_cycles)
        staged._grid_repair_backoff = copy.deepcopy(getattr(self, '_grid_repair_backoff', {}))
        staged._missing_grid_rebuild_products = set(self._missing_grid_rebuild_products)
        staged._missing_grid_rebuild_requested = self._missing_grid_rebuild_requested
        staged._session_product_universe = list(self._session_product_universe)
        staged._spot_refresh_generation = getattr(self, '_spot_refresh_generation', 0)
        staged._snapshot_loaded = False
        return staged

    def _apply_live_spot_updates(self, curr_mark=None):
        work = self.curr_mark.copy(deep=True) if curr_mark is None else curr_mark.copy(deep=True)
        if work.empty or 'product' not in work.columns:
            return work, []
        spot_sources = self._resolve_spot_sources()
        if not spot_sources:
            return work, []
        self.grid_engine.grids = self.pricing_grids
        changed_products = []
        products = self.sorted_products(
            [p for p in work['product'].dropna().unique().tolist() if p in spot_sources],
            priority=self.priority_product,
        )
        for product in products:
            info = spot_sources.get(product) or {}
            self.grid_engine.on_spot_update(
                work,
                product,
                info.get('source'),
                info.get('value'),
                sdr_price=info.get('price'),
            )
            changed_products.append(product)
        if changed_products and 'asof_date_time' in work.columns:
            now_ts = pd.Timestamp.now(tz='America/New_York')
            work.loc[work['product'].isin(changed_products), 'asof_date_time'] = now_ts
        self._fill_missing_px_spreads(work)
        return work, changed_products

    def _maybe_save_snapshot(self):
        _SNAP_MIN_INTERVAL = 120
        if (self._last_snapshot_save is None
                or (datetime.now(NY_TZ) - self._last_snapshot_save).total_seconds() >= _SNAP_MIN_INTERVAL):
            self._save_snapshot()

    def refresh_spot_only(self, publish_light_done=True):
        if self._is_light_refreshing:
            return {
                'changed_products': [],
                'spot_products': [],
                'spot_generation': getattr(self, '_spot_refresh_generation', 0),
            }
        self._is_light_refreshing = True
        al = self.activity_log
        try:
            cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
            grid_prods = sorted(self.pricing_grids.keys()) if self.pricing_grids else []
            logging.info(f'Spot refresh start: curr_mark={len(cm_prods)} {cm_prods}, '
                         f'grids={len(grid_prods)} {grid_prods}')
            al.log('Light refresh...', 'light')
            self._load_live_data()
            changed_products = self._known_products()
            new_cm, spot_products = self._apply_live_spot_updates()
            if not new_cm.empty:
                self.curr_mark = new_cm
            changed_products = sorted(set(changed_products) | set(spot_products))
            self._bump_plot_versions(products=changed_products, include_snapshot=False)
            now_ts = datetime.now(NY_TZ)
            self.last_light_refresh = now_ts
            self.last_spot_refresh = now_ts
            self._spot_refresh_generation = getattr(self, '_spot_refresh_generation', 0) + 1
            self.activity_log.broadcast('spot_update', self.get_spot_snapshot(products=changed_products))
            if publish_light_done:
                payload = self.get_light_refresh_payload()
                payload['spot_only'] = True
                self.activity_log.broadcast('light_refresh_done', payload)
            al.log(f'Spot refresh done: {len(self.cdx_opt_live_trades):,} trades, {len(self.quotes_df):,} quotes', 'light')
            return {
                'changed_products': changed_products,
                'spot_products': spot_products,
                'spot_generation': self._spot_refresh_generation,
            }
        finally:
            self._is_light_refreshing = False
            self._light_refresh_ready = True

    def refresh_heavy_from_loaded_state(self, skip_prewarm=False):
        stage_started = time.perf_counter()

        def stage(name):
            nonlocal stage_started
            now = time.perf_counter()
            logging.info('Heavy refresh stage=%s elapsed_ms=%.1f', name, (now - stage_started) * 1000)
            stage_started = now

        al = self.activity_log
        changed_products = self._known_products()
        cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
        grid_prods = sorted(self.pricing_grids.keys()) if self.pricing_grids else []
        logging.info(f'Heavy refresh start: curr_mark={len(cm_prods)} {cm_prods}, grids={len(grid_prods)} {grid_prods}')
        al.log('Background grid repricing...', 'compute')

        self._refresh_product_agg_quotes()
        agg_repair = self.repair_missing_product_aggs()
        stage('quote_aggregates')
        if agg_repair['repaired']:
            changed_products = sorted(set(changed_products) | set(agg_repair['repaired']))

        repair = self.repair_missing_grids()
        if repair['attempted']:
            changed_products = sorted(set(changed_products) | set(repair['attempted']) | set(repair['repaired']))
        self._retry_failed_grid_builds()
        stage('grid_repairs')

        if self.pricing_grids and not self.curr_mark.empty:
            spot_sources = self._resolve_spot_sources()
            all_quotes = self._collect_filtered_agg_quotes()
            self.grid_engine.grids = self.pricing_grids
            all_products = sorted(set(self.pricing_grids.keys()) | set(self.curr_mark['product'].unique()))
            new_cm = self.grid_engine.refresh(
                self.curr_mark.copy(deep=True), all_quotes, all_products, spot_sources,
                progress_cb=lambda msg: al.log(msg, 'compute'),
            )
            changed_products = all_products
            if 'asof_date_time' in new_cm.columns:
                now_ts = pd.Timestamp.now(tz='America/New_York')
                refreshed_mask = new_cm['product'].isin(all_products) & new_cm['implied_vol'].notna()
                new_cm.loc[refreshed_mask, 'asof_date_time'] = now_ts
            self._fill_missing_px_spreads(new_cm)
            self.curr_mark = new_cm
            stage('grid_marks')
            al.log('Grid engine: spots, vols, Greeks refreshed', 'compute')
            self._refresh_position_marks()
            stage('position_marks')
            self._refresh_sender_marks()
            stage('sender_marks')
            self.grid_pos_df = self._grid_reprice_all()
            stage('position_interpolation')
            al.log(f'Grid re-priced: {len(self.grid_pos_df):,} positions', 'data')

        missing_grids = self.get_missing_grid_products()
        self._record_missing_grid_state(missing_grids)
        self._bump_plot_versions(products=changed_products, include_snapshot=False)
        self.last_heavy_refresh = datetime.now(NY_TZ)
        if not skip_prewarm:
            self.queue_prewarm_plot_cache(products=changed_products)
        al.log(f'Heavy refresh done: {len(self.cdx_opt_live_trades):,} trades, {len(self.quotes_df):,} quotes', 'light')
        if self.pricing_grids and not self.curr_mark.empty:
            self._save_snapshot()
            stage('snapshot')
            logging.info('Heavy refresh: snapshot persisted (canonical implied_vol available on next page load even if swap fails)')
        return {'changed_products': changed_products}

    def refresh_live_overlay_from_loaded_state(self, version_source=None, live_data_source=None):
        al = self.activity_log
        changed_products = self._known_products()
        if version_source is not None:
            self._product_plot_versions = dict(
                getattr(version_source, '_product_plot_versions', getattr(self, '_product_plot_versions', {}))
            )
            self._shared_trade_version = max(
                getattr(self, '_shared_trade_version', 0),
                getattr(version_source, '_shared_trade_version', 0),
            )
            self._snapshot_plot_version = max(
                getattr(self, '_snapshot_plot_version', 0),
                getattr(version_source, '_snapshot_plot_version', 0),
            )
            self._spot_refresh_generation = getattr(
                version_source, '_spot_refresh_generation', getattr(self, '_spot_refresh_generation', 0),
            )

        cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
        grid_prods = sorted(self.pricing_grids.keys()) if self.pricing_grids else []
        logging.info(
            f'Live overlay start: curr_mark={len(cm_prods)} {cm_prods}, grids={len(grid_prods)} {grid_prods}'
        )
        al.log('Background grid repricing catching up latest live data...', 'compute')

        if live_data_source is not None:
            self._copy_live_data_from(live_data_source)
            logging.info(f'Live overlay: borrowed live data from peer (no SQL) -- '
                         f'quotes={len(self.quotes_df):,}, live_trades={len(self.cdx_opt_live_trades):,}')
        else:
            self._load_live_data()
        self._refresh_product_agg_quotes()
        agg_repair = self.repair_missing_product_aggs()
        if agg_repair['repaired']:
            changed_products = sorted(set(changed_products) | set(agg_repair['repaired']))

        new_cm, spot_products = self._apply_live_spot_updates()
        if not new_cm.empty:
            self.curr_mark = new_cm

        changed_products = sorted(set(changed_products) | set(spot_products))
        self._refresh_position_marks()
        self._refresh_sender_marks()
        self.grid_pos_df = self._grid_reprice_all()
        missing_grids = self.get_missing_grid_products()
        self._record_missing_grid_state(missing_grids)
        self._bump_plot_versions(products=changed_products, include_snapshot=False)
        now_ts = datetime.now(NY_TZ)
        self.last_light_refresh = now_ts
        self.last_spot_refresh = now_ts
        al.log(f'Live overlay done: {len(self.cdx_opt_live_trades):,} trades, {len(self.quotes_df):,} quotes', 'light')
        return {
            'changed_products': changed_products,
            'spot_products': spot_products,
            'spot_generation': getattr(self, '_spot_refresh_generation', 0),
        }

    def refresh_light(self, skip_grid_refresh=False, skip_prewarm=False):
        spot_result = self.refresh_spot_only(publish_light_done=skip_grid_refresh)
        if skip_grid_refresh:
            if not skip_prewarm and spot_result.get('changed_products'):
                self.queue_prewarm_plot_cache(products=spot_result['changed_products'])
            self._maybe_save_snapshot()
            return spot_result
        heavy_result = self.refresh_heavy_from_loaded_state(skip_prewarm=skip_prewarm)
        payload = self.get_light_refresh_payload()
        payload['spot_only'] = False
        self.activity_log.broadcast('light_refresh_done', payload)
        self._maybe_save_snapshot()
        return heavy_result

    def _resolve_spot_sources(self):
        if self.curr_mark.empty:
            return {}
        all_quotes = (self.quotes_df[self.quotes_df['ticker'].isin(self.tickers)]
                      if not self.quotes_df.empty and 'ticker' in self.quotes_df.columns
                      else pd.DataFrame())
        sources = {}
        for product in self.curr_mark['product'].unique():
            sdr_raw = self._get_sdr_spot(product)
            if isinstance(sdr_raw, dict):
                # SDR always provides both spread (decimal) and price.
                spread_val = sdr_raw.get('spread', np.nan)
                if pd.notna(spread_val):
                    sources[product] = {'source': 'sdr', 'value': spread_val,
                                        'price': sdr_raw.get('price')}
                    continue
                else:
                    logging.warning(f'_resolve_spot_sources({product}): SDR returned dict but spread is NaN: {sdr_raw}')
            elif pd.notna(sdr_raw):
                # Fallback: scalar spread decimal (shouldn't happen now, but safe)
                sources[product] = {'source': 'sdr', 'value': sdr_raw}
                continue
            pq = all_quotes[all_quotes['product'] == product] if not all_quotes.empty else pd.DataFrame()
            is_px = _is_px_index(product)
            spot_col = 'spot' if is_px else 'spread_spot'
            if pq.empty or spot_col not in pq.columns:
                logging.info(f'_resolve_spot_sources({product}): no SDR, no quotes (pq_empty={pq.empty}, col={spot_col})')
                continue
            pq_valid = pq[pq[spot_col].notna()]
            if pq_valid.empty:
                latest_spot = np.nan
            elif 'asof_date_time' in pq_valid.columns:
                pq_sorted = pq_valid.sort_values('asof_date_time', ascending=False)
                latest_spot = pq_sorted[spot_col].iloc[0]
                top3 = pq_sorted[['asof_date_time', spot_col]].head(3).to_dict('records')
                logging.info(f'_resolve_spot_sources({product}): top3={top3}')
            else:
                latest_spot = pq_valid[spot_col].iloc[0]
            if pd.isnull(latest_spot):
                logging.info(f'_resolve_spot_sources({product}): quotes present but {spot_col} all NaN')
                continue
            if _is_px_index(product):
                sources[product] = {'source': 'price', 'value': latest_spot}
            else:
                sources[product] = {'source': 'spread', 'value': latest_spot}
        n_products = len(self.curr_mark['product'].unique())
        src_parts = []
        for p, s in sources.items():
            src_parts.append('%s: %s=%.6f' % (p, s['source'], s['value']))
        logging.info('_resolve_spot_sources: %d/%d resolved: {%s}', len(sources), n_products, ', '.join(src_parts))
        return sources

    # ── Quote Bootstrap & Fallbacks ──────────────────────────

    def _parse_product(self, product):
        parts = product.rsplit('_', 2)
        if len(parts) == 3:
            return parts[0], int(parts[1]), int(parts[2])
        return product, 0, 0

    def _product_score(self, product):
        _, series, version = self._parse_product(product)
        return series * 10000 + version

    def _find_otr_product(self, product):
        ticker, series, version = self._parse_product(product)
        if self._is_otr(product):
            return None
        candidates = [p for p in self.quotes_df['product'].unique()
                      if p.startswith(f'{ticker}_{series}_') and self._is_otr(p)]
        return candidates[0] if candidates else None

    def _find_default_otr_product(self, product, universe=None):
        ticker, _, _ = self._parse_product(product)
        universe = list(universe or self.get_session_product_universe())
        if not universe:
            return None
        candidates = [p for p in universe if self._parse_product(p)[0] == ticker]
        if not candidates:
            return None
        otr_candidates = [p for p in candidates if self._is_otr(p)]
        best = max(otr_candidates or candidates, key=self._product_score)
        return best if best != product else None

    # ── Prev-Day Data (parquet-cached) ────────────────────────

    def _get_prev_day_quotes(self, product, lookback_days=3):
        cache_key = f'prev_quotes_{product}'
        cached = _load_cache(cache_key, self.todt)
        if cached is not None:
            if '_no_data' in cached.columns:
                return pd.DataFrame(), 'cache_miss'
            return cached, 'cache'

        for i in range(1, lookback_days + 1):
            dt = dtutil.dateadd(self.todt, f'-{i}b')
            prev_q = _to_ny(cds_index_vol.get_quotes(dt, dt, product=product, use_live=False))
            if not prev_q.empty:
                _save_cache(cache_key, self.todt, prev_q)
                logging.info(f'[{product}] Prev quotes from {dt}: {len(prev_q)} rows (saved to cache)')
                return prev_q, dt

        _save_cache_raw(cache_key, self.todt, pd.DataFrame({'_no_data': [True]}))
        return pd.DataFrame(), None

    def _get_prev_day_vol_mark(self, product, lookback_days=5):
        cache_key = f'prev_vol_mark_{product}'
        cached = _load_cache(cache_key, self.todt)
        if cached is not None:
            if '_no_data' in cached.columns:
                return pd.DataFrame(), 'cache_miss'
            return cached, 'cache'

        if not self.vol_mark_df.empty:
            prod_vols = self.vol_mark_df[self.vol_mark_df['product'] == product].copy()
            if not prod_vols.empty:
                prod_vols = prod_vols.sort_values('asof_date')
                latest = prod_vols[pd.to_datetime(prod_vols['asof_date']).dt.date < self.todt]
                if not latest.empty:
                    dt = latest['asof_date'].max()
                    result = latest[latest['asof_date'] == dt]
                    _save_cache(cache_key, self.todt, result)
                    return result, dt

        for i in range(1, lookback_days + 1):
            dt = dtutil.dateadd(self.todt, f'-{i}b')
            vm = _to_ny(cds_index_vol.get_vol_mark(dt, dt, product=product, vol_mark_type='median', use_live=False))
            if not vm.empty:
                _save_cache(cache_key, self.todt, vm)
                return vm, dt

        _save_cache_raw(cache_key, self.todt, pd.DataFrame({'_no_data': [True]}))
        return pd.DataFrame(), None

    # ── OTR Vol Deltas ────────────────────────────────────────

    def _compute_otr_vol_deltas(self, otr_product):
        vol_col = 'implied_vol'
        exp_col = 'option_end_date'

        otr_today = self.curr_mark[self.curr_mark['product'] == otr_product] if not self.curr_mark.empty else pd.DataFrame()
        if otr_today.empty or vol_col not in otr_today.columns:
            return {}, np.nan

        otr_prev, _ = self._get_prev_day_vol_mark(otr_product)
        if otr_prev.empty or vol_col not in otr_prev.columns:
            return {}, np.nan

        if exp_col not in otr_today.columns or exp_col not in otr_prev.columns:
            today_med = otr_today[vol_col].dropna().median()
            prev_med = otr_prev[vol_col].dropna().median()
            if pd.notna(today_med) and pd.notna(prev_med) and prev_med > 0:
                return {}, np.log(today_med) - np.log(prev_med)
            return {}, np.nan

        today_by_exp = otr_today.groupby(exp_col)[vol_col].median()
        prev_by_exp = otr_prev.groupby(exp_col)[vol_col].median()

        expiry_deltas = {}
        all_diffs = []
        for exp in today_by_exp.index:
            vt = today_by_exp[exp]
            vp = prev_by_exp.get(exp, np.nan)
            if pd.notna(vt) and pd.notna(vp) and vp > 0 and vt > 0:
                d = np.log(vt) - np.log(vp)
                expiry_deltas[exp] = d
                all_diffs.append(d)

        product_diff = np.median(all_diffs) if all_diffs else np.nan
        return expiry_deltas, product_diff

    def _propagate_otr_vols(self, product, otr_product, prev_vol_mark):
        al = self.activity_log
        vol_col = 'implied_vol'
        exp_col = 'option_end_date'

        if vol_col not in prev_vol_mark.columns:
            return prev_vol_mark

        expiry_deltas, product_diff = self._compute_otr_vol_deltas(otr_product)
        if not expiry_deltas and pd.isna(product_diff):
            al.log(f'[{product}] No OTR vol deltas available from {otr_product}', 'data')
            return prev_vol_mark

        otr_expiries = sorted(expiry_deltas.keys()) if expiry_deltas else []
        prev_vol_mark = prev_vol_mark.copy()

        def _get_log_diff(row_exp):
            if row_exp in expiry_deltas:
                return expiry_deltas[row_exp]
            if otr_expiries:
                nearest = min(otr_expiries, key=lambda e: abs((pd.Timestamp(e) - pd.Timestamp(row_exp)).days))
                return expiry_deltas[nearest]
            return product_diff

        if exp_col in prev_vol_mark.columns:
            prev_vol_mark['_log_diff'] = prev_vol_mark[exp_col].map(_get_log_diff)
        else:
            prev_vol_mark['_log_diff'] = product_diff

        mask = prev_vol_mark['_log_diff'].notna() & prev_vol_mark[vol_col].notna() & (prev_vol_mark[vol_col] > 0)
        prev_vol_mark.loc[mask, vol_col] = prev_vol_mark.loc[mask, vol_col] * np.exp(prev_vol_mark.loc[mask, '_log_diff'])
        prev_vol_mark.drop(columns=['_log_diff'], inplace=True)

        n_adj = mask.sum()
        diff_str = f', product_diff={product_diff:.4f}' if pd.notna(product_diff) else ''
        al.log(f'[{product}] OTR vol propagated from {otr_product}: {n_adj} rows, '
               f'{len(expiry_deltas)} expiry deltas{diff_str}', 'data')
        return prev_vol_mark

    # ── Quote Bootstrap ───────────────────────────────────────

    def _get_otr_series(self):
        otr_series = {}
        if self.index_info is not None and not self.index_info.empty:
            for _, row in self.index_info.iterrows():
                p = row.get('product', '')
                if p and self._is_otr(p):
                    ticker, series, _ = self._parse_product(p)
                    otr_series[ticker] = max(otr_series.get(ticker, 0), series)
        return otr_series

    def _bootstrap_quotes(self):
        al = self.activity_log
        if 'ticker' not in self.quotes_df.columns:
            self.quotes_df = self.quotes_df.copy()
            self.quotes_df['ticker'] = pd.Series(dtype='object')
        if 'product' not in self.quotes_df.columns:
            self.quotes_df['product'] = pd.Series(dtype='object')
        all_quotes = self.quotes_df[self.quotes_df['ticker'].isin(self.tickers)]
        products = set(all_quotes['product'].unique().tolist())

        if self.index_info is not None and not self.index_info.empty:
            for _, row in self.index_info.iterrows():
                p = row.get('product', '')
                if p and any(p.startswith(t) for t in self.tickers):
                    products.add(p)

        if not self.vol_mark_df.empty and 'product' in self.vol_mark_df.columns:
            recent_vols = self.vol_mark_df[
                pd.to_datetime(self.vol_mark_df['asof_date']).dt.date >= dtutil.dateadd(self.todt, '-5b')
            ] if 'asof_date' in self.vol_mark_df.columns else self.vol_mark_df
            for p in recent_vols['product'].unique():
                if any(p.startswith(t) for t in self.tickers):
                    products.add(p)

        logging.info(f'_bootstrap_quotes: {len(products)} products from quotes={len(all_quotes["product"].unique())}, '
                     f'index_info={"yes" if self.index_info is not None else "None"}, vol_marks={"yes" if not self.vol_mark_df.empty else "empty"}')

        otr_series = self._get_otr_series()
        max_series_gap = 5

        bootstrapped = []
        skipped = 0
        skipped_old = 0
        for product in products:
            ticker, series, _ = self._parse_product(product)
            otr_s = otr_series.get(ticker, 0)
            if otr_s and series < otr_s - max_series_gap:
                skipped_old += 1
                continue
            pq = all_quotes[all_quotes['product'] == product]
            if len(pq) >= MIN_QUOTES_FOR_BOOTSTRAP:
                continue

            prev_q, prev_dt = self._get_prev_day_quotes(product)
            if prev_q.empty:
                skipped += 1
                logging.debug(f'[{product}] No prev day quotes ({prev_dt})')
                continue

            if not self._is_otr(product):
                otr = self._find_otr_product(product)
                if otr:
                    prev_q = self._apply_otr_spot_adjustment(product, prev_q, otr)

            bootstrapped.append(prev_q)
            al.log(f'[{product}] Bootstrapped {len(prev_q)} quotes from {prev_dt}', 'data')

        if bootstrapped:
            bootstrapped_df = pd.concat(bootstrapped, ignore_index=True)
            common_cols = [c for c in all_quotes.columns if c in bootstrapped_df.columns]
            all_quotes = pd.DataFrame(
                all_quotes[common_cols].to_dict('records') + bootstrapped_df[common_cols].to_dict('records')
            )

        al.log(f'Bootstrap: {len(bootstrapped)} filled, {skipped} no prev data, {skipped_old} ancient series skipped', 'data')
        return all_quotes

    def _apply_otr_spot_adjustment(self, product, prev_quotes, otr_product):
        al = self.activity_log
        otr_today = self.quotes_df[self.quotes_df['product'] == otr_product]
        if otr_today.empty or 'spot' not in otr_today.columns:
            return prev_quotes

        otr_spot_today = otr_today['spot'].dropna()
        if otr_spot_today.empty:
            return prev_quotes

        otr_prev_q, _ = self._get_prev_day_quotes(otr_product)
        if otr_prev_q.empty or 'spot' not in otr_prev_q.columns:
            return prev_quotes

        otr_spot_prev = otr_prev_q['spot'].dropna()
        if otr_spot_prev.empty or otr_spot_prev.iloc[-1] == 0:
            return prev_quotes

        pct_chg = otr_spot_today.iloc[-1] / otr_spot_prev.iloc[-1]
        if 'spot' in prev_quotes.columns:
            prev_quotes = prev_quotes.copy()
            prev_quotes['spot'] = prev_quotes['spot'] * pct_chg
            al.log(f'[{product}] OTR spot adj: {otr_product} pct_chg={pct_chg:.6f}', 'data')
        return prev_quotes

    # ── Aggregation Pipeline ─────────────────────────────────

    def _run_product_agg(self, product, product_quotes, mark_time, mark_date=None, cached_grid=None):
        al = self.activity_log
        tag = f'[{product}]'
        mark_date = mark_date or self.todt
        quote_fallback_dt = None

        grid = cached_grid if cached_grid is not None else self.pricing_grids.get(product)
        has_grid = _grid_is_usable(grid)

        def _grid_engine(active_grid):
            if not _grid_is_usable(active_grid):
                return None
            return GridPricingEngine({product: active_grid})

        if self._has_grid_error(product) and not self.should_rebuild_grid():
            error_age = self._grid_error_age_seconds(product)
            if error_age is not None and error_age < _GRID_RETRY_INTERVAL_S:
                if has_grid:
                    logging.info(f'{tag} Grid error on file but cached grid exists -- using cache')
                else:
                    logging.info(f'{tag} SKIPPED -- grid error ({error_age:.0f}s old), no cached grid')
                    return {}
            else:
                logging.info(f'{tag} Grid error stale ({error_age:.0f}s old) — retrying build')

        if product_quotes.empty:
            prev_quotes, prev_quote_dt = self._get_prev_day_quotes(product)
            if not prev_quotes.empty:
                otr_product = self._find_otr_product(product)
                if otr_product:
                    prev_quotes = self._apply_otr_spot_adjustment(product, prev_quotes, otr_product)
                product_quotes = prev_quotes.copy()
                quote_fallback_dt = prev_quote_dt
                logging.info(f'{tag} quote fallback from {prev_quote_dt}: {len(product_quotes)} rows')
            else:
                prev_vm, prev_dt = self._get_prev_day_vol_mark(product)
                if not prev_vm.empty:
                    rebuilt_grid = self._build_grid(product, prev_vm.copy(), None)
                    if _grid_is_usable(rebuilt_grid):
                        enriched = _grid_engine(rebuilt_grid).enrich_marks(prev_vm.copy(), product)
                        logging.info(f'{tag} zero quotes -> rebuilt grid from prev day vol marks ({prev_dt})')
                        return {'curr_mark': enriched, 'sender_mark': pd.DataFrame(), 'grid': rebuilt_grid, 'agg': None}
                if has_grid and not prev_vm.empty:
                    enriched = _grid_engine(grid).enrich_marks(prev_vm.copy(), product)
                    logging.info(f'{tag} zero quotes -> carry-forward grid with prev day vol marks ({prev_dt})')
                    return {'curr_mark': enriched, 'sender_mark': pd.DataFrame(), 'grid': grid, 'agg': None}
                if has_grid:
                    logging.info(f'{tag} zero quotes -> cached grid only')
                    return {'curr_mark': pd.DataFrame(), 'sender_mark': pd.DataFrame(), 'grid': grid, 'agg': None}
                logging.info(f'{tag} SKIPPED -- zero quotes for product')
                return {}

        if 'asof_date' in product_quotes.columns:
            n_before = len(product_quotes)
            product_quotes = product_quotes[product_quotes['asof_date'].notna()].copy()
            n_dropped = n_before - len(product_quotes)
            if n_dropped:
                logging.warning(f'{tag} dropped {n_dropped} quotes with null asof_date')
            if product_quotes.empty:
                logging.info(f'{tag} SKIPPED -- all quotes had null asof_date')
                return {}

        logging.info(f'{tag} CDXOptionQuoteAgg: {len(product_quotes):,} quotes')

        shared_markit = getattr(self, '_shared_markit', None)
        product_markit = None
        if shared_markit is not None:
            pm = shared_markit[shared_markit['ticker'] == product]
            if not pm.empty:
                product_markit = pm.copy()

        agg = CDXOptionQuoteAgg(
            product_quotes,
            libor_curves=getattr(self, '_shared_libor', None),
            markit_df=product_markit,
            default_pv_df=getattr(self, '_shared_default_pv', None),
            adjust_tz=False,
        )

        thresh = self.thresh_time_override or '4h'
        window_h = int(thresh.rstrip('h')) if thresh.endswith('h') else 4
        if quote_fallback_dt is not None:
            window_h = max(window_h, 24)
            quote_dt = pd.to_datetime(product_quotes['asof_date'], errors='coerce').dropna()
            fallback_dt = quote_dt.dt.date.max() if not quote_dt.empty else dtutil.todate(quote_fallback_dt)
            end_time_str = f'{fallback_dt} 23:59'
        else:
            end_time_str = f'{mark_date} {mark_time}'
        agg.filter_quotes(from_threshold=f'{window_h}h', to_threshold='0h', end_time=end_time_str)

        if self._is_otr(product):
            while len(agg.quotes) < MIN_QUOTES_FOR_BOOTSTRAP and window_h < 24:
                window_h += 6
                agg = CDXOptionQuoteAgg(
                    product_quotes,
                    libor_curves=getattr(self, '_shared_libor', None),
                    markit_df=product_markit,
                    default_pv_df=getattr(self, '_shared_default_pv', None),
                    adjust_tz=False,
                )
                agg.filter_quotes(from_threshold=f'{window_h}h', to_threshold='0h', end_time=end_time_str)
                logging.info(f'{tag} OTR window expanded to {window_h}h => {len(agg.quotes)} quotes')

        if not agg.quotes.empty and 'asof_date_time' in agg.quotes.columns:
            qt = pd.to_datetime(agg.quotes['asof_date_time'])
            logging.info(f'{tag} filter window={window_h}h ending={end_time_str}, quotes={len(agg.quotes)}, '
                         f'time_range=[{qt.min()} to {qt.max()}]')
        else:
            logging.info(f'{tag} filter window={window_h}h ending={end_time_str}, quotes={len(agg.quotes)}')
        logging.info(f'{tag} Filtered: {len(agg.quotes):,} quotes (window={window_h}h)')

        grid_is_stale = has_grid and grid.valuedt != self.todt

        if agg.quotes.empty and has_grid:
            prev_vm, prev_dt = self._get_prev_day_vol_mark(product)
            # If grid is stale (wrong valuation date), rebuild from prev vols
            # so premiums/greeks reflect today's time-to-expiry and curves.
            # Only the vols carry forward — everything else is recalculated.
            if grid_is_stale and not prev_vm.empty:
                logging.info(f'{tag} Stale grid (valuedt={grid.valuedt}), rebuilding from prev vols for {self.todt}')
                rebuilt = self._build_grid(product, prev_vm.copy(), None)
                if _grid_is_usable(rebuilt):
                    grid, has_grid = rebuilt, True
                    enriched = _grid_engine(rebuilt).enrich_marks(prev_vm.copy(), product)
                    logging.info(f'{tag} stale grid rebuilt from prev day vol marks ({prev_dt}) for {self.todt}')
                    return {'curr_mark': enriched, 'sender_mark': pd.DataFrame(), 'grid': rebuilt, 'agg': None}
                else:
                    logging.warning(f'{tag} stale grid rebuild failed (0 options), '
                                    f'falling back to stale grid from {grid.valuedt}')
                    self._save_grid_error(product, f'stale grid rebuild failed, using old grid from {grid.valuedt}')
            if not prev_vm.empty:
                enriched = _grid_engine(grid).enrich_marks(prev_vm.copy(), product)
                logging.info(f'{tag} carry-forward grid with prev day vol marks ({prev_dt})')
                return {'curr_mark': enriched, 'sender_mark': pd.DataFrame(), 'grid': grid, 'agg': None}
            logging.info(f'{tag} No quotes + cached grid -- carry-forward grid only')
            return {'curr_mark': pd.DataFrame(), 'sender_mark': pd.DataFrame(), 'grid': grid, 'agg': None}

        sdr_raw = self._get_sdr_spot(product)
        is_px = _is_px_index(product)
        sdr_spread_fixup = np.nan
        spot_value = np.nan

        if isinstance(sdr_raw, dict):
            spot_value = sdr_raw['price']
            sdr_spread_fixup = sdr_raw['spread']
            if pd.notna(spot_value):
                logging.info(f'{tag} SDR spot: px={spot_value:.4f} spd={sdr_spread_fixup*1e4:.2f}bps (EWMA, price-based)')
                al.log(f'{tag} SDR spot: {spot_value:.4f} pts', 'data')
            else:
                logging.warning(f'{tag} SDR returned dict but price=NaN, spd={sdr_spread_fixup} — falling through to quotes')
        elif pd.notna(sdr_raw):
            spot_value = sdr_raw
            logging.info(f'{tag} SDR spot={sdr_raw*1e4:.2f} bps (EWMA, spread-based)')
            al.log(f'{tag} SDR spot: {sdr_raw*1e4:.2f} bps', 'data')

        if pd.isna(spot_value):
            if not agg.quotes.empty:
                spot_col = 'spot' if is_px else 'spread_spot'
                if spot_col in agg.quotes.columns:
                    last_quote_df = agg.quotes.groupby(
                        ['sender_bank', 'product', 'asof_date', 'asof_date_time']
                    )[spot_col].agg(lambda x: x.iloc[-1]).reset_index()
                    if not last_quote_df.empty:
                        spot_df = last_quote_df.rename(columns={spot_col: 'last_spot'}).groupby('product').apply(
                            calc_wt_avg
                        ).reset_index(drop=True)
                        spot_row = spot_df.drop_duplicates('product', keep='last')
                        if 'ewa_spot' in spot_row.columns and not spot_row['ewa_spot'].isna().all():
                            spot_value = spot_row['ewa_spot'].iloc[0]
            if pd.isna(spot_value):
                raw_col = 'spot' if is_px else 'spread_spot'
                raw_val = product_quotes[raw_col].dropna().iloc[-1] if raw_col in product_quotes.columns and product_quotes[raw_col].notna().any() else np.nan
                spot_value = raw_val
                logging.warning(f'{tag} SDR+quote ewa_spot unavailable, using raw {raw_col}: {spot_value}')

        if pd.isna(spot_value) and not self._is_otr(product):
            otr = self._find_otr_product(product)
            if otr:
                otr_raw = self._get_sdr_spot(otr)
                otr_spot = otr_raw['price'] if isinstance(otr_raw, dict) else otr_raw
                if pd.notna(otr_spot):
                    prev_vm, _ = self._get_prev_day_vol_mark(product)
                    otr_prev_vm, _ = self._get_prev_day_vol_mark(otr)
                    if not prev_vm.empty and not otr_prev_vm.empty:
                        prev_spot_col = 'spot_spread' if 'spot_spread' in prev_vm.columns else 'spot_price'
                        prev_spot = prev_vm[prev_spot_col].dropna().iloc[0] if prev_vm[prev_spot_col].notna().any() else np.nan
                        otr_prev_spot = otr_prev_vm[prev_spot_col].dropna().iloc[0] if otr_prev_vm[prev_spot_col].notna().any() else np.nan
                        if pd.notna(prev_spot) and pd.notna(otr_prev_spot) and otr_prev_spot > 0:
                            pct_chg = otr_spot / otr_prev_spot
                            spot_value = prev_spot * pct_chg
                            al.log(f'{tag} OTR spot fallback via {otr}: pct_chg={pct_chg:.6f} => {spot_value:.2f}', 'data')

        if pd.isna(spot_value):
            al.log(f'{tag} SKIPPED -- no spot available (SDR + quotes + OTR fallback)', 'error')
            self._save_grid_error(product, 'no spot available from SDR or quotes')
            return {}

        if agg.quotes.empty:
            al.log(f'{tag} No quotes -- attempting bootstrap from prev day vol marks', 'data')
            prev_vm, prev_dt = self._get_prev_day_vol_mark(product)
            if prev_vm.empty:
                al.log(f'{tag} SKIPPED -- no quotes and no prev day vol marks', 'error')
                self._save_grid_error(product, f'no quotes and no prev day vol marks (spot={spot_value:.2f})')
                return {}
            otr = self._find_otr_product(product)
            if otr:
                prev_vm = self._propagate_otr_vols(product, otr, prev_vm)
            al.log(f'{tag} Building grid from prev day vol marks ({prev_dt}, {len(prev_vm)} rows)...', 'compute')
            new_grid = self._build_grid(product, prev_vm, agg)
            if _grid_is_usable(new_grid):
                al.log(f'{tag} Grid built from bootstrapped vols ({len(new_grid.grids)} options)', 'data')
                return {'curr_mark': prev_vm, 'sender_mark': pd.DataFrame(), 'grid': new_grid, 'agg': agg}
            else:
                al.log(f'{tag} SKIPPED -- grid build from prev vols failed', 'error')
                self._save_grid_error(product, 'grid build from prev day vols failed')
                return {}

        rebuild = self.should_rebuild_grid()
        need_option_calcs = (not has_grid) or rebuild or grid_is_stale
        if grid_is_stale:
            logging.info(f'{tag} grid stale (valuedt={grid.valuedt} != {self.todt}), forcing option_calcs + rebuild')
        al.log(f'{tag} Spot adj (option_calcs={need_option_calcs}, rebuild={rebuild})...', 'compute')
        agg.calc_spot_adj(**{product: spot_value}, option_calcs=need_option_calcs)
        if is_px and pd.notnull(sdr_spread_fixup):
            agg.quotes.loc[agg.quotes['product'] == product, 'spread_spot_adj'] = sdr_spread_fixup
        al.log(f'{tag} Spot adj done', 'data')
        self._ensure_quote_strike_calcs(agg.quotes)

        if has_grid and not need_option_calcs:
            self._sync_quote_spot_calc_from_raw(agg.quotes, product)

        vol_cols = ['pay_vol_bid_calc', 'pay_vol_ask_calc', 'rec_vol_bid_calc', 'rec_vol_ask_calc']
        if has_grid and not need_option_calcs:
            _grid_engine(grid).on_quote_vols(agg.quotes, product)
            vol_filled = {c: agg.quotes[c].notna().sum() for c in vol_cols if c in agg.quotes.columns}
            logging.info(f'{tag} after grid_enrich: vol_filled={vol_filled}')
            al.log(f'{tag} Grid implied vols on {len(agg.quotes):,} quotes', 'data')
        else:
            vol_filled = {c: agg.quotes[c].notna().sum() for c in vol_cols if c in agg.quotes.columns}
            logging.info(f'{tag} {"rebuild" if rebuild else "no grid"}, option_calcs vol_filled={vol_filled}')
            for _vc in vol_cols:
                if _vc not in agg.quotes.columns:
                    agg.quotes[_vc] = np.nan

        use_calc_risk = rebuild or not has_grid
        min_q = [2] if use_calc_risk else [1]
        al.log(f'{tag} Vol marks (calc_risk={use_calc_risk})...', 'compute')
        agg.set_median_vol_mark_df(
            mark_intervals={MARK_ID: mark_time}, window=f'{window_h}h',
            calc_risk=use_calc_risk, min_quotes=min_q,
            index_info=self.index_info, rate_curves=self.rate_curves,
        )
        curr = agg.median_vol_mark_df
        if curr is None:
            curr = pd.DataFrame()
        else:
            curr = curr.copy()
        logging.info(f'{tag} set_median_vol_mark_df => curr_mark={len(curr)} rows')

        if not curr.empty and 'spot_spread' in curr.columns:
            prod_q = agg.quotes[agg.quotes['product'] == product]
            ss = prod_q['spread_spot_adj'].dropna()
            raw_spot = prod_q['spot'].dropna()
            raw_spread_spot = prod_q['spread_spot'].dropna()
            logging.info(f'{tag} SPOT_DATA: is_px={is_px}, quotes={len(prod_q)}, '
                         f'spread_spot_adj={ss.iloc[0] if len(ss) else "EMPTY"}, '
                         f'spot={raw_spot.iloc[0] if len(raw_spot) else "EMPTY"}, '
                         f'spread_spot={raw_spread_spot.iloc[0] if len(raw_spread_spot) else "EMPTY"}')
            
            if not is_px:
                if not ss.empty:
                    ss_val = ss.iloc[0]
                    final_val = ss_val if ss_val > 1 else ss_val * 1e4
                    curr['spot_spread'] = final_val
                    logging.info(f'{tag} SPOT_SPREAD[spread-based]: {ss_val:.6f} -> {final_val:.2f}bps')
                elif not raw_spread_spot.empty:
                    s = raw_spread_spot.iloc[-1]
                    final_val = s if s > 1 else s * 1e4
                    curr['spot_spread'] = final_val
                    logging.info(f'{tag} SPOT_SPREAD[spread-based-raw]: {s:.6f} -> {final_val:.2f}bps')
            else:
                if not raw_spot.empty:
                    pa = prod_q['spot_adj'].dropna()
                    curr['spot_price'] = pa.iloc[0] if not pa.empty else raw_spot.iloc[0]
                    logging.info(f'{tag} SPOT_PRICE[price-based]: {curr["spot_price"].iloc[0]:.4f}')
                    
                px_for_spread = (
                    curr['spot_price'].dropna().iloc[0]
                    if 'spot_price' in curr.columns and curr['spot_price'].notna().any()
                    else spot_value
                )
                spot_spread_resolved = np.nan

                if has_grid and grid.spot_measures:
                    spread_dec = grid.price_to_spread(px_for_spread)
                    if pd.notnull(spread_dec):
                        spot_spread_resolved = spread_dec * 1e4
                        logging.info(f'{tag} SPOT_SPREAD[price-based-grid]: px={px_for_spread:.4f} -> {spread_dec*1e4:.2f}bps')

                if pd.isna(spot_spread_resolved) and pd.notnull(sdr_spread_fixup):
                    spot_spread_resolved = sdr_spread_fixup * 1e4
                    logging.info(f'{tag} SPOT_SPREAD[price-based-sdr]: {sdr_spread_fixup*1e4:.2f}bps')

                if pd.isna(spot_spread_resolved):
                    solved_spread = self._solve_spread_from_price(product, px_for_spread, agg)
                    if pd.notnull(solved_spread):
                        spot_spread_resolved = solved_spread * 1e4
                        logging.info(f'{tag} SPOT_SPREAD[price-based-solved]: '
                                     f'px={px_for_spread:.4f} -> {solved_spread*1e4:.2f}bps')

                if pd.notnull(spot_spread_resolved):
                    curr['spot_spread'] = spot_spread_resolved
                else:
                    curr['spot_spread'] = np.nan
                    logging.warning(f'{tag} SPOT_SPREAD[price-based-none]: '
                                    f'grid p2s + SDR + solver all failed')

        bootstrapped_from_prev = False
        if curr.empty:
            prev_vm, prev_dt = self._get_prev_day_vol_mark(product)
            if not prev_vm.empty:
                otr = self._find_otr_product(product)
                if otr:
                    prev_vm = self._propagate_otr_vols(product, otr, prev_vm)
                curr = prev_vm.copy()
                bootstrapped_from_prev = True
                al.log(f'{tag} Vol mark bootstrapped from {prev_dt} ({len(curr)} rows)', 'data')
            elif not has_grid:
                al.log(f'{tag} No grid and no vol marks (incl prev day)', 'data')
                self._save_grid_error(product, 'no vol marks to build grid')

        need_grid_build = (not has_grid) or rebuild or bootstrapped_from_prev or grid_is_stale
        if need_grid_build and not curr.empty:
            reason = ('bootstrapped' if bootstrapped_from_prev
                      else 'stale grid' if grid_is_stale
                      else 'scheduled' if rebuild
                      else 'no cached grid')
            al.log(f'{tag} Building grid ({reason})...', 'compute')
            new_grid = self._build_grid(product, curr, agg)
            if _grid_is_usable(new_grid):
                grid, has_grid = new_grid, True
            else:
                msg = 'grid build returned 0 options (all spot curves failed?)'
                logging.warning(f'{tag} {msg} — stale grid from {grid.valuedt if has_grid else "N/A"} remains')
                if not has_grid:
                    self._save_grid_error(product, msg)
                al.log(f'{tag} Grid rebuild failed: {msg}', 'error')
        elif need_grid_build and curr.empty and not has_grid:
            logging.warning(f'{tag} need_grid_build=True but curr_mark is empty and no cached grid')
            self._save_grid_error(product, 'need grid build but no vol marks available')
        elif has_grid and not need_grid_build:
            al.log(f'{tag} Grid from cache ({len(grid.grids)} options)', 'data')

        if has_grid and not curr.empty:
            engine = _grid_engine(grid)
            curr = engine.enrich_marks(curr, product)
            # Ensure spot_price is populated from the grid for all products
            if 'price_spot_calc' in curr.columns and curr['price_spot_calc'].notna().any():
                mask_no_px = curr['spot_price'].isna() if 'spot_price' in curr.columns else pd.Series(True, index=curr.index)
                curr.loc[mask_no_px, 'spot_price'] = curr.loc[mask_no_px, 'price_spot_calc']
            # For price-based products, also ensure spot_spread is set from par_spread_spot_calc
            if is_px and 'par_spread_spot_calc' in curr.columns and curr['par_spread_spot_calc'].notna().any():
                mask_no_spd = curr['spot_spread'].isna() if 'spot_spread' in curr.columns else pd.Series(True, index=curr.index)
                n_fill = mask_no_spd.sum()
                if n_fill > 0:
                    sample_val = curr.loc[mask_no_spd, 'par_spread_spot_calc'].dropna().iloc[0] if curr.loc[mask_no_spd, 'par_spread_spot_calc'].notna().any() else np.nan
                    curr.loc[mask_no_spd, 'spot_spread'] = curr.loc[mask_no_spd, 'par_spread_spot_calc'] * 1e4
                    logging.info(f'{tag} SPOT_SPREAD_SET[6-par_spread]: filled {n_fill} rows, '
                                 f'sample par_spread={sample_val:.6f} -> {sample_val*1e4:.2f}bps')
            pq = agg.quotes[agg.quotes['product'] == product] if (
                'product' in agg.quotes.columns and not agg.quotes.empty) else pd.DataFrame()
            if not pq.empty:
                engine._update_live_vols(curr, pq, product)
                curr = engine.enrich_marks(curr, product)
            al.log(f'{tag} Vol marks: {len(curr):,} rows (Greeks from grid)', 'data')
        else:
            al.log(f'{tag} Vol marks: {len(curr):,} rows (no grid)', 'data')

        al.log(f'{tag} Sender marks (by sender_bank)...', 'compute')
        agg.set_median_vol_mark_df(
            mark_intervals={MARK_ID: mark_time}, window=f'{window_h}h', calc_risk=False,
            min_quotes=[1], additional_gby=['sender_bank']
        )
        sender = agg.median_vol_mark_df
        if sender is None:
            sender = pd.DataFrame()
        else:
            sender = sender.copy()
        al.log(f'{tag} Sender marks: {len(sender):,} rows', 'data')

        return {'curr_mark': curr, 'sender_mark': sender, 'grid': grid, 'agg': agg}

    def _solve_spread_from_price(self, product, px, agg=None):
        if pd.isnull(px) or px <= 0:
            return np.nan
        libor_curve, index_coupon, recovery, maturity = self._resolve_curve_inputs(product, agg)
        if libor_curve is None or pd.isna(index_coupon) or pd.isna(recovery) or maturity is None:
            return np.nan
        try:
            return CDXOptionQuoteAgg.solve_price_or_spread(
                self.todt, libor_curve, maturity, index_coupon, recovery, '0d', px=px
            )
        except Exception:
            logging.warning(f'[{product}] solve_spread_from_price failed for px={px}')
            return np.nan

    @staticmethod
    def _series_product_rows(df, product):
        if df is None or df.empty or 'product' not in df.columns:
            return pd.DataFrame()
        exact = df[df['product'] == product]
        if not exact.empty:
            return exact
        stem, separator, version = product.rpartition('_')
        if not separator or not version.isdigit():
            return exact
        return df[df['product'].astype(str).str.startswith(f'{stem}_')]

    def _resolve_curve_inputs(self, product, agg=None):
        libor_curve = None
        index_coupon = np.nan
        recovery = np.nan
        maturity = None
        if agg is not None and hasattr(agg, 'cdx_curves_df_orig'):
            curves = self._series_product_rows(agg.cdx_curves_df_orig, product)
            if not curves.empty:
                curve_product = curves['product'].iloc[0]
                libor_curve = agg.libor_curves.get(max(agg.asof_dates))
                index_coupon = curves['running_coupon'].iloc[0]
                recovery = curves['real_recovery'].iloc[0]
                maturity = agg._get_product_mty(curve_product)
                if curve_product != product:
                    logging.info(f'[{product}] using same-series curve inputs from {curve_product}')
        if libor_curve is None:
            shared_libor = getattr(self, '_shared_libor', None) or {}
            if shared_libor:
                libor_curve = shared_libor.get(self.todt) or shared_libor.get(max(shared_libor))
        if libor_curve is None and self.rate_curves:
            usd_curves = self.rate_curves.get('USD') or {}
            if usd_curves:
                libor_curve = usd_curves.get(self.todt)
                if libor_curve is None:
                    last_dt = max(usd_curves)
                    last_curve = usd_curves.get(last_dt)
                    if last_curve is not None:
                        libor_curve = copy.copy(last_curve)
                        libor_curve._valuation_date = tofindate(self.todt)
        if (pd.isna(index_coupon) or pd.isna(recovery) or maturity is None) and self.index_info is not None and not self.index_info.empty:
            match = self._series_product_rows(self.index_info, product)
            if not match.empty:
                row = match.iloc[0]
                matched_product = row['product']
                if pd.isna(index_coupon):
                    index_coupon = row.get('running_coupon', row.get('coupon', np.nan))
                if pd.isna(recovery):
                    recovery = row.get('real_recovery', row.get('recovery_rate', np.nan))
                if maturity is None:
                    maturity = row.get('maturity_5y', None)
                if matched_product != product:
                    logging.info(f'[{product}] using same-series index inputs from {matched_product}')
        return libor_curve, index_coupon, recovery, maturity

    def _build_grid(self, product, curr, agg):
        tag = f'[{product}]'
        grid = PricingGrid(product, self.todt, is_price_based(product))
        libor_curve, index_coupon, recovery, maturity = self._resolve_curve_inputs(product, agg)

        if libor_curve is None or pd.isna(index_coupon) or pd.isna(recovery) or maturity is None:
            logging.warning(f'{tag} No CDS curves data for product -- cannot build grid')
            self._save_grid_error(product, 'no CDS curves data')
            self.activity_log.log(f'{tag} Grid build skipped: no CDS curves data', 'error')
            return None
        try:
            grid.build(
                curr_mark=curr,
                libor_curve=libor_curve,
                index_coupon=index_coupon,
                recovery=recovery,
                maturity=maturity,
            )
            if grid.grids:
                grid.to_parquet()
                self.activity_log.log(f'{tag} Pricing grid: {len(grid.grids)} options (cached)', 'data')
            else:
                reason = 'all spot curves failed (ZeroDivisionError)' if not grid.grids else 'empty grid'
                logging.warning(f'{tag} Grid build produced 0 options — {reason}')
                self._save_grid_error(product, reason)
                self.activity_log.log(f'{tag} Grid build produced 0 options — {reason}', 'error')
            return grid
        except Exception as e:
            logging.exception(f'{tag} Grid build failed')
            self._save_grid_error(product, e)
            self.activity_log.log(f'{tag} Grid build failed: {str(e)[:100]}', 'error')
            return None

    # ── Mark Time ─────────────────────────────────────────────

    def _resolve_mark_time(self):
        if self.mark_time_override and ':' in self.mark_time_override:
            return self.mark_time_override.strip()
        now = datetime.now(NY_TZ)
        return f'{now.hour:02d}:{now.minute:02d}'

    def get_status_info(self):
        now = datetime.now(NY_TZ)
        snap_save = self._snapshot_save_time or self._last_snapshot_save
        missing_grids = self.get_missing_grid_products()
        missing_aggs = self.get_missing_agg_products()
        return {
            'now_local': now.strftime('%H:%M'),
            'mark_time': self._resolve_mark_time(),
            'thresh_time': self.thresh_time_override or '4h',
            'status': 'live',
            'grids_loaded': len(self.pricing_grids),
            'missing_grid_count': len(missing_grids),
            'missing_grid_products': missing_grids,
            'missing_agg_count': len(missing_aggs),
            'missing_agg_products': missing_aggs,
            'missing_grid_rebuild_pending': self._missing_grid_rebuild_requested,
            'todt': str(self.todt),
            'last_load': snap_save.strftime('%m/%d %H:%M') if snap_save else None,
            'snapshot_from': str(self._snapshot_date) if self._snapshot_date else None,
        }

    def _prefetch_shared_market_data(self, all_quotes):
        from pricing_wrapper.market.curves.cds_curve import get_fin_cds_index_curves
        from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
        from pricing_wrapper.utils.date_funcs import tofindate

        t0 = time.time()
        al = self.activity_log

        if all_quotes.empty:
            asof_dates = [self.todt]
        else:
            asof_dates = sorted(pd.to_datetime(all_quotes['asof_date'], errors='coerce').dropna().dt.date.unique())
        if not asof_dates:
            asof_dates = [self.todt]
        fromdt = dtutil.dateadd(min(asof_dates), '-1b')
        todt = max(asof_dates)
        dts = list(dtutil.daterange(fromdt, todt, step='1d', use_bday=False))

        al.log('Pre-fetch: LIBOR curves', 'query')
        libor = get_fin_curves_by_ccy(fromdt, todt, 'USD')
        for _dt in dts:
            if _dt not in libor:
                prev = libor.get(dtutil.dateadd(_dt, '-1d'))
                if prev:
                    prev._valuation_date = tofindate(_dt)
                    libor[_dt] = prev
        self._shared_libor = libor
        logging.info(f'Pre-fetch LIBOR: {time.time()-t0:.1f}s, {len(libor)} curves')

        if all_quotes.empty:
            all_tickers = sorted(self._known_products())
        else:
            all_tickers = all_quotes['product'].dropna().unique().tolist()
        al.log(f'Pre-fetch: markit CDS curves [{len(all_tickers)} tickers]', 'query')
        t1 = time.time()
        self._shared_markit = get_fin_cds_index_curves(
            fromdt, todt, None, all_tickers, as_df=True,
            tenor='5Y', use_full_ticker=True, max_series_version_only=False,
        )
        logging.info(f'Pre-fetch markit: {time.time()-t1:.1f}s, {len(self._shared_markit)} rows')

        al.log('Pre-fetch: default PV', 'query')
        t2 = time.time()
        self._shared_default_pv = cds_index.get_default_pv_by_ticker(dt=todt)
        logging.info(f'Pre-fetch default_pv: {time.time()-t2:.1f}s')

        logging.info(f'Pre-fetch total: {time.time()-t0:.1f}s')
        al.log(f'Pre-fetch complete: {time.time()-t0:.1f}s', 'data')

    def _run_agg_pipeline(self, p, on_product_ready=None):
        al = self.activity_log
        mark_time = self._resolve_mark_time()
        mark_date = self.todt

        p.advance('agg_pipeline')
        p.products_ready.clear()
        try:
            self.try_load_grids()
        except Exception:
            logging.exception('try_load_grids failed; continuing pipeline without cached grids')

        all_quotes = self._bootstrap_quotes()
        self._prefetch_shared_market_data(all_quotes)
        prio = getattr(self, 'priority_product', None)
        products = self.sorted_products(
            list(set(all_quotes['product'].dropna().unique().tolist()) | set(self._known_products())),
            priority=prio,
        )
        self.set_session_product_universe(products, priority=prio)
        logging.info(f'AGG PIPELINE: products={products}, mark_time={mark_time}, total_quotes={len(all_quotes)}')
        al.log(f'Parallel agg pipeline: {products}, mark_time={mark_time}', 'compute')

        stub_products = [pr for pr in products if isinstance(self.product_aggs.get(pr), _StubAgg)]
        if stub_products:
            logging.info(f'AGG PIPELINE: preserving {len(stub_products)} snapshot _StubAgg entries until replacement: {stub_products}')

        _MARK_COLS = ('payer_premium', 'receiver_premium', 'delta', 'gamma', 'vega', 'vega_ann',
                      'par_spread_spot_calc', 'price_spot_calc', 'risky_pv01_spot_calc')
        published_curr = self.curr_mark.copy() if isinstance(self.curr_mark, pd.DataFrame) and not self.curr_mark.empty else pd.DataFrame()
        published_sender = self.sender_mark.copy() if isinstance(self.sender_mark, pd.DataFrame) and not self.sender_mark.empty else pd.DataFrame()
        published_grids = dict(self.pricing_grids)
        published_aggs = dict(self.product_aggs)
        cached_grids = dict(self.pricing_grids)
        n_products = len(products)
        succeeded, failed, skipped = [], [], []

        with ThreadPoolExecutor(max_workers=n_products) as pool:
            futures = {
                pool.submit(
                    self._run_product_agg, pr,
                    all_quotes[all_quotes['product'] == pr].copy(), mark_time, mark_date,
                    cached_grid=cached_grids.get(pr),
                ): pr
                for pr in products
            }
            for future in as_completed(futures):
                product = futures[future]
                tag = f'[{product}]'
                try:
                    r = future.result()
                except Exception as e:
                    al.log(f'FAILED: {product} -- {str(e)[:200]}', 'error')
                    logging.exception(f'{tag} agg pipeline failed')
                    failed.append(product)
                    continue
                if not r:
                    skipped.append(product)
                    continue
                succeeded.append(product)
                next_curr = _prepare_mark_frame(r.get('curr_mark'), 'curr_mark', product=product)
                next_sender = _prepare_mark_frame(r.get('sender_mark'), 'sender_mark', product=product)
                published_curr = _merge_product_frame(published_curr, product, next_curr, keep_existing_on_empty=True)
                published_sender = _merge_product_frame(published_sender, product, next_sender, keep_existing_on_empty=False)

                next_grid = r.get('grid')
                if next_grid is not None and getattr(next_grid, 'grids', None):
                    published_grids[product] = next_grid
                next_agg = r.get('agg')
                if next_agg is None:
                    published_aggs.pop(product, None)
                else:
                    published_aggs[product] = next_agg

                live_curr = published_curr.copy() if not published_curr.empty else pd.DataFrame()
                for _gc in _MARK_COLS:
                    if not live_curr.empty and _gc not in live_curr.columns:
                        live_curr[_gc] = np.nan
                if not live_curr.empty:
                    _sanitize_mark_dtypes(live_curr)

                self.pricing_grids = dict(published_grids)
                self.grid_engine = GridPricingEngine(self.pricing_grids) if self.pricing_grids else GridPricingEngine({})
                self.product_aggs = dict(published_aggs)
                self.curr_mark = live_curr
                self.sender_mark = published_sender.copy() if not published_sender.empty else pd.DataFrame()

                product_curr = self.curr_mark[self.curr_mark['product'] == product] if (
                    not self.curr_mark.empty and 'product' in self.curr_mark.columns
                ) else pd.DataFrame()
                if product_curr.empty:
                    logging.warning(f'{tag} publish retained previous state only; curr_mark still empty for product')
                    continue

                p.mark_product_ready(product)
                al.log(f'{tag} ready ({len(p.products_ready)}/{n_products})', 'data')
                if on_product_ready:
                    on_product_ready(product)

        self._force_grid_rebuild = False
        self._missing_grid_rebuild_requested = False
        self._missing_grid_cycles.clear()
        self._missing_grid_rebuild_products.clear()
        cm_prods = sorted(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else []
        logging.info(f'LOAD COMPLETE: curr_mark={len(self.curr_mark)}, grids={len(self.pricing_grids)}, '
                     f'aggs={len(self.product_aggs)} | '
                     f'succeeded={succeeded} | failed={failed} | skipped={skipped} | '
                     f'curr_mark_products={cm_prods}')
        if skipped or failed:
            logging.warning(f'AGG PIPELINE ISSUES: {len(failed)} failed {failed}, {len(skipped)} skipped {skipped} '
                            f'(check activity log for per-product reasons)')
        al.log(f'All products done: curr_mark={len(self.curr_mark):,}, '
               f'sender_mark={len(self.sender_mark):,}, grids={len(self.pricing_grids)}', 'data')

        p.advance('positions')
        al.log('Loading & marking positions (parallel by product)...', 'compute')
        pos_err = self._safe_load_positions()
        if pos_err:
            al.log(f'Position marking failed: {pos_err}', 'error')
            logging.error(f'Position marking failed: {pos_err}')
        else:
            al.log(f'Positions: {len(self.cdx_opt_pos_df):,} position records', 'data')

        if self.pricing_grids:
            try:
                self.grid_pos_df = self._grid_reprice_all()
                al.log(f'Grid initial re-price: {len(self.grid_pos_df):,} positions', 'data')
            except Exception:
                logging.exception('Grid reprice failed')
                al.log('Grid reprice failed -- see logs', 'error')
        p.mark_ready('positions')

        self._bump_plot_versions(include_snapshot=True)
        self.queue_prewarm_plot_cache()
        self.last_heavy_refresh = datetime.now(NY_TZ)
        logging.info(f'LOAD COMPLETE: curr_mark={len(self.curr_mark)}, cdx_opt_trades={len(self.cdx_opt_trades)}, '
                 f'grids={len(self.pricing_grids)}, aggs={len(self.product_aggs)}, '
                 f'products={list(self.product_aggs.keys())}')
        p.snap_ready = False
        p.advance('done')

    # ── Grid Cache Management ────────────────────────────────

    def _load_grids_from_dir(self, cache_dir, match_date=None):
        if not os.path.isdir(cache_dir):
            return {}
        loaded = {}
        for fname in os.listdir(cache_dir):
            if not (fname.startswith('grid_') and fname.endswith('.parquet') and '_meta' not in fname):
                continue
            path = os.path.join(cache_dir, fname)
            try:
                grid = PricingGrid.from_parquet(path)
            except Exception:
                logging.exception(f'Failed to load grid from {path}, skipping')
                continue
            if not grid or (match_date and grid.valuedt != dtutil.todate(match_date)):
                continue
            if any(grid.product.startswith(t) for t in self.tickers):
                loaded[grid.product] = grid
        return loaded

    def try_load_grids(self):
        al = self.activity_log
        purged = purge_old_grid_cache()
        if purged:
            al.log(f'Purged {purged} old grid cache folder(s)', 'data')

        loaded = self._load_grids_from_dir(get_grid_cache_dir(self.todt))
        if loaded:
            self.pricing_grids = loaded
            # Detect stale grids that were seeded/copied from a previous day
            # into today's folder — their valuedt will be older than self.todt.
            stale = {p for p, g in loaded.items() if g.valuedt != self.todt}
            if stale:
                self._grids_carried_forward = True
                self._stale_grid_products = stale
                logging.info(f'try_load_grids: {len(loaded)} grids from today cache, '
                             f'{len(stale)} stale (valuedt != {self.todt}): {sorted(stale)}')
            else:
                al.log(f'Loaded {len(loaded)} grids from today cache', 'data')
            return True

        prev_dt = dtutil.dateadd(self.todt, '-1b')
        prev_loaded = self._load_grids_from_dir(get_grid_cache_dir(prev_dt))
        if prev_loaded:
            self.pricing_grids = prev_loaded
            self._grids_carried_forward = True
            self._stale_grid_products = set(prev_loaded.keys())
            for prod in prev_loaded:
                err_path = self._grid_error_path(prod)
                if os.path.isfile(err_path):
                    os.remove(err_path)
            al.log(f'Carry-forward: loaded {len(prev_loaded)} grids from {prev_dt} (will rebuild when quotes arrive)', 'data')
            logging.info(f'try_load_grids: carry-forward {sorted(prev_loaded.keys())} from {prev_dt}')
            return True

        al.log('No today grids -- will rebuild from DB data', 'data')
        return False

    def _grid_error_path(self, product):
        return os.path.join(get_grid_cache_dir(self.todt), f'grid_{product}_error.txt')

    def _has_grid_error(self, product):
        return os.path.isfile(self._grid_error_path(product))

    def _grid_error_age_seconds(self, product):
        path = self._grid_error_path(product)
        if not os.path.isfile(path):
            return None
        return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds()

    def _save_grid_error(self, product, error):
        atomic_file.write_text(
            f'{datetime.now()}: {error}\n',
            self._grid_error_path(product),
        )

    def _clear_grid_cache(self):
        cache_dir = get_grid_cache_dir(self.todt)
        if not os.path.isdir(cache_dir):
            return
        for f in os.listdir(cache_dir):
            if f.startswith('grid_') and (f.endswith('.parquet') or f.endswith('_error.txt')):
                os.remove(os.path.join(cache_dir, f))

    def _retry_failed_grid_builds(self):
        if self.curr_mark.empty:
            return
        gridded = {product for product, grid in self.pricing_grids.items() if _grid_is_usable(grid)}
        all_cm_products = set(self.curr_mark['product'].unique())
        retryable = []
        for product in sorted(all_cm_products - gridded):
            if getattr(self, '_grid_repair_backoff', {}).get(product, {}).get('retry_at', 0) > time.time():
                continue
            age = self._grid_error_age_seconds(product)
            if age is not None and age < _GRID_RETRY_INTERVAL_S:
                continue
            pm = self.curr_mark[self.curr_mark['product'] == product]
            if pm.empty:
                continue
            has_spread = ('spot_spread' in pm.columns
                          and pm['spot_spread'].dropna().pipe(lambda s: not s.empty and (s > 0).any()))
            has_price = ('spot_price' in pm.columns
                         and pm['spot_price'].dropna().pipe(lambda s: not s.empty and (s > 0).any()))
            if not has_spread and not has_price:
                self._save_grid_error(product, 'retry skipped: no spot_spread or spot_price')
                continue
            retryable.append(product)

        if not retryable:
            return

        logging.info(f'Grid retry: {len(retryable)} products eligible: {retryable}')
        for product in retryable:
            tag = f'[{product}]'
            try:
                pm = self.curr_mark[self.curr_mark['product'] == product].copy()
                agg = self.product_aggs.get(product)
                ss = pm['spot_spread'].dropna()
                sp = pm['spot_price'].dropna() if 'spot_price' in pm.columns else pd.Series(dtype=float)
                ss_disp = f'{ss.iloc[0]:.2f}' if not ss.empty else 'NaN'
                sp_disp = f'{sp.iloc[0]:.4f}' if not sp.empty else 'NaN'
                logging.info(f'{tag} Retrying grid build (marks={len(pm)}, '
                             f'spot_spread={ss_disp}, spot_price={sp_disp}, '
                             f'agg={"yes" if agg else "no"})')
                new_grid = self._build_grid(product, pm, agg)
                if _grid_is_usable(new_grid):
                    self.pricing_grids[product] = new_grid
                    err_path = self._grid_error_path(product)
                    if os.path.isfile(err_path):
                        os.remove(err_path)
                    logging.info(f'{tag} Grid retry SUCCEEDED: {len(new_grid.grids)} options')
                    self.activity_log.log(f'{tag} Grid retry succeeded ({len(new_grid.grids)} options)', 'data')
                else:
                    logging.warning(f'{tag} Grid retry FAILED')
            except Exception:
                logging.exception(f'{tag} Grid retry ERROR')

    def _fill_missing_px_spreads(self, cm):
        if cm.empty or 'spot_spread' not in cm.columns or 'spot_price' not in cm.columns:
            return
        for product in cm['product'].unique():
            if not is_price_based(product):
                continue
            if _grid_is_usable(self.pricing_grids.get(product)):
                continue
            mask = cm['product'] == product
            if cm.loc[mask, 'spot_spread'].notna().any():
                continue
            px_vals = cm.loc[mask, 'spot_price'].dropna()
            if px_vals.empty:
                continue
            agg = self.product_aggs.get(product)
            solved = self._solve_spread_from_price(product, px_vals.iloc[0], agg)
            if pd.notnull(solved) and solved > 0:
                cm.loc[mask, 'spot_spread'] = solved * 1e4
                logging.info(f'[{product}] _fill_missing_px_spreads: '
                             f'px={px_vals.iloc[0]:.4f} -> {solved*1e4:.2f}bps')

    def _grid_reprice_all(self):
        if not self.pricing_grids or self.cdx_opt_pos_df.empty:
            return pd.DataFrame()
        started = time.perf_counter()
        parts = []
        grouped = dict(tuple(self.cdx_opt_pos_df.groupby('product', sort=False))) if 'product' in self.cdx_opt_pos_df else None
        for product, grid in self.pricing_grids.items():
            positions = grouped.get(product) if grouped is not None else self.cdx_opt_pos_df
            if positions is None:
                continue
            repriced = grid.reprice_positions(positions)
            if not repriced.empty:
                parts.append(repriced)
        logging.info('Position grid interpolation: rows=%s products=%s elapsed_ms=%.1f',
                     len(self.cdx_opt_pos_df), len(parts), (time.perf_counter() - started) * 1000)
        if parts:
            return pd.concat(parts, ignore_index=True)
        return pd.DataFrame()

    def _fetch_parallel_soft(self, tasks):
        names = [t[0] for t in tasks]
        funcs = [t[1] for t in tasks]
        self.activity_log.log(f'Parallel fetch ({len(tasks)} threads): {", ".join(names)}', 'query')
        executor = JobExecutor('thread', max_workers=len(tasks))
        results = executor.submitjob(funcs)
        out = {}
        for name, res in zip(names, results):
            if res['error']:
                logging.error(f'_fetch_parallel_soft: {name} failed: {str(res["error"])[:300]}')
                self.activity_log.log(f'WARN: {name} failed (continuing)', 'error')
                out[name] = pd.DataFrame()
            else:
                out[name] = res['result']
        return out

    def _load_live_data(self, prefetched=None):
        tasks = [
            ('live_trades', lambda: sdr_db.get_cdx_opt_trades(self.todt, self.todt, use_live=True)),
            ('quotes', lambda: cds_index_vol.get_quotes(self.todt, self.todt, use_live=True)),
        ]
        if self.index_info is not None and self.rate_curves:
            tasks.append(('cdx_trades', lambda: sdr_db.get_cdx_trades(
                dtutil.dateadd(self.todt, '-1b'), self.todt, use_live=True
            )))
        r = prefetched or self._fetch_parallel_soft(tasks)
        self.cdx_opt_live_trades = _to_ny(r['live_trades'])
        self.quotes_df = _to_ny(r['quotes'])
        hist = self.cdx_opt_hist_trades
        live = self.cdx_opt_live_trades
        if not hist.empty and not live.empty:
            shared = list(hist.columns.intersection(live.columns))
            self.cdx_opt_trades = pd.DataFrame(
                {c: pd.concat([hist[c], live[c]], ignore_index=True) for c in shared}
            )
        else:
            self.cdx_opt_trades = pd.concat([hist, live], ignore_index=True)
        for ts_col in ['event_timestamp', 'execution_timestamp']:
            if ts_col in self.cdx_opt_trades.columns:
                self.cdx_opt_trades[ts_col] = pd.to_datetime(self.cdx_opt_trades[ts_col], errors='coerce')
        self.cdx_opt_trades = sdr_db.drop_dupe_trades(self.cdx_opt_trades)
        cdx_trades = _to_ny(r.get('cdx_trades', pd.DataFrame()))
        if not cdx_trades.empty and self.index_info is not None and self.rate_curves:
            self._update_cdx_marker(cdx_trades)

    def _copy_live_data_from(self, peer):
        for attr in ('cdx_opt_live_trades', 'quotes_df', 'cdx_opt_trades'):
            df = getattr(peer, attr, None)
            if df is not None:
                setattr(self, attr, df)
        peer_marker = getattr(peer, 'cdx_marker', None)
        if peer_marker is not None:
            self.cdx_marker = peer_marker

    def _update_cdx_marker(self, cdx_trades):
        if self.cdx_marker is None or self.cdx_marker.cdx_df.empty:
            self.cdx_marker = SDRCDXMark(cdx_trades, self.index_info, self.rate_curves)
            logging.info(f'CDX marker created: {len(self.cdx_marker.cdx_df)} rows')
            return

        existing_df = self.cdx_marker.cdx_df
        last_ts = existing_df.index.get_level_values('execution_timestamp').max()

        new_trades = cdx_trades[cdx_trades['execution_timestamp'] > last_ts]
        if new_trades.empty:
            logging.info(f'CDX marker: no new trades since {last_ts}')
            return

        temp_marker = SDRCDXMark(new_trades, self.index_info, self.rate_curves)
        if temp_marker.cdx_df.empty:
            logging.info(f'CDX marker: {len(new_trades)} new trades yielded 0 processed rows')
            return

        combined = pd.concat([existing_df, temp_marker.cdx_df]).sort_index()
        combined = combined[~combined.index.duplicated(keep='last')]
        self.cdx_marker.cdx_df = combined
        logging.info(f'CDX marker updated: +{len(temp_marker.cdx_df)} rows, total={len(combined)}')

    # ── Position Marking ──────────────────────────────────────

    def _enrich_pos_for_grid(self, product, pos_df, vol_mark_df, cdx_marker):
        al = self.activity_log
        tag = f'[{product}]'
        al.log(f'{tag} Grid-enriching {len(pos_df):,} positions (no analytical pricing)...', 'compute')
        product_vol = vol_mark_df[vol_mark_df['product'] == product]
        marker = SDRCDXOptPosMark(
            pos_df, product_vol, self.rate_curves, self.index_info,
            cdx_marker=cdx_marker
        )
        cm = self.curr_mark[self.curr_mark['product'] == product]
        spot = cm['spot_spread'].dropna().iloc[0] if not cm.empty and 'spot_spread' in cm.columns and cm['spot_spread'].notna().any() else np.nan
        marker.cdx_opt_pos_df['spot_spread'] = spot
        if 'spot_price' in cm.columns and cm['spot_price'].notna().any():
            marker.cdx_opt_pos_df['spot_price'] = cm['spot_price'].dropna().iloc[0]

        grid = self.pricing_grids.get(product)
        if grid and grid.spot_measures:
            spot_dec = spot / 1e4 if pd.notnull(spot) and spot > 1 else spot
            if pd.notnull(spot_dec):
                fwd_price = grid.interp_spot_measure(spot_dec, 'clean_price')
                fwd_spread = spot_dec
                rpv01 = grid.interp_spot_measure(spot_dec, 'risky_pv01')
                marker.cdx_opt_pos_df['forward_price'] = fwd_price
                marker.cdx_opt_pos_df['forward_spread'] = fwd_spread
                marker.cdx_opt_pos_df['risky_pv01'] = rpv01

        spot_dec = spot / 1e4 if pd.notnull(spot) and spot > 1 else spot
        for idx, row in marker.cdx_opt_pos_df.iterrows():
            expiry = dtutil.todate(row.get('expiration_date'))
            strike = row.get('strike_price')
            asof = row.get('asof_date')
            if pd.isnull(strike) or pd.isnull(expiry) or pd.isnull(asof):
                continue
            vol_out = marker.get_strike_vols_by_expiry(f'{asof} 23:59', product, strike, expiry)
            if len(vol_out) > 0 and 'implied_vol' in vol_out.columns:
                marker.cdx_opt_pos_df.at[idx, 'implied_vol'] = vol_out.iloc[0]['implied_vol']

            vol = marker.cdx_opt_pos_df.at[idx, 'implied_vol'] if 'implied_vol' in marker.cdx_opt_pos_df.columns else np.nan
            if grid and pd.notnull(vol) and pd.notnull(spot_dec):
                measures = grid.interpolate(expiry, strike, spot_dec, vol)
                for col in ('delta', 'gamma', 'vega', 'vega_ann', 'payer_premium', 'receiver_premium'):
                    if col in measures:
                        marker.cdx_opt_pos_df.at[idx, col] = measures[col]

        n_vol = marker.cdx_opt_pos_df['implied_vol'].notna().sum() if 'implied_vol' in marker.cdx_opt_pos_df.columns else 0
        al.log(f'{tag} Grid enrich done: {len(marker.cdx_opt_pos_df):,} pos, '
               f'spot={spot:.1f}, {n_vol} vol-matched', 'data')
        return marker.cdx_opt_pos_df

    def _mark_product_positions(self, product, pos_df, vol_mark_df, cdx_marker):
        al = self.activity_log
        tag = f'[{product}]'
        al.log(f'{tag} Analytical marking {len(pos_df):,} positions...', 'compute')
        product_vol = vol_mark_df[vol_mark_df['product'] == product]
        marker = SDRCDXOptPosMark(
            pos_df, product_vol, self.rate_curves, self.index_info,
            cdx_marker=cdx_marker
        )
        marker.process_pos(calc_scenario=self.pos_scenario_run)
        al.log(f'{tag} Positions marked: {len(marker.cdx_opt_pos_df):,} rows', 'data')
        return marker.cdx_opt_pos_df

    def _safe_load_positions(self):
        try:
            self._load_and_mark_positions()
            return None
        except Exception as e:
            logging.exception('Position marking failed')
            return str(e)[:200]

    def _load_and_mark_positions(self):
        al = self.activity_log
        tasks = [
            ('pos_hist', lambda: sdr_db.get_cdx_opt_pos(
                dtutil.dateadd(self.todt, '-1b'), dtutil.dateadd(self.todt, '-1b'), ticker=self.tickers
            )),
        ]
        need_static = self.mark_pos and (self.index_info is None or self.rate_curves is None)
        if self.mark_pos:
            tasks.append(('cdx_trades', lambda: sdr_db.get_cdx_trades(
                dtutil.dateadd(self.todt, '-1b'), self.todt, use_live=True
            )))
            if need_static:
                tasks.append(('index_info', lambda: SDRMarkHelpers.get_index_info()))
                tasks.append(('rate_curves', lambda: SDRMarkHelpers.get_rate_curves(
                    dtutil.dateadd(self.todt, '-3b'), self.todt
                )))

        r = self._fetch_parallel(tasks)

        cdx_opt_pos_hist_df = _to_ny(r['pos_hist'])
        al.log(f'Pos hist: {len(cdx_opt_pos_hist_df):,} rows', 'data')
        cdx_opt_pos_live_df = sdr_db.agg_trades_to_pos(self.cdx_opt_live_trades)
        cdx_opt_pos_live_df = cdx_opt_pos_live_df[
            pd.to_datetime(cdx_opt_pos_live_df['trade_date']).dt.date >= self.todt
        ]
        cdx_opt_pos_live_df = cdx_opt_pos_live_df[cdx_opt_pos_live_df['ticker'].isin(self.tickers)]
        cdx_opt_pos_hist_df['trade_date'] = cdx_opt_pos_hist_df['asof_date']

        self.cdx_opt_pos_df = cdx_opt_pos_hist_df

        if self.mark_pos and not cdx_opt_pos_live_df.empty:
            if need_static:
                self.index_info = r['index_info']
                self.rate_curves = r['rate_curves']

            cdx_trades = _to_ny(r['cdx_trades'])
            rc_summary = {ccy: len(v) for ccy, v in self.rate_curves.items()} if self.rate_curves else 'None'
            ii_len = len(self.index_info) if self.index_info is not None else 'None'
            logging.info(f'SDRCDXMark inputs: cdx_trades={len(cdx_trades)} rows, '
                     f'cols={list(cdx_trades.columns)}, '
                     f'index_info={ii_len} rows, rate_curves={rc_summary}')
            if not cdx_trades.empty and self.index_info is not None:
                matched = cdx_trades['red9'].isin(self.index_info['red9']).sum()
                logging.info(f'SDRCDXMark red9 match: {matched}/{len(cdx_trades)} trades matched index_info')

            cdx_marker = SDRCDXMark(cdx_trades, self.index_info, self.rate_curves)
            self.cdx_marker = cdx_marker

            products = cdx_opt_pos_live_df['ticker'].unique().tolist()
            grid_prods = [pr for pr in products if pr in self.pricing_grids and self.pricing_grids[pr].grids]
            calc_prods = [pr for pr in products if pr not in grid_prods]

            all_tasks = []
            if grid_prods:
                al.log(f'Grid-enriching positions (no pricer): {grid_prods}', 'compute')
                all_tasks += [
                    (pr, lambda pr=pr: self._enrich_pos_for_grid(
                        pr,
                        cdx_opt_pos_live_df[cdx_opt_pos_live_df['ticker'] == pr].copy(),
                        self.curr_mark,
                        cdx_marker,
                    ))
                    for pr in grid_prods
                ]
            if calc_prods:
                al.log(f'Analytical pos marking: {calc_prods}', 'compute')
                all_tasks += [
                    (pr, lambda pr=pr: self._mark_product_positions(
                        pr,
                        cdx_opt_pos_live_df[cdx_opt_pos_live_df['ticker'] == pr].copy(),
                        self.curr_mark,
                        cdx_marker,
                    ))
                    for pr in calc_prods
                ]
            marked = self._fetch_parallel(all_tasks)
            self.cdx_opt_pos_df = pd.concat(
                [cdx_opt_pos_hist_df] + list(marked.values()), ignore_index=True
            )

        date_cols = ['trade_date', 'expiration_date', 'asof_date', 'maturity_5y']
        for c in date_cols:
            if c in self.cdx_opt_pos_df.columns:
                self.cdx_opt_pos_df[c] = pd.to_datetime(self.cdx_opt_pos_df[c], format='mixed', dayfirst=False)

    # ── UI Data Queries ───────────────────────────────────────

    def get_prev_mark(self, lookback='1b'):
        if self.curr_mark.empty or self.vol_mark_df.empty:
            return pd.DataFrame()
        adt = self.curr_mark['asof_date_time']
        if not pd.api.types.is_datetime64_any_dtype(adt):
            adt = pd.to_datetime(adt, format='mixed', dayfirst=False, errors='coerce')
        ref_date = adt.max()
        prev_dt = dtutil.dateadd(ref_date, f'-{lookback}')
        vm_dates = pd.to_datetime(self.vol_mark_df['asof_date'], format='mixed', dayfirst=False).dt.date
        eligible = vm_dates <= prev_dt
        latest_dates = vm_dates[eligible].groupby(self.vol_mark_df.loc[eligible, 'product']).transform('max')
        prev_mark = self.vol_mark_df.loc[latest_dates.index[vm_dates[eligible] == latest_dates]].copy()
        used_dt = sorted(pd.to_datetime(prev_mark['asof_date']).dt.date.unique())
        logging.info(f'get_prev_mark(lookback={lookback}): ref={ref_date}, target={prev_dt}, '
                     f'used={used_dt}, rows={len(prev_mark)}')
        if prev_mark.empty:
            return prev_mark
        prev_mark['max_dttime'] = prev_mark.groupby('product')['asof_date_time'].transform('max')
        prev_mark = prev_mark[prev_mark['max_dttime'] == prev_mark['asof_date_time']]
        return prev_mark.drop(columns=['max_dttime'])

    def convert_spot(self, product, ref_px=None, ref_bps=None):
        grid = self.pricing_grids.get(product)
        if grid is None or not grid.spot_measures:
            return ref_px, ref_bps
        if ref_px is not None and ref_bps is not None:
            if _is_px_index(product):
                ref_bps = None
            else:
                ref_px = None
        if ref_px is not None and ref_bps is None:
            spread_dec = grid.price_to_spread(ref_px)
            return ref_px, round(spread_dec * 1e4, 2) if pd.notna(spread_dec) else None
        if ref_bps is not None and ref_px is None:
            spread_dec = ref_bps / 1e4 if ref_bps > 1 else ref_bps
            price = grid.spread_to_price(spread_dec)
            return round(price, 2) if pd.notna(price) else None, ref_bps
        return ref_px, ref_bps

    def get_live_spot(self, product):
        sub = self.curr_mark[self.curr_mark['product'] == product].copy() if not self.curr_mark.empty else pd.DataFrame()
        if sub.empty:
            return None, None
        if 'asof_date_time' in sub.columns:
            sub = sub.assign(_sort_ts=pd.to_datetime(sub['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
        px = pd.to_numeric(sub['spot_price'], errors='coerce').dropna().iloc[-1] if 'spot_price' in sub.columns and sub['spot_price'].notna().any() else None
        bps = pd.to_numeric(sub['spot_spread'], errors='coerce').dropna().iloc[-1] if 'spot_spread' in sub.columns and sub['spot_spread'].notna().any() else None
        return (round(float(px), 2) if px is not None else None,
                round(float(bps), 2) if bps is not None else None)

    def get_live_fwd_bps(self, product, expiry=None):
        if self.curr_mark.empty:
            return None
        pm = self.curr_mark[self.curr_mark['product'] == product].copy()
        if pm.empty:
            return None
        if expiry is not None and 'option_end_date' in pm.columns:
            exp_match = pm[pd.to_datetime(pm['option_end_date'], errors='coerce').dt.normalize() == pd.Timestamp(expiry).normalize()]
            if not exp_match.empty:
                pm = exp_match
        if 'asof_date_time' in pm.columns:
            pm = pm.assign(_sort_ts=pd.to_datetime(pm['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
        for col, scale in [('forward_spread', 1e4), ('par_spread_fwd_calc', 1), ('spot_spread', 1)]:
            if col in pm.columns:
                vals = pd.to_numeric(pm[col], errors='coerce').dropna()
                vals = vals[vals > 0]
                if not vals.empty:
                    return round(float(vals.iloc[-1]) * scale, 2)
        return None

    def get_update_text(self):
        def _max_ts(df, col):
            if df is not None and not df.empty and col in df.columns:
                mx = pd.to_datetime(df[col], errors='coerce').max()
                if pd.notna(mx):
                    return mx.strftime('%H:%M:%S')
            return '--:--'

        quotes_ts = _max_ts(self.quotes_df, 'asof_date_time')
        vols_ts = _max_ts(self.curr_mark, 'asof_date_time')
        trades_ts = _max_ts(self.cdx_opt_trades, 'execution_timestamp')
        refresh_ts = (
            getattr(self, 'last_spot_refresh', None)
            or getattr(self, 'last_light_refresh', None)
            or getattr(self, 'last_heavy_refresh', None)
        )
        refresh_ts = refresh_ts.strftime('%H:%M:%S') if refresh_ts else '--:--'
        return f'Quotes: {quotes_ts} | Vols: {vols_ts} | Trades: {trades_ts} | Refresh: {refresh_ts}'

    def get_grid_badge_text(self):
        if not self.pricing_grids:
            return ''
        first = next(iter(self.pricing_grids.values()))
        if first.built_at:
            return f'Grid built {first.built_at.strftime("%H:%M")} ({len(self.pricing_grids)} products)'
        return f'{len(self.pricing_grids)} grids loaded'

    def get_spot_snapshot(self, products=None):
        products = set(products or self._known_products())
        ts = (
            getattr(self, 'last_spot_refresh', None)
            or getattr(self, 'last_light_refresh', None)
            or getattr(self, 'last_heavy_refresh', None)
            or datetime.now(NY_TZ)
        ).strftime('%H:%M:%S')
        spots = {}
        if not self.curr_mark.empty:
            cols = [c for c in ['product', 'spot_price', 'spot_spread'] if c in self.curr_mark.columns]
            if cols:
                sub = self.curr_mark[[c for c in ['product', 'spot_price', 'spot_spread', 'asof_date_time'] if c in self.curr_mark.columns]].copy()
                if 'asof_date_time' in sub.columns:
                    sub = sub.assign(_sort_ts=pd.to_datetime(sub['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
                sub = sub.drop_duplicates('product', keep='last')
                for _, row in sub.iterrows():
                    product = row.get('product')
                    if product not in products:
                        continue
                    px = row.get('spot_price', np.nan)
                    bps = row.get('spot_spread', np.nan)
                    spots[product] = {
                        'px': round(float(px), 2) if pd.notnull(px) else None,
                        'bps': round(float(bps), 2) if pd.notnull(bps) else None,
                    }
        items = [item for item in self.get_delta_one_strip() if item.get('product') in products] or self.get_delta_one_strip()
        return {'ts': ts, 'spots': spots, 'items': items}

    def get_light_refresh_payload(self):
        payload = self.get_spot_snapshot()
        payload['update_text'] = self.get_update_text()
        payload['grid_badge'] = self.get_grid_badge_text()
        payload['strip_items'] = payload.get('items', [])
        return payload

    def get_product_title(self, product):
        px, bps = self.get_live_spot(product)
        if px is None and bps is None:
            return f"{product} No vol marks in window"
        parts = []
        if px is not None:
            parts.append(f"Px: {px:.2f}")
        if bps is not None:
            parts.append(f"Spread: {bps:.2f}")
        return f"{product} | {' / '.join(parts)}" if parts else f"{product}"

    _SDR_STALE_MINUTES = 60  # If last SDR trade is older than this, fall back to quotes

    def _get_sdr_spot(self, product):
        if self.cdx_marker is None or self.index_info is None:
            return np.nan
        match = self.index_info[self.index_info['product'] == product]
        if match.empty:
            return np.nan
        red9 = match.iloc[0]['red9']
        if red9 not in self.cdx_marker.cdx_df.index.get_level_values('red9'):
            return np.nan
        cdx_ref = self.cdx_marker.cdx_df.xs(red9, level='red9').reset_index()
        exec_time = cdx_ref['execution_timestamp'].max()

        # Staleness check: if the last trade is too old, let the caller fall
        # back to dealer quotes which are typically more current.
        now_utc = pd.Timestamp.now(tz='UTC')
        if pd.notna(exec_time):
            ts = pd.Timestamp(exec_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize('UTC')
            age_min = (now_utc - ts).total_seconds() / 60
            if age_min > self._SDR_STALE_MINUTES:
                logging.info(f'_get_sdr_spot({product}): last trade {age_min:.0f}m ago '
                             f'(>{self._SDR_STALE_MINUTES}m) — stale, falling back to quotes')
                return np.nan

        ref = self.cdx_marker.calculate_ewma_ref(exec_time, red9, half_life=10)
        if ref.empty:
            return np.nan
        # SDR always computes both price (spot_calc) and spread (spread_spot_calc).
        # Return both for all product types so on_spot_update can set spot_price
        # without needing the grid.
        return {'price': ref.get('spot_calc', np.nan),
                'spread': ref.get('spread_spot_calc', np.nan)}

    def get_delta_one_strip(self):
        if self.curr_mark.empty:
            return []
        cols = [c for c in ['product', 'spot_spread', 'spot_price', 'asof_date_time'] if c in self.curr_mark.columns]
        sub = self.curr_mark[cols].copy()
        if 'asof_date_time' in sub.columns:
            sub = sub.assign(_sort_ts=pd.to_datetime(sub['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
        sub = sub.drop_duplicates('product', keep='last')
        prev = self.get_prev_mark()
        prev_map = {}
        if not prev.empty and 'spot_spread' in prev.columns:
            prev_sub = prev[[c for c in ['product', 'spot_spread', 'asof_date_time'] if c in prev.columns]].copy()
            if 'asof_date_time' in prev_sub.columns:
                prev_sub = prev_sub.assign(_sort_ts=pd.to_datetime(prev_sub['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
            prev_map = prev_sub.drop_duplicates('product', keep='last').set_index('product')['spot_spread'].to_dict()

        item_map = {}
        for _, row in sub.iterrows():
            p = row['product']
            spd = row.get('spot_spread', np.nan)
            px = row.get('spot_price', np.nan)
            prev_spd = prev_map.get(p, np.nan)
            chg = spd - prev_spd if pd.notnull(spd) and pd.notnull(prev_spd) else np.nan
            item_map[p] = {'product': p, 'spread': spd, 'price': px, 'chg': chg}
        prio = getattr(self, 'priority_product', None)
        otr_set = self._compute_otr_set(list(item_map.keys()))
        ordered = self.sorted_products(list(item_map.keys()), priority=prio)
        for p in ordered:
            item_map[p]['is_otr'] = p in otr_set
        return [item_map[p] for p in ordered]

    def _is_otr(self, product):
        if self.index_info is None or self.index_info.empty:
            return True
        match = self.index_info[self.index_info['product'] == product]
        if match.empty:
            return False
        row = match.iloc[0]
        if 'on_the_run' in match.columns and pd.notnull(row.get('on_the_run')):
            return int(row['on_the_run']) == 1
        if 'active_version' in match.columns and pd.notnull(row.get('active_version')):
            return int(row['active_version']) == 1
        return True

    def _get_grid_strikes(self, product):
        is_px = _is_px_index(product)
        cm = self.curr_mark[self.curr_mark['product'] == product]
        if cm.empty:
            return None
        spot_col = 'spot_price' if is_px else 'spot_spread'
        if spot_col not in cm.columns or cm[spot_col].isna().all():
            return None
        spot = cm[spot_col].dropna().iloc[0]
        base = SDRCDXOptPosMark.get_strike_range(is_px, spot)
        if is_px:
            step = 0.5
            top = base.max()
            above = np.arange(top + step, top + step * 9, step)
            return np.sort(np.unique(np.concatenate([base, above])))
        step = 2.5
        bottom = base.min()
        below = np.arange(bottom - step, bottom - step * 5, -step)
        return np.sort(np.unique(np.concatenate([base, below[below > 0]])))

    _INDEX_ORDER = {'NAHY': 0, 'NAIG': 1, 'Europe': 2, 'Xover': 3}

    def _product_sort_key(self, p, priority=None, otr_set=None):
        ticker, series, version = self._parse_product(p)
        idx_rank = 99
        for tag, rank in self._INDEX_ORDER.items():
            if tag in ticker:
                idx_rank = rank
                break
        is_priority = 0 if priority and p == priority else 1
        is_otr = 0 if otr_set is not None and p in otr_set else 1
        return (is_priority, is_otr, idx_rank, -(series * 10000 + version))

    def _compute_otr_set(self, products):
        best = {}
        for p in products:
            ticker, _, _ = self._parse_product(p)
            score = self._product_score(p)
            if ticker not in best or score > best[ticker][1]:
                best[ticker] = (p, score)
        return {v[0] for v in best.values()}

    def sorted_products(self, products, priority=None):
        otr_set = self._compute_otr_set(products)
        return sorted(products, key=lambda p: self._product_sort_key(p, priority, otr_set))

    def get_available_products(self, priority=None):
        cm = set(self.curr_mark['product'].unique().tolist()) if not self.curr_mark.empty else set()
        agg = set(self.product_aggs.keys()) if self.product_aggs else set()
        grids = set(self.pricing_grids.keys()) if self.pricing_grids else set()
        grid_ready = {product for product in grids if _grid_is_usable(self.pricing_grids.get(product))}
        ready = cm | (agg & grid_ready)
        withheld_agg = agg - ready
        withheld_grids = grids - ready
        if withheld_agg or withheld_grids:
            logging.warning(f'get_available_products: withholding incomplete products '
                            f'aggs_not_ready={sorted(withheld_agg)}, grids_not_ready={sorted(withheld_grids)}')
        return self.sorted_products(list(ready), priority=priority)

    @staticmethod
    def _normalize_option_expiry(value):
        ts = pd.to_datetime(value, errors='coerce')
        if pd.isna(ts):
            return None
        return ts.normalize()

    @staticmethod
    def _normalize_option_strike(value):
        try:
            strike = float(value)
        except (TypeError, ValueError):
            return None
        return int(strike) if strike.is_integer() else strike

    def _grid_option_universe(self, product):
        expiries = set()
        strikes = set()
        grid = self.pricing_grids.get(product)
        if grid is None or not getattr(grid, 'grids', None):
            return expiries, strikes
        keys = tuple(grid.grids)
        signature = (self.todt, keys)
        cache = getattr(self, '_grid_universe_cache', {})
        previous = cache.get(product)
        if previous and previous[0] == signature:
            return set(previous[1]), set(previous[2])
        for expiry in {key[0] for key in keys}:
            exp = self._normalize_option_expiry(expiry)
            if exp is not None and exp.date() >= self.todt:
                expiries.add(exp)
        for strike in {key[1] for key in keys}:
            norm_strike = self._normalize_option_strike(strike)
            if norm_strike is not None:
                strikes.add(norm_strike)
        cache[product] = (signature, frozenset(expiries), frozenset(strikes))
        self._grid_universe_cache = cache
        return expiries, strikes

    def get_available_expiries(self, product, min_contributors=2):
        cutoff = self.todt
        valid_expiries = self._expiries_with_min_contributors(product, min_contributors)
        expiries, _ = self._grid_option_universe(product)

        def _add(values, apply_contrib_filter):
            for value in values:
                exp = self._normalize_option_expiry(value)
                if exp is None or exp.date() < cutoff:
                    continue
                if apply_contrib_filter and valid_expiries is not None and exp.date() not in valid_expiries:
                    continue
                expiries.add(exp)

        if not self.curr_mark.empty:
            sub = self.curr_mark[self.curr_mark['product'] == product]
            if 'option_end_date' in sub.columns:
                _add(sub['option_end_date'].dropna().unique(), True)
        if not self.quotes_df.empty:
            sub = self.quotes_df[self.quotes_df['product'] == product]
            if 'expiry' in sub.columns:
                _add(sub['expiry'].dropna().unique(), True)
        return sorted(expiries)

    def _expiries_with_min_contributors(self, product, min_contributors=2):
        agg = self.product_aggs.get(product)
        if agg is None or agg.quotes.empty:
            return None
        q = agg.quotes
        if 'sender_bank' not in q.columns or 'expiry' not in q.columns:
            return None
        contrib = q.groupby('expiry')['sender_bank'].nunique()
        result = set(pd.to_datetime(contrib[contrib >= min_contributors].index).date)
        if not result:
            logging.warning(f'_expiries_with_min_contributors({product}): all expiries below {min_contributors} '
                            f'contributors, falling back to unfiltered')
            return None
        return result

    def get_available_strikes(self, product):
        _, strikes = self._grid_option_universe(product)
        if not self.curr_mark.empty:
            sub = self.curr_mark[self.curr_mark['product'] == product]
            for strike in sub['strike'].dropna().unique().tolist():
                norm_strike = self._normalize_option_strike(strike)
                if norm_strike is not None:
                    strikes.add(norm_strike)
        if not self.quotes_df.empty:
            sub = self.quotes_df[self.quotes_df['product'] == product]
            if 'strike' in sub.columns:
                for strike in sub['strike'].dropna().unique().tolist():
                    norm_strike = self._normalize_option_strike(strike)
                    if norm_strike is not None:
                        strikes.add(norm_strike)
        return sorted(strikes)

    def build_early_trades(self):
        if self.cdx_opt_trades.empty:
            return {}
        cdx_opt_trades = self.cdx_opt_trades
        todt = self.todt
        tickers = self.tickers
        trade_cols = [
            'execution_timestamp', 'product',
            "concat(strftime(cast(expiration_date as date), '%m-%y'), ' ', strike_price, left(option_type,1)) as option",
            'buy_prob_greedy',
            "case when buy_prob_greedy=1 then 'long' else 'short' end as dlr_dir",
            'estimated_notional', 'option_premium_bps',
            'implied_normal_vol*sqrt(252) as vol_ann',
            "concat(round(spread_spot_calc*1e4,2),'/',round(spot_calc,2)) as spot",
            'delta', 'expiration_date', 'strike_price',
        ]
        sel = ','.join(trade_cols)
        intraday = _duckq(f"select {sel} from cdx_opt_trades where ticker in {tosqllist(tickers)} and cast(trade_date as date)='{todt}'", cdx_opt_trades=cdx_opt_trades)
        prior = _duckq(f"select {sel} from cdx_opt_trades where ticker in {tosqllist(tickers)} and cast(trade_date as date)='{dtutil.dateadd(todt, '-1b')}'", cdx_opt_trades=cdx_opt_trades)
        wtd = _duckq(f"select {sel} from cdx_opt_trades where ticker in {tosqllist(tickers)} and cast(trade_date as date)>='{dtutil.dateadd(todt, '-10b')}'", cdx_opt_trades=cdx_opt_trades)
        return {
            'intraday_trades_df': intraday,
            'priorday_trades_df': prior,
            'all_trades_wtd_df': wtd,
        }

    def _get_trade_window_slices(self):
        cache_key = (self._shared_trade_version, tuple(self.tickers), str(self.todt))
        with self._shared_trade_cache_lock:
            cached = self._shared_trade_cache.get(cache_key)
            if cached is not None:
                return cached
            evt = self._shared_trade_inflight.get(cache_key)
            if evt is not None:
                owner = False
            else:
                evt = threading.Event()
                self._shared_trade_inflight[cache_key] = evt
                owner = True

        if not owner:
            evt.wait(timeout=180)
            with self._shared_trade_cache_lock:
                return self._shared_trade_cache.get(cache_key)

        try:
            trade_details = [
                'execution_timestamp', 'product',
                "concat(strftime(cast(expiration_date as date), '%m-%y'), ' ', strike_price, left(option_type,1)) as option",
                "concat(strftime(cast(expiration_date_unadj as date), '%m-%y'), ' ', strike_price_unadj, left(option_type_unadj,1)) as option_unadj",
                'buy_prob_greedy', 'estimated_notional', 'option_premium_bps',
                "CASE WHEN coalesce(round(option_premium_bps*mid_premium, 4), 0) = 0 THEN 0 WHEN buy_prob_greedy = 1 THEN delta * forward_price / 100 * estimated_notional ELSE -1 * delta * forward_price / 100 * estimated_notional END AS est_MVE",
                "CASE WHEN coalesce(round(option_premium_bps*mid_premium, 4), 0) = 0 THEN 0 WHEN buy_prob_greedy = 1 THEN estimated_notional * (mid_premium - option_premium_bps) / 1e4 ELSE -1 * estimated_notional * (mid_premium - option_premium_bps) / 1e4 END AS intraday_tcost",
                'option_premium_bps - mid_premium as option_premium_chg',
                'expiration_date',
                'implied_normal_vol*sqrt(252) as vol_ann',
                'implied_vol',
                'forward_spread * mid_vol * 10000 as mid_vol_ann',
                "concat(round(spread_spot_calc*1e4,2),'/',round(spot_calc,2)) as spot",
                "concat(round(forward_spread*1e4,2),'/',round(forward_price,2)) as fwd",
                'mid_vol', 'strike_price', 'strike_price_unadj',
                'CASE WHEN strike_price <> strike_price_unadj then 1 else 0 END as strike_adjusts',
                'CASE WHEN option_type <> option_type_unadj then 1 else 0 END as option_adjusts',
                'CASE WHEN expiration_date <> expiration_date_unadj then 1 else 0 END as expiration_adjusts',
                'option_type_unadj', 'option_type', 'delta', 'index_factor',
                "CASE WHEN coalesce(round(option_premium_bps, 4), 0) = 0 THEN 0 WHEN buy_prob_greedy = 1 THEN vega_ann / 1e8 * estimated_notional ELSE -1 * vega_ann / 1e8 * estimated_notional END AS net_vega_ann",
                'is_adj'
            ]
            cdx_opt_trades = self.cdx_opt_trades
            todt = self.todt
            tickers = self.tickers

            intraday_trades_df = _duckq(f"""
                select {",".join(trade_details)}
                from cdx_opt_trades
                where ticker in {tosqllist(tickers)}
                and cast(trade_date as date) = '{todt}'
            """, cdx_opt_trades=cdx_opt_trades)

            prior_dt = dtutil.dateadd(todt, '-1b')
            if not cdx_opt_trades.empty:
                trade_dates = pd.to_datetime(cdx_opt_trades['trade_date']).dt.date
                if prior_dt not in trade_dates.values:
                    avail = sorted([d for d in trade_dates.unique() if d < todt], reverse=True)
                    if avail:
                        prior_dt = avail[0]
                        gap = (todt - prior_dt).days
                        log = logging.warning if gap > 5 else logging.info
                        log(f'priorday_trades: -1b ({dtutil.dateadd(todt, "-1b")}) missing, '
                            f'falling back to {prior_dt} ({gap}d gap) — hist trades may be stale')

            priorday_trades_df = _duckq(f"""
                select {",".join(trade_details)}
                from cdx_opt_trades
                where ticker in {tosqllist(tickers)}
                and cast(trade_date as date) = '{prior_dt}'
            """, cdx_opt_trades=cdx_opt_trades)

            all_trades_wtd_df = _duckq(f"""
                select {",".join(trade_details)}
                from cdx_opt_trades
                where ticker in {tosqllist(tickers)}
                and cast(trade_date as date) >= '{dtutil.dateadd(todt, '-10b')}'
            """, cdx_opt_trades=cdx_opt_trades)

            cached = {
                'intraday_trades_df': intraday_trades_df,
                'priorday_trades_df': priorday_trades_df,
                'all_trades_wtd_df': all_trades_wtd_df,
            }
        except Exception:
            logging.exception('_get_trade_window_slices owner crashed')
            cached = {'intraday_trades_df': pd.DataFrame(), 'priorday_trades_df': pd.DataFrame(), 'all_trades_wtd_df': pd.DataFrame()}
        finally:
            with self._shared_trade_cache_lock:
                self._shared_trade_cache = {cache_key: cached}
                self._shared_trade_inflight.pop(cache_key, None)
            evt.set()
        return cached

    def prewarm_plot_cache(self, products=None):
        al = self.activity_log
        if self.curr_mark.empty or self.cdx_opt_trades.empty:
            al.log('Plot cache prewarm skipped (no data)', 'compute')
            return
        warmed = 0
        for product in (products or self.product_aggs.keys()):
            try:
                strikes = self.get_available_strikes(product)
                expiries = self.get_available_expiries(product)
                strike = strikes[len(strikes) // 2] if strikes else 100
                expiry = str(expiries[0]) if expiries else None
                if expiry:
                    self.build_plot_dfs(product, strike, expiry, skip_positions=True)
                    warmed += 1
            except Exception as e:
                logging.warning(f'prewarm_plot_cache({product}) failed: {e}')
        al.log(f'Plot cache pre-warmed: {warmed} products, {len(self._plot_cache)} entries', 'compute')

    def build_expiry_chart_frames(self, product, strike, expiry):
        strike_key = self._plot_selection_key(strike, numeric=True)
        expiry_key = self._plot_selection_key(expiry)
        cache_key = (
            'expiry_chart_frames', product,
            self._product_plot_versions.get(product, 0),
            self._snapshot_plot_version,
            strike_key, expiry_key,
        )
        with self._plot_cache_lock:
            cached = self._plot_cache.get(cache_key)
        if cached is not None:
            return cached
        frames = expiry_chart_frames(
            self.vol_mark_df, self.curr_mark, self.sender_mark, self.cdx_opt_trades,
            product, strike, expiry, self.plot_cols,
        )
        with self._plot_cache_lock:
            self._plot_cache[cache_key] = frames
        return frames

    @staticmethod
    def _plot_selection_key(value, numeric=False):
        values = value if isinstance(value, (list, tuple)) else [value]
        result = []
        for item in values:
            if item in (None, ''):
                continue
            if numeric:
                try:
                    result.append(float(item))
                    continue
                except (TypeError, ValueError):
                    pass
            result.append(str(item))
        return tuple(result)

    def build_plot_dfs(self, product, strike, expiry, ref_spot=None, vol_shift_bps=None, vol_lookback='5b',
                       include_all_trade_products=False, skip_positions=False):
        if self.curr_mark.empty or self.cdx_opt_trades.empty:
            logging.warning(f'build_plot_dfs({product}) -> None: curr_mark={len(self.curr_mark)}, cdx_opt_trades={len(self.cdx_opt_trades)}')
            return None

        has_overrides = (ref_spot is not None) or (vol_shift_bps is not None and vol_shift_bps != 0) or (vol_lookback != '5b')
        strike_key = self._plot_selection_key(strike, numeric=True)
        expiry_key = self._plot_selection_key(expiry)
        dedup_key = (product, strike_key, expiry_key, ref_spot, vol_shift_bps, vol_lookback,
                     include_all_trade_products, skip_positions)
        cache_key = (product, self._product_plot_versions.get(product, 0),
                     self._snapshot_plot_version, strike_key, expiry_key, ref_spot,
                     vol_shift_bps, vol_lookback, include_all_trade_products, skip_positions)
        with self._plot_cache_lock:
            if cache_key in self._plot_cache:
                return self._plot_cache[cache_key]
            evt = self._plot_inflight.get(dedup_key)
            if evt is not None:
                owner = False
            else:
                evt = threading.Event()
                self._plot_inflight[dedup_key] = evt
                owner = True

        if not owner:
            evt.wait(timeout=180)
            with self._plot_cache_lock:
                return self._plot_cache.get(cache_key)

        try:
            if has_overrides:
                result = self._compute_plot_dfs(
                    product, strike, expiry, ref_spot=ref_spot, vol_shift_bps=vol_shift_bps, vol_lookback=vol_lookback,
                    include_all_trade_products=include_all_trade_products, skip_positions=skip_positions,
                )
            else:
                result = self._compute_plot_dfs(
                    product, strike, expiry, vol_lookback=vol_lookback,
                    include_all_trade_products=include_all_trade_products, skip_positions=skip_positions,
                )
        except Exception:
            logging.exception(f'build_plot_dfs({product}) owner crashed')
            result = None
        finally:
            with self._plot_cache_lock:
                self._plot_inflight.pop(dedup_key, None)
            evt.set()

        if result is not None:
            fresh_cache_key = (product, self._product_plot_versions.get(product, 0),
                               self._snapshot_plot_version, strike_key, expiry_key, ref_spot,
                               vol_shift_bps, vol_lookback, include_all_trade_products, skip_positions)
            with self._plot_cache_lock:
                self._plot_cache[fresh_cache_key] = result
        return result

    def _compute_plot_dfs(self, product, strike, expiry, ref_spot=None, vol_shift_bps=None, vol_lookback='5b',
                          include_all_trade_products=False, skip_positions=False):
        prev_mark = self.get_prev_mark(lookback='1b')
        vol_lookback_mark = self.get_prev_mark(lookback=vol_lookback) if vol_lookback else pd.DataFrame()
        self.prev_mark = prev_mark
        cdx_opt_trades = self.cdx_opt_trades
        trade_windows = self._get_trade_window_slices()
        intraday_trades_df = trade_windows['intraday_trades_df']
        priorday_trades_df = trade_windows['priorday_trades_df']
        all_trades_wtd_df = trade_windows['all_trades_wtd_df']
        if not include_all_trade_products and 'product' in intraday_trades_df.columns:
            intraday_trades_df = intraday_trades_df[intraday_trades_df['product'] == product].copy()
        if not include_all_trade_products and 'product' in priorday_trades_df.columns:
            priorday_trades_df = priorday_trades_df[priorday_trades_df['product'] == product].copy()
        if not include_all_trade_products and 'product' in all_trades_wtd_df.columns:
            all_trades_wtd_df = all_trades_wtd_df[all_trades_wtd_df['product'] == product].copy()

        logging.info(f'_compute_plot_dfs({product}): raw trades cdx_opt_trades={len(cdx_opt_trades)} '
                     f'intraday={len(intraday_trades_df)} prior={len(priorday_trades_df)} '
                     f'wtd={len(all_trades_wtd_df)} todt={self.todt}')

        vol_query_sql = f"""
            select asof_date_time, vol_mark_id,
            {','.join(['v.' + x for x in self.plot_cols])}
            {{3}}
            from {{0}} v
            where v.product='{product}' and strike={{1}} and cast(cast(option_end_date as string) as date)='{{2}}'
            order by asof_date_time
        """
        trades_query_sql = f"""
            select execution_timestamp, implied_vol, implied_normal_vol as vol_bpd,
            implied_normal_vol*sqrt(252) as vol_ann, notional/1e6 as notional, dissemination_identifier
            from {{0}}
            where product='{product}' and strike_price={{1}} and cast(expiration_date as date)='{{2}}'
        """

        vol_mark_df = self.vol_mark_df
        curr_mark = self.curr_mark
        sender_mark = self.sender_mark

        cutoff = pd.to_datetime(self.todt)
        valid_exp = self._expiries_with_min_contributors(product)
        if not curr_mark.empty and 'option_end_date' in curr_mark.columns:
            exp_dt = pd.to_datetime(curr_mark['option_end_date'], errors='coerce')
            mask = exp_dt >= cutoff
            if valid_exp is not None:
                mask &= exp_dt.dt.date.isin(valid_exp)
            curr_mark = curr_mark[mask]
        if not sender_mark.empty and 'option_end_date' in sender_mark.columns:
            exp_dt = pd.to_datetime(sender_mark['option_end_date'], errors='coerce')
            mask = exp_dt >= cutoff
            if valid_exp is not None:
                mask &= exp_dt.dt.date.isin(valid_exp)
            sender_mark = sender_mark[mask]

        # Always re-enrich via _apply_spot_vol_overrides so that:
        #   1. _ensure_grid_rows adds any missing (expiry, strike) combos
        #   2. _fill_vol_gaps interpolates vols for newly-added rows
        #   3. premiums/greeks are recomputed at the *current* spot
        # Without this, the no-override path returns stale/incomplete data
        # because self.curr_mark may not contain all grid rows and its
        # payer_premium values may have been computed at an older spot.
        curr_mark = self._apply_spot_vol_overrides(curr_mark, product, ref_spot, vol_shift_bps)

        trades_df = self._get_strike_expiry_query_df(trades_query_sql, cdx_opt_trades, '', strike, expiry)
        vol_ts_df = self._get_strike_expiry_query_df(vol_query_sql, vol_mark_df, ', spot_price, spot_spread', strike, expiry)
        curr_vol_df = self._get_strike_expiry_query_df(vol_query_sql, curr_mark, ', spot_price, spot_spread', strike, expiry)
        sender_vol_df = self._get_strike_expiry_query_df(vol_query_sql, sender_mark, ', sender_bank', strike, expiry)
        logging.info(
            f'_compute_plot_dfs({product}): vol history rows={sum(len(v) for v in vol_ts_df.values())} '
            f'current={sum(len(v) for v in curr_vol_df.values())} sender={sum(len(v) for v in sender_vol_df.values())}'
        )

        mid_col = [x for x in self.plot_cols if ('bid' not in x) and ('ask' not in x)][0]

        idx_cols = ['product', 'strike', 'option_end_date']
        if prev_mark.empty or not all(c in prev_mark.columns for c in idx_cols):
            prev_mark = curr_mark.iloc[:0].copy()

        for _df in [curr_mark, prev_mark]:
            if 'option_end_date' in _df.columns and not _df.empty:
                _df['option_end_date'] = pd.to_datetime(_df['option_end_date'], format='mixed', dayfirst=False, errors='coerce')

        for _mdf in [curr_mark, prev_mark, vol_lookback_mark]:
            if not _mdf.empty and 'implied_vol' in _mdf.columns:
                fwd_bps = pd.to_numeric(_mdf.get('forward_spread'), errors='coerce').astype('float64') * 1e4 if 'forward_spread' in _mdf.columns else pd.Series(dtype=float)
                fwd_bps = fwd_bps.where(fwd_bps > 0)
                if 'par_spread_fwd_calc' in _mdf.columns:
                    fwd_bps = fwd_bps.reindex(_mdf.index).fillna(pd.to_numeric(_mdf['par_spread_fwd_calc'], errors='coerce'))
                if 'spot_spread' in _mdf.columns:
                    fwd_bps = fwd_bps.reindex(_mdf.index).fillna(_mdf['spot_spread'])
                _vm = _mdf['implied_vol'].notna() & fwd_bps.notna() & (fwd_bps > 0)
                _mdf.loc[_vm, 'vol_mid_ann'] = _mdf.loc[_vm, 'implied_vol'] * fwd_bps.loc[_vm]

        grid_strikes = self._get_grid_strikes(product)
        payer_prem = self._get_option_attrib_df('payer_premium', curr_mark, product, strikes=grid_strikes)
        receiver_prem = self._get_option_attrib_df('receiver_premium', curr_mark, product, strikes=grid_strikes)
        vols = self._get_option_attrib_df(mid_col, curr_mark, product, strikes=grid_strikes)

        vols_1b = self._get_option_attrib_df(mid_col, prev_mark, product, strikes=None)
        if not vols.empty and not vols_1b.empty:
            vols_1b_interp = self._interpolate_vol_grid(vols_1b, vols.index, vols.columns)
            both_present = vols.notna() & vols_1b_interp.notna()
            vol_diff_1b = (vols - vols_1b_interp).where(both_present)
            vol_diff_1b = vol_diff_1b.dropna(how='all')
        else:
            vol_diff_1b = pd.DataFrame()

        vol_diff_nb = pd.DataFrame()
        if vol_lookback == '1b':
            vol_diff_nb = vol_diff_1b.copy()
        elif vol_lookback and not vol_lookback_mark.empty:
            vols_nb = self._get_option_attrib_df(mid_col, vol_lookback_mark, product, strikes=None)
            if not vols.empty and not vols_nb.empty:
                vols_nb_interp = self._interpolate_vol_grid(vols_nb, vols.index, vols.columns)
                both_present = vols.notna() & vols_nb_interp.notna()
                vol_diff_nb = (vols - vols_nb_interp).where(both_present)
                vol_diff_nb = vol_diff_nb.dropna(how='all')
        spread_vols = self._get_option_attrib_df('implied_vol', curr_mark, product, strikes=grid_strikes)
        if not spread_vols.empty:
            spread_vols = spread_vols * 100
        deltas = self._get_option_attrib_df('delta', curr_mark, product, strikes=grid_strikes)
        gammas = self._get_option_attrib_df('gamma', curr_mark, product, strikes=grid_strikes)
        vegas = self._get_option_attrib_df('vega_ann', curr_mark, product, strikes=grid_strikes)
        bps_d = vols / np.sqrt(252) if not vols.empty else pd.DataFrame()
        if not vegas.empty:
            vegas = vegas / 1000
        spot_ref_result = self._build_spot_ref_matrix(curr_mark, product, grid_strikes)
        spot_ref = spot_ref_result['df']
        vol_ref = self._get_vol_ref_df(curr_mark, product, grid_strikes)
        live_spot_ref, live_vol_ref = self._get_live_refs(curr_mark, product)

        grid_map = dict(payer_prem=payer_prem, receiver_prem=receiver_prem, vol_diff_1b=vol_diff_1b,
                        vol_diff_nb=vol_diff_nb, vols=vols, bps_d=bps_d, spread_vols=spread_vols,
                        deltas=deltas, gammas=gammas, vegas=vegas)
        non_empty = [g for g in grid_map.values() if not g.empty]
        all_idx = non_empty[0].index if non_empty else pd.Index([])
        all_col = non_empty[0].columns if non_empty else pd.Index([])
        for g in non_empty[1:]:
            all_idx = all_idx.union(g.index)
            all_col = all_col.union(g.columns)
        grid_map = {k: v.reindex(index=all_idx, columns=all_col) for k, v in grid_map.items()}
        if not spot_ref.empty:
            spot_ref = spot_ref.reindex(columns=all_col)
        if not vol_ref.empty:
            vol_ref = vol_ref.reindex(columns=all_col)
        payer_prem, receiver_prem, vol_diff_1b, vol_diff_nb, vols, bps_d, spread_vols, deltas, gammas, vegas = grid_map.values()

        posdf = pd.DataFrame() if skip_positions else self._build_posdf(product, ref_spot=ref_spot, vol_shift_bps=vol_shift_bps)

        curr_mark_local = curr_mark.copy()
        for _col in ['payer_premium', 'receiver_premium']:
            if _col not in curr_mark_local.columns:
                curr_mark_local[_col] = np.nan
        for _mdf in [curr_mark_local]:
            _sanitize_mark_dtypes(_mdf)
        mark_join_cols = ['product', 'strike', 'option_end_date']
        curr_dupes = curr_mark_local.duplicated(mark_join_cols, keep=False).sum() if all(c in curr_mark_local.columns for c in mark_join_cols) else 0
        prev_mark_local = prev_mark.copy()
        _sanitize_mark_dtypes(prev_mark_local)
        prev_dupes = prev_mark_local.duplicated(mark_join_cols, keep=False).sum() if all(c in prev_mark_local.columns for c in mark_join_cols) else 0
        if curr_dupes:
            sort_cols = ['asof_date_time'] if 'asof_date_time' in curr_mark_local.columns else mark_join_cols
            curr_mark_local = curr_mark_local.sort_values(sort_cols).drop_duplicates(mark_join_cols, keep='last')
        if prev_dupes:
            sort_cols = ['asof_date_time'] if 'asof_date_time' in prev_mark_local.columns else mark_join_cols
            prev_mark_local = prev_mark_local.sort_values(sort_cols).drop_duplicates(mark_join_cols, keep='last')
        if curr_dupes or prev_dupes:
            logging.info(f'[{product}] deduped mark joins: curr={curr_dupes} prev={prev_dupes}')
        trades_chg_sql = '''
        select
        t.execution_timestamp, t.product,
        case when t.product like '%CDX%' then 'US' else 'non-US' end as region,
        t.option, option_unadj,
        case when t.buy_prob_greedy=1 then 'long' else 'short' end as dlr_dir,
        t.is_adj, t.estimated_notional, t.option_premium_bps,
        t.option_premium_chg, t.vol_ann,
        t.mid_vol_ann - pm.implied_normal_vol*sqrt(252) as vol_ann_mid_chg,
        t.vol_ann - t.mid_vol_ann as vol_ann_tcost,
        t.intraday_tcost,
        CASE WHEN coalesce(round(t.option_premium_bps*(case when right(t.option,1)='P' then cm.payer_premium else cm.receiver_premium end), 4), 0) = 0 THEN 0
        WHEN t.buy_prob_greedy = 1 THEN t.estimated_notional * ((case when right(t.option,1)='P' then cm.payer_premium else cm.receiver_premium end) - t.option_premium_bps) / 1e4
        ELSE -1 * t.estimated_notional * ((case when right(t.option,1)='P' then cm.payer_premium else cm.receiver_premium end) - t.option_premium_bps) / 1e4 END AS live_pnl,
        cm.spot_spread as live_spot, cm.spot_price as live_px, cm.implied_vol as live_implied_vol,
        cm.vol_mid_ann as live_vol_ann, cm.delta as live_delta, cm.vega_ann as live_vega_ann,
        t.spot, t.fwd, t.delta, t.est_MVE, t.net_vega_ann,
        expiration_date, strike_adjusts, option_adjusts, expiration_adjusts, strike_price
        from {0} t
        left join curr_mark_local cm on cm.product=t.product and cm.strike=t.strike_price and cm.option_end_date=t.expiration_date
        left join prev_mark_local pm on pm.product=t.product and pm.strike=t.strike_price and pm.option_end_date=t.expiration_date
        where t.expiration_date < '{1}'
        '''

        exp_cutoff = dtutil.dateadd(dtutil.today(), '12m')
        _join_tables = dict(curr_mark_local=curr_mark_local, prev_mark_local=prev_mark_local)

        if not curr_mark_local.empty and not intraday_trades_df.empty:
            mark_keys = set(zip(curr_mark_local['product'], curr_mark_local['strike'], curr_mark_local['option_end_date'].astype(str)))
            trade_keys = set(zip(intraday_trades_df['product'], intraday_trades_df['strike_price'], intraday_trades_df['expiration_date'].astype(str)))
            missing = trade_keys - mark_keys
            new_missing = missing - self._warned_missing_marks
            if new_missing:
                logging.warning(f'[{product}] {len(new_missing)} trades missing marks: {list(new_missing)[:5]}...')
                logging.info(f'[{product}] curr_mark has {len(mark_keys)} keys, sample: {list(mark_keys)[:3]}')
                self._warned_missing_marks |= new_missing
            elif missing:
                logging.debug(f'[{product}] {len(missing)} trades still missing marks (already reported)')

        intraday_trades_df = _duckq(trades_chg_sql.format('intraday_trades_df', exp_cutoff), intraday_trades_df=intraday_trades_df, **_join_tables)
        priorday_trades_df = _duckq(trades_chg_sql.format('priorday_trades_df', exp_cutoff), priorday_trades_df=priorday_trades_df, **_join_tables)
        all_trades_wtd_df = _duckq(trades_chg_sql.format('all_trades_wtd_df', exp_cutoff), all_trades_wtd_df=all_trades_wtd_df, **_join_tables)

        if include_all_trade_products:
            intraday_trades_df = self._enrich_trade_frame_all_products(intraday_trades_df, curr_mark)
            priorday_trades_df = self._enrich_trade_frame_all_products(priorday_trades_df, curr_mark)
            all_trades_wtd_df = self._enrich_trade_frame_all_products(all_trades_wtd_df, curr_mark)
        else:
            intraday_trades_df = self._enrich_trades_with_live_greeks(intraday_trades_df, product, curr_mark)
            priorday_trades_df = self._enrich_trades_with_live_greeks(priorday_trades_df, product, curr_mark)
            all_trades_wtd_df = self._enrich_trades_with_live_greeks(all_trades_wtd_df, product, curr_mark)

        logging.info(f'_compute_plot_dfs({product}): after join intraday={len(intraday_trades_df)} '
                     f'prior={len(priorday_trades_df)} wtd={len(all_trades_wtd_df)} exp_cutoff={exp_cutoff}')

        agg_live_mve = intraday_trades_df['live_MVE'].sum() if 'live_MVE' in intraday_trades_df.columns else 0
        agg_live_vega = intraday_trades_df['live_vega'].sum() if 'live_vega' in intraday_trades_df.columns else 0
        agg_live_pnl = intraday_trades_df['live_pnl'].sum() if 'live_pnl' in intraday_trades_df.columns else 0

        return {
            'vol_ts_df': vol_ts_df,
            'curr_vol_df': curr_vol_df,
            'sender_vol_df': sender_vol_df,
            'trades_df': trades_df,
            'intraday_trades_df': intraday_trades_df,
            'priorday_trades_df': priorday_trades_df,
            'all_trades_wtd_df': all_trades_wtd_df,
            'spot_ref': spot_ref,
            'spot_ref_status': spot_ref_result['status'],
            'vol_ref': vol_ref,
            'live_spot_ref': live_spot_ref,
            'live_vol_ref': live_vol_ref,
            'live_mve': agg_live_mve,
            'live_vega': agg_live_vega,
            'live_pnl': agg_live_pnl,
            'payer_prem': payer_prem,
            'receiver_prem': receiver_prem,
            'vol_diff_1b': vol_diff_1b,
            'vol_diff_nb': vol_diff_nb,
            'vol_lookback': vol_lookback,
            'vol_change_dates': {
                key: sorted(pd.to_datetime(mark.loc[mark['product'] == product, 'asof_date'],
                                           errors='coerce').dropna().dt.strftime('%Y-%m-%d').unique())
                if not mark.empty and 'asof_date' in mark else []
                for key, mark in [('vol_diff_1b', prev_mark), ('vol_diff_nb', vol_lookback_mark)]
            },
            'vols': vols,
            'bps_d': bps_d,
            'spread_vols': spread_vols,
            'deltas': deltas,
            'gammas': gammas,
            'vegas': vegas,
            'posdf': posdf,
        }

    def _get_strike_expiry_query_df(self, sql, df, sel_clause, strike, expiry):
        if df is None or df.empty or df.columns.empty:
            return {}
        df = _prepare_duckdb_query_frame(df)
        strike_list = tolist(strike)
        expiry_list = tolist(expiry)
        fill = strike_list[-1] if (len(strike_list) < len(expiry_list)) else expiry_list[-1]
        ret = {}
        for (s, e) in itertools.zip_longest(strike_list, expiry_list, fillvalue=fill):
            exp = pd.Timestamp(e).date().isoformat() if pd.notna(e) else e
            _df = _duckq(sql.format('df', s, exp, sel_clause), df=df)
            ret[(s, e)] = _df
        return ret

    def _apply_spot_vol_overrides(self, curr_mark, product, ref_spot=None, vol_shift_bps=None):
        cm = curr_mark[curr_mark['product'] == product].copy()
        other = curr_mark[curr_mark['product'] != product]
        if cm.empty:
            return curr_mark

        grid = self.pricing_grids.get(product)
        is_px = _is_px_index(product)

        if ref_spot is not None:
            old_spot = cm['spot_spread'].dropna().iloc[0] if cm['spot_spread'].notna().any() else None
            if is_px:
                cm['spot_price'] = ref_spot
                if grid and grid.spot_measures:
                    spot_dec = grid.price_to_spread(ref_spot)
                    cm['spot_spread'] = spot_dec * 1e4
            else:
                cm['spot_spread'] = ref_spot
                spot_dec = ref_spot / 1e4 if ref_spot > 1 else ref_spot
                if grid and grid.spot_measures:
                    cm['spot_price'] = grid.interp_spot_measure(spot_dec, 'clean_price')
            new_spot = cm['spot_spread'].dropna().iloc[0] if cm['spot_spread'].notna().any() else None
            if 'forward_spread' in cm.columns and old_spot and new_spot and old_spot > 0:
                fwd_vals = pd.to_numeric(cm['forward_spread'], errors='coerce')
                if (fwd_vals > 0).any():
                    cm['forward_spread'] = fwd_vals * (new_spot / old_spot)

        if vol_shift_bps is not None and vol_shift_bps != 0 and 'implied_vol' in cm.columns:
            fwd_bps = 0
            for _fc, _sc in [('forward_spread', 1e4), ('par_spread_fwd_calc', 1), ('spot_spread', 1)]:
                if _fc in cm.columns:
                    _fv = pd.to_numeric(cm[_fc], errors='coerce').dropna()
                    _fv = _fv[_fv > 0]
                    if not _fv.empty:
                        fwd_bps = float(_fv.iloc[0]) * _sc
                        break
            if fwd_bps > 0:
                cm['implied_vol'] = cm['implied_vol'] + vol_shift_bps / fwd_bps
            mid_col = [x for x in self.plot_cols if 'bid' not in x and 'ask' not in x][0]
            if mid_col in cm.columns:
                cm[mid_col] = cm[mid_col] + vol_shift_bps

        cm = pd.concat([other, cm], ignore_index=True)

        if grid and grid.grids:
            engine = GridPricingEngine(self.pricing_grids)
            if ref_spot is not None:
                agg = self.product_aggs.get(product)
                quotes = getattr(agg, 'quotes', None) if agg is not None else None
                if quotes is not None and not quotes.empty:
                    pq = quotes[quotes['product'] == product] if 'product' in quotes.columns else quotes
                    if 'strike_display' not in pq.columns and 'strike' in pq.columns:
                        pq = pq.assign(strike_display=pq['strike'])
                    if not pq.empty:
                        engine._update_live_vols(cm, pq, product)
            cm = engine.enrich_marks(cm, product)

        return cm

    def _interpolate_vol_grid(self, source_df, target_index, target_columns):
        """Interpolate a vol grid to match target index (expiries) and columns (strikes)."""
        if source_df.empty:
            return pd.DataFrame(index=target_index, columns=target_columns)

        from scipy.interpolate import RegularGridInterpolator

        src_expiries = pd.to_datetime(source_df.index).astype(np.int64) / 1e9
        src_strikes = source_df.columns.astype(float).values
        src_values = source_df.values

        valid_rows = ~np.all(np.isnan(src_values), axis=1)
        valid_cols = ~np.all(np.isnan(src_values), axis=0)
        if not valid_rows.any() or not valid_cols.any():
            return pd.DataFrame(index=target_index, columns=target_columns)

        src_expiries_valid = src_expiries[valid_rows]
        src_strikes_valid = src_strikes[valid_cols]
        src_values_valid = src_values[np.ix_(valid_rows, valid_cols)]

        src_values_filled = pd.DataFrame(src_values_valid).ffill(axis=0).bfill(axis=0).ffill(axis=1).bfill(axis=1).values
        if np.any(np.isnan(src_values_filled)):
            return source_df.reindex(index=target_index, columns=target_columns)

        if len(src_expiries_valid) < 2 or len(src_strikes_valid) < 2:
            return source_df.reindex(index=target_index, columns=target_columns)

        interp = RegularGridInterpolator(
            (src_expiries_valid, src_strikes_valid), src_values_filled,
            method='linear', bounds_error=False, fill_value=np.nan
        )

        tgt_expiries = pd.to_datetime(target_index).astype(np.int64) / 1e9
        tgt_strikes = target_columns.astype(float).values

        result = np.zeros((len(tgt_expiries), len(tgt_strikes)))
        for i, exp in enumerate(tgt_expiries):
            for j, stk in enumerate(tgt_strikes):
                result[i, j] = interp((exp, stk))

        return pd.DataFrame(result, index=target_index, columns=target_columns)

    def _get_option_attrib_df(self, col, df, product, strikes=None):
        if df.empty:
            return pd.DataFrame()
        needed = ['option_end_date', 'strike', col]
        if not all(c in df.columns for c in needed + ['product']):
            return pd.DataFrame()
        _df = df.loc[df['product'] == product, needed].copy()
        if 'option_end_date' in _df.columns and not pd.api.types.is_datetime64_any_dtype(_df['option_end_date']):
            _df['option_end_date'] = pd.to_datetime(_df['option_end_date'], format='mixed', dayfirst=False, errors='coerce')
        if _df.empty:
            return pd.DataFrame()
        _df = _df.drop_duplicates(subset=['option_end_date', 'strike'], keep='last')
        _df = _df.set_index(['option_end_date', 'strike']).unstack() \
            .dropna(how='all', axis=0).dropna(how='all', axis=1)
        if _df.empty or _df.columns.empty:
            return pd.DataFrame()
        if strikes is None:
            strike_vals = _df.columns.get_level_values('strike')
            tightest_strike = strike_vals.max() if _is_px_index(product) else strike_vals.min()
            if pd.isna(tightest_strike):
                return pd.DataFrame()
            strikes = SDRCDXOptPosMark.get_strike_range(_is_px_index(product), tightest_strike)
        result = _df.T.unstack(0).reindex(strikes).T.unstack()
        if isinstance(result.columns, pd.MultiIndex):
            result.columns = result.columns.droplevel(-1)
        return result

    def _build_spot_ref_matrix(self, curr_mark, product, strikes):
        df = self._get_spot_ref_df(curr_mark, product, strikes)
        if not df.empty:
            return {'df': df, 'status': {'source': 'grid', 'ok': True, 'message': 'grid spot measures'}}
        strike_vals = self._spot_ref_strikes(curr_mark, product, strikes)
        agg = self.product_aggs.get(product)
        df = (
            self._get_px_spot_ref_from_curves(product, strike_vals, agg)
            if _is_px_index(product)
            else self._get_spread_spot_ref_from_curves(product, strike_vals, agg)
        )
        ok = not df.empty and df.select_dtypes(include=[np.number]).notna().any().any()
        return {
            'df': df,
            'status': {
                'source': 'curve' if ok else 'none',
                'ok': ok,
                'message': 'curve price-to-spread fallback' if ok else 'no curve spot reference',
            },
        }

    @staticmethod
    def _spot_ref_strikes(curr_mark, product, strikes):
        if strikes is not None:
            return strikes
        if curr_mark.empty or 'product' not in curr_mark.columns or 'strike' not in curr_mark.columns:
            return []
        return curr_mark.loc[curr_mark['product'] == product, 'strike'].dropna().unique()

    def _get_spot_ref_df(self, curr_mark, product, strikes):
        grid = self.pricing_grids.get(product)
        is_px = _is_px_index(product)
        strike_vals = self._spot_ref_strikes(curr_mark, product, strikes)
        if grid is None or not grid.spot_measures:
            return pd.DataFrame()
        rows = {}
        for k in strike_vals:
            spread_dec = grid.price_to_spread(k) if is_px else (k / 1e4 if k > 1 else k)
            rows[k] = {
                'Spread (bps)': spread_dec * 1e4,
                'Price': grid.interp_spot_measure(spread_dec, 'clean_price'),
                'Risky PV01': grid.interp_spot_measure(spread_dec, 'risky_pv01'),
            }
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        drop_label = 'Price' if is_px else 'Spread (bps)'
        if drop_label in df.index:
            df.drop(drop_label, inplace=True)
        return df

    def _get_px_spot_ref_from_curves(self, product, strikes, agg=None):
        libor_curve, index_coupon, recovery, maturity = self._resolve_curve_inputs(product, agg)
        if libor_curve is None or pd.isna(index_coupon) or pd.isna(recovery) or maturity is None:
            logging.warning(f'[{product}] spot_ref fallback skipped: no curve inputs')
            return pd.DataFrame()
        rows = {}
        for k in strikes:
            if pd.isna(k):
                continue
            spread_dec = self._solve_spread_from_price(product, float(k), agg)
            risky_pv01 = self._spot_ref_risky_pv01(
                product, spread_dec, libor_curve, index_coupon, recovery, maturity,
            )
            rows[k] = {
                'Spread (bps)': spread_dec * 1e4 if pd.notnull(spread_dec) else np.nan,
                'Price': float(k),
                'Risky PV01': risky_pv01,
            }
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        if 'Price' in df.index:
            df.drop('Price', inplace=True)
        return df

    def _get_spread_spot_ref_from_curves(self, product, strikes, agg=None):
        libor_curve, index_coupon, recovery, maturity = self._resolve_curve_inputs(product, agg)
        if libor_curve is None or pd.isna(index_coupon) or pd.isna(recovery) or maturity is None:
            logging.warning(f'[{product}] spot_ref fallback skipped: no curve inputs')
            return pd.DataFrame()
        rows = {}
        for k in strikes:
            if pd.isna(k):
                continue
            spread_dec = float(k) / 1e4 if float(k) > 1 else float(k)
            measures = self._spot_ref_curve_measures(
                product, spread_dec, libor_curve, index_coupon, recovery, maturity,
            )
            rows[k] = {
                'Spread (bps)': spread_dec * 1e4,
                'Price': measures.get('price', np.nan),
                'Risky PV01': measures.get('risky_pv01', np.nan),
            }
        df = pd.DataFrame(rows)
        if 'Spread (bps)' in df.index:
            df.drop('Spread (bps)', inplace=True)
        return df

    def _spot_ref_curve_measures(self, product, spread_dec, libor_curve, index_coupon, recovery, maturity):
        if pd.isna(spread_dec) or spread_dec <= 0:
            return {}
        try:
            index_curve = cds_curve_using_dict(self.todt, libor_curve, {maturity: spread_dec}, recovery_rate=recovery)
            return CDXOptionQuoteAgg.calc_delta_one(
                self.todt, libor_curve, index_curve, maturity, index_coupon,
                recovery=recovery, fwd_tenor='0d', risk_metrics=['price', 'risky_pv01'],
            )
        except Exception as e:
            logging.warning(f'[{product}] spot_ref curve fallback failed: {type(e).__name__}: {e}')
            return {}

    def _spot_ref_risky_pv01(self, product, spread_dec, libor_curve, index_coupon, recovery, maturity):
        if pd.isna(spread_dec) or spread_dec <= 0:
            return np.nan
        try:
            index_curve = cds_curve_using_dict(self.todt, libor_curve, {maturity: spread_dec}, recovery_rate=recovery)
            d1 = CDXOptionQuoteAgg.calc_delta_one(
                self.todt, libor_curve, index_curve, maturity, index_coupon,
                recovery=recovery, fwd_tenor='0d', risk_metrics=['risky_pv01'],
            )
            return d1.get('risky_pv01', np.nan) if isinstance(d1, dict) else np.nan
        except Exception as e:
            logging.warning(f'[{product}] spot_ref risky_pv01 fallback failed: {type(e).__name__}: {e}')
            return np.nan

    def _get_vol_ref_df(self, curr_mark, product, strikes):
        if curr_mark.empty:
            return pd.DataFrame()
        cm = curr_mark[curr_mark['product'] == product]
        if cm.empty:
            return pd.DataFrame()
        if strikes is None:
            strikes = cm['strike'].dropna().unique()
        rows = {}
        for k in strikes:
            row_data = cm[cm['strike'] == k]
            if row_data.empty:
                continue
            r = row_data.iloc[0]
            vol_pct = r.get('implied_vol', np.nan)
            vol_ann = r.get('vol_mid_ann', np.nan)
            fwd_dec = r.get('forward_spread', np.nan)
            fwd_bps = fwd_dec * 1e4 if pd.notnull(fwd_dec) and fwd_dec > 0 else 0
            if fwd_bps <= 0:
                fwd_bps = r.get('par_spread_fwd_calc', 0) or 0
            if fwd_bps <= 0:
                fwd_bps = r.get('spot_spread', 0) or 0
            rows[k] = {
                'Vol (%)': vol_pct * 100 if pd.notnull(vol_pct) else np.nan,
                'Vol Ann (bps)': vol_ann,
                'Vol Ann (%)': vol_ann / fwd_bps * 100 if fwd_bps > 0 and pd.notnull(vol_ann) else np.nan,
            }
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def _get_live_refs(self, curr_mark, product):
        cm = curr_mark[curr_mark['product'] == product].copy()
        if cm.empty:
            return '', ''
        if 'asof_date_time' in cm.columns:
            cm = cm.assign(_sort_ts=pd.to_datetime(cm['asof_date_time'], errors='coerce')).sort_values('_sort_ts')
        spd = pd.to_numeric(cm['spot_spread'], errors='coerce').dropna().iloc[-1] if 'spot_spread' in cm.columns and cm['spot_spread'].notna().any() else np.nan
        px = pd.to_numeric(cm['spot_price'], errors='coerce').dropna().iloc[-1] if 'spot_price' in cm.columns and cm['spot_price'].notna().any() else np.nan
        vol_pct = pd.to_numeric(cm['implied_vol'], errors='coerce').dropna().iloc[-1] if 'implied_vol' in cm.columns and cm['implied_vol'].notna().any() else np.nan
        vol_ann = pd.to_numeric(cm['vol_mid_ann'], errors='coerce').dropna().iloc[-1] if 'vol_mid_ann' in cm.columns and cm['vol_mid_ann'].notna().any() else np.nan
        spot_parts = []
        if pd.notnull(px):
            spot_parts.append(f"{px:.2f} px")
        if pd.notnull(spd):
            spot_parts.append(f"{spd:.2f} bps")
        live_spot = ' | '.join(spot_parts) if spot_parts else ''
        vol_parts = []
        if pd.notnull(vol_pct):
            vol_parts.append(f"{vol_pct * 100:.1f}%")
        if pd.notnull(vol_ann):
            vol_parts.append(f"{vol_ann:.1f} bps ann")
        live_vol = ' | '.join(vol_parts) if vol_parts else ''
        return live_spot, live_vol

    def _enrich_trades_with_live_greeks(self, trades_df, product, curr_mark=None):
        if trades_df.empty:
            return trades_df

        is_px = _is_px_index(product)
        grid = self.pricing_grids.get(product)

        notional = trades_df['estimated_notional'].fillna(0)
        direction = np.where(trades_df['dlr_dir'] == 'long', 1, -1)

        live_px = trades_df['live_px'].copy() if 'live_px' in trades_df.columns else pd.Series(np.nan, index=trades_df.index)

        cm_sub = curr_mark[curr_mark['product'] == product] if curr_mark is not None and not curr_mark.empty else pd.DataFrame()
        spot_bps = cm_sub['spot_spread'].dropna().iloc[0] if not cm_sub.empty and 'spot_spread' in cm_sub.columns and cm_sub['spot_spread'].notna().any() else np.nan
        spot_dec = spot_bps / 1e4 if pd.notnull(spot_bps) and spot_bps > 1 else spot_bps
        fwd_bps = spot_bps
        if not cm_sub.empty:
            for _fc, _sc in [('forward_spread', 1e4), ('par_spread_fwd_calc', 1)]:
                if _fc in cm_sub.columns:
                    _fv = pd.to_numeric(cm_sub[_fc], errors='coerce').dropna()
                    _fv = _fv[_fv > 0]
                    if not _fv.empty:
                        fwd_bps = float(_fv.iloc[0]) * _sc
                        break

        raw_delta = trades_df['live_delta'].copy() if 'live_delta' in trades_df.columns else pd.Series(np.nan, index=trades_df.index)
        live_vega_ann = trades_df['live_vega_ann'].copy() if 'live_vega_ann' in trades_df.columns else pd.Series(np.nan, index=trades_df.index)
        live_vol_ann = trades_df['live_vol_ann'].copy() if 'live_vol_ann' in trades_df.columns else pd.Series(np.nan, index=trades_df.index)
        live_spot = trades_df['live_spot'].copy() if 'live_spot' in trades_df.columns else pd.Series(np.nan, index=trades_df.index)

        live_spot = live_spot.fillna(spot_bps)

        vol_interp = {}
        if not cm_sub.empty and 'strike' in cm_sub.columns and 'implied_vol' in cm_sub.columns:
            for exp, grp in cm_sub.groupby('option_end_date'):
                valid = grp[['strike', 'implied_vol']].dropna().sort_values('strike')
                if len(valid) >= 2:
                    vol_interp[exp] = (valid['strike'].values, valid['implied_vol'].values)

        missing_delta = pd.isna(raw_delta)
        if missing_delta.any() and grid is not None and bool(grid.grids):
            miss_idx = trades_df.index[missing_delta]
            expiries = trades_df.loc[miss_idx, 'expiration_date'].values
            strikes = trades_df.loc[miss_idx, 'strike_price'].values
            spots = np.full(len(miss_idx), spot_dec)

            vols = np.zeros(len(miss_idx))
            for i, (exp, strike) in enumerate(zip(expiries, strikes)):
                exp_key = pd.Timestamp(exp).normalize() if pd.notnull(exp) else None
                if exp_key in vol_interp:
                    strike_arr, vol_arr = vol_interp[exp_key]
                    vols[i] = np.interp(strike, strike_arr, vol_arr)
                elif 'vol_ann' in trades_df.columns and fwd_bps > 0:
                    vols[i] = trades_df.loc[miss_idx[i], 'vol_ann'] / fwd_bps

            batch = grid.interpolate_batch(expiries, strikes, spots, vols)
            raw_delta.loc[miss_idx] = batch.get('delta', np.nan)
            live_vega_ann.loc[miss_idx] = batch.get('vega_ann', np.nan)
            live_vol_ann.loc[miss_idx] = vols * fwd_bps

            filled = pd.notna(batch.get('delta', [])).sum()
            logging.info(f'[{product}] grid filled {filled}/{len(miss_idx)} missing deltas (vol interp from {len(vol_interp)} expiries)')

        option_col = trades_df['option'].astype(str) if 'option' in trades_df.columns else pd.Series('', index=trades_df.index)
        is_payer = option_col.str.endswith('P')

        spot_price = np.where(is_px, live_px, live_spot)
        has_mark = pd.notna(raw_delta) & pd.notna(spot_price)

        delta = np.where(is_payer, -raw_delta, 1 - raw_delta)
        live_mve = np.where(has_mark, direction * delta * spot_price / 100 * notional, np.nan)
        live_vega = np.where(has_mark & pd.notna(live_vega_ann), direction * live_vega_ann / 1e8 * notional, np.nan)

        no_mark = (~has_mark).sum()
        if no_mark > 0:
            logging.warning(f'[{product}] live_greeks: {has_mark.sum()}/{len(trades_df)} have marks, {no_mark} still missing')

        trades_df['live_vol_ann'] = live_vol_ann
        trades_df['live_spot'] = live_spot
        trades_df['live_delta'] = raw_delta
        trades_df['live_MVE'] = live_mve
        trades_df['live_vega'] = live_vega
        return trades_df

    def _enrich_trade_frame_all_products(self, trades_df, curr_mark=None):
        if trades_df.empty or 'product' not in trades_df.columns:
            return trades_df
        parts = []
        for prod, grp in trades_df.groupby('product', sort=False):
            parts.append(self._enrich_trades_with_live_greeks(grp.copy(), prod, curr_mark))
        return pd.concat(parts, ignore_index=True) if parts else trades_df

    def _build_posdf(self, product, ref_spot=None, vol_shift_bps=None):
        if self.cdx_opt_pos_df.empty:
            return pd.DataFrame()

        cache_key = (product, ref_spot, vol_shift_bps)
        now = time.time()
        with self._posdf_cache_lock:
            entry = self._posdf_cache.get(cache_key)
            if entry and (now - entry[1]) < 60:
                logging.info(f'_build_posdf cache HIT for {product} (age={now - entry[1]:.1f}s)')
                return entry[0].copy()
            evt = self._posdf_inflight.get(cache_key)
            if evt is not None:
                owner = False
            else:
                evt = threading.Event()
                self._posdf_inflight[cache_key] = evt
                owner = True

        if not owner:
            evt.wait(timeout=180)
            with self._posdf_cache_lock:
                entry = self._posdf_cache.get(cache_key)
                return entry[0].copy() if entry else pd.DataFrame()

        ticker = product.rsplit('_', 2)[0] if '_' in product else product
        base_filter = (self.cdx_opt_pos_df['time_scenario'].fillna('0d') == '0d')
        ticker_filter = (self.cdx_opt_pos_df['ticker'] == ticker) if 'ticker' in self.cdx_opt_pos_df.columns else True
        posdf = self.cdx_opt_pos_df[base_filter & ticker_filter].copy()

        grid = self.pricing_grids.get(product)
        is_px = _is_px_index(product)

        if grid is not None and bool(grid.grids):
            enriched = 0
            spot_dec_for_grid = np.nan

            if ref_spot is not None:
                old_spot_bps = posdf['spot_spread'].dropna().iloc[0] if 'spot_spread' in posdf.columns and posdf['spot_spread'].notna().any() else None
                if is_px:
                    spot_dec_for_grid = grid.price_to_spread(ref_spot) if grid.spot_measures else np.nan
                    posdf['spot_price'] = ref_spot
                    if pd.notnull(spot_dec_for_grid):
                        posdf['spot_spread'] = spot_dec_for_grid * 1e4
                else:
                    spot_dec_for_grid = ref_spot / 1e4 if ref_spot > 1 else ref_spot
                    posdf['spot_spread'] = ref_spot
                    if grid.spot_measures:
                        posdf['spot_price'] = grid.interp_spot_measure(spot_dec_for_grid, 'clean_price')
                new_spot_bps = posdf['spot_spread'].dropna().iloc[0] if posdf['spot_spread'].notna().any() else None
                if 'forward_spread' in posdf.columns and old_spot_bps and new_spot_bps and old_spot_bps > 0:
                    fwd_vals = pd.to_numeric(posdf['forward_spread'], errors='coerce')
                    if (fwd_vals > 0).any():
                        posdf['forward_spread'] = fwd_vals * (new_spot_bps / old_spot_bps)
            elif grid.spot_measures:
                cm = self.curr_mark[self.curr_mark['product'] == product]
                raw_spot = cm['spot_spread'].dropna().iloc[0] if not cm.empty and 'spot_spread' in cm.columns and cm['spot_spread'].notna().any() else np.nan
                spot_dec_for_grid = raw_spot / 1e4 if pd.notnull(raw_spot) and raw_spot > 1 else raw_spot

            if pd.notnull(spot_dec_for_grid) and grid.spot_measures:
                fwd_price = grid.interp_spot_measure(spot_dec_for_grid, 'clean_price')
                if 'forward_price' not in posdf.columns or posdf['forward_price'].isna().all():
                    posdf['forward_price'] = fwd_price

            if vol_shift_bps is not None and vol_shift_bps != 0:
                vol_col = 'implied_vol' if 'implied_vol' in posdf.columns else 'vol_ref_implied_vol'
                if vol_col in posdf.columns:
                    mark_fwd = self.get_live_fwd_bps(product) or 0
                    if ref_spot is not None and mark_fwd > 0:
                        _, orig_bps = self.get_live_spot(product)
                        new_bps = posdf['spot_spread'].dropna().iloc[0] if posdf['spot_spread'].notna().any() else 0
                        if orig_bps and orig_bps > 0 and new_bps > 0:
                            mark_fwd = mark_fwd * (new_bps / orig_bps)
                    fwd_bps = mark_fwd if mark_fwd > 0 else (posdf['spot_spread'].dropna().iloc[0] if 'spot_spread' in posdf.columns and posdf['spot_spread'].notna().any() else 0)
                    if fwd_bps > 0:
                        posdf[vol_col] = posdf[vol_col] + vol_shift_bps / fwd_bps

            expiries = posdf['expiration_date'].values if 'expiration_date' in posdf.columns else np.full(len(posdf), np.nan)
            strikes = posdf['strike_price'].values if 'strike_price' in posdf.columns else (posdf['strike'].values if 'strike' in posdf.columns else np.full(len(posdf), np.nan))
            spots = posdf['spot_spread'].values if 'spot_spread' in posdf.columns else (posdf['vol_ref_spot_spread'].values if 'vol_ref_spot_spread' in posdf.columns else np.full(len(posdf), np.nan))
            spots = np.where((spots > 1) & pd.notnull(spots), spots / 1e4, spots)
            vols = posdf['implied_vol'].values if 'implied_vol' in posdf.columns else (posdf['vol_ref_implied_vol'].values if 'vol_ref_implied_vol' in posdf.columns else np.full(len(posdf), np.nan))

            if len(posdf) > 0:
                logging.info(f'_build_posdf sample row: strike_price={strikes[0]} expiry={expiries[0]} spot={spots[0]} vol={vols[0]}')

            batch_result = grid.interpolate_batch(expiries, strikes, spots, vols)
            enriched = 0
            for col in ('delta', 'gamma', 'vega_ann'):
                if col in batch_result:
                    posdf[col] = batch_result[col]
                    enriched = np.count_nonzero(~np.isnan(batch_result[col]))
            skipped = np.count_nonzero(pd.isnull(spots) | pd.isnull(vols) | pd.isnull(expiries))
            missed = len(posdf) - enriched - skipped
            logging.info(f'_build_posdf grid: enriched={enriched} missed={missed} skipped={skipped} total={len(posdf)} for {product}')

        for col in ('gamma', 'delta', 'vega_ann', 'forward_price', 'prob_wt_estimated_notional'):
            if col not in posdf.columns:
                posdf[col] = np.nan

        posdf['gamma_usd'] = posdf['gamma'] * posdf['forward_price'] * 0.01 * posdf['prob_wt_estimated_notional']
        posdf['delta_usd'] = posdf['delta'] * posdf['forward_price'] * 0.01 * posdf['prob_wt_estimated_notional']
        posdf['vega_ann_usd'] = posdf['vega_ann'] * posdf['prob_wt_estimated_notional'] / 100_000_000
        posdf = _duckq(f"""
            select * from posdf
            where ticker='{ticker}' and expiration_date < '{dtutil.dateadd(dtutil.today(), '12m')}'
        """, posdf=posdf)
        posdf['product'] = product

        with self._posdf_cache_lock:
            self._posdf_cache[cache_key] = (posdf.copy(), time.time())
            self._posdf_inflight.pop(cache_key, None)
        evt.set()

        return posdf

    def get_rv_data(self, opt1, opt2=None, strike_type='strike', option_cols=None):
        option_cols = option_cols or ['vol_mid_ann']
        return VolMark.get_vol_using_mark(
            self.vol_mark_df.copy(deep=False), opt1, opt2=opt2,
            strike_type=strike_type, option_cols=option_cols
        )

    def get_rv_data_current(self, opt1, opt2=None, strike_type='strike', option_cols=None):
        option_cols = option_cols or ['vol_mid_ann']
        return VolMark.get_vol_using_mark(
            self.curr_mark.copy(deep=False), opt1, opt2=opt2,
            strike_type=strike_type, option_cols=option_cols
        )


def refresh_cdx_options_cache(asof_date=None, lookback='6m', full_build=True):
    asof_date = dtutil.adjustdate(asof_date or dtutil.today(), 'backward')
    logging.info(f'refreshing cdx options cache for {asof_date} (full_build={full_build})')

    if full_build:
        loader = CDXOptionsDataLoader(todt=asof_date, lookback=lookback)
        loader._force_grid_rebuild = True
        loader.load_all_with_progress()
        logging.info(f'full build done: curr_mark={len(loader.curr_mark)}, grids={len(loader.pricing_grids)}, '
                 f'products={list(loader.product_aggs.keys())}')
    else:
        fromdt = dtutil.dateadd(asof_date, f'-{lookback}')
        logging.info(f'loading hist vols from DB: {fromdt} to {asof_date}')
        vols = _to_ny(cds_index_vol.get_vol_mark(fromdt, asof_date, vol_mark_type='median', use_live=False))
        if vols is not None and not vols.empty:
            _save_hist_cache('hist_vols', asof_date, vols)
            logging.info(f'cached hist vols: {len(vols):,} rows')

        logging.info(f'loading hist trades from DB')
        trades = _to_ny(sdr_db.get_cdx_opt_trades(dtutil.dateadd(asof_date, '-6m'), dtutil.dateadd(asof_date, '-1d'), use_live=False))
        if trades is not None and not trades.empty:
            _save_hist_cache('hist_trades', asof_date, trades)
            logging.info(f'cached hist trades: {len(trades):,} rows')

    purge_old_grid_cache()
    logging.info('cdx options cache refresh complete')


def _seed_grids_from_prev_day(asof_date):
    import shutil
    prev_date = dtutil.dateadd(asof_date, '-1b')
    prev_dir = get_grid_cache_dir(prev_date)
    today_dir = get_grid_cache_dir(asof_date)

    if not os.path.isdir(prev_dir):
        logging.info(f'no prev day grid cache at {prev_dir}')
        return

    existing = [f for f in os.listdir(today_dir) if f.startswith('grid_') and f.endswith('.parquet')]
    if existing:
        logging.info(f'today grid cache already has {len(existing)} files -- skipping seed')
        return

    copied = 0
    for fname in os.listdir(prev_dir):
        if not fname.startswith('grid_') or '_error' in fname:
            continue
        if fname.endswith('.parquet'):
            shutil.copy2(os.path.join(prev_dir, fname), os.path.join(today_dir, fname))
            copied += 1
    logging.info(f'seeded {copied} grid files from {prev_date} to {asof_date}')
