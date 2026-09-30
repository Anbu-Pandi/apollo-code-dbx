import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, Optional

import pyarrow.flight as flight

from apo_config.config import required_config
from dash_reports.industry_tracker.constants import APP
from dash_reports.industry_tracker.data import data_loader
from dash_reports.industry_tracker.data.universe import get_industry, list_industries
from dash_reports.industry_tracker.html_render import render_report
from dash_reports.sub_servers.cdx_options_server.serialization import json_result, error_result
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_certs


class IndustryTrackerFlightServer(flight.FlightServerBase):
    _request_counter = 0
    _counter_lock = threading.Lock()

    def __init__(self, host: str = None, port: int = None, **kwargs):
        host = host or required_config(APP, "server.host")
        port = port or int(required_config(APP, "server.port"))
        ssl_ctx = load_tls_certs()
        if ssl_ctx:
            with open(ssl_ctx[0], 'rb') as f:
                tls_cert = f.read()
            with open(ssl_ctx[1], 'rb') as f:
                tls_key = f.read()
            super().__init__(flight.Location.for_grpc_tls(host, port),
                             tls_certificates=[(tls_cert, tls_key)], **kwargs)
            logging.info('Industry Tracker Flight TLS on %s:%s', host, port)
        else:
            super().__init__(flight.Location.for_grpc_tcp(host, port), **kwargs)
            logging.info('Industry Tracker Flight (no TLS) on %s:%s', host, port)

        self._host, self._port = host, port
        self._lock = threading.Lock()
        self._html_cache: Dict[str, str] = {}
        self._asof_cache: Dict[str, datetime] = {}
        self._refreshing = False
        self._refresh_thread: Optional[threading.Thread] = None
        self._events = []
        self._events_offset = 0
        self._events_lock = threading.Lock()

        self._autoload()

    def _autoload(self):
        threading.Thread(target=self._build_all_industries, daemon=True,
                         name='IndustryTrackerInitialBuild').start()

    def _build_all_industries(self):
        logging.info('Initial build of all industry HTML caches')
        for ind in list_industries():
            try:
                self._rebuild_industry_html(ind.key)
            except Exception:
                logging.exception('Failed to build initial HTML for %s', ind.key)

    def _rebuild_industry_html(self, industry_key: str) -> bool:
        ind = get_industry(industry_key)
        if ind is None:
            logging.warning('Unknown industry: %s', industry_key)
            return False
        bundle = data_loader.load_industry_bundle(industry_key)
        html_str = render_report(
            industry=ind,
            public_comps_df=bundle.get('public_comps'),
            lt_historical_df=bundle.get('lt_historical'),
            wwr_items=bundle.get('wwr', []),
            wwo_items=bundle.get('wwo', []),
            asof=bundle.get('asof'),
            credit_loans_df=bundle.get('credit_loans'),
            credit_bonds_df=bundle.get('credit_bonds'),
            earnings_calls_df=bundle.get('earnings_calls'),
            earnings_commentary=bundle.get('earnings_commentary'),
            earnings_eps_df=bundle.get('earnings_eps'),
        )
        with self._lock:
            self._html_cache[industry_key] = html_str
            self._asof_cache[industry_key] = bundle.get('asof') or datetime.now()
        logging.info('Industry HTML cache updated: %s (%d chars)', industry_key, len(html_str))
        if industry_key == 'consumer':
            try:
                from dash_reports.industry_tracker.html_render.argus_report import generate_consumer_argus_html
                generate_consumer_argus_html()
            except Exception:
                logging.exception('Failed to write Consumer_Tracker_Argus.html')
        self._push_event('industry_updated', {'industry': industry_key})
        return True

    def _push_event(self, event_type: str, payload: Optional[dict] = None):
        event = {'type': event_type, 'ts': datetime.now().isoformat(), **(payload or {})}
        with self._events_lock:
            self._events.append(event)
            if len(self._events) > 200:
                trim = len(self._events) - 200
                self._events = self._events[-200:]
                self._events_offset += trim

    def _drain_events(self, since: int = 0):
        with self._events_lock:
            abs_total = self._events_offset + len(self._events)
            if since < self._events_offset:
                since = self._events_offset
            if since > abs_total:
                since = abs_total
            idx = since - self._events_offset
            return list(self._events[idx:]), abs_total

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
            t.name = f'IT_{self._request_counter}'

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
            result = handler(body)
            if hasattr(result, '__next__'):
                yield from result
            else:
                yield result
        except Exception as e:
            logging.exception('Action %s failed', action.type)
            yield error_result(str(e))
        finally:
            elapsed = (time.time() - t0) * 1000
            if elapsed > 50:
                logging.info('[SERVER] %s took %.0fms', action.type, elapsed)

    def _act_health_check(self, b):
        return json_result({
            'success': True,
            'service': APP,
            'cached_industries': sorted(self._html_cache.keys()),
            'refreshing': self._refreshing,
        })

    def _act_get_industries(self, b):
        return json_result([
            {'key': ind.key, 'display_name': ind.display_name, 'subtitle': ind.subtitle}
            for ind in list_industries()
        ])

    def _act_get_html(self, b):
        industry_key = b.get('industry') or list_industries()[0].key
        if get_industry(industry_key) is None:
            return error_result(f'Unknown industry: {industry_key}')
        with self._lock:
            html_str = self._html_cache.get(industry_key)
            asof = self._asof_cache.get(industry_key)
        if html_str is None:
            self._rebuild_industry_html(industry_key)
            with self._lock:
                html_str = self._html_cache.get(industry_key)
                asof = self._asof_cache.get(industry_key)
        return json_result({
            'industry': industry_key,
            'html': html_str or '',
            'asof': asof.isoformat() if asof else None,
        })

    def _act_refresh(self, b):
        industries = b.get('industries')
        with self._lock:
            if self._refreshing:
                return json_result({'status': 'already_refreshing'})
            self._refreshing = True

        def _do_refresh():
            try:
                self._refresh_targets(industries)
            finally:
                with self._lock:
                    self._refreshing = False
                self._push_event('refresh_done', {})
                logging.info('Refresh complete')

        self._refresh_thread = threading.Thread(target=_do_refresh, daemon=True,
                                                name='IndustryTrackerRefresh')
        self._refresh_thread.start()
        return json_result({'status': 'started'})

    def _refresh_targets(self, industries=None):
        if industries:
            targets = [get_industry(k) for k in industries]
            targets = [t for t in targets if t is not None]
        else:
            targets = list_industries()
        logging.info('Refresh started for %d industries', len(targets))
        refreshed = []
        for ind in targets:
            try:
                data_loader.refresh_industry_data(ind)
                try:
                    data_loader.refresh_earnings_calls(ind)
                except Exception:
                    logging.exception('Earnings calls refresh failed for %s', ind.key)
                self._rebuild_industry_html(ind.key)
                refreshed.append(ind.key)
            except Exception:
                logging.exception('Refresh failed for %s', ind.key)
        return refreshed

    def _act_rebuild_html(self, b):
        industry_key = b.get('industry')
        if not industry_key:
            return error_result('industry required')
        ok = self._rebuild_industry_html(industry_key)
        return json_result({'status': 'ok' if ok else 'unknown_industry'})

    def _act_get_progress_events(self, b):
        events, cursor = self._drain_events(b.get('since', 0))
        return json_result({'events': events, 'cursor': cursor})
