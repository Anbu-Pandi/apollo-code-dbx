import logging
from functools import partial

import numpy as np
import pandas as pd
from pyspark.sql import functions as sparkfuncs

from common.utils import dtutil, sqlutil
from dash_reports.issuer_debt_in_index.data.debt import apply_issuer_curves_spark, clean_azure_temp_files
from dash_reports.issuer_debt_in_index.data.issuer_data import get_issuer_data
from dash_reports.report_common.data.data_model import datastore
from data_access.spark.api import sparkmanager
from data_access.spark.transforms import df_join

TABLE_DIR = 'data'

BOND_RV_APP = 'databricks'

BOND_ISSUER_CURVES_TABLE = 'bond_issuer_curves'
BOND_RV_TABLE = 'bond_rv'


def zscore(idf, z_min=3, ct_min=126):
    key_cols = ['asof_date', 'instrument_id', 'instrument_id_right']
    idf.sort_values(key_cols, inplace=True, ascending=[False, True, True])

    asof_date = pd.to_datetime(idf.iloc[0]['asof_date'])
    isin = idf.iloc[0]['instrument_id']
    isin_right = idf.iloc[0]['instrument_id_right']
    b1 = idf.loc[:, 'oas']
    b2 = idf.loc[:, 'oas_right']
    diff = (b1 - b2).dropna()

    c = len(diff)
    z = np.nan

    if c > 0:
        z = (diff.iloc[0] - diff.mean()) / diff.std()

    if z > 0:
        df = pd.DataFrame({'asof_date': [asof_date], 'instrument_id': [isin], 'instrument_id_right': [isin_right],
                           'zscore': [z], 'ct': [c]})
    else:
        df = pd.DataFrame({'asof_date': [asof_date], 'instrument_id': [isin_right], 'instrument_id_right': [isin],
                           'zscore': [abs(z)], 'ct': [c]})

    df = df[(df['zscore'].abs() >= z_min) & (df['ct'] >= ct_min)].dropna()
    return df


def save_dfs(fromdt, todt):
    clean_azure_temp_files
    save_bond_issuer_curves_df(fromdt, todt)
    save_bond_rv_df(fromdt, todt)


def save_bond_issuer_curves_df(fromdt, todt):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    issuer_curves_sparkdf = sparkmanager.readfile(BOND_ISSUER_CURVES_TABLE, TABLE_DIR, type='delta')
    update_fromdt = fromdt
    if issuer_curves_sparkdf is not None:
        max_dt = issuer_curves_sparkdf.agg(sparkfuncs.max('asof_date')).collect()[0][0]
        max_dt = dtutil.todate(dtutil.tz_convert(max_dt, from_tz='America/New_York', to_tz='UTC'))
        update_fromdt = dtutil.dateadd(max_dt, '1d')

    logging.info(f'getting issuer data from: {update_fromdt}')
    issuer_data_df = pd.DataFrame()
    if update_fromdt <= todt:
        issuer_data_df = get_issuer_data(update_fromdt, todt, add_issuer_curve=True)
    if len(issuer_data_df) > 0:
        write_sparkdf = sparkmanager.pandastospark(issuer_data_df)
        if not 'spread_diff_bond_v_curve' in issuer_data_df.columns:
            write_sparkdf = apply_issuer_curves_spark(write_sparkdf, spread_col='oas_usd')
        write_sparkdf = write_sparkdf.withColumn('asof_date', sparkfuncs.col('asof_date').cast('string'))

        sqlutil.execsql(f"DELETE FROM {BOND_ISSUER_CURVES_TABLE} where cast(asof_date as date) BETWEEN '{update_fromdt}' and '{todt}' ",
                        app=BOND_RV_APP)
        sparkmanager.writetosql(write_sparkdf, BOND_ISSUER_CURVES_TABLE, app=BOND_RV_APP)


def save_bond_rv_df(fromdt, todt):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    dt_range_pred = (sparkfuncs.col('asof_date').cast('date') >= fromdt) & \
                    (sparkfuncs.col('asof_date').cast('date') <= todt)

    zsparkdf = sparkmanager.readfile(BOND_RV_TABLE, TABLE_DIR, type='delta')
    max_dt = zsparkdf.agg(sparkfuncs.max('asof_date')).collect()[0][0]
    max_dt = dtutil.todate(dtutil.tz_convert(max_dt, from_tz='America/New_York', to_tz='UTC'))
    if max_dt < todt:
        sparkdf = sparkmanager.readfile(BOND_ISSUER_CURVES_TABLE, TABLE_DIR, type='delta') \
            .filter(dt_range_pred)\
            .withColumn('asof_date', sparkfuncs.col('asof_date').cast('date'))
        sparkdf_subset = sparkdf.select('asof_date', 'industry_lvl4_desc', 'instrument_id', 'oas')

        join_cols = ['industry_lvl4_desc', 'asof_date']
        sparkdfi = df_join(sparkdf_subset, sparkdf_subset, left_on=join_cols, right_on=join_cols, how='inner')
        sparkdfi = sparkdfi.filter((sparkfuncs.col('instrument_id') < sparkfuncs.col('instrument_id_right')))

        zsparkdf = sparkdfi \
            .select('asof_date', 'instrument_id', 'instrument_id_right', 'oas', 'oas_right') \
            .groupBy(['instrument_id', 'instrument_id_right']) \
            .applyInPandas(partial(zscore, z_min=3),
                           schema='asof_date timestamp, instrument_id string, instrument_id_right string, zscore double, ct long') \
            .filter(sparkfuncs.col('asof_date').cast('date') > max_dt)

        sqlutil.execsql(f"DELETE FROM {BOND_RV_TABLE} where cast(asof_date as date) BETWEEN '{todt}' and '{todt}' ",
                        app=BOND_RV_APP)
        sparkmanager.writetosql(zsparkdf, BOND_RV_TABLE, app=BOND_RV_APP)


def get_bond_df(dt):
    dt = dtutil.todate(dt)
    fromdt = dtutil.dateadd(dt, '-2y')
    bond_issuer_curves_df = sqlutil.readdfsql(f"select * from bond_issuer_curves "
                                              f"where cast(asof_date as date)>='{fromdt}' and cast(asof_date as date)<='{dt}' ",
                                              app=BOND_RV_APP)
    current_isins = \
        bond_issuer_curves_df[bond_issuer_curves_df['asof_date'].max() == bond_issuer_curves_df['asof_date']] \
            ['instrument_id'].unique()
    cdf = bond_issuer_curves_df[bond_issuer_curves_df['instrument_id'].isin(current_isins)]
    return cdf


def get_bond_rv_df(dt):
    dt = dtutil.todate(dt)
    zdf = sqlutil.readdfsql(f"select * from bond_rv where asof_date between '{dt}' and '{dt}'",
                            app=BOND_RV_APP)
    cdf = sqlutil.readdfsql(
        f"select * from bond_issuer_curves where cast(asof_date as date) between '{dt}' and '{dt}'",
        app=BOND_RV_APP)

    isins = cdf.sort_values('asof_date', ascending=False) \
        [[x for x in cdf.columns if 'asof_date' != x]] \
        .drop_duplicates(subset='instrument_id', keep='first')

    zdf2 = pd.merge(zdf, isins, left_on='instrument_id', right_on='instrument_id')
    zdf2 = pd.merge(zdf2.rename(columns={'instrument_id_right': 'instrument_id2'}), isins,
                    left_on='instrument_id2', right_on='instrument_id', suffixes=('', '_right'))

    return zdf2


def get_datastore_key(datakey, dt):
    dt = dtutil.todate(dt)
    if datakey == 'z':
        key = f'issuer_curve_spread_history_z_{dt}'
    else:
        key = f'issuer_curve_spread_history_{dt}'

    return key


def refresh_issuer_curves_history():
    dt = dtutil.dateadd(dtutil.today(), '-1b')

    df = datastore.read_or_create_cache_for_df(get_datastore_key('hist', dt),
                                               lambda: get_bond_df(dt))
    zdf = datastore.read_or_create_cache_for_df(get_datastore_key('z', dt),
                                                lambda: get_bond_rv_df(dt))


def split_input(s):
    s = (s or '').split(',')
    s = [x.strip() for x in s if len(x.strip()) > 0]
    return s


def filter_df(df, column, input):
    logging.info(f'pre-filter column: {column} input: {input} df: {df.shape}')
    input = split_input(input)
    neg = False
    eq = False
    for i in input:
        imod = i.replace('!', '').replace('=', '').lower()
        if i[:1] == '!':
            neg = True
        if (i[:1] == '=') or (i[:2][-1:] == '='):
            eq = True
        if eq:
            flag = (df[column].str.lower() == imod)
            if f'{column}_right' in df.columns:
                flag = flag | (df[f'{column}_right'].str.lower() == imod)
        else:
            flag = df[column].str.lower().str.contains(imod)
            if f'{column}_right' in df.columns:
                flag = flag | df[f'{column}_right'].str.lower().str.contains(imod)

        if neg:
            flag = ~flag
        df = df[flag.fillna(False)]
    logging.info(f'post-filter column: {column} input: {input} df: {df.shape}')
    return df


def get_rv_df(issuer, industry, maturity, constrained_columns):
    dt = dtutil.today()
    while (1):
        logging.info(f"bond rv z page for dt: {dt}")
        df = datastore.read_or_create_cache_for_df(get_datastore_key('z', dt))
        if (df is not None) or (dt <= dtutil.dateadd(dtutil.today(), '-1m')):
            assert df is not None, f'cannot find any df until date: {dt}'
            logging.info(f"found bond rv df for dt: {dt} df: {df.shape}")
            df = df.rename(columns={'curve_spread': 'curve', 'secured_curve_spread': 'secured_curve',
                                    'other_curve_spread': 'other_curve', 'unsecured_curve_spread': 'unsecured_curve',
                                    'curve_spread_right': 'curve_right',
                                    'secured_curve_spread_right': 'secured_curve_right',
                                    'other_curve_spread_right': 'other_curve_right',
                                    'unsecured_curve_spread_right': 'unsecured_curve_right',
                                    })
            if not 'industry_lvl3_desc' in df.columns:
                df['industry_lvl3_desc'] = df['industry_lvl4_desc']
            df['asof_date'] = dt
            break
        dt = dtutil.dateadd(dt, '-1b')

    for c in constrained_columns:
        if c in df.columns:
            logging.info(f'pre-constrained column: {c} df: {df.shape}')
            df = df[df[c] == df[f'{c}_right']]
            logging.info(f'post-constrained column: {c} df: {df.shape}')

    df = filter_df(df, 'ticker', issuer)
    df = filter_df(df, 'industry_lvl3_desc', industry)
    df = filter_df(df, 'maturity_bucket', maturity)

    return df


def get_rv_bond_df(bonds=None, tickers=None):
    dt = dtutil.today()
    while (1):
        logging.info(f"bond rv page for dt: {dt}")
        df = datastore.read_or_create_cache_for_df(get_datastore_key('hist', dt))
        if (df is not None) or (dt <= dtutil.dateadd(dtutil.today(), '-1m')):
            assert df is not None, f'cannot find any df until date: {dt}'
            logging.info(f"found bond rv df for dt: {dt} df: {df.shape}")
            df = df.rename(columns={'curve_spread': 'curve', 'secured_curve_spread': 'secured_curve',
                                    'other_curve_spread': 'other_curve', 'unsecured_curve_spread': 'unsecured_curve', })
            if not 'industry_lvl3_desc' in df.columns:
                df['industry_lvl3_desc'] = df['industry_lvl4_desc']
            break
        dt = dtutil.dateadd(dt, '-1b')
    dfs = []
    for b in split_input(bonds):
        dfs.append(filter_df(df, 'instrument_id', b))
    for b in split_input(tickers):
        dfs.append(filter_df(df, 'ticker', b))
    df = pd.concat(dfs)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    sparkmanager.init()

    save_dfs('2023-10-25', '2024-10-25')
