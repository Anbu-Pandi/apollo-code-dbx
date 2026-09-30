import logging

import numpy as np
import pandas as pd

from data_access.dbtables.index_data.etf_constituents import get_constituents_by_date, get_latest_dt

index_list = ['LQD', 'VCIT', 'VCSH', 'BSV', 'HYG', 'IGSB', 'JNK', 'IGIB', 'SPSB', 'SPIB', 'USHY', 'HYLB', 'USIG',
              'SHYG', 'VCLT', 'SJNK', 'FLRN', 'SLQD', 'IGLB', 'BSCL', 'BSCM', 'ANGL', 'HYS'
              ]

pivot_index = ['composite_ticker', 'cusip', 'isin']
pivot_values = ['shares_held']
agg_func = {'shares_held': np.sum}


def _create_pivot(df, index, values, agg_func):
    df_pivot = df.pivot_table(index=index, values=values, aggfunc=agg_func)
    return df_pivot


def _get_pivot_data(curr_df, prev_df):
    prev_pivot = _create_pivot(prev_df, index=pivot_index, values=pivot_values, agg_func=agg_func)
    curr_pivot = _create_pivot(curr_df, index=pivot_index, values=pivot_values, agg_func=agg_func)

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

    net_change_df['cusip'] = net_change_df['cusip'].map(lambda x:x[:-1])
    diff_by_fund['cusip'] = diff_by_fund['cusip'].map(lambda x: x[:-1])

    return diff_by_fund, net_change_df


def get_etf_change(prev_date, curr_date):
    logging.info(f'Running ETF Change Analysis for {prev_date} vs {curr_date}')
    prev_date = get_latest_dt(prev_date)
    curr_date = get_latest_dt(curr_date)

    prev_df = get_constituents_by_date(prev_date, index=index_list)
    curr_df = get_constituents_by_date(curr_date, index=index_list)

    prev_pivot, curr_pivot = _get_pivot_data(curr_df, prev_df)

    joined_df = pd.merge(curr_pivot, prev_pivot, how='outer', left_index=True, right_index=True,
                         suffixes=('_curr', '_prev'), sort=False)

    diff_by_fund, net_change_df = _cal_change(joined_df)

    logging.info(f'Completed ETF Change Analysis for {prev_date} vs {curr_date}')
    return diff_by_fund, net_change_df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('prod')

    prev_date = '2021-07-30'
    curr_date = '2021-08-17'

    diff_by_fund, net_change_df = get_etf_change(prev_date, curr_date)

    print(diff_by_fund)

    print(net_change_df)

    pass
