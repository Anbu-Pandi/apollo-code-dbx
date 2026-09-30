import logging

from common.utils.sqlutil import readdfsql, insertdf_batch
from data_access.dbtables import HAVER_APP
from data_access.utils import get_where_clause, join_where_clauses
from data_access.beta_attribution.utils import get_date_between_clause


def get_data(domains=None, start_date=None, end_date=None, metrics=None, sector=None, subsector=None, frequency=None):
    sql = """
    SELECT asof_date, domain, sector, subsector, frequency, metric_name, metric_value, 
           knowledge_date, load_date
    FROM similarweb_data
    """
    
    where_clauses = []
    
    if domains:
        where_clauses.append(get_where_clause('domain', domains))
    
    if start_date or end_date:
        date_clause = get_date_between_clause('asof_date', start_date, end_date)
        where_clauses.append(date_clause)
    
    if metrics:
        where_clauses.append(get_where_clause('metric_name', metrics))
    
    if sector:
        where_clauses.append(get_where_clause('sector', sector))
    
    if subsector:
        where_clauses.append(get_where_clause('subsector', subsector))
    
    if frequency:
        where_clauses.append(get_where_clause('frequency', frequency))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY domain, asof_date, frequency, metric_name"
    
    logging.info('Reading SimilarWeb data from database')
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_domains(sector=None, subsector=None):
    sql = """
    SELECT DISTINCT domain, sector, subsector
    FROM similarweb_data
    """
    
    where_clauses = []
    
    if sector:
        where_clauses.append(get_where_clause('sector', sector))
    
    if subsector:
        where_clauses.append(get_where_clause('subsector', subsector))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY sector, subsector, domain"
    
    logging.info('Reading SimilarWeb domains from database')
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_latest_date(domain=None, frequency=None):
    sql = """
    SELECT domain, frequency, MAX(asof_date) as latest_date, MAX(load_date) as last_loaded
    FROM similarweb_data
    """
    
    where_clauses = []
    
    if domain:
        where_clauses.append(get_where_clause('domain', domain))
    
    if frequency:
        where_clauses.append(get_where_clause('frequency', frequency))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nGROUP BY domain, frequency"
    
    df = readdfsql(sql, app=HAVER_APP)
    return df


def upsert_data(df):
    if df.empty:
        logging.warning('Empty dataframe provided for upsert')
        return
    
    required_cols = ['asof_date', 'domain', 'metric_name', 'metric_value', 'frequency']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')
    
    logging.info('Upserting %s rows to similarweb_data', len(df))
    
    delete_existing_data(df)
    
    insertdf_batch('similarweb_data', df, app=HAVER_APP)


def delete_existing_data(df):
    from data_access.utils import delete_from_db_whereclause
    from common.utils import dfutil
    import pandas as pd
    
    df_clean = df.dropna(subset=['asof_date'])
    
    if df_clean.empty:
        logging.warning('No valid dates in dataframe, skipping delete')
        return
    
    df_clean['delete_key'] = (df_clean['domain'].astype(str) + '|' + 
                              df_clean['frequency'].astype(str) + '|' + 
                              df_clean['metric_name'].astype(str))
    
    unique_keys = df_clean.groupby('delete_key').agg({
        'asof_date': ['min', 'max'],
        'domain': 'first',
        'frequency': 'first',
        'metric_name': 'first'
    }).reset_index()
    unique_keys.columns = ['delete_key', 'min_date', 'max_date', 'domain', 'frequency', 'metric_name']
    
    unique_keys = unique_keys[~(pd.isna(unique_keys['min_date']) | pd.isna(unique_keys['max_date']))]
    
    if unique_keys.empty:
        logging.warning('No valid date ranges for delete')
        return
    
    logging.info('Deleting existing data for %s unique combinations using batch IN clauses', len(unique_keys))
    
    batches = list(dfutil.chunks_list(unique_keys.to_dict('records'), 5000))
    
    for i, batch in enumerate(batches, 1):
        logging.info('Processing delete batch %s/%s (%s keys)', i, len(batches), len(batch))
        
        min_date = min(row['min_date'] for row in batch)
        max_date = max(row['max_date'] for row in batch)
        
        delete_keys = [f"'{row['domain']}|{row['frequency']}|{row['metric_name']}'" for row in batch]
        keys_in_clause = ','.join(delete_keys)
        
        where_clause = f"CONCAT(domain, '|', frequency, '|', metric_name) IN ({keys_in_clause})"
        where_clause += f" AND {get_date_between_clause('asof_date', min_date, max_date)}"
        
        delete_from_db_whereclause('similarweb_data', where_clause, app=HAVER_APP)


def delete_data(domain=None, start_date=None, end_date=None, frequency=None):
    from data_access.utils import delete_from_db_whereclause
    
    where_clauses = []
    
    if domain:
        where_clauses.append(get_where_clause('domain', domain))
    
    if start_date or end_date:
        date_clause = get_date_between_clause('asof_date', start_date, end_date)
        where_clauses.append(date_clause)
    
    if frequency:
        where_clauses.append(get_where_clause('frequency', frequency))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    
    if not where_clause:
        logging.warning('No filter conditions provided for delete - skipping for safety')
        return
    
    logging.info('Deleting SimilarWeb data')
    delete_from_db_whereclause('similarweb_data', where_clause, app=HAVER_APP)
    logging.info('✓ Data deleted')

