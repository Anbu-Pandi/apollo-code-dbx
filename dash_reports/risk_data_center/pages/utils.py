from datetime import date
import dash_html_components as html
import dash_core_components as dcc
import pandas as pd

from common.utils.dtutil import today
from dash_reports.report_common.utils.components import make_dash_table
from data_access.dbtables.risk_pnl_data.pnl_risk_enhanced import get_max_risk_date, get_apo_fund_list
from data_access.beta_attribution.ice_index import get_ice_index_ticker
from data_access.dbtables.index_data.etf_constituents import get_etf_index_ticker
from dash_reports.risk_data_center import pages

risk_metric_list = ['Net & Gross Exposure', 'Modified Duration', 'Spread Duration', 'Theta Yield', 'Stress Scenarios',
                    'Unlevered YTW', 'Beta Contribution']

data_for_download_list = ['Risk Enhanced', 'ICE Index Holdings', 'ETF Index Holdings']

other_data = ['Risk Enhanced']

risk_data_list = risk_metric_list + other_data

grpby_list = {'Fund Entity Code': 'fund_entity_code', 'FO Strategy': 'fo_strategy',
              'FO Strategy Broad Category': 'fo_strategy_broad_category', 'Strategy': 'strategy',
              'Issuer Ult': 'issuer_ult'}


def get_datepicker_control(dt=date(2000, 1, 1), id='my-date-picker-single', max_date=today()):
    return dcc.DatePickerSingle(
        id=id,
        min_date_allowed=date(2018, 1, 1),
        max_date_allowed=max_date,
        initial_visible_month=dt,
        date=dt,
        style={"margin-right": "100px", "margin_down": "30px"}
    )


def get_label(string):
    return html.Div([html.Label(string,
                                className='subtitle')],
                    style={"margin-right": "5%"},
                    )


def get_date_section():
    # dt = get_max_risk_date()
    dt = today()
    return html.Div(id="date-input-controls",
                    children=[get_label('Start Date'),
                              get_datepicker_control(dt=dt, id='start_date_control', max_date=dt),
                              get_label('End Date'),
                              get_datepicker_control(dt=dt, id='end_date_control', max_date=dt),
                              ],
                    style={'display': 'flex'},
                    className='fund_filter')


def get_data_button():
    return html.Div(className='fund_filter',
                    children=[html.Button('Get Data', id='risk_data_center_submit_button',
                                          className='submit-button')],
                    style={'width': '50%'})


def get_fund_label_default_value(data):
    if data == 'Risk Enhanced':
        fund_list = get_apo_fund_list()
        label = 'APO Fund'
        default_value = 'CSTRAT'
        multi_flag = True
    elif data == 'ETF Index Holdings':
        fund_list = get_etf_index_ticker()
        label = 'Benchmark'
        default_value = 'HYG'
        multi_flag = False
    elif data == 'ICE Index Holdings':
        fund_list = get_ice_index_ticker()
        label = 'Benchmark'
        default_value = 'H0A0'
        multi_flag = False

    return fund_list, label, default_value, multi_flag


def get_fund_dropdown(data='Risk Enhanced', display='flex'):
    option_list = []

    if data:
        fund_list, label, default_value, multi_flag = get_fund_label_default_value(data)
        fund_list.sort()
        for fund in fund_list:
            option_list.append({'label': fund, 'value': fund})
    else:
        label = ''
        default_value = ''
        multi_flag = False

    return html.Div(id='get_fund_dropdown',
                    className='fund_filter',
                    children=[html.Div(style=dict(display=display),
                                       children=[get_label(label),
                                                 html.Div([dcc.Dropdown(options=option_list,
                                                                        value=default_value,
                                                                        multi=multi_flag,
                                                                        id='fund'),
                                                           html.Div(id='fund_output_container')],
                                                          style=dict(width='20%'))
                                                 ])])


def get_grpby_dropdown(display='flex'):
    option_list = []

    for key, value in grpby_list.items():
        option_list.append({'label': key, 'value': value})
    return html.Div(id='get_grpby_dropdown',
                    className='fund_filter',
                    children=[html.Div(style=dict(display=display),
                                       children=[get_label('Group By  '),
                                                 html.Div([dcc.Dropdown(options=option_list,
                                                                        value='fund_entity_code',
                                                                        multi=False,
                                                                        id='grpby'),
                                                           html.Div(id='grpby_selection_container')],
                                                          style=dict(width='20%'))
                                                 ])])


def get_data_center_links():
    menu = html.Div([dcc.Link("Risk Data", href="/data-center/risk-data", className="tab first", ),
                     dcc.Link("ETF Holdings", href="/data-center/etf-holdings", className="tab", ),
                     dcc.Link("ICE Holdings", href="/data-center/ice-holdings", className="tab", ),
                     dcc.Link("Benchmark Beta to CDXHY", href="/data-center/benchmark-beta-to-cdxhy",
                              className="tab", ),
                     dcc.Link("SPAC Beta", href="/data-center/spac-beta",
                              className="tab", ),
                     ],
                    className="row all-tabs", )
    return menu


def get_risk_metrics(default_value='Risk Enhanced', display='flex'):
    option_list = []

    for title in risk_data_list:
        option_list.append({'label': title, 'value': title})

    return html.Div(style=dict(display=display),
                    children=dcc.RadioItems(id='risk_metrics',
                                            options=option_list,
                                            value=default_value,
                                            ),  # labelStyle={'display': 'inline-block'}
                    className="subtitle", )


def create_data_center_layout(app, page):
    if page == "risk-data":
        return pages.risk_metrics.create_risk_metric_layout()
    elif page == "etf-holdings":
        return pages.external_datasets.create_external_data_layout('ETF Index Holdings')
    elif page == "ice-holdings":
        return pages.external_datasets.create_external_data_layout('ICE Index Holdings')
    elif page == "benchmark-beta-to-cdxhy":
        return pages.risk_metrics.create_other_layout('Benchmark Beta to CDXHY')
    elif page == "spac-beta":
        return pages.risk_metrics.create_other_layout('SPAC Beta')
    else:
        return pages.risk_metrics.create_risk_metric_layout()


def get_dash_table(data, fig=None, title=None):
    if isinstance(data, str):
        fig_html = html.H6('Chart Not Prepared')
        val = html.H6(data)
    elif isinstance(data, pd.DataFrame):
        data = data.sort_values('asof_date', ascending=False)
        fig_html = html.Div([dcc.Graph(figure=fig)], className="six columns", style={'display': 'inline-block'})
        val = make_dash_table(data)
    else:
        fig_html = html.H6('Chart Not Prepared')
        val = html.H6(f'Data type not supported {type(data)}')
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        [title], className="subtitle padded"
                                    ),
                                    html.Br(),
                                    fig_html,
                                    html.Br(),
                                    val,
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
