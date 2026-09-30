import logging

from common.utils.dtutil import todate
from common.utils.sqlutil import readdfsql
from data_access.beta_attribution.utils import tosqllist
from data_access.dbtables import ETF_APP as APP
from data_access.utils import get_where_clause, join_where_clauses

SQL = '''   SELECT *
            FROM
                dbo.etf_constituents
            WHERE 
                cast({3} as date) BETWEEN '{0}' and '{1}'
                and composite_ticker in {2}
        '''


def get_constituents_by_date(dt, index):
    sql = SQL.format(dt, dt, tosqllist(index), 'asof_date')
    df = readdfsql(sql, app=APP)
    if df.empty:
        logging.info(f'No etf data fetched for date: {dt}')

    return df


def get_constituents_by_date_range(fromdt, todt, index):
    sql = SQL.format(fromdt, todt, tosqllist(index), 'etfg_date')
    df = readdfsql(sql, app=APP).drop(columns=['asof_date']).rename(columns={'etfg_date': 'asof_date'})

    return df


def get_latest_dt(dt, tickers=None):
    ticker_clause = get_where_clause('composite_ticker', tickers, use_databricks=True)
    dt_clause = get_where_clause('asof_date', todate(dt), use_databricks=True).replace('=', '<=')
    where_clause = join_where_clauses(' AND ', [ticker_clause, dt_clause])
    sql = f"SELECT max(asof_date) as latest_date FROM dbo.etf_constituents WHERE {where_clause}"
    df = readdfsql(sql, app=APP)
    return todate(df.iloc[0]['latest_date'])


def get_etf_index_ticker():
    sql = f"select distinct composite_ticker from dbo.etf_constituents"
    df = readdfsql(sql, app=APP)
    return df['composite_ticker'].to_list()


def get_timeseries_constituents(start_dt, end_dt, index):
    sql = f'''SELECT *
            FROM
                dbo.etf_constituents
            WHERE 
                asof_date between '{start_dt}' and '{end_dt}' 
                and composite_ticker in {tosqllist(index)}'''
    df = readdfsql(sql, app=APP)
    if df.empty:
        logging.info(f'No etf data fetched for start date: {start_dt} and end date: {end_dt}')

    return df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')
    df = get_timeseries_constituents('2023-06-06', '2023-06-06', 'HYG')
    print(df)
