from dash_reports.etf_apo_funds_report.pages.components import *
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.etf_apo_funds_report.data.etf_apo_fund_comparison import get_etf_fund_comparison, main
from dash import Input, Output, State, ALL, MATCH, callback


def create_layout():
    layout = html.Div([get_page_title("ETF vs APO Funds"),
                       html.Br(),
                       get_date_section(),
                       html.Br(),
                       get_fund_dropdown(),
                       html.Br(),
                       get_etf_funds_dropdown(),
                       html.Br(),
                       get_submit_button(),
                       html.Br(),
                       get_results_links(),
                       html.Br(),
                       html.Div(id='etf-analysis-output-container'),
                       ])
    return layout


def create_overlap_analysis_layout(dt, etf_fund, apo_fund, key):
    dt = date.fromisoformat(dt)
    result = main(dt, etf_fund, apo_fund, key)
    return html.Div(get_results_html(apo_fund, etf_fund, result, key))


@callback(
    Output('etf-analysis-output-container', 'children'),
    Input("search_button", "n_clicks"),
    State('as_of_date_control', 'date'),
    State('apo_fund', 'value'),
    State('etf_fund', 'value'),
    State('url', "pathname"),
    prevent_initial_call=True
)
def etf_vs_apo_funds(n_clicks, start_date, apo_fund, etf_fund, pathname):
    if 'etf-vs-apo-funds/' in pathname:
        return create_overlap_analysis_layout(start_date, etf_fund, apo_fund,
                                              pathname.split('etf-vs-apo-funds/')[-1])
    return []
