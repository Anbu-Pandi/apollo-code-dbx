import logging

import pandas as pd

from common.utils.sqlutil import readdfsql, insertdf_batch
from data_access.dbtables import APP
from data_access.dbtables.security_data.utils import _clean_tables_by_ids, _get_existing_ids, processdf_for_insert
from data_access.spark.api import sparkmanager
from data_access.utils import get_where_clause

TABLE_KEY_MAP = {'wso_bond': 'id',
                 'wso_bond_amount_details': 'id',
                 'wso_bond_asset_exchange': 'id',
                 'wso_bond_call_schedule': 'id',
                 'wso_bond_cash_flow_details': 'id',
                 'wso_bond_clearing_schedule': 'id',
                 'wso_bond_derived_data': 'id',
                 'wso_bond_entity_details': 'id',
                 'wso_bond_holiday_schedule': 'id',
                 'wso_bond_instrument_relation': 'id',
                 'wso_bond_interest_schedule': 'id',
                 'wso_bond_issuer': 'id',
                 'wso_bond_notes': 'id',
                 'wso_bond_principal_details': 'id',
                 'wso_bond_put_schedule': 'id',
                 'wso_bond_sedol_details': 'id',
                 'wso_bond_trading_and_selling_restriction_details': 'id',
                 'wso_bond_covenants': 'id',
                 'wso_bond_regulation_details': 'id',
                 'wso_bond_government_auction_details': 'id'}


def get_all_bonds():
    SQL = 'SELECT DISTINCT * FROM dbo.wso_bond'
    df = readdfsql(SQL, app=APP)
    return df


def get_bonds(bondids):
    SQL = f"""
SELECT DISTINCT * FROM dbo.wso_bond 
WHERE 
{get_where_clause('id', bondids)}
OR {get_where_clause('cusip', bondids)}
OR {get_where_clause('isin', bondids)}
OR {get_where_clause('bloomberg_ticker', bondids)}
OR {get_where_clause('bloomberg_unique_id', bondids)}
OR {get_where_clause('figi', bondids)}
    """
    df = readdfsql(SQL, app=APP)
    return df


def clean_bond(bondids):
    dfbond = get_bonds(bondids)
    ids = dfbond['id'].unique().tolist()
    _clean_tables_by_ids('id', ids, TABLE_KEY_MAP)


def upsert_bond(dfs, overwrite=False, usespark=False):
    for table, df in dfs.items():
        if isinstance(df, list):
            df = pd.concat(df)
        logging.info(f'upserting table: {table}, df shape: {df.shape}')
        id_column = TABLE_KEY_MAP[table]
        ids = df[id_column].unique().tolist()
        if overwrite:
            _clean_tables_by_ids(id_column, ids, TABLE_KEY_MAP)
        df = df[~df[id_column].isin(_get_existing_ids(table, id_column, ids))]
        df = processdf_for_insert(df)
        if usespark:
            sparkmanager.writetosql(df, table)
        else:
            insertdf_batch(table, df, app=APP)
