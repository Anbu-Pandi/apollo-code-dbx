import json
import logging

import pyarrow.flight as flight

from apo_config.config import required_config
from common.service_discovery import resolve_internal_service_host
from dash_reports.sub_servers.flight_common import load_tls_root_cert
from dash_reports.sub_servers.vc_server import APP

_client = None


def get_client():
    global _client
    if _client is None:
        _client = VCFlightClient()
    return _client


class VCFlightClient:

    def __init__(self, host=None, port=None):
        host = host or resolve_internal_service_host()
        port = port or int(required_config(APP, "server.port"))
        tls_root = load_tls_root_cert(APP)
        if tls_root:
            self._client = flight.FlightClient(f"grpc+tls://{host}:{port}",
                                               tls_root_certs=tls_root,
                                               disable_server_verification=True)
        else:
            self._client = flight.FlightClient(f"grpc://{host}:{port}")
        logging.info("VC Flight client -> %s:%s", host, port)

    def _call(self, action_type, body=None, timeout=60):
        raw = json.dumps(body or {}, default=str).encode("utf-8")
        options = flight.FlightCallOptions(timeout=timeout)
        results = list(self._client.do_action(flight.Action(action_type, raw), options=options))
        if not results:
            return None
        data = json.loads(results[0].body.to_pybytes())
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(data.get("error", "vc server error"))
        return data

    def health_check(self):
        return self._call("health_check")

    def get_summary(self, window_days=90, apollo_only=False, **filters):
        return self._call('get_summary', {'window_days': window_days, 'apollo_only': apollo_only, **filters})

    def get_deals(self, window_days=90, apollo_only=False, sector=None, limit=500, **filters):
        return self._call('get_deals', {'window_days': window_days, 'apollo_only': apollo_only, 'sector': sector, 'limit': limit, **filters})

    def get_sector_heatmap(self, window_days=90, axis='round_type', apollo_only=False, metric='count', **filters):
        return self._call('get_sector_heatmap', {'window_days': window_days, 'axis': axis, 'apollo_only': apollo_only, 'metric': metric, **filters})

    def get_deals_for_cell(self, sector, axis='round_type', bucket=None, window_days=90, apollo_only=False, **filters):
        return self._call('get_deals_for_cell', {'sector': sector, 'axis': axis, 'bucket': bucket, 'window_days': window_days, 'apollo_only': apollo_only, **filters})

    def get_investor_activity(self, window_days=90, apollo_only=False):
        return self._call("get_investor_activity", {"window_days": window_days,
                                                    "apollo_only": apollo_only})

    def get_activity(self, **selection):
        return self._call("get_activity", selection)

    def get_firm_drill(self, firm, window_days=90):
        return self._call("get_firm_drill", {"firm": firm, "window_days": window_days})


    def get_company_backers(self, window_days=90, min_size_usd=0):
        return self._call("get_company_backers", {"window_days": window_days,
                                                  "min_size_usd": min_size_usd})

    def get_follow_candidates(self, window_days=90):
        return self._call("get_follow_candidates", {"window_days": window_days})

    def get_hot_companies(self, window_days=90, **filters):
        return self._call('get_hot_companies', {'window_days': window_days, **filters})

    def get_firm_deals(self, window_days=7, apollo_only=False, **filters):
        return self._call('get_firm_deals', {'window_days': window_days, 'apollo_only': apollo_only, **filters})

    def get_geo_breakdown(self, window_days=90, apollo_only=False, **filters):
        return self._call('get_geo_breakdown', {'window_days': window_days, 'apollo_only': apollo_only, **filters})

    def list_subsectors(self):
        return self._call("list_subsectors")

    def get_review_queue(self):
        return self._call("get_review_queue")

    def confirm_subsector(self, deal_id, sub_sector, sector=None):
        return self._call("confirm_subsector", {"deal_id": deal_id, "sub_sector": sub_sector,
                                                "sector": sector})

    def list_sources(self):
        return self._call("list_sources")

    def list_universe(self):
        return self._call("list_universe")

    def upsert_source(self, row):
        return self._call("upsert_source", {"row": row})

    def toggle_source(self, source_id, enabled):
        return self._call("toggle_source", {"source_id": source_id, "enabled": enabled})

    def delete_source(self, source_id):
        return self._call("delete_source", {"source_id": source_id})
