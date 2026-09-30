import json
import logging

import pyarrow.flight as flight

from common.utils.jobutil import JobExecutor
from dash_reports.sub_servers.flight_common import error_result, json_result, load_tls_certs
from dash_reports.sub_servers.vc_server import APP
from dash_reports.vc_dashboard.data import queries

FAST_ACTIONS = frozenset({
    "health_check", "get_summary", "list_sources", "list_universe",
    "toggle_source", "upsert_source", "delete_source",
    "list_subsectors", "confirm_subsector",
})


class VCFlightServer(flight.FlightServerBase):

    def __init__(self, host="0.0.0.0", port=None, **kwargs):
        tls = load_tls_certs(APP)
        if tls:
            crt, key = tls
            with open(crt, "rb") as fc, open(key, "rb") as fk:
                certs = [(fc.read(), fk.read())]
            location = flight.Location.for_grpc_tls(host, port)
            super().__init__(location, tls_certificates=certs, **kwargs)
        else:
            location = flight.Location.for_grpc_tcp(host, port)
            super().__init__(location, **kwargs)
        logging.info("VC Flight server on %s:%s (tls=%s)", host, port, bool(tls))
        self._executor = JobExecutor("thread", max_workers=6)

    def list_actions(self, context):
        for name in dir(self):
            if name.startswith("_act_"):
                yield flight.ActionType(name[len("_act_"):], "")

    def do_action(self, context, action):
        handler = getattr(self, f"_act_{action.type}", None)
        if handler is None:
            yield error_result(f"unknown action: {action.type}")
            return
        try:
            raw = action.body.to_pybytes() if action.body else b""
            body = json.loads(raw) if raw else {}
            if action.type in FAST_ACTIONS:
                result = handler(body)
            else:
                result = self._executor.submitjob(handler, [[body]], job_name=action.type)
                result = result[0] if isinstance(result, list) else result
            yield json_result(result)
        except Exception as exc:
            logging.exception("vc action %s failed", action.type)
            yield error_result(exc)

    def _act_health_check(self, b):
        return {"success": True, "app": APP}

    def _act_get_summary(self, b):
        return queries.get_summary(b.get('window_days', 90), b.get('apollo_only', False), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))

    def _act_get_deals(self, b):
        return {'deals': queries.get_deals(b.get('window_days', 90), b.get('apollo_only', False), b.get('sector'), b.get('limit', 500), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))}

    def _act_get_sector_heatmap(self, b):
        return queries.get_sector_heatmap(b.get('window_days', 90), b.get('axis', 'round_type'), b.get('apollo_only', False), b.get('metric', 'count'), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))

    def _act_get_deals_for_cell(self, b):
        return {'deals': queries.get_deals_for_cell(b.get('sector'), b.get('axis', 'round_type'), b.get('bucket'), b.get('window_days', 90), b.get('apollo_only', False), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))}

    def _act_get_investor_activity(self, b):
        return {"firms": queries.get_investor_activity(b.get("window_days", 90),
                                                       b.get("apollo_only", False))}

    def _act_get_activity(self, b):
        return queries.get_activity(**{key: b[key] for key in (
            "perspective", "window_days", "apollo_only", "search", "min_size_usd", "sort") if key in b})

    def _act_get_firm_drill(self, b):
        return queries.get_firm_drill(b.get("firm"), b.get("window_days", 90))


    def _act_get_company_backers(self, b):
        return {"deals": queries.get_company_backers(b.get("window_days", 90),
                                                     b.get("min_size_usd", 0))}

    def _act_get_follow_candidates(self, b):
        return {"candidates": queries.get_follow_candidates(b.get("window_days", 90))}

    def _act_get_hot_companies(self, b):
        return {'companies': queries.get_hot_companies(b.get('window_days', 90), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0), apollo_only=b.get('apollo_only', False))}

    def _act_get_firm_deals(self, b):
        return {'deals': queries.get_firm_deals(b.get('window_days', 7), b.get('apollo_only', False), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))}

    def _act_get_geo_breakdown(self, b):
        return queries.get_geo_breakdown(b.get('window_days', 90), b.get('apollo_only', False), search=b.get('search', ''), min_size_usd=b.get('min_size_usd', 0))

    def _act_list_subsectors(self, b):
        return {"subsectors": queries.list_subsectors()}

    def _act_get_review_queue(self, b):
        return {"deals": queries.get_review_queue()}

    def _act_confirm_subsector(self, b):
        return queries.confirm_subsector(b.get("deal_id"), b.get("sub_sector"),
                                         b.get("sector"))

    # News is served by the VC wiki reader (/vcnews), not this sub-server.

    def _act_list_sources(self, b):
        return {"sources": queries.list_sources()}

    def _act_list_universe(self, b):
        return {"universe": queries.list_universe()}

    def _act_upsert_source(self, b):
        return queries.upsert_source(b.get("row", {}))

    def _act_toggle_source(self, b):
        return queries.toggle_source(b.get("source_id"), b.get("enabled", True))

    def _act_delete_source(self, b):
        return queries.delete_source(b.get("source_id"))


def build_server(host=None, port=None):
    from apo_config.config import required_config
    host = host or required_config(APP, "server.host")
    port = port or int(required_config(APP, "server.port"))
    return VCFlightServer(host=host, port=port)
