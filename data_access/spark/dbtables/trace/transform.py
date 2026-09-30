import itertools

import pandas as pd
import pyspark.sql.functions as sparkfuncs
from pyspark.sql.types import *
from pyspark.sql.window import Window

from data_access.spark.dbtables.trace.sqlqueries import TIME_COLUMN
from data_access.spark.transforms import add_datetime_col

PORTFOLIO_CUSIP_COUNT = 10
PORTFOLIO_TRADE_QUANTITY = 50e3
PORTFOLIO_AVG_QUANTITY_THRESHOLD = 750e3


def calc_portfolio_flag(df):
    df = add_datetime_col(df, '_dttime', 'effective_date', 'effective_time')

    w_group = Window.partitionBy('effective_date', '_group').orderBy(sparkfuncs.col('_dttime').cast('long'))
    w_cusip_price = Window.partitionBy('effective_date', '_group', 'cusip', 'price') \
        .orderBy(sparkfuncs.col('_dttime').cast('long'))
    w_portfolio_trade = Window.partitionBy('effective_date', 'portfolio_trade_index') \
        .orderBy(sparkfuncs.col('_dttime').cast('long'))

    w_current = w_group.rangeBetween(0, 0)
    w_1s_back = w_group.rangeBetween(-1, 0)
    w_rolling = w_group.rowsBetween(Window.unboundedPreceding, 0)
    w_cusip_price_current = w_cusip_price.rangeBetween(0, 0)
    w_portfolio_trade_current = w_portfolio_trade.rangeBetween(0, 0)

    cusip_count_pred = (sparkfuncs.col('_cusip_count') >= PORTFOLIO_CUSIP_COUNT)
    qty_pred = (sparkfuncs.col('_cusip_price_qty') >= PORTFOLIO_TRADE_QUANTITY)
    not_delayed_tsy_spot_trade = ~(sparkfuncs.col('effective_time').between('15:00:00', '15:05:00'))
    avg_qty_pred = (sparkfuncs.col('portfolio_trade_avg_qty') >= PORTFOLIO_AVG_QUANTITY_THRESHOLD)
    client_trade_pred = (sparkfuncs.col('_contra_party_type') == 'C')

    second_modifier_flag_pred = (sparkfuncs.col('second_modifier') == 'P')

    pindex_notnull_pred = sparkfuncs.col('portfolio_trade_index').isNotNull()
    pindextime_null_pred = sparkfuncs.col('_portfolio_trade_index_time').isNull()

    groups = ["".join(x) for x in itertools.product(*[['D', 'T'], ['A', 'C', 'D'], ['B', 'S']])]
    map_group = {x: -i for (i, x) in enumerate(groups)}
    mapping_expr = sparkfuncs.create_map([sparkfuncs.lit(x) for x in itertools.chain(*map_group.items())])

    df = df.withColumn('_reporting_party_type',
                       sparkfuncs.when(sparkfuncs.col('reporting_party_type').isin(['D', 'T']), 'D').otherwise(
                           sparkfuncs.col('reporting_party_type'))) \
        .withColumn('_contra_party_type',
                    sparkfuncs.when(sparkfuncs.col('contra_party_type').isin(['C', 'A']), 'C').otherwise('D')) \
        .withColumn('_group', sparkfuncs.concat(sparkfuncs.col('reporting_party_type'),
                                                sparkfuncs.col('contra_party_type'),
                                                sparkfuncs.col('side'))) \
        .withColumn('_row_number_in_group', sparkfuncs.row_number().over(w_group)) \
        .withColumn('_row_number',
                    (len(groups) * sparkfuncs.col('_row_number_in_group')) + mapping_expr.getItem(
                        sparkfuncs.col('_group'))) \
        .withColumn('_cusip_price_qty', sparkfuncs.sum('quantity').over(w_cusip_price_current)) \
        .withColumn('_cusip',
                    sparkfuncs.when(qty_pred & not_delayed_tsy_spot_trade & client_trade_pred, sparkfuncs.col('cusip'))) \
        .withColumn('_cusip_count', sparkfuncs.approx_count_distinct('_cusip').over(w_current)) \
        .withColumn('portfolio_trade_index', sparkfuncs.when((cusip_count_pred &
                                                             qty_pred &
                                                             not_delayed_tsy_spot_trade &
                                                             client_trade_pred) | (second_modifier_flag_pred),
                                                             sparkfuncs.col('_row_number'))) \
        .withColumn('portfolio_trade_index',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.min('portfolio_trade_index').over(w_current))) \
        .withColumn('portfolio_trade_index_2s',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.min('portfolio_trade_index').over(w_1s_back))) \
        .withColumn('_portfolio_trade_index_time', sparkfuncs.min('portfolio_trade_index').over(w_current)) \
        .withColumn('_not_portfolio_flag',
                    sparkfuncs.when(pindextime_null_pred, sparkfuncs.lit(1)).otherwise(sparkfuncs.lit(0))) \
        .withColumn('portfolio_trade_index_block', sparkfuncs.sum('_not_portfolio_flag').over(w_rolling)) \
        .withColumn('portfolio_trade_index_block',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.col('portfolio_trade_index_block'))) \
        .withColumn('portfolio_trade_avg_qty',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.mean('quantity').over(w_portfolio_trade_current))) \
        .withColumn('portfolio_trade_cusip_qty',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.col('_cusip_price_qty'))) \
        .withColumn('portfolio_trade_type', sparkfuncs.when(avg_qty_pred,
                                                            sparkfuncs.concat(sparkfuncs.col('_reporting_party_type'),
                                                                              sparkfuncs.col('_contra_party_type'),
                                                                              sparkfuncs.lit('+'))) \
                    .otherwise(sparkfuncs.concat(sparkfuncs.col('_reporting_party_type'),
                                                 sparkfuncs.col('_contra_party_type'),
                                                 sparkfuncs.lit('-')))) \
        .withColumn('portfolio_trade_type',
                    sparkfuncs.when(pindex_notnull_pred, sparkfuncs.col('portfolio_trade_type')))

    df = df.drop(*[x for x in df.columns if x[0] == '_'])

    return df


@sparkfuncs.pandas_udf(StringType(), sparkfuncs.PandasUDFType.SCALAR)
def calc_interval_code(timecolumn: pd.Series) -> pd.Series:
    return pd_calc_interval_code(timecolumn)


def pd_calc_interval_code(timecolumn: pd.Series) -> pd.Series:
    from data_access.spark.staticdf import BOND_QUOTE_TIME_INTERVAL_DF

    timecolumn = pd.to_datetime(timecolumn.fillna('00:00:00'), format='%H:%M:%S')
    timecoltomerge = timecolumn.drop_duplicates().sort_values()
    timecoltomerge = pd.merge_asof(timecoltomerge, BOND_QUOTE_TIME_INTERVAL_DF,
                                   left_on=timecoltomerge.name,
                                   right_on='End',
                                   direction='forward')

    return pd.merge(timecolumn, timecoltomerge, on=timecolumn.name, how='left')['IntervalCode']


def calc_tzinterval_code(df):
    df = df.withColumn('interval_code', calc_interval_code(sparkfuncs.col(TIME_COLUMN)))
    return df


if __name__ == '__main__':
    pass
