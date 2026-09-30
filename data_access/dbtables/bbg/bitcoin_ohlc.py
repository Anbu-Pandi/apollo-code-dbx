import logging

from common.utils.sqlutil import readdfsql, insertdf_batch, execsql
from data_access.dbtables import BBG_APP
from data_access.utils import get_where_clause, join_where_clauses
from data_access.beta_attribution.utils import get_date_between_clause


def get_ohlc_data(security=None, start_date=None, end_date=None):
    sql = """
    SELECT security, bar_time, bar_type, bar_open, bar_high, bar_low, bar_close, 
           bar_volume, bar_vwap, bar_tick_count, load_date
    FROM bitcoin_ohlc_data
    """
    
    where_clauses = []
    
    if security:
        where_clauses.append(get_where_clause('security', security))
    
    if start_date or end_date:
        date_clause = get_date_between_clause('bar_time', start_date, end_date)
        where_clauses.append(date_clause)
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY security, bar_time"
    
    logging.info('Reading bitcoin OHLC data from database')
    df = readdfsql(sql, app=BBG_APP)
    return df


def get_securities():
    sql = """
    SELECT DISTINCT security, 
           MIN(bar_time) as first_bar_time,
           MAX(bar_time) as last_bar_time,
           COUNT(*) as bar_count
    FROM bitcoin_ohlc_data
    GROUP BY security
    ORDER BY security
    """
    
    df = readdfsql(sql, app=BBG_APP)
    return df


def upsert_ohlc_data(df):
    if df.empty:
        logging.warning('Empty dataframe provided for upsert')
        return
    
    required_cols = ['security', 'bar_time']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')
    
    logging.info('Upserting %s rows to bitcoin_ohlc_data', len(df))
    insertdf_batch('bitcoin_ohlc_data', df, app=BBG_APP)


def delete_all_bitcoin_data():
    logging.info('Deleting ALL bitcoin_ohlc_data')
    execsql('DELETE FROM bitcoin_ohlc_data', app=BBG_APP)
    logging.info('✓ Bitcoin OHLC table cleaned')


def delete_security_data(security):
    where_clause = get_where_clause('security', security)
    sql = f'DELETE FROM bitcoin_ohlc_data WHERE {where_clause}'
    
    logging.info('Deleting data for security: %s', security)
    execsql(sql, app=BBG_APP)
    logging.info('✓ Deleted data for: %s', security)

