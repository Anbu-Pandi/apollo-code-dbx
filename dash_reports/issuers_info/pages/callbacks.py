import datetime
import pandas as pd
import logging

from dash import Input, Output, State, callback, html
from dash_reports.report_common.elastic_search.api import default_es

from dash_reports.issuers_info.data.financials import get_financials_by_company

from dash_reports.issuers_info.pages.capital_structure \
    import RELS_COS_STORE_ID, create_fin_ui, create_cap_struct_layout

from dash_reports.issuers_info.pages.capital_structure \
    import DD_SECTION_ID, RESULTS_SECTION_ID, SUBMIT_BUTTON_ID, DD_COMPANY_NAME_ID, SHOW_SECURITY_ISSUERS_CHECKLIST_ID, \
    OPT_WITH_SECURITIES, get_company_name_dropdown

from dash_reports.issuers_info.pages.fundamental_estimate \
    import FUND_EST_DD_FE_ITEM_ID, FUND_EST_RESULTS_SECTION_ID, FUND_EST_SUBMIT_BUTTON_ID, \
    FROM_DATE_CONTROL_ID, TO_DATE_CONTROL_ID, \
    create_fundamental_estimate_layout

from dash_reports.issuers_info.pages.issuer_financials \
    import FIN_RESULTS_SECTION_ID, FIN_SUBMIT_BUTTON_ID, ISSUER_DD_ID, create_issuer_financials_layout

from dash_reports.issuers_info.pages.issuer_current_financials \
    import CURR_FIN_RESULTS_SECTION_ID, CURR_FIN_SUBMIT_BUTTON_ID, create_curr_financials_layout

from dash_reports.report_common.pages import ui_config

from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX, ES_BBG_BONDS_INDEX, ES_WSO_LOANS_INDEX
from common.utils import dtutil


@callback(
    [Output(DD_COMPANY_NAME_ID, "options"),
     Output(DD_COMPANY_NAME_ID, "value"),
     ],
    Input(DD_COMPANY_NAME_ID, "search_value"),
    Input(DD_COMPANY_NAME_ID, "value"),
    State(DD_COMPANY_NAME_ID, "options"),
    prevent_initial_call=True)
def update_options(search_value, selected_id, options):
    df = pd.DataFrame()
    if search_value:
        logging.info(f'searching for value: {search_value}')
        df = default_es.search_prefix([ES_BBG_ISSUERS_INDEX, ES_BBG_BONDS_INDEX, ES_WSO_LOANS_INDEX],
                                      ['id_bb_global_company_name', 'id_isin', 'id_cusip', 'id_bb_global',
                                       'ticker', 'loanx_id'],
                                      search_value or -1)
        if len(df) > 0:
            df = df.dropna(subset=['id_bb_global_company_name', 'id_bb_company']) \
                .drop_duplicates(subset=['id_bb_global_company_name', 'id_bb_company'])

    if len(df) > 0:
        options = [{'label': x['id_bb_global_company_name'],
                    'value': int(x['id_bb_company'])}
                   for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]
        if (len(df) >= 1) and (len(df) <= 2):
            selected_id = int(df.iloc[0]['id_bb_company'])

    return [options, selected_id]


@callback(Output(RESULTS_SECTION_ID, "children"),
          Output(DD_SECTION_ID, "children"),
          Input(SUBMIT_BUTTON_ID, "n_clicks"),
          State(SHOW_SECURITY_ISSUERS_CHECKLIST_ID, "value"),
          State(DD_COMPANY_NAME_ID, 'value'),
          State(DD_COMPANY_NAME_ID, 'options'),
          prevent_initial_call=True)
def on_select_bbg_company(n_clicks, show_security_issuers_checked, bbg_id, options):
    logging.info(f'on select bbg company: {bbg_id}')
    show_security_issuers = OPT_WITH_SECURITIES in show_security_issuers_checked
    page_html = create_cap_struct_layout(bbg_id, show_security_issuers=show_security_issuers, show_corp_tree_df=True)
    dd_control = get_company_name_dropdown(bbg_id, options, show_security_issuers)
    return [page_html], [dd_control]


@callback(Output(FUND_EST_RESULTS_SECTION_ID, "children"),
          Input(FUND_EST_SUBMIT_BUTTON_ID, "n_clicks"),
          State(DD_COMPANY_NAME_ID, 'value'),
          State(FROM_DATE_CONTROL_ID, 'date'),
          State(TO_DATE_CONTROL_ID, 'date'),
          State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                 'index': FUND_EST_DD_FE_ITEM_ID}, 'value'),
          prevent_initial_call=True)
def on_select_bbg_company_fe_item(n_clicks, co_id, fromdt, todt, fe_items):
    logging.info(f'on select co id and fe item: {co_id} {fe_items} {fromdt} {todt}')
    page_html = create_fundamental_estimate_layout(co_id, fromdt, dtutil.dateadd(todt, '5y'), fe_items)
    return [page_html]


@callback(Output('page-content-dynamic-1', 'children'),
          [Input(RELS_COS_STORE_ID, 'modified_timestamp'),
           State(RELS_COS_STORE_ID, 'data')],
          prevent_initial_call=True)
def update_fin_ui(ts, rels_co):
    if len(rels_co) > 0:
        t2 = datetime.datetime.now()
        t1 = dtutil.dateadd(t2, '-3y')
        fin_dict = get_financials_by_company(rels_co, t1, t2, freq='Q,LTM,A,YTD')
        return html.Div(children=create_fin_ui(fin_dict), className='page')
    return None


@callback(Output(FIN_RESULTS_SECTION_ID, "children"),
          [Input(FIN_SUBMIT_BUTTON_ID, "n_clicks"),
           State(ISSUER_DD_ID, 'value'), ],
          prevent_initial_call=True
          )
def on_select_issuer_id_financials(n_clicks, issuer_ids):
    logging.info(f'on select issuer for issuer financials: {issuer_ids}')
    page_html = create_issuer_financials_layout(issuer_ids)
    return [page_html]


@callback(Output(CURR_FIN_RESULTS_SECTION_ID, "children"),
          [Input(CURR_FIN_SUBMIT_BUTTON_ID, "n_clicks"), ],
          prevent_initial_call=True
          )
def on_submit_issuer_current_financials(n_clicks):
    logging.info(f'on issuer current financials submit')
    page_html = create_curr_financials_layout()
    return [page_html]
