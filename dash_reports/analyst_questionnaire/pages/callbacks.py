import pandas as pd
import json
import logging

from dash.exceptions import PreventUpdate
from dash import Input, Output, State, ALL, MATCH, callback, dcc, ctx

from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.report_common.utils.dash_authentication import dash_access_check

import dash_reports.analyst_questionnaire.pages.utils as page_utils
import dash_reports.analyst_questionnaire.pages.analyst_questionnaire as analyst_ques
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
from dash_reports.analyst_questionnaire.pages import bulk_load
from dash_reports.report_common.pages import user_info


@callback(
    Output('questionnaire-header-container', 'children'),
    Output('questionnaire-result-container', 'children'),
    Input(page_utils.ANALYST_SUBMIT_BUTTON_ID, "n_clicks"),
    State(page_utils.DD_COMPANY_NAME_ID, 'value'),
    State('url', "pathname"),
    State('url', "search"),
    prevent_initial_call=True
)
def on_analyst_ques_submit(n_clicks, company_id, pathname, search):
    logging.info(f'company selected: {company_id} on path: {pathname} old query param: {search}')
    if 'analyst-questionnaire/' in pathname:
        page = pathname.split('analyst-questionnaire/')[-1]
        hd = page_utils.create_questionnaire_specific_header_layout(page, company_id)
        bd = page_utils.get_ques_specific_kids(page, company_id)
        return hd, bd
    return [], []


@callback(
    Output("diff-store", "data"),
    [Input({'type': page_utils.METRICS_TABLE_ID, 'index': ALL}, "data_timestamp")],
    [
        State({'type': page_utils.METRICS_TABLE_ID, 'index': ALL}, "data"),
        State({'type': page_utils.METRICS_TABLE_ID, 'index': ALL}, "data_previous"),
        State("diff-store", "data"),
        State(page_utils.DD_COMPANY_NAME_ID, 'value')
    ],
    prevent_initial_call=True
)
def analyst_questionnaire_capture_diff(ts, data, data_previous, diff_store_data, company_id):
    if all(x is None for x in ts) or all(x is None for x in data_previous) is None:
        raise PreventUpdate
    return analyst_ques.capture_diffs(ts, data, data_previous, diff_store_data, company_id)


@callback(
    [Output({'type': page_utils.DB_INSERT_STATUS_ID, 'index': ALL}, 'children'),
     Output("diff-store", "clear_data")],
    [Input({'type': page_utils.PUSH_TO_DB_BUTTON_ID, 'index': ALL}, "n_clicks")],
    [
        State("diff-store", "data"),
        State(user_info.LOGGED_USER_ID, 'data'),
        State(user_info.LOGGED_USER_GROUPS_ID, 'data'),
        State("url", "pathname")
    ],
    prevent_initial_call=True
)
def analyst_questionnaire_push_to_db(n_clicks, diff_store_data, user, groups, path):
    if all(x is None for x in n_clicks):
        raise PreventUpdate
    if dash_access_check(groups, path, access_level='write'):
        return analyst_ques.insert_data_to_db(user_name=user, source_tbl='snp', diff_store_data=diff_store_data)
    else:
        return [dcc.Markdown(["You don't have write permissions"], style=page_utils.MARKDOWN_STYLE)]


@callback(
    Output('push-to-db-result-container2', 'children'),
    [Input('basic-interactions-1', 'selectedData')],
    prevent_initial_call=True
)
def display_click_data(selectedData):
    if selectedData is None:
        raise PreventUpdate
    else:
        return json.dumps(selectedData)


@callback(
    Output(page_utils.DD_COMPANY_NAME_ID, "options"),
    Input(page_utils.DD_COMPANY_NAME_ID, "search_value")
)
def update_options(search_value):
    if not search_value:
        raise PreventUpdate
    df = default_es.search_prefix([ES_ISSUER_COVERAGE_INDEX], ['id_bb_global_company_name'], search_value)
    return [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
            for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]


@callback(
    Output("download-report-template", "data"),
    Input("btn_download", "n_clicks"),
    State(user_info.LOGGED_USER_ID, 'data'),
    prevent_initial_call=True,
)
def on_download_report_template(n_clicks, user):
    fname = bulk_load.get_template_file(user)
    return dcc.send_file(fname)


@callback(Output('output-report-upload', 'children'),
          Input('upload-report-template', 'contents'),
          State('upload-report-template', 'filename'),
          State('upload-report-template', 'last_modified'),
          State(user_info.LOGGED_USER_ID, 'data'),
          prevent_initial_call=True, )
def on_upload_report_template(contents, file_name, dt, user):
    return bulk_load.load_response(contents, file_name, dt, user)


@callback(
    [Output({'type': page_utils.QUES_CALC_DIV, 'index': MATCH}, 'children')],
    [Input({'type': page_utils.ANALYST_RESPONSE, 'index': MATCH}, "value")],
    [State({'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': ALL}, 'data'),
     State({'type': page_utils.ANALYST_RESPONSE, 'index': MATCH}, "id")],
    prevent_initial_call=False
)
def update_base_value_calc(analyst_response, assumption_values, response_id):
    return analyst_ques.show_base_value_cal(analyst_response, assumption_values, response_id)


@callback(
    Output({'type': page_utils.MODAL_WINDOW_BODY_ID, 'index': MATCH}, 'children'),
    [Input({'type': 'analyst_response_button', 'index': MATCH}, "n_clicks")],
    [State({'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': ALL}, 'data'),
     State({'type': page_utils.QUES_CALC_DIV, 'index': ALL}, 'id'),
     State({'type': page_utils.QUES_CALC_DIV, 'index': ALL}, 'children')
     ],
    prevent_initial_call=True
)
def on_analyst_response_button(n_clicks, assumption_value, base_value_cal_div_id, base_value_cal_div_value):
    triggered_id = ctx.triggered_id['index']
    base_value_cal_dict = dict(zip([v['index'] for v in base_value_cal_div_id], base_value_cal_div_value))

    return analyst_ques.get_modal_window_content(triggered_id, assumption_value, base_value_cal_dict)


@callback(
    Output({'type': page_utils.MODAL_WINDOW_ID, 'index': MATCH}, 'is_open'),
    [Input({'type': 'analyst_response_button', 'index': MATCH}, "n_clicks"),
     Input({'type': page_utils.MODAL_CONFRIM_BUTTON_ID, 'index': MATCH}, "n_clicks"),
     Input({'type': page_utils.MODAL_CANCEL_BUTTON_ID, 'index': MATCH}, "n_clicks")],
    [State({'type': page_utils.MODAL_WINDOW_ID, 'index': MATCH}, 'is_open')],
    prevent_initial_call=True,
)
def display_modal_window(n1, n2, n3, is_open):
    if n1 or n2 or n3:
        return not is_open
    return is_open


@callback(
    Output({'type': 'analyst-ques-response-output', 'index': MATCH}, 'children'),
    [Input({'type': page_utils.MODAL_CONFRIM_BUTTON_ID, 'index': MATCH}, "n_clicks")],
    [State({'type': page_utils.ANALYST_QUESTIONS, 'index': ALL}, 'id'),
     State({'type': page_utils.ANALYST_RESPONSE, 'index': ALL}, 'id'),
     State({'type': page_utils.ANALYST_RESPONSE, 'index': ALL}, 'value'),
     State('url', "pathname"),
     State({'type': 'analyst_response_button', 'index': MATCH}, "id"),
     State(page_utils.DD_COMPANY_NAME_ID, 'value'),
     State(user_info.LOGGED_USER_ID, 'data'),
     State(user_info.LOGGED_USER_GROUPS_ID, 'data')
     ],
    prevent_initial_call=True
)
def on_modal_confirm_button(n_clicks, question_list, response_ids, response_list,
                            pathname, submit_id, company_id, user, groups):
    dash_access_check(groups, pathname, access_level='write')
    section = submit_id['index']
    question_list = [id_pair for id_pair in question_list if id_pair['index'].startswith(f'{section}|')]
    response_list = [x for x, id_pair in zip(response_list, response_ids) if id_pair['index'].startswith(f'{section}|')]
    if dash_access_check(groups, pathname, access_level='write'):
        return analyst_ques.record_analyst_response(company_id, [x['index'].split('|')[1] for x in question_list],
                                                    response_list,
                                                    pathname.split('analyst-questionnaire/')[-1], user)
    else:
        return [dcc.Markdown(["You don't have write permissions"], style=page_utils.MARKDOWN_STYLE)]


@callback(
    [Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|div_paid_chg_est'}, 'value'),
     Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|div_paid_chg_est_million'}, 'value')],
    [Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|div_paid_chg_est'}, "value"),
     Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|div_paid_chg_est_million'}, "value")],
    [State({'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': 'equity'}, 'data')],
    prevent_initial_call=True
)
def update_dvd_input_fields_value(dvd_rate_change, abs_dvd_pay, assumptions_value):
    triggered_id = ctx.triggered_id['index']
    return analyst_ques.update_dvd_input_values(triggered_id, dvd_rate_change, abs_dvd_pay, assumptions_value)


@callback(
    [Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'debt|gross_debt_chg_est'}, 'value'),
     Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'debt|gross_debt_chg_est_million'}, 'value')],
    [Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'debt|gross_debt_chg_est'}, "value"),
     Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'debt|gross_debt_chg_est_million'}, "value")],
    [State({'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': 'debt'}, 'data')],
    prevent_initial_call=True
)
def update_debt_change_input_fields_value(debt_chg_rate, notional_debt_chg, assumptions_value):
    triggered_id = ctx.triggered_id['index']
    return analyst_ques.update_gross_debt_input_values(triggered_id, debt_chg_rate, notional_debt_chg,
                                                       assumptions_value)


@callback(
    [Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|shares_out_chg_est'}, 'value'),
     Output({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|shares_out_chg_est_million'}, 'value')],
    [Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|shares_out_chg_est'}, "value"),
     Input({'type': page_utils.ANALYST_RESPONSE, 'index': 'equity|shares_out_chg_est_million'}, "value")],
    [State({'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': 'equity'}, 'data')],
    prevent_initial_call=True
)
def update_shares_out_input_fields_value(debt_chg_rate, notional_debt_chg, assumptions_value):
    triggered_id = ctx.triggered_id['index']
    return analyst_ques.update_shares_out_input_values(triggered_id, debt_chg_rate, notional_debt_chg,
                                                       assumptions_value)
