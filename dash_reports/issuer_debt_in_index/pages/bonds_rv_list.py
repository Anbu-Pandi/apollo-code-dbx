from dash import dcc

import pandas as pd

import dash_html_components as html
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils import dfhelpers

from ...issuer_debt_in_index.data import bond_rv
from ...issuer_debt_in_index.pages import utils

ISSUER_INPUT_ID = 'bonds-rv-list-issuer-input'
INDUSTRY_INPUT_ID = 'bonds-rv-list-industry-input'
MATURITY_INPUT_ID = 'bonds-rv-list-maturity-input'

CHECK_LABELS = {'ticker': 'Same Issuer Ticker', 'maturity_bucket': 'Same Maturity',
                'secured_unsecured': 'Same Seniority', 'currency': 'Same Currency',
                'callable': 'Same Callability Feature'}
CHECK_ID = 'bonds-rv-list-check'

SUBMIT_ID = 'bonds-rv-submit'

RESULT_ID = 'bonds-rv-result'


def create_layout(page):
    static_kids = [utils.get_debt_view_index_menu(page)]

    issuer_input = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Issuer:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=ISSUER_INPUT_ID, type='search', className='input')],
                                    style=dict(width='30%', height='35px'))])
    industry_input = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Industry:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=INDUSTRY_INPUT_ID, type='search', className='input')],
                                    style=dict(width='30%', height='35px'))])

    maturity_input = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Maturity:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=MATURITY_INPUT_ID, type='search', className='input')],
                                    style=dict(width='30%', height='35px'))])

    constraints_check = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div(children=[dcc.Checklist(id=CHECK_ID,
                                                            options=[{'label': l, 'value': i} for i, l in
                                                                     CHECK_LABELS.items()],
                                                            value=['secured_unsecured', 'currency', 'callable'], )]), ])

    submit_btn = html.Div([html.Button('Go', id=SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=RESULT_ID, children=[])

    dynamic_kids = html.Div(
        [html.Div([html.Br(), issuer_input, industry_input, maturity_input, constraints_check,
                   html.Br(), submit_btn, ],
                  className='fund_filter'),
         search_results])
    return static_kids, dynamic_kids


def create_result_layout(issuer, industry, maturity, constrained_columns):
    df = bond_rv.get_rv_df(issuer, industry, maturity, constrained_columns)
    df = df.sort_values('zscore', key=abs, ascending=False)
    dt = df['asof_date'].max()
    show_cols = ['zscore', 'industry_lvl3_desc', 'industry_lvl4_desc', 'ticker', 'coupon', 'maturity',
                 'ticker_right', 'coupon_right', 'maturity_right',
                 'instrument_id', 'instrument_id_right',
                 'maturity_bucket', 'maturity_bucket_right',
                 'gross_volume_1m', 'gross_volume_1m_right']
    df['zscore'] = df[['zscore', 'instrument_id', 'instrument_id_right']] \
        .apply(lambda x: f"[{round(x['zscore'], 2)}]"
                         f"(./bonds-rv-ts?bonds={x['instrument_id']},{x['instrument_id_right']})",
               axis=1)
    df = df[[x for x in show_cols if x in df.columns] + [x for x in df.columns if x not in show_cols]]
    show_cols = [dfhelpers.ui_column_name(x) for x in show_cols]
    df.columns = df.columns.map(dfhelpers.ui_column_name)
    hide_cols = [x for x in df.columns if x not in show_cols]

    div_kids = []
    div_kids.append(html.Br())
    div_kids.append(html.Label(f"Results as of date: {dt}", className='subtitle'))

    div_kids.append(make_dash_table(df, page_size=10, dynamic_num_format=True, hidden_columns=hide_cols,
                                    link_columns=[dfhelpers.ui_column_name('zscore')]))
    div_html = html.Div(div_kids, className='fund_filter')

    return html.Div([div_html], style=dict(width='100%'), className='page')
