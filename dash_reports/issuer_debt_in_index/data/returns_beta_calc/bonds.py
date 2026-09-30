import logging

import pandas as pd

from common.utils import sqlutil, dtutil, dfutil
from common.utils.sqlutil import sql_replace_escape_char
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.utils import add_estimated_beta, vectorized_calc_tenor
from dash_reports.issuer_debt_in_index.data.sql_const import BOND_SQL, ASOF_DATE_EQUAL_CLAUSE, ICE_INDICES
from data_access.beta_attribution import security_ts_hist
from data_access.dbtables import ICE_INDEX_APP
from data_access.utils import tosqllist, get_where_clause, join_where_clauses


def calc_bond_betas(cusips, refdt):
    cusips = set(cusips)
    refdt = dfutil.tolist(refdt)
    df = security_ts_hist.get_timeseries(dtutil.dateadd(min(refdt), '-3m'),
                                         max(refdt),
                                         cusips,
                                         'BETA_WITH_ALPHA_12M_CDXHY')
    df = df.sort_values('ASOFDATE').drop_duplicates(subset='SEC_ID', keep='last') \
        .rename(columns={'VALUE': 'beta'}).set_index('SEC_ID')

    return df.reindex(cusips)['beta']


def update_bond_df(df):
    df['industry'] = df['industry_lvl3_desc']
    df['tenor'] = vectorized_calc_tenor(pd.to_datetime(df['maturity_date']).dt.date,
                                        pd.to_datetime(df['asof_date']).dt.date)
    return df


def get_bond_betas(asof_dt, tickers=None):
    select_cols = 'distinct asof_date, isin_number, ice_index_ticker, cusip, industry_lvl3_desc, oas, maturity_date, ' \
                  'trr_pct_1day_loc/100 as dtd_return, trr_pct_1week_local/100 as [1w_return], trr_pct_1month_local/100 as [1m_return], ' \
                  'trr_pct_mtd_loc/100 as mtd_return, trr_pct_qtd_local/100 as qtd_return, trr_pct_ytd_local/100 as ytd_return'
    asof_dt = dtutil.todate(asof_dt)
    asof_date_clause = ASOF_DATE_EQUAL_CLAUSE.format(asof_dt)
    ticker_clause = get_where_clause('ticker', tickers)
    where_clause = join_where_clauses(' AND ', [asof_date_clause, ticker_clause])

    bond_df = sqlutil.readdfsql(
        BOND_SQL.format(sql_replace_escape_char(select_cols), tosqllist(ICE_INDICES), where_clause), app=ICE_INDEX_APP)
    return get_bond_betas_with_df(bond_df)


def get_bond_betas_with_df(bond_df):
    bond_df = update_bond_df(bond_df)
    bond_h0a0_df = bond_df[bond_df['ice_index_ticker'] == 'H0A0']

    bond_betas = calc_bond_betas(bond_df['cusip'].dropna().tolist() + bond_h0a0_df['cusip'].dropna().tolist(),
                                 bond_df['asof_date'].dropna().unique().tolist())
    bond_df['beta'] = bond_df['cusip'].map(bond_betas)
    bond_h0a0_df['beta'] = bond_h0a0_df['cusip'].map(bond_betas)

    bond_df_est = bond_df[pd.isnull(bond_df['beta'])]
    cols = ['cusip', 'beta', 'industry', 'oas', 'tenor']
    modeldata = pd.concat([bond_df_est[cols], bond_h0a0_df[cols]]).drop_duplicates(subset=['cusip'])

    if len(modeldata['beta'].dropna()) > 0:
        modeldata = add_estimated_beta(modeldata, "beta ~ industry + oas + tenor")
        estimated_beta_by_cusip = modeldata.set_index('cusip')['estimated_beta']

        bond_df['estimated_beta'] = bond_df['cusip'].map(estimated_beta_by_cusip)
        bond_df['beta'] = bond_df['beta'].combine_first(bond_df['estimated_beta'])

    return bond_df.drop_duplicates(subset=['isin_number'])


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')
    df = get_bond_betas('2022-05-31')
    logging.info(df)
