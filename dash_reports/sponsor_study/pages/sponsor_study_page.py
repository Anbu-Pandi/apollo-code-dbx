import pandas as pd
from dash import Input, Output, callback, dash_table, dcc, html

from dash_reports.sponsor_study import PAGE_TITLE
from dash_reports.sponsor_study.data.dataset import (
    data_quality,
    debt_issuer_bridge,
    load_local_dataset,
    opportunity_screens,
    reconciliation,
    sponsor_portfolio_detail,
    sponsor_summary,
    website_holding_candidates,
    website_holding_matches,
)


def _table(title: str, df: pd.DataFrame, max_rows: int = 50):
    if df.empty:
        return html.Div([html.H6(title, className="subtitle"), html.Div("No data available.")], className="fund_filter")
    visible = df.head(max_rows).copy()
    return html.Div([
        html.H6(f"{title} ({len(df):,} rows)", className="subtitle"),
        dash_table.DataTable(
            data=visible.to_dict("records"),
            columns=[{"name": c, "id": c} for c in visible.columns],
            page_size=25,
            sort_action="native",
            filter_action="native",
            style_table={"overflowX": "auto"},
        ),
    ], className="fund_filter")


def _metric_card(label: str, value):
    return html.Div([
        html.Div(label, className="subtitle"),
        html.H4(value),
    ], style={"width": "18%", "display": "inline-block", "padding": "10px"})


def create_layout():
    dataset = load_local_dataset()
    summary = sponsor_summary(dataset)
    screens = opportunity_screens(dataset)
    dq = data_quality(dataset)
    recon = reconciliation(dataset)
    stage = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame())

    total_companies = 0 if stage.empty else stage["portco_id"].nunique()
    active_companies = 0 if stage.empty else int(stage["current_status"].astype("string").str.lower().eq("active").sum())
    at_risk = 0 if screens.empty else int(screens[screens["screen_name"].isin(["stressed", "distressed"])]["portco_id"].nunique())
    screen_count = 0 if screens.empty else screens["screen_name"].nunique()

    screen_options = []
    if not screens.empty:
        screen_options = [{"label": x, "value": x} for x in sorted(screens["screen_name"].dropna().unique())]
    sponsor_options = []
    if not summary.empty:
        sponsor_options = [{"label": x, "value": x} for x in sorted(summary["sponsor"].dropna().unique())]

    return html.Div([
        html.H3(PAGE_TITLE),
        html.Br(),
        html.Div([
            _metric_card("Companies", f"{total_companies:,}"),
            _metric_card("Active Investments", f"{active_companies:,}"),
            _metric_card("At-Risk Names", f"{at_risk:,}"),
            _metric_card("Screens", f"{screen_count:,}"),
            _metric_card("DQ Observations", f"{len(dq):,}"),
        ], className="fund_filter"),
        html.Div([
            html.H6("Sponsor Filter", className="subtitle"),
            dcc.Dropdown(id="sponsor-study-sponsor-filter", options=sponsor_options),
        ], className="fund_filter"),
        html.Div([
            html.H6("Screen Filter", className="subtitle"),
            dcc.Dropdown(id="sponsor-study-screen-filter", options=screen_options, multi=True),
        ], className="fund_filter"),
        dcc.Tabs([
            dcc.Tab(label="Sponsor Overview", children=[
                _table("Sponsor Summary", summary, max_rows=100),
                html.Div(id="sponsor-study-portfolio-detail", children=_table("Portfolio Detail", sponsor_portfolio_detail(dataset), max_rows=200)),
            ]),
            dcc.Tab(label="Website Holdings", children=[
                html.Div(id="sponsor-study-website-candidates", children=_table("Website Holding Candidates", website_holding_candidates(dataset), max_rows=200)),
                html.Div(id="sponsor-study-website-matches", children=_table("Website Candidate Matches", website_holding_matches(dataset), max_rows=200)),
            ]),
            dcc.Tab(label="Opportunity Screens", children=[
                html.Div(id="sponsor-study-screen-table", children=_table("Opportunity Screens", screens, max_rows=200)),
            ]),
            dcc.Tab(label="Data Quality", children=[
                _table("Validation Reconciliation", recon, max_rows=100),
                _table("Data Quality Observations", dq, max_rows=200),
                _table("Debt Issuer Bridge", debt_issuer_bridge(dataset), max_rows=200),
            ]),
        ]),
    ])


@callback(
    Output("sponsor-study-screen-table", "children"),
    Input("sponsor-study-screen-filter", "value"),
    Input("sponsor-study-sponsor-filter", "value"),
)
def update_screen_table(selected_screens, selected_sponsor):
    screens = opportunity_screens()
    if selected_screens:
        screens = screens[screens["screen_name"].isin(selected_screens)]
    if selected_sponsor and not screens.empty:
        screens = screens[screens["sponsor"].astype("string").eq(str(selected_sponsor))]
    return _table("Opportunity Screens", screens, max_rows=200)


@callback(
    Output("sponsor-study-portfolio-detail", "children"),
    Output("sponsor-study-website-candidates", "children"),
    Output("sponsor-study-website-matches", "children"),
    Input("sponsor-study-sponsor-filter", "value"),
)
def update_sponsor_detail(selected_sponsor):
    dataset = load_local_dataset()
    return (
        _table("Portfolio Detail", sponsor_portfolio_detail(dataset, selected_sponsor), max_rows=200),
        _table("Website Holding Candidates", website_holding_candidates(dataset, selected_sponsor), max_rows=200),
        _table("Website Candidate Matches", website_holding_matches(dataset, selected_sponsor), max_rows=200),
    )

