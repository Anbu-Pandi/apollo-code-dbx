import logging

import pandas as pd
from dash import html, dcc

from dash_reports.issuers_info.data.findox_financials import get_all_issuers, get_latest_financial_models
from dash_reports.report_common.utils import combos, dfhelpers, components

ISSUER_DD_ID = 'issuer_dd_id'
FIND_DD_SECTION_ID = 'fundamental_estimate_dd_section_id'
FIN_SUBMIT_BUTTON_ID = 'fin_submit_id'
FIN_TABLE_ID = 'issuer-financials-table-id'
FIN_RESULTS_SECTION_ID = 'fin_section_id'


def get_issuer_financials_page_controls(co_selected_value=None):
    issuers_df = get_all_issuers()
    co_selected_value = [int(pd.to_numeric(x, errors='coerce')) for x in
                         str(co_selected_value).replace(' ', '').split(',')
                         if pd.notnull(pd.to_numeric(x, errors='coerce'))]
    co_dd_control = dcc.Dropdown(value=co_selected_value,
                                 multi=True,
                                 id=ISSUER_DD_ID,
                                 options=[{'label': x['apo_issuer_name'], 'value': x['issuer_id']}
                                          for _, x in issuers_df.iterrows()])

    co_dd = combos.get_labelled_component('Issuer:', co_dd_control)

    company_dd_header = html.Div(id='issuer_financials_dd_container',
                                 className='fund_filter',
                                 children=[html.Div(style=dict(display='inline'),
                                                    children=[co_dd]),
                                           html.Br(),
                                           html.Div(children=[html.Button('Go', id=FIN_SUBMIT_BUTTON_ID,
                                                                          className='submit-button')])
                                           ])

    return company_dd_header


def create_issuer_financials_layout(issuer_ids):
    logging.info(f'creating issuer financials layout for issuers: {issuer_ids}')
    ret_dict = get_latest_financial_models(issuer_ids)

    df = ret_dict['pivot_values']
    formula_df = ret_dict['pivot_formula']

    df.columns = df.columns.map(lambda x: ('', dfhelpers.ui_column_name(x[0])) if x[1] == '' else x)
    formula_df.columns = formula_df.columns.map(lambda x: ('', dfhelpers.ui_column_name(x[0])) if x[1] == '' else x)

    fin_dash_table = components.make_dash_table(df, fixed_columns=5, page_size=10)
    formula_dash_table = components.make_dash_table(formula_df, fixed_columns=5, page_size=10)

    lbl = html.H6("Financial Summary:", className='subtitle')
    formula_lbl = html.H6("Calculations:", className='subtitle')

    div_kids = []
    div_kids.append(html.Br())
    div_kids.append(lbl)
    div_kids.append(fin_dash_table)

    div_kids.append(html.Br())
    div_kids.append(formula_lbl)
    div_kids.append(formula_dash_table)

    return html.Div(className='page',
                    children=[html.Div(id=FIN_TABLE_ID,
                                       children=[html.Div(children=div_kids, className='fund_filter')])])


def create_static_layout(issuer_ids=None):
    dd_control = html.Div(id=FIND_DD_SECTION_ID,
                          children=[get_issuer_financials_page_controls(issuer_ids), ],
                          className='fund_filter')
    return dd_control


def create_dynamic_layout():
    result_section = html.Div(id=FIN_RESULTS_SECTION_ID, children=[])
    return html.Div(children=[result_section])


def create_layout(issuer_ids=None):
    return create_static_layout(issuer_ids), create_dynamic_layout()
