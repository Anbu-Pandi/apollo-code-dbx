import logging

import pyspark.sql.functions as sparkfuncs

from data_access.dbtables import MARKIT_CDS_PRICING_APP
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.markit_cds.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause
from common.utils.sqlutil import sql_replace_escape_char


def create_where_clause(datefilters, redcode_or_ticker=None, ccy=None, tier=None, doc=None, tenor=None,
                        sector=None,
                        snap=None):
    tickerclause = get_where_clause('[ticker]', redcode_or_ticker, use_databricks=True)
    shortnameclause = get_where_clause('[short_name]', redcode_or_ticker, use_databricks=True)
    redcodeclause = get_where_clause('[red_code]', redcode_or_ticker, use_databricks=True)
    redcode_or_ticker_clause = join_where_clauses(' OR ',
                                                  [tickerclause, shortnameclause, redcodeclause])

    ccyclause = get_where_clause('[tier]', ccy, use_databricks=True)
    tierclause = get_where_clause('[tier]', tier, use_databricks=True)
    docclause = get_where_clause('[doc_clause]', doc, use_databricks=True)
    tenorclause = get_where_clause('[tenor]', tenor, use_databricks=True)

    sectorclause = get_where_clause('[sector]', sector, use_databricks=True)
    snapclause = get_where_clause('[snap]', snap, use_databricks=True)
    dateclause = _parsedatefilters(datefilters, DATE_COLUMN, databricks_format=True)

    whereclause = join_where_clauses(' AND ', [redcode_or_ticker_clause, ccyclause, tierclause, docclause, tenorclause,
                                               sectorclause, snapclause, dateclause])

    return whereclause


def get_pricing(datefilters, redcode_or_ticker=None, ccy=None, tier=None, doc=None, tenor=None,
                sector=None,
                snap=None):
    sql = sql_replace_escape_char(SQL).format(
        create_where_clause(datefilters, redcode_or_ticker, ccy, tier, doc, tenor, sector, snap),
        app=MARKIT_CDS_PRICING_APP)
    df = sparkmanager.readsql(sql)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('etf_api_test')

    config.init('prod')
    sparkmanager.init()

    dates = {'from_yyyymmdd': 20210405, 'to_yyyymmdd': '20210405'}
    df = get_pricing(dates)

    avgs = df \
        .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('trace_month') \
        .agg({'par_spread': 'mean'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
