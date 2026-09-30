import logging

from dash import Input, MATCH, Output, State, callback, clientside_callback, ctx, html, no_update
from dash.exceptions import PreventUpdate

from dash_reports.data_catalog.data import (
    catalog_status,
    get_asset,
    get_facets,
    get_summary,
    load_assets,
    refresh_catalog,
    search_assets,
    source_status,
)
from dash_reports.data_catalog.pages.catalog_page import (
    ASSET_STORE_ID,
    DETAIL_BODY_ID,
    DETAIL_ID,
    DETAIL_TAB_ID,
    FILTER_IDS,
    GOVERNANCE_FILTER_ID,
    INIT_ID,
    KPI_ID,
    PAGE_STORE_ID,
    REFRESH_ID,
    RESULTS_ID,
    SEARCH_BUTTON_ID,
    SEARCH_ID,
    SECTION_BODY_TYPE,
    SECTION_IDS_TYPE,
    SECTION_NEXT_TYPE,
    SECTION_PAGE_TYPE,
    SECTION_PARAMS_TYPE,
    SECTION_PREV_TYPE,
    SECTION_STATUS_TYPE,
    SNAPSHOT_STORE_ID,
    SOURCE_STATUS_ID,
    STATUS_ID,
    URL_SYNC_ID,
    VIEW_STORE_ID,
    detail_shell,
    discovery_directory,
    kpi_strip,
    render_detail_tab,
    render_schema_page,
    result_status,
    schema_sections,
    source_status_panel,
)


log = logging.getLogger(__name__)


clientside_callback(
    """
    function(asset, view, page, query, vendors, categories, kinds, sources, catalogs, schemas, tags, governance) {
        const params = new URLSearchParams();
        const put = (name, value) => {
            if (Array.isArray(value)) value = value.filter(Boolean).join(",");
            if (value !== null && value !== undefined && String(value).length) {
                params.set(name, String(value));
            }
        };
        put("asset", asset);
        if (view && view !== "directory") put("view", view);
        if (page && Number(page) !== 1) put("page", page);
        put("q", query);
        put("vendor", vendors);
        put("category", categories);
        put("kind", kinds);
        put("source", sources);
        put("catalog", catalogs);
        put("schema", schemas);
        put("tags", tags);
        put("governance", governance);
        const search = params.toString();
        window.history.replaceState({}, "", window.location.pathname + (search ? "?" + search : ""));
        return search;
    }
    """,
    Output(URL_SYNC_ID, "data"),
    Input(ASSET_STORE_ID, "data"),
    Input(VIEW_STORE_ID, "data"),
    Input(PAGE_STORE_ID, "data"),
    Input(SEARCH_ID, "value"),
    Input(FILTER_IDS["vendor"], "value"),
    Input(FILTER_IDS["data_category"], "value"),
    Input(FILTER_IDS["asset_kind"], "value"),
    Input(FILTER_IDS["source"], "value"),
    Input(FILTER_IDS["catalog"], "value"),
    Input(FILTER_IDS["schema"], "value"),
    Input(FILTER_IDS["tags"], "value"),
    Input(GOVERNANCE_FILTER_ID, "value"),
)


def _catalog_content(matched, view, catalogs=None, schemas=None, page=1, params=None):
    if view == "datasets":
        return schema_sections(matched, params)
    return discovery_directory(matched, params)


def _polling_disabled(snapshot):
    return bool(snapshot.get("available") and not snapshot.get("refreshing"))


def _snapshot_state(snapshot):
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "refreshing": bool(snapshot.get("refreshing")),
    }


@callback(
    Output(SNAPSHOT_STORE_ID, "data"),
    Output(INIT_ID, "disabled"),
    Input(INIT_ID, "n_intervals"),
    Input(REFRESH_ID, "n_clicks"),
    State(SNAPSHOT_STORE_ID, "data"),
)
def poll_snapshot(_interval, _refresh_clicks, current):
    if ctx.triggered_id == REFRESH_ID:
        refresh_catalog()
    elif not _interval:
        load_assets()
    snapshot = catalog_status()
    state = _snapshot_state(snapshot)
    return (state if state != current else no_update), _polling_disabled(snapshot)


@callback(
    Output(KPI_ID, "children"),
    Output(FILTER_IDS["vendor"], "options"),
    Output(FILTER_IDS["data_category"], "options"),
    Output(FILTER_IDS["asset_kind"], "options"),
    Output(FILTER_IDS["source"], "options"),
    Output(FILTER_IDS["catalog"], "options"),
    Output(FILTER_IDS["schema"], "options"),
    Output(RESULTS_ID, "children"),
    Output(STATUS_ID, "children"),
    Output(SOURCE_STATUS_ID, "children"),
    Input(SNAPSHOT_STORE_ID, "data"),
    Input(SEARCH_BUTTON_ID, "n_clicks"),
    Input(SEARCH_ID, "n_submit"),
    Input(FILTER_IDS["vendor"], "value"),
    Input(FILTER_IDS["data_category"], "value"),
    Input(FILTER_IDS["asset_kind"], "value"),
    Input(FILTER_IDS["source"], "value"),
    Input(FILTER_IDS["catalog"], "value"),
    Input(FILTER_IDS["schema"], "value"),
    Input(FILTER_IDS["tags"], "value"),
    Input(GOVERNANCE_FILTER_ID, "value"),
    Input(VIEW_STORE_ID, "data"),
    Input(PAGE_STORE_ID, "data"),
    State(SEARCH_ID, "value"),
)
def update_catalog(
    _snapshot,
    _search_clicks,
    _search_submit,
    vendors,
    data_categories,
    asset_kinds,
    sources,
    catalogs,
    schemas,
    tags,
    governance,
    view,
    page,
    query,
):
    filters = {
        "vendor": vendors,
        "data_category": data_categories,
        "asset_kind": asset_kinds,
        "source": sources,
        "catalog": catalogs,
        "schema": schemas,
        "tags": tags,
    }
    filters.update({name: True for name in governance or []})
    assets = load_assets()
    matched = search_assets(query or "", filters, limit=None, frame=assets)
    summary = get_summary(matched)
    facets = get_facets(assets)
    snapshot = catalog_status()
    navigation = {
        "view": view,
        "vendor": ",".join(map(str, vendors or [])),
        "category": ",".join(map(str, data_categories or [])),
        "kind": ",".join(map(str, asset_kinds or [])),
        "source": ",".join(map(str, sources or [])),
        "catalog": ",".join(map(str, catalogs or [])),
        "schema": ",".join(map(str, schemas or [])),
        "tags": tags or "",
        "governance": ",".join(map(str, governance or [])),
        "q": query or "",
    }
    content = _catalog_content(
        matched,
        view,
        catalogs,
        schemas,
        page,
        navigation,
    )
    status = result_status(matched, assets, tags)
    if not snapshot.get("available") and snapshot.get("refreshing"):
        status = "Preparing the first data discovery snapshot"
    return (
        kpi_strip(summary),
        facets["vendor"],
        facets["data_category"],
        facets["asset_kind"],
        facets["source"],
        facets["catalog"],
        facets["schema"],
        content,
        status,
        source_status_panel(source_status(snapshot), snapshot),
    )


@callback(
    Output(DETAIL_ID, "children"),
    Input(ASSET_STORE_ID, "data"),
)
def open_asset(asset_id):
    if not asset_id:
        return []
    asset = get_asset(asset_id)
    if not asset:
        return html.Div("The requested asset is unavailable.", className="catalog-note")
    return detail_shell(asset)


@callback(
    Output({"type": SECTION_BODY_TYPE, "schema": MATCH}, "children"),
    Output({"type": SECTION_STATUS_TYPE, "schema": MATCH}, "children"),
    Output({"type": SECTION_PREV_TYPE, "schema": MATCH}, "disabled"),
    Output({"type": SECTION_NEXT_TYPE, "schema": MATCH}, "disabled"),
    Output({"type": SECTION_PAGE_TYPE, "schema": MATCH}, "data"),
    Input({"type": SECTION_PREV_TYPE, "schema": MATCH}, "n_clicks"),
    Input({"type": SECTION_NEXT_TYPE, "schema": MATCH}, "n_clicks"),
    State({"type": SECTION_PAGE_TYPE, "schema": MATCH}, "data"),
    State({"type": SECTION_IDS_TYPE, "schema": MATCH}, "data"),
    State({"type": SECTION_PARAMS_TYPE, "schema": MATCH}, "data"),
    prevent_initial_call=True,
)
def paginate_schema_section(_prev_clicks, _next_clicks, page, asset_ids, params):
    return render_schema_page(ctx.triggered_id, page, asset_ids, params)


@callback(
    Output(DETAIL_BODY_ID, "children"),
    Input(DETAIL_TAB_ID, "value"),
    State(ASSET_STORE_ID, "data"),
)
def load_detail_tab(tab, asset_id):
    if not asset_id:
        raise PreventUpdate
    try:
        return render_detail_tab(asset_id, tab or "overview")
    except Exception as exc:
        log.warning("Data catalog detail failed for %s/%s: %s", asset_id, tab, exc)
        return html.Div("This metadata panel is currently unavailable.", className="catalog-note")
