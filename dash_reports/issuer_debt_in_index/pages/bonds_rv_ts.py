import pandas as pd
from dash import dcc

from common.utils import dtutil

import dash_html_components as html
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils import dfhelpers
from dash_reports.risk_query.pages.query_page import create_line_chart

from ...issuer_debt_in_index.data import bond_rv
from ...issuer_debt_in_index.pages import utils

BONDS_INPUT_ID = 'bonds-rv-ts-input'

SUBMIT_ID = 'bonds-rv-ts-submit'

RESULT_ID = 'bonds-rv-ts-result'


def create_layout(page, bonds):
    bonds = bonds or ''
    static_kids = [utils.get_debt_view_index_menu(page)]

    bonds_input = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Bonds:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=BONDS_INPUT_ID, type='search', className='input', value=bonds)],
                                    style=dict(width='30%', height='35px'))])

    submit_btn = html.Div([html.Button('Go', id=SUBMIT_ID, className='submit-button')])

    search_res_div_kids = []
    if len(bonds) > 0:
        search_res_div_kids = create_result_layout(bonds)
    search_results = html.Div(id=RESULT_ID, children=search_res_div_kids)

    dynamic_kids = html.Div(
        [html.Div([html.Br(), bonds_input, html.Br(), submit_btn, ],
                  className='fund_filter'),
         search_results])
    return static_kids, dynamic_kids


def create_result_layout(bonds):
    df = bond_rv.get_rv_bond_df(bonds=bonds)
    df['asof_date'] = pd.to_datetime(df['asof_date'], format='mixed').dt.date
    df = df.sort_values(['asof_date', 'ticker'], ascending=False)
    dt = df['asof_date'].max()
    df['maturity'] = df['maturity'].astype(str)
    show_cols = ['asof_date', 'zscore', 'industry_lvl4_desc', 'ticker', 'coupon', 'maturity', 'price', 'oas',
                 'instrument_id', 'maturity_bucket', 'gross_volume_1m', ]
    df = df[[x for x in show_cols if x in df.columns] + [x for x in df.columns if x not in show_cols]]

    div_kids = []
    div_kids.append(html.Br())
    div_kids.append(html.Label(f"Results as of date: {dt}", className='subtitle'))

    chart_cols = ['oas_usd', 'yield_to_worst_conv_usd', 'price', 'xccy', 'curve', 'spread_diff_bond_v_curve', ]
    df['coupon'] = pd.to_numeric(df['coupon'], errors='coerce').round(4)
    for chart_col in chart_cols:
        pdf = pd.pivot_table(df, values=chart_col, index=['asof_date'],
                             columns=['instrument_id', 'ticker', 'coupon', 'maturity'])
        if len(pdf) > 0:
            div_kids.append(create_line_chart(pdf, title=dfhelpers.ui_column_name(chart_col), base_line_width=1))

    df = df[[x for x in show_cols if x in df.columns] + [x for x in df.columns if x not in show_cols]]
    show_cols = [dfhelpers.ui_column_name(x) for x in show_cols]
    df.columns = df.columns.map(dfhelpers.ui_column_name)
    hide_cols = [x for x in df.columns if x not in show_cols]
    div_kids.append(html.Br())
    div_kids.append(make_dash_table(df, page_size=10, dynamic_num_format=True, hidden_columns=hide_cols))

    div_html = html.Div(div_kids, className='fund_filter')

    return html.Div([div_html], style=dict(width='100%'), className='page')
