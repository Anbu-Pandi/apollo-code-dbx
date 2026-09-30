import logging

import pyspark.sql.functions as sparkfuncs

from data_access.dbtables import MARKIT_LOAN_PRICING_APP
from data_access.spark.api import sparkmanager
from data_access.spark.dbtables.markit_loan_pricing.sqlqueries import DATE_COLUMN, SQL
from data_access.utils import _parsedatefilters, join_where_clauses, get_where_clause
from common.utils.sqlutil import sql_replace_escape_char


def create_where_clause(datefilters, loanxid=None):
    loanxidclause = get_where_clause('[loanx_id]', loanxid, use_databricks=True)
    dateclause = _parsedatefilters(datefilters, DATE_COLUMN, databricks_format=True)
    return join_where_clauses(' AND ', [loanxidclause, dateclause])


def get_loan_pricing(datefilters, loanxid=None):
    sql = sql_replace_escape_char(SQL).format(create_where_clause(datefilters, loanxid))
    df = sparkmanager.readsql(sql, app=MARKIT_LOAN_PRICING_APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('etf_api_test')

    config.init('dev')
    sparkmanager.init()

    dates = {'from_yyyy': 2020, 'to_yyyy': '2020'}
    df = get_loan_pricing(dates)

    avgs = df \
        .withColumn("trace_month", sparkfuncs.month(sparkfuncs.col(DATE_COLUMN))) \
        .groupby('trace_month') \
        .agg({'Evaluated Price': 'mean'}) \
        .collect()

    logging.info(avgs)

    logging.info('completed')

    sparkmanager.stop()

    pass
