import logging
import pandas as pd
from common.utils import dtutil
from common.utils import sqlutil

from data_access.dbtables import JPM_LL_APP
from dash_reports.issuer_debt_in_index.data.sql_const import JPM_LOAN_SELECT, ASOF_DATE_BETWEEN_CLAUSE
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.utils import add_estimated_beta, vectorized_calc_tenor, \
    beta_with_index, get_period_returns
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.index_ts import get_index_returns_jpm, LOAN_REF_INDEX


def calc_loan_betas(loan_df):
    dt = loan_df['asof_date'].max()
    loantrs_return_ds = get_index_returns_jpm(LOAN_REF_INDEX, dtutil.dateadd(dt, '-1y-1b'), dt)
    loantrs_return_ds.index = pd.to_datetime(loantrs_return_ds.index)

    loan_betas = loan_df.groupby('loanx')[['asof_date', 'total_return']] \
        .apply(lambda x: beta_with_index(x, loantrs_return_ds.dropna()))

    return loan_betas[['beta']].dropna()


def update_loan_df(df):
    df['asof_date'] = pd.to_datetime(df['asof_date']).dt.date
    df['tenor'] = vectorized_calc_tenor(pd.to_datetime(df['maturity_date']).dt.date,
                                        pd.to_datetime(df['asof_date']).dt.date)

    df.sort_values('asof_date', inplace=True)
    df['total_return_index'] = df.groupby('loanx')[['total_return']] \
        .apply(lambda df: (1 + df).cumprod()).droplevel('loanx', axis=0)

    return df


def get_loan_betas(dt):
    dt = dtutil.todate(dt)
    fromdt = dtutil.dateadd(dt, '-1y')
    select_cols = ('asof_date, loanx as loanx, tot_ret as total_return, '
                   'leveraged_mkt_wt+sec_lien_mkt_wt+liquid_mkt_wt+bb_b_mkt_wt+euro_mkt_wt as weights, '
                   'offer_spread/1e4 as libor_spread, ind_segment as narrow_industry, consolidated_rating as current_rating, maturity_date as maturity_date')
    loan_df = sqlutil.readdfsql(JPM_LOAN_SELECT.format(select_cols, ASOF_DATE_BETWEEN_CLAUSE.format(fromdt, dt)), app=JPM_LL_APP)
    loan_df['asof_date'] = pd.to_datetime(loan_df['asof_date']).dt.date

    loan_df = update_loan_df(loan_df)

    loan_betas = calc_loan_betas(loan_df)
    loan_returns = loan_df.set_index('asof_date').groupby('loanx')['total_return_index'] \
        .apply(lambda x: get_period_returns(x, dt)).unstack()
    return_cols = loan_returns.columns.tolist()

    loan_df_curr = loan_df[pd.to_datetime(loan_df['asof_date']).dt.date == dtutil.todate(dt)]
    loan_df_curr['beta'] = loan_betas.reindex(loan_df_curr['loanx'])['beta'].values
    loan_df_curr = add_estimated_beta(loan_df_curr, "beta ~ narrow_industry + current_rating + tenor + libor_spread")
    loan_df_curr['beta'] = loan_df_curr['beta'].combine_first(loan_df_curr['estimated_beta'])
    (loan_df_curr[return_cols[0]],
     loan_df_curr[return_cols[1]],
     loan_df_curr[return_cols[2]],
     loan_df_curr[return_cols[3]],
     loan_df_curr[return_cols[4]],
     loan_df_curr[return_cols[5]]) = zip(*loan_returns.reindex(loan_df_curr['loanx']).values)

    return loan_df_curr


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_loan_betas('2024-10-29')
    logging.info(df)
