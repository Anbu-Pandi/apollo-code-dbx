import base64
import io
import logging

import dash_html_components as html
import pandas as pd
from dash import dcc

from dash_reports.analyst_questionnaire.data.bulk_load import update_xl, bulk_insert_analyst_response, \
    get_ques_base_value
from dash_reports.analyst_questionnaire.pages.issuers import get_issuers_df
from dash_reports.analyst_questionnaire.pages.utils import MARKDOWN_STYLE, make_dash_table
from dash_reports.report_common.utils import dfhelpers
from data_importer.wso_api.helpers import to_naming_convention_string


def bulk_load_layout():
    return html.Div(
        [html.Br(), html.Br(),
         html.Button("Download Template", id="btn_download", className='submit-button'),
         dcc.Download(id="download-report-template"),
         html.Br(), html.Br(),
         dcc.Upload(html.Button('Upload File', className='submit-button'),
                    id="upload-report-template", ),
         html.Div(id='output-report-upload', className='page')
         ], className='sub_page')


def get_template_file(user):
    logging.info(f'download response template for user: {user}')
    df = get_issuers_df()
    df_ques_base_value = get_ques_base_value()
    xl_file = update_xl(df, df_ques_base_value, user)
    return xl_file


def recalculate_val_with_user_input(df):
    base_value_df = get_ques_base_value(co_ids=df['issuer_id_bb_company'].tolist(),
                                        metrics=['IQ_COMMON_DIV_CF', 'sharesOutstanding', 'IQ_TOTAL_DEBT'],
                                        add_changes=True, add_forecasts=True)

    base_value_df = base_value_df.rename(columns={'Issuer Id Bb Company': 'issuer_id_bb_company'})

    required_metrics = ['IQ_COMMON_DIV_CF', 'sharesOutstanding', 'sharesOutstanding - 6m annualized',
                        'sharesOutstanding - estimated change', 'IQ_TOTAL_DEBT',
                        'IQ_TOTAL_DEBT - 6m annualized', 'IQ_TOTAL_DEBT - estimated change']

    base_value_df = base_value_df[base_value_df['formula_metric'].isin(required_metrics)]

    base_value_df_pivot = base_value_df.pivot(index='issuer_id_bb_company', columns='formula_metric',
                                              values='latest_qtr_value')

    base_value_df_pivot.reset_index(inplace=True)

    div_forecast = base_value_df[base_value_df['formula_metric'].isin(['IQ_COMMON_DIV_CF'])][
        ['issuer_id_bb_company', '6m_forecast', '6m_chg_forecast']]

    div_forecast = div_forecast.rename(columns=
                                       {'6m_forecast': 'IQ_COMMON_DIV_CF - estimated change',
                                        '6m_chg_forecast': 'IQ_COMMON_DIV_CF_chg'})

    base_value_df_pivot = pd.merge(base_value_df_pivot, div_forecast, on='issuer_id_bb_company', how='left')

    df = pd.merge(df, base_value_df_pivot, on='issuer_id_bb_company', how='left')

    shares_out_chg_flag = (df['shares_out_chg_est'] != df['sharesOutstanding - 6m annualized'].div(2).round(
        2)) & (df['shares_out_chg_est_million'] == df['sharesOutstanding - estimated change'].round(2))

    df.loc[shares_out_chg_flag, 'shares_out_chg_est_million'] = df.loc[shares_out_chg_flag, 'sharesOutstanding'] \
        .mul(df.loc[shares_out_chg_flag, 'shares_out_chg_est']) \
        .mul(0.01).round(0)

    shares_out_chg_million_flag = (df['shares_out_chg_est'] == df['sharesOutstanding - 6m annualized'].div(
        2).round(2)) & (df['shares_out_chg_est_million'] != df['sharesOutstanding - estimated change'].round(2))

    df.loc[shares_out_chg_million_flag, 'shares_out_chg_est'] = df.loc[
        shares_out_chg_million_flag, 'shares_out_chg_est_million'].div(
        df.loc[shares_out_chg_million_flag, 'sharesOutstanding']).mul(100).round(2)

    both_sharesout_input_chg_flag = (df['shares_out_chg_est'] != df['sharesOutstanding - 6m annualized'].div(
        2).round(2)) & (df['shares_out_chg_est_million'] != df['sharesOutstanding - estimated change'].round(2))

    df.loc[both_sharesout_input_chg_flag, 'shares_out_chg_est_million'] = df.loc[
        both_sharesout_input_chg_flag, 'sharesOutstanding'].mul(
        df.loc[both_sharesout_input_chg_flag, 'shares_out_chg_est']) \
        .mul(0.01).round(0)

    total_debt_chg_flag = (df['gross_debt_chg_est'] != df['IQ_TOTAL_DEBT - 6m annualized'].div(2).round(
        2)) & (df['gross_debt_chg_est_million'] == df['IQ_TOTAL_DEBT - estimated change'].round(2))

    df.loc[total_debt_chg_flag, 'gross_debt_chg_est_million'] = df.loc[shares_out_chg_flag, 'IQ_TOTAL_DEBT'] \
        .mul(df.loc[shares_out_chg_flag, 'gross_debt_chg_est']).mul(0.01).round(0)

    total_debt_chg_million_flag = (df['gross_debt_chg_est'] == df['IQ_TOTAL_DEBT - 6m annualized'].div(
        2).round(2)) & (df['gross_debt_chg_est_million'] != df['IQ_TOTAL_DEBT - estimated change'].round(2))

    df.loc[total_debt_chg_million_flag, 'gross_debt_chg_est'] = df.loc[
        total_debt_chg_million_flag, 'gross_debt_chg_est_million'].div(
        df.loc[total_debt_chg_million_flag, 'IQ_TOTAL_DEBT']).mul(100).round(2)

    both_total_debt_input_chg_flag = (df['gross_debt_chg_est'] != df['IQ_TOTAL_DEBT - 6m annualized'].div(
        2).round(2)) & (df['gross_debt_chg_est_million'] != df['IQ_TOTAL_DEBT - estimated change'].round(2))

    df.loc[both_total_debt_input_chg_flag, 'gross_debt_chg_est_million'] = df.loc[
        both_total_debt_input_chg_flag, 'IQ_TOTAL_DEBT'].mul(
        df.loc[both_total_debt_input_chg_flag, 'gross_debt_chg_est']).mul(0.01).round(0)

    div_chg_flag = (df['div_paid_chg_est'] != df['IQ_COMMON_DIV_CF_chg'].round(
        2)) & (df['div_paid_chg_est_million'] == df['IQ_COMMON_DIV_CF - estimated change'].round(2))

    df.loc[div_chg_flag, 'div_paid_chg_est_million'] = df.loc[div_chg_flag, 'IQ_COMMON_DIV_CF'].div(2) \
        .mul(df.loc[div_chg_flag, 'div_paid_chg_est']).mul(0.01).round(0)

    div_chg_million_flag = (df['div_paid_chg_est'] == df['IQ_COMMON_DIV_CF_chg'].round(2)) & (
            df['div_paid_chg_est_million'] != df['IQ_COMMON_DIV_CF - estimated change'].round(2))

    df.loc[div_chg_million_flag, 'div_paid_chg_est'] = df.loc[
        div_chg_million_flag, 'div_paid_chg_est_million'].div(
        df.loc[div_chg_million_flag, 'IQ_COMMON_DIV_CF'].div(2)).mul(100).round(2)

    both_div_input_chg_flag = (df['div_paid_chg_est'] != df['IQ_COMMON_DIV_CF_chg'].round(2)) & (
            df['div_paid_chg_est_million'] != df['IQ_COMMON_DIV_CF - estimated change'].round(2))

    df.loc[both_div_input_chg_flag, 'div_paid_chg_est_million'] = df.loc[
        both_div_input_chg_flag, 'IQ_COMMON_DIV_CF'].div(2).mul(
        df.loc[both_div_input_chg_flag, 'div_paid_chg_est']).mul(0.01).round(0)

    df.drop(columns=required_metrics + ['IQ_COMMON_DIV_CF_chg', 'IQ_COMMON_DIV_CF - estimated change'], axis=1,
            inplace=True)

    return df


def load_response(contents, file_name, dt, user):
    logging.info(f'upload responses for user: {user}')

    def parse_contents(contents, filename, date):
        try:
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
            assert filename.endswith('xlsx')
            df = pd.read_excel(io.BytesIO(decoded), skiprows=1)
            df.columns = df.columns.map(to_naming_convention_string)
            df = df.iloc[:, 0: df.columns.get_loc('upload') + 1]
            df = df[[col for col in df.columns if 'prior' not in col]]
            df = df[df['upload'] == 1]

            logging.info(f'uploading user: {user} responses: {df.shape}')
            return df
        except Exception as e:
            logging.info(f'error in upload for user: {user} {str(e)}')

    df = parse_contents(contents, file_name, dt)

    if (df is not None) and len(df) > 0:
        df = recalculate_val_with_user_input(df)
        inserted_df = bulk_insert_analyst_response(df, user)
        inserted_df = inserted_df.rename(columns={x: dfhelpers.ui_column_name(x) for x in inserted_df.columns})
        inserted_df['Short Question'] = inserted_df['Short Question'].apply(lambda x: dfhelpers.ui_column_name(x))
        inserted_df = inserted_df.sort_values(by=['Company Id', 'Short Question'])
        return [html.Div([html.Br(),
                          dcc.Markdown(["Following Data Inserted"], style=MARKDOWN_STYLE),
                          make_dash_table(inserted_df, txt_col_width='250px', num_col_width='125px')
                          ])
                ]
    else:
        return [dcc.Markdown(["No new updated row found. Please check if upload column in sheet is 1 for rows edited"],
                             style=MARKDOWN_STYLE)]
