import json
import threading

import pyarrow.flight as flight

from dash_reports.sub_servers.cdx_research_server.settings import endpoint, setting

_client = None
_lock = threading.Lock()


def get_client():
    global _client
    with _lock:
        if _client is None:
            _client = CDXResearchClient()
        return _client


class CDXResearchClient:
    def __init__(self, host=None, port=None, tls_root=None):
        host, port = endpoint(host, port, client=True)
        if host in ('0.0.0.0', '::'):
            from common.service_discovery import resolve_internal_service_host
            host = resolve_internal_service_host()
        directory = setting('certificate_dir')
        if tls_root is None and directory:
            from pathlib import Path
            tls_root = (Path(directory) / 'server.crt').read_bytes()
        if tls_root:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from flight_server.client_sdk.flight_transport import connect_flight
            certificate = x509.load_pem_x509_certificate(tls_root)
            server_name = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            self._client = connect_flight(f'grpc+tls://{host}:{port}', tls_root_certs=tls_root,
                                          tls_server_name=server_name)
        else:
            self._client = flight.FlightClient(f'grpc://{host}:{port}')

    def _call(self, action, body=None):
        response = list(self._client.do_action(
            flight.Action(action, json.dumps(body or {}).encode('utf-8')),
            options=flight.FlightCallOptions(timeout=float(setting('client.timeout_seconds', 10)))))
        if not response:
            raise RuntimeError('CDX Research returned no response')
        result = json.loads(response[0].body.to_pybytes())
        if result.get('success') is False:
            raise RuntimeError(result.get('error', 'CDX Research request failed'))
        return result

    def research_start(self, kind, params, force=False):
        return self._call('research_start', {'kind': kind, 'params': params, 'force': force})

    def research_poll(self, job_id, since=0):
        return self._call('research_poll', {'id': job_id, 'since': since})

    def health_check(self):
        return self._call('health_check')

    def market_snapshot(self):
        return self._call('market_snapshot')

    def close(self):
        self._client.close()
