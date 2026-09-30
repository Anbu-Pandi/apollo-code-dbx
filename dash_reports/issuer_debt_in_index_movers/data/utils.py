import logging

import numpy as np
import pandas as pd

from common.utils import dtutil
from dash_reports.issuer_debt_in_index_movers.data import loan_index, bond_index
from data_access.beta_attribution.jpm_dq import get_symbols_by_secid
from jp_data_api.dqhelper import get_expr_results

BETA_CALC_THRESHOLD_PERCENT = 0.75


def get_idx_level(idx_ticker, startdt, enddt):
    jpquery = get_symbols_by_secid(idx_ticker, 'DAILY')['QUERY'].iloc[0]
    df = get_expr_results(jpquery, startdt, enddt, 'd')
    df.index = pd.to_datetime(df.index.map(str)).date
    df = df.sort_index(ascending=False)
    return df


def get_period_returns_for_index(idx_ticker, refdt):
    ydt = dtutil.dateadd(dtutil.boyear(refdt), '-1b')
    mdt = dtutil.dateadd(dtutil.bomonth(refdt), '-1b')
    wdt = dtutil.dateadd(dtutil.boweek(refdt), '-1b')
    yestdt = dtutil.dateadd(refdt, '-1b')

    df = get_idx_level(idx_ticker, ydt, refdt)

    yret = (df.loc[(dtutil.todate(refdt))].values[0] / df.loc[(dtutil.todate(ydt))].values[0] - 1)
    mret = (df.loc[(dtutil.todate(refdt))].values[0] / df.loc[(dtutil.todate(mdt))].values[0] - 1)
    wret = (df.loc[(dtutil.todate(refdt))].values[0] / df.loc[(dtutil.todate(wdt))].values[0] - 1)
    dret = (df.loc[(dtutil.todate(refdt))].values[0] / df.loc[(dtutil.todate(yestdt))].values[0] - 1)
    return {'return_ytd': yret, 'return_mtd': mret, 'return_wtd': wret, 'return_dtd': dret}


def get_wt_ret_fromdate(df, refdt, dtcol, cusipcol, pricecol, wtcol, retcol):
    wt = df[pd.to_datetime(df[dtcol]).dt.date < refdt].groupby(cusipcol)[wtcol].apply(lambda x: x.iloc[-1])
    wt = df[cusipcol].map(wt)
    price = df[pd.to_datetime(df[dtcol]).dt.date < refdt].groupby(cusipcol)[pricecol].apply(lambda x: x.iloc[-1])
    price = df[cusipcol].map(price)
    ret = df[pd.to_datetime(df[dtcol]).dt.date >= refdt] \
        .groupby(cusipcol)[retcol].apply(lambda x: ((1 + x).cumprod() - 1)) \
        .droplevel(cusipcol, axis=0)

    return price, wt, ret


def update_df_with_wts_rets(df, dtcol, cusipcol, pricecol, wtcol, retcol):
    refdt = pd.to_datetime(df[dtcol]).dt.date.max()
    return_window_map = {'dtd': ('prev_day', lambda x: x),
                         'wtd': ('prev_week', dtutil.boweek),
                         'mtd': ('prev_month', dtutil.bomonth),
                         'ytd': ('prev_year', dtutil.boyear)}

    for k, v in return_window_map.items():
        tag = v[0]
        dtfunc = v[1]
        (df[f'price_{tag}'], df[f'wt_{tag}'], df[f'return_{k}']) = \
            get_wt_ret_fromdate(df, dtfunc(refdt), dtcol, cusipcol, pricecol, wtcol, retcol)
    return df


def linearmodel(X, Y):
    X = np.concatenate([np.ones_like(X), X], axis=1)
    b = np.linalg.pinv(X.T.dot(X)).dot(X.T).dot(Y)
    return b


def beta_with_index(df, indexdf):
    betas = [np.nan, np.nan]
    df = df.set_index('asof_date')
    betadf = pd.merge(df, indexdf, left_index=True, right_index=True)
    if len(betadf) > BETA_CALC_THRESHOLD_PERCENT * len(indexdf):
        betas = linearmodel(betadf[[df.columns[0]]].values, betadf[indexdf.columns[0]].values)
    return pd.Series({'alpha': betas[0], 'beta': betas[1]})


def explain_return(df, idx_ret, retcol):
    df[f'beta_{retcol}'] = df['beta'] * idx_ret
    df[f'alpha_{retcol}'] = df[retcol] - df[f'beta_{retcol}']

    df[f'bond_point_{retcol}'] = df['price'] - df['price'] / (1 + df[retcol])
    df[f'bond_point_beta_{retcol}'] = df['price'] - df['price'] / (1 + df[f'beta_{retcol}'])
    df[f'bond_point_alpha_{retcol}'] = df['price'] - df['price'] / (1 + df[f'alpha_{retcol}'])

    return df


def add_estimated_beta(indexdf, modelstring):
    df = indexdf.copy()
    from statsmodels.formula.api import ols
    model = ols(modelstring, data=df)
    res = model.fit()
    df['estimated_beta'] = res.predict(df)
    return df


def get_reference_returns(index, dt):
    if index.lower() in loan_index.LOAN_INDEX_TICKERS:
        reference_index = loan_index.LOAN_REF_INDEX
    else:
        reference_index = bond_index.BOND_REF_INDEX
    reference_returns = get_period_returns_for_index(reference_index, dt)
    return pd.DataFrame(reference_returns, index=[reference_index.upper()])


def add_relative_spread(df):
    from common.utils import creditutil
    df['maturity_bucket'] = creditutil.map_maturity_bucket(df['tenor'])
    df['rating_bucket'] = creditutil.map_rating(df['rating'])

    df['spread_x_wt'] = df['weights'] * df['oas']

    for gby in [['maturity_bucket'], ['industry'], ['rating_bucket'], ['industry', 'rating_bucket'], ['maturity_bucket', 'rating_bucket']]:
        gby_name = 'oas_' + '_'.join(gby)
        gby_spds = (df.groupby(gby)['spread_x_wt'].sum() / df.groupby(gby)['weights'].sum()) \
            .to_frame(gby_name).reset_index()
        df = pd.merge(df, gby_spds, on=gby, how='left')

    return df


def get_index_movers(index, dt, trades=None):
    if index.lower() in loan_index.LOAN_INDEX_TICKERS:
        indexdf = loan_index.get_index_df_with_betas(dt)
    else:
        indexdf = bond_index.get_index_df_with_betas(index, dt, trades)

    reference_returns = get_reference_returns(index, dt)
    indexdf['realized_beta'] = indexdf['beta']
    indexdf['beta'] = indexdf['beta'].combine_first(indexdf['estimated_beta'])

    for retcol, wtcol in zip(['return_dtd', 'return_wtd', 'return_mtd', 'return_ytd', ],
                             ['wt_prev_day', 'wt_prev_week', 'wt_prev_month', 'wt_prev_year', ], ):
        indexdf = explain_return(indexdf, reference_returns.iloc[0][retcol], retcol)

    indexdf = add_relative_spread(indexdf)
    return indexdf


def calc_tenor(mty, refdt):
    return (mty - refdt).days / 365.25


vectorized_calc_tenor = np.vectorize(calc_tenor)

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init()

    from dash_reports.report_common.data.data_model import datastore

    dt = dtutil.todate('2022-11-10')
    datastore.paraminit()
    df = datastore.get_index_returns('H0A0', dt)
    logging.info(df.head())

    df = add_relative_spread(df)
    logging.info(df.head())
