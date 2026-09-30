from datetime import date
import dash_html_components as html
import dash_core_components as dcc
import pandas as pd

from common.utils.dtutil import today
from dash_reports.report_common.data.config import FUND_CODE_LIST
from dash_reports.report_common.utils.components import make_dash_table
from data_access.dbtables.risk_pnl_data.pnl_risk_enhanced import get_max_risk_date

etf_funds = ['LQD', 'VCIT', 'VCSH', 'BSV', 'HYG', 'IGSB', 'JNK', 'IGIB', 'SPSB', 'SPIB', 'USHY', 'HYLB', 'USIG', 'SHYG',
             'VCLT', 'SJNK', 'FLRN', 'SLQD', 'IGLB', 'BSCL', 'BSCM', 'ANGL', 'HYS', 'BKLN']

ice_funds = ['H0A0', 'C0A0']

benchmark_funds = etf_funds + ice_funds
benchmark_funds.sort()

title_list = ['Overlap by Issuer', 'Overlap by Rating', 'Overlap by Industry']

path_dict = {'overlap-by-issuer': 'Overlap by Issuer',
             'overlap-by-rating': 'Overlap by Rating',
             'overlap-by-industry': 'Overlap by Industry'
             }


def get_datepicker_control(dt=date(2021, 8, 2), id='my-date-picker-single', max_date=today()):
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


def get_sub_header(string):
    return html.Div([html.H6(string,
                             className='subtitleheading')],

                    )


def is_business_day(date):
    return bool(len(pd.bdate_range(date, date)))


def get_date_section():
    dt = get_max_risk_date()
    # if ~is_business_day(dt):
    #     dt = dt - BDay()
    #     dt = dt.date()
    return html.Div(id="date-control",
                    children=[get_label('As of Date'),
                              get_datepicker_control(dt=dt, id='as_of_date_control', max_date=dt),
                              ],
                    style={'display': 'flex'},
                    className='fund_filter')


def get_results_links():
    menu = html.Div([dcc.Link("Overlap by Issuer", href="/etf-vs-apo-funds/overlap-by-issuer", className="tab first", ),
                     dcc.Link("Overlap by Rating", href="/etf-vs-apo-funds/overlap-by-rating", className="tab", ),
                     dcc.Link("Overlap by Industry", href="/etf-vs-apo-funds/overlap-by-industry", className="tab", ),
                     ],
                    className="row all-tabs", )
    return menu


def get_submit_button():
    return html.Div(className='fund_filter',
                    children=[html.Button('submit', id='search_button',
                                          className='submit-button')],
                    style={'width': '50%'})


def get_fund_dropdown():
    option_list = []
    for fund in FUND_CODE_LIST:
        option_list.append({'label': fund, 'value': fund})
    return html.Div(id='get_fund_dropdown',
                    className='fund_filter',
                    children=[html.Div(style=dict(display='flex'),
                                       children=[get_label('APO Fund'),
                                                 html.Div([dcc.Dropdown(options=option_list,
                                                                        value='CSTRAT',
                                                                        multi=True,
                                                                        id='apo_fund'),
                                                           html.Div(id='apo_fund_container')],
                                                          style=dict(width='20%'))
                                                 ])])


def get_etf_funds_dropdown():
    option_list = []

    for title in benchmark_funds:
        option_list.append({'label': title, 'value': title})
    return html.Div(id='get_etf_funds_dropdown',
                    className='fund_filter',
                    children=[html.Div(style=dict(display='flex'),
                                       children=[get_label('Benchmark Fund'),
                                                 html.Div([dcc.Dropdown(options=option_list,
                                                                        value='HYG',
                                                                        multi=False,
                                                                        id='etf_fund'),
                                                           html.Div(id='etf_selection_container')],
                                                          style=dict(width='20%'))
                                                 ])])


def _check_isinstance_list(val):
    if not isinstance(val, list):
        val = [val]
    return val


def get_results_html(apo_funds, benchmark_funds, result, header):
    apo_funds = _check_isinstance_list(apo_funds)
    benchmark_funds = _check_isinstance_list(benchmark_funds)

    title_list = path_dict[header]
    sub_heading = get_sub_header(title_list)

    html_code = [html.Div([sub_heading], className='etf_sub_page')]

    for benchmark_fund in benchmark_funds:
        for apo_fund in apo_funds:
            title = html.H6(f'{apo_fund} vs {benchmark_fund}', className="subtitleheading")
            temp_html = []

            if 'issue' in result[f'{benchmark_fund}_{apo_fund}'].keys():
                html_code.append(html.Div([html.Br(), title, html.Br(),
                                           html.H6(result[f'{benchmark_fund}_{apo_fund}']['issue'])],
                                          className='etf_sub_page'))
                continue

            if header == "overlap-by-issuer":
                temp_html = get_html_issuer_overlap(result, benchmark_fund, apo_fund, header, title, temp_html)
            else:
                temp_html = get_html_industry_rating_overlap(result, benchmark_fund, apo_fund, header, title, temp_html)

            html_code.append(html.Div(temp_html, className="etf_sub_page"))

    return html_code


def get_html_issuer_overlap(result, benchmark_fund, apo_fund, header, title, temp_html):
    fig = result[f'{benchmark_fund}_{apo_fund}_chart']
    temp_html = temp_html + [html.Br(), title, html.Br(), html.Div([dcc.Graph(figure=fig)]), html.Br()]
    temp_df = result[f'{benchmark_fund}_{apo_fund}'][header]
    dash_table = make_dash_table(temp_df)
    val = html.Div(
        [
            dash_table,
        ],
        className="six columns",
    )

    temp_html.append(
        html.Div(
            [
                val,
            ],
            className="etf_sub_page ",
        ),
    )

    return temp_html


def get_html_industry_rating_overlap(result, bnchmrk_fund, apo_fund, header, title, temp_html):
    distribution_charts_html = html.Div(
        [
            html.Div(
                [dcc.Graph(figure=result[f'{bnchmrk_fund}_{apo_fund}'][header]['apo_fig'])],
                className="", style={'width': '50%', 'display': 'inline-block'}
            ),
            html.Div(
                [dcc.Graph(figure=result[f'{bnchmrk_fund}_{apo_fund}'][header]['bnchmrk_fig'])],
                className="", style={'width': '50%', 'display': 'inline-block'}
            )
        ],
        style={'width': '100%', 'display': 'inline-block'},
        className=''
    )
    overlap_charts_html = html.Div(
        [html.Div(
            html.Div(
                [dcc.Graph(figure=result[f'{bnchmrk_fund}_{apo_fund}'][header]['long_intersection_fig']),
                 html.Br(),
                 html.Div(
                     [
                         make_dash_table(result[f'{bnchmrk_fund}_{apo_fund}'][header]['long_overlap_df']),
                     ],
                     className="six columns",
                 )
                 ]),
            className="", style={'width': '50%', 'display': 'inline-block'},
        ),
            html.Div(
                html.Div(
                    [dcc.Graph(figure=result[f'{bnchmrk_fund}_{apo_fund}'][header]['short_intersection_fig']),
                     html.Br(),
                     html.Div(
                         [
                             make_dash_table(result[f'{bnchmrk_fund}_{apo_fund}'][header]['short_overlap_df']),
                         ],
                         className="six columns",
                     )
                     ]),
                className="", style={'width': '50%', 'display': 'inline-block'},
            )
        ],
        style={'width': '100%', 'display': 'inline-block'},
        className=''
    )
    temp_html = temp_html + [html.Br(), title, html.Br(), html.Div(distribution_charts_html), html.Br(),
                             html.Div(overlap_charts_html)]
    return temp_html
