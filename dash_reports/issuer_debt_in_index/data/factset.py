import logging
import re

import numpy as np
import pandas as pd

from common.utils import sqlutil, dtutil, dfutil
from dash_reports.issuer_debt_in_index.data.fx import adjust_fx_amt_fields
from data_access.dbtables.market_data.spot.fx import get_fx
from dash_reports.issuer_debt_in_index.data.map_constants import DCS_MAP, DCS_SENIORITY_LIEN_MAP_DF
from data_access.dbtables import BOND_REFERENCEDATA_APP
from data_access.dbtables.factset.dcs import get_dcs_df
from data_access.dbtables.factset.estimates import get_estimates
from data_access.dbtables.factset.financial_data import get_company_financials, get_company_report_date
from data_access.dbtables.factset.pricing_returns import get_ticker_prices
from data_access.dbtables.factset.sec_reference import get_factset_ids_from_isin
from data_access.utils import get_where_clause, join_where_clauses

FF_ITEMS = ['FF_MKT_VAL', 'FF_EBITDA_OPER', 'FF_CAPEX', 'FF_INT_EXP_NET',
            'FF_ENTRPR_VAL', 'FF_DEBT', 'FF_NET_DEBT', 'FF_COM_SHS_OUT_EPS_DIL', 'FF_CASH_ONLY', 'FF_SALES',
            'FF_ASSETS']

FF_RATIO_ITEMS = ['FF_DEBT_EBITDA_OPER', 'FF_DEBT_ASSETS', 'FF_NET_DEBT_ASSETS', ]


def get_related_instruments(instrument_ids=None, linked_instrument_ids=None):
    linked_instru_clause = get_where_clause('linked_instrument_id', linked_instrument_ids)
    instru_clause = get_where_clause('linked_instrument_id', instrument_ids)
    where_clause = join_where_clauses(' AND ', [linked_instru_clause, instru_clause])
    if len(where_clause) > 0:
        where_clause = 'WHERE ' + where_clause
    related_instruments = sqlutil.readdfsql(f'select * from related_instruments {where_clause}',
                                            app=BOND_REFERENCEDATA_APP)
    return related_instruments


def _map_seniority(seniority, lien_type):
    if pd.isnull(seniority):
        seniority = ''
    if pd.isnull(lien_type):
        lien_type = ''

    return DCS_SENIORITY_LIEN_MAP_DF.reindex([(seniority.upper(), lien_type.upper())]).values.tolist()[0]


def _extract_suffixes(text, search_strings):
    if not isinstance(text, str):
        return text
    for search_string in search_strings:
        # Constructing a pattern to find the word containing the search string
        pattern = r'\b\w*' + re.escape(search_string) + r'\w*\b'

        # Searching for the pattern in the text
        match = re.search(pattern, text)

        if match and len(text[match.end():]) > 0:
            # If the pattern is found, return everything to the right of the matched word
            return text[match.end():]

    return text


def get_dcs_df_mapped(dt, ticker_region_list, related_instruments, spot_fx_df=None):
    dcs_df = get_dcs_df(dt, ticker_region_list)

    loan_text_terms = ['Revolver', 'Term Loan', 'Facility']
    dcs_df = dcs_df[dcs_df['max_report_date'] == dcs_df['report_date']]
    dcs_df['instrument_description'] = dcs_df['instrument_description'] \
        .map(lambda x: _extract_suffixes(x, loan_text_terms))

    dcs_df.columns = dcs_df.columns.map(lambda x: f'fs_{x.lower()}')

    dcs_df[['secured_unsecured', 'seniority']] = dcs_df \
        .apply(lambda x: _map_seniority(x['fs_seniority_desc'], x['fs_debt_desc']),
               axis=1, result_type='expand')

    dcs_df_mapped = dcs_df[DCS_MAP.keys()].rename(columns=DCS_MAP)

    dcs_df_mapped = dcs_df_mapped.loc[:, ~dcs_df_mapped.columns.duplicated()]
    dcs_df_mapped = pd.merge(dcs_df_mapped, related_instruments, left_on='instrument_id',
                             right_on='linked_instrument_id', how='left', suffixes=('', '_related'))
    dcs_df_mapped['instrument_id_uniq'] = dcs_df_mapped['instrument_id_related'].combine_first(
        dcs_df_mapped['instrument_id'])

    dcs_df_mapped = dcs_df_mapped.sort_values('asof_date')

    if spot_fx_df is None:
        spot_fx_df = get_fx(dt, dt)
    dcs_df_mapped = adjust_fx_amt_fields(dcs_df_mapped, spot_fx_df)
    return dcs_df_mapped


def _combine_debt_and_dcs(index_debt_df, dcs_df_mapped):
    dfs = {}
    instrument_ids_completed = []
    for in_type, join_on in {'Bond': 'instrument_id_uniq', 'Loan': 'bbg_id'}.items():
        dfs[in_type] = pd.merge(
            index_debt_df[index_debt_df['instrument_type'].str.contains(in_type)].assign(source='index') \
                .dropna(subset=[join_on]),
            dcs_df_mapped.assign(source='fs').dropna(subset=[join_on]).drop_duplicates(subset=[join_on]),
            on=[join_on], suffixes=('', '_fs'), how='left')
        instrument_ids_completed.extend(dfs[in_type][join_on].unique().tolist())

    dfs['equity'] = index_debt_df[index_debt_df['instrument_type'] == 'Equity'].assign(source='equity')
    dfs['other_index'] = index_debt_df[~(index_debt_df['instrument_type'].str.contains('Bond') |
                                         index_debt_df['instrument_type'].str.contains('Loan') |
                                         (index_debt_df['instrument_type'] == 'Equity'))] \
        .assign(source='index')
    dfs['other_fs'] = dcs_df_mapped[~(dcs_df_mapped['instrument_id_uniq'].isin(instrument_ids_completed) |
                                      dcs_df_mapped['bbg_id'].isin(instrument_ids_completed))].assign(source='fs')
    dfs['other_fs'].rename(columns={'ticker': 'ticker_fs'}, inplace=True)

    final_debt_df = pd.concat([df.assign(merge_type=key) for key, df in dfs.items()], ignore_index=True)

    for x in final_debt_df.columns:
        if x.endswith('_fs'):
            final_debt_df[x[:-3] + '_index'] = final_debt_df[x[:-3]]
            final_debt_df[x[:-3]] = final_debt_df[x].combine_first(final_debt_df[x[:-3]])
    final_debt_df['source'] = final_debt_df['source_fs'].combine_first(final_debt_df['source'])
    final_debt_df['asof_date'] = final_debt_df['asof_date_index'].combine_first(final_debt_df['asof_date_fs'])

    for col_name in ['ticker', 'issuer_name']:
        details_by_ticker_region = index_debt_df.groupby(['ticker_region', col_name]) \
            ['asof_date'].count() \
            .reset_index() \
            .sort_values('asof_date', ascending=False) \
            .drop(columns=['asof_date']) \
            .drop_duplicates(subset=['ticker_region'], keep='first')

        suffix = f'_debt_{col_name}'
        final_debt_df = pd.merge(final_debt_df, details_by_ticker_region, how='left', on='ticker_region',
                                 suffixes=('', suffix))

        final_debt_df[col_name] = final_debt_df[f'{col_name}{suffix}'].combine_first(final_debt_df[col_name])

    def _get_desc(x):
        return x['seniority'] + ' ' + \
            (x['debt_code'][:2] + ' ' if len(x['debt_code']) > 0 else '') + \
            x['instrument_desc'] + ' ' + str(x['coupon']) + ' ' + str(x['maturity']) \
                .replace('  ', ' ')

    def _get_cds_desc(x):
        return x['seniority'] + ' ' + \
            ('CDS' + ' ') + \
            str(x['coupon']) + ' ' + str(x['maturity']) \
                .replace('  ', ' ')

    def _get_bond_desc(x):
        return x['seniority'] + ' ' + \
            (x['debt_code'][:2] + ' ' if len(x['debt_code']) > 0 else '') + \
            str(x['coupon']) + ' ' + str(x['maturity']) \
                .replace('  ', ' ')

    is_bond = (final_debt_df['instrument_type'] == 'Bond') | (final_debt_df['debt_code'].str[:2] == 'BD')
    is_cds = (final_debt_df['instrument_type'] == 'CDS')
    final_debt_df.loc[~is_bond, 'instrument_desc'] = final_debt_df.loc[~is_bond, :].fillna('') \
        .apply(_get_desc, axis=1)
    final_debt_df.loc[is_bond, 'instrument_desc'] = final_debt_df.loc[is_bond, :].fillna('') \
        .apply(_get_bond_desc, axis=1)
    final_debt_df.loc[is_cds, 'instrument_desc'] = final_debt_df.loc[is_cds, :].fillna('') \
        .apply(_get_cds_desc, axis=1)

    final_debt_df['asof_date_fs'] = pd.to_datetime(final_debt_df['asof_date_fs'].fillna('1900-01-01'))
    final_debt_df['issue_date'] = pd.to_datetime(final_debt_df['issue_date'].fillna('1900-01-01'))

    final_debt_df['report_date_fs'] = final_debt_df.groupby('ticker')['asof_date_fs'].transform(np.max)

    final_debt_df['coupon_numeric'] = pd.to_numeric(final_debt_df['coupon'], errors='coerce').fillna(-1)

    drop_bonds_df = final_debt_df[((final_debt_df['source'] == 'index') &
                                   (final_debt_df['instrument_type'] == 'Bond') &
                                   (final_debt_df['issue_date'] <= final_debt_df['report_date_fs']))]

    debt_df_without_dupes = final_debt_df[~((final_debt_df['source'] == 'index') &
                                            (final_debt_df['instrument_type'] == 'Bond') &
                                            (final_debt_df['issue_date'] <= final_debt_df['report_date_fs']))]

    ret_df = pd.merge(debt_df_without_dupes,
                      drop_bonds_df[['ticker', 'coupon_numeric', 'maturity']].assign(_drop_row=1),
                      how='left', on=['ticker', 'coupon_numeric', 'maturity'])

    ret_df = pd.concat([ret_df[ret_df['_drop_row'].isnull()], drop_bonds_df], ignore_index=True)

    return ret_df


def merge_factset_dcs(debt_df, spot_fx_df=None):
    ref_dt = debt_df['asof_date'].max()
    debt_df = add_ticker_region_by_ticker(debt_df)

    ticker_region_list = debt_df['ticker_region'].unique().tolist()
    fs_ref_dt = dtutil.dateadd(ref_dt, '-1y')

    related_instruments = get_related_instruments()
    dcs_df = get_dcs_df_mapped(fs_ref_dt, ticker_region_list, related_instruments, spot_fx_df)

    index_debt_df = pd.merge(debt_df, related_instruments, left_on='instrument_id',
                             right_on='linked_instrument_id', how='left', suffixes=('', '_related'))
    index_debt_df['instrument_id_uniq'] = index_debt_df['instrument_id_related'].combine_first(
        index_debt_df['instrument_id'])
    index_debt_df = index_debt_df.drop_duplicates(subset=['instrument_id_uniq'])

    final_debt_df = _combine_debt_and_dcs(index_debt_df, dcs_df)

    ticker_name_map = final_debt_df.groupby(['ticker', 'issuer_name'])[['instrument_id']].count() \
        .sort_values('instrument_id', ascending=False) \
        .reset_index() \
        .drop_duplicates(subset=['ticker'], keep='first') \
        .drop(columns=['instrument_id'])

    final_debt_df = dfutil.drop_columns(final_debt_df, ['issuer_name'])
    final_debt_df = pd.merge(final_debt_df, ticker_name_map, how='left', on='ticker')
    final_debt_df['asof_date_instrument'] = final_debt_df['asof_date']
    final_debt_df['asof_date'] = ref_dt

    return final_debt_df


def add_ticker_region_by_ticker(debt_df):
    ticker_cols = ['ticker_region']
    if 'bbg_ticker' in debt_df.columns:
        ticker_cols.append('bbg_ticker')
    bond_eqy_map = debt_df.groupby(['ticker'] + ticker_cols)[['isin']].count() \
        .sort_values('isin', ascending=False) \
        .reset_index() \
        .drop_duplicates(subset=['ticker'], keep='first') \
        .drop(columns=['isin'])

    debt_df = dfutil.drop_columns(debt_df, ticker_cols)
    debt_df = pd.merge(debt_df, bond_eqy_map, how='left', on='ticker')
    debt_df['equity_ticker'] = debt_df['ticker_region']

    return debt_df


def merge_factset_equity_tickers(debt_df):
    dt = pd.to_datetime(debt_df['asof_date'], format='mixed').dt.date.max()
    isins = debt_df[debt_df['instrument_type'] == 'Bond']['instrument_id'].unique().tolist()
    isin_eqy_map = get_factset_ids_from_isin(isins, dt)

    debt_df = pd.merge(dfutil.drop_columns(debt_df, isin_eqy_map.columns.tolist()),
                       isin_eqy_map, how='left',
                       left_on='instrument_id', right_on='isin')

    debt_df = add_ticker_region_by_ticker(debt_df)

    return debt_df


def merge_factset_equity_returns(debt_df):
    todt = pd.to_datetime(debt_df['asof_date'], format='mixed', errors='coerce').max().date()
    fromdt = todt
    eq_pricing_df = get_ticker_prices(fromdt, todt, debt_df['ticker_region'].unique().tolist())

    eqy_ticker_map = debt_df.dropna(how='any', subset=['ticker', 'ticker_region', 'bbg_ticker']) \
        [debt_df['is_equity_ticker_parent'].fillna(False)] \
        .groupby(['ticker', 'ticker_region', 'bbg_ticker'])[['isin']].count() \
        .sort_values('isin', ascending=False) \
        .reset_index() \
        .drop_duplicates(subset=['ticker_region', 'bbg_ticker'], keep='first') \
        .drop(columns=['isin'])

    eqy_instru_df = eq_pricing_df[
        ['ticker_region', 'bbg_ticker', 'eq_mkt_cap_mm', 'equity_currency', 'equity_px', 'price_date']]
    eqy_instru_df = pd.merge(eqy_instru_df, eqy_ticker_map, on='ticker_region')
    eqy_instru_df = eqy_instru_df.rename(columns={'ticker_region': 'instrument_id',
                                                  'eq_mkt_cap_mm': 'amt_outstanding_loc',
                                                  'equity_currency': 'currency',
                                                  'price_date': 'asof_date',
                                                  'equity_px': 'price'})
    eqy_instru_df['ticker_region'] = eqy_instru_df['instrument_id']
    eqy_instru_df['eq_mkt_cap_mm'] = eqy_instru_df['amt_outstanding_loc']
    eqy_instru_df['equity_px'] = eqy_instru_df['price']
    eqy_instru_df['instrument_type'] = 'Equity'
    eqy_instru_df['secured_unsecured'] = 'Equity'
    eqy_instru_df['seniority'] = 'Equity'

    debt_df = pd.concat([debt_df, eqy_instru_df], ignore_index=True)
    debt_df = pd.merge(debt_df, eq_pricing_df, how='left', on='ticker_region', suffixes=('_eq_pricing', ''))

    return debt_df


def merge_factset_financials(debt_df, factset_data_ids=None):
    debt_df = add_ticker_region_by_ticker(debt_df)

    factset_data_ids = (factset_data_ids or (FF_ITEMS + FF_RATIO_ITEMS))

    todt = pd.to_datetime(debt_df['asof_date'], format='mixed').dt.date.max()
    fromdt = dtutil.dateadd(todt, '-1y')

    findf = get_company_financials(debt_df['ticker_region'].unique().tolist(),
                                   fromdt, todt, 'ltm', factset_data_ids)
    report_date_df = get_company_report_date(debt_df['ticker_region'].unique().tolist())

    findf['max_report_date'] = findf.groupby('ticker_region')['period_end_date'].transform(np.max)
    ticker_ccy = findf.groupby(['ticker_region', 'currency'])['period_end_date'].count() \
        .reset_index().sort_values('period_end_date').drop_duplicates(keep='last').drop(columns='period_end_date')

    findf = findf[findf['max_report_date'] == findf['period_end_date']]
    findf = pd.pivot_table(findf, index=['ticker_region', 'period_end_date'], columns=['factset_id'],
                           values='value').reset_index()
    debt_df = pd.merge(debt_df, findf, how='left', on='ticker_region')
    debt_df = pd.merge(debt_df, ticker_ccy, how='left', on='ticker_region', suffixes=('', '_financial'))
    debt_df = pd.merge(debt_df, report_date_df, how='left', on='ticker_region', suffixes=('', '_financial'))

    return debt_df


def adjust_fx_financials(debt_df, fx_df):
    fx_df = fx_df.rename(columns={'ccy': 'currency', 'value': 'fx_rate'}, errors='ignore')
    fx_df = fx_df.dropna(subset=['fx_rate'], how='all')
    fx_df = fx_df[['asof_date', 'currency', 'fx_rate']]
    debt_df['period_end_date'] = pd.to_datetime(debt_df['period_end_date'], errors='coerce', format='mixed')
    fx_df['asof_date'] = pd.to_datetime(fx_df['asof_date'], errors='coerce', format='mixed')

    null_period_df = debt_df[pd.isnull(debt_df['period_end_date'])]
    notnull_period_df = debt_df[pd.notnull(debt_df['period_end_date'])]

    notnull_period_df = pd.merge_asof(notnull_period_df.sort_values('period_end_date'),
                                      fx_df.sort_values('asof_date'),
                                      left_on='period_end_date', right_on='asof_date',
                                      left_by='currency_financial', right_by='currency', suffixes=('', '_fx'))

    debt_df = pd.concat([notnull_period_df, null_period_df], ignore_index=True)
    debt_df['fx_rate'] = debt_df['fx_rate'].fillna(1.0)
    cols_to_adjust = [x for x in debt_df.columns if x.upper() in FF_ITEMS]

    for x in cols_to_adjust:
        debt_df[f"{x}_USD"] = debt_df[x] / debt_df['fx_rate']
    return debt_df


def merge_factset_estimates(debt_df):
    fromdt = pd.to_datetime(debt_df['asof_date'], format='mixed').dt.date.max()
    todt = dtutil.dateadd(fromdt, '6m')
    ticker_regions = debt_df['ticker_region'].unique().tolist()
    estimates_df = get_estimates(fromdt, todt, ['SALES', 'EBITDA', 'EBITDA_ADJ', 'INT_EXP', 'TOTAL_DEBT', 'CAPEX'],
                                 tickers=ticker_regions, rel_per=[1])

    estimates_df = pd.pivot_table(estimates_df, index=['TICKER_REGION', 'FE_FP_END'],
                                  columns=['FE_ITEM'], values='FE_MEAN', aggfunc='mean').reset_index()

    col_map = {'fe_fp_end': 'estimate_report_date', 'ticker_region': 'ticker_region'}
    estimates_df.columns = estimates_df.columns.map(lambda x: col_map.get(x.lower(), f"{x.lower()}_estimate"))

    debt_df = pd.merge(debt_df, estimates_df, on='ticker_region', how='left')
    return debt_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_related_instruments()

    df = get_dcs_df_mapped('2024-06-29', ['4755-JP'], get_related_instruments())

    _df = pd.read_clipboard()
    df = merge_factset_dcs(_df)
    logging.info(df)
