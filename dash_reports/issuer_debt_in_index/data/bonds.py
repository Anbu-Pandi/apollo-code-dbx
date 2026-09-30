import logging

import numpy as np
import pandas as pd

from common.utils import creditutil, sqlutil, dtutil, dfutil
from dash_reports.issuer_debt_in_index.data.factset import get_related_instruments
from dash_reports.issuer_debt_in_index.data.oas_percentile_calc.bonds import get_bond_oas_percentile_df
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.bonds import get_bond_betas
from dash_reports.issuer_debt_in_index.data.sql_const import (ICE_INDICES, BOND_SQL, ASOF_DATE_EQUAL_CLAUSE,
                                                              BBG_BOND_TICKER_SQL)
from data_access.dbtables import MSG1_QUOTES_APP, ICE_INDEX_APP, BOND_REFERENCEDATA_APP
from data_access.utils import tosqllist, get_where_clause, join_where_clauses

try:
    from dash_reports.issuer_debt_in_index.data.pecs_calcs.bonds import calc_pecs
except:
    logging.warning('import calc_pecs failed')
from dash_reports.issuer_debt_in_index.data.volume_calc.bonds import get_bond_volume, get_etf_change
import re


def _map_rating(x):
    if pd.isnull(x): return x
    if x[-1] == '1':
        return x[:-1] + '+'
    if x[-1] == '2':
        return x[:-1] + ''
    if x[-1] == '3':
        return x[:-1] + '-'
    return x


def _get_paymentrk_df(dt, isins):
    PAYMENT_RK_MAP = {
        'SECR_1L': r'^(1L\b|1st lien)\b',
        'SECR_2L': r'^(2L\b|2nd lien)\b',
        'SECR_3L': r'^3L\b',
        'SECR': r'\b(?<!Un)Secured\b|\b(?<!Un)Secd\b',
    }

    def _map_payment_rk(entry):
        default_category = list(PAYMENT_RK_MAP.keys())[-1]
        category_found = default_category

        for category, pattern in PAYMENT_RK_MAP.items():
            if re.search(pattern, entry, re.IGNORECASE):
                return category
        return category_found

    paymentrk_df = sqlutil.readdfsql(f'''
SELECT 
    t.isin_realtime, 
    max(t.payment_rank_rt) AS payment_rank_rt
FROM 
    dbo.msg1_quotes t
INNER JOIN (
    SELECT 
        isin_realtime, 
        MAX(event_timestamp) AS max_date
    FROM 
        dbo.msg1_quotes
    WHERE 
        cast(event_timestamp as date) between '{dtutil.dateadd(dt, '-1m')}' and '{dtutil.todate(dt)}'
        AND payment_rank_rt IS NOT NULL
        AND isin_realtime IN {tosqllist(isins)}
        AND isin_realtime <> 'N.A.'
    GROUP BY 
        all
) tm ON t.isin_realtime = tm.isin_realtime AND t.event_timestamp = tm.max_date
WHERE 
    t.payment_rank_rt IS NOT NULL
GROUP BY ALL
ORDER BY 
    t.isin_realtime;
    ''', app=MSG1_QUOTES_APP)

    paymentrk_df['seniority'] = paymentrk_df['payment_rank_rt'].map(_map_payment_rk)
    return paymentrk_df


def _get_lien_df(isins):
    lien_df = sqlutil.readdfsql(f"""
        select * from f_get_bond_security_master_attributes(NULL) where id_isin IN {tosqllist(isins)}
        and attribute_name IN ('FIRST_LIEN_INDICATOR', 'SECOND_LIEN_INDICATOR')
        """, app=BOND_REFERENCEDATA_APP)
    lien_df = pd.pivot_table(lien_df, index='id_isin', columns=['attribute_name'], values='attribute_value',
                             aggfunc=max).reset_index()
    lien_df['OTHER_LIEN_INDICATOR'] = lien_df.apply(
        lambda x: 'Y' if (x['FIRST_LIEN_INDICATOR'] == 'N') and (x['SECOND_LIEN_INDICATOR'] == 'N') else 'N', axis=1)

    def _map_seniority(x):
        if x['FIRST_LIEN_INDICATOR'] == 'Y':
            return 'SECR_1L'
        if x['SECOND_LIEN_INDICATOR'] == 'Y':
            return 'SECR_2L'
        if x['OTHER_LIEN_INDICATOR'] == 'Y':
            return 'SECR_3L'
        return 'SECR'

    lien_df['seniority'] = np.nan
    if len(lien_df) > 0:
        lien_df['seniority'] = lien_df.apply(_map_seniority, axis=1)
    return lien_df


def _map_seniority(dt, df):
    df['secured_unsecured'] = creditutil.map_secured_unsecured(df['collat_typ'], df['is_perpetual'])

    isins = df['isin_number'].unique().tolist()
    lien_df = _get_lien_df(isins)
    paymentrk_df = _get_paymentrk_df(dt, isins)

    secured_df = df[df['secured_unsecured'] == 'SEC']
    non_secured_df = df[df['secured_unsecured'].fillna('') != 'SEC']

    secured_df2 = pd.merge(secured_df, lien_df[['id_isin', 'seniority']], how='left', left_on='isin_number',
                           right_on='id_isin', suffixes=('', '_lien'))
    secured_df2 = pd.merge(secured_df2, paymentrk_df[['isin_realtime', 'seniority']], how='left', left_on='isin_number',
                           right_on='isin_realtime', suffixes=('', '_paymentrk'))
    secured_df2['seniority'] = secured_df2['seniority_paymentrk'].combine_first(secured_df2['seniority'])
    non_secured_df['seniority'] = non_secured_df['subordination_type']
    outdf = pd.concat([secured_df2, non_secured_df])

    return outdf


def _merge_bond_ref_df(asof_date, bonds_df, tickers=None):
    num_cols = bonds_df.select_dtypes(include=['number']).columns
    dt_cols = bonds_df.select_dtypes(include=['datetime']).columns

    col_map = {'isin_number': 'id_isin',
               'ticker': 'ticker',
               'description': 'short_name',
               'maturity_date': 'calc_maturity',
               'issue_date': 'issue_dt',
               'subordination_type': 'collat_typ',
               'iso_currency_code': 'crncy',
               'par_wtd_coupon': 'cpn',
               'current_period_coupon': 'cpn',
               'industry_lvl2_desc': 'industry_sector',
               'industry_lvl3_desc': 'industry_group',
               'industry_lvl4_desc': 'industry_subgroup',
               'face_value_loc': 'amt_outstanding',
               }
    bbg_cols_to_retain = ['id_isin', 'id_bb_global', 'id_bb_company', 'collat_typ', 'is_perpetual', 'amt_issued',
                          'cntry_of_domicile', 'cntry_of_incorporation']

    bbg_cols = set(list(col_map.values()) + bbg_cols_to_retain)

    ice_cols_pref = ['subordination_type', 'description', 'maturity_date',
                     'issue_date', 'ticker']
    issuer_level_details = ['description', 'industry_lvl2_desc', 'industry_lvl3_desc', 'industry_lvl4_desc',
                            'id_bb_company']

    isins = bonds_df['isin_number'].unique().tolist()
    tickers = tickers or bonds_df['ticker'].unique().tolist()
    bond_ref_df = sqlutil.readdfsql(BBG_BOND_TICKER_SQL.format(",".join(bbg_cols), asof_date, tosqllist(tickers)),
                                    app=BOND_REFERENCEDATA_APP)
    for k, v in col_map.items():
        bond_ref_df[k] = bond_ref_df[v]

    bond_ref_df = bond_ref_df[list(set(list(col_map.keys()) + bbg_cols_to_retain))]

    bond_ref_df['amt_issued'] /= 1e6
    bond_ref_df['face_value_loc'] /= 1e6
    bond_ref_df['asof_date'] = dtutil.todate(asof_date)
    bond_ref_df['years_since_issue'] = (pd.to_datetime(bond_ref_df['asof_date']) -
                                        pd.to_datetime(bond_ref_df['issue_date'])).dt.days / 365

    bond_related_instrument_ids = get_related_instruments(
        linked_instrument_ids=bond_ref_df['id_isin'].unique().tolist() +
                              bonds_df['isin_number'].unique().tolist())

    bond_ref_df = pd.merge(bond_ref_df, bond_related_instrument_ids, left_on='id_isin', right_on='linked_instrument_id',
                           how='left')
    bond_ref_df.drop_duplicates(subset=['instrument_id'], inplace=True)

    bonds_df = pd.merge(bonds_df, bond_related_instrument_ids, left_on='isin_number', right_on='linked_instrument_id',
                        how='left')
    bonds_df.drop_duplicates(subset=['instrument_id'], inplace=True)

    bonds_df = pd.merge(bonds_df, bond_ref_df,
                        on='instrument_id', how='outer', suffixes=('', '_ref'))
    for bbg_col_name in bonds_df.columns:
        if bbg_col_name.endswith('_ref'):
            ice_col_name = bbg_col_name[:-4]
            if ice_col_name in ice_cols_pref:
                bonds_df[ice_col_name] = bonds_df[ice_col_name].combine_first(bonds_df[bbg_col_name])
            else:
                bonds_df[ice_col_name] = bonds_df[bbg_col_name].combine_first(bonds_df[ice_col_name])

    bonds_df['ref_source'] = 'ice'
    bonds_df.loc[bonds_df['ice_index_ticker'].isnull(), 'ref_source'] = 'bbg'
    bonds_df.drop(columns=[c for c in bonds_df.columns if c.endswith('_ref')], inplace=True)
    bonds_df.drop(columns=['instrument_id', 'linked_instrument_id'], inplace=True)

    for c in num_cols:
        bonds_df[c] = pd.to_numeric(bonds_df[c], errors='coerce')
    for c in dt_cols:
        bonds_df[c] = pd.to_datetime(bonds_df[c], errors='coerce')

    ticker_level_details = bonds_df[(bonds_df['ref_source'] == 'ice')].groupby('ticker')[issuer_level_details] \
        .apply(lambda x: x.dropna().max()).reset_index()
    bonds_df = pd.merge(bonds_df, ticker_level_details, on='ticker', suffixes=('', '_tckr'), how='left')

    for ticker_col_name in bonds_df.columns:
        if ticker_col_name.endswith('_tckr'):
            ice_col_name = ticker_col_name[:-5]
            bonds_df[ice_col_name] = bonds_df[ticker_col_name].combine_first(bonds_df[ice_col_name])

    bonds_df.drop(columns=[c for c in bonds_df.columns if c.endswith('_tckr')], inplace=True)
    return bonds_df


def get_bond_df(asof_date, calc_pecs_flag=True, tickers=None):
    select_cols = 'asof_date, isin_number, ice_index_ticker, ticker, description, price, rating, maturity_date, years_since_issue, ' \
                  'subordination_type, iso_currency_code, par_wtd_coupon, current_period_coupon, industry_lvl2_desc, industry_lvl3_desc,' \
                  'industry_lvl4_desc, face_value_loc, full_market_value_usd, yield_to_worst_conv, yield_to_maturity_conv,' \
                  'oas, effective_duration, spread_duration, oas_1_day_chg, oas_1_week_chg, oas_1_mo_chg, ' \
                  'oas_3_mo_chg, oas_12_mo_chg, oas_mtd_change, oas_qtd_chg, oas_ytd_chg'

    ticker_fromdt = dtutil.dateadd(asof_date, '-1y')
    ticker_todt = dtutil.todate(asof_date)

    if tickers is None:
        tickers_df = sqlutil.readdfsql(BOND_SQL.format('distinct ticker',
                                                       tosqllist(ICE_INDICES),
                                                       f" cast(asof_date as date) between '{ticker_fromdt}' and '{ticker_todt}' "),
                                       app=ICE_INDEX_APP)
        tickers = tickers_df['ticker'].unique().tolist()

    asof_date_clause = ASOF_DATE_EQUAL_CLAUSE.format(asof_date)
    ticker_clause = get_where_clause('ticker', tickers)
    where_clause = join_where_clauses(' AND ', [asof_date_clause, ticker_clause])
    bonds_df = sqlutil.readdfsql(BOND_SQL.format(select_cols,
                                                 tosqllist(ICE_INDICES),
                                                 where_clause), app=ICE_INDEX_APP)
    bonds_df['issue_date'] = bonds_df['asof_date'] - pd.to_timedelta(bonds_df['years_since_issue'] * 365, unit='D')

    bonds_df = bonds_df[~bonds_df['ticker'].isin(['CASH', 'CASHM'])]
    bond_betas = get_bond_betas(asof_date, tickers=tickers)
    bond_volume = get_bond_volume(asof_date, asof_date, join_uncapped=False, tickers=tickers)
    bond_etf = get_etf_change(asof_date)

    bond_oas = get_bond_oas_percentile_df(ICE_INDICES, asof_date)

    bonds_df = _merge_bond_ref_df(asof_date, bonds_df, tickers=tickers)
    bonds_df = pd.merge(bonds_df,
                        bond_betas[
                            list(set(['isin_number', 'asof_date']) | set(bond_betas.columns) - set(bonds_df.columns))],
                        on=['asof_date', 'isin_number'], how='left')
    bonds_df = pd.merge(bonds_df, dfutil.drop_columns(bond_volume, ['asof_date']), on=['cusip'], how='left')
    bonds_df = pd.merge(bonds_df, bond_etf[1], on=['cusip'], how='left').rename(columns={'etf_change': 'etf_change_1m'})
    bonds_df['etf_change_1m'] = bonds_df['etf_change_1m'] / 1e6

    if calc_pecs_flag:
        bond_pecs = calc_pecs(asof_date)
        if 'asof_date' in bond_pecs.columns:
            bond_pecs = bond_pecs.drop(columns=['asof_date'])
        bonds_df = pd.merge(bonds_df,
                            bond_pecs[list(set(['isin_number']) | set(bond_pecs.columns) - set(bonds_df.columns))],
                            on=['isin_number'], how='left')

    bonds_df = pd.merge(bonds_df, bond_oas.drop(columns=['oas', 'asof_date']), on=['isin_number'], how='left')

    bonds_df['rating'] = bonds_df['rating'].map(_map_rating)
    bonds_df = _map_seniority(asof_date, bonds_df)
    bonds_df['instrument_type'] = 'Bond'

    bonds_df = bonds_df.reset_index(drop=True)
    bonds_df['effective_duration'] = bonds_df['effective_duration'].replace(0, np.nan) \
        .combine_first(bonds_df['spread_duration'])

    return bonds_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_bond_df('2024-11-26',  calc_pecs_flag=False, tickers=['DISH'])
    logging.info(df)
