import json
import logging
import time

import pyarrow.flight as flight

from apo_config.config import required_config
from common.service_discovery import resolve_internal_service_host
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_root_cert
from dash_reports.sub_servers.html_sub_server.constants import APP

_client = None


def get_client():
    global _client
    if _client is None:
        _client = HtmlSubServerClient()
    return _client


class HtmlSubServerClient:
    def __init__(self, host=None, port=None):
        host = host or resolve_internal_service_host()
        port = port or int(required_config(APP, "server.port"))

        tls_root = load_tls_root_cert(APP)
        if tls_root:
            self._client = flight.FlightClient(
                f'grpc+tls://{host}:{port}',
                tls_root_certs=tls_root,
                disable_server_verification=True,
            )
            logging.info('HTML sub-server Flight client TLS -> %s:%s', host, port)
        else:
            self._client = flight.FlightClient(f'grpc://{host}:{port}')
            logging.info('HTML sub-server Flight client -> %s:%s', host, port)

    def _call(self, action_type, body=None, timeout=30):
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
            raise RuntimeError(data.get('error', 'HTML sub-server error'))
        return data

    def health_check(self):
        return self._call('health_check')

    def get_html(self, html_path):
        return self._call('get_html', {'path': html_path})

    def get_path(self, html_path=''):
        return self._call('get_path', {'path': html_path})

    def list_html(self):
        return self._call('list_html')

    def close(self):
        self._client.close()
