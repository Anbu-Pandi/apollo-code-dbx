import logging

import pandas as pd

from common.utils.sqlutil import readdfsql, insertdf_batch, execsql
from data_access.dbtables import HAVER_APP
from data_access.utils import get_where_clause, join_where_clauses
from data_access.beta_attribution.utils import get_date_between_clause


SERIES_DATA_TABLE = 'haver_series_data'
LATEST_SERIES_DATA_VIEW = 'v_haver_series_data'


def _get_date_filter(date_column, start_date=None, end_date=None):
    if start_date and end_date:
        return get_date_between_clause(date_column, start_date, end_date)
    if start_date:
        return f"{date_column} >= '{pd.to_datetime(start_date).strftime('%Y-%m-%d')}'"
    if end_date:
        return f"{date_column} <= '{pd.to_datetime(end_date).strftime('%Y-%m-%d')}'"
    return None


def get_series_data(series_codes, start_date=None, end_date=None):
    sql = """
    SELECT series_code, series_name, database_name, asof_date, value, frequency, geography, source_name, last_modified
    FROM haver_series_data
    """
    
    where_clauses = []
    
    where_clauses.append(get_where_clause('series_code', series_codes))
    
    if start_date or end_date:
        date_clause = _get_date_filter('asof_date', start_date, end_date)
        where_clauses.append(date_clause)
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY series_code, asof_date"
    
    logging.info('Reading %s series from database', len(series_codes) if isinstance(series_codes, list) else 1)
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_series_metadata(database=None, series_codes=None):
    sql = """
    SELECT DISTINCT 
        series_code, series_name, database_name, description, 
        frequency, geography, source_name, last_modified, load_date
    FROM haver_series_metadata
    """
    
    where_clauses = []
    
    if database:
        where_clauses.append(get_where_clause('database_name', database))
    
    if series_codes:
        where_clauses.append(get_where_clause('series_code', series_codes))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    logging.info('Reading metadata from database')
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_databases():
    sql = """
    SELECT database_name, database_description, total_series_count, 
           last_updated, is_active, load_date
    FROM haver_databases
    """
    
    where_clause = get_where_clause('is_active', 1)
    sql += f"\nWHERE {where_clause}"
    sql += "\nORDER BY database_name"
    
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_database_info(database_name):
    sql = """
    SELECT database_name, database_description, total_series_count, 
           last_updated, is_active, load_date
    FROM haver_databases
    """
    
    where_clause = get_where_clause('database_name', database_name)
    sql += f"\nWHERE {where_clause}"
    
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_series_by_database(database):
    sql = """
    SELECT DISTINCT series_code, series_name, description, frequency, geography
    FROM haver_series_metadata
    """
    
    where_clause = get_where_clause('database_name', database)
    sql += f"\nWHERE {where_clause}"
    sql += "\nORDER BY series_code"
    
    df = readdfsql(sql, app=HAVER_APP)
    return df


def upsert_series_data(df):
    if df.empty:
        logging.warning('Empty dataframe provided for upsert')
        return
    
    required_cols = ['series_code', 'asof_date', 'value']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')
    
    logging.info('Processing %s rows for haver_series_data', len(df))
    
    from data_access.utils import tosqllist
    import pandas as pd

    df = df.copy()
    df['asof_date'] = pd.to_datetime(df['asof_date'])
    if 'load_date' in df.columns:
        df = df.sort_values('load_date')
    df['_value_key'] = df['value'].round(10)
    df = df.drop_duplicates(subset=['series_code', 'asof_date', '_value_key'], keep='first').drop(columns='_value_key')

    series_codes = df['series_code'].unique().tolist()
    min_asof_date = df['asof_date'].min().strftime('%Y-%m-%d')
    max_asof_date = df['asof_date'].max().strftime('%Y-%m-%d')

    CHUNK = 200
    existing_dfs = []
    for i in range(0, len(series_codes), CHUNK):
        chunk = series_codes[i:i + CHUNK]
        sql = f"""
        SELECT series_code, asof_date, value
        FROM haver_series_data
        WHERE series_code IN {tosqllist(chunk)}
          AND asof_date >= '{min_asof_date}'
          AND asof_date <= '{max_asof_date}'
        """
        chunk_df = readdfsql(sql, app=HAVER_APP)
        if not chunk_df.empty:
            existing_dfs.append(chunk_df)

    df_existing = pd.concat(existing_dfs, ignore_index=True) if existing_dfs else pd.DataFrame()

    if df_existing.empty:
        logging.info('No existing data, inserting all %s rows', len(df))
        insertdf_batch('haver_series_data', df, app=HAVER_APP)
        return
    
    df_existing['asof_date'] = pd.to_datetime(df_existing['asof_date'])
    
    existing_set = set(
        zip(df_existing['series_code'], df_existing['asof_date'].dt.date, df_existing['value'].round(10))
    )
    
    df_new = df[
        ~df.apply(lambda r: (r['series_code'], r['asof_date'].date(), round(r['value'], 10)) in existing_set, axis=1)
    ]
    
    if df_new.empty:
        logging.info('✓ No new/changed values to insert (all %s rows already exist)', len(df))
        return
    
    logging.info('📥 Inserting %s rows (%s unchanged)', len(df_new), len(df) - len(df_new))
    insertdf_batch('haver_series_data', df_new, app=HAVER_APP)


def upsert_series_metadata(df):
    if df.empty:
        logging.warning('Empty dataframe provided for metadata upsert')
        return
    
    required_cols = ['series_code', 'database_name']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')
    
    logging.info('Upserting %s metadata rows to haver_series_metadata', len(df))
    
    from data_access.utils import tosqllist
    
    series_codes = df['series_code'].unique().tolist()
    
    delete_sql = f"""
    DELETE FROM haver_series_metadata 
    WHERE series_code IN {tosqllist(series_codes)}
    """
    
    execsql(delete_sql, app=HAVER_APP)
    logging.info('Deleted existing metadata for %s series', len(series_codes))
    insertdf_batch('haver_series_metadata', df, app=HAVER_APP)


def get_latest_update_date(series_code):
    sql = """
    SELECT series_code, MAX(asof_date) as latest_date, MAX(load_date) as last_loaded
    FROM haver_series_data
    """
    
    where_clause = get_where_clause('series_code', series_code)
    sql += f"\nWHERE {where_clause}"
    sql += "\nGROUP BY series_code"
    
    df = readdfsql(sql, app=HAVER_APP)
    return df


def get_series_count_by_database():
    sql = """
    SELECT m.database_name, 
           COUNT(DISTINCT m.series_code) as series_count_loaded,
           d.total_series_count as series_count_total,
           d.last_updated
    FROM haver_series_metadata m
    LEFT JOIN haver_databases d ON m.database_name = d.database_name
    GROUP BY m.database_name, d.total_series_count, d.last_updated
    ORDER BY series_count_loaded DESC
    """
    df = readdfsql(sql, app=HAVER_APP)
    return df


def upsert_database_info(df):
    if df.empty:
        logging.warning('Empty dataframe provided for database info upsert')
        return
    
    required_cols = ['database_name']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')
    
    logging.info('Upserting %s database records', len(df))
    
    from data_access.utils import tosqllist
    
    database_names = df['database_name'].unique().tolist()
    
    delete_sql = f"""
    DELETE FROM haver_databases 
    WHERE database_name IN {tosqllist(database_names)}
    """
    
    execsql(delete_sql, app=HAVER_APP)
    logging.info('Deleted existing info for %s databases', len(database_names))
    insertdf_batch('haver_databases', df, app=HAVER_APP)


def deactivate_databases(database_names):
    from data_access.utils import tosqllist

    if isinstance(database_names, str):
        database_names = [database_names]
    database_names = [name for name in dict.fromkeys(database_names) if name]
    if not database_names:
        return

    logging.info('Deactivating inaccessible databases: %s', ', '.join(database_names[:20]))
    execsql(
        f"UPDATE haver_databases SET is_active = false WHERE database_name IN {tosqllist(database_names)}",
        app=HAVER_APP
    )


def deactivate_database(database_name):
    deactivate_databases([database_name])


def get_series_needing_update(database=None, days_old=1):
    from datetime import datetime, timedelta
    cutoff_date = datetime.now() - timedelta(days=days_old)
    
    sql = """
    SELECT DISTINCT series_code
    FROM haver_series_metadata
    WHERE load_date < ?
    """
    
    params = [cutoff_date]
    
    if database:
        sql += " AND database_name = ?"
        params.append(database)
    
    sql += " ORDER BY series_code"
    
    logging.info('Finding series older than %s days', days_old)
    df = readdfsql(sql, app=HAVER_APP, params=params)
    return df


def get_series_with_data_since(database=None, since_date=None):
    import pandas as pd
    
    if since_date:
        since_dt = pd.to_datetime(since_date)
    else:
        from datetime import datetime, timedelta
        since_dt = datetime.now() - timedelta(days=1)
    
    sql = """
    SELECT DISTINCT series_code
    FROM haver_series_data
    WHERE asof_date >= ?
    """
    
    params = [since_dt]
    
    if database:
        sql += " AND database_name = ?"
        params.append(database)
    
    sql += " ORDER BY series_code"
    
    logging.info('Finding series with data since %s', since_dt.strftime('%Y-%m-%d'))
    df = readdfsql(sql, app=HAVER_APP, params=params)
    return df


def delete_all_haver_data():
    logging.info('Deleting ALL haver_series_metadata')
    execsql('DELETE FROM haver_series_metadata', app=HAVER_APP)
    
    logging.info('Deleting ALL haver_databases')
    execsql('DELETE FROM haver_databases', app=HAVER_APP)
    
    logging.info('✓ All haver tables cleaned')


def get_series_data_latest(series_codes, start_date=None, end_date=None):
    sql = f"""
    SELECT series_code, series_name, database_name, asof_date, value, last_modified, knowledge_date, load_date
    FROM {LATEST_SERIES_DATA_VIEW}
    """
    
    where_clauses = []
    where_clauses.append(get_where_clause('series_code', series_codes))
    
    if start_date or end_date:
        date_clause = _get_date_filter('asof_date', start_date, end_date)
        where_clauses.append(date_clause)
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY series_code, asof_date"
    
    logging.info('Reading latest %s series from view', len(series_codes) if isinstance(series_codes, list) else 1)
    return readdfsql(sql, app=HAVER_APP)


def get_series_data_asof(series_codes, knowledge_date, start_date=None, end_date=None):
    knowledge_dt = pd.to_datetime(knowledge_date).strftime('%Y-%m-%d %H:%M:%S')
    sql = f"""
    WITH ranked AS (
        SELECT series_code, series_name, database_name, asof_date, value,
               frequency, geography, source_name, last_modified, knowledge_date, load_date,
               ROW_NUMBER() OVER (
                   PARTITION BY series_code, asof_date
                   ORDER BY knowledge_date DESC, load_date DESC
               ) AS rn
        FROM {SERIES_DATA_TABLE}
    """

    where_clauses = [get_where_clause('series_code', series_codes)]
    where_clauses.append(f"knowledge_date <= '{knowledge_dt}'")

    if start_date or end_date:
        where_clauses.append(_get_date_filter('asof_date', start_date, end_date))

    sql += f"\nWHERE {join_where_clauses(' AND ', where_clauses)}"
    sql += """
    )
    SELECT series_code, series_name, database_name, asof_date, value,
           frequency, geography, source_name, last_modified, knowledge_date, load_date
    FROM ranked
    WHERE rn = 1
    ORDER BY series_code, asof_date
    """

    logging.info('Reading %s series as of knowledge date %s', len(series_codes) if isinstance(series_codes, list) else 1, knowledge_dt)
    return readdfsql(sql, app=HAVER_APP)


def delete_exact_duplicates():
    logging.info('🧹 Removing exact duplicate rows (keeping oldest load_date)')
    
    count_sql = """
    SELECT COUNT(*) as cnt FROM (
        SELECT series_code, asof_date, value, load_date,
            ROW_NUMBER() OVER (PARTITION BY series_code, asof_date, value ORDER BY load_date ASC) as rn
        FROM haver_series_data
    ) WHERE rn > 1
    """
    df_count = readdfsql(count_sql, app=HAVER_APP)
    dupe_count = int(df_count.iloc[0]['cnt']) if not df_count.empty else 0
    
    if dupe_count == 0:
        logging.info('✓ No exact duplicates found')
        return 0
    
    logging.info('Found %s exact duplicates, removing...', dupe_count)
    
    dedup_sql = """
    CREATE OR REPLACE TABLE haver_series_data AS
    SELECT series_code, series_name, database_name, series_id, asof_date, 
           value, last_modified, knowledge_date, load_date
    FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY series_code, asof_date, value 
            ORDER BY load_date ASC
        ) as rn
        FROM haver_series_data
    )
    WHERE rn = 1
    """
    
    execsql(dedup_sql, app=HAVER_APP)
    logging.info('✅ Removed %s exact duplicate rows', dupe_count)
    return dupe_count
