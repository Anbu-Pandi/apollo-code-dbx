import dash_core_components as dcc
import dash_html_components as html
from dash import Input, Output, State, ALL, MATCH, callback
import logging
from dash_reports.report_common.data.config import \
    FUND_CODE_LIST
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import get_datepicker_control, create_link
from dash_reports.report_common.utils.dates import reportdates

import dash_reports


def create_riskpage_layout(app, riskpage, fund):
    fund = fund.split(',')
    if not app is None:
        app.title = 'Risk Report'
    if riskpage.startswith("attribution-index"):
        return dash_reports.risk_report.pages.index_attribution.create_layout(app, fund)
    elif riskpage.startswith("charts-sankey"):
        return dash_reports.risk_report.pages.sankey.create_layout(app, fund)
    elif riskpage.startswith("charts-beta"):
        return dash_reports.risk_report.pages.beta_charts.create_layout(app, fund)
    elif riskpage.startswith("attribution-pnl"):
        return dash_reports.risk_report.pages.attribution_pnl.create_layout(app, fund)
    elif riskpage.startswith("rolling-pnl"):
        return dash_reports.risk_report.pages.rolling_pnl.create_layout(app, fund)
    elif riskpage.startswith("attribution-sector"):
        return dash_reports.risk_report.pages.attribution_sector.create_layout(app, fund)
    elif riskpage.startswith("attribution-rating"):
        return dash_reports.risk_report.pages.attribution_rating.create_layout(app, fund)
    elif riskpage.startswith("beta-contributors"):
        return dash_reports.risk_report.pages.beta_contributors.create_layout(app, fund)
    elif riskpage.startswith("index-static-dynamic-beta"):
        return dash_reports.risk_report.pages.benchmark_beta_charts.create_layout()
    elif riskpage.startswith("risk-details"):
        return dash_reports.risk_report.pages.risk_details.create_layout(app, fund)
    elif riskpage.startswith("pnl-details"):
        return dash_reports.risk_report.pages.pnl_details.create_layout(app, fund)
    elif riskpage.startswith("beta-by-diff/summary"):
        return html.Div([get_beta_by_diff_menu(",".join(fund)),
                         dash_reports.risk_report.pages.beta_by_index.create_layout(app, fund)])
    elif riskpage.startswith("beta-by-diff/pivot-summary"):
        return html.Div([get_beta_by_diff_menu(",".join(fund)),
                         dash_reports.risk_report.pages.beta_diff_pivot_summary.create_layout(
                             app, fund)])
    elif riskpage.startswith("risk-summary"):
        return html.Div([get_risk_overview_menu(",".join(fund)),
                         dash_reports.risk_report.pages.risk.create_layout(app, fund)])
    elif riskpage.startswith("risk-stress-scenario"):
        return html.Div([get_risk_overview_menu(",".join(fund)),
                         dash_reports.risk_report.pages.risk.create_stress_layout(app, fund)])
    elif riskpage.startswith("full-view"):
        return (dash_reports.risk_report.pages.index_attribution.create_layout(app, fund),
                dash_reports.risk_report.pages.sankey.create_layout(app, fund),
                dash_reports.risk_report.pages.beta_charts.create_layout(app, fund),
                dash_reports.risk_report.pages.attribution_pnl.create_layout(app, fund),
                dash_reports.risk_report.pages.risk.create_layout(app, fund, hidepivotcontrols=True),
                dash_reports.risk_report.pages.rolling_pnl.create_layout(app, fund),
                dash_reports.risk_report.pages.attribution_sector.create_layout(app, fund),
                dash_reports.risk_report.pages.attribution_rating.create_layout(app, fund),
                dash_reports.risk_report.pages.beta_contributors.create_layout(app, fund),
                dash_reports.risk_report.pages.beta_by_index.create_layout(app, fund),)
    else:
        return dash_reports.risk_report.pages.index_attribution.create_layout(app, fund)


def get_risk_header():
    return html.Div([get_page_title("Beta Attribution Report"),
                     html.Br(),
                     dates_section(),
                     html.Br(),
                     ])


def get_risk_links(page, fund=None):
    fund = fund or 'ALL'
    menu = html.Div([create_link('risk-report', 'overview', 'Overview', f'fund={fund}', page),
                     create_link('risk-report', 'attribution-index', 'Index Attribution', f'fund={fund}', page),
                     create_link('risk-report', 'rolling-pnl', 'Issuer PnL', f'fund={fund}', page),
                     create_link('risk-report', 'beta-contributors', 'Issuer Beta Contribution',
                                 f'fund={fund}', page),
                     create_link('risk-report', 'attribution-pnl', 'Attribution - PnL', f'fund={fund}', page),
                     create_link('risk-report', 'attribution-sector', 'Attribution - Sector', f'fund={fund}', page),
                     create_link('risk-report', 'attribution-rating', 'Attribution - Rating', f'fund={fund}', page),
                     create_link('risk-report', 'charts-beta', 'Fund Beta', f'fund={fund}', page),
                     create_link('risk-report', 'beta-by-diff/summary', 'Beta Diff', f'fund={fund}', page),
                     create_link('risk-report', 'index-static-dynamic-beta', 'Index Beta', f'fund={fund}', page),
                     create_link('risk-report', 'charts-sankey', 'Sanky Charts', f'fund={fund}', page),
                     create_link('risk-report', 'risk-summary', 'Risk Overview', f'fund={fund}', page),
                     create_link('risk-report', 'risk-details', 'Risk Details', f'fund={fund}', page),
                     create_link('risk-report', 'pnl-details', 'Pnl Details', f'fund={fund}', page),
                     create_link('risk-report', 'full-view', 'Full View', f'fund={fund}', page), ],
                    className="row all-tabs", )
    return menu


def get_beta_by_diff_menu(fund=None):
    fund = fund or 'ALL'
    menu = html.Div(id='beta_by_diff_menu', children=[
        create_link('risk-report', 'beta-by-diff/summary', 'Summary', f'fund={fund}'),
        create_link('risk-report', 'beta-by-diff/pivot-summary', 'Pivot Summary', f'fund={fund}'), ],
                    className="beta-by-diff-tabs ")
    return menu


def get_risk_overview_menu(fund=None):
    fund = fund or 'ALL'
    menu = html.Div(id='risk_overview_menu', children=[dcc.Link("Summary",
                                                                href=f"/risk-report/risk-summary?fund={fund}",
                                                                className="tab first", ),
                                                       dcc.Link("Stress Scenario",
                                                                href=f"/risk-report/risk-stress-scenario?fund={fund}",
                                                                className="tab", ), ],
                    className="beta-by-diff-tabs")
    return menu


def get_fund_dropdown(selectedvalues=None):
    selectedvalues = selectedvalues or 'ALL'
    selectedvalues = selectedvalues.split(',')
    option_list = [{'label': 'ALL', 'value': 'ALL'}]
    for fund in FUND_CODE_LIST:
        option_list.append({'label': fund, 'value': fund})

    fund_menu = html.Div(id='get_fund_dropdown',
                         className='fund_filter',
                         children=[html.Div(style=dict(display='flex'),
                                            children=[html.Div(
                                                [html.Label("{:<25}".format("Select Funds:"),
                                                            className='subtitle')],
                                                style=dict(width='20%')),
                                                html.Div([dcc.Dropdown(options=option_list,
                                                                       value=selectedvalues,
                                                                       multi=True,
                                                                       id='fund'),
                                                          html.Div(id='fund_output_container')],
                                                         style=dict(width='80%'))
                                            ]),
                                   html.Div(
                                       [html.Button('Filter', id='fund-filter-submit',
                                                    className='submit-button')])])

    return fund_menu


def dates_section():
    (attrib_start_date, attrib_end_date, prev_risk_date, year_start_date, risk_year_start_date) = \
        reportdates.get_report_folder_dates(datastore.reportdt, datastore.reportstartdt, datastore.reportenddt)
    dates = html.Div(id="dates",
                     children=["Risk Start Date: ", get_datepicker_control('startdt', attrib_start_date),
                               "Risk End Date: ", get_datepicker_control('enddt', attrib_end_date),
                               "Curr. Risk Date: ", get_datepicker_control('riskdt', attrib_end_date),
                               "Prev. Risk Date: ", get_datepicker_control('prevriskdt', prev_risk_date), ],
                     style={'display': 'inline-block'},
                     className='row all-tabs')
    return dates


def get_fund_based_header_elements(page, fund=None):
    return [get_risk_links(page, fund), get_fund_dropdown(fund)]


def create_risk_specific_page_layout(page, fund):
    return html.Div(id='risk-page-content',
                    children=get_fund_based_header_elements(page, fund))


@callback(Output("risk-page-content", "children"),
          Input("fund-filter-submit", "n_clicks"),
          State('fund', 'value'),
          State("url", "pathname"),
          State("url", "search"), )
def on_fundselect_submit(n_clicks, fund, pathname, search):
    fund = ",".join(fund)
    page = pathname.split('risk-report/')[-1]
    logging.info(f'on fund select for path: {pathname} and fund: {fund}')

    div_kids = get_fund_based_header_elements(page, fund)
    if 'risk-report/' in pathname:
        div_kids.append(create_riskpage_layout(None, page, fund))

    return html.Div(children=div_kids)


if __name__ == '__main__':
    pass
