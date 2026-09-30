import logging

import numpy as np

from common.utils import sqlutil
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.dcs_const import FACTSET_DCS_SQL, ISIN_BY_TICKER_DCS_QUERY
from data_access.utils import tosqllist, get_where_clause, join_where_clauses


def get_dcs_df(dt, ticker_regions):
    ticker_region_sqllist = tosqllist(ticker_regions)
    dcs_df = sqlutil.readdfsql(FACTSET_DCS_SQL.format(ticker_region_sqllist, dt), app=APP)
    dcs_df.columns = dcs_df.columns.map(lambda x: x.lower())
    dcs_df['max_report_date'] = dcs_df.groupby(['ticker_region'])['report_date'].transform(np.max)
    return dcs_df


def get_isin_ticker_df(dt, ticker_regions=None, isins=None):
    ticker_region_clause = get_where_clause('tr.ticker_region', ticker_regions)
    isin_clause = get_where_clause('isn.isin', isins)
    assert (ticker_region_clause is not None) or (isin_clause is not None), 'need a ticker region or isin filter'

    where_clause = join_where_clauses(' AND ', [ticker_region_clause, isin_clause])
    df = sqlutil.readdfsql(ISIN_BY_TICKER_DCS_QUERY.format(dt, where_clause), app=APP)
    df.columns = df.columns.map(lambda x: x.lower())

    df = df.sort_values('last_report_date').drop_duplicates(subset=['isin'], keep='last')
    return df


if __name__ == '__main__':
    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('prod')
    df = get_dcs_df('2024-06-01', ['RKT-GB', 'RKT-US'])
    logging.info(df.to_string())
