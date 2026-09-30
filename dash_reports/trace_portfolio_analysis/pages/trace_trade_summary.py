import pandas as pd
import dash_html_components as html
import dash_core_components as dcc
import dash_reports.trace_portfolio_analysis.data.portfolio_trades as data
import dash_reports.trace_portfolio_analysis.pages.summary as summary_page
import dash_reports.trace_portfolio_analysis.pages.ig_trades as ig_trades
import dash_reports.trace_portfolio_analysis.pages.hy_trades as hy_trades

from dash_reports.report_common.pages.link_and_headers import get_page_title
from common.utils.dtutil import today, dateadd
from dash_reports.report_common.utils.components import get_datepicker_control, create_link
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import make_dash_table
import dash_reports.trace_portfolio_analysis.pages.utils as page_util
import common.utils.dtutil as dtutil

TRD_SUMMARY_OUTPUT_DIV = 'trace_trd_summary_output'
TRD_SUMMARY_SUBMIT_ID = 'trace_trd_summary_submit'
TRD_SUMMARY_FROM_DT = 'trace_trd_summary_from_dt'
TRD_SUMMARY_TO_DT = 'trace_trd_summary_to_dt'


def create_trade_summary_layout():
    to_dt = dateadd(today(), '-1k')
    from_dt = dateadd(to_dt, '-21b')

    static_layout = html.Div([html.Br(),
                              html.Div(
                                  children=[
                                      html.Div([html.Label('From Date', className='subtitle')],
                                               style={"margin-right": "5%"}),
                                      get_datepicker_control(dt=from_dt, id=TRD_SUMMARY_FROM_DT, disabled=False),
                                  ],
                                  style={'display': 'flex'},
                                  className='fund_filter'),
                              html.Br(),
                              html.Div(
                                  children=[
                                      html.Div([html.Label('To Date', className='subtitle')],
                                               style={"margin-right": "5%"}),
                                      get_datepicker_control(dt=to_dt, id=TRD_SUMMARY_TO_DT, disabled=False),
                                  ],
                                  style={'display': 'flex'},
                                  className='fund_filter'),

                              html.Div(html.Button('Submit', id=TRD_SUMMARY_SUBMIT_ID, className='submit-button'),
                                       className='fund_filter'),
                              html.Br()
                              ])
    dynamic_layout = html.Div(children=[], className="page", id=TRD_SUMMARY_OUTPUT_DIV)
    return static_layout, dynamic_layout


def get_trade_summary_headers(app=None):
    if app is not None:
        app.title = 'Trace Trade Summary'
    layout = html.Div([get_page_title("Trace Trade Summary"),
                       html.Br()
                       ])
    return layout


def create_tab_layout(df, ice_index_summary_df, index, from_dt):
    if index == 'Summary':
        layout = summary_page.create_summary_category_layout(df, ice_index_summary_df, from_dt)
    elif index == 'IG':
        layout = ig_trades.create_ig_layout(df, ice_index_summary_df, from_dt)
    elif index == 'HY':
        layout = hy_trades.create_hy_layout(df, ice_index_summary_df, from_dt)
    else:
        layout = []

    return layout


def generate_header_tabs(from_dt, to_dt):
    dt_6m = dtutil.dateadd(to_dt, '-6m')
    df = data.get_trade_summary(dt_6m, to_dt, is_portfolio_trade=False)
    ice_index_summary_df = data.get_ice_index_summary(dt_6m, to_dt)
    tab_kids = []
    for tab_name in ['Portfolio Trades', 'Non Portfolio Trades']:
        if tab_name == 'Portfolio Trades':
            df_temp = df.loc[~df['portfolio_trade_index'].isin(['etrade', 'vtrade']), :]
        else:
            df_temp = df.loc[df['portfolio_trade_index'].isin(['etrade', 'vtrade']), :]

        tab = generate_child_tabs(df_temp, ice_index_summary_df, from_dt)
        tab_kids.append(dcc.Tab(label=tab_name, children=tab))

    return dcc.Tabs(tab_kids)


def generate_child_tabs(df, ice_index_summary_df, from_dt):
    tab_kids = []
    for tab_name in ['Summary', 'IG', 'HY']:
        tab_label = tab_name
        tab = create_tab_layout(df, ice_index_summary_df, tab_name, from_dt)
        tab_kids.append(dcc.Tab(label=tab_label, children=tab))

    return dcc.Tabs(tab_kids)


def get_apo_dv01_tbl(key, dt):
    df = data.get_trade_summary(dt, dt)
    df_filter = df[df['aggregate_level'] == key]
    df_cat_buy, df_cat_sell = data.get_apo_dv01_df(df_filter)

    df_cat_buy = df_cat_buy[page_util.DISPLAY_COLS]
    df_cat_sell = df_cat_sell[page_util.DISPLAY_COLS]
    df_cat_buy.columns = [dfhelpers.ui_column_name(x) for x in df_cat_buy.columns]
    df_cat_sell.columns = [dfhelpers.ui_column_name(x) for x in df_cat_sell.columns]

    dash_table_layout = [html.Div([html.H3('Buy Side', className="subtitle padded"),
                                   html.Br(),
                                   make_dash_table(df_cat_buy,
                                                   cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                   txt_col_width='150px',
                                                   num_col_width='125px',
                                                   fixed_columns=2
                                                   ),
                                   ], style={'display': 'inline-block', 'width': '48%', 'margin-right': '2%'}),
                         html.Div([html.H3('Sell Side', className="subtitle padded"),
                                   html.Br(),
                                   make_dash_table(df_cat_sell,
                                                   cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                   txt_col_width='150px',
                                                   num_col_width='125px',
                                                   fixed_columns=2
                                                   ),
                                   ], style={'display': 'inline-block', 'width': '48%'})]

    data_tbl_list = [html.Div([html.Br(),
                               html.Div(dash_table_layout),
                               html.Br()
                               ])
                     ]
    return data_tbl_list
