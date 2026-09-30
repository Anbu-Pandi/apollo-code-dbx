import logging
import os

import numpy as np
import pandas as pd

from common.utils import dtutil
from data_access.dbtables.flow_data.trace import get_trades
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.trace.api import add_model_volume_to_trades

BLOB_TRACE_WEEKLY_ANALYSIS_FOLDER = 'trace_weekly_flow'


def get_recent_parquet_file():
    folders = sparkmanager.listfiles(BLOB_TRACE_WEEKLY_ANALYSIS_FOLDER)
    folders = sorted(list(set([os.path.split(x)[0] for x in folders])))
    tracefile = sorted(folders)[-1]
    logging.info(f"most recent trace file: {tracefile}")
    return tracefile


def get_trace_weekly_analysis():
    if not sparkmanager.isinit:
        sparkmanager.init()
    df = sparkmanager.readfile_pandas(get_recent_parquet_file(), BLOB_TRACE_WEEKLY_ANALYSIS_FOLDER)
    return df


def get_funds_by_cusip():
    from dash_reports.report_common.data.data_model import datastore
    currriskdf = datastore.get_cur_risk()
    fundsbycusip = currriskdf.groupby('CUSIP')[['Fund Entity Code']].agg(
        lambda x: ",".join(sorted(np.unique(x)))).reset_index()
    return fundsbycusip


def get_cusip_by_funds():
    from dash_reports.report_common.data.data_model import datastore
    currriskdf = datastore.get_cur_risk()
    cusipbyfunds = currriskdf[['Fund Entity Code', 'CUSIP']].dropna(how='any').drop_duplicates()
    return cusipbyfunds


def merge_df_with_funds(df):
    fundsbycusip = get_funds_by_cusip()
    df = pd.merge(df, fundsbycusip, how='left', left_on='Cusip', right_on='CUSIP') \
        .drop(columns=['CUSIP'])
    return df


def get_trace_history():
    df = get_trace_weekly_analysis()
    df.rename(columns={
        'effective_date': 'Trade Date',
        'week_end': 'Week Ending Date',
        'month_end': 'Month End Date',
        'yield_bucket': 'Yield Bucket',
        'cusip': 'Cusip',
        'ticker': 'Ticker',
        'sector': 'Sector',
        'trade_counterparties': 'Trade Counterparties',
        'avg_price': 'Avg Price',
        'sum_net_qty_apo_estimated': 'Net Quantity',
        'sum_gross_qty_apo_estimated': 'Gross Quantity',
        'avg_oas_spread': 'Avg OAS',
        'sum_sell_qty': 'Sell Quantity',
        'sum_buy_qty': 'Buy Quantity',
        'count_cusip': 'Trade Count',
    }, inplace=True)

    dealermap = {'Dealer_2_Client': 'DLR to Dom',
                 'Dealer_2_Affiliate': 'DLR to Foreign',
                 'Dealer_2_Dealer': 'Inter-DLR'}
    df = df[df['Trade Counterparties'].isin(list(dealermap.keys()))]
    df['Trade Counterparties'] = df['Trade Counterparties'].map(dealermap)
    for qtycol in ['Net Quantity', 'Gross Quantity', 'Buy Quantity', 'Sell Quantity']:
        df[qtycol] = df[qtycol] / 1e6
        if qtycol != 'Sell Quantity':
            df.loc[df['Trade Counterparties'] == 'Inter-DLR', qtycol] = \
                df.loc[df['Trade Counterparties'] == 'Inter-DLR', qtycol].abs()
    df = merge_df_with_funds(df)
    return df.sort_values(['Week Ending Date', 'Sector', 'Ticker'], ascending=[False, True, True])


def get_trace_by_cusip_isin_ticker(dt, cusip_or_isin, sector, fundentitycode):
    def processstr(x):
        if (x is None or (isinstance(x, str) and len(x) == 0)):
            return []
        if '%' in x:
            return x
        return [y.strip() for y in x.split(',')]

    cusip_or_isin = processstr(cusip_or_isin)
    sector = processstr(sector)
    fundentitycode = processstr(fundentitycode)
    if len(fundentitycode) > 0:
        cusipbyfunds = get_cusip_by_funds()
        fund_cusips = cusipbyfunds[cusipbyfunds['Fund Entity Code'].isin([str(x).upper() for x in fundentitycode])][
            'CUSIP'].unique().tolist()
        if len(cusip_or_isin) > 0:
            cusip_or_isin = list(set(cusip_or_isin) & set(fund_cusips))
        else:
            cusip_or_isin = fund_cusips
    if ((len(cusip_or_isin) > 0) or (len(sector) > 0)) and (pd.notnull(dt)):
        dtstr = pd.to_datetime(dt).strftime('%Y%m%d')
        column_map = {'apo_transaction_id': 'Transaction Id',
                      'cusip': 'Cusip',
                      'isin': 'ISIN',
                      'nasd_symbol': 'NASD Symbol',
                      'apo_quantity_indicator': 'Quantity Indicator',
                      'price': 'Price',
                      'yield': 'Yield',
                      'quantity': 'Quantity',
                      'estimated_quantity': 'Trace Estimated Quantity',
                      'effective_date': 'Trade Date',
                      'effective_time': 'Trade Time',
                      'oas_spread': 'OAS',
                      'ticker': 'Ticker',
                      'coupon': 'Coupon',
                      'maturity': 'Maturity',
                      'sector': 'Sector',
                      'market_segment': 'Market Segment',
                      'apo_is_investment_grade': 'IG or HY',
                      'apo_side': 'Trade Side',
                      'apo_reporting_party_type': 'Trade Reported By',
                      'apo_contra_party_type': 'Counterparty', }

        df = get_trades({'from_yyyymmdd': dtstr, 'to_yyyymmdd': dtstr}, cusip_or_isin=cusip_or_isin, sector=sector)
        df = add_model_volume_to_trades(df)
        df.loc[pd.notnull(df['real_quantity']), 'quantity'] = df.loc[pd.notnull(df['real_quantity']), 'real_quantity']
        df.loc[pd.notnull(df['real_quantity']), 'apo_quantity_indicator'] = 'Actual'
        column_order = ['Trade Date', 'Trade Time', 'Trade Side', 'Ticker', 'Cusip', 'ISIN',
                        'Price', 'Yield', 'OAS', 'Quantity', 'Quantity Indicator', 'Trace Estimated Quantity',
                        'Sector', 'Coupon', 'Maturity', 'IG or HY',
                        'Trade Reported By', 'Counterparty', 'Fund Entity Code']
        sort_columns = ['Trade Date', 'Trade Time', ]
        df = df.rename(columns=column_map)
        df = merge_df_with_funds(df)
        df = df[column_order].sort_values(sort_columns)
        df = df.rename(columns={'Fund Entity Code': 'Fund EntityCode'})  # hack to avoid default sorting
        return df
    else:
        return pd.DataFrame()


def get_trace_volume(fromdt, todt, tenor='1m', id_col='cusip', join_uncapped=True, tickers=None):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    if tenor == 'mtd':
        fromdt_query = dtutil.bomonth(fromdt)
    elif dtutil.isreldate(tenor):
        fromdt_query = dtutil.dateadd(fromdt, f'-{tenor}')
    else:
        raise ValueError(f'Invalid tenor: {tenor}')

    dts = {'from_yyyymmdd': dtutil.todatestr(fromdt_query), 'to_yyyymmdd': dtutil.todatestr(todt)}
    trades = get_trades(dts, join_uncapped=join_uncapped, cusip_or_isin=tickers)
    if len(trades) > 0:
        trades['trade_counterparties'] = (trades['apo_reporting_party_type'].astype(str) + '_2_' +
                                          trades['apo_contra_party_type'].astype(str))
        trades = trades.loc[trades['trade_counterparties'] == 'Dealer_2_Client', :]
        trades = trades[['asof_date', id_col, 'side', 'quantity',
                         'estimated_quantity', 'quantity_indicator', 'maturity',
                         'effective_date', 'is_investment_grade', 'effective_time']]
        trades = add_model_volume_to_trades(trades)
        trades[f'volume_{tenor}'] = trades['estimated_quantity']

        trades['asof_date'] = pd.to_datetime(trades['asof_date'])
        trades = trades.sort_values('asof_date')

        def _rolling_sum(group):
            tenor_days = (fromdt - fromdt_query).days
            return group.set_index('asof_date')[f'volume_{tenor}'].rolling(window=f'{tenor_days}D').sum()

        trades = trades.groupby([id_col, 'side']).apply(_rolling_sum).reset_index()

        trades[f'gross_volume_{tenor}'] = trades[f'volume_{tenor}']
        trades[f'volume_{tenor}'] = trades[f'volume_{tenor}'] * np.where(trades['side'] == 'S', -1, 1)
        trades = (trades.groupby(['asof_date', id_col]) \
                      [[f'volume_{tenor}', f'gross_volume_{tenor}']].sum()).reset_index()

        if id_col == 'cusip':
            trades[id_col] = trades[id_col].map(lambda x: x[:-1])

        if fromdt == todt:
            fromdt = pd.to_datetime(trades['asof_date'], errors='coerce').max()

        trades = trades[(pd.to_datetime(trades['asof_date']) >= pd.to_datetime(fromdt)) &
                        (pd.to_datetime(trades['asof_date']) <= pd.to_datetime(todt))]
        logging.info(f'trace volume completed for dates:{fromdt} and {todt}')
    return trades


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('trace_flow')

    config.init('prod')

    logging.info(get_trace_volume('2021-08-02', '2021-08-30').to_string())
    pass
