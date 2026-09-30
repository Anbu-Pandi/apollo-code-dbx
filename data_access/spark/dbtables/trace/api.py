import glob
import logging
import os
from datetime import datetime
from enum import Enum
from functools import partial
from functools import reduce

import numpy as np
import pandas as pd
import pyspark.sql.functions as sparkfuncs
from pyspark.sql.types import *

from apo_config.config import config
from data_access.dbtables import TRACE_APP
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.trace.sqlqueries import \
    SELECT_QUERY, SELECT_QUERY_WITHOUT_REALQTY, REALQUANTITY_COLUMN, REPORTDATE_COLUMN, DATE_COLUMN, TIME_COLUMN
from data_access.spark.dbtables.trace.transform import calc_tzinterval_code, calc_portfolio_flag
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause

MTY_BUCKET_COL = 'maturity_years_bucket'
INV_GRADE_COL = 'ig_flag'
INT_CODE_COL = 'interval_code_short'

from data_access.spark.dbtables.trace.transform import pd_calc_interval_code

TRACE_QTY_MODEL_FOLDER = 'trace_qty_avg_model'

TraceTradeType = Enum('TraceTradeType', 'Portfolio Non-Portfolio Any')


def _genrealqtyclause(queryrealqtyonly):
    if queryrealqtyonly:
        return f'{REALQUANTITY_COLUMN} IS NOT NULL'
    return None


def create_where_clause(datefilters, cusip_or_isin=None, sector=None):
    isinclause = get_where_clause('isin', cusip_or_isin)
    cusipclause = get_where_clause('cusip', cusip_or_isin)
    tickerclause = get_where_clause('ticker', cusip_or_isin)
    sectorclause = get_where_clause('sector', sector)
    dateclause = _parsedatefilters(datefilters, 'cast([{}] as date)'.format(DATE_COLUMN), databricks_format=True)
    cusip_or_isin_clause = join_where_clauses(' OR ',
                                              [isinclause, cusipclause, tickerclause])
    whereclause = join_where_clauses(' AND ', [dateclause, sectorclause, cusip_or_isin_clause])
    if len(whereclause) > 0:
        return f' AND {whereclause}'
    return ''


def add_tracedatetime(df):
    df = df.withColumn('trace_datetime',
                       sparkfuncs.to_timestamp(sparkfuncs.concat(sparkfuncs.col(DATE_COLUMN),
                                                                 sparkfuncs.lit(' '),
                                                                 sparkfuncs.col(TIME_COLUMN)),
                                               format='yyyy-MM-dd HH:mm:ss')
                       .cast(TimestampType()))
    return df


def apply_transforms(df):
    logging.info(f'apply transform to tracedf')
    df = add_tracedatetime(df) \
        .withColumn('net_quantity', sparkfuncs.when(sparkfuncs.col('side') == 'S', -1).otherwise(1)
                    * sparkfuncs.col('quantity'))

    df = calc_portfolio_flag(df)
    df = calc_tzinterval_code(df)

    logging.info(f'completed transform in tracedf')
    return df


def get_trace_query(datefilters, knowledgedatetime=None, cusip_or_isin=None, sector=None, join_uncapped=True):
    whereclause = create_where_clause(datefilters, cusip_or_isin, sector)
    if knowledgedatetime is None:
        knowledgedatetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    tracequerysql = (SELECT_QUERY if join_uncapped else SELECT_QUERY_WITHOUT_REALQTY) \
        .format(knowledgedatetime, whereclause)
    return tracequerysql


def get_trades(datefilters, knowledgedatetime=None,
               cusip_or_isin=None, sector=None,
               filterportfoliotrades=False,
               tzinterval=None, transform=True,
               sparkfilters=None, join_uncapped=True):
    tracequerysql = get_trace_query(datefilters, knowledgedatetime, cusip_or_isin, sector, join_uncapped)
    df = sparkmanager.readsql(tracequerysql, partitioncolumn='issue_id', lowerbound=0, upperbound=1e20,
                              app=TRACE_APP)

    if not sparkfilters is None:
        df = df.filter(sparkfilters)

    if transform:
        func_model_qty = partial(add_model_volume_to_trades, model_folder=None)
        outschema = StructType([x for x in df.schema] + [StructField('maturity_years_bucket', DoubleType()),
                                                         StructField('interval_code_short', StringType()),
                                                         StructField('tenor', DoubleType())])
        df = df.groupby(['cusip']).applyInPandas(func_model_qty, schema=outschema)

        df = apply_transforms(df)

        if filterportfoliotrades:
            df = df.filter(sparkfuncs.col('portfolio_trade_index').isNotNull())

        if tzinterval is not None:
            if not isinstance(tzinterval, list):
                tzinterval = [tzinterval]
            df = df.where(reduce(lambda a, b: a | b, (df['interval_code'].like(pat) for pat in tzinterval)))

    return df


def summarize_trades_by_interval(groupinterval,
                                 tracedf,
                                 aggmap={}):
    if len(aggmap) == 0:
        aggmap = {'quantity': 'sum', 'net_quantity': 'sum'}

    grouped = tracedf.groupBy('cusip', sparkfuncs.window('trace_datetime', groupinterval)) \
        .agg(aggmap)

    selected_cols = [sparkfuncs.col('cusip'), sparkfuncs.col('window.end').alias('trace_datetime')]
    for (k, v) in aggmap.items():
        selected_cols += [sparkfuncs.col(f"{v}({k})")]

    return grouped.select(selected_cols)


def dropduplicatesingroup(df, countthreshold=4, qtythreshold=200e6):
    if df['quantity'].sum() > qtythreshold and len(df) > countthreshold:
        return df.head(1).loc[[False], :]
    return df


def dedupedf(df, countthreshold=4, qtythreshold=200e6):
    logging.info(f'drop duplicates by in tracedf')
    gbycols = ['issue_price_id', REPORTDATE_COLUMN, 'sequence_number']
    aggcols = (sparkfuncs.first(sparkfuncs.col(c)).alias(c) for c in df.columns if c not in gbycols)
    df = df.groupby(gbycols).agg(*aggcols)

    logging.info(f'drop duplicates in tracedf by threshold')
    dedupefunc = partial(dropduplicatesingroup, countthreshold=countthreshold, qtythreshold=qtythreshold)
    df = df.groupby(['apo_transaction_id', REPORTDATE_COLUMN, 'data_source']) \
        .applyInPandas(dedupefunc, schema=df.schema)

    logging.info(f'completed finished df')
    return df


def get_trace_qty_models(folder=None):
    models = {}
    model_folder = folder or os.path.abspath(os.path.join(os.path.realpath(__file__), '../../../../models/trace'))
    logging.info(f'using trace qty models from folder: {model_folder}')
    if os.path.exists(model_folder):
        for f in glob.glob(os.path.join(model_folder, '*.csv')):
            models[f] = pd.read_csv(os.path.join(model_folder, f))
    assert len(models) > 0, f'trace qty models not found: {model_folder}'
    return models


def add_model_volume_to_trades(trades, model_folder=None):
    models = get_trace_qty_models(model_folder)
    if len(trades) > 0:
        trades = update_qty_to_model(trades, models)
        flags = trades['quantity_indicator'] == 'A'
        trades.loc[flags, 'quantity_merged'] = trades.loc[flags, 'quantity']
        trades.loc[~flags, 'quantity_merged'] = trades.loc[~flags, 'model_volume'] \
            .combine_first(trades.loc[~flags, 'estimated_quantity'])
        trades['estimated_quantity'] = trades['quantity_merged']
        trades.drop(columns=['quantity_merged', 'model_volume'], inplace=True)
    return trades


def calc_tenor(mty, refdt):
    if pd.notnull(mty) and pd.notnull(refdt):
        return (mty - refdt).days / 365.25
    return np.NaN


vectorized_calc_tenor = np.vectorize(calc_tenor)


def _map_tenor_to_buckets(df, mty_buckets, tenorcolumn='tenor'):
    if mty_buckets.name in df.columns:
        return df

    tenors = df[tenorcolumn].drop_duplicates().dropna().sort_values()
    tenors = pd.merge_asof(tenors, mty_buckets.astype(float).sort_values(),
                           left_on=tenorcolumn, right_on=mty_buckets.name, direction='forward')
    df = pd.merge(df, tenors, on=tenorcolumn, how='left')
    return df


def _update_qty_using_columns(df, model, model_columns):
    model['dqty'] = model['avg(REALQUANTITY)'] - model['avg(QUANTITY)']
    model = model[model_columns + ['dqty']]
    if MTY_BUCKET_COL in model.columns:
        df = _map_tenor_to_buckets(df, model[MTY_BUCKET_COL].drop_duplicates())
    if INV_GRADE_COL in model.columns:
        df.rename(columns={'is_investment_grade': INV_GRADE_COL}, inplace=True)

    df = pd.merge(df, model, on=model_columns, how='left')
    df.loc[df['quantity_indicator'] != 'E', 'dqty'] = 0
    if not 'model_volume' in df.columns:
        df['model_volume'] = df['quantity'] + df['dqty']
    else:
        flags = pd.isnull(df['model_volume'])
        df.loc[flags, 'model_volume'] = df.loc[flags, 'quantity'] + df.loc[flags, 'dqty']

    df.drop(columns='dqty', inplace=True)
    if INV_GRADE_COL in model.columns:
        df.rename(columns={INV_GRADE_COL: 'is_investment_grade'}, inplace=True)
    return df


def update_qty_to_model(df, models):
    df['tenor'] = vectorized_calc_tenor(df['maturity'], df['effective_date'])
    df[INT_CODE_COL] = pd_calc_interval_code(df['effective_time']).str.replace(r'\d+', '')

    has_maturity = lambda df: MTY_BUCKET_COL in df.columns
    has_ig = lambda df: INV_GRADE_COL in df.columns
    has_interval = lambda df: INT_CODE_COL in df.columns

    for k, model in models.items():
        if has_maturity(model) and has_ig(model) and has_interval(model):
            df = _update_qty_using_columns(df, model, [INT_CODE_COL, INV_GRADE_COL, MTY_BUCKET_COL])
    for _, model in models.items():
        if has_ig(model) and has_interval(model) and not has_maturity(model):
            df = _update_qty_using_columns(df, model, [INT_CODE_COL, INV_GRADE_COL])
    for _, model in models.items():
        if has_maturity(model) and has_interval(model) and not has_ig(model):
            df = _update_qty_using_columns(df, model, [INT_CODE_COL, MTY_BUCKET_COL])
    for _, model in models.items():
        if has_interval(model) and not has_maturity(model) and not has_ig(model):
            df = _update_qty_using_columns(df, model, [INT_CODE_COL])

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger('trace_api_test')

    config.init('prod')
    sparkmanager.init()

    for transform in [True]:
        for dates in [{'from_yyyy': 2020, 'to_yyyy': '2020'},
                      {'from_yyyy': 2017, 'to_yyyy': '2020'},
                      {'from_yyyy': 2000, 'to_yyyy': '2020'}]:
            cachefile = "".join([x for x in str(dates) if x.isalnum()]) + \
                        ("transformed" if transform else "nontransformed")

            logging.info(f'working: {cachefile}.')
            if sparkmanager.fileexists(cachefile) and False:
                logging.info(f'{cachefile} exists and hence skipped.')
                df = sparkmanager.readcache(cachefile)
            else:
                df = get_trades(datefilters=dates, transform=transform)

            avgs = df.filter(sparkfuncs.col(REALQUANTITY_COLUMN).isNotNull()) \
                .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(REPORTDATE_COLUMN))) \
                .withColumn("qtyestimateerror", sparkfuncs.col(REALQUANTITY_COLUMN) - sparkfuncs.col('quantity')) \
                .groupby('trace_month') \
                .agg({'quantity': 'mean', REALQUANTITY_COLUMN: 'mean'}) \
                .collect()

            logging.info(f'{cachefile} analysis completed.')

            if not sparkmanager.fileexists(cachefile) or True:
                sparkmanager.writecache(df, cachefile, partitioncolumn=REPORTDATE_COLUMN, overwrite=True)
                logging.info(f"{cachefile} write cache completed.")

    logging.info('completed')

    sparkmanager.stop()

    pass
