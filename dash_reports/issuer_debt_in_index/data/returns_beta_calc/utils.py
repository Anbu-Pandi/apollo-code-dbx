import logging

import numpy as np
import pandas as pd

from common.utils import dtutil

BETA_CALC_THRESHOLD_PERCENT = 0.75


def add_estimated_beta(df, modelstring):
    from statsmodels.formula.api import ols
    model = ols(modelstring, data=df)
    res = model.fit()
    df['estimated_beta'] = res.predict(df)
    return df


def get_period_dates(refdt):
    periods = {'ytd': dtutil.boyear,
               'mtd': dtutil.bomonth,
               'qtd': dtutil.boqtr,
               '1m': lambda x: dtutil.adjustdate(dtutil.dateadd(x, '-1m')),
               '1w': lambda x: dtutil.adjustdate(dtutil.dateadd(x, '-1w')),
               'dtd': lambda x: x}
    dts = {}
    for period, func in periods.items():
        dts[period] = dtutil.dateadd(func(refdt), '-1b')

    return pd.Series(dts)


def get_period_returns(ds, refdt):
    rets = {}
    for period, start_dt in get_period_dates(refdt).iteritems():
        ds_slice = ds.loc[start_dt:refdt]
        if len(ds_slice) > 0:
            rets[f'{period}_return'] = ds_slice.iloc[-1] / ds_slice.iloc[0] - 1
    return pd.Series(rets)


def calc_tenor(mty, refdt):
    return (mty - refdt).days / 365.25


vectorized_calc_tenor = np.vectorize(calc_tenor)


def linearmodel(X, Y):
    X = np.concatenate([np.ones_like(X), X], axis=1)
    b = np.linalg.pinv(X.T.dot(X)).dot(X.T).dot(Y)
    return b


def beta_with_index(df, ref_df):
    betas = [np.nan, np.nan]
    df = df.set_index('asof_date')

    df.index = pd.to_datetime(df.index)
    ref_df.index = pd.to_datetime(ref_df.index)

    df = df.resample('W-FRI').sum().replace([np.inf, -np.inf],np.nan).dropna(how='any')
    ref_df = ref_df.resample('W-FRI').sum().replace([np.inf, -np.inf],np.nan).dropna(how='any')

    betadf = pd.merge(df, ref_df, left_index=True, right_index=True).dropna(how='any')
    if len(betadf) > BETA_CALC_THRESHOLD_PERCENT * len(ref_df):
        betas = linearmodel(betadf[[df.columns[0]]].values, betadf[ref_df.columns[0]].values)
    return pd.Series({'alpha': betas[0], 'beta': betas[1]})


def add_explain_returns(df, index_rets_df, retcols):
    for retcol in retcols:
        df[f'index_{retcol}'] = df['index_ticker'] \
            .map(lambda x: index_rets_df.loc[x, retcol] if not pd.isnull(x) else np.nan)
        df[f'beta_{retcol}'] = df['beta'] * df[f'index_{retcol}']
        df[f'alpha_{retcol}'] = df[retcol] - df[f'beta_{retcol}']

        df[f'bond_point_{retcol}'] = df['price'] - df['price'] / (1 + df[retcol])
        df[f'bond_point_beta_{retcol}'] = df['price'] - df['price'] / (1 + df[f'beta_{retcol}'])
        df[f'bond_point_alpha_{retcol}'] = df['price'] - df['price'] / (1 + df[f'alpha_{retcol}'])

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    df = pd.Series([100, 101, 102, 103, 104],
                   index=pd.to_datetime(['2021-12-31', '2022-04-01', '2022-05-01', '2022-05-30', '2022-05-31']))
    ret = get_period_returns(df, '2022-05-31')
    logging.info(ret)
