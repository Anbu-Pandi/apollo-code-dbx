import logging

import numpy as np
import pandas as pd

from common.utils import dtutil
from dash_reports.trace.data.trace_flow import get_trace_volume
from data_access.dbtables.index_data.etf_constituents import get_constituents_by_date, get_latest_dt


def get_bond_volume(fromdt, todt, join_uncapped=True, tickers=None):
    trace_df = get_trace_volume(fromdt, todt, tenor='1m', join_uncapped=join_uncapped, tickers=tickers)
    return trace_df


BOND_ETF_LIST = ['LQD', 'VCIT', 'VCSH', 'BSV', 'HYG', 'IGSB', 'JNK', 'IGIB', 'SPSB', 'SPIB', 'USHY', 'HYLB', 'USIG',
                 'SHYG', 'VCLT', 'SJNK', 'FLRN', 'SLQD', 'IGLB', 'BSCL', 'BSCM', 'ANGL', 'HYS']

PIVOT_INDEX = ['composite_ticker', 'cusip', 'isin']
PIVOT_VALUES = ['shares_held']
AGG_FUNC = {'shares_held': np.sum}


def _get_pivot_data(curr_df, prev_df):
    prev_pivot = prev_df.pivot_table(index=PIVOT_INDEX, values=PIVOT_VALUES, aggfunc=AGG_FUNC)
    curr_pivot = curr_df.pivot_table(index=PIVOT_INDEX, values=PIVOT_VALUES, aggfunc=AGG_FUNC)

    return prev_pivot, curr_pivot


def _cal_change(df):
    diff_by_fund = pd.DataFrame()
    diff_by_fund['changed_qty'] = df['shares_held_curr'] - df['shares_held_prev']
    '''to get dealers perspective'''
    diff_by_fund = diff_by_fund.mul(-1)

    diff_by_fund = diff_by_fund.unstack('composite_ticker')
    net_change_df = diff_by_fund.sum(axis=1).reset_index().rename(columns={0: 'etf_change'})

    diff_by_fund = diff_by_fund.reset_index().reset_index(drop=True)
    diff_by_fund.columns = [x[0] if x[1] == '' else x[1] for x in diff_by_fund.columns]

    net_change_df['cusip'] = net_change_df['cusip'].map(lambda x: x[:-1])
    diff_by_fund['cusip'] = diff_by_fund['cusip'].map(lambda x: x[:-1])

    return diff_by_fund, net_change_df


def get_etf_change(curr_date):
    prev_date = dtutil.dateadd(curr_date, '-1m')
    logging.info(f'Running ETF Change Analysis for {prev_date} vs {curr_date}')
    prev_date = get_latest_dt(prev_date, tickers=BOND_ETF_LIST)
    curr_date = get_latest_dt(curr_date, tickers=BOND_ETF_LIST)

    prev_df = get_constituents_by_date(prev_date, index=BOND_ETF_LIST)
    curr_df = get_constituents_by_date(curr_date, index=BOND_ETF_LIST)

    prev_pivot, curr_pivot = _get_pivot_data(curr_df, prev_df)

    joined_df = pd.merge(curr_pivot, prev_pivot, how='outer', left_index=True, right_index=True,
                         suffixes=('_curr', '_prev'), sort=False)

    diff_by_fund, net_change_df = _cal_change(joined_df)

    logging.info(f'Completed ETF Change Analysis for {prev_date} vs {curr_date}')
    return diff_by_fund, net_change_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')
    df = get_etf_change('2022-05-31')
    logging.info(df)
