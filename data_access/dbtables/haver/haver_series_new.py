import logging

import pandas as pd

from common.utils.sqlutil import readdfsql, insertdf_batch, execsql
from data_access.beta_attribution.utils import get_date_between_clause
from data_access.dbtables import HAVER_APP
from data_access.utils import get_where_clause, join_where_clauses, tosqllist


SERIES_DATA_TABLE = 'haver_series_data_new'
SERIES_METADATA_TABLE = 'haver_series_metadata_new'
DATABASES_TABLE = 'haver_databases_new'


def get_series_data(series_codes, start_date=None, end_date=None):
    sql = f"""
    SELECT series_code, series_name, database_name, asof_date, value, frequency, geography, source_name, last_modified
    FROM {SERIES_DATA_TABLE}
    """
    where_clauses = [get_where_clause('series_code', series_codes)]

    if start_date or end_date:
        where_clauses.append(get_date_between_clause('asof_date', start_date, end_date))

    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"

    sql += "\nORDER BY series_code, asof_date"
    logging.info('Reading %s series from %s', len(series_codes) if isinstance(series_codes, list) else 1, SERIES_DATA_TABLE)
    return readdfsql(sql, app=HAVER_APP)


def get_series_metadata(database=None, series_codes=None):
    sql = f"""
    SELECT DISTINCT
        series_code, series_name, database_name, description,
        frequency, geography, source_name, last_modified, load_date
    FROM {SERIES_METADATA_TABLE}
    """
    where_clauses = []

    if database:
        where_clauses.append(get_where_clause('database_name', database))
    if series_codes:
        where_clauses.append(get_where_clause('series_code', series_codes))

    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"

    logging.info('Reading metadata from %s', SERIES_METADATA_TABLE)
    return readdfsql(sql, app=HAVER_APP)


def get_databases():
    sql = f"""
    SELECT database_name, database_description, total_series_count,
           last_updated, is_active, load_date
    FROM {DATABASES_TABLE}
    """
    sql += f"\nWHERE {get_where_clause('is_active', 1)}"
    sql += "\nORDER BY database_name"
    return readdfsql(sql, app=HAVER_APP)


def get_database_info(database_name):
    sql = f"""
    SELECT database_name, database_description, total_series_count,
           last_updated, is_active, load_date
    FROM {DATABASES_TABLE}
    """
    sql += f"\nWHERE {get_where_clause('database_name', database_name)}"
    return readdfsql(sql, app=HAVER_APP)


def get_series_by_database(database):
    sql = f"""
    SELECT DISTINCT series_code, series_name, description, frequency, geography
    FROM {SERIES_METADATA_TABLE}
    """
    sql += f"\nWHERE {get_where_clause('database_name', database)}"
    sql += "\nORDER BY series_code"
    return readdfsql(sql, app=HAVER_APP)


def upsert_series_data(df):
    if df.empty:
        logging.warning('Empty dataframe provided for upsert')
        return

    required_cols = ['series_code', 'asof_date', 'value']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')

    df = df.copy()
    df['asof_date'] = pd.to_datetime(df['asof_date'])
    if 'load_date' in df.columns:
        df = df.sort_values('load_date')
    df['_value_key'] = df['value'].round(10)
    df = df.drop_duplicates(subset=['series_code', 'asof_date', '_value_key'], keep='first').drop(columns='_value_key')

    series_codes = df['series_code'].unique().tolist()
    min_asof_date = df['asof_date'].min().strftime('%Y-%m-%d')
    max_asof_date = df['asof_date'].max().strftime('%Y-%m-%d')

    existing_dfs = []
    for i in range(0, len(series_codes), 200):
        chunk = series_codes[i:i + 200]
        sql = f"""
        SELECT series_code, asof_date, value
        FROM {SERIES_DATA_TABLE}
        WHERE series_code IN {tosqllist(chunk)}
          AND asof_date >= '{min_asof_date}'
          AND asof_date <= '{max_asof_date}'
        """
        chunk_df = readdfsql(sql, app=HAVER_APP)
        if not chunk_df.empty:
            existing_dfs.append(chunk_df)

    df_existing = pd.concat(existing_dfs, ignore_index=True) if existing_dfs else pd.DataFrame()
    if df_existing.empty:
        logging.info('No existing data in %s, inserting %s rows', SERIES_DATA_TABLE, len(df))
        insertdf_batch(SERIES_DATA_TABLE, df, app=HAVER_APP)
        return

    df_existing['asof_date'] = pd.to_datetime(df_existing['asof_date'])
    existing_set = set(zip(
        df_existing['series_code'],
        df_existing['asof_date'].dt.date,
        df_existing['value'].round(10),
    ))

    df_new = df[
        ~df.apply(lambda r: (r['series_code'], r['asof_date'].date(), round(r['value'], 10)) in existing_set, axis=1)
    ]
    if df_new.empty:
        logging.info('No new/changed values to insert into %s', SERIES_DATA_TABLE)
        return

    logging.info('Inserting %s rows into %s', len(df_new), SERIES_DATA_TABLE)
    insertdf_batch(SERIES_DATA_TABLE, df_new, app=HAVER_APP)


def upsert_series_metadata(df):
    if df.empty:
        logging.warning('Empty dataframe provided for metadata upsert')
        return

    required_cols = ['series_code', 'database_name']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')

    series_codes = df['series_code'].unique().tolist()
    execsql(f"DELETE FROM {SERIES_METADATA_TABLE} WHERE series_code IN {tosqllist(series_codes)}", app=HAVER_APP)
    insertdf_batch(SERIES_METADATA_TABLE, df, app=HAVER_APP)
    logging.info('Upserted %s metadata rows to %s', len(df), SERIES_METADATA_TABLE)


def get_latest_update_date(series_code):
    sql = f"""
    SELECT series_code, MAX(asof_date) as latest_date, MAX(load_date) as last_loaded
    FROM {SERIES_DATA_TABLE}
    """
    sql += f"\nWHERE {get_where_clause('series_code', series_code)}"
    sql += "\nGROUP BY series_code"
    return readdfsql(sql, app=HAVER_APP)


def get_series_count_by_database():
    sql = f"""
    SELECT m.database_name,
           COUNT(DISTINCT m.series_code) as series_count_loaded,
           d.total_series_count as series_count_total,
           d.last_updated
    FROM {SERIES_METADATA_TABLE} m
    LEFT JOIN {DATABASES_TABLE} d ON m.database_name = d.database_name
    GROUP BY m.database_name, d.total_series_count, d.last_updated
    ORDER BY series_count_loaded DESC
    """
    return readdfsql(sql, app=HAVER_APP)


def upsert_database_info(df):
    if df.empty:
        logging.warning('Empty dataframe provided for database info upsert')
        return

    required_cols = ['database_name']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f'DataFrame must contain columns: {required_cols}')

    database_names = df['database_name'].unique().tolist()
    execsql(f"DELETE FROM {DATABASES_TABLE} WHERE database_name IN {tosqllist(database_names)}", app=HAVER_APP)
    insertdf_batch(DATABASES_TABLE, df, app=HAVER_APP)
    logging.info('Upserted %s database rows to %s', len(df), DATABASES_TABLE)


def deactivate_databases(database_names):
    if isinstance(database_names, str):
        database_names = [database_names]
    database_names = [name for name in dict.fromkeys(database_names) if name]
    if not database_names:
        return

    execsql(
        f"UPDATE {DATABASES_TABLE} SET is_active = false WHERE database_name IN {tosqllist(database_names)}",
        app=HAVER_APP,
    )
    logging.info('Deactivated %s databases in %s', len(database_names), DATABASES_TABLE)


def deactivate_database(database_name):
    deactivate_databases([database_name])


def get_series_needing_update(database=None, days_old=1):
    from datetime import datetime, timedelta

    cutoff_date = datetime.now() - timedelta(days=days_old)
    sql = f"""
    SELECT DISTINCT series_code
    FROM {SERIES_METADATA_TABLE}
    WHERE load_date < ?
    """
    params = [cutoff_date]

    if database:
        sql += " AND database_name = ?"
        params.append(database)

    sql += " ORDER BY series_code"
    return readdfsql(sql, app=HAVER_APP, params=params)


def get_series_with_data_since(database=None, since_date=None):
    if since_date:
        since_dt = pd.to_datetime(since_date)
    else:
        from datetime import datetime, timedelta
        since_dt = datetime.now() - timedelta(days=1)

    sql = f"""
    SELECT DISTINCT series_code
    FROM {SERIES_DATA_TABLE}
    WHERE asof_date >= ?
    """
    params = [since_dt]

    if database:
        sql += " AND database_name = ?"
        params.append(database)

    sql += " ORDER BY series_code"
    return readdfsql(sql, app=HAVER_APP, params=params)


def delete_all_haver_data():
    execsql(f'DELETE FROM {SERIES_METADATA_TABLE}', app=HAVER_APP)
    execsql(f'DELETE FROM {DATABASES_TABLE}', app=HAVER_APP)
    logging.info('All _new Haver tables cleaned')


def get_series_data_latest(series_codes, start_date=None, end_date=None):
    return get_series_data(series_codes, start_date=start_date, end_date=end_date)
