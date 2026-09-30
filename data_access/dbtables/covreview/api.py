"""
Covenant Review Database Operations

Functions for reading and writing Covenant Review data to the database.
"""

import logging
from typing import List, Optional

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.utils import get_where_clause, join_where_clauses


APP = 'databricks'
TABLE_NAME = 'creditsights_covenant_scores'
SCHEMA = 'dbo'


def get_covenant_data(
    lxids: Optional[List[str]] = None,
    borrowers: Optional[List[str]] = None,
    limit: Optional[int] = None
) -> pd.DataFrame:
    """
    Get covenant data from the database.
    
    Args:
        lxids: Filter by LXID list
        borrowers: Filter by borrower names
        limit: Max rows to return
        
    Returns:
        DataFrame with covenant data
    """
    sql = f"""
    SELECT *
    FROM {SCHEMA}.{TABLE_NAME}
    """
    
    where_clauses = []
    
    if lxids:
        where_clauses.append(get_where_clause('lxid', lxids))
    
    if borrowers:
        where_clauses.append(get_where_clause('borrower', borrowers))
    
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    
    sql += "\nORDER BY lxid"
    
    if limit:
        sql += f"\nLIMIT {limit}"
    
    logging.info('Reading covenant data from database')
    df = readdfsql(sql, app=APP)
    return df


def get_covenant_metadata() -> pd.DataFrame:
    """
    Get metadata about the covenant data table.
    
    Returns:
        DataFrame with table statistics
    """
    sql = f"""
    SELECT 
        COUNT(*) as total_records,
        COUNT(DISTINCT lxid) as unique_lxids,
        COUNT(DISTINCT borrower) as unique_borrowers,
        MIN(_load_timestamp) as earliest_load,
        MAX(_load_timestamp) as latest_load
    FROM {SCHEMA}.{TABLE_NAME}
    """
    
    df = readdfsql(sql, app=APP)
    return df


def upsert_covenant_data(df: pd.DataFrame, mode: str = 'overwrite') -> None:
    """
    Write covenant data to the database.
    
    Args:
        df: DataFrame with covenant data
        mode: Write mode ('overwrite' or 'append')
    """
    if df.empty:
        logging.warning('Empty dataframe provided')
        return
    
    from data_access.spark.api import sparkmanager
    
    logging.info('Writing %s records to %s.%s (mode=%s)', len(df), SCHEMA, TABLE_NAME, mode)
    
    sparkmanager.pandastodelta(
        df=df,
        table_name=TABLE_NAME,
        blob_dir='tables/dbo',
        mode=mode
    )
    
    logging.info('✓ Data written to %s.%s', SCHEMA, TABLE_NAME)


def get_lxid_universe() -> pd.DataFrame:
    """
    Get the LXID universe from findox holdings.
    
    Returns:
        DataFrame with LXID list and metadata
    """
    sql = """
    WITH quoted_identifiers AS (
      SELECT
        issuer_id,
        identifier AS quoted_identifier,
        asset_name as security,
        MAX(load_date) as max_load_date
      FROM dbo.findox_models_common_ids
      WHERE identifier_type_name = 'lxid'
      GROUP BY issuer_id, identifier, asset_name
    )
    SELECT
      fc.deal_issuer_id,
      fc.issuer_name,
      fc.system_issuer_name,
      idents.quoted_identifier AS lxid,
      idents.security as security,
      idents.max_load_date
    FROM dbo.findox_core_holdings fc
    LEFT JOIN quoted_identifiers idents
      ON fc.deal_issuer_id = idents.issuer_id
    WHERE idents.quoted_identifier IS NOT NULL
    """
    
    logging.info('Fetching LXID universe from database')
    df = readdfsql(sql, app=APP)
    return df


if __name__ == '__main__':
    import os
    from common.utils.logutil import setuplogger

    setuplogger('covreview_db', logconsole=True)

    from apo_config.config import config
    from data_access.spark.api import sparkmanager

    env = os.environ.get('APO_ENV', 'dev')
    config.init(env)
    sparkmanager.init(copy_libs=False)

    logging.info('=' * 70)
    logging.info('🔗 COVENANT REVIEW DATABASE TEST')
    logging.info('=' * 70)

    # Test LXID universe query
    logging.info('Testing LXID universe query...')
    lxid_df = get_lxid_universe()
    logging.info('Found %s LXID records', len(lxid_df))
    
    # Test covenant data query
    logging.info('')
    logging.info('Testing covenant data query...')
    try:
        data_df = get_covenant_data(limit=5)
        logging.info('Found %s covenant records', len(data_df))
        if not data_df.empty:
            print(data_df[['lxid', 'borrower']].head())
    except Exception as e:
        logging.warning('No covenant data in table yet: %s', e)
    
    logging.info('')
    logging.info('=' * 70)
    logging.info('✅ DATABASE TEST COMPLETE')
    logging.info('=' * 70)
