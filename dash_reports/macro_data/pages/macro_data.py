import pandas as pd
import dash_html_components as html
import dash_core_components as dcc
import dash
import json
import dash_reports.macro_data.data.price_spread_volume as data

from dash_reports.report_common.pages.link_and_headers import get_page_title
import dash_reports.macro_data.pages.index_price_volume as page_content
from common.utils.dtutil import today, dateadd
from dash_reports.report_common.utils.components import get_datepicker_control
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import make_dash_table

MACRO_DATA_OUTPUT_DIV = 'macro_data_output'
MACRO_DATA_SUBMIT_ID = 'macro_data_submit'
MACRO_DATA_DATE_ID = 'asof_date'


def create_macro_data_layout():
    dt = dateadd(today(), '-1k')
    static_layout = html.Div([html.Br(),
                              html.Div(
                                  children=[
                                      html.Div([html.Label('As Of Date', className='subtitle')],
                                               style={"margin-right": "5%"}),
                                      get_datepicker_control(dt=dt, id=MACRO_DATA_DATE_ID, disabled=False),
                                  ],
                                  style={'display': 'flex'},
                                  className='fund_filter'),
                              html.Br(),
                              html.Div(html.Button('Submit', id=MACRO_DATA_SUBMIT_ID, className='submit-button'),
                                       className='fund_filter'),
                              html.Br()
                              ])
    dynamic_layout = html.Div(children=[], className="page", id=MACRO_DATA_OUTPUT_DIV)
    return static_layout, dynamic_layout


def get_macro_data_headers(app=None):
    if app is not None:
        app.title = 'Macro Data'
    layout = html.Div([get_page_title("Macro Data"),
                       html.Br(),
                       ])
    return layout


def generate_macro_data_result_tabs(asof_date):
    df = data.get_price_spread_volume_df(asof_date)
    cdx_ex_default_df = data.get_cdx_ex_default(list(data.CDX_TICKERS_SHORT_NAME.keys()), asof_date)
    otr_hist_df = data.get_otr_data(tenor='5Y')

    tab_kids = []
    for k, v in data.CDX_TICKERS_SHORT_NAME.items():
        tab = page_content.create_index_tab(asof_date, df, otr_hist_df, cdx_ex_default_df, v, k)
        tab_kids.append(dcc.Tab(label=v, children=tab))

    return dcc.Tabs(tab_kids)


def get_cdx_ex_default_tbl(ticker, dt):
    df = data.get_cdx_ex_default(ticker, dt)
    df = df[df['asof_date'] == dt]
    df = df.sort_values('apo_ex_default_ticker')
    df.columns = [dfhelpers.ui_column_name(x) for x in df.columns]
    return [html.Div(make_dash_table(df,
                                     cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                     txt_col_width='150px',
                                     num_col_width='125px',
                                     fixed_columns=6
                                     ),
                     className='list')]
