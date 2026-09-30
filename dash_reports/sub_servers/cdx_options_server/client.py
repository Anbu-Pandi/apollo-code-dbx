import json
import logging

import pyarrow.flight as flight

from apo_config.config import required_config
from common.service_discovery import resolve_internal_service_host
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_root_cert

_client = None


def get_client():
    global _client
    if _client is None:
        _client = CDXFlightClient()
    return _client


class CDXFlightClient:

    def __init__(self, host=None, port=None):
        host = host or resolve_internal_service_host()
        port = port or int(required_config("cdx_options_server", "server.port"))

        tls_root = load_tls_root_cert()
        if tls_root:
            self._client = flight.FlightClient(
                f"grpc+tls://{host}:{port}",
                tls_root_certs=tls_root,
                disable_server_verification=True,
            )
            logging.info(f"CDX Flight client TLS -> {host}:{port}")
        else:
            self._client = flight.FlightClient(f"grpc://{host}:{port}")
            logging.info(f"CDX Flight client -> {host}:{port}")

    def _call(self, action_type, body=None, timeout=300):
        import time
        t0 = time.time()
        raw_body = json.dumps(body or {}, default=str).encode('utf-8')
        action = flight.Action(action_type, raw_body)
        options = flight.FlightCallOptions(timeout=timeout)
        payload = None
        for result in self._client.do_action(action, options=options):
            if payload is None:
                payload = result.body.to_pybytes()
        elapsed = (time.time() - t0) * 1000
        if elapsed > 100:
            logging.warning(f'[CLIENT] {action_type} took {elapsed:.0f}ms')
        if payload is None:
            return None
        data = json.loads(payload)
        del payload
        if isinstance(data, dict) and data.get('success') is False:
            raise RuntimeError(data.get('error', 'CDX server error'))
        return data

    # ── Lifecycle ───────────────────────────────────────────────

    def load(self, tickers=None, todt=None, lookback='6m', mark_pos=True, mark_time='', thresh_time='4h'):
        return self._call('load', {
            'tickers': tickers, 'todt': str(todt) if todt else None,
            'lookback': lookback, 'mark_pos': mark_pos, 'mark_time': mark_time,
            'thresh_time': thresh_time,
        })

    def refresh(self, mark_pos=True, mark_time='', thresh_time='4h'):
        return self._call('refresh', {'mark_pos': mark_pos, 'mark_time': mark_time, 'thresh_time': thresh_time})

    def rebuild_grids(self):
        return self._call('rebuild_grids')

    def set_refresh_interval(self, seconds):
        return self._call('set_refresh_interval', {'seconds': seconds})

    def start_manual_refresh(self):
        return self._call('start_manual_refresh')

    # ── Status ──────────────────────────────────────────────────

    def get_status(self):
        return self._call('get_status')

    def get_progress_events(self, since=0):
        return self._call('get_progress_events', {'since': since})

    def health_check(self):
        return self._call('health_check')

    # ── Metadata ────────────────────────────────────────────────

    def get_products(self):
        return self._call('get_products')

    def get_expiries(self, product):
        return self._call('get_expiries', {'product': product})

    def get_strikes(self, product):
        return self._call('get_strikes', {'product': product})

    def get_product_title(self, product):
        r = self._call('get_product_title', {'product': product})
        return r.get('title', product) if isinstance(r, dict) else product

    def get_live_spot(self, product):
        r = self._call('get_live_spot', {'product': product})
        return r.get('px'), r.get('bps')

    def get_spot_snapshot(self, products=None):
        return self._call('get_spot_snapshot', {'products': products})

    def convert_spot(self, product, ref_px=None, ref_bps=None):
        r = self._call('convert_spot', {
            'product': product, 'ref_px': ref_px, 'ref_bps': ref_bps,
        })
        return r.get('px'), r.get('bps')

    def get_delta_one_strip(self):
        return self._call('get_delta_one_strip')

    def get_update_text(self):
        r = self._call('get_update_text')
        return r.get('text', '') if isinstance(r, dict) else ''

    def get_grid_badge(self):
        r = self._call('get_grid_badge')
        return r.get('text', '') if isinstance(r, dict) else ''

    def get_status_info(self):
        return self._call('get_status_info')

    def get_activity(self):
        return self._call('get_activity')

    def pick_defaults(self, active_product=None, strike=None, expiry=None):
        return self._call('pick_defaults', {
            'active_product': active_product, 'strike': strike, 'expiry': expiry,
        })

    # ── Render data ─────────────────────────────────────────────

    def render_vol_grids(self, product, strike=None, expiry=None,
                         ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b',
                         suppress_push=False):
        return self._call('render_vol_grids', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'ref_px': ref_px, 'ref_bps': ref_bps, 'vol_shift_bps': vol_shift_bps,
            'vol_lookback': vol_lookback, '_internal': suppress_push,
        })

    def render_trade_blotter(self, product, strike=None, expiry=None,
                             ref_px=None, ref_bps=None, vol_shift_bps=None, suppress_push=False):
        return self._call('render_trade_blotter', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'ref_px': ref_px, 'ref_bps': ref_bps, 'vol_shift_bps': vol_shift_bps,
            '_internal': suppress_push,
        })

    def render_positions(self, product, strike=None, expiry=None,
                         ref_px=None, ref_bps=None, vol_shift_bps=None, suppress_push=False):
        return self._call('render_positions', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'ref_px': ref_px, 'ref_bps': ref_bps, 'vol_shift_bps': vol_shift_bps,
            '_internal': suppress_push,
        })

    def render_expiry_charts(self, product, strike=None, expiry=None,
                             ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b',
                             suppress_push=False):
        body = {'product': product, 'strike': strike, 'expiry': expiry, '_internal': suppress_push}
        if ref_px is not None:
            body['ref_px'] = ref_px
        if ref_bps is not None:
            body['ref_bps'] = ref_bps
        if vol_shift_bps is not None:
            body['vol_shift_bps'] = vol_shift_bps
        if vol_lookback:
            body['vol_lookback'] = vol_lookback
        return self._call('render_expiry_charts', body)

    def render_option_stack(self, product, strike=None, expiry=None, ref_px=None, ref_bps=None, suppress_push=False):
        body = {'product': product, 'strike': strike, 'expiry': expiry, '_internal': suppress_push}
        if ref_px is not None:
            body['ref_px'] = ref_px
        if ref_bps is not None:
            body['ref_bps'] = ref_bps
        return self._call('render_option_stack', body)

    def get_calc_context(self, product, strike=None, expiry=None,
                         ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b'):
        return self._call('get_calc_context', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'ref_px': ref_px, 'ref_bps': ref_bps,
            'vol_shift_bps': vol_shift_bps, 'vol_lookback': vol_lookback,
        }, timeout=15)

    def render_rv_chart(self, opt1, opt2=None):
        return self._call('render_rv_chart', {'opt1': opt1, 'opt2': opt2})

    def get_cached_tabs(self, product, strike=None, expiry=None, wait_s=0, requested_tabs=None):
        return self._call('get_cached_tabs', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'wait_s': wait_s, 'requested_tabs': requested_tabs,
        }, timeout=8)

    def render_all_tabs(self, product, strike=None, expiry=None,
                        ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b'):
        return self._call('render_all_tabs', {
            'product': product, 'strike': strike, 'expiry': expiry,
            'ref_px': ref_px, 'ref_bps': ref_bps,
            'vol_shift_bps': vol_shift_bps, 'vol_lookback': vol_lookback,
        }, timeout=180)

    def render_materialized_tabs(self, product, strike=None, expiry=None, requested_tabs=None,
                                 ref_px=None, ref_bps=None, vol_shift_bps=None, vol_lookback='5b'):
        return self._call('render_all_tabs', {
            'product': product,
            'strike': strike,
            'expiry': expiry,
            'requested_tabs': requested_tabs,
            'ref_px': ref_px,
            'ref_bps': ref_bps,
            'vol_shift_bps': vol_shift_bps,
            'vol_lookback': vol_lookback,
        }, timeout=180)

    def close(self):
        self._client.close()
