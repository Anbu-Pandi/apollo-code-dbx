from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, NumericType
from functools import reduce


def _spark_aggregate_by_index(df, index_cols):
    agg_exprs = [
        (
            F.avg(c).alias(c)
            if isinstance(df.schema[c].dataType, NumericType)
            else F.array_join(F.array_sort(F.collect_set(F.col(c))), "|").alias(c)
        )
        for c in df.columns if c not in index_cols
    ]

    return df.groupBy(index_cols).agg(*agg_exprs)


def _spark_report_diff_expr(left_col, right_col, tolerance, lnum, diff_tolerance):
    left = F.col(left_col)
    right = F.col(right_col)

    # Normalize for null/zero handling
    both_null = left.isNull() & right.isNull()
    left_null_right_zero = left.isNull() & (right == 0)
    right_null_left_zero = right.isNull() & (left == 0)

    no_diff_special = both_null | left_null_right_zero | right_null_left_zero

    # String comparison
    left_str = F.coalesce(left.cast("string"), F.lit(""))
    right_str = F.coalesce(right.cast("string"), F.lit(""))
    string_equal = left_str == right_str

    # Numeric: relative/absolute tolerance
    both_zero = (left == 0) & (right == 0)
    abs_diff_ok = F.abs(right - left) <= diff_tolerance

    small_scale = (F.abs(left) <= lnum) | (F.abs(right) <= lnum)
    rel_ok = (left != 0) & (F.abs(right / left - 1) <= tolerance)

    numeric_no_diff = both_zero | (
        F.when(left == 0, abs_diff_ok)
        .when(small_scale & (left != 0), abs_diff_ok)
        .otherwise(rel_ok)
    )

    return (
        F.when(no_diff_special, F.lit(None).cast(StringType()))
        .when(string_equal, F.lit(None).cast(StringType()))
        .when(numeric_no_diff, F.lit(None).cast(StringType()))
        .otherwise(F.concat(left_str, F.lit(" | "), right_str))
    )


def comparedfs_spark(
        df1: DataFrame,
        df2: DataFrame,
        index_cols: list,
        ignore_cols: list = None,
        tolerance: float = 0.0,
        lnum: float = 1.0,
        diff_tolerance: float = 1.0
) -> DataFrame:
    ignore_cols = ignore_cols or []

    # Drop ignored columns
    df1 = df1.drop(*[c for c in ignore_cols if c in df1.columns])
    df2 = df2.drop(*[c for c in ignore_cols if c in df2.columns])

    # Align schema
    common = [c for c in df1.columns if c in df2.columns]

    if set(common) != set(df1.columns) or set(common) != set(df2.columns):
        raise ValueError("Dataframes do not have the same columns after ignore_cols")

    df1 = df1.select(common)
    df2 = df2.select(common)

    value_cols = [c for c in common if c not in index_cols]

    if not value_cols:
        return df1.sparkSession.createDataFrame([], df1.schema)

    # Aggregate
    agg1 = _spark_aggregate_by_index(df1, index_cols)
    agg2 = _spark_aggregate_by_index(df2, index_cols)

    # Rename columns in ONE projection
    agg1 = agg1.select(
        *index_cols,
        *[F.col(c).alias(f"{c}_old") for c in value_cols]
    )

    agg2 = agg2.select(
        *index_cols,
        *[F.col(c).alias(f"{c}_new") for c in value_cols]
    )

    merged = agg1.join(agg2, on=index_cols, how="outer")

    # Build diff expressions in ONE projection
    diff_exprs = [
        _spark_report_diff_expr(
            f"{c}_old",
            f"{c}_new",
            tolerance,
            lnum,
            diff_tolerance
        ).alias(c)
        for c in value_cols
    ]

    changed_df = merged.select(
        *index_cols,
        *diff_exprs
    )

    # Keep rows with at least one difference
    diff_predicate = reduce(
        lambda a, b: a | b,
        [F.col(c).isNotNull() for c in value_cols]
    )

    changed_df = changed_df.filter(diff_predicate)

    row = changed_df.agg(
        *[
            F.max(F.col(c).isNotNull().cast("int")).alias(c)
            for c in changed_df.columns
        ]
    ).first()

    cols_with_values = [c for c, v in row.asDict().items() if v == 1]
    changed_df_clean = changed_df.select(*cols_with_values)

    return changed_df_clean
