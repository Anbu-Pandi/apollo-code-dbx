import datetime
import json
import logging

import dash_core_components as dcc
import pandas as pd
import dash
import re

from common.utils.dtutil import today
from dash_reports.analyst_questionnaire.data.bulk_load import add_override_flag
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
from dash_reports.analyst_questionnaire.data.questions import get_questions
from dash_reports.analyst_questionnaire.pages.utils import \
    diff_dashtable, get_curr_ts, _convert_to_df, _push_to_db, markdown_style, ANALYST_DATA_OVERRIDE_TBL, \
    ANALYST_RESPONSE_TBL, assumption_title


def _get_callback_trigger_component_id():
    ctx = dash.callback_context
    tbl_key = json.loads(ctx.triggered[0]["prop_id"].split(".")[0])['index']
    tbl_id = int(tbl_key[-1])
    return tbl_key, tbl_id, len(ctx.inputs_list[0])


def capture_diffs(ts, data, data_previous, diff_store_data, company_id):
    tbl_key, tbl_id, input_count = _get_callback_trigger_component_id()

    logging.info(f'Analyst Questionnaire: Store Analyst Inputs for table - {tbl_key}')

    diff_store_data = diff_store_data or {}

    ts = get_curr_ts()

    if not data_previous[tbl_id] is None:
        diff_store_data[ts] = diff_dashtable(data[tbl_id], data_previous[tbl_id], company_id)
    logging.info(f'Analyst Questionnaire: Data Stored: {diff_store_data} for table - {tbl_key}')

    return diff_store_data


def insert_data_to_db(user_name, source_tbl, diff_store_data):
    component_key, component_id, input_count = _get_callback_trigger_component_id()
    logging.info(f"Analyst Questionnaire: Inserting Analyst Changes to database - {diff_store_data}")

    output_list = [''] * input_count
    output_list[component_id] = dcc.Markdown([f'No Changes Found.'], style=markdown_style)

    if diff_store_data:
        df = _convert_to_df(diff_store_data)
        df['last_updated_by'] = user_name

        _push_to_db(df, ANALYST_DATA_OVERRIDE_TBL)
        output_list[component_id] = dcc.Markdown([f'Changes Submitted.'], style=markdown_style)
        return output_list, []

    return output_list, []


def _parse_analyst_respone_df(company_id, question_list, response_list, path, analyst_name):
    issuer_df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', company_id)
    df = pd.DataFrame({'question': question_list, 'answer': response_list})
    df['last_updated_by'] = analyst_name
    df['last_update_ts'] = datetime.datetime.now()
    df['asof_date'] = today()
    df['company_id'] = int(company_id)
    df['company_name'] = issuer_df.iloc[0]['id_bb_global_company_name']
    df = add_override_flag(df)
    return df


def record_analyst_response(company_id, question_list, response_list, path, analyst_name):
    logging.info(f'Analyst Questionnaire: Pushing Response to Questions to Database for {company_id}')
    display_string = f'Response to mandatory questions required.'
    if not check_null_mandatory_response(question_list, response_list):
        df = _parse_analyst_respone_df(company_id, question_list, response_list, path, analyst_name)
        _push_to_db(df, ANALYST_RESPONSE_TBL)
        display_string = f'Your response: "{response_list}" has been recorded successfully!'
    return dcc.Markdown([display_string], style=markdown_style)


def check_null_mandatory_response(question_list, response_list):
    df = pd.DataFrame({'question': question_list, 'answer': response_list})
    questions_df = get_questions()

    filter_condition = (questions_df['report_name'] == 'analyst_questionnaire') & (questions_df['required'] == 'yes')

    required_qids = questions_df[filter_condition]['qid'].to_list()
    df = df[df['question'].isin(required_qids)]
    return any(df['answer'].isnull())


def show_base_value_cal(analyst_response, assumption_values, response_id):
    statement = []
    section, qid = response_id['index'].split('|')
    if analyst_response is not None:
        for assumption in assumption_values:
            for k, v in assumption.items():
                if k != section:
                    continue

                if qid == 'gross_debt_chg_est':
                    base_value = v['IQ_TOTAL_DEBT']['absolute']
                    if base_value is not None and base_value != 0:
                        value = round(base_value * analyst_response / 100, 0)
                        statement = f"""$(a) * (1) * 0.01$  
                        ${base_value} * {analyst_response} * 0.01$ = {value} mm"""
                    else:
                        statement = f"""6mo Total Debt Change = {analyst_response}%"""
                elif qid == 'gross_debt_chg_est_million':
                    base_value = v['IQ_TOTAL_DEBT']['absolute']
                    if base_value is not None and base_value != 0:
                        value = round((analyst_response / base_value) * 100, 2)
                        statement = f"""100 x ($(2)$ / $(a)$)  
                        100 x (${analyst_response}$ / ${base_value}$) = {value}%"""
                    else:
                        statement = f"""6mo Total Debt Change = {analyst_response}mm"""
                elif qid == 'shares_out_chg_est':
                    base_value = v['sharesOutstanding']['absolute']
                    if base_value is not None and base_value != 0:
                        value = round(base_value * analyst_response / 100, 0)
                        statement = f"""$(c) * (1) * 0.01$  
                        ${"{:,.0f}".format(base_value)} * {analyst_response} * 0.01$ = {"{:,.0f}".format(value)}"""
                    else:
                        statement = f"""6mo Shares Out. Change = {analyst_response}%"""
                elif qid == 'shares_out_chg_est_million':
                    base_value = v['sharesOutstanding']['absolute']
                    if base_value is not None and base_value != 0:
                        value = round((analyst_response / base_value) * 100, 2)
                        statement = f"""100 x ($(1)$ / $(c)$)  
                        100 x (${analyst_response}$ / ${base_value}$) = {value}%"""
                    else:
                        statement = f"""6mo Shares Out. Change = {analyst_response}mm"""
                elif qid == 'div_paid_chg_est':
                    base_value = v['IQ_COMMON_DIV_CF']['absolute']
                    if base_value is not None and base_value != 0:
                        value = int((base_value * (1 + analyst_response * 0.01)))
                        statement = f"""$(a)$ x (1 + $(2)$ x 0.01)  
                        6mo Dividend Forecast (mm) = ${base_value}$ x ( 1 + ${analyst_response}$ x 0.01) = {value} mm"""
                    else:
                        statement = f"""6mo Dividend Forecast Change (%) = ${analyst_response}$%"""
                elif qid == 'div_paid_chg_est_million':
                    base_value = v['IQ_COMMON_DIV_CF']['absolute']
                    if base_value is not None and base_value != 0:
                        value = int(100 * (analyst_response / base_value - 1))
                        statement = f"""100 x ($(2)$ / $(a)$ - 1)  
                        6mo Dividend Forecast (%) = 100 x (${analyst_response}$ / ${base_value}$ - 1) = {value}%"""
                    else:
                        statement = f"""6mo Dividend Forecast (mm) = ${analyst_response}$ mm"""

    return [statement]


def get_modal_window_content(triggered_id, base_value_dict, base_value_cal_dict):
    statement = []
    for base_value in base_value_dict:
        if triggered_id not in base_value.keys():
            continue
        assumption_value = base_value[triggered_id]
        if triggered_id == 'equity':
            shares_outstanding = assumption_value['sharesOutstanding']['absolute']
            shares_outstanding = round(shares_outstanding / 1e6,
                                       0) if shares_outstanding is not None else shares_outstanding
            pct_change_out_shares = assumption_value['sharesOutstanding']['pct_change']
            abs_dvd_pay = assumption_value['IQ_COMMON_DIV_CF']['absolute']
            pct_change_dvd = assumption_value['IQ_COMMON_DIV_CF']['pct_change']
            dvd_forecast = assumption_value['DIVIDEND_FORECAST']['absolute']
            dvd_6m_pct_chg_forecast = assumption_value['DIVIDEND_FORECAST']['pct_change']

            out_shares_statement_nominal = base_value_cal_dict['equity|shares_out_chg_est'].split(chr(10))[
                -1].strip() if isinstance(
                base_value_cal_dict['equity|shares_out_chg_est'], str) else None

            out_shares_statement_chg = base_value_cal_dict['equity|shares_out_chg_est_million'].split(chr(10))[
                -1].strip() if isinstance(
                base_value_cal_dict['equity|shares_out_chg_est_million'], str) else None

            dvd_change_statement = base_value_cal_dict['equity|div_paid_chg_est'].split(chr(10))[
                -1].strip() if isinstance(base_value_cal_dict['equity|div_paid_chg_est'], str) else None

            tot_dvd_statement = base_value_cal_dict['equity|div_paid_chg_est_million'].split(chr(10))[
                -1].strip() if isinstance(base_value_cal_dict['equity|div_paid_chg_est_million'], str) else None

            statement = f"""Assumptions:  
(a) {assumption_title['IQ_COMMON_DIV_CF']['absolute']}: {abs_dvd_pay}  
(b) {assumption_title['IQ_COMMON_DIV_CF']['pct_change']}: {pct_change_dvd}  
(c) {assumption_title['sharesOutstanding']['absolute']}: {shares_outstanding}  
(d) {assumption_title['sharesOutstanding']['pct_change']}: {pct_change_out_shares}  
(e) {assumption_title['DIVIDEND_FORECAST']['absolute']}: {dvd_forecast}  
(f) {assumption_title['DIVIDEND_FORECAST']['pct_change']}: {round(dvd_6m_pct_chg_forecast, 2)}

6mo Shares Outstanding Change (mm) = {out_shares_statement_nominal}  
6mo Shares Outstanding Change (%) = {out_shares_statement_chg}  
{dvd_change_statement}  
{tot_dvd_statement} 
                    """
        elif triggered_id == 'debt':
            abs_tot_debt = assumption_value['IQ_TOTAL_DEBT']['absolute']
            pct_change_tot_debt = assumption_value['IQ_TOTAL_DEBT']['pct_change']

            tot_debt_statement_notional = base_value_cal_dict['debt|gross_debt_chg_est'].split(chr(10))[
                -1].strip() if isinstance(base_value_cal_dict['debt|gross_debt_chg_est'], str) else None

            tot_debt_statement_change = base_value_cal_dict['debt|gross_debt_chg_est_million'].split(chr(10))[
                -1].strip() if isinstance(base_value_cal_dict['debt|gross_debt_chg_est_million'], str) else None

            statement = f"""Assumptions:  
(a) {assumption_title['IQ_TOTAL_DEBT']['absolute']}: {abs_tot_debt}  
(b) {assumption_title['IQ_TOTAL_DEBT']['pct_change']}: {pct_change_tot_debt}

6mo Total Debt Notional Change (mm): {tot_debt_statement_notional}  
6mo Total Debt % Change: {tot_debt_statement_change}  
                    """

    return [dcc.Markdown(statement, style={'color': 'black', 'font-size': '14px'}, mathjax=True)]


def update_dvd_input_values(triggered_id, dvd_rate_change, abs_dvd_pay, assumptions_value):
    base_value = assumptions_value['equity']['IQ_COMMON_DIV_CF']['absolute']
    if triggered_id == 'equity|div_paid_chg_est':
        if base_value is not None and dvd_rate_change is not None and base_value != 0:
            new_value = round((base_value * dvd_rate_change * 0.01) + base_value, 0)
        else:
            new_value = abs_dvd_pay
        return dvd_rate_change, new_value
    elif triggered_id == 'equity|div_paid_chg_est_million':
        if base_value is not None and abs_dvd_pay is not None and base_value != 0:
            new_value = round(((abs_dvd_pay / base_value) - 1) * 100, 0)
        else:
            new_value = dvd_rate_change
        return new_value, abs_dvd_pay
    else:
        return dvd_rate_change, abs_dvd_pay


def update_shares_out_input_values(triggered_id, chg_rate, notional_chg, assumptions_value):
    base_value = assumptions_value['equity']['sharesOutstanding']['absolute']
    if triggered_id == 'equity|shares_out_chg_est':
        if base_value is not None and chg_rate is not None and base_value != 0:
            new_value = round((base_value * chg_rate * 0.01), 0)
        else:
            new_value = notional_chg
        return chg_rate, new_value
    elif triggered_id == 'equity|shares_out_chg_est_million':
        if base_value is not None and notional_chg is not None and base_value != 0:
            new_value = round((notional_chg / base_value) * 100, 0)
        else:
            new_value = chg_rate
        return new_value, notional_chg
    else:
        return chg_rate, notional_chg


def update_gross_debt_input_values(triggered_id, debt_chg_rate, notional_debt_chg, assumptions_value):
    base_value = assumptions_value['debt']['IQ_TOTAL_DEBT']['absolute']
    if triggered_id == 'debt|gross_debt_chg_est':
        if base_value is not None and debt_chg_rate is not None and base_value != 0:
            new_value = round((base_value * debt_chg_rate * 0.01), 0)
        else:
            new_value = notional_debt_chg
        return debt_chg_rate, new_value
    elif triggered_id == 'debt|gross_debt_chg_est_million':
        if base_value is not None and notional_debt_chg is not None and base_value != 0:
            new_value = round((notional_debt_chg / base_value) * 100, 0)
        else:
            new_value = debt_chg_rate
        return new_value, notional_debt_chg
    else:
        return debt_chg_rate, notional_debt_chg
