import dash_ag_grid as dag
from dash import dash_table, dcc, html

from dash_reports.vc_dashboard.constants import (
    BACKERS_MIN_SIZE_OPTIONS, DEFAULT_AXIS, DEFAULT_WINDOW, HEATMAP_AXIS_OPTIONS,
    HEATMAP_METRIC_OPTIONS, SOURCE_TYPES, TAB_DEALS, TAB_FIRMS, TAB_FUNDING, TAB_GEO,
    TAB_NEWS, TAB_SOURCES, WINDOW_OPTIONS,
)

VC_TABS = "vc-tabs"
VC_TAB_VALUES = {TAB_FUNDING, TAB_FIRMS, TAB_DEALS, TAB_GEO, TAB_NEWS, TAB_SOURCES,
                 'portfolio', 'relationships'}
VC_WINDOW = "vc-window"
VC_APOLLO = "vc-apollo"
VC_SUMMARY = "vc-summary"

VC_FUNDING_PANEL = "vc-funding-panel"
VC_FUNDING_TABLE = "vc-funding-table"

VC_DEALS_PANEL = "vc-deals-panel"
VC_HEATMAP_AXIS = "vc-heatmap-axis"
VC_HEATMAP_METRIC = "vc-heatmap-metric"
VC_HEATMAP_TABLE = "vc-heatmap-table"
VC_DRILL_TITLE = "vc-drill-title"
VC_DRILL_TABLE = "vc-drill-table"
VC_REVIEW_TABLE = "vc-review-table"
VC_REVIEW_CONFIRM = "vc-review-confirm"
VC_REVIEW_STATUS = "vc-review-status"

VC_FIRMS_PANEL = "vc-firms-panel"
VC_FIRMS_FEED = "vc-firms-feed"

VC_GEO_PANEL = "vc-geo-panel"
VC_GEO_METRIC = "vc-geo-metric"
VC_GEO_CHART = "vc-geo-chart"
VC_GEO_TABLE = "vc-geo-table"

VC_NEWS_PANEL = "vc-news-panel"
VC_NEWS_SEARCH = "vc-news-search"
VC_NEWS_SEARCH_BTN = "vc-news-search-btn"
VC_NEWS_LIST = "vc-news-list"

VC_SOURCES_PANEL = "vc-sources-panel"
VC_SOURCES_TABLE = "vc-sources-table"
VC_SOURCE_NAME = "vc-source-name"
VC_SOURCE_TYPE = "vc-source-type"
VC_SOURCE_URL = "vc-source-url"
VC_SOURCE_TRUST = "vc-source-trust"
VC_SOURCE_ADD_BTN = "vc-source-add-btn"
VC_SOURCES_STATUS = "vc-sources-status"

_GRID_THEME = {"function": "themeQuartz.withParams({accentColor:'var(--apollo-green)',backgroundColor:'var(--apollo-surface)',foregroundColor:'var(--apollo-text)',headerBackgroundColor:'var(--apollo-section-bg)',borderColor:'var(--apollo-border-light)',fontFamily:'var(--apollo-sans)',fontSize:12,wrapperBorder:false,wrapperBorderRadius:0,cellHorizontalPadding:14})"}

_TABLE_STYLE = dict(
    style_table={"overflowX": "auto"},
    style_cell={"padding": "12px 14px", "fontSize": "13px", "textAlign": "left",
                "fontFamily": "var(--apollo-sans)", "border": "0",
                "borderBottom": "1px solid var(--apollo-border-light)",
                "color": "var(--apollo-text)", "backgroundColor": "var(--apollo-surface)"},
    style_header={"fontWeight": "600", "backgroundColor": "var(--apollo-section-bg)"},
    page_size=20,
)


def _deal_grid(grid_id, compact=False):
    fields = [("deal_date", "Date"), ("company", "Company"), ("round_type", "Deal type"),
              ("deal_status", "Status"), ("deal_size", "Deal size"), ("post_val", "Post valuation"), ("sector", "Sector"),
              ("themes", "Themes"), ("region", "Region"), ("lead_investors", "Lead investors")]
    columns = []
    for field, name in fields:
        if compact and field in {"post_val", "themes"}:
            continue
        column = {"field": field, "headerName": name, "minWidth": 125, "flex": 1}
        if field in {"deal_size", "post_val"}:
            column.update(type="numericColumn", cellDataType="number", filter="agNumberColumnFilter",
                          valueFormatter={"function": "params.value == null ? '—' : d3.format('$,.3~s')(params.value).replace('G', 'B').replace('k', 'K')"})
        if field == "company":
            column.update(minWidth=200, pinned="left", cellClass="vc-company")
        columns.append(column)
    return dag.AgGrid(
        id=grid_id, columnDefs=columns, rowData=[], getRowId="params.data.deal_id",
        defaultColDef={"sortable": True, "filter": True, "resizable": True,
                       "floatingFilter": True, "tooltipField": "company"},
        dashGridOptions={"theme": _GRID_THEME,
                         "pagination": True, "paginationPageSize": 20,
                         "paginationPageSizeSelector": [20, 50, 100], "rowHeight": 43,
                         "headerHeight": 40, "animateRows": False,
                         "overlayNoRowsTemplate": "No deals in this selection. Try a longer window."},
        className="vc-grid", style={"height": "620px", "width": "100%"})


def _funding_panel():
    return html.Div(id=VC_FUNDING_PANEL, children=[
        html.Div([
            html.Section([html.Div([html.H3("Deal value over time"),
                html.Span("Completed deals · disclosed USD value", className="vc-muted")], className="vc-section-head"),
                dcc.Graph(id="vc-funding-trend", config={"displayModeBar": False},
                          style={"height": "265px"})], className="vc-card"),
            html.Section([html.Div([html.H3("Sector deal value"),
                html.Span("Completed deals · USD", className="vc-muted")], className="vc-section-head"),
                dcc.Graph(id="vc-sector-trend", config={"displayModeBar": False},
                          style={"height": "265px"})], className="vc-card"),
        ], className="vc-chart-grid"),
        html.Section([html.Div([html.H3("Deals"),
            html.Span(id="vc-funding-caption", className="vc-muted")], className="vc-section-head"),
            _deal_grid(VC_FUNDING_TABLE)], className="vc-card"),
        html.Button("Clear chart drill-down", id="vc-clear-chart", n_clicks=0, className="vc-news-home"),
        html.Section(id="vc-deal-detail", children="Select a deal to read its synopsis and source details.",
                     className="vc-card vc-detail"),
    ])


def _firms_panel():
    return html.Div(id=VC_FIRMS_PANEL, style={"display": "none"}, children=[
        html.Section([html.Div([html.H3("Hot companies"),
            html.Span("Ranked by capital raised, repeat rounds and valuation step-ups",
                      className="vc-muted")], className="vc-section-head"),
            dcc.Loading(html.Div(id="vc-hot-companies", className="vc-firms-feed"),
                        color="#007B63", delay_show=300)], className="vc-card"),
        html.Section([html.Div([html.H3("New deals by followed firms"),
            html.Span("Day-by-day deal flow from the tracked VC-firm watchlist",
                      className="vc-muted")], className="vc-section-head"),
            html.P("Every deal in the window with a followed firm among its participants, "
                   "newest day first. Search by company, investor, sector or geography.", className="vc-panel-note"),
            dcc.Loading(html.Div(id=VC_FIRMS_FEED, className="vc-firms-feed"),
                        color="#007B63", delay_show=300)], className="vc-card"),
    ])


def _geo_panel():
    return html.Div(id=VC_GEO_PANEL, style={"display": "none"}, children=[
        html.Section([html.Div([html.H3("Funding by geography"),
            html.Span("Company headquarters location · completed deal value",
                      className="vc-muted")], className="vc-section-head"),
            html.Div([html.Div([html.Label("Measure"), dcc.RadioItems(id=VC_GEO_METRIC,
                options=HEATMAP_METRIC_OPTIONS, value="size", inline=True,
                className="vc-segments")])], className="vc-toolbar"),
            dcc.Graph(id=VC_GEO_CHART, config={"displayModeBar": False},
                      style={"height": "360px"})], className="vc-card"),
        html.Section([html.Div([html.H3("Metro breakdown"),
            html.Span("Deals and value by headquarters metro", className="vc-muted")],
            className="vc-section-head"),
            dag.AgGrid(id=VC_GEO_TABLE, columnDefs=[
                {"field": "country", "headerName": "Country", "flex": 1, "minWidth": 160},
                {"field": "metro", "headerName": "Metro", "flex": 1.3, "minWidth": 190},
                {"field": "deals", "headerName": "Deals", "flex": .6, "minWidth": 100,
                 "type": "numericColumn", "cellDataType": "number"},
                {"field": "value", "headerName": "Deal value", "flex": .9, "minWidth": 130,
                 "type": "numericColumn", "cellDataType": "number",
                 "valueFormatter": {"function": "params.value == null || !params.value ? '—' : d3.format('$,.3~s')(params.value).replace('G', 'B').replace('k', 'K')"}},
                {"field": "top_sector", "headerName": "Top sector", "flex": .9, "minWidth": 140},
            ], rowData=[], getRowId="params.data.country + '|' + params.data.metro",
                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                dashGridOptions={"theme": _GRID_THEME, "pagination": True,
                                 "paginationPageSize": 20, "rowHeight": 43, "headerHeight": 40},
                className="vc-grid", style={"height": "560px"})], className="vc-card"),
        html.Section(id="vc-geo-deals", className="vc-card vc-detail",
                     children="Select a country bar or metro row to inspect its deals."),
    ])


def _deals_panel():
    return html.Div(id=VC_DEALS_PANEL, style={"display": "none"}, children=[
        html.Section([
            html.Div([html.H3("Sector activity"), html.Span("Darker cells indicate more activity. Select a cell to explore.",
                     className="vc-muted")], className="vc-section-head"),
            html.Div([
                html.Div([html.Label("Break out by"), dcc.RadioItems(id=VC_HEATMAP_AXIS,
                    options=HEATMAP_AXIS_OPTIONS, value=DEFAULT_AXIS, inline=True, className="vc-segments")]),
            html.Div([html.Label("Measure"), dcc.RadioItems(id=VC_HEATMAP_METRIC,
                    options=HEATMAP_METRIC_OPTIONS, value="count", inline=True, className="vc-segments")]),
            ], className="vc-toolbar"),
            dash_table.DataTable(id=VC_HEATMAP_TABLE, cell_selectable=True,
                style_cell_conditional=[{"if": {"column_id": "sector"}, "fontWeight": "600"}],
                **_TABLE_STYLE),
        ], className="vc-card"),
        html.Section([html.Div(html.H3(id=VC_DRILL_TITLE, children="Select a cell to inspect its deals"),
                     className="vc-section-head"), _deal_grid(VC_DRILL_TABLE, compact=True)], className="vc-card"),
        html.Section(id="vc-sector-deal-detail", className="vc-card vc-detail"),
        html.Section([html.Div([html.H3("Subsector review queue"),
            html.Span("AI buckets it was unsure about — correct the dropdown if needed, then confirm",
                      className="vc-muted")], className="vc-section-head"),
            html.P("Confirmed labels train the classifier: future deals that read like a "
                   "confirmed one inherit its bucket automatically.", className="vc-panel-note"),
            dash_table.DataTable(id=VC_REVIEW_TABLE, editable=True, columns=[
                {"name": "Date", "id": "deal_date", "editable": False},
                {"name": "Company", "id": "company", "editable": False},
                {"name": "Sector", "id": "sector", "editable": False},
                {"name": "Sub-sector (AI guess)", "id": "sub_sector", "presentation": "dropdown"},
                {"name": "Confidence", "id": "sub_sector_confidence", "type": "numeric", "editable": False},
                {"name": "Synopsis", "id": "description", "editable": False},
            ], data=[], style_cell_conditional=[
                {"if": {"column_id": "description"}, "maxWidth": "440px", "whiteSpace": "normal"},
                {"if": {"column_id": "sub_sector"}, "fontWeight": "600"}],
                css=[{"selector": ".Select-menu-outer", "rule": "display: block !important"}],
                **_TABLE_STYLE),
            html.Div([html.Button("Confirm all shown", id=VC_REVIEW_CONFIRM, n_clicks=0,
                                  className="vc-primary"),
                      html.Span(id=VC_REVIEW_STATUS, role="status", className="vc-status")],
                     className="vc-review-actions"),
        ], className="vc-card"),
    ])


def _news_panel():
    return html.Div(id=VC_NEWS_PANEL, style={"display": "none"}, children=[
        html.Section([html.Nav([
            html.Div([
                html.Button([html.Span(className="vc-news-home-icon", **{"aria-hidden": "true"}), "News feed"],
                            id="vc-news-home", n_clicks=0, className="vc-news-home",
                            title="Return to the latest news"),
                html.Span("›", className="vc-news-chevron", **{"aria-hidden": "true"}),
                html.Span("Today", id="vc-news-current", className="vc-news-current", **{"aria-live": "polite"}),
            ], className="vc-news-breadcrumb"),
            html.A([html.Span("Open", className="vc-news-open-label"), html.Span("↗", **{"aria-hidden": "true"})],
                   id="vc-news-open", href="/vcnews/default", target="_blank", rel="noopener",
                   className="vc-news-open", title="Open this page in a new tab",
                   **{"aria-label": "Open this page in a new tab"}),
            ], className="vc-news-nav", **{"aria-label": "News navigation"}),
            dcc.Store(id="vc-news-navigation"),
            html.Iframe(id="vc-news-frame", title="VC news and research", className="vc-news-frame")],
            className="vc-card vc-news-card"),
    ])


def _sources_panel():
    return html.Div(id=VC_SOURCES_PANEL, style={"display": "none"}, children=[
        html.Section([html.Div([html.H3("Source registry"),
            html.Span("Manage coverage and trust", className="vc-muted")], className="vc-section-head"),
            html.Div([
                html.Div([html.Label("Source name", htmlFor=VC_SOURCE_NAME),
                          dcc.Input(id=VC_SOURCE_NAME, placeholder="Publication name")]),
                html.Div([html.Label("Type", htmlFor=VC_SOURCE_TYPE),
                          dcc.Dropdown(id=VC_SOURCE_TYPE, options=[{"label": t.replace("_", " ").title(), "value": t} for t in SOURCE_TYPES],
                                       value="blog", clearable=False)]),
                html.Div([html.Label("Feed URL or handle", htmlFor=VC_SOURCE_URL),
                          dcc.Input(id=VC_SOURCE_URL, placeholder="https://…")]),
                html.Div([html.Label("Trust · 0–1", htmlFor=VC_SOURCE_TRUST),
                          dcc.Input(id=VC_SOURCE_TRUST, type="number", value=0.7, min=0, max=1, step=0.05)]),
                html.Button("Add source", id=VC_SOURCE_ADD_BTN, n_clicks=0, className="vc-primary"),
            ], className="vc-source-form"),
            html.Div(id=VC_SOURCES_STATUS, role="status", className="vc-status"),
            dash_table.DataTable(id=VC_SOURCES_TABLE, editable=True, row_deletable=True,
                sort_action="native", filter_action="native", columns=[
                    {"name": "Name", "id": "name", "editable": False},
                    {"name": "Type", "id": "type", "editable": False},
                    {"name": "URL / handle", "id": "url_or_handle"},
                    {"name": "Trust", "id": "trust_weight", "type": "numeric"},
                    {"name": "Sector", "id": "sector_hint"},
                    {"name": "Enabled", "id": "enabled", "presentation": "dropdown"},
                    {"name": "Last success", "id": "last_ok_at", "editable": False},
                    {"name": "Last error", "id": "last_error", "editable": False},
                ], dropdown={"enabled": {"options": [{"label": "Enabled", "value": True},
                                                     {"label": "Disabled", "value": False}]}},
                **_TABLE_STYLE),
        ], className="vc-card"),
    ])


def create_vc_dashboard_layout(tab=TAB_FUNDING, entity=None, name=None, selection=None):
    selection = selection or {}
    tab = tab if tab in VC_TAB_VALUES else TAB_FUNDING
    if entity in {"company", "investor", "deal"} and name:
        tab = TAB_NEWS
    window = str(selection.get("window", DEFAULT_WINDOW))
    window = int(window) if window in {str(option["value"]) for option in WINDOW_OPTIONS} else DEFAULT_WINDOW
    minimum = str(selection.get("min", 0))
    minimum = next((option["value"] for option in BACKERS_MIN_SIZE_OPTIONS
                    if str(option["value"]) == minimum or str(int(option["value"])) == minimum), 0)
    return [], html.Div([
        dcc.Store(id="vc-tab-url"),
        html.Header([html.Div([html.Span("PRIVATE MARKETS / VENTURE", className="vc-eyebrow"),
            html.H2("Venture capital"), html.P("Spot changes in funding, compare activity and follow the evidence."),
            dcc.Link("Subscribe by email", id="vc-email-subscribe", href="/email-subscriptions?report=vc_deals")]),
            html.Div([html.Span("LATEST DEAL", className="vc-eyebrow"), html.Strong(id="vc-latest-deal")], className="vc-asof")],
            className="vc-header"),
        html.Div([
            html.Div([html.Label("Window"), dcc.RadioItems(id=VC_WINDOW, options=WINDOW_OPTIONS, value=window,
                           inline=True, className="vc-segments")]),
            html.Div([html.Label("Search the deal universe", htmlFor="vc-search"),
                      dcc.Input(id="vc-search", type="search", value=selection.get("q", ""), debounce=.3,
                                placeholder="Company, investor, sector, location…", className="vc-search")], className="vc-search-control"),
            html.Div([html.Label("Minimum deal size", htmlFor="vc-min-size"),
                      dcc.Dropdown(id="vc-min-size", options=BACKERS_MIN_SIZE_OPTIONS, value=minimum,
                                   clearable=False, className="vc-min-size")]),
            dcc.Checklist(id=VC_APOLLO, options=[{"label": "Apollo-relevant only", "value": "apollo"}],
                          value=["apollo"] if selection.get("apollo") == "1" else [],
                          inline=True, className="vc-apollo")], className="vc-toolbar"),
        dcc.Loading(html.Div(id=VC_SUMMARY, className="vc-metrics"), color="#007B63", delay_show=300),
        html.P(id="vc-coverage", className="vc-coverage", role="status"),
        dcc.Tabs(id=VC_TABS, value=tab, className="vc-tabs", children=[
            dcc.Tab(label=label, value=value, className="vc-tab", selected_className="vc-tab-selected")
            for label, value in [("Deals & trends", TAB_FUNDING), ("New deals", TAB_FIRMS),
                                 ("Company activity", "relationships"), ("Investor activity", "portfolio"),
                                 ("Sector map", TAB_DEALS), ("Geography", TAB_GEO),
                                 ("News & research", TAB_NEWS), ("Sources", TAB_SOURCES)]
        ]),
        dcc.Loading(html.Div([_funding_panel(), _firms_panel(), _deals_panel(), _geo_panel(),
                              _news_panel(), _sources_panel(),
            html.Div(id="vc-activity-panel", style={"display": "none"}, children=[
                html.Section(className="vc-card", children=[
                    html.Div([html.H3(id="vc-activity-title"),
                              html.Span(id="vc-activity-caption", className="vc-muted")], className="vc-section-head"),
                    html.Div([html.Label("Rank by", htmlFor="vc-activity-sort"),
                              dcc.RadioItems(id="vc-activity-sort", inline=True, className="vc-segments",
                                             value=selection.get("sort", "deals") if selection.get("sort", "deals") in {"deals", "value", "momentum", "recent"} else "deals",
                                             options=[{"label": label, "value": value} for label, value in [
                                                 ("Number of deals", "deals"), ("Deal value", "value"),
                                                 ("Change in deal count", "momentum"), ("Most recent", "recent")]])], className="vc-toolbar"),
                    html.P("Change compares the previous equal-length window. Top five counterparties are ranked by shared deals, then completed round value. "
                           "Values describe entire rounds, not investor check sizes. Select a name for its wiki, news and complete recorded deal history.", className="vc-panel-note"),
                    dcc.Loading(html.Div(id="vc-activity-list", className="vc-firms-feed"),
                                color="#007B63", delay_show=300)])])],
                            id="vc-overview", className="vc-panels"), color="#007B63", delay_show=300),
    ], className="vc-dashboard")


def get_vc_dashboard_headers(app):
    return []
