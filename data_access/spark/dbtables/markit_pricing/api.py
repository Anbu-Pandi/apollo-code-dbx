import logging

import pyspark.sql.functions as sparkfuncs

from common.utils.sqlutil import sql_replace_escape_char
from data_access.dbtables import MARKIT_BOND_PRICING_APP
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.markit_pricing.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause


def create_where_clause(datefilters, cusip_or_isin=None, currency=None):
    isinclause = get_where_clause('[isin]', cusip_or_isin, use_databricks=True)
    cusipclause = get_where_clause('[cusip]', cusip_or_isin, use_databricks=True)
    tickerclause = get_where_clause('[ticker]', cusip_or_isin, use_databricks=True)
    issuerclause = get_where_clause('[short_name_of_issuer]', cusip_or_isin, use_databricks=True)
    ccyclause = get_where_clause('[currency]', currency, use_databricks=True)
    dateclause = _parsedatefilters(datefilters, DATE_COLUMN, databricks_format=True)
    cusip_or_isin_clause = join_where_clauses(' OR ',
                                              [isinclause, cusipclause, tickerclause, issuerclause])
    return join_where_clauses(' AND ', [dateclause, ccyclause, cusip_or_isin_clause])


def get_bond_pricing(datefilters, cusip_or_isin=None, currency=None):
    sql = sql_replace_escape_char(SQL).format(create_where_clause(datefilters, cusip_or_isin, currency))
    df = sparkmanager.readsql(sql, app=MARKIT_BOND_PRICING_APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('markit_bond_api_test')

    config.init('prod')
    sparkmanager.init()

    dates = {'from_yyyymmdd': 20210129, 'to_yyyymmdd': '20210129'}
    df = get_bond_pricing(dates, )

    avgs = df \
        .withColumn("month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('month') \
        .agg({'mid_price': 'mean'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
