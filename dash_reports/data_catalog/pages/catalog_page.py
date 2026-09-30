from urllib.parse import urlencode

import dash_cytoscape as cyto
import pandas as pd
from dash import dcc, html

from apo_config.config import config
from dash_reports.data_catalog.data import (
    get_access,
    get_asset,
    get_column_lineage,
    get_columns,
    get_lineage,
    get_quality,
    get_usage,
    load_assets,
)
from dash_reports.data_catalog.data.categorization import (
    BUSINESS_TOPICS,
    category_description,
    describe_asset,
    friendly_name,
)
from dash_reports.report_common.pages.genie_ui import kpi_card
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import make_dash_table, make_loading


INIT_ID = "catalog-init"
SNAPSHOT_STORE_ID = "catalog-snapshot"
ASSET_STORE_ID = "catalog-asset"
VIEW_STORE_ID = "catalog-view"
PAGE_STORE_ID = "catalog-page"
URL_SYNC_ID = "catalog-url-sync"
KPI_ID = "catalog-kpis"
SEARCH_ID = "catalog-search"
SEARCH_BUTTON_ID = "catalog-search-button"
REFRESH_ID = "catalog-refresh"
RESULTS_ID = "catalog-results"
STATUS_ID = "catalog-status"
SOURCE_STATUS_ID = "catalog-source-status"
DETAIL_ID = "catalog-detail"
DETAIL_TAB_ID = "catalog-detail-tab"
DETAIL_BODY_ID = "catalog-detail-body"
FILTER_IDS = {
    "vendor": "catalog-filter-vendor",
    "data_category": "catalog-filter-category",
    "asset_kind": "catalog-filter-kind",
    "source": "catalog-filter-source",
    "catalog": "catalog-filter-catalog",
    "schema": "catalog-filter-schema",
    "tags": "catalog-filter-tags",
}
GOVERNANCE_FILTER_ID = "catalog-filter-governance"
RESEARCH_SOURCE = "broker research"
DEFAULT_TAG_FILTER = "tier=gold"
SCHEMA_PAGE_SIZE = 25
MAX_SCHEMA_SECTIONS = 60
SECTION_IDS_TYPE = "catalog-section-ids"
SECTION_PARAMS_TYPE = "catalog-section-params"
SECTION_PAGE_TYPE = "catalog-section-page"
SECTION_PREV_TYPE = "catalog-section-prev"
SECTION_NEXT_TYPE = "catalog-section-next"
SECTION_BODY_TYPE = "catalog-section-body"
SECTION_STATUS_TYPE = "catalog-section-status"


def get_data_catalog_header():
    return html.Div([get_page_title("Argus Data Discovery"), html.Br()])


def _facet(label, facet, value=None):
    seeded = value if isinstance(value, list) else ([value] if value else [])
    return html.Div(
        [
            html.Label(label, className="catalog-filter-label"),
            dcc.Dropdown(
                id=FILTER_IDS[facet],
                options=seeded,
                value=value,
                multi=True,
                placeholder=f"All {label.lower()}",
                className="catalog-dropdown",
            ),
        ],
        className="catalog-filter",
    )


def _tag_filter(value=None):
    return html.Div(
        [
            html.Label("Tags", className="catalog-filter-label"),
            dcc.Input(
                id=FILTER_IDS["tags"],
                type="text",
                debounce=True,
                placeholder="tier=gold; tier=silver",
                value=value,
                className="catalog-filter-input",
            ),
            html.Div("Separate OR conditions with ;", className="catalog-filter-help"),
        ],
        className="catalog-filter",
    )


def create_layout(
    initial_asset=None,
    initial_vendor=None,
    initial_category=None,
    initial_view="directory",
    initial_catalog=None,
    initial_schema=None,
    initial_source=None,
    initial_asset_kind=None,
    initial_tags=DEFAULT_TAG_FILTER,
    initial_governance=None,
    initial_query=None,
    initial_page=1,
):
    hero = html.Div(
        [
            html.Div("Argus Data Discovery", className="catalog-eyebrow"),
            html.H1("Find the right data", className="catalog-title"),
            html.P(
                "Browse trusted data by provider and business topic. Understand what each "
                "dataset contains, where it comes from, and when to use it.",
                className="catalog-subtitle",
            ),
        ],
        className="catalog-hero",
    )
    search = html.Div(
        [
            dcc.Input(
                id=SEARCH_ID,
                type="search",
                debounce=True,
                placeholder="What data are you looking for? Try bond prices, portfolio holdings, company fundamentals…",
                value=initial_query,
                className="catalog-search-input",
            ),
            html.Button("Search", id=SEARCH_BUTTON_ID, n_clicks=0, className="catalog-primary-button"),
            html.Button("Update data", id=REFRESH_ID, n_clicks=0, className="catalog-secondary-button"),
        ],
        className="catalog-searchbar",
    )
    filters = html.Aside(
        [
            html.Div("Discover", className="catalog-filters-title"),
            _facet("Providers", "vendor", initial_vendor),
            _facet("Business topics", "data_category", initial_category),
            _facet("Dataset types", "asset_kind", initial_asset_kind),
            html.Details(
                [
                    html.Summary("More filters"),
                    html.Div(
                        [
                            _facet("Platforms", "source", initial_source),
                            _facet("Catalogs", "catalog", initial_catalog),
                            _facet("Schemas", "schema", initial_schema),
                            _tag_filter(initial_tags),
                            html.Label("Data confidence", className="catalog-filter-label"),
                            dcc.Checklist(
                                id=GOVERNANCE_FILTER_ID,
                                options=[
                                    {"label": "Has a description", "value": "documented"},
                                    {"label": "Certified", "value": "certified"},
                                    {"label": "Shows dependencies", "value": "has_lineage"},
                                    {"label": "Health monitored", "value": "has_quality"},
                                    {"label": "Recently used", "value": "has_usage"},
                                ],
                                value=initial_governance or [],
                                className="catalog-checklist",
                            ),
                        ],
                        className="catalog-more-filters",
                    ),
                ],
                className="catalog-more-details",
            ),
        ],
        className="catalog-filters",
    )
    browser = html.Div(
        [
            filters,
            html.Main(
                [
                    html.Div(id=STATUS_ID, className="catalog-result-status"),
                    make_loading(html.Div(id=RESULTS_ID)),
                ],
                className="catalog-browser-main",
            ),
        ],
        className="catalog-browser",
    )
    return html.Div(
        [
            dcc.Store(id=ASSET_STORE_ID, data=initial_asset),
            dcc.Store(id=VIEW_STORE_ID, data=initial_view),
            dcc.Store(id=PAGE_STORE_ID, data=initial_page),
            dcc.Store(id=URL_SYNC_ID),
            dcc.Store(id=SNAPSHOT_STORE_ID),
            dcc.Interval(id=INIT_ID, interval=2000, n_intervals=0, max_intervals=150),
            hero,
            html.Div(id=KPI_ID, className="catalog-kpis"),
            search,
            html.Div(id=DETAIL_ID),
            browser,
            html.Div(id=SOURCE_STATUS_ID, className="catalog-source-status"),
        ],
        className="catalog-page",
    )


def _format_number(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _format_bytes(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if abs(size) < 1024:
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} EB"


def kpi_strip(summary):
    cards = (
        ("Datasets", summary["assets"], "Available to discover", "green"),
        ("Data providers", summary["vendors"], "Apollo and external sources", "blue"),
        ("Business topics", summary["categories"], "Organized for easy browsing", "blue"),
        ("Source tables", summary["source_tables"], "Detailed source data", "orange"),
        ("Ready-to-use views", summary["views"], "Prepared for analysis", "green"),
    )
    return [
        kpi_card(label, _format_number(value), accent=tone, sub=subtitle)
        for label, value, subtitle, tone in cards
    ]


def _source_counts(frame):
    if frame.empty or "source" not in frame:
        return ""
    counts = frame["source"].astype(str).value_counts()
    return ", ".join(f"{int(count):,} {source}" for source, count in counts.items())


def _untagged_summary(assets):
    if assets.empty or "tags" not in assets:
        return None
    has_tier = assets["tags"].fillna("").astype(str).apply(
        lambda value: any(tag.strip().casefold().startswith("tier=") for tag in value.split(","))
    )
    missing = assets[~has_tier]
    if missing.empty:
        return None
    detail = _source_counts(missing)
    return (
        f"{len(missing):,} datasets have no tier metadata ({detail}). "
        "Tier filters only surface datasets with a Unity Catalog or Delphi Portal tier badge."
    )


def result_status(matched, assets=None, tags=None):
    total = len(matched)
    if not tags:
        return f"{total:,} datasets available"
    children = [html.Span(f"{total:,} datasets match {tags}", className="catalog-status-headline")]
    breakdown = _source_counts(matched)
    if breakdown:
        children.append(html.Span(f"  ·  {breakdown}", className="catalog-status-breakdown"))
    hint = _untagged_summary(assets if assets is not None else matched)
    if hint:
        children.append(html.Div(hint, className="catalog-status-hint"))
    return html.Div(children, className="catalog-status")


def _badge(text, tone="neutral"):
    return html.Span(str(text), className=f"catalog-badge catalog-badge-{tone}")


def _as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _truthy(value):
    return str(value).strip().lower() in {"true", "1"}


def _is_research(asset):
    return str(asset.get("source")).lower() == RESEARCH_SOURCE


def _structure_tags(frame):
    source = frame["source"].astype(str).str.lower()
    tags = []
    if source.ne(RESEARCH_SOURCE).any():
        tags.append(_badge("Structured", "structured"))
    if source.eq(RESEARCH_SOURCE).any():
        tags.append(_badge("Unstructured", "unstructured"))
    return tags


def _ranking_signals(asset):
    signals = []
    if _is_research(asset):
        docs = _as_int(asset.get("row_count"))
        recent = _as_int(asset.get("queries_30d"))
        if docs:
            signals.append(_badge(f"{docs:,} documents", "usage"))
        if recent:
            signals.append(_badge(f"{recent:,} new · 30d", "access"))
        return signals
    queries = _as_int(asset.get("queries_30d"))
    if queries:
        signals.append(_badge(f"{queries:,} queries · 30d", "usage"))
    if _truthy(asset.get("service_can_select")):
        signals.append(_badge("Service access", "access"))
    return signals


def _connection_strings(asset):
    connection = asset.get("connection") or ""
    profile = config.getappconfig(connection) if connection else {}
    if str(asset.get("source")).lower() == "snowflake":
        host = str(profile.get("server") or "")
        if host and not host.endswith(".snowflakecomputing.com"):
            host = f"{host}.snowflakecomputing.com"
        database = asset.get("catalog") or profile.get("database_name") or ""
        schema = asset.get("schema") or ""
        warehouse = profile.get("warehouse") or ""
        authenticator = profile.get("application") or "externalbrowser"
        jdbc = (
            f"jdbc:snowflake://{host}/?db={database}&schema={schema}"
            f"&warehouse={warehouse}&authenticator={authenticator}"
        )
        odbc = (
            f"Driver={{SnowflakeDSIIDriver}};Server={host};Database={database};"
            f"Schema={schema};Warehouse={warehouse};Authenticator={authenticator};"
        )
        return {"JDBC": jdbc, "ODBC": odbc}
    host = str(profile.get("databricks_host") or profile.get("server_hostname") or "").removeprefix("https://")
    http_path = profile.get("databricks_http_path") or profile.get("http_path") or ""
    catalog = asset.get("catalog") or profile.get("databricks_catalog") or ""
    schema = asset.get("schema") or ""
    jdbc = (
        f"jdbc:databricks://{host}:443/default;transportMode=http;ssl=1;"
        f"httpPath={http_path};AuthMech=11;Auth_Flow=2;ConnCatalog={catalog};ConnSchema={schema}"
    )
    odbc = (
        f"Driver={{Databricks ODBC Driver}};Host={host};Port=443;HTTPPath={http_path};"
        f"SSL=1;ThriftTransport=2;AuthMech=11;Auth_Flow=2;Catalog={catalog};Schema={schema};"
    )
    return {"JDBC": jdbc, "ODBC": odbc}


def _connection_panel(asset):
    strings = _connection_strings(asset)
    return html.Div(
        [
            html.Div("Connection strings", className="catalog-section-title"),
            html.Div(
                "Choose the format supported by your database client. Authentication opens in your browser.",
                className="catalog-description",
            ),
            *[
                html.Div(
                    [
                        html.Div(label, className="catalog-connection-label"),
                        html.Pre(value, className="catalog-connection-code"),
                    ]
                )
                for label, value in strings.items()
            ],
        ],
        className="catalog-detail-panel",
    )


def _catalog_href(params=None, **updates):
    query = {
        key: value
        for key, value in dict(params or {}).items()
        if value is not None and str(value)
    }
    query.update(updates)
    return f"/data-catalog?{urlencode(query)}"


def asset_card(asset, rank=None, params=None):
    vendor = asset.get("vendor") or asset.get("provider") or "Apollo"
    category = asset.get("data_category") or asset.get("domain") or "General"
    kind = asset.get("asset_kind") or asset.get("object_type") or "Dataset"
    research = _is_research(asset)
    badges = [
        _badge("Unstructured" if research else "Structured", "unstructured" if research else "structured"),
        _badge(vendor, "source"),
        _badge(category, "domain"),
        _badge(kind, "type"),
    ]
    if asset["documented"]:
        badges.append(_badge("Documented", "success"))
    if asset["certified"]:
        badges.append(_badge("Certified", "success"))
    fields = str(asset.get("example_fields") or "").split(",")
    description = asset.get("business_description") or describe_asset(asset, fields)
    head = [
        html.Div(
            [
                html.Div(asset["name"], className="catalog-card-title"),
                html.Div(asset["full_name"], className="catalog-card-fqn"),
            ]
        )
    ]
    if rank:
        head.append(html.Span(f"#{rank}", className="catalog-card-rank"))
    return dcc.Link(
        [
            html.Div(head, className="catalog-card-head"),
            html.Div(description, className="catalog-card-description"),
            html.Div(badges + _ranking_signals(asset), className="catalog-card-badges"),
        ],
        href=_catalog_href(params, asset=asset["asset_id"]),
        className="catalog-card",
    )


def results_list(frame, start_rank=0, params=None):
    if frame.empty:
        return html.Div(
            [
                html.Div("No matching assets", className="catalog-empty-title"),
                html.Div("Clear filters or broaden the search.", className="catalog-empty-subtitle"),
            ],
            className="catalog-empty",
        )
    return html.Div(
        [
            asset_card(row, rank=start_rank + index + 1, params=params)
            for index, row in enumerate(frame.to_dict("records"))
        ],
        className="catalog-results-list",
    )


def _schema_metrics(group):
    query_values = group["queries_30d"] if "queries_30d" in group else pd.Series(0, index=group.index)
    queries = int(pd.to_numeric(query_values, errors="coerce").fillna(0).sum())
    selectable = group.get("service_can_select", pd.Series(False, index=group.index))
    selectable_count = int(selectable.astype(str).str.lower().isin({"true", "1"}).sum())
    return queries, selectable_count


def _section_controls(key, total, page_count):
    return html.Div(
        [
            html.Button(
                "Previous",
                id={"type": SECTION_PREV_TYPE, "schema": key},
                n_clicks=0,
                disabled=True,
                className="catalog-page-button",
            ),
            html.Span(
                f"1–{min(SCHEMA_PAGE_SIZE, total):,} of {total:,}",
                id={"type": SECTION_STATUS_TYPE, "schema": key},
                className="catalog-page-status",
            ),
            html.Button(
                "Next",
                id={"type": SECTION_NEXT_TYPE, "schema": key},
                n_clicks=0,
                disabled=page_count <= 1,
                className="catalog-page-button",
            ),
        ],
        className="catalog-pagination",
        style={} if page_count > 1 else {"display": "none"},
    )


def _schema_section(catalog, schema, group, params):
    key = f"{catalog}.{schema}"
    total = len(group)
    page_count = max(1, (total + SCHEMA_PAGE_SIZE - 1) // SCHEMA_PAGE_SIZE)
    queries, selectable_count = _schema_metrics(group)
    research = _is_research(group.iloc[0])
    if research:
        description = f"{total:,} providers · ranked by document activity"
        counts = [
            html.Span(f"{int(pd.to_numeric(group['row_count'], errors='coerce').fillna(0).sum()):,} documents"),
            html.Span(f"{queries:,} new in 30 days"),
        ]
    else:
        description = f"{total:,} tables and views · ranked by usage and access"
        counts = [
            html.Span(f"{queries:,} queries in 30 days"),
            html.Span(f"{selectable_count:,} service-accessible"),
        ]
    return html.Section(
        [
            dcc.Store(id={"type": SECTION_IDS_TYPE, "schema": key}, data=group["asset_id"].tolist()),
            dcc.Store(id={"type": SECTION_PARAMS_TYPE, "schema": key}, data=params),
            dcc.Store(id={"type": SECTION_PAGE_TYPE, "schema": key}, data=1),
            html.Div(
                [
                    html.Div(key, className="catalog-schema-title"),
                    html.Div(description, className="catalog-schema-description"),
                    html.Div(counts, className="catalog-directory-counts"),
                ],
                className="catalog-schema-section-head",
            ),
            html.Div(
                results_list(group.head(SCHEMA_PAGE_SIZE), params=params),
                id={"type": SECTION_BODY_TYPE, "schema": key},
            ),
            _section_controls(key, total, page_count),
        ],
        className="catalog-schema-section",
    )


def schema_sections(frame, params=None):
    if frame.empty:
        return results_list(frame, params=params)
    params = dict(params or {})
    groups = list(frame.groupby(["catalog", "schema"], sort=False))
    shown = groups[:MAX_SCHEMA_SECTIONS]
    header = [
        dcc.Link(
            "Back to browse",
            href=_catalog_href(params, view="directory"),
            className="catalog-back",
        ),
        html.H2("Datasets by schema", className="catalog-directory-heading"),
        html.P(
            "Schemas are ordered by their highest-ranked accessible dataset. Each schema lists its "
            f"most-used, service-accessible tables first — page through {SCHEMA_PAGE_SIZE} at a time.",
            className="catalog-directory-intro",
        ),
    ]
    if len(groups) > len(shown):
        header.append(
            html.Div(
                f"Showing the top {len(shown)} of {len(groups):,} schemas. "
                "Refine with search or filters to narrow the list.",
                className="catalog-result-status",
            )
        )
    sections = [_schema_section(str(catalog), str(schema), group, params) for (catalog, schema), group in shown]
    return html.Div(header + [html.Div(sections, className="catalog-schema-sections")])


def render_schema_page(triggered_id, page, asset_ids, params=None):
    asset_ids = list(asset_ids or [])
    total = len(asset_ids)
    page_count = max(1, (total + SCHEMA_PAGE_SIZE - 1) // SCHEMA_PAGE_SIZE)
    page = min(max(_as_int(page) or 1, 1), page_count)
    direction = (triggered_id or {}).get("type")
    if direction == SECTION_NEXT_TYPE:
        page = min(page + 1, page_count)
    elif direction == SECTION_PREV_TYPE:
        page = max(page - 1, 1)
    start = (page - 1) * SCHEMA_PAGE_SIZE
    stop = min(start + SCHEMA_PAGE_SIZE, total)
    page_ids = asset_ids[start:stop]
    assets = load_assets()
    frame = assets[assets["asset_id"].isin(page_ids)]
    frame = frame.set_index("asset_id").reindex(page_ids).dropna(how="all").reset_index()
    status = f"{start + 1:,}–{stop:,} of {total:,}"
    return results_list(frame, start_rank=start, params=params), status, page <= 1, page >= page_count, page


def _directory_counts(frame):
    source = frame["source"].astype(str).str.lower()
    structured = frame[source.ne(RESEARCH_SOURCE)]
    research = frame[source.eq(RESEARCH_SOURCE)]
    counts = [html.Span(f"{len(frame):,} datasets")]
    if not structured.empty:
        counts.append(html.Span(f"{int((structured['asset_kind'] == 'Source table').sum()):,} source tables"))
        counts.append(html.Span(f"{int((structured['asset_kind'] == 'Ready-to-use view').sum()):,} views"))
    if not research.empty:
        docs = int(pd.to_numeric(research["row_count"], errors="coerce").fillna(0).sum())
        counts.append(html.Span(f"{docs:,} documents"))
    return counts


def _directory_card(label, frame, query_key, description, examples, params=None, icon=None):
    return dcc.Link(
        [
            html.Div(
                [
                    html.Span(label),
                    html.Span(icon, className="catalog-directory-icon") if icon else None,
                ],
                className="catalog-directory-title",
            ),
            html.Div(_structure_tags(frame), className="catalog-card-badges"),
            html.Div(description, className="catalog-directory-description"),
            html.Div(examples, className="catalog-directory-examples"),
            html.Div(_directory_counts(frame), className="catalog-directory-counts"),
        ],
        href=_catalog_href(params, view="datasets", **{query_key: str(label)}),
        className="catalog-directory-card",
    )


def _directory_section(title, intro, cards):
    return html.Details(
        [
            html.Summary(
                [
                    html.Span(title),
                    html.Span(f"{len(cards):,} options", className="catalog-directory-option-count"),
                ],
                className="catalog-directory-summary",
            ),
            html.P(intro, className="catalog-directory-intro"),
            html.Div(cards, className="catalog-directory-grid"),
        ],
        className="catalog-directory-section",
        open=False,
    )


def discovery_directory(frame, params=None):
    if frame.empty:
        return results_list(frame, params=params)
    providers = [
        _directory_card(
            vendor,
            group,
            "vendor",
            f"Explore datasets supplied by {vendor}.",
            "Leading topics: "
            + ", ".join(group["data_category"].value_counts().head(3).index.astype(str)),
            params,
        )
        for vendor, group in frame.groupby("vendor", sort=True)
    ]
    category_groups = {category: group for category, group in frame.groupby("data_category", sort=True)}
    ordered_categories = [
        *[category for category in BUSINESS_TOPICS if category in category_groups],
        *sorted(category for category in category_groups if category not in BUSINESS_TOPICS),
    ]
    categories = [
        _directory_card(
            category,
            category_groups[category],
            "category",
            category_description(category),
            BUSINESS_TOPICS.get(category, {}).get("examples")
            or "Examples: "
            + ", ".join(
                friendly_name(value)
                for value in category_groups[category].sort_values(
                    ["documented", "column_count"], ascending=False
                )["name"].head(3)
            ),
            params,
            BUSINESS_TOPICS.get(category, {}).get("icon"),
        )
        for category in ordered_categories
    ]
    platforms = [
        _directory_card(
            source,
            group,
            "source",
            f"Explore datasets available through {source}.",
            "Leading providers: "
            + ", ".join(group["vendor"].value_counts().head(3).index.astype(str)),
            params,
        )
        for source, group in frame.groupby("source", sort=True)
    ]
    sections = [
        _directory_section(
            "Browse by data provider",
            "Start with the organization that supplies or manages the data.",
            providers,
        ),
        _directory_section(
            "Browse by business topic",
            "Explore data organized around the questions you are trying to answer.",
            categories,
        ),
        _directory_section(
            "Browse by platform",
            "Choose the system where the data is available.",
            platforms,
        ),
    ]
    return html.Div(sections, className="catalog-directory")


def source_status_panel(status, snapshot=None):
    snapshot = snapshot or {}
    rows = [
        html.Div(
            [html.Span(source, className="catalog-source-name"), html.Span(value)],
            className="catalog-source-row",
        )
        for source, value in status.items()
    ]
    generated_at = str(snapshot.get("generated_at") or "")[:19]
    if snapshot.get("refreshing"):
        message = "Updating in the background — current data remains available"
    elif generated_at:
        message = f"Data updated {generated_at} UTC"
    else:
        message = "Preparing the first data discovery snapshot"
    return html.Div(
        [
            html.Div(message, className="catalog-snapshot-message"),
            html.Details(
                [html.Summary("About this data"), html.Div(rows, className="catalog-source-rows")],
                className="catalog-source-details",
            ),
        ],
    )


def _detail_stat(label, value):
    return html.Div(
        [html.Div(value or "—", className="catalog-detail-stat-value"), html.Div(label, className="catalog-detail-stat-label")],
        className="catalog-detail-stat",
    )


def detail_shell(asset):
    tabs = dcc.Tabs(
        id=DETAIL_TAB_ID,
        value="overview",
        children=[
            dcc.Tab(label="Overview", value="overview"),
            dcc.Tab(label="Fields", value="schema"),
            dcc.Tab(label="Sources & dependencies", value="lineage"),
            dcc.Tab(label="Data health", value="quality"),
            dcc.Tab(label="Popularity", value="usage"),
            dcc.Tab(label="Who can use it", value="access"),
        ],
        className="catalog-tabs",
    )
    return html.Section(
        [
            html.Div(
                [
                    dcc.Link("Back to catalog", href="/data-catalog", className="catalog-back"),
                    html.Div(asset["name"], className="catalog-detail-title"),
                    html.Div(asset["full_name"], className="catalog-detail-fqn"),
                    html.Div(
                        [
                            _badge(asset.get("vendor") or asset["provider"], "source"),
                            _badge(asset.get("data_category") or asset["domain"], "domain"),
                            _badge(asset.get("asset_kind") or asset["object_type"], "type"),
                        ],
                        className="catalog-card-badges",
                    ),
                ],
                className="catalog-detail-head",
            ),
            tabs,
            make_loading(html.Div(id=DETAIL_BODY_ID)),
        ],
        className="catalog-detail",
    )


def _coverage_text(asset):
    start = str(asset.get("created") or "")[:10]
    end = str(asset.get("last_altered") or "")[:10]
    if start and end:
        return f"{start} → {end}"
    return end or start or "—"


def _research_location_panel(asset):
    vault = config.getconfig("argus_wiki", "vault_root") or ""
    location = f"{vault}\\wiki\\senders" if vault else "Configured in the Argus research wiki"
    return html.Div(
        [
            html.Div("Where to find it", className="catalog-section-title"),
            html.Div(
                "Synthesized research pages live in the Argus wiki vault, grouped by sending provider.",
                className="catalog-description",
            ),
            html.Pre(location, className="catalog-connection-code"),
        ],
        className="catalog-detail-panel",
    )


def _research_overview(asset):
    return html.Div(
        [
            html.Div(
                [
                    _detail_stat("Data provider", asset.get("vendor") or asset["provider"]),
                    _detail_stat("Research type", asset.get("schema")),
                    _detail_stat("Documents", _format_number(asset["row_count"])),
                    _detail_stat("Formats", asset.get("data_format") or "—"),
                    _detail_stat("Coverage", _coverage_text(asset)),
                    _detail_stat("Latest document", str(asset["last_altered"])[:10]),
                ],
                className="catalog-detail-stats",
            ),
            html.Div(
                [
                    html.Div("What this research covers", className="catalog-section-title"),
                    html.Div(asset.get("business_description") or "", className="catalog-description"),
                ],
                className="catalog-detail-panel",
            ),
            _research_location_panel(asset),
        ]
    )


def _overview(asset):
    if _is_research(asset):
        return _research_overview(asset)
    capabilities = asset.get("capabilities") or {}
    capability_badges = [
        _badge(name.title(), "success" if enabled else "muted")
        for name, enabled in capabilities.items()
    ]
    return html.Div(
        [
            html.Div(
                [
                    _detail_stat("Data owner", asset["owner"]),
                    _detail_stat("Data provider", asset.get("vendor") or asset["provider"]),
                    _detail_stat("Business topic", asset.get("data_category") or asset["domain"]),
                    _detail_stat("Fields", _format_number(asset["column_count"])),
                    _detail_stat("Rows", _format_number(asset["row_count"])),
                    _detail_stat("Last updated", str(asset["last_altered"])[:19]),
                ],
                className="catalog-detail-stats",
            ),
            html.Div(
                [
                    html.Div("What this dataset contains", className="catalog-section-title"),
                    html.Div(
                        asset.get("business_description")
                        or describe_asset(asset, str(asset.get("example_fields") or "").split(",")),
                        className="catalog-description",
                    ),
                ],
                className="catalog-detail-panel",
            ),
            html.Div(
                [
                    html.Div("What we know about this data", className="catalog-section-title"),
                    html.Div(capability_badges, className="catalog-card-badges"),
                    html.Div(asset["tags"] or "No table tags are available.", className="catalog-description"),
                ],
                className="catalog-detail-panel",
            ),
            _connection_panel(asset),
        ]
    )


def _table_or_note(frame, note, table_id):
    if frame.empty:
        return html.Div(note, className="catalog-note")
    return make_dash_table(frame, id=table_id, page_size=20)


def _business_table(frame, labels=None):
    return frame.drop(
        columns=["asset_id", "catalog", "schema", "name"],
        errors="ignore",
    ).rename(columns=labels or {})


def _lineage_graph(asset, frame):
    if frame.empty:
        return html.Div("No lineage events were observed in the last 90 days.", className="catalog-note")
    nodes = {asset["full_name"]}
    for row in frame.to_dict("records"):
        nodes.update([row["source"], row["target"]])
    elements = [
        {
            "data": {"id": node, "label": ".".join(str(node).split(".")[-2:])},
            "classes": "focus" if node.lower() == asset["full_name"].lower() else "",
        }
        for node in nodes
        if node
    ]
    elements.extend(
        {
            "data": {
                "source": row["source"],
                "target": row["target"],
                "label": f"{int(row['events']):,}",
            }
        }
        for row in frame.to_dict("records")
        if row["source"] and row["target"]
    )
    return cyto.Cytoscape(
        id="catalog-lineage-graph",
        elements=elements,
        layout={"name": "breadthfirst", "directed": True, "spacingFactor": 1.4},
        stylesheet=[
            {"selector": "node", "style": {"label": "data(label)", "font-size": 10, "background-color": "#395878"}},
            {"selector": "node.focus", "style": {"background-color": "#007B63", "width": 36, "height": 36}},
            {
                "selector": "edge",
                "style": {
                    "curve-style": "bezier",
                    "target-arrow-shape": "triangle",
                    "line-color": "#CAC2BA",
                    "target-arrow-color": "#CAC2BA",
                    "label": "data(label)",
                    "font-size": 9,
                },
            },
        ],
        style={"width": "100%", "height": "480px"},
    )


def _usage_view(frame):
    if frame.empty:
        return html.Div("Usage analytics are not available for this source.", className="catalog-note")
    row = frame.iloc[0].to_dict()
    return html.Div(
        [
            _detail_stat("Queries · 30d", _format_number(row.get("queries_30d"))),
            _detail_stat("Queries · 90d", _format_number(row.get("queries_90d"))),
            _detail_stat("Last accessed", str(_clean_display(row.get("last_accessed")))[:19]),
            _detail_stat("Average duration", f"{_format_number(row.get('avg_duration_ms'))} ms"),
            _detail_stat("Bytes read", _format_bytes(row.get("read_bytes"))),
            _detail_stat("Client applications", _format_number(row.get("client_applications"))),
        ],
        className="catalog-detail-stats",
    )


def _clean_display(value):
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    return value


def render_detail_tab(asset_id, tab):
    asset = get_asset(asset_id)
    if not asset:
        return html.Div("Asset not found.", className="catalog-note")
    if tab == "overview":
        return _overview(asset)
    if tab == "schema":
        columns = _business_table(
            get_columns(asset_id),
            {
                "ordinal_position": "Order",
                "column_name": "Field",
                "data_type": "Type",
                "is_nullable": "Optional",
                "comment": "Description",
                "tags": "Tags",
            },
        )
        return _table_or_note(columns, "Field descriptions are unavailable.", "catalog-columns-grid")
    if tab == "lineage":
        lineage = get_lineage(asset_id)
        column_lineage = get_column_lineage(asset_id)
        return html.Div(
            [
                html.Div("Dataset connections · last 90 days", className="catalog-section-title"),
                _lineage_graph(asset, lineage),
                html.Div("Field-level connections", className="catalog-section-title catalog-section-spaced"),
                _table_or_note(
                    _business_table(
                        column_lineage,
                        {
                            "source_table": "Source dataset",
                            "source_column": "Source field",
                            "target_table": "Destination dataset",
                            "target_column": "Destination field",
                            "events": "Observed uses",
                            "last_seen": "Last observed",
                        },
                    ),
                    "No field-level connections were observed.",
                    "catalog-column-lineage-grid",
                ),
            ]
        )
    if tab == "quality":
        quality = get_quality(asset_id)
        children = [
            _table_or_note(quality["summary"], "Data health details are unavailable.", "catalog-quality-summary-grid")
        ]
        if not quality["profile"].empty:
            children.extend(
                [
                    html.Div("Field completeness", className="catalog-section-title catalog-section-spaced"),
                    make_dash_table(
                        _business_table(quality["profile"]),
                        id="catalog-quality-profile-grid",
                        page_size=20,
                    ),
                ]
            )
        if not quality["drift"].empty:
            children.extend(
                [
                    html.Div("Changes over time", className="catalog-section-title catalog-section-spaced"),
                    make_dash_table(
                        _business_table(quality["drift"]),
                        id="catalog-quality-drift-grid",
                        page_size=20,
                    ),
                ]
            )
        return html.Div(children)
    if tab == "usage":
        return _usage_view(get_usage(asset_id))
    if tab == "access":
        access = get_access(asset_id)
        owner_link = (
            html.A(f"Contact {asset['owner']}", href=f"mailto:{asset['owner']}", className="catalog-primary-link")
            if "@" in str(asset["owner"])
            else html.Div(f"Owner: {asset['owner'] or 'Unavailable'}", className="catalog-description")
        )
        return html.Div(
            [
                html.Div(
                    [
                        html.Div("Access details", className="catalog-section-title"),
                        html.Div(asset["full_name"], className="catalog-copy-fqn"),
                        owner_link,
                    ],
                    className="catalog-detail-panel",
                ),
                _table_or_note(
                    _business_table(access),
                    "No dataset-level permissions are visible.",
                    "catalog-access-grid",
                ),
            ]
        )
    return _overview(asset)
