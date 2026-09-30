import copy
import gc
import gzip
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime as _dt

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.flight as flight
import psutil
import pytz

from apo_config.config import required_config
from common.utils import dtutil
from common.utils.jobutil import JobExecutor
from dash_reports.cdx_options.scheduler import stage_timer
from dash_reports.cdx_options.data.data_loader import CDXOptionsDataLoader, LoadProgress, ActivityLog
from dash_reports.cdx_options.data.pricing_grid import get_grid_cache_dir
from dash_reports.cdx_options.data.relative_vol import parse_rv_option
from dash_reports.cdx_options.constants import DEFAULT_TICKERS
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_certs
from dash_reports.sub_servers.cdx_options_server.serialization import sanitize, json_result, error_result

APP = 'cdx_options_server'
_NY = pytz.timezone('America/New_York')
_ROLLOVER_HOUR = 6
_VIEW_CACHE_VERSION = 1
_VIEW_CACHE_PREFIX = 'materialized_view_'
_SELECTION_VIEWS_PER_PRODUCT = 8

FAST_ACTIONS = frozenset({
    'health_check', 'get_status', 'get_progress_events', 'pick_defaults',
    'get_products', 'get_expiries', 'get_strikes', 'get_grid_badge',
    'get_status_info', 'get_activity', 'get_product_title',
    'get_live_spot', 'get_spot_snapshot', 'get_delta_one_strip', 'get_update_text', 'convert_spot',
    'get_calc_context', 'get_cached_tabs',
})


class CDXOptionsFlightServer(flight.FlightServerBase):
    _request_counter = 0
    _counter_lock = threading.Lock()

    def __init__(self, host=None, port=None, **kwargs):
        psutil.Process().nice(psutil.NORMAL_PRIORITY_CLASS)
        host = host or required_config("cdx_options_server", "server.host")
        port = port or int(required_config("cdx_options_server", "server.port"))
        ssl_ctx = load_tls_certs()
        if ssl_ctx:
            with open(ssl_ctx[0], 'rb') as f:
                tls_cert = f.read()
            with open(ssl_ctx[1], 'rb') as f:
                tls_key = f.read()
            location = flight.Location.for_grpc_tls(host, port)
            super().__init__(location, tls_certificates=[(tls_cert, tls_key)], **kwargs)
            logging.info(f"CDX Flight TLS on {host}:{port}")
        else:
            location = flight.Location.for_grpc_tcp(host, port)
            super().__init__(location, **kwargs)
            logging.info(f"CDX Flight (no TLS) on {host}:{port}")

        self._host, self._port = host, port
        self._loader = None
        self._load_thread = None
        self._refresh_thread = None
        self._rebuild_thread = None
        self._light_refresh_thread = None
        self._staged_reprice_thread = None
        self._staged_reprice_pending = False
        self._staged_hist_thread = None
        self._staged_hist_pending = False
        self._lock = threading.Lock()
        self._events = []
        self._events_offset = 0
        self._events_lock = threading.Lock()
        self._data_generation = 0
        self._refresh_interval = 60
        self._refresh_timer = None
        self._defaults_cache = {}
        self._last_defaults_event_sig = None
        self._last_status_info_event_sig = None
        self._products_cache = []
        self._tab_cache = {}
        self._tab_cache_lock = threading.Lock()
        self._view_cache_lock = threading.Lock()
        self._view_cache_context_key = None
        self._tab_cache_ready = {}
        self._heavy_tab_cache = {}
        self._selection_view_cache = OrderedDict()
        self._tab_compute_inflight = {}
        self._tab_compute_results = {}
        self._vol_grid_inflight = {}
        self._vol_grid_results = {}
        self._agg_refresh_lock = threading.Lock()
        self._agg_refresh_inflight = set()
        self._push_pending = {}
        self._push_lock = threading.Lock()
        self._push_wake = threading.Event()
        self._push_worker = threading.Thread(target=self._push_loop, daemon=True, name='PushWorker')
        self._push_worker.start()
        self._executor = JobExecutor('thread', max_workers=8, timeout=300)

        self._autoload()

    def _autoload(self):
        todt = dtutil.adjustdate(dtutil.today(), 'backward')
        body = {
            'tickers': DEFAULT_TICKERS, 'todt': str(todt),
            'lookback': '6m', 'mark_pos': True, 'mark_time': '',
        }
        logging.info(f'AUTO-LOAD: starting immediately with {DEFAULT_TICKERS}, todt={todt}')
        self._start_load(body, is_refresh=False)

    # ── State helpers ───────────────────────────────────────────

    def _is_loading(self):
        return self._load_thread is not None and self._load_thread.is_alive()

    def _is_refreshing(self):
        return self._refresh_thread is not None and self._refresh_thread.is_alive()

    def _is_rebuilding(self):
        return self._rebuild_thread is not None and self._rebuild_thread.is_alive()

    def _is_light_refreshing_thread(self):
        return self._light_refresh_thread is not None and self._light_refresh_thread.is_alive()

    def _is_background_repricing_thread(self):
        return self._staged_reprice_thread is not None and self._staged_reprice_thread.is_alive()

    def _is_background_hist_refresh_thread(self):
        return self._staged_hist_thread is not None and self._staged_hist_thread.is_alive()

    @staticmethod
    def _curr_mark_products(loader):
        if loader is None or loader.curr_mark.empty or 'product' not in loader.curr_mark.columns:
            return set()
        return set(loader.curr_mark['product'].dropna().unique().tolist())

    def _selected_product_hint(self, loader=None):
        ldr = loader or self._loader
        return getattr(ldr, 'priority_product', None) or (self._defaults_cache or {}).get('active_product')

    @staticmethod
    def _active_subscription_selection(product):
        if not product:
            return {}
        try:
            from dash_reports.cdx_options.ws_events import event_bus
            return event_bus.latest_selection(product=product)
        except Exception:
            logging.exception('Failed to read live tab selection for %s', product)
            return {}

    @staticmethod
    def _quote_max_ts(df):
        if df is None or df.empty or 'asof_date_time' not in df.columns:
            return pd.NaT
        return pd.to_datetime(df['asof_date_time'], errors='coerce').max()

    @staticmethod
    def _json_ts(value):
        if value is None or pd.isna(value):
            return ''
        try:
            return pd.Timestamp(value).isoformat()
        except Exception:
            return str(value)

    @staticmethod
    def _json_sel(value):
        if isinstance(value, (list, tuple)):
            return [CDXOptionsFlightServer._json_sel(v) for v in value]
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value

    @staticmethod
    def _key_value(value, numeric=False, timestamp=False):
        if isinstance(value, (list, tuple)):
            return tuple(CDXOptionsFlightServer._key_value(v, numeric=numeric, timestamp=timestamp) for v in value if v not in (None, ''))
        if timestamp:
            try:
                return pd.Timestamp(value).strftime('%Y-%m-%d')
            except Exception:
                return str(value)
        if numeric:
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value)
        return '' if value is None else str(value)

    class _TabComputeOwner:

        def __init__(self, server, key):
            self._server = server
            self._key = key
            self._done = False

        def finish(self, payload):
            with self._server._tab_cache_lock:
                self._server._tab_compute_results[self._key] = (time.time(), payload)
                evt = self._server._tab_compute_inflight.pop(self._key, None)
                if evt is not None:
                    evt.set()
            self._done = True
            return json_result(payload)

        def fail(self):
            if self._done:
                return
            with self._server._tab_cache_lock:
                evt = self._server._tab_compute_inflight.pop(self._key, None)
                if evt is not None:
                    evt.set()
            self._done = True

    def _begin_tab_compute(self, tab_name, compute_key, ttl=60):
        full_key = (tab_name, *compute_key)
        now = time.time()
        with self._tab_cache_lock:
            stale = [k for k, (ts, _) in self._tab_compute_results.items() if now - ts > ttl]
            for k in stale:
                self._tab_compute_results.pop(k, None)
            cached = self._tab_compute_results.get(full_key)
            if cached is not None:
                return None, cached[1]
            evt = self._tab_compute_inflight.get(full_key)
            if evt is None:
                evt = threading.Event()
                self._tab_compute_inflight[full_key] = evt
                return self._TabComputeOwner(self, full_key), None
        logging.info(f'{tab_name}: waiting for inflight {compute_key}')
        evt.wait(timeout=300)
        with self._tab_cache_lock:
            cached = self._tab_compute_results.get(full_key)
        if cached is not None:
            return None, cached[1]
        logging.warning(f'{tab_name}: inflight wait returned without cached result {compute_key}')
        return None, None

    def _build_calc_context(self, ldr, product, strike, expiry, ref_px=None, ref_bps=None,
                            vol_shift_bps=None, vol_lookback='5b'):
        show_px = show_bps = None
        if ref_px is not None or ref_bps is not None:
            convert_spot = getattr(ldr, 'convert_spot', None)
            show_px, show_bps = convert_spot(product, ref_px=ref_px, ref_bps=ref_bps) if convert_spot else (ref_px, ref_bps)
        elif hasattr(ldr, 'get_live_spot'):
            show_px, show_bps = ldr.get_live_spot(product)

        curr_mark = getattr(ldr, 'curr_mark', pd.DataFrame())
        cm = curr_mark[curr_mark['product'] == product].copy() if not curr_mark.empty and 'product' in curr_mark.columns else pd.DataFrame()
        qdf = getattr(ldr, 'quotes_df', pd.DataFrame())
        qdf = qdf[qdf['product'] == product] if not qdf.empty and 'product' in qdf.columns else pd.DataFrame()
        agg = getattr(ldr, 'product_aggs', {}).get(product)
        agg_q = getattr(agg, 'quotes', pd.DataFrame()) if agg is not None else pd.DataFrame()
        grid = getattr(ldr, 'pricing_grids', {}).get(product)
        grid_version = ''
        if grid is not None:
            grid_version = '|'.join(str(getattr(grid, attr, '')) for attr in ('valuedt', 'updated_at', 'version'))

        context = {
            'product': product,
            'strike': self._json_sel(strike),
            'expiry': self._json_sel(expiry),
            'ref_px': show_px if ref_px is not None or ref_bps is not None else None,
            'ref_bps': show_bps if ref_px is not None or ref_bps is not None else None,
            'live_px': show_px,
            'live_bps': show_bps,
            'vol_shift_bps': vol_shift_bps,
            'vol_lookback': vol_lookback or '5b',
            'spot_ts': self._json_ts(self._quote_max_ts(cm)),
            'quote_ts': self._json_ts(self._quote_max_ts(qdf)),
            'agg_quote_ts': self._json_ts(self._quote_max_ts(agg_q)),
            'grid_version': grid_version,
            'plot_version': getattr(ldr, '_product_plot_versions', {}).get(product, 0),
            'snapshot_version': getattr(ldr, '_snapshot_plot_version', 0),
            'spot_generation': getattr(ldr, '_spot_refresh_generation', 0),
            'override': ref_px is not None or ref_bps is not None or vol_shift_bps is not None,
        }
        context['calc_sig'] = json.dumps(context, sort_keys=True, default=str)
        return context

    def _refresh_live_product_agg_if_stale(self, ldr, product):
        if ldr is None or not product:
            return False
        quotes_df = getattr(ldr, 'quotes_df', pd.DataFrame())
        if quotes_df is None or quotes_df.empty or 'product' not in quotes_df.columns:
            return False
        product_quotes = quotes_df[quotes_df['product'] == product]
        if product_quotes.empty:
            return False
        agg = (getattr(ldr, 'product_aggs', {}) or {}).get(product)
        live_max = self._quote_max_ts(product_quotes)
        agg_max = self._quote_max_ts(getattr(agg, 'quotes', pd.DataFrame()))
        if pd.isna(live_max) or (pd.notna(agg_max) and live_max <= agg_max):
            return False
        refresh_aggs = getattr(ldr, '_refresh_product_agg_quotes', None)
        if not callable(refresh_aggs):
            return False

        with self._agg_refresh_lock:
            if product in self._agg_refresh_inflight:
                logging.info('Product agg refresh already queued for %s', product)
                return True
            self._agg_refresh_inflight.add(product)

        def _run():
            try:
                updated = set(refresh_aggs(products=[product]) or [])
                if product not in updated:
                    return
                self._mark_tab_caches_stale([product], reason='live_quotes_changed')
                self._publish_data_generation(
                    products=[product],
                    reason='live_quotes_changed',
                    changed_tabs=self._HEAVY_TABS,
                )
                logging.info('Refreshed stale product_agg for %s in background (%s -> %s)', product, agg_max, live_max)
            except Exception:
                logging.exception('Background product agg refresh failed for %s', product)
            finally:
                with self._agg_refresh_lock:
                    self._agg_refresh_inflight.discard(product)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f'AggRefresh-{product}',
        ).start()
        logging.info('Queued stale product_agg refresh for %s; serving current aggregate', product)
        return True

    def _queue_live_heavy_tab_refresh(self, ldr):
        product = self._selected_product_hint(ldr)
        if not product:
            return False
        refresh_aggs = getattr(ldr, '_refresh_product_agg_quotes', None)
        if callable(refresh_aggs):
            refresh_aggs(products=[product])
        agg = (getattr(ldr, 'product_aggs', {}) or {}).get(product)
        if agg is None:
            ensure_agg = getattr(ldr, 'ensure_product_agg', None)
            if callable(ensure_agg):
                agg = ensure_agg(product)
        if agg is None:
            return False
        selection = self._active_subscription_selection(product)
        strike = selection.get('strike') or None
        expiry = selection.get('expiry') or None
        with self._tab_cache_lock:
            prev_heavy = dict(self._heavy_tab_cache.get(product) or {})
        self._mark_tab_caches_stale([product], reason='spot_refresh')
        if (strike is None or expiry is None) and prev_heavy.get('_key'):
            cached_strike, cached_expiry = prev_heavy['_key']
            strike = strike or cached_strike or None
            expiry = expiry or cached_expiry or None
        self._push_tab_data(product, strike=strike, expiry=expiry, bridge_only=False)
        logging.info('Queued live heavy-tab refresh for %s strike=%s expiry=%s', product, strike, expiry)
        return True

    def _can_swap_loader(self, current, candidate):
        cand_products = self._curr_mark_products(candidate)
        if not cand_products:
            logging.warning('Swap rejected: candidate loader has empty curr_mark')
            return False
        curr_products = self._curr_mark_products(current)
        missing = sorted(curr_products - cand_products)
        if missing:
            logging.warning(f'Swap rejected: candidate curr_mark missing live products {missing}')
            return False
        return True

    def _commit_loader(self, loader):
        with self._lock:
            self._loader = loader
        self._mark_tab_caches_stale(reason='loader_commit')
        self._update_defaults_cache()

    def _clear_tab_caches(self, product=None):
        with self._tab_cache_lock:
            selection_cache = getattr(self, '_selection_view_cache', None)
            if selection_cache is None:
                selection_cache = OrderedDict()
                self._selection_view_cache = selection_cache
            if product is None:
                self._tab_cache.clear()
                self._heavy_tab_cache.clear()
                selection_cache.clear()
                self._tab_compute_inflight.clear()
                self._tab_compute_results.clear()
                self._tab_cache_ready.clear()
                return
            self._tab_cache.pop(product, None)
            self._heavy_tab_cache.pop(product, None)
            for key in [key for key in selection_cache if key[0] == product]:
                selection_cache.pop(key, None)
            for key in list(self._tab_compute_inflight):
                if key and (key[0] == product or (len(key) > 1 and key[1] == product)):
                    self._tab_compute_inflight.pop(key, None)
            for key in list(self._tab_compute_results):
                if key and (key[0] == product or (len(key) > 1 and key[1] == product)):
                    self._tab_compute_results.pop(key, None)
            self._tab_cache_ready.pop(product, None)

    @classmethod
    def _selection_view_key(cls, product, strike, expiry):
        strike_key, expiry_key = cls._selection_key(strike, expiry)
        return product, tuple(strike_key), tuple(expiry_key)

    def _selection_cache_locked(self):
        cache = getattr(self, '_selection_view_cache', None)
        if cache is None:
            cache = OrderedDict()
            self._selection_view_cache = cache
        return cache

    def _get_selection_view_locked(self, product, strike, expiry):
        cache = self._selection_cache_locked()
        key = self._selection_view_key(product, strike, expiry)
        entry = cache.get(key)
        if entry is not None:
            cache.move_to_end(key)
        return entry

    def _store_selection_view_locked(self, product, strike, expiry, entry):
        cache = self._selection_cache_locked()
        key = self._selection_view_key(product, strike, expiry)
        cache[key] = entry
        cache.move_to_end(key)
        product_keys = [candidate for candidate in cache if candidate[0] == product]
        for old_key in product_keys[:-_SELECTION_VIEWS_PER_PRODUCT]:
            cache.pop(old_key, None)
        return key

    @staticmethod
    def _materialized_context(loader):
        return {
            'version': _VIEW_CACHE_VERSION,
            'tickers': sorted(getattr(loader, 'tickers', []) or []),
            'mark_pos': bool(getattr(loader, 'mark_pos', True)),
            'mark_time': getattr(loader, 'mark_time_override', '') or '',
            'thresh_time': getattr(loader, 'thresh_time_override', '4h') or '4h',
            'lookback': getattr(loader, 'lookback', '6m') or '6m',
        }

    @classmethod
    def _materialized_context_key(cls, loader):
        context = {
            **cls._materialized_context(loader),
            'todt': str(getattr(loader, 'todt', '')),
        }
        return json.dumps(context, sort_keys=True)

    @staticmethod
    def _materialized_view_path(loader, product, valuedt=None):
        safe_product = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(product))
        return os.path.join(
            get_grid_cache_dir(valuedt or getattr(loader, 'todt', None)),
            f'{_VIEW_CACHE_PREFIX}{safe_product}.json.gz',
        )

    def _persist_materialized_view(self, product):
        loader = self._loader
        if loader is None or not product or not getattr(loader, 'todt', None):
            return
        with self._tab_cache_lock:
            bridge = copy.deepcopy(self._tab_cache.get(product) or {})
            heavy = copy.deepcopy(self._heavy_tab_cache.get(product) or {})
            selections = [
                {
                    'strike': list(key[1]),
                    'expiry': list(key[2]),
                    'view': copy.deepcopy(entry),
                }
                for key, entry in self._selection_cache_locked().items()
                if key[0] == product
            ]
        if not bridge and not heavy and not selections:
            return
        envelope = sanitize({
            'version': _VIEW_CACHE_VERSION,
            'context': self._materialized_context(loader),
            'product': product,
            'data_asof': str(getattr(loader, 'todt', '')),
            'saved_at': _dt.now(_NY).isoformat(),
            'bridge': bridge,
            'heavy': heavy,
            'selections': selections,
        })
        path = self._materialized_view_path(loader, product)
        temp_path = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
        try:
            lock = getattr(self, '_view_cache_lock', None)
            if lock is None:
                lock = threading.Lock()
                self._view_cache_lock = lock
            with lock:
                with gzip.open(temp_path, 'wt', encoding='utf-8') as f:
                    json.dump(envelope, f, separators=(',', ':'), allow_nan=False)
                os.replace(temp_path, path)
        except (OSError, TypeError, ValueError):
            logging.exception('Failed to persist materialized view for %s', product)
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _load_materialized_views(self, loader):
        dates = []
        for value in (getattr(loader, 'todt', None), getattr(loader, '_snapshot_date', None)):
            if value is not None and value not in dates:
                dates.append(value)
        expected_context = self._materialized_context(loader)
        loaded = 0
        seen = set()
        for valuedt in dates:
            cache_dir = get_grid_cache_dir(valuedt)
            for name in os.listdir(cache_dir):
                if not name.startswith(_VIEW_CACHE_PREFIX) or not name.endswith('.json.gz'):
                    continue
                path = os.path.join(cache_dir, name)
                try:
                    with gzip.open(path, 'rt', encoding='utf-8') as f:
                        envelope = json.load(f)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    logging.exception('Failed to load materialized view %s', path)
                    continue
                product = envelope.get('product')
                if not product or product in seen:
                    continue
                stored_context = dict(envelope.get('context') or {})
                stored_context.setdefault('lookback', '6m')
                if envelope.get('version') != _VIEW_CACHE_VERSION or stored_context != expected_context:
                    continue
                bridge = dict(envelope.get('bridge') or {})
                heavy = dict(envelope.get('heavy') or {})
                selections = list(envelope.get('selections') or [])
                if bridge.get('_bridge_key'):
                    bridge['_bridge_key'] = self._selection_key(*bridge['_bridge_key'])
                if heavy.get('_key'):
                    heavy['_key'] = self._selection_key(*heavy['_key'])
                stale_meta = {
                    '_cache_state': 'stale',
                    '_stale': True,
                    '_stale_reason': 'server_restart',
                    '_stale_at': _dt.now(_NY).isoformat(),
                    '_materialized_at': envelope.get('saved_at'),
                    '_data_asof': envelope.get('data_asof'),
                }
                if bridge:
                    bridge.update(stale_meta)
                if heavy:
                    heavy.update(stale_meta)
                with self._tab_cache_lock:
                    if bridge:
                        self._tab_cache[product] = bridge
                    if heavy:
                        self._heavy_tab_cache[product] = heavy
                    restored_entries = 0
                    for selection in selections:
                        entry = dict(selection.get('view') or {})
                        if not entry:
                            continue
                        entry.update(stale_meta)
                        self._store_selection_view_locked(
                            product,
                            selection.get('strike'),
                            selection.get('expiry'),
                            entry,
                        )
                        restored_entries += 1
                    if not restored_entries:
                        bridge_selection = bridge.get('_bridge_key')
                        heavy_selection = heavy.get('_key')
                        if bridge_selection:
                            entry = {
                                key: copy.deepcopy(value)
                                for key, value in bridge.items()
                                if key in self._BRIDGE_TABS
                            }
                            entry.update(stale_meta)
                            if heavy_selection == bridge_selection:
                                entry.update({
                                    key: copy.deepcopy(value)
                                    for key, value in heavy.items()
                                    if key in self._HEAVY_TABS
                                })
                            self._store_selection_view_locked(
                                product, bridge_selection[0], bridge_selection[1], entry,
                            )
                        if heavy_selection and heavy_selection != bridge_selection:
                            entry = {
                                key: copy.deepcopy(value)
                                for key, value in heavy.items()
                                if key in self._HEAVY_TABS
                            }
                            entry.update(stale_meta)
                            self._store_selection_view_locked(
                                product, heavy_selection[0], heavy_selection[1], entry,
                            )
                    ready = self._tab_cache_ready.setdefault(product, threading.Event())
                    ready.set()
                seen.add(product)
                loaded += 1
        if loaded:
            logging.info('Restored %s durable materialized product views as stale', loaded)
        return loaded

    def _activate_materialized_cache(self, loader):
        context_key = self._materialized_context_key(loader)
        if context_key == self._view_cache_context_key:
            return
        self._clear_tab_caches()
        self._view_cache_context_key = context_key
        self._load_materialized_views(loader)

    def _mark_tab_caches_stale(self, products=None, reason='data_changed'):
        with self._tab_cache_lock:
            targets = set(products or self._tab_cache.keys() | self._heavy_tab_cache.keys())
            stale_at = _dt.now(_NY).isoformat()
            for product in targets:
                for cache in (self._tab_cache, self._heavy_tab_cache):
                    entry = cache.get(product)
                    if isinstance(entry, dict):
                        entry.update({
                            '_cache_state': 'stale',
                            '_stale': True,
                            '_stale_reason': reason,
                            '_stale_at': stale_at,
                        })
                    elif entry is not None:
                        cache.pop(product, None)
                        self._tab_cache_ready.pop(product, None)
            for key, entry in self._selection_cache_locked().items():
                if key[0] in targets:
                    entry.update({
                        '_cache_state': 'stale',
                        '_stale': True,
                        '_stale_reason': reason,
                        '_stale_at': stale_at,
                        '_stale_tabs': [
                            tab for tab in self._BRIDGE_TABS | self._HEAVY_TABS
                            if tab in entry
                        ],
                    })
            for key, evt in list(self._tab_compute_inflight.items()):
                product = key[0] if isinstance(key, tuple) and key else key
                if product in targets:
                    if hasattr(evt, 'set'):
                        evt.set()
                    self._tab_compute_inflight.pop(key, None)
            for key in list(self._tab_compute_results):
                product = key[0] if isinstance(key, tuple) and key else key
                if product in targets:
                    self._tab_compute_results.pop(key, None)
        return targets

    def _maybe_start_pending_hist_refresh(self):
        next_loader = None
        with self._lock:
            if (self._staged_hist_pending
                    and not self._is_background_repricing_thread()
                    and not self._is_background_hist_refresh_thread()):
                next_loader = self._loader
        if (next_loader is not None
                and not self._is_loading()
                and not self._is_refreshing()
                and not self._is_rebuilding()):
            logging.info('Background hist refresh starting after pending request')
            self._start_background_hist_refresh(next_loader)

    def _start_background_reprice(self, current):
        if current is None:
            return 'no_loader'
        with self._lock:
            if self._is_background_repricing_thread():
                self._staged_reprice_pending = True
                current.activity_log.log('Background grid repricing already running; coalescing latest spot update', 'compute')
                return 'coalesced'
            self._staged_reprice_pending = False
            self._staged_reprice_thread = threading.Thread(
                target=self._run_process_reprice,
                args=(current,),
                daemon=True,
                name='ProcessReprice',
            )
            self._staged_reprice_thread.start()
        current.activity_log.log('Background grid repricing queued', 'compute')
        self._push_status_info_event(force=True)
        return 'started'

    def _start_background_hist_refresh(self, current):
        if current is None:
            return 'no_loader'
        with self._lock:
            if self._is_background_hist_refresh_thread():
                self._staged_hist_pending = True
                current.activity_log.log('Background hist refresh already running; coalescing latest request', 'query')
                return 'coalesced'
            staged = current.spawn_staged_reprice_loader()
            staged._source_spot_refresh_generation = getattr(current, '_spot_refresh_generation', 0)
            staged.activity_log = ActivityLog(event_bus=_EventSink(self))
            self._staged_hist_pending = False
            self._staged_hist_thread = threading.Thread(
                target=self._run_staged_hist_refresh,
                args=(current, staged),
                daemon=True,
                name='StagedHistRefresh',
            )
            self._staged_hist_thread.start()
        if self._is_background_repricing_thread():
            current.activity_log.log('Background hist refresh started in parallel with grid repricing', 'query')
        else:
            current.activity_log.log('Background hist refresh queued', 'query')
        self._push_status_info_event(force=True)
        return 'started'

    def _run_process_reprice(self, current):
        try:
            current.activity_log.log('Background grid repricing started (separate process)', 'compute')
            from dash_reports.sub_servers.cdx_options_server import reprice_worker
            settings = {
                'tickers': list(current.tickers),
                'todt': str(current.todt),
                'lookback': getattr(current, 'lookback', '6m'),
                'mark_pos': getattr(current, 'mark_pos', True),
                'mark_time': getattr(current, 'mark_time_override', ''),
                'thresh_time': getattr(current, 'thresh_time_override', '4h'),
                'grid_repair_backoff': getattr(current, '_grid_repair_backoff', {}),
            }
            executor = JobExecutor('process', max_workers=1, timeout=900)
            try:
                results = executor.submitjob(reprice_worker.run_reprice, [[settings]],
                                             job_name='grid_reprice', initconfig=True)
            finally:
                executor.cleanup()
            meta = results[0].get('result') if results else None
            if not meta:
                raise RuntimeError(f"reprice worker failed: {results[0].get('error') if results else 'no result'}")
            self._apply_process_reprice(current, meta)
        except Exception:
            logging.exception('Background process repricing failed; keeping current loader')
            if current is not None:
                current.activity_log.log('Background grid repricing failed; keeping current live state', 'error')
                self._push_event('activity', _activity_payload(current))
        finally:
            next_loader = None
            with self._lock:
                rerun = self._staged_reprice_pending
                self._staged_reprice_pending = False
                self._staged_reprice_thread = None
                if rerun:
                    next_loader = self._loader
            self._push_status_info_event(force=True)
            self._schedule_refresh()
            gc.collect()
            if rerun and next_loader is not None and not self._is_loading() and not self._is_refreshing() and not self._is_rebuilding():
                logging.info('Background grid repricing rerun requested after coalesced spot updates')
                self._start_background_reprice(next_loader)
            else:
                self._maybe_start_pending_hist_refresh()

    def _apply_process_reprice(self, current, meta):
        from dash_reports.sub_servers.cdx_options_server import reprice_worker
        from dash_reports.cdx_options.data.data_loader import _StubAgg
        out_dir = reprice_worker.reprice_output_dir(current.todt)
        with self._lock:
            live = self._loader
        if live is None or live is not current:
            logging.info('Process repricing result discarded; live loader changed')
            return
        live._grid_repair_backoff = meta.get('grid_repair_backoff', {})
        live.curr_mark = pd.read_parquet(os.path.join(out_dir, 'curr_mark.parquet'))
        for attr, name in (('grid_pos_df', 'grid_pos.parquet'), ('sender_mark', 'sender_mark.parquet')):
            path = os.path.join(out_dir, name)
            if os.path.isfile(path):
                setattr(live, attr, pd.read_parquet(path))
        agg_path = os.path.join(out_dir, 'agg_quotes.parquet')
        if os.path.isfile(agg_path):
            agg_df = pd.read_parquet(agg_path)
            live.product_aggs = {
                prod: _StubAgg(grp.drop(columns=['_agg_product']))
                for prod, grp in agg_df.groupby('_agg_product')
            }
        new_cm, _ = live._apply_live_spot_updates()
        if not new_cm.empty:
            live.curr_mark = new_cm
        products = sorted(self._curr_mark_products(live))
        live._bump_plot_versions(products=products, include_snapshot=False)
        self._mark_tab_caches_stale(products, reason='vol_mark_update')
        self._update_defaults_cache()
        self._push_event('spot_update', live.get_spot_snapshot())
        payload = live.get_light_refresh_payload()
        payload['spot_only'] = False
        self._push_event('light_refresh_done', payload)
        self._publish_data_generation(products, reason='vol_mark_update')
        live.activity_log.log('Background grid repricing applied from worker process', 'compute')
        logging.info(f'Background process repricing applied: products={products} elapsed={meta.get("elapsed_s")}s')

    def _run_staged_hist_refresh(self, current, staged):
        try:
            staged.activity_log.log('Background hist refresh started', 'query')
            staged.refresh_hist_trades()
            with self._lock:
                live = self._loader
            if live is None:
                logging.info('Background staged hist refresh discarded; no live loader')
            else:
                live.apply_hist_trades_from(staged)
                products = sorted(self._curr_mark_products(live))
                self._mark_tab_caches_stale(products, reason='hist_trades_update')
                self._update_defaults_cache()
                live._maybe_save_snapshot()
                self._publish_data_generation(products, reason='hist_trades_update')
                live.activity_log.log('Background hist refresh applied (trade frames patched)', 'query')
                logging.info('Background staged hist refresh applied additively')
        except Exception:
            logging.exception('Background staged hist refresh failed; keeping current loader')
            if current is not None:
                current.activity_log.log('Background hist refresh failed; keeping current live state', 'error')
                self._push_event('activity', _activity_payload(current))
        finally:
            with self._lock:
                self._staged_hist_thread = None
            self._push_status_info_event(force=True)
            self._schedule_refresh()
            gc.collect()
            self._maybe_start_pending_hist_refresh()

    @staticmethod
    def _hist_trades_stale(ldr):
        ht = getattr(ldr, 'cdx_opt_hist_trades', pd.DataFrame())
        if ht is None or ht.empty or 'trade_date' not in ht.columns:
            logging.info('Hist trades coverage: none loaded -> stale=True')
            return True
        max_dt = pd.to_datetime(ht['trade_date']).dt.date.max()
        if pd.isna(max_dt):
            logging.info('Hist trades coverage: no valid trade_date -> stale=True')
            return True
        expected = dtutil.dateadd(ldr.todt, '-1b')
        stale = max_dt < expected
        logging.info(f'Hist trades coverage: max_date={max_dt}, expected>={expected}, stale={stale}')
        return stale

    def _queue_post_startup_refresh(self, ldr):
        logging.info('Deferred: staged grid repricing left to auto-refresh scheduler')

    def _ensure_hist_fresh(self, ldr):
        if not self._hist_trades_stale(ldr):
            return
        logging.info(f'Deferred: hist trades stale, refreshing for {ldr.todt}')
        hist_state = self._start_background_hist_refresh(self._loader or ldr)
        logging.info(f'Deferred: staged hist refresh {hist_state}')

    _COALESCE_TYPES = frozenset({'tab_data', 'defaults', 'progress', 'compute_status'})
    _BRIDGE_TABS = frozenset({'vol_grids', 'trade_blotter', 'positions'})
    _HEAVY_TABS = frozenset({'option_stack', 'expiry_charts'})
    _TAB_BODY_KEYS = frozenset({'vol_grids', 'trade_blotter', 'positions', 'expiry_charts', 'option_stack', 'defaults'})
    _TAB_CACHE_META = (
        '_cache_state', '_stale', '_stale_reason', '_stale_at', '_materialized_at',
        '_data_asof', '_generation', '_refresh_duration_ms', '_stale_tabs',
        '_key', '_bridge_key', '_bridge_calc_sig', '_tab_generations',
    )

    @classmethod
    def _event_coalesce_key(cls, event_type, payload=None):
        payload = payload or {}
        if event_type == 'tab_data':
            strike_key, expiry_key = cls._selection_key(payload.get('strike'), payload.get('expiry'))
            return event_type, payload.get('product'), tuple(strike_key), tuple(expiry_key)
        if event_type == 'compute_status':
            strike_key, expiry_key = cls._selection_key(payload.get('strike'), payload.get('expiry'))
            return event_type, payload.get('product'), tuple(strike_key), tuple(expiry_key)
        if event_type in ('defaults', 'progress', 'status_info'):
            return event_type,
        return None

    def _push_event(self, event_type, payload=None):
        event = {'type': event_type, **(payload or {})}
        event_key = self._event_coalesce_key(event_type, payload)
        with self._events_lock:
            if event_key is not None:
                for ev in self._events:
                    if ev.get('_event_key') == event_key and not ev.get('_sup'):
                        ev['_sup'] = True
            if event_key is not None:
                event['_event_key'] = event_key
            self._events.append(event)
            if len(self._events) > 500:
                trim = len(self._events) - 500
                self._events = self._events[-500:]
                self._events_offset += trim

    def _drain_events(self, since=0):
        with self._events_lock:
            abs_total = self._events_offset + len(self._events)
            if since < self._events_offset:
                since = self._events_offset
            if since > abs_total:
                since = self._events_offset
            idx = since - self._events_offset
            return [e for e in self._events[idx:] if not e.get('_sup')], abs_total

    # ── Arrow / JSON helpers ────────────────────────────────────

    # ── Flight protocol ─────────────────────────────────────────

    def list_actions(self, context):
        for attr in dir(self):
            if attr.startswith('_act_'):
                name = attr[5:]
                yield (name, name)

    def do_action(self, context, action):
        t0 = time.time()
        t = threading.current_thread()
        if t.name.startswith('Dummy'):
            with self._counter_lock:
                self._request_counter += 1
            t.name = f"CDX_{self._request_counter}"

        body = {}
        if action.body and action.body.to_pybytes():
            try:
                body = json.loads(action.body.to_pybytes().decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        handler = getattr(self, f'_act_{action.type}', None)
        if handler is None:
            yield error_result(f'Unknown action: {action.type}')
            return
        try:
            if action.type in FAST_ACTIONS:
                result = handler(body)
            else:
                res = self._executor.submitjob(handler, [[body]], job_name=action.type)
                result = res[0]['result'] if res and res[0]['error'] is None else error_result(res[0]['error'] if res else 'executor failed')
            if hasattr(result, '__next__'):
                yield from result
            else:
                yield result
        except Exception as e:
            logging.exception(f'Action {action.type} failed')
            yield error_result(str(e))
        finally:
            elapsed = (time.time() - t0) * 1000
            if elapsed > 50:
                logging.info(f'[SERVER] {action.type} took {elapsed:.0f}ms')

    # ── Lifecycle actions ───────────────────────────────────────

    def _act_health_check(self, b):
        return json_result({
            'success': True, 'service': APP,
            'loader': self._loader is not None,
            'loading': self._is_loading(),
            'refreshing': self._is_refreshing(),
            'rebuilding': self._is_rebuilding(),
            'light_refreshing': self._is_light_refreshing_thread(),
            'background_repricing': self._is_background_repricing_thread(),
            'background_hist_refresh': self._is_background_hist_refresh_thread(),
        })

    def _act_load(self, b):
        return self._start_load(b, is_refresh=False)

    def _act_refresh(self, b):
        return self._start_load(b, is_refresh=True)

    def _act_refresh_light(self, b):
        ldr = self._loader
        if ldr is None:
            return json_result({'status': 'no_loader'})
        if self._is_light_refreshing_thread() or ldr._is_light_refreshing:
            return json_result({'status': 'light_refresh_in_progress'})
        self._run_light_refresh_async(ldr, queue_heavy=True, reason='manual')
        return json_result({'status': 'started'})

    def _act_rebuild_grids(self, b):
        ldr = self._loader
        if ldr is None:
            return json_result({'status': 'no_loader'})
        if self._is_rebuilding():
            return json_result({'status': 'rebuild_in_progress'})
        settings = {
            'tickers': list(ldr.tickers),
            'todt': ldr.todt,
            'mark_pos': ldr.mark_pos,
            'mark_time_override': getattr(ldr, 'mark_time_override', ''),
            'thresh_time_override': getattr(ldr, 'thresh_time_override', '4h'),
        }
        with self._lock:
            self._rebuild_thread = threading.Thread(
                target=self._run_staged_rebuild_from_current,
                args=(ldr, settings),
                daemon=True,
                name='StagedFullRebuild',
            )
            self._rebuild_thread.start()
        self._push_status_info_event(force=True)
        ldr.activity_log.log('Grid rebuild queued in background', 'compute')
        self._push_event('activity', _activity_payload(ldr))
        logging.info('Staged rebuild thread queued (loader construction moved off request thread)')
        return json_result({'status': 'started'})

    def _act_set_refresh_interval(self, b):
        self._refresh_interval = max(5, b.get('seconds', 60))
        self._schedule_refresh()
        return json_result({'status': 'ok', 'interval': self._refresh_interval})

    def _act_start_manual_refresh(self, b):
        ldr = self._loader
        if ldr is None or not ldr.progress.done:
            return json_result({'status': 'no_loader' if ldr is None else 'not_ready'})
        if self._is_light_refreshing_thread() or ldr._is_light_refreshing:
            return json_result({'status': 'light_refresh_in_progress'})
        self._run_light_refresh_async(ldr, queue_heavy=True, reason='manual')
        return json_result({'status': 'started'})

    # ── Status actions ──────────────────────────────────────────

    def _act_get_status(self, b):
        ldr = self._loader
        if ldr is None:
            return json_result({'loader': None})
        snap_save = getattr(ldr, '_snapshot_save_time', None) or getattr(ldr, '_last_snapshot_save', None)
        return json_result(sanitize({
            'progress': ldr.progress.to_dict(),
            'loading': self._is_loading(),
            'refreshing': self._is_refreshing(),
            'rebuilding': self._is_rebuilding(),
            'light_refreshing': self._is_light_refreshing_thread() or ldr._is_light_refreshing,
            'background_repricing': self._is_background_repricing_thread(),
            'background_hist_refresh': self._is_background_hist_refresh_thread(),
            'hist_loaded': ldr._hist_loaded,
            'tickers': ldr.tickers,
            'todt': str(ldr.todt),
            'products': ldr.get_session_product_universe(),
            'ready_products': ldr.get_available_products(),
            'last_load': snap_save.strftime('%m/%d %H:%M') if snap_save else None,
            'snapshot_from': str(getattr(ldr, '_snapshot_date', None)),
        }))

    def _thin_progress_event(self, event):
        if event.get('type') != 'tab_data':
            return event
        return {key: value for key, value in event.items() if key not in self._TAB_BODY_KEYS}

    def _copy_cached_tabs(self, source, requested_tabs):
        if not source:
            return {}
        keys = set(requested_tabs or (self._BRIDGE_TABS | self._HEAVY_TABS))
        keys.update(self._TAB_CACHE_META)
        return {key: copy.deepcopy(source[key]) for key in keys if key in source}

    def _act_get_progress_events(self, b):
        events, cursor = self._drain_events(b.get('since', 0))
        return json_result({'events': [self._thin_progress_event(event) for event in events], 'cursor': cursor})

    # ── Metadata actions ────────────────────────────────────────

    def _act_get_products(self, b):
        ldr = self._loader
        return json_result(ldr.get_session_product_universe() if ldr else [])

    def _act_get_expiries(self, b):
        ldr = self._loader
        if not ldr:
            return json_result([])
        return json_result([str(e) for e in ldr.get_available_expiries(b['product'])])

    def _act_get_strikes(self, b):
        ldr = self._loader
        if not ldr:
            return json_result([])
        return json_result(ldr.get_available_strikes(b['product']))

    def _act_get_product_title(self, b):
        ldr = self._loader
        return json_result({'title': ldr.get_product_title(b['product']) if ldr else b['product']})

    def _act_get_live_spot(self, b):
        ldr = self._loader
        if not ldr:
            return json_result({'px': None, 'bps': None})
        px, bps = ldr.get_live_spot(b['product'])
        return json_result(sanitize({'px': px, 'bps': bps}))

    def _act_get_spot_snapshot(self, b):
        ldr = self._loader
        if not ldr:
            return json_result({'ts': '', 'spots': {}, 'items': []})
        return json_result(sanitize(ldr.get_spot_snapshot(products=b.get('products'))))

    def _act_convert_spot(self, b):
        ldr = self._loader
        if not ldr:
            return json_result({'px': b.get('ref_px'), 'bps': b.get('ref_bps')})
        px, bps = ldr.convert_spot(b['product'], ref_px=b.get('ref_px'), ref_bps=b.get('ref_bps'))
        return json_result(sanitize({'px': px, 'bps': bps}))

    def _act_get_delta_one_strip(self, b):
        ldr = self._loader
        return json_result(sanitize(ldr.get_delta_one_strip()) if ldr else [])

    def _act_get_update_text(self, b):
        ldr = self._loader
        if not ldr:
            return json_result({'text': ''})
        return json_result({'text': ldr.get_update_text()})

    def _act_get_grid_badge(self, b):
        ldr = self._loader
        return json_result({'text': ldr.get_grid_badge_text() if ldr else ''})

    def _act_get_status_info(self, b):
        ldr = self._loader
        if ldr and hasattr(ldr, 'get_status_info'):
            return json_result(ldr.get_status_info())
        from datetime import datetime
        import pytz
        now = datetime.now(pytz.timezone('America/New_York'))
        return json_result({
            'now_local': now.strftime('%H:%M'),
            'mark_time': now.strftime('%H:%M'),
            'status': 'live', 'grids_loaded': 0,
        })

    def _act_get_activity(self, b):
        ldr = self._loader
        return json_result(_activity_payload(ldr))

    def _act_pick_defaults(self, b):
        ldr = self._loader
        if not ldr:
            if self._defaults_cache:
                return json_result(self._defaults_cache)
            return json_result(self._empty_defaults_payload())
        ap = b.get('active_product')
        strike = b.get('strike')
        expiry = b.get('expiry')
        if isinstance(strike, list):
            strike = strike[0] if strike else None
        if isinstance(expiry, list):
            expiry = expiry[0] if expiry else None
        if not ap and strike is None and expiry is None and self._defaults_cache:
            return json_result(self._defaults_cache)
        payload = self._build_defaults_payload(ldr, active_product=ap, strike=strike, expiry=expiry)
        ap = payload.get('active_product')
        if ap:
            ldr.priority_product = ap
        self._products_cache = [opt['value'] for opt in payload.get('product_options', [])]
        return json_result(payload)

    # ── Render actions ──────────────────────────────────────────

    def _act_render_vol_grids(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']

        ref_px, ref_bps = b.get('ref_px'), b.get('ref_bps')
        vol_shift = b.get('vol_shift_bps')
        has_overrides = (ref_px is not None or ref_bps is not None or vol_shift is not None
                         or (b.get('vol_lookback') or '5b') != '5b')
        if not has_overrides and not b.get('_internal'):
            with self._tab_cache_lock:
                cached = (self._tab_cache.get(product) or {}).get('vol_grids')
            if cached:
                self._push_tab_data(product)
                return json_result(cached)
            self._push_tab_data(product)

        strike, expiry = self._defaults(ldr, product, b.get('strike'), b.get('expiry'))
        if not expiry:
            return json_result({'grids': None})

        vol_lookback = b.get('vol_lookback', '5b') or '5b'
        compute_key = (
            product, self._key_value(strike, numeric=True), self._key_value(expiry, timestamp=True), str(ref_px), str(ref_bps),
            str(vol_shift), str(vol_lookback),
        )
        with self._tab_cache_lock:
            stale = [k for k, (ts, _) in self._vol_grid_results.items() if time.time() - ts > 60]
            for k in stale:
                self._vol_grid_results.pop(k, None)
            evt = self._vol_grid_inflight.get(compute_key)
            if evt is None:
                evt = threading.Event()
                self._vol_grid_inflight[compute_key] = evt
                inflight_owner = True
            else:
                inflight_owner = False
        if not inflight_owner:
            logging.info(f'render_vol_grids: waiting for inflight {product} strike={strike} expiry={expiry}')
            evt.wait(timeout=300)
            with self._tab_cache_lock:
                cached_result = self._vol_grid_results.get(compute_key)
            if cached_result is not None:
                return json_result(cached_result[1])

        def finish(payload):
            with self._tab_cache_lock:
                self._vol_grid_results[compute_key] = (time.time(), payload)
                done_evt = self._vol_grid_inflight.pop(compute_key, None)
                if done_evt is not None:
                    done_evt.set()
            return json_result(payload)

        try:
            ref_spot, show_px, show_bps = self._resolve_overrides(ldr, product, ref_px, ref_bps)

            live_px, live_bps = ldr.get_live_spot(product)
            title = ldr.get_product_title(product)

            from sdr.dtccpy.credit_utils import _is_px_index
            plot_data = ldr.build_plot_dfs(
                product, strike, expiry,
                ref_spot=ref_spot, vol_shift_bps=vol_shift,
                vol_lookback=vol_lookback, skip_positions=True,
            )
            if plot_data:
                plot_data['vol_lookback'] = vol_lookback
            if not plot_data:
                return finish({'grids': None})
            from dash_reports.cdx_options.data.vol_grids import build_all_vol_grids
            grids = build_all_vol_grids(plot_data, title, is_px=_is_px_index(product))
            return finish(sanitize({
                'grids': grids,
                'live_px': live_px, 'live_bps': live_bps,
                'show_px': show_px, 'show_bps': show_bps,
                'live_spot_ref': plot_data.get('live_spot_ref', ''),
                'live_vol_ref': plot_data.get('live_vol_ref', ''),
                'live_mve': plot_data.get('live_mve', 0),
                'live_vega': plot_data.get('live_vega', 0),
                'product_title': title, 'strike': strike, 'expiry': expiry,
                'override_text': self._build_override_text(ldr, product, ref_px, ref_bps, vol_shift),
            }))
        except Exception:
            with self._tab_cache_lock:
                done_evt = self._vol_grid_inflight.pop(compute_key, None)
                if done_evt is not None:
                    done_evt.set()
            raise

    def _act_get_calc_context(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']
        strike, expiry = self._defaults(ldr, product, b.get('strike'), b.get('expiry'))
        ref_px, ref_bps = b.get('ref_px'), b.get('ref_bps')
        vol_shift = b.get('vol_shift_bps')
        vol_lookback = b.get('vol_lookback', '5b')
        calc_context = self._build_calc_context(
            ldr, product, strike, expiry,
            ref_px=ref_px, ref_bps=ref_bps,
            vol_shift_bps=vol_shift, vol_lookback=vol_lookback,
        )
        return json_result(sanitize({
            'product': product,
            'strike': strike,
            'expiry': expiry,
            'calc_context': calc_context,
            '_calc_sig': calc_context['calc_sig'],
            'defaults': self._defaults_cache,
            'update_text': ldr.get_update_text(),
            'grid_badge': ldr.get_grid_badge_text(),
            'override_text': self._build_override_text(ldr, product, ref_px, ref_bps, vol_shift),
        }))

    def _act_render_trade_blotter(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']
        ref_px, ref_bps = b.get('ref_px'), b.get('ref_bps')
        vol_shift = b.get('vol_shift_bps')
        has_overrides = ref_px is not None or ref_bps is not None or vol_shift is not None
        if not has_overrides and not b.get('_internal'):
            with self._tab_cache_lock:
                cached = (self._tab_cache.get(product) or {}).get('trade_blotter')
            if cached:
                self._push_tab_data(product)
                return json_result(cached)
            self._push_tab_data(product)

        strike, expiry = self._defaults(ldr, product, b.get('strike'), b.get('expiry'))
        compute_key = (
            product, self._key_value(strike, numeric=True), self._key_value(expiry, timestamp=True),
            str(ref_px), str(ref_bps), str(vol_shift),
        )
        owner, cached = self._begin_tab_compute('trade_blotter', compute_key)
        if cached is not None:
            return json_result(cached)

        try:
            ref_spot, show_px, show_bps = self._resolve_overrides(ldr, product, ref_px, ref_bps)
            live_px, live_bps = ldr.get_live_spot(product)
            agg_ready = ldr.progress.data_ready.get('agg', False)

            logging.info(f'render_trade_blotter: product={product} agg_ready={agg_ready} '
                         f'curr_mark={len(ldr.curr_mark)} trades={len(ldr.cdx_opt_trades)}')

            plot_data = None
            if agg_ready:
                plot_data = ldr.build_plot_dfs(product, strike, expiry,
                                              ref_spot=ref_spot, vol_shift_bps=vol_shift,
                                              include_all_trade_products=True,
                                              skip_positions=True) if expiry else None
                logging.info(f'render_trade_blotter: strike={strike} expiry={expiry} '
                             f'plot_data={"None" if plot_data is None else list(plot_data.keys())[:5]}')
                from dash_reports.cdx_options.data.trade_blotter import build_trade_blotter_data
                if plot_data:
                    blotters = sanitize(build_trade_blotter_data(plot_data))
                    for k, v in blotters.items():
                        logging.info(f'render_trade_blotter: {k} rows={len(v.get("data", []))}')
            early_fallback = plot_data is None
            if early_fallback:
                from dash_reports.cdx_options.data.trade_blotter import format_trade_df, build_trade_columns
                raw = ldr.build_early_trades()
                blotters = {}
                for key, label in [('intraday_trades_df', 'Intraday Trades'),
                                   ('priorday_trades_df', 'Prior Day Trades'),
                                   ('all_trades_wtd_df', 'WTD Trades (10d)')]:
                    df = raw.get(key, pd.DataFrame())
                    formatted = format_trade_df(df)
                    blotters[key] = {
                        'title': label,
                        'columns': build_trade_columns(formatted),
                        'data': formatted.to_dict('records') if not formatted.empty else [],
                    }
                blotters = sanitize(blotters)

            live_spot_ref, live_vol_ref = '', ''
            live_mve, live_vega, live_pnl = 0, 0, 0
            if agg_ready and plot_data:
                live_spot_ref = plot_data.get('live_spot_ref', '')
                live_vol_ref = plot_data.get('live_vol_ref', '')
                live_mve = plot_data.get('live_mve', 0)
                live_vega = plot_data.get('live_vega', 0)
                live_pnl = plot_data.get('live_pnl', 0)

            payload = {
                'blotters': blotters,
                'live_px': live_px, 'live_bps': live_bps,
                'show_px': show_px, 'show_bps': show_bps,
                'live_spot_ref': live_spot_ref, 'live_vol_ref': live_vol_ref,
                'live_mve': live_mve, 'live_vega': live_vega, 'live_pnl': live_pnl,
                'still_loading': not agg_ready or early_fallback,
                'override_text': self._build_override_text(ldr, product, ref_px, ref_bps, vol_shift),
            }
            return owner.finish(payload) if owner is not None else json_result(payload)
        except Exception:
            if owner is not None:
                owner.fail()
            raise

    def _act_render_positions(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']
        ref_px, ref_bps = b.get('ref_px'), b.get('ref_bps')
        vol_shift = b.get('vol_shift_bps')
        has_overrides = ref_px is not None or ref_bps is not None or vol_shift is not None
        if not has_overrides and not b.get('_internal'):
            with self._tab_cache_lock:
                cached = (self._tab_cache.get(product) or {}).get('positions')
            if cached:
                self._push_tab_data(product)
                return json_result(cached)
            self._push_tab_data(product)

        ref_spot, show_px, show_bps = self._resolve_overrides(ldr, product, ref_px, ref_bps)
        live_px, live_bps = ldr.get_live_spot(product)

        ticker = product.rsplit('_', 2)[0] if '_' in product else product
        posdf = ldr.cdx_opt_pos_df
        if not posdf.empty and 'ticker' in posdf.columns:
            posdf = posdf[posdf['ticker'] == ticker].copy()
        from dash_reports.cdx_options.data.positions import build_position_greeks_data
        pos_data = build_position_greeks_data(posdf, product)

        from dash_reports.cdx_options.data import charts
        is_px = pos_data.get('is_px_index', False)
        aligned_scenario_key = 'greeks_by_price' if is_px else 'greeks_by_spread'
        aligned_scenario_col = 'spot_price_scenario' if is_px else 'spot_spread_scenario'
        scenario_df = pos_data.get(aligned_scenario_key, pd.DataFrame())
        strike_df = pos_data.get('notional_by_strike', pd.DataFrame())
        shared_range = None
        candidates = []
        if not scenario_df.empty and aligned_scenario_col in scenario_df.columns:
            candidates.append(scenario_df[aligned_scenario_col])
        if not strike_df.empty and 'strike_price' in strike_df.columns:
            candidates.append(strike_df['strike_price'])
        if candidates:
            vs = pd.concat(candidates).dropna().astype(float)
            if not vs.empty:
                pad = max((vs.max() - vs.min()) * 0.02, 0.5)
                shared_range = [float(vs.min() - pad), float(vs.max() + pad)]

        figures = []
        for key, title in [
            ('greeks_by_spread', 'Greeks by Spot Spread Scenario'),
            ('greeks_by_price', 'Greeks by Spot Price Scenario'),
            ('notional_by_strike', 'Notional by Strike'),
        ]:
            df = pos_data.get(key, pd.DataFrame())
            if df.empty:
                figures.append({'key': key, 'title': title, 'empty': True})
                continue
            x_range = shared_range if key in (aligned_scenario_key, 'notional_by_strike') else None
            fig = charts.build_multi_gby_figure(
                df.set_index(df.columns[:2].tolist()) if len(df.columns) > 2 else df,
                pos_data.get('vol_ref_spot_spread'),
                pos_data.get('vol_ref_spot_price'),
                is_px,
                max_columns=2, ref_spot=ref_spot, x_range=x_range,
            )
            figures.append({
                'key': key, 'title': title,
                'figure': json.loads(fig.to_json()),
            })

        live_spot_ref, live_vol_ref = ldr._get_live_refs(ldr.curr_mark, product)

        return json_result({
            'figures': figures,
            'live_px': live_px, 'live_bps': live_bps,
            'show_px': show_px, 'show_bps': show_bps,
            'live_spot_ref': live_spot_ref, 'live_vol_ref': live_vol_ref,
            'live_mve': 0, 'live_vega': 0,
        })

    def _act_render_expiry_charts(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']
        if hasattr(ldr, 'ensure_hist_vols_current') and ldr.ensure_hist_vols_current(product=product):
            self._mark_tab_caches_stale([product], reason='hist_vols_update')
        ref_px = b.get('ref_px')
        ref_bps = b.get('ref_bps')
        has_overrides = ref_px is not None or ref_bps is not None
        if not has_overrides and not b.get('_internal'):
            with self._tab_cache_lock:
                hc = self._heavy_tab_cache.get(product) or {}
                cached = hc.get('expiry_charts')
                cache_key = hc.get('_key')
            req_key = self._selection_key(b.get('strike'), b.get('expiry'))
            if cached and cache_key == req_key:
                self._push_tab_data(product)
                self._refresh_live_product_agg_if_stale(ldr, product)
                return json_result(cached)
            self._push_tab_data(product)

        from dash_reports.cdx_options.data import charts

        if ref_px is not None or ref_bps is not None:
            logging.info(f'[expiry_charts] {product}: override ref_px={ref_px} ref_bps={ref_bps}')

        product_title = ldr.get_product_title(product)
        plot_cols = ldr.plot_cols

        raw_expiry = b.get('expiry')
        raw_strike = b.get('strike')
        expiry_list = raw_expiry if isinstance(raw_expiry, list) else ([raw_expiry] if raw_expiry else [])
        if not expiry_list:
            expiry_list = [str(e) for e in ldr.get_available_expiries(product)]
        selected_expiries = [str(e) for e in expiry_list]

        strike_list = raw_strike if isinstance(raw_strike, list) else ([raw_strike] if raw_strike is not None else [])
        if not strike_list:
            strikes = ldr.get_available_strikes(product)
            strike_list = [strikes[len(strikes) // 2]] if strikes else [100]

        agg = ldr.product_aggs.get(product)
        ref_spot, show_px, show_bps = self._resolve_overrides(ldr, product, ref_px, ref_bps)
        if ref_px is not None or ref_bps is not None:
            live_bps = show_bps
        else:
            _, live_bps = ldr.get_live_spot(product)
        grid = ldr.pricing_grids.get(product)
        if live_bps is None and grid and hasattr(grid, 'price_to_spread'):
            live_px, _ = ldr.get_live_spot(product)
            if live_px is not None:
                spread_dec = grid.price_to_spread(live_px)
                if spread_dec is not None and not pd.isna(spread_dec):
                    live_bps = spread_dec * 1e4

        live_fwd_bps = {}
        _, orig_bps = ldr.get_live_spot(product)
        for expiry in selected_expiries:
            expiry_fwd = ldr.get_live_fwd_bps(product, expiry=expiry)
            if expiry_fwd and live_bps and (ref_px is not None or ref_bps is not None):
                if orig_bps and orig_bps > 0:
                    expiry_fwd = expiry_fwd * (live_bps / orig_bps)
            live_fwd_bps[str(pd.Timestamp(expiry).normalize().date())] = expiry_fwd

        exp_fig = charts.build_exp_vols_figure(
            None, ldr.sender_mark, ldr.curr_mark,
            product, selected_expiries, plot_cols,
            agg=agg, live_spot_bps=live_bps, live_fwd_bps=live_fwd_bps, grid=grid,
        )

        chart_sets = []
        spot_ts_json = None
        frame_builder = getattr(ldr, 'build_expiry_chart_frames', None) if ref_spot is None and not b.get('vol_shift_bps') else None
        for expiry in selected_expiries:
            for strike in strike_list:
                if frame_builder:
                    plot_data = frame_builder(product, strike, expiry)
                else:
                    plot_data = ldr.build_plot_dfs(
                        product, strike, expiry,
                        ref_spot=ref_spot, vol_shift_bps=b.get('vol_shift_bps'),
                        vol_lookback=b.get('vol_lookback', '5b'),
                        skip_positions=True,
                    )
                if not _expiry_chart_frames_ready(plot_data):
                    continue
                label = f'{expiry} K={strike}' if len(strike_list) > 1 else str(expiry)
                hist_fig, spot_ts_fig = charts.build_vol_hist_figure_split(
                    f'{product_title} - Vol History ({label})',
                    plot_data['vol_ts_df'], plot_cols,
                    plot_data['curr_vol_df'], plot_data['sender_vol_df'], plot_data['trades_df']
                )
                if spot_ts_json is None and spot_ts_fig is not None:
                    spot_ts_json = json.loads(spot_ts_fig.to_json())
                chart_sets.append({
                    'expiry': str(expiry),
                    'strike': strike,
                    'vol_hist_figure': json.loads(hist_fig.to_json()) if hist_fig else None,
                })

        logging.info(
            f'[expiry_charts] {product}: {len(chart_sets)} chart sets '
            f'for {len(selected_expiries)} expiries x {len(strike_list)} strikes '
            f'via={"loaded_frames" if frame_builder else "plot_dfs"}'
        )
        result = {
            'exp_vols_figure': json.loads(exp_fig.to_json()),
            'spot_ts_figure': spot_ts_json,
            'charts': chart_sets,
        }
        if not has_overrides and not b.get('_internal'):
            req_key = self._selection_key(b.get('strike'), b.get('expiry'))
            with self._tab_cache_lock:
                hc = dict(self._heavy_tab_cache.get(product) or {})
                hc['expiry_charts'] = result
                hc['_key'] = req_key
                self._heavy_tab_cache[product] = hc
        if not b.get('_internal'):
            self._refresh_live_product_agg_if_stale(ldr, product)
        return json_result(result)

    def _act_render_option_stack(self, b):
        ldr = self._loader
        if not ldr:
            return error_result('no_loader')
        product = b['product']
        ref_px = b.get('ref_px')
        ref_bps = b.get('ref_bps')
        has_overrides = ref_px is not None or ref_bps is not None
        if not has_overrides and not b.get('_internal'):
            with self._tab_cache_lock:
                hc = self._heavy_tab_cache.get(product) or {}
                cached = hc.get('option_stack')
                cache_key = hc.get('_key')
            req_key = self._selection_key(b.get('strike'), b.get('expiry'))
            if cached and cache_key == req_key:
                self._push_tab_data(product)
                self._refresh_live_product_agg_if_stale(ldr, product)
                return json_result(cached)
            self._push_tab_data(product)

        agg = ldr.product_aggs.get(product)
        if agg is None:
            agg = ldr.ensure_product_agg(product)
        if agg is None:
            logging.warning(f'[option_stack] {product}: no product_agg available')
            return json_result({'stacks': []})

        from dash_reports.cdx_options.data import charts
        raw_strikes = b.get('strike')
        raw_expiries = b.get('expiry')

        strikes = raw_strikes if isinstance(raw_strikes, list) else ([raw_strikes] if raw_strikes is not None else [None])
        expiries = raw_expiries if isinstance(raw_expiries, list) else ([raw_expiries] if raw_expiries is not None else [None])
        if not strikes:
            strikes = [None]
        if not expiries:
            expiries = [None]

        grid = ldr.pricing_grids.get(product)

        if ref_px is not None or ref_bps is not None:
            live_px, live_spot_bps = ldr.convert_spot(product, ref_px=ref_px, ref_bps=ref_bps)
        else:
            live_px, live_spot_bps = ldr.get_live_spot(product)

        if live_spot_bps is None and live_px is not None and grid and grid.spot_measures:
            spread_dec = grid.price_to_spread(live_px)
            if spread_dec is not None and not pd.isna(spread_dec):
                live_spot_bps = spread_dec * 1e4

        _, orig_spot_bps = ldr.get_live_spot(product)

        stacks = []
        for exp in expiries:
            live_fwd_bps = ldr.get_live_fwd_bps(product, expiry=exp)
            if live_fwd_bps and live_spot_bps and has_overrides:
                if orig_spot_bps and orig_spot_bps > 0:
                    live_fwd_bps = live_fwd_bps * (live_spot_bps / orig_spot_bps)
            for stk in strikes:
                ladder_df = charts.build_option_ladder(agg, product, expiry=exp, strike=stk,
                                                       grid=grid, live_spot_bps=live_spot_bps,
                                                       live_fwd_bps=live_fwd_bps)
                if ladder_df.empty:
                    continue
                stacks.append({
                    'records': sanitize(ladder_df.to_dict('records')),
                    'columns': list(ladder_df.columns),
                    'product': product, 'expiry': exp, 'strike': stk,
                })
        logging.info(f'[option_stack] {product}: {len(stacks)} stacks for {len(expiries)} expiries x {len(strikes)} strikes')
        result = {
            'stacks': stacks,
            'override_text': self._build_override_text(ldr, product, ref_px, ref_bps, b.get('vol_shift_bps')),
        }
        if not has_overrides and not b.get('_internal'):
            req_key = self._selection_key(b.get('strike'), b.get('expiry'))
            with self._tab_cache_lock:
                hc = dict(self._heavy_tab_cache.get(product) or {})
                hc['option_stack'] = result
                hc['_key'] = req_key
                self._heavy_tab_cache[product] = hc
        if not b.get('_internal'):
            self._refresh_live_product_agg_if_stale(ldr, product)
        return json_result(result)

    def _act_render_rv_chart(self, b):
        ldr = self._loader
        if not ldr:
            return json_result({'figure': None, 'error': 'Load data first'})
        try:
            leg1 = parse_rv_option(b.get('opt1'))
            leg2 = parse_rv_option(b['opt2'], 'Leg 2') if b.get('opt2') is not None else None
        except ValueError as exc:
            return json_result({'figure': None, 'error': str(exc)})
        opt1 = (leg1['product'], leg1['tenor'], leg1['strike'])
        opt2 = (leg2['product'], leg2['tenor'], leg2['strike']) if leg2 else None
        if leg2 is None or leg1['strike_type'] == leg2['strike_type']:
            rv_data = ldr.get_rv_data(opt1, opt2, strike_type=leg1['strike_type'])
        else:
            left = ldr.get_rv_data(opt1, strike_type=leg1['strike_type'])
            right = ldr.get_rv_data(opt2, strike_type=leg2['strike_type'])
            if left.empty or right.empty:
                return json_result({'figure': None, 'error': 'No volatility history for one of the selected legs.'})
            rv_data = left.join(right, how='outer', lsuffix='_1', rsuffix='_2').sort_index().ffill()
            rv_data['vol_mid_ann_diff'] = rv_data['vol_mid_ann_1'] - rv_data['vol_mid_ann_2']

        from dash_reports.cdx_options.data import charts
        def leg_title(leg):
            unit = {'delta': 'Δ', 'price_strike': 'Px', 'spread_strike': 'bps'}[leg['strike_type']]
            return f"{leg['product']} · {leg['tenor']} · {unit} {leg['strike']:g}"

        labels = {'vol_mid_ann': leg_title(leg1), 'vol_mid_ann_1': leg_title(leg1)}
        if leg2:
            labels['vol_mid_ann_2'] = leg_title(leg2)
        fig = charts.build_rv_figure(rv_data, 'Relative volatility' if leg2 else 'Volatility history', labels)
        fig.update_layout(uirevision=json.dumps([leg1, leg2]))
        return json_result({'figure': json.loads(fig.to_json())})

    # ── Load orchestration ──────────────────────────────────────

    @staticmethod
    def _defaults(ldr, product, strike, expiry):
        if isinstance(strike, tuple):
            strike = list(strike)
        if isinstance(expiry, tuple):
            expiry = list(expiry)
        if isinstance(strike, list):
            strike = [s for s in strike if s not in (None, '')] or None
        if isinstance(expiry, list):
            expiry = [e for e in expiry if e not in (None, '')] or None
        if expiry is not None:
            expiry = CDXOptionsFlightServer._snap_expiry_to_grid(ldr, product, expiry)
        if strike is None:
            strikes = ldr.get_available_strikes(product)
            strike = strikes[len(strikes) // 2] if strikes else 100
        else:
            strike = CDXOptionsFlightServer._snap_strike_to_grid(ldr, product, strike)
        if expiry is None:
            expiries = ldr.get_available_expiries(product)
            expiry = str(expiries[0]) if expiries else None
        return strike, expiry

    @staticmethod
    def _snap_strike_to_grid(ldr, product, strike):
        universe = getattr(ldr, '_grid_option_universe', None)
        if universe is None or strike is None:
            return strike
        grid_strikes = sorted({float(s) for s in (universe(product)[1] or set())})
        if not grid_strikes:
            return strike
        scalar = not isinstance(strike, list)
        values = [strike] if scalar else list(strike)
        snapped = []
        for value in values:
            try:
                want = float(value)
            except (TypeError, ValueError):
                snapped.append(value)
                continue
            if want in grid_strikes:
                snapped.append(value)
                continue
            nearest = min(grid_strikes, key=lambda s: (abs(s - want), s))
            grid_value = next(s for s in (universe(product)[1] or set()) if float(s) == nearest)
            logging.info(f'snap_strike_to_grid: {product} {value} not priced, using {grid_value}')
            snapped.append(grid_value)
        return snapped[0] if scalar else snapped

    @staticmethod
    def _snap_expiry_to_grid(ldr, product, expiry):
        universe = getattr(ldr, '_grid_option_universe', None)
        if universe is None or expiry is None:
            return expiry
        grid_dates = sorted({(d.date() if hasattr(d, 'date') else d) for d in (universe(product)[0] or set())})
        if not grid_dates:
            return expiry
        scalar = not isinstance(expiry, list)
        values = [expiry] if scalar else list(expiry)
        snapped = []
        for value in values:
            want = pd.to_datetime(value, errors='coerce')
            if pd.isna(want) or want.date() in grid_dates:
                snapped.append(value)
                continue
            nearest = min(grid_dates, key=lambda d: (abs(d - want.date()), d))
            grid_value = next(d for d in (universe(product)[0] or set())
                              if (d.date() if hasattr(d, 'date') else d) == nearest)
            logging.info(f'snap_expiry_to_grid: {product} {value} not priced, using {grid_value}')
            snapped.append(grid_value)
        return snapped[0] if scalar else snapped

    @staticmethod
    def _selection_values(value, numeric=False):
        if value is None:
            return []
        items = list(value) if isinstance(value, tuple) else value
        items = items if isinstance(items, list) else [items]
        out = []
        for item in items:
            if item is None or item == '':
                continue
            if numeric:
                try:
                    out.append(float(item))
                    continue
                except (TypeError, ValueError):
                    pass
            out.append(str(item))
        return out

    @classmethod
    def _selection_key(cls, strike, expiry):
        return (
            cls._selection_values(strike, numeric=True),
            cls._selection_values(expiry),
        )

    @staticmethod
    def _resolve_overrides(ldr, product, ref_px, ref_bps):
        ref_spot = show_px = show_bps = None
        if ref_px is not None or ref_bps is not None:
            show_px, show_bps = ldr.convert_spot(product, ref_px=ref_px, ref_bps=ref_bps)
            from sdr.dtccpy.credit_utils import _is_px_index
            ref_spot = show_px if _is_px_index(product) else show_bps
        return ref_spot, show_px, show_bps

    @classmethod
    def _build_override_text(cls, ldr, product, ref_px, ref_bps, vol_shift_bps):
        parts = []
        if ref_px is not None or ref_bps is not None:
            _, show_px, show_bps = cls._resolve_overrides(ldr, product, ref_px, ref_bps)
            px_s = f'{show_px:.2f}' if show_px is not None else '\u2014'
            bps_s = f'{show_bps:.2f}' if show_bps is not None else '\u2014'
            parts.append(f'REF: {px_s} px / {bps_s} bps')
        if vol_shift_bps:
            parts.append(f'Vol Bump: {vol_shift_bps:+.0f} bps')
        return ' | '.join(parts)

    def _wire_events(self, loader):
        bus = _EventSink(self)
        loader.progress = LoadProgress(event_bus=bus)
        loader.activity_log = ActivityLog(event_bus=bus)

    def _start_load(self, body, is_refresh):
        t0 = time.time()
        with self._lock:
            if is_refresh:
                if self._is_refreshing():
                    return json_result({'status': 'already_refreshing'})
            else:
                if self._is_loading():
                    return json_result({'status': 'already_loading'})

            todt = pd.to_datetime(body['todt']).date() if body.get('todt') else None

            existing = self._loader
            new_mark_time = body.get('mark_time', '')
            new_thresh_time = body.get('thresh_time', '4h')
            if (not is_refresh
                    and existing is not None
                    and existing._hist_loaded
                    and existing.tickers == body.get('tickers')
                    and str(existing.todt) == str(todt)
                    and getattr(existing, 'mark_time_override', '') == new_mark_time
                    and getattr(existing, 'thresh_time_override', '4h') == new_thresh_time):
                self._wire_events(existing)
                for k in list(existing.progress.data_ready.keys()):
                    existing.progress.mark_ready(k)
                for prod in list(existing.product_aggs.keys()) + list(existing.pricing_grids.keys()):
                    existing.progress.products_ready.add(prod)

                def _signal():
                    time.sleep(0.5)
                    existing.progress.advance('done')
                    self._schedule_refresh()

                threading.Thread(target=_signal, daemon=True).start()
                logging.info(f'_start_load: REUSED in {time.time()-t0:.1f}s -- products_ready={list(existing.progress.products_ready)[:3]}...')
                return json_result({'status': 'reused'})

            if not is_refresh or self._loader is None:
                self._loader = CDXOptionsDataLoader(
                    tickers=body.get('tickers'), todt=todt,
                    lookback=body.get('lookback', '6m'),
                )
            self._loader.mark_pos = body.get('mark_pos', True)
            self._loader.mark_time_override = body.get('mark_time', '')
            self._loader.thresh_time_override = body.get('thresh_time', '4h')
            self._loader.priority_product = (self._defaults_cache or {}).get('active_product')
            self._wire_events(self._loader)
            self._activate_materialized_cache(self._loader)

            snap = getattr(self._loader, '_snapshot_loaded', False)
            mark_time_explicit = bool(new_mark_time and ':' in new_mark_time)
            remark_only = (is_refresh
                           and mark_time_explicit
                           and not self._loader.quotes_df.empty)
            if mark_time_explicit and (snap or remark_only):
                logging.info(f'_start_load: mark_time={new_mark_time} explicit -- '
                             f'clearing agg/plot/tab caches (remark={remark_only})')
                self._loader.product_aggs.clear()
                with self._loader._plot_cache_lock:
                    self._loader._plot_cache.clear()
                self._clear_tab_caches()
                snap = False
            if snap:
                ldr_ref = self._loader
                for prod in list(ldr_ref.product_aggs.keys()) + list(ldr_ref.pricing_grids.keys()):
                    ldr_ref.progress.products_ready.add(prod)
                for k in list(ldr_ref.progress.data_ready.keys()):
                    ldr_ref.progress.mark_ready(k)
                ldr_ref.progress.snap_ready = True
                self._update_defaults_cache()
                cm_prods = sorted(ldr_ref.curr_mark['product'].unique().tolist()) if not ldr_ref.curr_mark.empty else []
                agg_prods = sorted(ldr_ref.product_aggs.keys()) if ldr_ref.product_aggs else []
                grid_prods = sorted(ldr_ref.pricing_grids.keys()) if ldr_ref.pricing_grids else []
                logging.info(f'_start_load: snapshot state -- '
                             f'curr_mark={len(cm_prods)} {cm_prods}, '
                             f'product_aggs={len(agg_prods)} {agg_prods}, '
                             f'grids={len(grid_prods)} {grid_prods}, '
                             f'products_ready={ldr_ref.progress.products_ready}')

                def _snap_done():
                    time.sleep(0.5)
                    ldr_ref.progress.advance('done')
                    self._background_prewarm()

                threading.Thread(target=_snap_done, daemon=True).start()

            thread = threading.Thread(target=self._run_load, args=(is_refresh, snap, remark_only), daemon=True)
            if is_refresh:
                self._refresh_thread = thread
            else:
                self._load_thread = thread
            thread.start()
        self._push_status_info_event(force=True)
        status = 'remark' if remark_only else ('started_with_snapshot' if snap else 'started')
        thread_type = 'remark' if remark_only else ('refresh' if is_refresh else 'load')
        logging.info(f'_start_load: {thread_type} thread launched in {time.time()-t0:.1f}s (snap={snap})')
        return json_result({'status': status})

    def _on_product_ready(self, product):
        self._mark_tab_caches_stale([product], reason='product_ready')
        self._update_defaults_cache()
        self._push_tab_data(product)
        self._trigger_override_refreshes(reason='product_ready', product_filter=product)

    def _run_deferred_missing_grid_refresh(self, ldr, on_ready):
        t0 = time.time()
        missing_before = (
            ldr.get_missing_grid_products()
            if hasattr(ldr, 'get_missing_grid_products')
            else []
        )
        cm_before = sorted(ldr.curr_mark['product'].unique().tolist()) if not ldr.curr_mark.empty else []
        logging.info(
            f'Deferred: starting missing-grid refresh from loaded state | '
            f'missing={missing_before} | curr_mark_before={cm_before}'
        )
        ldr.activity_log.log(f'Deferred missing-grid refresh: {missing_before}', 'compute')
        result = ldr.repair_missing_grids()
        changed = set(result.get('attempted', [])) | set(result.get('repaired', []))
        if changed and hasattr(ldr, '_bump_plot_versions'):
            ldr._bump_plot_versions(products=changed, include_snapshot=False)
        if result.get('repaired') and hasattr(ldr, '_clear_missing_grid_tracking'):
            ldr._clear_missing_grid_tracking(result['repaired'])
        cm_after = sorted(ldr.curr_mark['product'].unique().tolist()) if not ldr.curr_mark.empty else []
        grid_set = sorted(ldr.pricing_grids.keys()) if ldr.pricing_grids else []
        missing_after = (
            ldr.get_missing_grid_products()
            if hasattr(ldr, 'get_missing_grid_products')
            else []
        )
        repaired = sorted(set(missing_before) - set(missing_after))
        logging.info(
            f'Deferred: missing-grid refresh complete | repaired={repaired} | '
            f'changed={sorted(changed)} | curr_mark={cm_after} | grids={grid_set} | '
            f'elapsed={time.time()-t0:.1f}s'
        )
        if missing_after:
            logging.warning(f'Deferred: POST-REFRESH GAPS: {missing_after} have marks but no grid')
        for product in repaired:
            on_ready(product)
        if hasattr(ldr, '_maybe_save_snapshot'):
            ldr._maybe_save_snapshot()
        changed_products = repaired or sorted(changed)
        self._mark_tab_caches_stale(changed_products, reason='grid_rebuild')
        self._publish_data_generation(changed_products, reason='grid_rebuild')
        self._push_all_tab_data(changed_products, exclude_subscribed=True)

    def _run_load(self, is_refresh, skip_full_load=False, remark_only=False):
        ldr = self._loader
        if ldr is None:
            return
        on_ready = lambda product: self._on_product_ready(product)
        generation_published = False
        try:
            if remark_only:
                logging.info(f'_run_load: REMARK path — mark_time={ldr.mark_time_override}, '
                             f'thresh={ldr.thresh_time_override}, quotes_df={len(ldr.quotes_df):,}')
                ldr.remark_with_progress(on_product_ready=on_ready)
            elif skip_full_load:
                cm_set = set(ldr.curr_mark['product'].unique()) if not ldr.curr_mark.empty else set()
                grid_set = set(ldr.pricing_grids.keys()) if ldr.pricing_grids else set()
                missing_grids = ldr.get_missing_grid_products() if hasattr(ldr, 'get_missing_grid_products') else sorted(cm_set - grid_set)
                needs_rebuild = bool(missing_grids)
                if needs_rebuild:
                    logging.warning(f'Snapshot incomplete: {sorted(missing_grids)} have marks but no usable grid. '
                                    f'Will run deferred full rebuild.')
                else:
                    logging.info(f'Snapshot OK: curr_mark={sorted(cm_set)}, grids={sorted(grid_set)}. '
                                 f'Light refresh only (use UI to trigger full rebuild if needed).')

                logging.info('Snapshot loaded - running light refresh for immediate UI')
                t_spot = time.time()
                result = ldr.refresh_spot_only(publish_light_done=True)
                logging.info(f'Snapshot live spot refresh complete in {time.time()-t_spot:.1f}s')
                changed = result.get('changed_products', []) if isinstance(result, dict) else []
                self._mark_tab_caches_stale(changed or None, reason='startup_spot_refresh')
                self._publish_data_generation(changed, reason='startup_spot_refresh', spot_only=True)
                generation_published = True

                def _deferred_background(needs_rebuild):
                    try:
                        time.sleep(self._STARTUP_DEFER_S)
                        self._ensure_hist_fresh(ldr)
                        if needs_rebuild:
                            self._run_deferred_missing_grid_refresh(ldr, on_ready)
                        else:
                            self._queue_post_startup_refresh(ldr)
                        self._update_defaults_cache()
                        if needs_rebuild:
                            self._background_prewarm()
                    except Exception:
                        logging.exception('Deferred background thread DIED')
                threading.Thread(target=_deferred_background, args=(needs_rebuild,), daemon=True).start()
            elif is_refresh:
                ldr.refresh_with_progress(on_product_ready=on_ready)
            else:
                ldr.load_all_with_progress(on_product_ready=on_ready)
            logging.info('Load complete')
            self._last_reprice_at = time.time()
            products = ldr.get_available_products(priority=self._selected_product_hint(ldr))
            self._update_defaults_cache()
            if not generation_published:
                self._mark_tab_caches_stale(products, reason='load_complete')
                self._publish_data_generation(products, reason='load_complete')
                self._push_all_tab_data(products, exclude_subscribed=True)
        except Exception:
            logging.exception('Load thread crashed')
            ldr.progress.set_error('Load thread crashed -- check logs')
        self._push_status_info_event(force=True)
        self._schedule_refresh()
        gc.collect()
        self._maybe_start_pending_hist_refresh()

    def _run_staged_rebuild(self, current, staged):
        try:
            staged.load_all_with_progress()
            if not self._can_swap_loader(current, staged):
                raise RuntimeError('staged rebuild incomplete; keeping current loader live')
            self._commit_loader(staged)
            products = sorted(self._curr_mark_products(staged))
            self._publish_data_generation(products, reason='staged_rebuild')
            self._push_all_tab_data(products, exclude_subscribed=True)
            logging.info(f'Staged rebuild swap complete: products={products}')
            self._background_prewarm()
        except Exception:
            logging.exception('Staged rebuild failed; keeping current loader')
            staged.progress.set_error('Rebuild failed; keeping current live snapshot')
            if current is not None:
                self._push_event('activity', _activity_payload(current))
        self._push_status_info_event(force=True)
        self._schedule_refresh()
        gc.collect()
        self._maybe_start_pending_hist_refresh()

    def _run_staged_rebuild_from_current(self, current, settings):
        staged = None
        try:
            staged = CDXOptionsDataLoader(
                tickers=settings['tickers'],
                todt=settings['todt'],
                lookback='6m',
            )
            staged.mark_pos = settings['mark_pos']
            staged.mark_time_override = settings['mark_time_override']
            staged.thresh_time_override = settings['thresh_time_override']
            staged._force_grid_rebuild = True
            self._wire_events(staged)
            self._run_staged_rebuild(current, staged)
        except Exception:
            logging.exception('Staged rebuild setup failed; keeping current loader')
            if staged is not None:
                staged.progress.set_error('Rebuild failed; keeping current live snapshot')
            elif current is not None:
                current.progress.set_error('Rebuild failed before staged loader was ready')
                self._push_event('activity', _activity_payload(current))
            self._push_status_info_event(force=True)
            self._schedule_refresh()

    def _background_prewarm(self):
        ldr = self._loader
        if ldr is None:
            return
        def _prewarm():
            try:
                logging.info('Background prewarm starting...')
                ldr.queue_prewarm_plot_cache()
                logging.info('Background prewarm complete')
            except Exception:
                logging.exception('Background prewarm failed')
        threading.Thread(target=_prewarm, daemon=True).start()

    @staticmethod
    def _empty_defaults_payload():
        return {
            'product_options': [],
            'active_product': None,
            'strike_options': [],
            'strike': None,
            'expiry_options': [],
            'expiry': None,
            'ready_products': [],
        }

    @staticmethod
    def _build_product_options(products, ready_products):
        ready_set = set(ready_products)
        return [
            {
                'label': product,
                'value': product,
                **({'disabled': True} if product not in ready_set else {}),
            }
            for product in products
        ]

    def _resolve_active_product(self, ldr, products, ready_products, active_product=None):
        if not products:
            return None
        ready_set = set(ready_products)
        if active_product:
            if active_product in ready_set:
                return active_product
            preferred = ldr._find_default_otr_product(active_product, universe=products)
            if preferred and preferred in ready_set:
                return preferred
        for product in products:
            if product in ready_set:
                return product
        return None

    def _build_defaults_payload(self, ldr, active_product=None, strike=None, expiry=None):
        if ldr is None:
            return self._empty_defaults_payload()
        prio = active_product or getattr(ldr, 'priority_product', None)
        products = ldr.get_session_product_universe(priority=prio)
        ready_products = ldr.get_available_products(priority=prio)
        if not products:
            products = ready_products
        active = self._resolve_active_product(ldr, products, ready_products, active_product=active_product)
        strikes = []
        expiries = []
        use_strike = None
        use_expiry = None
        if active:
            expiries = [str(e) for e in ldr.get_available_expiries(active)]
            strikes = ldr.get_available_strikes(active)
            if expiry is not None and expiry not in expiries:
                prefix = str(expiry)[:10]
                expiry = next((e for e in expiries if str(e)[:10] == prefix), None)
            use_expiry = expiry if expiry is not None else (expiries[0] if expiries else None)
            use_expiry = self._snap_expiry_to_grid(ldr, active, use_expiry)
            if strike is not None and strike not in strikes:
                try:
                    strike = next((s for s in strikes if float(s) == float(strike)), None)
                except (TypeError, ValueError):
                    strike = None
            use_strike = strike if strike is not None else (strikes[len(strikes) // 2] if strikes else None)
            use_strike = self._snap_strike_to_grid(ldr, active, use_strike)
        return {
            'product_options': self._build_product_options(products, ready_products),
            'active_product': active,
            'strike_options': [{'label': str(s), 'value': s} for s in strikes],
            'strike': use_strike,
            'expiry_options': [{'label': e, 'value': e} for e in expiries],
            'expiry': use_expiry,
            'ready_products': ready_products,
        }

    def _update_defaults_cache(self):
        ldr = self._loader
        if ldr is None:
            return
        try:
            current_active = self._selected_product_hint(ldr)
            payload = self._build_defaults_payload(ldr, active_product=current_active)
            self._defaults_cache = payload
            self._products_cache = [opt['value'] for opt in payload.get('product_options', [])]
            active = payload.get('active_product')
            if active:
                ldr.priority_product = active
            logging.info(
                'Defaults cache updated: %s products %s | ready=%s active=%s',
                len(self._products_cache),
                self._products_cache,
                payload.get('ready_products', []),
                active,
            )
            self._push_defaults_event()
            self._push_status_info_event()
        except Exception:
            logging.exception('Defaults cache update failed')

    @staticmethod
    def _defaults_event_payload(payload):
        return {k: v for k, v in (payload or {}).items() if k not in ('strike', 'expiry')}

    def _push_defaults_event(self, force=False):
        payload = self._defaults_event_payload(self._defaults_cache)
        sig = json.dumps(payload, default=str, sort_keys=True)
        if not force and sig == self._last_defaults_event_sig:
            return False
        self._last_defaults_event_sig = sig
        self._push_event('defaults', payload)
        return True

    def _status_info_payload(self):
        ldr = self._loader
        if ldr is None or not hasattr(ldr, 'get_status_info'):
            return None
        payload = dict(ldr.get_status_info() or {})
        progress = getattr(ldr, 'progress', None)
        loading = self._is_loading()
        refreshing = self._is_refreshing()
        rebuilding = self._is_rebuilding()
        light_refreshing = self._is_light_refreshing_thread() or getattr(ldr, '_is_light_refreshing', False)
        background_repricing = self._is_background_repricing_thread()
        background_hist_refresh = self._is_background_hist_refresh_thread()
        payload.update({
            'loading': loading,
            'refreshing': refreshing,
            'rebuilding': rebuilding,
            'light_refreshing': light_refreshing,
            'background_repricing': background_repricing,
            'background_hist_refresh': background_hist_refresh,
            'progress_step': getattr(progress, 'step', ''),
            'progress_label': getattr(progress, 'step_label', ''),
            'progress_done': bool(getattr(progress, 'done', False)) if progress is not None else True,
            'progress_error': getattr(progress, 'error', None),
        })
        phase = 'live'
        if rebuilding:
            phase = 'rebuilding'
        elif refreshing:
            phase = 'remarking' if getattr(ldr, 'mark_time_override', '') else 'refreshing'
        elif loading:
            phase = 'loading'
        elif light_refreshing:
            phase = 'updating'
        elif background_repricing:
            phase = 'repricing'
        payload['status_phase'] = phase
        if phase == 'remarking' and getattr(ldr, 'mark_time_override', ''):
            payload['status_text'] = f'Re-marking @ {ldr.mark_time_override}'
        elif phase == 'repricing':
            payload['status_text'] = 'Spots updated; repricing grids in background'
        elif phase != 'live' and payload.get('progress_label'):
            payload['status_text'] = payload.get('progress_label')
        else:
            payload['status_text'] = ''
        return payload

    def _push_status_info_event(self, force=False):
        payload = self._status_info_payload()
        if payload is None:
            return False
        sig = json.dumps(payload, default=str, sort_keys=True)
        if not force and sig == self._last_status_info_event_sig:
            return False
        self._last_status_info_event_sig = sig
        self._push_event('status_info', payload)
        return True

    # ── Auto-refresh scheduler ──────────────────────────────────

    def _schedule_refresh(self):
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self._refresh_timer = threading.Timer(self._refresh_interval, self._auto_refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _can_light_refresh(self, ldr):
        return (not ldr.curr_mark.empty
                and not self._is_light_refreshing_thread()
                and not ldr._is_light_refreshing)

    _REPRICE_HEARTBEAT_S = 300
    _REPRICE_SPOT_THRESHOLD_BPS = 1.0
    _STARTUP_DEFER_S = 90

    def _reprice_needed(self, ldr, spot_products):
        if time.time() - getattr(self, '_last_reprice_at', 0) >= self._REPRICE_HEARTBEAT_S:
            return True
        last = getattr(self, '_last_reprice_spots', {})
        for product in spot_products or []:
            _, bps = ldr.get_live_spot(product)
            if bps is None:
                continue
            prev = last.get(product)
            if prev is None or abs(bps - prev) >= self._REPRICE_SPOT_THRESHOLD_BPS:
                return True
        return False

    def _note_reprice_started(self, ldr, spot_products):
        self._last_reprice_at = time.time()
        spots = dict(getattr(self, '_last_reprice_spots', {}))
        for product in spot_products or []:
            _, bps = ldr.get_live_spot(product)
            if bps is not None:
                spots[product] = bps
        self._last_reprice_spots = spots

    def _run_light_refresh_async(self, ldr, queue_heavy=True, reason='auto'):
        ldr._light_refresh_ready = False

        def _run():
            try:
                result = ldr.refresh_spot_only(publish_light_done=True)
                changed = result.get('changed_products', []) if isinstance(result, dict) else []
                spot_products = result.get('spot_products', []) if isinstance(result, dict) else []
                if changed:
                    self._mark_tab_caches_stale(changed, reason=f'{reason}_spot_refresh')
                    self._publish_data_generation(changed, reason=f'{reason}_spot_refresh', spot_only=True)
                if queue_heavy and not self._is_loading() and not self._is_refreshing() and not self._is_rebuilding():
                    if self._is_background_repricing_thread():
                        logging.info(f'{reason.capitalize()} spot refresh skipped staged repricer already in progress')
                    elif self._reprice_needed(ldr, spot_products):
                        self._note_reprice_started(ldr, spot_products)
                        state = self._start_background_reprice(ldr)
                        logging.info(f'{reason.capitalize()} spot refresh queued staged repricer: {state}')
                    else:
                        logging.info(f'{reason.capitalize()} spot refresh: no material spot move, reprice skipped')
                self._push_status_info_event(force=True)
            except Exception:
                logging.exception(f'{reason.capitalize()} light refresh failed')
                self._push_event('activity', _activity_payload(ldr))

        with self._lock:
            self._light_refresh_thread = threading.Thread(target=_run, daemon=True)
            self._light_refresh_thread.start()
        self._push_status_info_event(force=True)

    def _auto_refresh(self):
        ldr = self._loader
        if ldr is None:
            self._schedule_refresh()
            return

        if (not ldr.progress.done or self._is_loading() or self._is_refreshing()) and not ldr._is_light_refreshing:
            if ldr.progress.data_ready.get('trades'):
                try:
                    ldr._load_live_data()
                    logging.info('Background live data pull during full load')
                except Exception:
                    logging.exception('Background live data pull failed')
            if self._can_light_refresh(ldr):
                logging.info('Light refresh while load/rebuild in progress')
                self._run_light_refresh_async(ldr, queue_heavy=False, reason='load')
            self._schedule_refresh()
            return

        if self._is_light_refreshing_thread() or ldr._is_light_refreshing:
            self._schedule_refresh()
            return

        new_bday = dtutil.adjustdate(dtutil.today(), 'backward')
        if new_bday != ldr.todt and _dt.now(_NY).hour >= _ROLLOVER_HOUR:
            logging.info(f'NEW DAY: rolling {ldr.todt} -> {new_bday}')
            body = {
                'tickers': ldr.tickers, 'todt': str(new_bday),
                'lookback': '6m', 'mark_pos': ldr.mark_pos,
                'mark_time': getattr(ldr, 'mark_time_override', ''),
                'thresh_time': getattr(ldr, 'thresh_time_override', '4h'),
            }
            self._start_load(body, is_refresh=False)
            return

        if getattr(ldr, '_grids_carried_forward', False) and not ldr.quotes_df.empty:
            carried = set(ldr.pricing_grids.keys())
            quoted = set(ldr.quotes_df['product'].unique())
            if carried & quoted:
                logging.info(f'Carry-forward products now have quotes {sorted(carried & quoted)} -- triggering rebuild')
                ldr._force_grid_rebuild = True
                ldr._grids_carried_forward = False

        if ldr.should_rebuild_grid():
            self._wire_events(ldr)
            with self._lock:
                self._load_thread = threading.Thread(target=self._run_load, args=(True,), daemon=True)
                self._load_thread.start()
            return

        self._schedule_refresh()
        self._run_light_refresh_async(ldr, queue_heavy=True, reason='auto')


    def _extract_json(self, result):
        return json.loads(result.body.to_pybytes())

    def _compute_all_tabs(self, product, strike=None, expiry=None,
                          ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b',
                          bridge_only=False, requested_tabs=None):
        started = time.time()
        ldr = self._loader
        if not ldr:
            return {}
        raw_strike, raw_expiry = strike, expiry
        strike, expiry = self._defaults(ldr, product, strike, expiry)
        b = {'product': product, 'strike': strike, 'expiry': expiry,
             'ref_px': ref_px, 'ref_bps': ref_bps,
             'vol_shift_bps': vol_shift_bps, 'vol_lookback': vol_lookback}

        has_overrides = (
            ref_px is not None
            or ref_bps is not None
            or vol_shift_bps is not None
            or (vol_lookback and vol_lookback != '5b')
        )
        is_bridge = raw_strike is None and raw_expiry is None

        heavy_strike = raw_strike or strike
        heavy_expiry = raw_expiry or expiry
        if is_bridge and not bridge_only:
            with self._tab_cache_lock:
                hk = (self._heavy_tab_cache.get(product) or {}).get('_key')
            if hk:
                heavy_strike, heavy_expiry = hk
        b_multi = {**b, 'strike': heavy_strike, 'expiry': heavy_expiry}
        sel_strike = raw_strike if raw_strike is not None else heavy_strike
        sel_expiry = raw_expiry if raw_expiry is not None else heavy_expiry
        calc_context = self._build_calc_context(
            ldr, product, sel_strike, sel_expiry,
            ref_px=ref_px, ref_bps=ref_bps,
            vol_shift_bps=vol_shift_bps, vol_lookback=vol_lookback,
        )

        allowed_tabs = self._BRIDGE_TABS | self._HEAVY_TABS
        requested_set = set(requested_tabs or ())
        requested_set &= allowed_tabs
        if not requested_set:
            requested_set = set(self._BRIDGE_TABS if bridge_only else allowed_tabs)
        compute_key = (
            product,
            self._key_value(strike, numeric=True),
            self._key_value(expiry, timestamp=True),
            str(ref_px),
            str(ref_bps),
            str(vol_shift_bps),
            str(vol_lookback),
            tuple(sorted(requested_set)),
        )
        with self._tab_cache_lock:
            evt = self._tab_compute_inflight.get(compute_key)
            if evt is not None:
                inflight_owner = False
            else:
                evt = threading.Event()
                self._tab_compute_inflight[compute_key] = evt
                inflight_owner = True

        if not inflight_owner:
            logging.info(f'_compute_all_tabs: waiting for inflight {product}')
            evt.wait(timeout=180)
            with self._tab_cache_lock:
                cached = copy.deepcopy(
                    self._get_selection_view_locked(product, sel_strike, sel_expiry) or {}
                )
            result = {
                'product': product,
                'strike': sel_strike,
                'expiry': sel_expiry,
                **{key: cached[key] for key in requested_set if key in cached},
            }
            if ldr:
                result['defaults'] = self._defaults_cache
                result['update_text'] = ldr.get_update_text()
                result['grid_badge'] = ldr.get_grid_badge_text()
                result['override_text'] = ''
                result['calc_context'] = calc_context
                result['_calc_sig'] = calc_context['calc_sig']
            return result

        cached = {}
        if not has_overrides:
            with self._tab_cache_lock:
                candidate = copy.deepcopy(
                    self._get_selection_view_locked(product, sel_strike, sel_expiry) or {}
                )
            stale_tabs = set(candidate.get('_stale_tabs') or ())
            cached = {
                key: value
                for key, value in candidate.items()
                if key in requested_set and key not in stale_tabs and not candidate.get('_stale')
            }

        b['_internal'] = True
        b_multi['_internal'] = True
        tabs = [
            ('vol_grids', self._act_render_vol_grids, b),
            ('trade_blotter', self._act_render_trade_blotter, b),
            ('positions', self._act_render_positions, b),
        ]
        if not bridge_only:
            tabs.extend([
                ('expiry_charts', self._act_render_expiry_charts, b_multi),
                ('option_stack', self._act_render_option_stack, b_multi),
            ])
        tabs = [spec for spec in tabs if spec[0] in requested_set]

        _TAB_LABELS = {
            'vol_grids': 'Vol Grids', 'trade_blotter': 'Blotter',
            'positions': 'Positions', 'expiry_charts': 'Expiry Charts',
            'option_stack': 'Option Stack',
        }

        payload = {
            'product': product,
            'strike': sel_strike,
            'expiry': sel_expiry,
            'calc_context': calc_context,
            '_calc_sig': calc_context['calc_sig'],
        }
        to_compute = []
        failed_keys = set()
        for key, handler, args in tabs:
            if key in self._BRIDGE_TABS and key in cached:
                payload[key] = cached[key]
            else:
                to_compute.append((key, handler, args))

        if to_compute:
            computing = [_TAB_LABELS.get(k, k) for k, _, _ in to_compute]
            status_meta = {'product': product, 'strike': sel_strike, 'expiry': sel_expiry}
            self._push_event('compute_status', {'text': f'\u2699 Computing {", ".join(computing)}...', **status_meta})
            with ThreadPoolExecutor(max_workers=len(to_compute)) as pool:
                futures = {pool.submit(handler, args): key for key, handler, args in to_compute}
                done_keys = []
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        payload[key] = self._extract_json(future.result())
                        if not payload[key] or (
                                isinstance(payload[key], dict) and payload[key].get('error')):
                            failed_keys.add(key)
                    except Exception:
                        logging.exception(f'_compute_all_tabs: {key} failed for {product}')
                        payload[key] = {}
                        failed_keys.add(key)
                    done_keys.append(_TAB_LABELS.get(key, key))
                    remaining = [l for l in computing if l not in done_keys]
                    if remaining:
                        self._push_event('compute_status', {
                            'text': f'\u2699 {", ".join(done_keys)} done \u2502 {", ".join(remaining)}...',
                            **status_meta,
                        })

        materialized_at = _dt.now(_NY).isoformat()
        cache_meta = {
            '_cache_state': 'refresh-failed' if failed_keys else 'fresh',
            '_stale': bool(failed_keys),
            '_stale_reason': f'failed_tabs:{",".join(sorted(failed_keys))}' if failed_keys else '',
            '_materialized_at': materialized_at,
            '_data_asof': str(getattr(ldr, 'todt', '')),
            '_generation': getattr(self, '_data_generation', 0),
            '_refresh_duration_ms': round((time.time() - started) * 1000),
        }

        if failed_keys:
            with self._tab_cache_lock:
                previous = copy.deepcopy(
                    self._get_selection_view_locked(product, sel_strike, sel_expiry) or {}
                )
                if not previous:
                    old_bridge = copy.deepcopy(self._tab_cache.get(product) or {})
                    old_heavy = copy.deepcopy(self._heavy_tab_cache.get(product) or {})
                    req_key = self._selection_key(sel_strike, sel_expiry)
                    if old_bridge.get('_bridge_key') == req_key:
                        previous.update({
                            key: value for key, value in old_bridge.items()
                            if key in self._BRIDGE_TABS
                        })
                    if old_heavy.get('_key') == req_key:
                        previous.update({
                            key: value for key, value in old_heavy.items()
                            if key in self._HEAVY_TABS
                        })
            for key in failed_keys:
                if previous.get(key):
                    payload[key] = previous[key]

        if not has_overrides and not failed_keys:
            with self._tab_cache_lock:
                entry = copy.deepcopy(
                    self._get_selection_view_locked(product, sel_strike, sel_expiry) or {}
                )
                for key in requested_set:
                    if key in payload:
                        entry[key] = payload[key]
                stale_tabs = set(entry.get('_stale_tabs') or ())
                stale_reason = entry.get('_stale_reason') or 'partially_refreshed'
                stale_tabs -= {key for key in requested_set if key in payload}
                entry.update(cache_meta)
                entry['_stale_tabs'] = sorted(stale_tabs)
                if stale_tabs:
                    entry['_cache_state'] = 'stale'
                    entry['_stale'] = True
                    entry['_stale_reason'] = stale_reason
                tab_generations = dict(entry.get('_tab_generations') or {})
                for key in requested_set:
                    if key in payload:
                        tab_generations[key] = cache_meta['_generation']
                entry['_tab_generations'] = tab_generations
                self._store_selection_view_locked(product, sel_strike, sel_expiry, entry)

                bridge_entry = {
                    key: copy.deepcopy(entry[key])
                    for key in self._BRIDGE_TABS
                    if key in entry
                }
                if bridge_entry:
                    bridge_entry['_bridge_key'] = self._selection_key(sel_strike, sel_expiry)
                    bridge_entry['_bridge_calc_sig'] = calc_context['calc_sig']
                    bridge_entry.update(cache_meta)
                    self._tab_cache[product] = bridge_entry
                heavy_entry = {
                    key: copy.deepcopy(entry[key])
                    for key in self._HEAVY_TABS
                    if key in entry
                }
                if heavy_entry:
                    heavy_entry['_key'] = self._selection_key(sel_strike, sel_expiry)
                    heavy_entry.update(cache_meta)
                    self._heavy_tab_cache[product] = heavy_entry
        elif bridge_only and not failed_keys:
            with self._tab_cache_lock:
                previous = copy.deepcopy(
                    self._get_selection_view_locked(product, sel_strike, sel_expiry) or {}
                )
            for key in self._HEAVY_TABS:
                if key in requested_set and key not in payload and previous.get(key):
                    payload[key] = previous[key]

        if cached:
            logging.info(f'_compute_all_tabs: {product} used cached bridge tabs ({list(cached.keys())})')

        payload['defaults'] = self._defaults_cache
        payload['update_text'] = ldr.get_update_text()
        payload['grid_badge'] = ldr.get_grid_badge_text()
        payload['override_text'] = self._build_override_text(ldr, product, ref_px, ref_bps, vol_shift_bps)
        payload.update(cache_meta)
        for key in ('vol_grids', 'trade_blotter', 'positions', 'expiry_charts', 'option_stack'):
            if isinstance(payload.get(key), dict):
                payload[key]['calc_context'] = calc_context
                payload[key]['_calc_sig'] = calc_context['calc_sig']

        with self._tab_cache_lock:
            self._tab_compute_inflight.pop(compute_key, None)
        evt.set()

        if not has_overrides and not failed_keys:
            self._persist_materialized_view(product)
        if requested_set & self._HEAVY_TABS:
            self._refresh_live_product_agg_if_stale(ldr, product)
        return sanitize(payload)

    def _trigger_override_refreshes(self, reason='vol_mark_update', product_filter=None):
        """Re-render override views after vol-mark-changing events.

        Spot-tick-only paths must NOT call this — bridge tab_data filter already
        skips override clients (which is what we want: never overwrite override view
        with live spot data). But when vols/curves change, override views must rebuild
        on the new surface AT their override spot.
        """
        try:
            from dash_reports.cdx_options.ws_events import trigger_override_recompute_for_active_clients
            trigger_override_recompute_for_active_clients(reason=reason, product_filter=product_filter)
        except Exception:
            logging.exception(f'_trigger_override_refreshes failed (reason={reason}, product={product_filter})')

    def _push_tab_data(self, product=None, strike=None, expiry=None, **kw):
        ldr = self._loader
        if not ldr:
            return
        if not product:
            self._update_defaults_cache()
            prio = self._selected_product_hint(ldr)
            products = ldr.get_available_products(priority=prio)
            if not products:
                return
            product_name = prio if prio in products else products[0]
            with self._tab_cache_lock:
                has_cached_view = bool(self._tab_cache.get(product_name) or self._heavy_tab_cache.get(product_name))
            with self._push_lock:
                prev = self._push_pending.get(product_name)
                self._push_pending[product_name] = (strike, expiry, kw)
                evt = self._tab_cache_ready.get(product_name)
                if evt is None:
                    evt = threading.Event()
                    self._tab_cache_ready[product_name] = evt
                if not has_cached_view:
                    evt.clear()
                if prev is not None:
                    logging.info(f'_push_tab_data: coalesced for {product_name}')
            logging.info(f'_push_tab_data: queued active product {product_name} of {len(products)} ready products')
            self._push_wake.set()
            return
        with self._tab_cache_lock:
            has_cached_view = bool(self._tab_cache.get(product) or self._heavy_tab_cache.get(product))
        with self._push_lock:
            prev = self._push_pending.get(product)
            self._push_pending[product] = (strike, expiry, kw)
            evt = self._tab_cache_ready.get(product)
            if evt is None:
                evt = threading.Event()
                self._tab_cache_ready[product] = evt
            if not has_cached_view:
                evt.clear()
            if prev is not None:
                logging.info(f'_push_tab_data: coalesced for {product}')
        self._push_wake.set()

    def _push_all_tab_data(self, products=None, exclude_subscribed=False):
        ldr = getattr(self, '_loader', None)
        if products is None and ldr is None:
            return []
        available = list(products) if products is not None else list(
            ldr.get_available_products(priority=self._selected_product_hint(ldr))
        )
        priority = self._selected_product_hint(ldr) if ldr is not None else None
        selections = {
            product: self._active_subscription_selection(product)
            for product in set(available)
        }
        ordered = sorted(
            selections,
            key=lambda product: (
                not bool(selections[product].get('product')),
                product != priority,
                product,
            ),
        )
        for product in ordered:
            selection = selections[product]
            if exclude_subscribed and selection.get('product'):
                continue
            requested_tabs = sorted(self._BRIDGE_TABS - {'positions'}) if exclude_subscribed else None
            self._push_tab_data(
                product,
                strike=selection.get('strike') or None,
                expiry=selection.get('expiry') or None,
                requested_tabs=requested_tabs,
            )
        return ordered

    def _publish_data_generation(self, products=None, reason='data_changed', changed_tabs=None, spot_only=False):
        self._data_generation = getattr(self, '_data_generation', 0) + 1
        payload = {
            'generation': self._data_generation,
            'products': sorted(set(products or [])),
            'reason': reason,
            'changed_tabs': list(changed_tabs or self._BRIDGE_TABS),
            'spot_only': bool(spot_only),
            'ts': _dt.now(_NY).isoformat(),
        }
        self._push_event('data_generation', payload)
        return payload

    def _push_loop(self):
        logging.info('PushWorker thread started')
        while True:
            self._push_wake.wait(timeout=60)
            self._push_wake.clear()
            while True:
                with self._push_lock:
                    if not self._push_pending:
                        break
                    active = self._selected_product_hint()
                    product = active if active and active in self._push_pending else next(iter(self._push_pending))
                    strike, expiry, kw = self._push_pending.pop(product)
                logging.info(f'PushWorker: computing {product}')
                try:
                    t0 = time.time()
                    kw = dict(kw or {})
                    bridge_only = kw.pop('bridge_only', True)
                    with stage_timer('push_compute', product=product):
                        data = self._compute_all_tabs(product, strike, expiry, bridge_only=bridge_only, **kw)
                    data['_source'] = 'bridge'
                    data['product'] = product
                    self._push_event('tab_data', data)
                    elapsed = time.time() - t0
                    logging.info(f'Pushed tab_data for {product} in {elapsed:.1f}s '
                                 f'(vg={bool(data.get("vol_grids", {}).get("grids"))})')
                except Exception:
                    logging.exception(f'_push_loop failed for {product}')
                finally:
                    evt = self._tab_cache_ready.get(product)
                    if evt is None:
                        evt = threading.Event()
                        self._tab_cache_ready[product] = evt
                    evt.set()

    def _act_get_cached_tabs(self, b):
        product = b.get('product')
        if not product:
            return json_result({})
        req_key = self._selection_key(b.get('strike'), b.get('expiry'))
        requested_tabs = set(b.get('requested_tabs') or ())
        requested_tabs &= self._BRIDGE_TABS | self._HEAVY_TABS
        with self._tab_cache_lock:
            selection = self._copy_cached_tabs(
                self._get_selection_view_locked(product, b.get('strike'), b.get('expiry')) or {},
                requested_tabs,
            )
            bridge = self._copy_cached_tabs(self._tab_cache.get(product, {}), requested_tabs)
            heavy = self._copy_cached_tabs(self._heavy_tab_cache.get(product, {}), requested_tabs)
        if selection:
            available_tabs = requested_tabs or (self._BRIDGE_TABS | self._HEAVY_TABS)
            selected_payload = {
                key: selection[key]
                for key in available_tabs
                if key in selection
            }
            if selected_payload:
                stale_tabs = set(selection.get('_stale_tabs') or ())
                requested_stale = bool(stale_tabs & set(selected_payload))
                selection['_stale'] = requested_stale
                selection['_cache_state'] = 'stale' if requested_stale else 'fresh'
                bridge = selection
                heavy = selection
        bridge_key = bridge.get('_bridge_key')
        heavy_key = heavy.get('_key')
        selection_hit = bool(selection)
        bridge_payload = {
            k: bridge[k] for k in self._BRIDGE_TABS
            if k in bridge
            and (not requested_tabs or k in requested_tabs)
            and (selection_hit or bridge_key == req_key)
        }
        heavy_payload = {
            k: heavy[k] for k in self._HEAVY_TABS
            if k in heavy
            and (not requested_tabs or k in requested_tabs)
            and (selection_hit or heavy_key == req_key)
        }
        if not bridge_payload and not heavy_payload:
            with self._tab_cache_lock:
                cache = self._selection_cache_locked()
                product_keys = [key for key in cache if key[0] == product]
                fallback = self._copy_cached_tabs(
                    cache.get(product_keys[-1]) if product_keys else {}, requested_tabs,
                )
            fallback_tabs = {
                key: fallback[key]
                for key in (requested_tabs or (self._BRIDGE_TABS | self._HEAVY_TABS))
                if key in fallback and key in self._BRIDGE_TABS | self._HEAVY_TABS
            }
            if not fallback_tabs:
                return json_result({})
            bridge = dict(fallback, _stale=True, _cache_state='stale',
                          _stale_reason='selection_changed')
            heavy = bridge
            bridge_payload = {k: v for k, v in fallback_tabs.items() if k in self._BRIDGE_TABS}
            heavy_payload = {k: v for k, v in fallback_tabs.items() if k in self._HEAVY_TABS}
        ldr = self._loader
        payload = {'product': product, **bridge_payload, **heavy_payload}
        source = bridge if bridge_payload else heavy
        payload.update({
            key: source.get(key)
            for key in (
                '_cache_state', '_stale', '_stale_reason', '_stale_at',
                '_materialized_at', '_data_asof', '_generation', '_refresh_duration_ms',
            )
            if source.get(key) is not None
        })
        if ldr:
            strike, expiry = b.get('strike'), b.get('expiry')
            calc_context = self._build_calc_context(ldr, product, strike, expiry)
            payload['defaults'] = self._defaults_cache
            payload['update_text'] = ldr.get_update_text()
            payload['grid_badge'] = ldr.get_grid_badge_text()
            payload['override_text'] = ''
            payload['strike'] = strike
            payload['expiry'] = expiry
            payload['calc_context'] = calc_context
            payload['_calc_sig'] = calc_context['calc_sig']
            for key in ('vol_grids', 'trade_blotter', 'positions', 'expiry_charts', 'option_stack'):
                if isinstance(payload.get(key), dict):
                    payload[key]['calc_context'] = calc_context
                    payload[key]['_calc_sig'] = calc_context['calc_sig']
        return json_result(sanitize(payload))

    def _act_render_all_tabs(self, b):
        product = b.get('product')
        if not product:
            return error_result('product required')

        has_overrides = (
            b.get('ref_px') is not None
            or b.get('ref_bps') is not None
            or b.get('vol_shift_bps') is not None
            or (b.get('vol_lookback') and b.get('vol_lookback') != '5b')
        )
        if has_overrides:
            logging.info(
                '[render_all_tabs] %s override ref_px=%s ref_bps=%s vol_shift=%s vol_lookback=%s strike=%s expiry=%s',
                product, b.get('ref_px'), b.get('ref_bps'), b.get('vol_shift_bps'),
                b.get('vol_lookback', '5b'), b.get('strike'), b.get('expiry'),
            )
        data = self._compute_all_tabs(
            product, b.get('strike'), b.get('expiry'),
            ref_px=b.get('ref_px'), ref_bps=b.get('ref_bps'),
            vol_shift_bps=b.get('vol_shift_bps'), vol_lookback=b.get('vol_lookback', '5b'),
            requested_tabs=b.get('requested_tabs'),
        )
        return json_result(data)


class _EventSink:
    def __init__(self, server):
        self._srv = server

    def broadcast(self, event_type, payload=None):
        self._srv._push_event(event_type, payload)


def _expiry_chart_frames_ready(plot_data):
    if not plot_data:
        return False
    for name in ('vol_ts_df', 'curr_vol_df', 'sender_vol_df', 'trades_df'):
        bucket = plot_data.get(name)
        if isinstance(bucket, dict) and any(
            isinstance(frame, pd.DataFrame) and not frame.empty for frame in bucket.values()
        ):
            return True
    return False


def _activity_payload(loader):
    if loader and hasattr(loader, 'activity_log') and loader.activity_log:
        return {'entries': loader.activity_log.get_entries(50)}
    return {'entries': []}
