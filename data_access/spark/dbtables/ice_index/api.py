import logging

import pyspark.sql.functions as sparkfuncs

from data_access.dbtables import ICE_INDEX_APP
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.ice_index.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause


def create_where_clause(datefilters, index=None, cusip_or_isin=None, industry=None):
    indexclause = get_where_clause('[ice_index_ticker]', index)
    cusipclause = get_where_clause('[cusip]', cusip_or_isin)
    isinclause = get_where_clause('[isin_number]', cusip_or_isin)
    tickerclause = get_where_clause('[ticker]', cusip_or_isin)
    industry1clause = get_where_clause('[industry_lvl1_desc]', industry)
    industry2clause = get_where_clause('[industry_lvl2_desc]', industry)
    industry3clause = get_where_clause('[industry_lvl3_desc]', industry)

    dateclause = _parsedatefilters(datefilters, DATE_COLUMN)
    cusip_or_isin_clause = join_where_clauses(' OR ', [cusipclause, isinclause, tickerclause])
    industry_clause = join_where_clauses(' OR ', [industry1clause, industry2clause, industry3clause])
    return join_where_clauses(' AND ', [indexclause, dateclause, cusip_or_isin_clause, industry_clause])


def get_ice_index_query(datefilters, index=None, cusip_or_isin=None, industry=None):
    sql = SQL.format(create_where_clause(datefilters, index, cusip_or_isin, industry))
    return sql


def get_constituents(datefilters, index=None, cusip_or_isin=None, industry=None):
    sql = get_ice_index_query(datefilters, index, cusip_or_isin, industry)
    df = sparkmanager.readsql(sql, app=ICE_INDEX_APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('bofaindex_api_test')

    config.init('prod')
    sparkmanager.init()

    dates = {'from_yyyy': 2020, 'to_yyyy': '2020'}
    df = get_constituents(dates, index=['H0A0', 'C0A0'])

    avgs = df \
        .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('trace_month') \
        .agg({'Yield_to_Maturity_conv': 'mean', 'Spread_Duration': 'mean', 'Total_Return_%_1-day_LOC': 'sum'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
