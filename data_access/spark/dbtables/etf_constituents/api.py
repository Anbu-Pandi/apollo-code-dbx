import logging

import pyspark.sql.functions as sparkfuncs

from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.etf_constituents.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause
from data_access.dbtables import ETF_APP


def create_where_clause(datefilters, etf=None, cusip_or_isin=None):
    etfclause = get_where_clause('composite_ticker', etf, use_databricks=True)
    constituentclause = get_where_clause('constituent_ticker', cusip_or_isin, use_databricks=True)
    constnameclause = get_where_clause('constituent_name', cusip_or_isin, use_databricks=True)
    cusipclause = get_where_clause('cusip', cusip_or_isin, use_databricks=True)
    isinclause = get_where_clause('isin', cusip_or_isin, use_databricks=True)
    figiclause = get_where_clause('figi', cusip_or_isin, use_databricks=True)
    sedolclause = get_where_clause('sedol', cusip_or_isin, use_databricks=True)
    dateclause = _parsedatefilters(datefilters, DATE_COLUMN, databricks_format=True)
    cusip_or_isin_clause = join_where_clauses(' OR ', [constituentclause, constnameclause, cusipclause, isinclause,
                                                       figiclause, sedolclause])
    return join_where_clauses(' AND ', [etfclause, dateclause, cusip_or_isin_clause])


def get_contituents_query(datefilters, etf=None, cusip_or_isin=None):
    sql = SQL.format(create_where_clause(datefilters, etf, cusip_or_isin))
    return sql


def get_constituents(datefilters, etf=None, cusip_or_isin=None):
    sql = get_contituents_query(datefilters, etf, cusip_or_isin)
    df = sparkmanager.readsql(sql, app=ETF_APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('etf_api_test')

    config.init('dev')
    sparkmanager.init()

    dates = {'from_yyyy': 2020, 'to_yyyy': '2020'}
    df = get_constituents(dates, etf=['HYG'])

    avgs = df \
        .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('trace_month') \
        .agg({'Market_Value': 'sum'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
