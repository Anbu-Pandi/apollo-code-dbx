import json
import logging
import time

import pyarrow.flight as flight

from apo_config.config import required_config
from common.service_discovery import resolve_internal_service_host
from dash_reports.industry_tracker.constants import APP
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_root_cert

_client = None


def get_client():
    global _client
    if _client is None:
        _client = IndustryTrackerClient()
    return _client


class IndustryTrackerClient:
    def __init__(self, host: str = None, port: int = None):
        host = host or resolve_internal_service_host()
        port = port or int(required_config(APP, "server.port"))

        tls_root = load_tls_root_cert()
        if tls_root:
            self._client = flight.FlightClient(
                f'grpc+tls://{host}:{port}',
                tls_root_certs=tls_root,
                disable_server_verification=True,
            )
            logging.info('Industry Tracker Flight client TLS -> %s:%s', host, port)
        else:
            self._client = flight.FlightClient(f'grpc://{host}:{port}')
            logging.info('Industry Tracker Flight client -> %s:%s', host, port)

    def _call(self, action_type: str, body: dict = None, timeout: int = 60):
        t0 = time.time()
        raw = json.dumps(body or {}, default=str).encode('utf-8')
        action = flight.Action(action_type, raw)
        options = flight.FlightCallOptions(timeout=timeout)
        results = list(self._client.do_action(action, options=options))
        elapsed = (time.time() - t0) * 1000
        if elapsed > 100:
            logging.warning('[CLIENT] %s took %.0fms', action_type, elapsed)
        if not results:
            return None
        data = json.loads(results[0].body.to_pybytes())
        if isinstance(data, dict) and data.get('success') is False:
            raise RuntimeError(data.get('error', 'Industry Tracker server error'))
        return data

    def health_check(self):
        return self._call('health_check')

    def get_industries(self):
        return self._call('get_industries')

    def get_html(self, industry: str):
        return self._call('get_html', {'industry': industry})

    def refresh(self, industries=None):
        return self._call('refresh', {'industries': industries})

    def rebuild_html(self, industry: str):
        return self._call('rebuild_html', {'industry': industry})

    def get_progress_events(self, since: int = 0):
        return self._call('get_progress_events', {'since': since})
