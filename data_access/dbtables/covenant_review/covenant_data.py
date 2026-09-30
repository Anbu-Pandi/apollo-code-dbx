import logging
from typing import List, Optional

import pandas as pd

from common.utils.sqlutil import readdfsql, insertdf_batch
from data_access.dbtables.covenant_review import COVENANT_REVIEW_APP

TABLE_PORTFOLIO = 'creditsights_covenant_scores'
TABLE_INDEX = 'creditsights_covenant_scores_index'
PRIMARY_KEY_PORTFOLIO = ['lxid', 'lfi_id', 'status']
PRIMARY_KEY_INDEX = ['index_type', 'status', 'version_count', 'scoring_composite_n']
IGNORE_COLS = {'load_date', 'knowledge_date', 'asof_date', '_load_timestamp'}

TABLE_NAME = TABLE_PORTFOLIO
PRIMARY_KEY = PRIMARY_KEY_PORTFOLIO


def get_incremental(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    new_df, updated_df = _get_changes(df)
    combined = pd.concat([new_df, updated_df], ignore_index=True)
    logging.info('📊 Incremental: %s new + %s updated (from %s total)', len(new_df), len(updated_df), len(df))
    return combined


def write_data(df: pd.DataFrame, usespark: bool = False):
    if df.empty:
        logging.warning('No data to write')
        return 0
    _write_data(df, usespark)
    return len(df)


def load_covenant_data(df: pd.DataFrame, usespark: bool = False):
    incremental = get_incremental(df)
    if incremental.empty:
        logging.info('✨ No changes detected')
        return 0
    return write_data(incremental, usespark)


def _get_changes(df: pd.DataFrame) -> tuple:
    existing = _get_existing_data()
    if existing.empty:
        return df, pd.DataFrame()

    common_cols = [c for c in df.columns if c in existing.columns and c not in IGNORE_COLS]
    compare_cols = [c for c in common_cols if c not in PRIMARY_KEY]

    merged = df.merge(existing[common_cols], on=PRIMARY_KEY, how='left', suffixes=('', '_old'), indicator=True)

    new_df = merged[merged['_merge'] == 'left_only'][df.columns]

    both = merged[merged['_merge'] == 'both']
    if both.empty:
        return new_df, pd.DataFrame()

    changed_mask = pd.Series(False, index=both.index)
    for col in compare_cols:
        if f'{col}_old' in both.columns:
            changed_mask |= (both[col].astype(str).fillna('') != both[f'{col}_old'].astype(str).fillna(''))

    updated_df = both[changed_mask][df.columns]
    return new_df, updated_df


def _get_existing_data() -> pd.DataFrame:
    key_cols = ', '.join(PRIMARY_KEY)
    sql = f"""
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_cols} ORDER BY load_date DESC) as rn
        FROM dbo.{TABLE_NAME}
    ) WHERE rn = 1
    """
    return readdfsql(sql, app=COVENANT_REVIEW_APP)


def _write_data(df: pd.DataFrame, usespark: bool):
    if usespark:
        from data_access.spark.api import sparkmanager
        sparkmanager.pandastosql(df, f'dbo.{TABLE_PORTFOLIO}', app=COVENANT_REVIEW_APP)
    else:
        insertdf_batch(f'dbo.{TABLE_PORTFOLIO}', df, cleantable=False, app=COVENANT_REVIEW_APP)
    logging.info('Appended %s records to dbo.%s', len(df), TABLE_PORTFOLIO)


def get_covenant_data(lxids: Optional[List[str]] = None, status: Optional[str] = None) -> pd.DataFrame:
    sql = f"SELECT * FROM dbo.{TABLE_NAME} WHERE 1=1"
    
    if lxids:
        lxid_list = "','".join(lxids)
        sql += f" AND lxid IN ('{lxid_list}')"
    
    if status:
        sql += f" AND status = '{status}'"
    
    return readdfsql(sql, app=COVENANT_REVIEW_APP)


def get_latest_scores(lxids: Optional[List[str]] = None) -> pd.DataFrame:
    sql = f"""
    SELECT lxid, borrower, status, version_count,
           scoring_composite_n, scoring_composite_s, load_date
    FROM dbo.{TABLE_NAME}
    WHERE 1=1
    """
    
    if lxids:
        lxid_list = "','".join(lxids)
        sql += f" AND lxid IN ('{lxid_list}')"
    
    sql += " ORDER BY lxid, load_date DESC"
    
    return readdfsql(sql, app=COVENANT_REVIEW_APP)


def get_latest_load_date():
    sql = f"SELECT MAX(load_date) as max_load FROM dbo.{TABLE_NAME}"
    df = readdfsql(sql, app=COVENANT_REVIEW_APP)
    return df['max_load'].iloc[0] if not df.empty else None


def get_latest_view(lxids: Optional[List[str]] = None) -> pd.DataFrame:
    key_cols = ', '.join(PRIMARY_KEY)
    sql = f"""
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_cols} ORDER BY load_date DESC) as rn
        FROM dbo.{TABLE_NAME}
    ) WHERE rn = 1
    """
    if lxids:
        lxid_list = "','".join(lxids)
        sql = f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_cols} ORDER BY load_date DESC) as rn
            FROM dbo.{TABLE_NAME}
            WHERE lxid IN ('{lxid_list}')
        ) WHERE rn = 1
        """
    df = readdfsql(sql, app=COVENANT_REVIEW_APP)
    return df.drop(columns=['rn']) if 'rn' in df.columns else df


def get_incremental_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    new_df, updated_df = _get_changes_index(df)
    combined = pd.concat([new_df, updated_df], ignore_index=True)
    logging.info('📊 Index Incremental: %s new + %s updated (from %s total)', len(new_df), len(updated_df), len(df))
    return combined


def write_index_data(df: pd.DataFrame, usespark: bool = True):
    if df.empty:
        logging.warning('No index data to write')
        return 0
    _write_index_data(df, usespark)
    return len(df)


def load_index_data(df: pd.DataFrame, usespark: bool = True):
    """
    Load index data - appends daily snapshots.
    Index data is anonymized with no natural key, so we append full snapshots.
    Use load_date to filter/distinguish snapshots when querying.
    """
    if df.empty:
        logging.info('No index data to load')
        return 0
    return write_index_data(df, usespark)


def _get_changes_index(df: pd.DataFrame) -> tuple:
    existing = _get_existing_index_data()
    if existing.empty:
        return df, pd.DataFrame()

    pk = [c for c in PRIMARY_KEY_INDEX if c in df.columns]
    if not pk:
        logging.warning('No valid primary key columns for index comparison, treating all as new')
        return df, pd.DataFrame()

    common_cols = [c for c in df.columns if c in existing.columns and c not in IGNORE_COLS]
    compare_cols = [c for c in common_cols if c not in pk]

    merged = df.merge(existing[common_cols], on=pk, how='left', suffixes=('', '_old'), indicator=True)

    new_df = merged[merged['_merge'] == 'left_only'][df.columns]

    both = merged[merged['_merge'] == 'both']
    if both.empty:
        return new_df, pd.DataFrame()

    changed_mask = pd.Series(False, index=both.index)
    for col in compare_cols:
        if f'{col}_old' in both.columns:
            changed_mask |= (both[col].astype(str).fillna('') != both[f'{col}_old'].astype(str).fillna(''))

    updated_df = both[changed_mask][df.columns]
    return new_df, updated_df


def _get_existing_index_data() -> pd.DataFrame:
    pk = ', '.join(PRIMARY_KEY_INDEX)
    sql = f"""
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY load_date DESC) as rn
        FROM dbo.{TABLE_INDEX}
    ) WHERE rn = 1
    """
    return readdfsql(sql, app=COVENANT_REVIEW_APP)


def _write_index_data(df: pd.DataFrame, usespark: bool):
    if usespark:
        from data_access.spark.api import sparkmanager
        sparkmanager.pandastosql(df, f'dbo.{TABLE_INDEX}', app=COVENANT_REVIEW_APP)
    else:
        insertdf_batch(f'dbo.{TABLE_INDEX}', df, cleantable=False, app=COVENANT_REVIEW_APP)
    logging.info('Appended %s records to dbo.%s', len(df), TABLE_INDEX)


def get_index_data(index_type: Optional[str] = None) -> pd.DataFrame:
    sql = f"SELECT * FROM dbo.{TABLE_INDEX} WHERE 1=1"
    if index_type:
        sql += f" AND index_type = '{index_type}'"
    return readdfsql(sql, app=COVENANT_REVIEW_APP)


def get_latest_index_load_date():
    sql = f"SELECT MAX(load_date) as max_load FROM dbo.{TABLE_INDEX}"
    df = readdfsql(sql, app=COVENANT_REVIEW_APP)
    return df['max_load'].iloc[0] if not df.empty else None
