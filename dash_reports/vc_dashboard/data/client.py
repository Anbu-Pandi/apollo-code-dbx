import logging

from dash_reports.vc_dashboard.data import queries

_SERVER = None
_CHECKED = False


def _server():
    """Return the vc_server Flight client, or None to use direct queries.

    The Flight sub-server is OPT-IN (``vc_server.enabled = true`` in app_config). By
    default we use direct queries + the local read cache: attempting a Flight
    connection to a sub-server that isn't running blocks the render callbacks (the
    dashboard would sit on a spinner with no data). Reads are already fast from the
    per-host cache, so the sub-server is an optional optimization, not a dependency.
    """
    global _SERVER, _CHECKED
    if _CHECKED:
        return _SERVER
    _CHECKED = True
    _SERVER = None
    try:
        from apo_config.config import config
        enabled = str(config.getconfig("vc_server", "enabled") or "false").lower() == "true"
    except Exception:
        enabled = False
    if not enabled:
        return None
    try:
        from dash_reports.sub_servers.vc_server.client import get_client
        client = get_client()
        client.health_check()
        _SERVER = client
    except Exception as exc:
        logging.info("vc_server unavailable, using direct queries: %s", exc)
        _SERVER = None
    return _SERVER


def get_summary(window_days=90, apollo_only=False, **filters):
    s = _server()
    if s:
        return s.get_summary(window_days, apollo_only, **filters)
    return queries.get_summary(window_days, apollo_only, **filters)


def get_deals(window_days=90, apollo_only=False, sector=None, limit=500, **filters):
    s = _server()
    if s:
        return s.get_deals(window_days, apollo_only, sector, limit, **filters).get('deals', [])
    return queries.get_deals(window_days, apollo_only, sector, limit, **filters)


def get_sector_heatmap(window_days=90, axis='round_type', apollo_only=False, metric='count', **filters):
    s = _server()
    if s:
        return s.get_sector_heatmap(window_days, axis, apollo_only, metric, **filters)
    return queries.get_sector_heatmap(window_days, axis, apollo_only, metric, **filters)


def get_deals_for_cell(sector, axis='round_type', bucket=None, window_days=90, apollo_only=False, **filters):
    s = _server()
    if s:
        return s.get_deals_for_cell(sector, axis, bucket, window_days, apollo_only, **filters).get('deals', [])
    return queries.get_deals_for_cell(sector, axis, bucket, window_days, apollo_only, **filters)


def get_investor_activity(window_days=90, apollo_only=False):
    s = _server()
    if s:
        return s.get_investor_activity(window_days, apollo_only).get("firms", [])
    return queries.get_investor_activity(window_days, apollo_only)


def get_activity(**selection):
    server = _server()
    return server.get_activity(**selection) if server else queries.get_activity(**selection)


def get_firm_drill(firm, window_days=90):
    s = _server()
    if s:
        return s.get_firm_drill(firm, window_days)
    return queries.get_firm_drill(firm, window_days)




def get_company_backers(window_days=90, min_size_usd=0):
    s = _server()
    if s:
        return s.get_company_backers(window_days, min_size_usd).get("deals", [])
    return queries.get_company_backers(window_days, min_size_usd)


def get_follow_candidates(window_days=90):
    s = _server()
    if s:
        return s.get_follow_candidates(window_days).get("candidates", [])
    return queries.get_follow_candidates(window_days)


def get_hot_companies(window_days=90, **filters):
    s = _server()
    if s:
        return s.get_hot_companies(window_days, **filters).get('companies', [])
    return queries.get_hot_companies(window_days, **filters)


def get_firm_deals(window_days=7, apollo_only=False, **filters):
    s = _server()
    if s:
        return s.get_firm_deals(window_days, apollo_only, **filters).get('deals', [])
    return queries.get_firm_deals(window_days, apollo_only, **filters)


def get_geo_breakdown(window_days=90, apollo_only=False, **filters):
    s = _server()
    if s:
        return s.get_geo_breakdown(window_days, apollo_only, **filters)
    return queries.get_geo_breakdown(window_days, apollo_only, **filters)


def list_subsectors():
    s = _server()
    if s:
        return s.list_subsectors().get("subsectors", [])
    return queries.list_subsectors()


def get_review_queue():
    s = _server()
    if s:
        return s.get_review_queue().get("deals", [])
    return queries.get_review_queue()


def confirm_subsector(deal_id, sub_sector, sector=None):
    s = _server()
    if s:
        return s.confirm_subsector(deal_id, sub_sector, sector)
    return queries.confirm_subsector(deal_id, sub_sector, sector)


def list_sources():
    s = _server()
    if s:
        return s.list_sources().get("sources", [])
    return queries.list_sources()


def upsert_source(row):
    s = _server()
    if s:
        return s.upsert_source(row)
    return queries.upsert_source(row)


def toggle_source(source_id, enabled):
    s = _server()
    if s:
        return s.toggle_source(source_id, enabled)
    return queries.toggle_source(source_id, enabled)


def delete_source(source_id):
    s = _server()
    if s:
        return s.delete_source(source_id)
    return queries.delete_source(source_id)
