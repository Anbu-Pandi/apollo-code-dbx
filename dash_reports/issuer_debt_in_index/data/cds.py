import logging

import pandas as pd

from common.utils import sqlutil, dtutil
from common.utils.sqlutil import sql_replace_escape_char
from dash_reports.issuer_debt_in_index.data.irswap import get_irswap_par
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.cds import get_cds_returns
from dash_reports.issuer_debt_in_index.data.sql_const import CDS_SQL, ASOF_DATE_EQUAL_CLAUSE, CDS_PCTILE_SQL, \
    CDS_LIQUIDITY_5Y_SQL
from data_access.dbtables import MARKIT_CDS_PRICING_APP
from data_access.utils import tosqllist

CDS_SENIORITY_MAP = {'SNRFOR': 'UNSEC',
                     'SNRLAC': 'UNSEC',
                     'PREFT1': 'SUB',
                     'SECDOM': 'SEC',
                     'SUBLT2': 'SUB',
                     'JRSUBUT2': 'SUB'}


def get_cds_df(asof_date):
    ccys = ['USD', 'EUR', 'GBP', 'JPY']
    tenor = ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y']
    asof_clause = ASOF_DATE_EQUAL_CLAUSE.format(asof_date)
    cds_df = sqlutil.readdfsql(
        sql_replace_escape_char(CDS_SQL).format(asof_clause, tosqllist(tenor), tosqllist(ccys), asof_clause),
        app=MARKIT_CDS_PRICING_APP)
    cds_df['seniority'] = cds_df['seniority'].map(lambda x: CDS_SENIORITY_MAP.get(x, 'UNSEC'))
    cds_returns = get_cds_returns(asof_date, ccys, tenor)
    cds_spread_pctile_df = sqlutil.readdfsql(
        sql_replace_escape_char(CDS_PCTILE_SQL).format(dtutil.dateadd(asof_date, '-1y'), asof_date),
        app=MARKIT_CDS_PRICING_APP) \
        .drop(columns=['asof_date', 'oas'])

    cds_df = pd.merge(cds_df, cds_returns,
                      on=['ticker', 'red_code', 'tier', 'currency', 'doc_clause', 'tenor'],
                      how='left')
    cds_df = pd.merge(cds_df, cds_spread_pctile_df,
                      left_on=['ticker', 'red_code', 'tier', 'currency', 'doc_clause', 'tenor'],
                      right_on=['ticker', 'red_code', 'tier', 'ccy', 'doc_clause', 'tenor'],
                      how='left')

    cds_dts = {}
    irswap_par_rates = {}
    for t in tenor:
        cds_dts[t.upper()] = max(dtutil.dateadd(asof_date, '1b'), dtutil.cdsdate(asof_date, t))
        try:
            irswap_par_rates[t.upper()] = get_irswap_par(asof_date, cds_dts[t.upper()], ccys)
        except:
            irswap_par_rates[t.upper()] = get_irswap_par(asof_date, dtutil.dateadd(cds_dts[t.upper()], '2b'), ccys)

    cds_df['maturity'] = cds_df['tenor'].apply(lambda x: cds_dts[x.upper()])
    cds_df['instrument_type'] = 'CDS'
    cds_df['index'] = 'CDS'

    cds_df['yield_to_worst_conv'] = cds_df['yield_to_worst_conv'] \
                                    + cds_df[['tenor', 'currency']].apply(lambda x:
                                                                          irswap_par_rates[x['tenor'].upper()] \
                                                                          .get(x['currency'], 0.0) * 100, axis=1)
    cds_df['yield_to_maturity_conv'] = cds_df['yield_to_maturity_conv'] \
                                       + cds_df[['tenor', 'currency']].apply(lambda x:
                                                                             irswap_par_rates[x['tenor'].upper()] \
                                                                             .get(x['currency'], 0.0) * 100, axis=1)

    return cds_df


def get_cds_liquidity_5y_candidates(asof_date, tickers=None, red_codes=None):
    ccys = ['USD', 'EUR', 'GBP', 'JPY']
    asof_clause = ASOF_DATE_EQUAL_CLAUSE.format(asof_date)
    extra_clauses = []
    if tickers:
        extra_clauses.append(f"mcds.ticker in {tosqllist(tickers)}")
    if red_codes:
        extra_clauses.append(f"mcds.red_code in {tosqllist(red_codes, isstr=True)}")
    extra_clause = ''
    if extra_clauses:
        extra_clause = 'and ' + ' and '.join(extra_clauses)
    sql = sql_replace_escape_char(CDS_LIQUIDITY_5Y_SQL).format(
        tosqllist(ccys),
        asof_clause + extra_clause,
    )
    return sqlutil.readdfsql(sql, app=MARKIT_CDS_PRICING_APP)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_cds_df('2025-06-02')
    logging.info(df)
