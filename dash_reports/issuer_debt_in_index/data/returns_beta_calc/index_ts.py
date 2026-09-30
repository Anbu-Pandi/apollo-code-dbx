import logging
from data_access.beta_attribution import security_ts_hist
from common.utils import dtutil
import pandas as pd

from data_access.beta_attribution.jpm_dq import get_symbols_by_secid
from jp_data_api.dqhelper import get_expr_results
from common.utils.dfutil import tolist

from dash_reports.issuer_debt_in_index.data.returns_beta_calc.utils import get_period_returns

LOAN_REF_INDEX = 'iboxx_usloan_trs'
BOND_REF_INDEX = 'CDX_HY_OTR'


def get_index_level_jpm(index_tickers, startdt, enddt):
    jpquery = []
    for index_ticker in tolist(index_tickers):
        jpquery.append(get_symbols_by_secid(index_ticker, 'DAILY')['QUERY'].iloc[0])
    df = get_expr_results(jpquery, startdt, enddt, 'd')
    df.index = pd.to_datetime(df.index.map(str))
    df.columns = tolist(index_tickers)
    df = df.sort_index()
    return df


def get_index_returns_jpm(index_tickers, startdt, enddt):
    df = get_index_level_jpm(index_tickers, startdt, enddt)
    df = df / df.shift(1) - 1
    return df


def get_index_period_returns(index_tickers, dt):
    from_dt = dtutil.dateadd(dt, '-1y')
    index_ts = get_index_level_jpm(index_tickers, from_dt, dt)
    ret = {}
    for index_ticker in tolist(index_tickers):
        ret[index_ticker] = get_period_returns(index_ts[index_ticker], dt)
    ret = pd.DataFrame(ret).unstack().unstack()
    return ret


def get_loan_beta_multiplier(dt):
    df = security_ts_hist.get_timeseries(dtutil.dateadd(dt, f'-{int(365 * 1.5)}d'),
                                         dt,
                                         LOAN_REF_INDEX,
                                         'BETA_WITH_ALPHA_12M_CDXHY',
                                         'WEEKLY',
                                         'JPM')
    return df['VALUE'].mean()


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')

    df1 = get_index_returns_jpm(LOAN_REF_INDEX, '2021-05-31', '2022-05-31')
    df2 = get_index_period_returns(LOAN_REF_INDEX, '2022-05-31')
    df3 = get_index_period_returns(BOND_REF_INDEX, '2022-05-31')
    df4 = get_index_period_returns([LOAN_REF_INDEX, BOND_REF_INDEX], '2022-05-31')

    logging.info(df1)
    logging.info(df2)
    logging.info(df3)
    logging.info(df4)
