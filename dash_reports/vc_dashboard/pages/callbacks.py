import hashlib
from datetime import datetime

from dash import (ClientsideFunction, Input, Output, State, callback,
                  clientside_callback, ctx, html, no_update)

from dash_reports.vc_dashboard.data import client
from dash_reports.vc_dashboard.pages import formatting
from dash_reports.vc_dashboard.pages import vc_dashboard as page
from dash_reports.vc_dashboard.constants import (
    TAB_DEALS,
    TAB_FIRMS,
    TAB_FUNDING,
    TAB_GEO,
    TAB_NEWS,
    TAB_SOURCES,
)


@callback(Output('vc-email-subscribe', 'href'), Input('vc-search', 'value'), Input('vc-min-size', 'value'),
          Input(page.VC_WINDOW, 'value'), Input(page.VC_APOLLO, 'value'))
def email_subscription_link(search, minimum, window, apollo):
    from dash_reports.subscriptions.page import subscription_url
    return subscription_url('vc_deals', {'search': search or '', 'min_size_usd': minimum or 0,
                                       'window_days': int(window or 30), 'apollo_only': 'apollo' in (apollo or [])})

clientside_callback(ClientsideFunction(namespace='vcDashboard', function_name='syncTab'),
                    Output('vc-tab-url', 'data'), Input(page.VC_TABS, 'value'),
                    Input('vc-search', 'value'), Input('vc-min-size', 'value'),
                    Input(page.VC_WINDOW, 'value'), Input(page.VC_APOLLO, 'value'),
                    Input('vc-activity-sort', 'value'))
clientside_callback(ClientsideFunction(namespace='vcDashboard', function_name='navigateNews'),
                    Output('vc-news-navigation', 'data'), Input('vc-news-home', 'n_clicks'),
                    prevent_initial_call=True)


clientside_callback(ClientsideFunction(namespace='vcDashboard', function_name='loadNews'),
                    Output('vc-news-frame', 'src'), Input(page.VC_TABS, 'value'), State('vc-news-frame', 'src'))


def _apollo(values):
    return bool(values and "apollo" in values)


@callback(Output("vc-activity-title", "children"), Output("vc-activity-caption", "children"),
          Output("vc-activity-list", "children"), Input(page.VC_TABS, "value"),
          Input(page.VC_WINDOW, "value"), Input(page.VC_APOLLO, "value"),
          Input("vc-search", "value"), Input("vc-min-size", "value"), Input("vc-activity-sort", "value"))
def render_activity(tab, window, apollo, search, min_size, sort):
    if tab not in {"portfolio", "relationships"}:
        return no_update, no_update, no_update
    perspective = "company" if tab == "relationships" else "investor"
    rows = client.get_activity(perspective=perspective, window_days=window, apollo_only=_apollo(apollo),
                               search=search, min_size_usd=min_size, sort=sort)
    label = "Company activity" if perspective == "company" else "Investor activity"
    scope = "companies" if perspective == "company" else "followed investors"
    return label, f"{len(rows):,} {scope} · {window} days · same filters across both perspectives", formatting.activity(rows)


def _deal_rows(deals):
    return [{
        "deal_id": d.get("deal_id"),
        "company_pbid": d.get("company_pbid"),
        "deal_date": str(d.get("deal_date") or "")[:10],
        "company": d.get("company"),
        "round_type": d.get("round_type"),
        "deal_size": d.get("deal_size_usd"),
        "post_val": d.get("post_valuation_usd"),
        "sector": d.get("sector"),
        "themes": d.get("themes"),
        "region": d.get("region"),
        "lead_investors": d.get("lead_investors"),
        "investors": d.get("investors"),
        "deal_status": d.get("deal_status") or "Not provided",
        "description": d.get("description") or "No deal synopsis in the source export.",
        "source_observed_at": d.get("source_observed_at"),
    } for d in deals]


@callback(
    Output(page.VC_FUNDING_PANEL, "style"),
    Output(page.VC_FIRMS_PANEL, "style"),
    Output(page.VC_DEALS_PANEL, "style"),
    Output(page.VC_GEO_PANEL, "style"),
    Output(page.VC_NEWS_PANEL, "style"),
    Output(page.VC_SOURCES_PANEL, "style"),
    Output("vc-activity-panel", "style"),
    Input(page.VC_TABS, "value"),
)
def toggle_panels(tab):
    show, hide = {"display": "block"}, {"display": "none"}
    return (
        show if tab == TAB_FUNDING else hide,
        show if tab == TAB_FIRMS else hide,
        show if tab == TAB_DEALS else hide,
        show if tab == TAB_GEO else hide,
        show if tab == TAB_NEWS else hide,
        show if tab == TAB_SOURCES else hide,
        show if tab in {"portfolio", "relationships"} else hide,
    )


@callback(Output(page.VC_FUNDING_TABLE, 'rowData'), Output('vc-funding-caption', 'children'), Output('vc-funding-trend', 'figure'), Output('vc-sector-trend', 'figure'), Output('vc-latest-deal', 'children'), Output(page.VC_SUMMARY, 'children'), Output('vc-coverage', 'children'), Input(page.VC_WINDOW, 'value'), Input(page.VC_APOLLO, 'value'), Input('vc-search', 'value'), Input('vc-min-size', 'value'))
def render_funding(window, apollo, search, min_size):
    apollo = _apollo(apollo)
    deals = client.get_deals(window_days=window, apollo_only=apollo, limit=500, search=search, min_size_usd=min_size)
    rows = _deal_rows(deals)
    summary = client.get_summary(window_days=window, apollo_only=apollo, search=search, min_size_usd=min_size)
    capital, sectors = formatting.summary_charts(summary)
    caption = f'{len(rows):,} deals · USD · — not provided · select a row for details'
    freshness = 'Stale source' if summary.get('source_stale') else 'Source observed'
    coverage = f"{freshness}: {summary.get('source_as_of') or 'unknown'} · Deal-detail coverage: {summary.get('detail_companies', 0):,} companies · {summary.get('disclosed_count', 0):,}/{summary.get('deal_count', 0):,} deals have a completed deal value · Coverage spans deals with a followed firm among the participants (PitchBook)."
    return (rows, caption, capital, sectors, summary.get('latest_deal') or 'No data', formatting.summary_cards(summary), coverage)


@callback(Output(page.VC_FIRMS_FEED, 'children'), Input(page.VC_TABS, 'value'), Input(page.VC_WINDOW, 'value'), Input(page.VC_APOLLO, 'value'), Input('vc-search', 'value'), Input('vc-min-size', 'value'))
def render_firm_feed(tab, window, apollo, search, min_size):
    if tab != TAB_FIRMS:
        return no_update
    deals = client.get_firm_deals(window_days=window, apollo_only=_apollo(apollo), search=search, min_size_usd=min_size)
    return formatting.firm_deal_feed(deals)


@callback(Output('vc-hot-companies', 'children'), Input(page.VC_TABS, 'value'), Input(page.VC_WINDOW, 'value'), Input('vc-search', 'value'), Input('vc-min-size', 'value'), Input(page.VC_APOLLO, 'value'))
def render_hot_companies(tab, window, search, min_size, apollo):
    if tab != TAB_FIRMS:
        return no_update
    return formatting.hot_companies(client.get_hot_companies(window_days=window, search=search, min_size_usd=min_size, apollo_only=_apollo(apollo)))


@callback(Output(page.VC_GEO_CHART, 'figure'), Output(page.VC_GEO_TABLE, 'rowData'), Input(page.VC_TABS, 'value'), Input(page.VC_WINDOW, 'value'), Input(page.VC_GEO_METRIC, 'value'), Input(page.VC_APOLLO, 'value'), Input('vc-search', 'value'), Input('vc-min-size', 'value'))
def render_geo(tab, window, metric, apollo, search, min_size):
    if tab != TAB_GEO:
        return (no_update, no_update)
    geo = client.get_geo_breakdown(window_days=window, apollo_only=_apollo(apollo), search=search, min_size_usd=min_size)
    return (formatting.geo_chart(geo.get('countries', []), metric), geo.get('metros', []))


@callback(
    Output(page.VC_REVIEW_TABLE, "data"),
    Output(page.VC_REVIEW_TABLE, "dropdown"),
    Input(page.VC_TABS, "value"),
)
def load_review_queue(tab):
    if tab != TAB_DEALS:
        return no_update, no_update
    queue = client.get_review_queue()
    for row in queue:
        row["deal_date"] = str(row.get("deal_date") or "")[:10]
        row["description"] = (row.get("description") or "")[:220]
    options = [{"label": f"{s['sector']} · {s['sub_sector']}", "value": s["sub_sector"]}
               for s in client.list_subsectors()]
    return queue, {"sub_sector": {"options": options}}


@callback(
    Output(page.VC_REVIEW_STATUS, "children"),
    Output(page.VC_REVIEW_TABLE, "data", allow_duplicate=True),
    Input(page.VC_REVIEW_CONFIRM, "n_clicks"),
    State(page.VC_REVIEW_TABLE, "data"),
    prevent_initial_call=True,
)
def confirm_review_queue(n_clicks, rows):
    if not rows:
        return "Nothing to confirm.", no_update
    confirmed = 0
    for row in rows:
        result = client.confirm_subsector(row.get("deal_id"), row.get("sub_sector"))
        confirmed += 1 if result.get("ok") else 0
    remaining = client.get_review_queue()
    for row in remaining:
        row["deal_date"] = str(row.get("deal_date") or "")[:10]
        row["description"] = (row.get("description") or "")[:220]
    return f"Confirmed {confirmed} subsector label{'s' if confirmed != 1 else ''}.", remaining


@callback(Output("vc-deal-detail", "children"), Input(page.VC_FUNDING_TABLE, "cellClicked"),
          State(page.VC_FUNDING_TABLE, "rowData"), prevent_initial_call=True)
def render_deal_detail(event, rows):
    row = next((r for r in rows or [] if r["deal_id"] == (event or {}).get("rowId")), None)
    if row is None:
        return no_update
    return [html.H3(formatting.entity_link("company", row.get("company"))),
            formatting.entity_link("deal", row["deal_id"], "View exact deal →"),
            html.P(f"{row.get('round_type')} · {row.get('deal_status')} · {row.get('deal_date')}"),
            html.P(row.get("description")), html.P(f"Lead investors: {row.get('lead_investors') or 'Not provided by source'}"),
            html.P(f"Deal participants: {row.get('investors') or 'Not provided by source'}"),
            html.A("Open PitchBook company and deal history ↗",
                   href=f"https://my.pitchbook.com/profile/{row['company_pbid']}/company/profile",
                   target="_blank", rel="noopener noreferrer") if row.get('company_pbid') else None,
            html.Small(f"PitchBook deal {row['deal_id']} · source observed {row.get('source_observed_at') or 'not recorded'}")]


@callback(Output("vc-sector-deal-detail", "children"), Input(page.VC_DRILL_TABLE, "cellClicked"),
          State(page.VC_DRILL_TABLE, "rowData"), prevent_initial_call=True)
def render_sector_deal_detail(event, rows):
    return render_deal_detail(event, rows)


@callback(Output(page.VC_FUNDING_TABLE, "filterModel"), Input("vc-funding-trend", "clickData"),
          Input("vc-sector-trend", "clickData"), Input("vc-clear-chart", "n_clicks"),
          Input(page.VC_WINDOW, "value"), Input("vc-search", "value"), Input("vc-min-size", "value"), Input(page.VC_APOLLO, "value"),
          State(page.VC_FUNDING_TABLE, "filterModel"), prevent_initial_call=True)
def filter_chart_deals(time_click, sector_click, clear, window, search, minimum, apollo, filters):
    if ctx.triggered_id not in {"vc-funding-trend", "vc-sector-trend"}:
        return {}
    selection = time_click if ctx.triggered_id == "vc-funding-trend" else sector_click
    if not selection or not selection.get("points"):
        return no_update
    point = selection["points"][0]
    field, value = ("deal_date", str(point["x"])[:10]) if ctx.triggered_id == "vc-funding-trend" else ("sector", point["y"])
    return {**(filters or {}), field: {"filterType": "text", "type": "equals", "filter": value}}


@callback(Output("vc-geo-deals", "children"), Input(page.VC_GEO_CHART, "clickData"),
          Input(page.VC_GEO_TABLE, "cellClicked"), Input(page.VC_WINDOW, "value"),
          Input(page.VC_APOLLO, "value"), Input("vc-search", "value"), Input("vc-min-size", "value"),
          State(page.VC_GEO_TABLE, "rowData"), prevent_initial_call=True)
def drill_geography(chart, event, window, apollo, search, minimum, metros):
    if ctx.triggered_id not in {page.VC_GEO_CHART, page.VC_GEO_TABLE}:
        return "Select a country bar or metro row to inspect its deals."
    from dash_reports.vc_dashboard.data.queries import parse_geography
    if ctx.triggered_id == page.VC_GEO_CHART and chart and chart.get("points"):
        country, metro = chart["points"][0]["y"], None
    else:
        row = next((row for row in metros or [] if row["country"] + "|" + row["metro"] == (event or {}).get("rowId")), None)
        if row is None:
            return no_update
        country, metro = row["country"], row["metro"]
    deals = client.get_deals(window_days=window, apollo_only=_apollo(apollo), search=search,
                             min_size_usd=minimum, limit=10000)
    rows = [deal for deal in deals if (lambda location: location[0] == country and (metro is None or location[1] == metro))(
        parse_geography(deal.get("region")))]
    return [html.H3(f"{metro or country} · {len(rows):,} deals"), formatting.firm_deal_feed(rows, followed=False)]


@callback(Output(page.VC_HEATMAP_TABLE, 'columns'), Output(page.VC_HEATMAP_TABLE, 'data'), Output(page.VC_HEATMAP_TABLE, 'style_data_conditional'), Output(page.VC_HEATMAP_TABLE, 'active_cell'), Input(page.VC_WINDOW, 'value'), Input(page.VC_HEATMAP_AXIS, 'value'), Input(page.VC_HEATMAP_METRIC, 'value'), Input(page.VC_APOLLO, 'value'), Input('vc-search', 'value'), Input('vc-min-size', 'value'))
def render_heatmap(window, axis, metric, apollo, search, min_size):
    hm = client.get_sector_heatmap(window_days=window, axis=axis, apollo_only=_apollo(apollo), metric=metric, search=search, min_size_usd=min_size)
    return (*formatting.heatmap_table(hm), None)


@callback(Output(page.VC_DRILL_TITLE, 'children'), Output(page.VC_DRILL_TABLE, 'rowData'), Input(page.VC_HEATMAP_TABLE, 'active_cell'), State(page.VC_HEATMAP_TABLE, 'data'), State(page.VC_HEATMAP_AXIS, 'value'), State(page.VC_WINDOW, 'value'), State(page.VC_APOLLO, 'value'), State('vc-search', 'value'), State('vc-min-size', 'value'), prevent_initial_call=True)
def drilldown(active_cell, table_data, axis, window, apollo, search, min_size):
    if not active_cell or active_cell.get('column_id') == 'sector':
        return ('Select a cell to inspect its deals', [])
    sector = table_data[active_cell['row']]['sector']
    bucket = active_cell['column_id']
    deals = client.get_deals_for_cell(sector, axis=axis, bucket=bucket, window_days=window, apollo_only=_apollo(apollo), search=search, min_size_usd=min_size)
    title = f'{sector} · {bucket} — {len(deals)} deals'
    return (title, _deal_rows(deals))


@callback(
    Output(page.VC_SOURCES_TABLE, "data"),
    Input(page.VC_TABS, "value"),
)
def load_sources(tab):
    if tab != TAB_SOURCES:
        return no_update
    return client.list_sources()


@callback(
    Output(page.VC_SOURCES_STATUS, "children"),
    Output(page.VC_SOURCES_TABLE, "data", allow_duplicate=True),
    Input(page.VC_SOURCE_ADD_BTN, "n_clicks"),
    State(page.VC_SOURCE_NAME, "value"),
    State(page.VC_SOURCE_TYPE, "value"),
    State(page.VC_SOURCE_URL, "value"),
    State(page.VC_SOURCE_TRUST, "value"),
    prevent_initial_call=True,
)
def add_source(n_clicks, name, stype, url, trust):
    if not name or not url:
        return "Name and URL/handle are required.", no_update
    source_id = "src-" + hashlib.sha1(f"{name}|{url}".encode("utf-8")).hexdigest()[:12]
    client.upsert_source({
        "source_id": source_id, "name": name, "type": stype, "url_or_handle": url,
        "trust_weight": float(0.7 if trust is None else trust), "sector_hint": None, "enabled": True,
        "added_by": "ui", "added_at": datetime.now(), "last_ok_at": None, "last_error": None,
    })
    return f"Added {name}.", client.list_sources()


@callback(
    Output(page.VC_SOURCES_STATUS, "children", allow_duplicate=True),
    Input(page.VC_SOURCES_TABLE, "data"),
    State(page.VC_SOURCES_TABLE, "data_previous"),
    prevent_initial_call=True,
)
def edit_sources(data, previous):
    if previous is None:
        return no_update
    cur = {r["source_id"]: r for r in data}
    prev = {r["source_id"]: r for r in previous}
    for sid in prev.keys() - cur.keys():
        client.delete_source(sid)
        return f"Deleted {prev[sid].get('name')}."
    for sid, row in cur.items():
        if row != prev.get(sid):
            client.upsert_source(row)
            return f"Updated {row.get('name')}."
    return no_update
