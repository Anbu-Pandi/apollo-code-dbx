import pandas as pd

from dash_reports.report_common.data.data_model import datastore
from data_access.dbtables.findox import financial_data as findoxdb

FINDOX_ISSUERS_CACHE_KEY = 'findox_issuers'


def _transform_period_name(ds):
    return ds.str.replace(r'F(\d+)([HQ])(\d+)', r'F\3\2\1', regex=True).str.replace(r'FYE(\d+)', r'FY\1E', regex=True)


def get_latest_financial_models(issuer):
    df = findoxdb.get_latest_financial_models(issuer)
    df['period_name'] = _transform_period_name(df['period_name'])

    ret_values = {}
    pivot_df = pd.DataFrame()
    pivot_formula_df = pd.DataFrame()
    if len(df) > 0:
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        pivot_df = pd.pivot_table(df,
                                  index=['issuer_name', 'apo_issuer_name', 'line_name', 'identifier'],
                                  columns=['period_type', 'period_name'],
                                  values='value', aggfunc='mean').reset_index()

        agg_str = lambda x: ','.join([str(_x) for _x in x if pd.notnull(_x)])
        pivot_formula_df = pd.pivot_table(df, index=['issuer_name', 'apo_issuer_name', 'line_name', 'identifier'],
                                          columns=['period_type', 'period_name'],
                                          values='calculation_formula', aggfunc=agg_str).reset_index()

    ret_values['pivot_values'] = pivot_df
    ret_values['pivot_formula'] = pivot_formula_df
    return ret_values


def get_latest_period_financials():
    df = findoxdb.get_latest_period_financials()
    df['period_name'] = _transform_period_name(df['period_name'])

    ret_values = {}
    pivot_df = pd.DataFrame()
    pivot_formula_df = pd.DataFrame()
    if len(df) > 0:
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        pivot_df = pd.pivot_table(df, index=['issuer_name', 'apo_issuer_name', 'identifier', 'period_name'],
                                  columns=['line_name'],
                                  values='value', aggfunc='mean').reset_index()

        agg_str = lambda x: ','.join([str(_x) for _x in x if pd.notnull(_x)])
        pivot_formula_df = pd.pivot_table(df, index=['issuer_name', 'apo_issuer_name', 'identifier', 'period_name'],
                                          columns=['line_name'],
                                          values='calculation_formula', aggfunc=agg_str).reset_index()
        
    ret_values['pivot_values'] = pivot_df
    ret_values['pivot_formula'] = pivot_formula_df
    return ret_values


def get_all_issuers():
    df = datastore.get_app_key(FINDOX_ISSUERS_CACHE_KEY)
    if df is None:
        df = findoxdb.get_all_issuers()
        datastore.set_app_key(FINDOX_ISSUERS_CACHE_KEY, df)

    return df
