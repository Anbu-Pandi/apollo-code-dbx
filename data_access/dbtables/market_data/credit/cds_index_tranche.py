import logging

from common.utils import dtutil, sqlutil
from data_access.dbtables import MARKIT_CDS_INDEX_APP
from data_access.utils import tosqllist


def get_cds_index_tranche(fromdt=None, todt=None, index_tickers=None, index_family=None, 
                          attachment=None, detachment=None, on_the_run=None):
    fromdt = dtutil.todate(fromdt) if fromdt else dtutil.dateadd(dtutil.today(), '-1y')
    todt = dtutil.todate(todt) if todt else dtutil.today()
    
    where_clauses = [f"asof_date BETWEEN '{fromdt}' AND '{todt}'"]
    
    if index_tickers:
        ticker_list = tosqllist(index_tickers if isinstance(index_tickers, list) else [index_tickers])
        where_clauses.append(f"index_ticker IN ({ticker_list})")
    
    if index_family:
        family_list = tosqllist(index_family if isinstance(index_family, list) else [index_family])
        where_clauses.append(f"index_family IN ({family_list})")
    
    if attachment is not None:
        where_clauses.append(f"attachment = {attachment}")
    
    if detachment is not None:
        where_clauses.append(f"detachment = {detachment}")
    
    if on_the_run is not None:
        where_clauses.append(f"on_the_run = '{on_the_run}'")
    
    where_clause = ' AND '.join(where_clauses)
    
    sql = f'''
    SELECT 
        asof_date,
        batch,
        index_family,
        index_ticker,
        index_series,
        index_version,
        index_term,
        index_red_code,
        index_maturity,
        on_the_run,
        attachment,
        detachment,
        tranche_coupon,
        tranche_upfront_bid,
        tranche_upfront_mid,
        tranche_upfront_ask,
        tranche_spread_bid,
        tranche_spread_mid,
        tranche_spread_ask,
        tranche_price_bid,
        tranche_price_mid,
        tranche_price_ask,
        base_correlation,
        tranche_factor,
        delta,
        index_pv01,
        tranche_pv01,
        dealer_count_all,
        dealer_count_window,
        quote_count_all,
        quote_count_window,
        fuel_time_window,
        reference_price,
        reference_spread,
        type,
        knowledge_date,
        load_date
    FROM markit_cds_index_tranche
    WHERE {where_clause}
    ORDER BY asof_date DESC, index_ticker, attachment
    '''
    
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


def get_latest_tranche_data(index_tickers=None, index_family=None, asof_date=None):
    asof_date = dtutil.todate(asof_date) if asof_date else dtutil.today()
    
    where_clauses = [f"asof_date = '{asof_date}'"]
    
    if index_tickers:
        ticker_list = tosqllist(index_tickers if isinstance(index_tickers, list) else [index_tickers])
        where_clauses.append(f"index_ticker IN ({ticker_list})")
    
    if index_family:
        family_list = tosqllist(index_family if isinstance(index_family, list) else [index_family])
        where_clauses.append(f"index_family IN ({family_list})")
    
    where_clause = ' AND '.join(where_clauses)
    
    sql = f'''
    SELECT *
    FROM markit_cds_index_tranche
    WHERE {where_clause}
    ORDER BY index_ticker, attachment
    '''
    
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


def get_tranche_time_series(index_ticker, attachment, detachment, fromdt=None, todt=None):
    fromdt = dtutil.todate(fromdt) if fromdt else dtutil.dateadd(dtutil.today(), '-3m')
    todt = dtutil.todate(todt) if todt else dtutil.today()
    
    sql = f'''
    SELECT 
        asof_date,
        index_ticker,
        index_series,
        attachment,
        detachment,
        tranche_upfront_mid,
        tranche_spread_mid,
        base_correlation,
        delta,
        tranche_pv01,
        index_pv01
    FROM markit_cds_index_tranche
    WHERE asof_date BETWEEN '{fromdt}' AND '{todt}'
        AND index_ticker = '{index_ticker}'
        AND attachment = {attachment}
        AND detachment = {detachment}
    ORDER BY asof_date
    '''
    
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


def get_tranche_structure(index_ticker, asof_date=None):
    asof_date = dtutil.todate(asof_date) if asof_date else dtutil.today()
    
    sql = f'''
    SELECT 
        asof_date,
        index_ticker,
        index_series,
        index_term,
        attachment,
        detachment,
        tranche_upfront_mid,
        tranche_spread_mid,
        base_correlation,
        delta,
        tranche_pv01
    FROM markit_cds_index_tranche
    WHERE asof_date = '{asof_date}'
        AND index_ticker = '{index_ticker}'
    ORDER BY attachment
    '''
    
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


if __name__ == '__main__':
    df = get_cds_index_tranche(
        fromdt='2025-10-24',
        todt='2025-10-24',
        index_family=['CDX', 'ITRAXX-European']
    )
    logging.info(df)

