import logging

import pandas as pd
import pyspark.sql.functions as sparkfuncs
from pyspark.sql import Window

from common.utils import dtutil, sqlutil, creditutil
from dash_reports.issuer_debt_in_index.data.debt import add_issuer_curve_implied_spreads
from dash_reports.issuer_debt_in_index.data.fx import adjust_fx_oas
from dash_reports.issuer_debt_in_index.data.sql_const import BBG_BOND_SQL, ISSUER_BONDS, BOND_VOLUME, APO_PRICE_SQL
from dash_reports.issuer_debt_in_index.data.sql_const import ICE_INDICES
from data_access.dbtables import TRACE_APP, ICE_INDEX_APP, BOND_REFERENCEDATA_APP
from data_access.spark.api import sparkmanager
from data_access.utils import get_where_clause
from data_access.utils import tosqllist


def get_bond_volume_spark(fromdt, todt, ticker_clause=''):
    logging.info(f'calculating 30d rolling sum by isin using spark')
    bond_volume_df = sparkmanager.readsql(BOND_VOLUME.format(dtutil.dateadd(fromdt, '-1m'), todt, ticker_clause),
                                          app=TRACE_APP)

    window_size = 21
    w = Window().partitionBy('isin').orderBy("effective_date").rowsBetween(-window_size, 0)
    df = bond_volume_df.withColumn("buy_volume", sparkfuncs.sum("buy_volume").over(w)) \
        .withColumn("sell_volume", sparkfuncs.sum("sell_volume").over(w)) \
        .withColumn("gross_volume", sparkfuncs.sum("gross_volume").over(w)) \
        .withColumn("net_volume", sparkfuncs.col("buy_volume") - sparkfuncs.col("sell_volume"))

    bond_volume_df = df.toPandas()

    bond_volume_df['effective_date'] = pd.to_datetime(bond_volume_df['effective_date'])

    return bond_volume_df


def get_bond_volume_db(fromdt, todt, ticker_clause=''):
    bond_volume_df = sqlutil.readdfsql(BOND_VOLUME.format(dtutil.dateadd(fromdt, '-1m'), todt, ticker_clause),
                                       app=TRACE_APP)
    bond_volume_df['effective_date'] = pd.to_datetime(bond_volume_df['effective_date'])
    bond_volume_df.set_index(['effective_date', 'isin'], inplace=True)
    bond_volume_df.sort_index(inplace=True)
    logging.info(f'calculating 30d rolling sum by isin')
    for i, dfi in bond_volume_df.groupby('isin'):
        bond_volume_df.loc[dfi.index, :] = dfi.reset_index('isin') \
            .rolling('30D').sum().assign(isin=i).set_index('isin', append=True)
    bond_volume_df['net_volume'] = bond_volume_df['buy_volume'] - bond_volume_df['sell_volume']
    bond_volume_df.reset_index(inplace=True)
    return bond_volume_df


def get_bond_volume(fromdt, todt, ticker_clause=''):
    if ticker_clause == '':
        return get_bond_volume_spark(fromdt, todt, ticker_clause)
    else:
        return get_bond_volume_db(fromdt, todt, ticker_clause)


def get_issuer_data(fromdt, todt, tickers=None, add_issuer_curve=True):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    ticker_clause = get_where_clause('ticker', tickers) or ''
    if ticker_clause != '':
        ticker_clause = ' AND ' + ticker_clause

    bonds_df = sqlutil.readdfsql(ISSUER_BONDS.format(fromdt, todt, tosqllist(ICE_INDICES), ticker_clause),
                                 app=ICE_INDEX_APP)
    bonds_df['asof_date'] = pd.to_datetime(bonds_df['asof_date'])

    apo_prices = sqlutil.readdfsql(APO_PRICE_SQL.format(fromdt, todt), app=ICE_INDEX_APP)
    apo_prices['asof_date'] = pd.to_datetime(apo_prices['asof_date'])

    bonds_df = pd.merge(bonds_df, apo_prices, how='left', on=['asof_date', 'instrument_id'],
                        suffixes=('_ice', '_apo'))
    bonds_df['price'] = bonds_df['price_apo'].combine_first(bonds_df['price_ice'])
    bonds_df['oas'] = bonds_df['oas_apo'].combine_first(bonds_df['oas_ice'])
    bonds_df['yield_to_worst_conv'] = bonds_df['yield_to_worst_conv_apo'].combine_first(bonds_df['yield_to_worst_conv_ice'])

    bond_volume_df = get_bond_volume(fromdt, todt, ticker_clause)
    isins = bonds_df['instrument_id'].unique().tolist()
    bbg_cols = 'id_isin, collat_typ, is_perpetual, callable'
    bond_ref_df = sqlutil.readdfsql(BBG_BOND_SQL.format(bbg_cols, dtutil.todate(todt), tosqllist(isins)),
                                    app=BOND_REFERENCEDATA_APP)

    df = pd.merge(bonds_df, bond_ref_df, how='left', left_on='instrument_id', right_on='id_isin')
    df = pd.merge(df, bond_volume_df, how='left', left_on=['asof_date', 'instrument_id'],
                  right_on=['effective_date', 'isin'])
    df.rename(columns={x: f'{x}_1m' for x in df.columns if x.endswith('_volume')}, inplace=True)
    df['seniority'] = df['collat_typ'].combine_first(df['seniority'])
    df['secured_unsecured'] = creditutil.map_secured_unsecured(df['seniority'], df['is_perpetual'])
    df['maturity_years'] = creditutil.vec_calc_tenor(df['maturity'], df['asof_date'])
    df['maturity_bucket'] = creditutil.map_maturity_bucket(df['maturity_years'])
    df['callable'] = df['callable'].fillna(False)
    df['maturity_bucket_mid'] = df['maturity_bucket'].map(creditutil.calc_mid_maturity_bucket)
    df = adjust_fx_oas(fromdt, todt, df)
    if add_issuer_curve:
        df = add_issuer_curve_implied_spreads(df, spread_col='oas_usd')
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    sparkmanager.init()

    fromdt = '2022-01-24'
    todt = '2023-01-24'
    df = get_issuer_data(fromdt, todt)
    logging.info(df)
    pass
