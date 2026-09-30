import logging

import pandas as pd

from common.utils import sqlutil, dtutil
from common.utils.sqlutil import sql_replace_escape_char
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.loans import get_loan_betas
from dash_reports.issuer_debt_in_index.data.sql_const import JPM_LOAN_SQL1, LOAN_PRICE_SQL, \
    ASOF_DATE_EQUAL_CLAUSE, ASOF_DATE_IN_CLAUSE, LOAN_RISK_SQL, LOAN_OAS_PCTILE_SQL
from data_access.dbtables import JPM_LL_APP, MARKIT_LOAN_PRICING_APP
from data_access.utils import tosqllist, get_where_clause


def _map_seniority(seniority, lien_type):
    seniority = '' if pd.isna(seniority) else seniority.lower()
    lien_type = '' if pd.isna(lien_type) else lien_type.lower()

    if ('super' in seniority or 'senior secured' in seniority) and 'first lien' in lien_type:
        return ('SECR_1L', 'SEC')
    elif ('super' in seniority or 'senior secured' in seniority) and 'second lien' in lien_type:
        return ('SECR_2L', 'SEC')
    elif 'second lien' in lien_type or '2nd lien' in lien_type:
        return ('SECR_2L', 'SEC')
    elif 'third lien' in lien_type or '3rd lien' in lien_type:
        return ('SECR_3L', 'SEC')
    elif 'unsecured' in seniority:
        return ('SENR', 'UNSEC')
    elif 'secured' in seniority or 'super' in seniority or 'lien' in lien_type:
        return ('SECR', 'SEC')
    elif any(x in seniority for x in ['sub', 'equity', 'pref']):
        return ('SUB', 'SUB')
    else:
        return ('TBD', 'TBD')


def get_loan_df(asof_date, tickers=None):
    asof_date_clause = ASOF_DATE_EQUAL_CLAUSE.format(asof_date)
    ticker_clause = get_where_clause('li.ticker', tickers, use_databricks=True)
    where_clause = asof_date_clause + (f' and {ticker_clause}' if ticker_clause else '')
    loans_df = sqlutil.readdfsql(JPM_LOAN_SQL1.format(where_clause), app=JPM_LL_APP)

    loans_price_df = sqlutil.readdfsql(
        sql_replace_escape_char(LOAN_PRICE_SQL).format(ASOF_DATE_IN_CLAUSE.format(tosqllist([asof_date]))),
        app=MARKIT_LOAN_PRICING_APP)
    loan_betas_df = get_loan_betas(asof_date)
    loans_risk_df = sqlutil.readdfsql(LOAN_RISK_SQL.format(asof_date), app=JPM_LL_APP).drop(columns=['price'])
    loan_oas_df = sqlutil.readdfsql(LOAN_OAS_PCTILE_SQL.format(dtutil.dateadd(asof_date, '-1y'), asof_date),
                                    app=JPM_LL_APP) \
        .drop(columns=['asof_date', 'oas'])

    loans_df = loans_df.drop_duplicates(subset=['id'], keep='first')
    loans_df['original_amount'] = loans_df['original_amount'].fillna(0) / 1e6
    loans_df['current_outstanding'] = loans_df['current_outstanding'].fillna(0) / 1e6

    loans_price_df['asof_date'] = pd.to_datetime(loans_price_df['asof_date']).dt.date
    loans_price_df = pd.pivot_table(loans_price_df, columns='asof_date', index='loanx_id', values='evaluated_price') \
        .reset_index().rename(columns={pd.to_datetime(asof_date).date(): 'price'})

    loan_betas_df['asof_date'] = pd.to_datetime(loan_betas_df['asof_date'])

    loans_df = pd.merge(loans_df,
                        loans_price_df[list(set(['loanx_id']) | set(loans_price_df.columns) - set(loans_df.columns))],
                        on=['loanx_id'], how='left')
    loans_df = pd.merge(loans_df,
                        loan_betas_df[
                            list(set(['asof_date', 'loanx']) | set(loan_betas_df.columns) - set(loans_df.columns))],
                        left_on=['asof_date', 'loanx_id'], right_on=['asof_date', 'loanx'], how='left')
    loans_df = pd.merge(loans_df, loans_risk_df, left_on=['loanx_id'], right_on=['loanx'], how='left')
    loans_df = pd.merge(loans_df, loan_oas_df, left_on=['loanx_id'], right_on=['loanx'], how='left')

    if len(loans_df) > 0:
        loans_df[['seniority', 'secured_unsecured']] = \
            loans_df.apply(lambda x: _map_seniority(x['seniority'], x['lien_type']), axis=1, result_type='expand')

    loans_df['instrument_type'] = 'Loan'

    return loans_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_loan_df('2021-02-23')
    logging.info(df)
