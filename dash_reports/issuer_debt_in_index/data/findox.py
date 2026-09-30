import logging

import numpy as np
import pandas as pd
from common.utils import dtutil
from data_access.dbtables.findox.financial_data import get_company_financials
from data_access.dbtables.findox.sec_reference import get_findox_ids_from_instrument_ids, get_findox_ids_tickers
from ml_analytics.text_match import TextMatcher

to_factset_ids_map = {'IQ_EBITDA': 'FF_EBITDA_OPER',
                      'IQ_CAPEX': 'FF_CAPEX',
                      'IQ_NET_INTEREST_EXP': 'FF_INT_EXP_NET',
                      'IQ_TOTAL_DEBT': 'FF_DEBT',
                      'IQ_NET_DEBT': 'FF_NET_DEBT',
                      'IQ_TOTAL_ASSETS': 'FF_ASSETS',
                      'IQ_CASH_EQUIV': 'FF_CASH_ONLY',
                      'IQ_TOTAL_REVENUE': 'FF_SALES',
                      'IQ_TOTAL_DEBT_EBITDA': 'FF_DEBT_EBITDA_OPER',
                      'CUSTOM_TOTAL_DEBT_ASSETS': 'FF_DEBT_ASSETS',
                      'CUSTOM_NET_DEBT_ASSETS': 'FF_NET_DEBT_ASSETS',
                      '': 'FF_COM_SHS_OUT_EPS_DIL',
                      '': 'FF_MKT_VAL',
                      '': 'FF_ENTRPR_VAL', }


def cosine_sim(df, col1, col2):
    len_df = len(df)

    m = TextMatcher(list(df[col1].fillna('#n/a')) + list(df[col2].fillna('#n/a')))
    m.tfidf_fit()

    cosine_scores = np.array([
        m.tfidf[[i]].dot(m.tfidf[[i + len_df]].T).toarray()[0][0]
        for i in range(len_df)
    ])

    is_na_cols = df[col1].isna() | df[col2].isna()
    df['cosine_score'] = 1 - cosine_scores
    df.loc[is_na_cols, 'cosine_score'] = np.nan

    return df


def merge_findox_financials(debt_df):
    todt = pd.to_datetime(debt_df['asof_date'], format='mixed').dt.date.max()
    fromdt = dtutil.dateadd(todt, '-1y')

    findox_instrument_issuer_map = debt_df[['instrument_id', 'ticker']].dropna().drop_duplicates()
    sec_ref = get_findox_ids_from_instrument_ids(
        findox_instrument_issuer_map['instrument_id'].dropna().unique().tolist(), todt)

    findox_issuer_map = \
        pd.merge(findox_instrument_issuer_map, sec_ref, on=['instrument_id']).groupby(['ticker', 'issuer_id'])[
            'instrument_id'].count() \
            .sort_values().reset_index() \
            .drop_duplicates(subset=['ticker'], keep='last') \
            .drop(columns=['instrument_id'])

    findox_issuer_map2 = get_findox_ids_tickers() \
        .rename(columns={'ticker': 'ticker', 'findox_id': 'issuer_id', 'trading_name': 'findox_issuer_name'})
    findox_issuer_map2 = pd.merge(findox_issuer_map2,
                                  debt_df[['ticker', 'issuer_name']].dropna().drop_duplicates(),
                                  on=['ticker'], how='left')

    findox_issuer_map2 = findox_issuer_map2[~findox_issuer_map2['issuer_id'].isin(findox_issuer_map['issuer_id'])]
    findox_issuer_map2 = cosine_sim(findox_issuer_map2, 'issuer_name', 'findox_issuer_name')
    findox_issuer_map2 = findox_issuer_map2[findox_issuer_map2['cosine_score'] < 0.7][['ticker', 'issuer_id']]

    findox_issuer_map = pd.concat([findox_issuer_map, findox_issuer_map2], ignore_index=True)
    findox_issuer_map['issuer_id'] = pd.to_numeric(findox_issuer_map['issuer_id'], errors='coerce')
    company_ids = findox_issuer_map['issuer_id'].dropna().astype(int).unique().tolist()

    findox_df = get_company_financials(company_ids, fromdt, todt, 'ltm',
                                       dataitems=list(to_factset_ids_map.keys()))
    findox_df['findox_id'] = pd.to_numeric(findox_df['findox_id'])
    findox_df = pd.merge(findox_df, findox_issuer_map.rename(columns={'issuer_id': 'findox_id'}),
                         on=['findox_id'], suffixes=('_findox', ''))

    findox_df = findox_df[findox_df['period'].str[-8:] == findox_df['last_period']]
    findox_df = findox_df.sort_values(['ticker', 'period_id', 'line_id'], ascending=[True, False, True]) \
        .drop_duplicates(subset=['ticker', 'period_id', 'line_id'])
    findox_df['factset_id'] = findox_df['dataitem_id'].map(lambda x: to_factset_ids_map.get(x, None))
    findox_df = pd.pivot_table(findox_df, index='ticker', columns='factset_id', values='data_ltm', aggfunc='first')

    if len(findox_df) > 0:
        for rev_col in ['FF_INT_EXP_NET', 'FF_CAPEX']:
            if rev_col in findox_df.columns:
                findox_df[rev_col] = findox_df[rev_col] * -1

        debt_df = pd.merge(debt_df, findox_df, how='left', left_on='ticker', right_on='ticker',
                           suffixes=('_factset', '_findox'))
        for col in debt_df.columns:
            if col.endswith('_findox'):
                col = col.replace('_findox', '')
                debt_df[col] = debt_df[f'{col}_factset'].combine_first(debt_df[f'{col}_findox'])

        show_cols = []
        for col in debt_df.columns:
            if col.endswith('_findox'):
                show_cols.extend([col, col.replace('_findox', '_factset'), col.replace('_findox', '')])
        debt_df[show_cols].dropna(how='all', axis=1)

    return debt_df


if __name__ == '__main__':
    from common.utils import sqlutil
    from data_access.dbtables import FINDOX_APP

    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')

    dt = dtutil.todate('2024-12-09')
    df = sqlutil.readdfsql(f"SELECT * FROM dbo.debt_features where asof_date='{dt}' ", app=FINDOX_APP)

    df = df.drop(columns=[x for x in df.columns if x.endswith('_findox') or x.endswith('_factset')])
    findf = merge_findox_financials(df)
    logging.info(findf.to_string())
