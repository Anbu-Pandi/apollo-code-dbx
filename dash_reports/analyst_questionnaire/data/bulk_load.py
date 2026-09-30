import logging
import os
import re
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import xlwings as xw

from apo_config.config import config
from common.utils.dtutil import today
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
from dash_reports.analyst_questionnaire.data.equity import get_forecasts
from dash_reports.analyst_questionnaire.data.questions import get_questions
from dash_reports.analyst_questionnaire.data.utils import get_growth_lines
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.report_common.utils import dfhelpers
from data_access.beta_attribution import APP
from data_access.utils import insert_df_to_db

PRICING_METRICS = ['sharesOutstanding', 'TEV', 'totalReturn1Y']

QUESTION_FORECASTED_DEFAULT_VALUE_COLUMN_MAP = {
    'gross_debt_chg_est': {'metric': 'IQ_TOTAL_DEBT - 6m annualized', 'value_col': 'latest_qtr_value',
                           'pct_flag': True},
    'gross_debt_chg_est_million': {'metric': 'IQ_TOTAL_DEBT - estimated change', 'value_col': 'latest_qtr_value',
                                   'pct_flag': False},
    'shares_out_chg_est': {'metric': 'sharesOutstanding - 6m annualized', 'pct_flag': True,
                           'value_col': 'latest_qtr_value'},
    'shares_out_chg_est_million': {'metric': 'sharesOutstanding - estimated change', 'pct_flag': False,
                                   'value_col': 'latest_qtr_value'},
    'div_paid_chg_est': {'metric': 'IQ_COMMON_DIV_CF', 'value_col': '6m_chg_forecast',
                         'pct_flag': True},
    'div_paid_chg_est_million': {'metric': 'IQ_COMMON_DIV_CF', 'value_col': '6m_forecast',
                                 'pct_flag': False}}


def flatten_base_value_df(df):
    formula_metric_required = list({v['metric'] for k, v in QUESTION_FORECASTED_DEFAULT_VALUE_COLUMN_MAP.items()})

    base_value_df = df[df['formula_metric'].isin(formula_metric_required)]
    base_value_df = base_value_df[['Issuer Id Bb Company', 'formula_metric', 'latest_qtr_value', '6m_forecast',
                                   '6m_chg_forecast']]

    base_value_df = base_value_df.drop_duplicates(subset=['Issuer Id Bb Company', 'formula_metric']).set_index(
        ['Issuer Id Bb Company', 'formula_metric']).unstack('formula_metric')

    base_value_df.columns = base_value_df.columns.swaplevel()
    base_value_df.columns = base_value_df.columns.to_flat_index()

    col_rename = {(v['metric'], v['value_col']): k for k, v in QUESTION_FORECASTED_DEFAULT_VALUE_COLUMN_MAP.items()}

    base_value_df.rename(columns=col_rename, inplace=True)

    base_value_df = base_value_df[[k for k in QUESTION_FORECASTED_DEFAULT_VALUE_COLUMN_MAP.keys()]]

    return base_value_df


def add_predicted_base_value_cols(df, df_ques_base_value):
    logging.info('Adding predicted base value')
    base_value_df = flatten_base_value_df(df_ques_base_value)

    df = df.set_index('Issuer Id Bb Company')
    df = pd.merge(df, base_value_df, how='left', left_index=True, right_index=True, sort=False)
    df = df.reset_index()

    for qid_col, v in QUESTION_FORECASTED_DEFAULT_VALUE_COLUMN_MAP.items():
        qid_col_display_value = dfhelpers.ui_column_name(qid_col)
        col_loc = df.columns.get_loc(qid_col_display_value) + 1
        df.rename(columns={qid_col_display_value: f'Prior {qid_col_display_value}'}, inplace=True)

        df.insert(col_loc, qid_col_display_value, np.nan)
        df[qid_col_display_value] = df[qid_col]

        if qid_col in ['gross_debt_chg_est', 'shares_out_chg_est']:
            df[qid_col_display_value] = df[qid_col_display_value].div(2)

        df[qid_col_display_value] = df[qid_col_display_value].round(2)

    df = df.drop(base_value_df.columns, axis=1)
    return df


def update_xl(df, df_ques_base_value, user):
    df_ques_base_value.insert(3, 'key', df_ques_base_value['Issuer Id Bb Company'].astype(int).astype(str) +
                              df_ques_base_value['formula_metric'])

    logging.info(f'generating bulk load file for user: {user}, df shape: {df.shape}')
    df = add_predicted_base_value_cols(df, df_ques_base_value)

    template_file = os.path.join(os.path.split(__file__)[0], 'files/AnalystQuestionnaireBulkUpload.xlsx')
    write_folder = config.getconfig(APP, 'local_cache_dir')  # Assuming APP is defined
    dttime = str(datetime.now()).replace(':', '.')
    write_file = os.path.join(write_folder, f'AnalystQuestionnaireBulkUpload_request_{user}_{dttime}.xlsx')

    # Reindex df columns based on template
    df_template = pd.read_excel(template_file, sheet_name='Inputs', skiprows=1, nrows=2)
    cols = df_template.columns.tolist()
    cols = cols[:cols.index(next((c for c in cols if c.startswith('Unnamed:')), len(cols)))]
    df = df.reindex(cols, axis=1)

    shutil.copy(template_file, write_file)
    # Using with statement to manage the application context
    with xw.App(visible=False) as app:
        wb = app.books.open(write_file)

        # Update 'Inputs' sheet
        ws_inputs = wb.sheets['Inputs']
        ws_inputs.range('A3').options(index=False, header=False).value = df

        # Update 'Data' sheet
        ws_data = wb.sheets['Data']
        ws_data.range('A1').options(index=False, header=True).value = df_ques_base_value

        wb.save()
        wb.close()
    return write_file


def add_override_flag(df):
    metrics = ['sharesOutstanding', 'IQ_TOTAL_DEBT', 'IQ_COMMON_DIV_CF']
    qdf = get_ques_base_value(co_ids=df['company_id'].tolist(), metrics=metrics)
    estimates_df = flatten_base_value_df(qdf)

    estimates_df.columns.name = 'question'
    estimates_df.index.name = 'company_id'

    estimates_df['gross_debt_chg_est'] = estimates_df['gross_debt_chg_est'].div(2)
    estimates_df['shares_out_chg_est'] = estimates_df['shares_out_chg_est'].div(2)
    estimates_df = estimates_df.round(2)

    estimates_df = estimates_df.unstack().to_frame('predicted_value').reset_index()

    df = pd.merge(df, estimates_df, on=['company_id', 'question'], how='left')

    df['override_flag'] = df['answer'] == df['predicted_value']

    df['override_flag'] = df['override_flag'].replace(False, 'Y').replace(True, 'N')
    df = df.drop('predicted_value', axis=1)

    return df


def bulk_insert_analyst_response(df, user):
    ques_df = get_questions()
    ques_df = ques_df[ques_df['report_name'] == 'analyst_questionnaire']

    required_cols = ques_df['qid'].to_list()
    required_cols.extend(['issuer_id_bb_company', 'issuer_bb_global_company_name'])

    df = df[required_cols]
    df = df.rename(columns={'issuer_bb_global_company_name': 'company_name', 'issuer_id_bb_company': 'company_id'})
    df.set_index(['company_id', 'company_name'], inplace=True)
    df.columns.name = 'question'

    df = df.stack('question')
    df = df.dropna()
    df = df.to_frame(name='answer').reset_index()

    df = add_override_flag(df)

    df['last_updated_by'] = user
    df['asof_date'] = today()
    df['last_update_ts'] = datetime.now()
    df['company_id'] = df['company_id'].astype(int)

    insert_df_to_db('analyst_questionnaire_response', df, method='sql')

    display_df = pd.merge(df, ques_df[['qid', 'description']], right_on='qid', left_on='question').drop(
        columns=['question'])
    display_df = display_df[
        ['company_id', 'company_name', 'qid', 'description', 'answer', 'override_flag', 'last_updated_by', 'asof_date',
         'last_update_ts']]

    display_df = display_df.rename(columns={'qid': 'short_question'})

    return display_df


def _add_latest_values(df_pivot):
    qtr_cols = [x for x in df_pivot.columns if re.match(r"(\d{4} Q[1-4])", x)]
    bool_df = df_pivot[qtr_cols].notna()
    named_bool_df = bool_df.multiply(bool_df.columns, axis=1)
    named_bool_df = named_bool_df.where(named_bool_df != 0)
    df_pivot['latest_qtr'] = named_bool_df.max(axis=1)

    latest_qtr = df_pivot[qtr_cols].apply(lambda row: row.last_valid_index(), axis=1).fillna(qtr_cols[-1])
    latest_qtr_value = pd.merge(df_pivot[qtr_cols].reset_index().melt(id_vars='index'),
                                latest_qtr.reset_index(), left_on=['index', 'qtr'], right_on=['index', 0]) \
        .set_index('index')['value']
    df_pivot['latest_qtr_value'] = latest_qtr_value
    return df_pivot


def _get_pricing_df(fin_company_id_list, metrics):
    needs_pricing_metrics = (len([x for x in metrics if x in PRICING_METRICS]) > 0) or (len(metrics) == 0)
    search_fields = ['company_id']
    search_values = fin_company_id_list

    out_shares_df_pivot = pd.DataFrame()
    if needs_pricing_metrics:
        pricing_df = default_es.search_exact('analyst_ques_acquisition', search_fields, search_values)
        if len(pricing_df) > 0:
            pricing_df = pricing_df.drop_duplicates(subset=['company_id', 'qtr'] + PRICING_METRICS)

            out_shares_df = pricing_df[['company_id', 'company_name', 'qtr', ] + PRICING_METRICS] \
                .drop_duplicates(subset=['company_id', 'company_name', 'qtr', ]) \
                .set_index(['company_id', 'company_name', 'qtr']) \
                .stack().reset_index() \
                .rename(columns={'level_3': 'formula_metric', 0: 'value', 'company_id': 'fin_company_id'})

            if len(out_shares_df) > 0:
                out_shares_df_pivot = out_shares_df.pivot_table(
                    index=['fin_company_id', 'company_name', 'formula_metric'],
                    columns='qtr',
                    values='value',
                    aggfunc={'value': 'mean'}).reset_index()

    return out_shares_df_pivot


def _get_metrics_df(fin_company_id_list, metrics):
    search_fields = ['company_id']
    search_values = fin_company_id_list
    if len(metrics) > 0:
        search_fields.append('formula_metric')
        search_values += [x for x in metrics if x not in PRICING_METRICS]

    df = default_es.search_exact(['analyst_ques_debt', 'analyst_ques_equity', 'analyst_ques_mgmt'],
                                 search_fields, search_values)

    df_pivot = pd.DataFrame()
    if len(df) > 0:
        df = df.rename(columns={'company_id': 'fin_company_id'})
        df['value'] = pd.to_numeric(df['value'], errors='coerce')

        df_pivot = df.pivot_table(index=['fin_company_id', 'company_name', 'formula_metric'], columns='qtr',
                                  values='value',
                                  aggfunc={'value': 'mean'}).reset_index()
    return df_pivot


def _get_chg_df(df_pivot):
    chg_calc_formula_metrics = ['IQ_COMMON_DIV_CF', 'sharesOutstanding', 'IQ_TOTAL_DEBT']
    gby = ['fin_company_id', 'company_name', 'formula_metric']
    chg_df = df_pivot[df_pivot['formula_metric'].isin(chg_calc_formula_metrics)] \
        .groupby(gby) \
        .apply(lambda x: pd.DataFrame(get_growth_lines(x.set_index(gby).reset_index(drop=True).T[0])).T) \
        .reset_index()
    chg_df['formula_metric'] = (chg_df['formula_metric'] + ' - ' +
                                chg_df['level_3'].str.replace(' (rhs)', '', regex=False))
    chg_df.drop(columns='level_3', inplace=True)
    return chg_df


def _get_div_forecast_df(fin_company_id_list, df_pivot):
    dps_forecast = get_forecasts(fin_company_id_list, 'DPS', '6m')
    dps_forecast = dps_forecast.droplevel('FE_ITEM', axis=0)
    div_metrics = ['IQ_COMMON_DIV_CF', 'sharesOutstanding']
    div_gby = ['fin_company_id', 'formula_metric']
    curr_df = df_pivot[df_pivot['formula_metric'].isin(div_metrics)] \
        .groupby(div_gby)['latest_qtr_value'].mean().unstack()
    for m in div_metrics:
        if not m in curr_df.columns:
            curr_df[m] = np.nan
    div_forecast = dps_forecast * curr_df['sharesOutstanding']
    div_chg_forecast = div_forecast / (curr_df['IQ_COMMON_DIV_CF'] / 2) - 1
    div_forecast_df = pd.concat([div_forecast, div_chg_forecast], axis=1).reset_index()
    div_forecast_df.columns = ['fin_company_id', '6m_forecast', '6m_chg_forecast']
    div_forecast_df = div_forecast_df.assign(formula_metric='IQ_COMMON_DIV_CF')
    return div_forecast_df


def _add_nominal_change_value(df, chg_df, formula_metric):
    abs_change_df = pd.DataFrame()
    for metric in formula_metric:
        metric_df = df[df['formula_metric'] == metric].drop('formula_metric', axis=1).set_index(
            ['fin_company_id', 'company_name'])

        metric_chg_df = chg_df[chg_df['formula_metric'] == f'{metric} - 6m annualized'].drop('formula_metric',
                                                                                             axis=1).set_index(
            ['fin_company_id', 'company_name'])

        temp_df = metric_df * metric_chg_df.div(2)
        temp_df = temp_df.mul(0.01)
        temp_df['formula_metric'] = f'{metric} - estimated change'
        temp_df.reset_index(inplace=True)

        abs_change_df = pd.concat([abs_change_df, temp_df], ignore_index=True)

    return abs_change_df


def get_ques_base_value(co_ids=[], metrics=[], add_changes=True, add_forecasts=True):
    logging.info('getting base values for analyst questionnaire')
    if len(co_ids) == 0:
        issuer_universe = default_es.search_all(ES_ISSUER_COVERAGE_INDEX)
    else:
        issuer_universe = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', co_ids)

    issuer_universe.rename(columns={'factset_entity_id': 'fin_company_id'}, inplace=True)
    fin_company_id_list = issuer_universe['fin_company_id'].dropna().unique().tolist()

    df_pivot = _get_metrics_df(fin_company_id_list, metrics)
    out_shares_df_pivot = _get_pricing_df(fin_company_id_list, metrics)
    df_pivot = pd.concat([df_pivot, out_shares_df_pivot], ignore_index=True)
    if add_changes:
        chg_df = _get_chg_df(df_pivot)
        df_pivot = pd.concat([df_pivot, chg_df], ignore_index=True) \
            .drop_duplicates(subset=['fin_company_id', 'formula_metric'], keep='first')

        abs_chg_df = _add_nominal_change_value(df_pivot, chg_df, formula_metric=['IQ_TOTAL_DEBT', 'sharesOutstanding'])
        df_pivot = pd.concat([df_pivot, abs_chg_df], ignore_index=True)

    df_pivot = _add_latest_values(df_pivot)

    if add_forecasts:
        div_forecast_df = _get_div_forecast_df(fin_company_id_list, df_pivot)
        df_pivot = pd.merge(df_pivot, div_forecast_df, on=['fin_company_id', 'formula_metric'], how='left')

    merged_df = pd.merge(issuer_universe[['id_bb_company', 'id_bb_global_company_name', 'fin_company_id']], df_pivot,
                         on=['fin_company_id'], how='left') \
        .drop(['fin_company_id', 'company_name'], axis=1) \
        .sort_values(['id_bb_global_company_name', 'formula_metric']) \
        .rename(columns={'id_bb_company': 'Issuer Id Bb Company',
                         'id_bb_global_company_name': 'Bb Global Company Name', })

    logging.info('completed getting base values for analyst questionnaire')
    return merged_df
