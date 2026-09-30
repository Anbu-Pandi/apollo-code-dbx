import datetime
import logging
import pandas as pd
import numpy as np

from common.utils import dtutil

from dash import html, dcc

from dash_reports.report_common.utils import combos, dfhelpers

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.elastic_search.api import default_es

from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX
from dash_reports.risk_report.pages.utils import get_datepicker_control
from dash_reports.issuers_info.data.fundamental_estimate import get_fundamental_estimates, \
    get_recent_fundamental_estimate_detail

from dash_reports.issuers_info.pages.capital_structure import DD_COMPANY_NAME_ID
from dash_reports.issuers_info.pages import charters
from dash_reports.issuers_info.data.fundamental_estimate import get_fe_items_universe
from dash_reports.report_common.pages import ui_config

FUND_EST_TABLE_ID = 'fundamental-estimate-table-id'

FUND_EST_DD_FE_ITEM_ID = 'fundamental_estimate_item_dd_id'
FUND_EST_SUBMIT_BUTTON_ID = 'fundamental_estimate_submit_id'
FUND_EST_RESULTS_SECTION_ID = 'fundamental_estimate_section_id'
FUND_EST_DD_SECTION_ID = 'fundamental_estimate_dd_section_id'

FROM_DATE_CONTROL_ID = 'from-dt'
TO_DATE_CONTROL_ID = 'to-dt'


def get_fundamental_estimate_header():
    return html.Div([get_page_title("Fundamental Estimates"),
                     html.Br(), ])


def get_fundamental_estimate_page_controls(co_selected_value=None, user_settings=None):
    co_options = []
    if co_selected_value:
        df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_company', co_selected_value)
        co_options = [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
                      for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]

    co_dd_control = dcc.Dropdown(value=co_selected_value,
                                 multi=False,
                                 id=DD_COMPANY_NAME_ID,
                                 options=co_options)
    fe_items = get_fe_items_universe()
    fe_dd_control_id = {'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                        'index': FUND_EST_DD_FE_ITEM_ID}

    fe_dd_control = dcc.Dropdown(
        value=ui_config.get_control_value(FUND_EST_DD_FE_ITEM_ID, user_settings, list(fe_items.keys())[0]),
        multi=True,
        id=fe_dd_control_id,
        options=[{'label': v, 'value': k} for k, v in fe_items.items()])

    fromdt_control = get_datepicker_control(FROM_DATE_CONTROL_ID, dt=dtutil.dateadd(dtutil.today(use_bday=True), '-3y'),
                                            disabled=False)
    todt_control = get_datepicker_control(TO_DATE_CONTROL_ID, dt=dtutil.today(use_bday=True), disabled=False)

    fromdt = combos.get_labelled_component('From Date:', fromdt_control)
    todt = combos.get_labelled_component('To Date:', todt_control)
    co_dd = combos.get_labelled_component('Issuer:', co_dd_control)
    fe_dd = combos.get_labelled_component('Financial Item:', fe_dd_control)

    company_dd_header = html.Div(id='fundamental_estimate_company_name_dd_container',
                                 className='fund_filter',
                                 children=[html.Div(style=dict(display='inline'),
                                                    children=[fromdt, todt, co_dd, fe_dd]),
                                           html.Br(),
                                           html.Div(children=[html.Button('Go', id=FUND_EST_SUBMIT_BUTTON_ID,
                                                                          className='submit-button')])
                                           ])

    return company_dd_header


def create_div_kids():
    div_kids = []
    return div_kids


def create_fe_item_div(df, fe_item, detail_df):
    div_kids = []
    div_kids.append(charters.create_estimate_chart(df, fe_item))

    show_cols = ['TICKER_REGION', 'PROPER_NAME', 'FE_ITEM', 'FE_FP_END',
                 'FACTSET_PERSON_ID', 'PERSON_NAME', 'FACTSET_BROKER_ID', 'BROKER_NAME',
                 'ESTIMATE_DATE', 'ESTIMATE_VALUE', 'REVISION_DATE', 'REVISION_PREVIOUS_VALUE', 'REVISION_ARROW']
    hidden_cols = [dfhelpers.ui_column_name(x) for x in detail_df.columns if x not in show_cols]
    detail_df.columns = detail_df.columns.map(dfhelpers.ui_column_name)

    div_kids.append(combos.get_dash_table_with_header(f'Next Quarter {fe_item} Estimates: ', detail_df,
                                                      div_class_name='fund_filter',
                                                      hidden_columns=hidden_cols))

    return html.Div(div_kids)


def create_fe_items_tabs(dfs, detail_dfs):
    tabs = []
    for k, v in dfs.items():
        if len(v) > 0:
            k_desc = v.iloc[0]['FE_ITEM_DESC']
            tab_div = create_fe_item_div(v, k_desc, detail_dfs.get(k, pd.DataFrame()))
            tabs.append(dcc.Tab(label=k_desc, children=tab_div))
    return dcc.Tabs(tabs)


def create_fundamental_estimate_layout(id_bb_company, fromdt, todt, fe_items):
    logging.info(f'creating fundamental estimate layout for company_id: {id_bb_company} {fromdt} {todt} {fe_items}')
    dfs = get_fundamental_estimates(fromdt, todt, id_bb_company, fe_items)
    detail_dfs = get_recent_fundamental_estimate_detail(id_bb_company, fe_items)
    div_kids = []
    div_kids.append(create_fe_items_tabs(dfs, detail_dfs))

    return html.Div(className='page',
                    children=[html.Div(id=FUND_EST_TABLE_ID,
                                       children=[html.Div(children=div_kids, className='fund_filter')])])


def create_static_layout(bbg_id=None, user_settings=None):
    dd_control = html.Div(id=FUND_EST_DD_SECTION_ID,
                          children=[get_fundamental_estimate_page_controls(bbg_id, user_settings), ],
                          className='fund_filter')
    return dd_control


def create_dynamic_layout():
    search_results = html.Div(id=FUND_EST_RESULTS_SECTION_ID, children=[])
    return html.Div(children=[search_results])

def create_layout(bbg_id=None, user_settings=None):
    return create_static_layout(bbg_id, user_settings), create_dynamic_layout()