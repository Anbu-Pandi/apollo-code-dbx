import logging

from dash import html

from dash_reports.issuers_info.data.findox_financials import get_latest_period_financials
from dash_reports.report_common.utils import dfhelpers, components

CURR_FIN_DD_SECTION_ID = 'curr-fundamental-estimate-dd-section-id'
CURR_FIN_SUBMIT_BUTTON_ID = 'curr-fin-submit-id'
CURR_FIN_TABLE_ID = 'curr-issuer-financials-table-id'
CURR_FIN_RESULTS_SECTION_ID = 'curr_fin_section_id'


def get_curr_financials_page_controls():
    header = html.Div(id='curr_financials_dd_container',
                      className='fund_filter',
                      children=[html.Br(),
                                html.Div(children=[html.Button('Go', id=CURR_FIN_SUBMIT_BUTTON_ID,
                                                               className='submit-button')])
                                ])

    return header


def create_curr_financials_layout():
    logging.info(f'creating issuer current financials layout')
    ret_dict = get_latest_period_financials()

    df = ret_dict['pivot_values']
    formula_df = ret_dict['pivot_formula']

    df.columns = df.columns.map(dfhelpers.ui_column_name)
    formula_df.columns = formula_df.columns.map(dfhelpers.ui_column_name)

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
                    children=[html.Div(id=CURR_FIN_TABLE_ID,
                                       children=[html.Div(children=div_kids, className='fund_filter')])])


def create_static_layout():
    dd_control = html.Div(id=CURR_FIN_RESULTS_SECTION_ID,
                          children=[get_curr_financials_page_controls(), ],
                          className='fund_filter')
    return dd_control


def create_dynamic_layout():
    result_section = html.Div(id=CURR_FIN_RESULTS_SECTION_ID, children=[])
    return html.Div(children=[result_section])


def create_layout():
    return create_static_layout(), create_dynamic_layout()
