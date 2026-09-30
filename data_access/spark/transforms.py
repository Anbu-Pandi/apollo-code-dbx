import itertools
from functools import partial

import pandas as pd
import pyspark.sql.functions as sparkfuncs
from pyspark.ml.feature import Bucketizer
from pyspark.sql.types import *

from data_access.spark.api import sparkmanager
from data_access.spark.staticdf import BOND_QUOTE_TIME_INTERVAL_DF


def add_datetimestr_col(df, outputcol, datecol, timecol, datetimeformat='yyyy-MM-dd HH:mm:ss'):
    df = df.withColumn(outputcol,
                       sparkfuncs.to_timestamp(sparkfuncs.concat(sparkfuncs.col(datecol),
                                                                 sparkfuncs.lit(' '),
                                                                 sparkfuncs.col(timecol)),
                                               format=datetimeformat)
                       .cast(TimestampType()))
    return df


def add_datetime_col(df, outputcol, datecol, timecol):
    df = df.withColumn('_dtstrcol', sparkfuncs.date_format(datecol, 'yyyy-MM-dd')) \
        .withColumn('_timestrcol', sparkfuncs.date_format(timecol, 'HH:mm:ss')) \
        .withColumn(outputcol,
                    sparkfuncs.to_timestamp(sparkfuncs.concat(sparkfuncs.col('_dtstrcol'),
                                                              sparkfuncs.lit(' '),
                                                              sparkfuncs.col('_timestrcol')),
                                            format='yyyy-MM-dd HH:mm:ss')
                    .cast(TimestampType())) \
        .drop('_dtstrcol', '_timestrcol')
    return df


def add_bucket_col(df, inputcol, outputcol, buckets, bucket_labels):
    label_array = sparkfuncs.array(*(sparkfuncs.lit(label) for label in bucket_labels))
    bucketizer = Bucketizer(splits=buckets, inputCol=inputcol, outputCol=outputcol)

    df = bucketizer.setHandleInvalid('skip') \
        .transform(df) \
        .withColumn(outputcol, label_array.getItem(sparkfuncs.col(outputcol).cast('integer')))

    return df


def df_join(left_df, right_df, left_on, right_on, left_cols=None, right_cols=None, right_suffix='_right', how='left'):
    left_cols = left_cols or left_df.columns
    right_cols = right_cols or right_df.columns

    preds = None
    for (lcol, rcol) in zip(left_on, right_on):
        colpred = (sparkfuncs.col('left_df.' + lcol) == sparkfuncs.col('right_df.' + rcol))
        if preds is None:
            preds = colpred
        else:
            preds = preds & colpred

    common_on = set(left_on) & set(right_on) & set(left_df.columns) & set(right_df.columns)
    common_cols = (set(left_cols) & set(right_cols)) - common_on
    left_cols = (set(left_on) | set(left_cols)) - common_cols - common_on
    right_cols = (set(right_on) | set(right_cols)) - common_cols - common_on

    return left_df.alias('left_df') \
        .join(right_df.alias('right_df'), preds, how=how) \
        .select([sparkfuncs.coalesce(sparkfuncs.col('left_df.' + x), sparkfuncs.col('right_df.' + x)).alias(x) for x in
                 common_on]
                + [sparkfuncs.col('left_df.' + x).alias(x) for x in left_cols]
                + [sparkfuncs.col('right_df.' + x).alias(x) for x in right_cols]
                + [sparkfuncs.col('left_df.' + x).alias(x) for x in common_cols]
                + [sparkfuncs.col('right_df.' + x).alias(x + right_suffix) for x in common_cols])


def add_time_using_interval_code(df):
    interval_spark_df = sparkmanager.create_spark_df(BOND_QUOTE_TIME_INTERVAL_DF).repartition(1)
    return df_join(df, interval_spark_df, ['interval_code'], ['IntervalCode'], df.columns, ['End'], '',
                   how='left').withColumnRenamed('End', 'interval_end_time')


def add_intervals(df, tzintervaldf):
    tzsparkdf = sparkmanager.create_spark_df(tzintervaldf).repartition(1)
    return df_join(df, tzsparkdf, ['interval_code'], ['IntervalCode'], df.columns, ['End'], '',
                   how='left').withColumnRenamed('End', 'interval_end_time')


def _asof_join(left_df, right_df, left_on, right_on, left_by, right_by, **kwargs):
    try:
        for col in left_on:
            left_df[col] = pd.to_datetime(left_df[col], errors='coerce')
        for col in right_on:
            right_df[col] = pd.to_datetime(right_df[col], errors='coerce')

        left_df = left_df.sort_values(left_on + left_by)
        right_df = right_df.dropna(subset=right_on + right_by, how='any').sort_values(right_on + right_by)

        right_cols = list(set(right_on + right_by + right_df.columns.difference(left_df.columns).tolist()))
        right_df = right_df[right_cols]

        if (len(left_df.dropna(subset=left_on + left_by, how='any')) == 0) or (len(right_df) == 0):
            left_df[left_on] = left_df[left_on].astype(str)
            right_df[right_on] = right_df[right_on].astype(str)
            df = pd.merge(left_df, right_df.head(0),
                          how='left', left_on=left_on + left_by, right_on=right_on + right_by)
            return df

        df = pd.merge_asof(left_df, right_df,
                           left_on=left_on, right_on=right_on, left_by=left_by, right_by=right_by, **kwargs)

        dtcols = list(set(left_on + right_on))
        df[dtcols] = df[dtcols].astype(str)
        return df

    except Exception as e:
        raise Exception(f"{str(left_df[left_on + left_by].head())}\n"
                        f"{str(right_df[right_on + right_by].head())}\n") from e


def df_asof_join(left_df, right_df, left_on, right_on, left_by, right_by, num_partitions=1_000, **kwargs):
    fields = {x.name: StructField(x.name, x.dataType, True) for x in (left_df.schema.fields + right_df.schema.fields)}
    outschema = StructType(list(fields.values()))

    asof_join_f = partial(_asof_join, left_on=left_on, right_on=right_on, left_by=left_by, right_by=right_by, **kwargs)
    df = left_df.dropna(subset=left_on + left_by) \
        .repartition(num_partitions, *left_by) \
        .groupby(left_by) \
        .cogroup(right_df.dropna(subset=right_on + right_by) \
                 .repartition(num_partitions, *right_by) \
                 .groupby(right_by)) \
        .applyInPandas(asof_join_f, schema=outschema)

    added_cols = [c for c in df.columns if c not in left_df.columns]
    l_cols = left_on + left_by
    null_pred = sparkfuncs.col(l_cols[0]).isNull()
    for lc in l_cols[1:]:
        null_pred = null_pred | sparkfuncs.col(lc).isNull()
    df = df.union(left_df.filter(null_pred) \
                  .select('*', *[sparkfuncs.lit(None).alias(c) for c in added_cols]))

    return df


def stringify_timestamps(df):
    return df.select(*[sparkfuncs.col(c).cast("string").alias(c)
                       if t == "timestamp" else
                       sparkfuncs.col(c).alias(c)
                       for c, t in df.dtypes
                       ])


def map_column_values(df, column_name: str, mapping_dict: dict, default_key: str, new_column_name=None):
    new_column_name = new_column_name or column_name
    default_value = mapping_dict.get(default_key, None)
    mapping_expr = sparkfuncs.create_map([sparkfuncs.lit(x) for x in itertools.chain(*mapping_dict.items())])
    df_mapped = df.withColumn(new_column_name, mapping_expr[sparkfuncs.col(column_name)])
    df_mapped = df_mapped.fillna({column_name: default_value})
    return df_mapped
