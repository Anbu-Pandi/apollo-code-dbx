import logging

import pyspark.sql.functions as sparkfuncs

from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.sp_loan_index.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause


def create_where_clause(datefilters, issuer=None, industry=None, pricing_ids=None):
    issuerclause = get_where_clause('issuer', issuer)
    pricingidclause = get_where_clause('pricing_id', pricing_ids)
    broad_ind_clause = get_where_clause('lcd_broad_industry', industry)
    narrow_ind_clause = get_where_clause('narrow_industry', industry)
    dateclause = _parsedatefilters(datefilters, DATE_COLUMN, databricks_format=True)
    industry_clause = join_where_clauses(' OR ', [broad_ind_clause, narrow_ind_clause])
    return join_where_clauses(' AND ', [issuerclause, dateclause, industry_clause, pricingidclause])


def get_sp_loan_index_query(datefilters, issuer=None, industry=None, pricing_ids=None):
    sql = SQL.format(create_where_clause(datefilters, issuer, industry, pricing_ids))
    return sql

def get_constituents(datefilters, issuer=None, industry=None, pricing_ids=None):
    sql = get_sp_loan_index_query(datefilters, issuer, industry, pricing_ids)
    df = sparkmanager.readsql(sql)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('sploanindex_api_test')

    config.init('dev')
    sparkmanager.init()

    dates = {'from_yyyy': 2020, 'to_yyyy': '2020'}
    df = get_constituents(dates)

    avgs = df \
        .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('trace_month') \
        .agg({'WEIGHTS': 'sum'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
