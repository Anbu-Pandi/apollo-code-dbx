import datetime
import logging

import pandas as pd

from common.utils.sqlutil import readdfsql, insertdf_batch
from data_access.dbtables import LOAN_REFERENCEDATA_APP
from data_access.dbtables.security_data.utils import _clean_tables_by_ids, _get_existing_ids, processdf_for_insert
from data_access.spark.api import sparkmanager
from data_access.utils import get_where_clause

TABLE_KEY_MAP = {'wso_loan_agent_bank': 'wso_data_bank_deal_id',
                 'wso_loan_amortization': 'wso_data_facility_id',
                 'wso_loan_bank_deal': 'wso_data_bank_deal_id',
                 'wso_loan_call_schedule': 'wso_data_facility_id',
                 'wso_loan_contracts': 'wso_data_facility_id',
                 'wso_loan_currency_types': 'wso_data_facility_id',
                 'wso_loan_facility': 'wso_data_facility_id',
                 'wso_loan_hard_call': 'wso_data_facility_id',
                 'wso_loan_identifiers': 'wso_data_facility_id',
                 'wso_loan_issuers': 'wso_data_issuer_id',
                 'wso_loan_lines': 'wso_data_facility_id',
                 'wso_loan_make_whole': 'wso_data_facility_id',
                 'wso_loan_markit_tiers': 'wso_data_issuer_id',
                 'wso_loan_payments': 'global_amortization_id',
                 'wso_loan_rate_limits': 'wso_data_facility_id',
                 'wso_loan_rate_options': 'wso_data_facility_id',
                 'wso_loan_sponsor': 'wso_data_facility_id',
                 'wso_loan_spreads': 'wso_data_facility_id',
                 'wso_loan_syndication_agent': 'wso_data_facility_id', }

LOAD_DATE_TABLES = []


def get_all_facilities():
    SQL = 'SELECT DISTINCT * FROM dbo.wso_loan_facility'
    df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)
    return df


def get_all_issuers():
    SQL = f"SELECT DISTINCT * FROM dbo.wso_loan_issuers"
    df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)
    return df


def get_issuers(issuer):
    SQL = f"SELECT * FROM dbo.wso_loan_issuers WHERE {get_where_clause('wso_data_issuer_id', issuer)} " \
          f"OR {get_where_clause('name', issuer)} "
    df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)
    return df


def get_bankdeals(bankdeals=None, issuer=None):
    assert not (bankdeals is None and issuer is None), "need either bank deals or issuer to search!"
    df = pd.DataFrame()
    if issuer is not None:
        dfissuer = get_issuers(issuer)
        SQL = f"SELECT * FROM dbo.wso_loan_bank_deal WHERE {get_where_clause('wso_data_issuer_id', dfissuer['wso_data_issuer_id'].unique().tolist())} "
        df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)
    if bankdeals is not None:
        SQL = f"SELECT * FROM dbo.wso_loan_bank_deal WHERE {get_where_clause('wso_data_bank_deal_id', bankdeals)} "
        df = pd.concat([df, readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)]).drop_duplicates()
    return df


def get_facilities(facilityids=None, bankdeals=None, issuer=None):
    assert not (facilityids is None and bankdeals is None and issuer is None), \
        "need either facility ids or bank deals or issuer to search!"
    df = pd.DataFrame()
    if not (bankdeals is None and issuer is None):
        dfbankdeal = get_all_facilities(bankdeals, issuer)
        SQL = f"SELECT * FROM dbo.wso_loan_facility WHERE {get_where_clause('wso_data_bank_deal_id', dfbankdeal['wso_data_bank_deal_id'].unique().tolist())} "
        df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)
    if facilityids is not None:
        SQL = f"SELECT * FROM dbo.wso_loan_facility WHERE {get_where_clause('wso_data_facility_id', facilityids)} "
        df = pd.concat([df, readdfsql(SQL, app=LOAN_REFERENCEDATA_APP)]).drop_duplicates()
    return df


def get_amortization(facilityids):
    SQL = f"SELECT * FROM dbo.wso_loan_amortization WHERE {get_where_clause('wso_data_facility_id', facilityids)} "
    df = readdfsql(SQL, app=LOAN_REFERENCEDATA_APP).drop_duplicates()
    return df


def clean_issuers(issuer):
    dfissuer = get_issuers(issuer)
    issuerids = dfissuer['wso_data_issuer_id'].unique().tolist()
    dfbankdeal = get_bankdeals(issuer=issuerids)
    bankdeals = dfbankdeal['wso_data_bank_deal_id'].unique().tolist()

    clean_bankdeals(bankdeals)
    _clean_tables_by_ids('wso_data_issuer_id', issuerids, TABLE_KEY_MAP, app=LOAN_REFERENCEDATA_APP)


def clean_bankdeals(bankdealids):
    dffacility = get_facilities(bankdeals=bankdealids)
    facilityids = dffacility['wso_data_facility_id'].unique().tolist()

    clean_facilities(facilityids)
    _clean_tables_by_ids('wso_data_bank_deal_id', bankdealids, TABLE_KEY_MAP, app=LOAN_REFERENCEDATA_APP)


def clean_facilities(facilityids):
    dfamort = get_amortization(facilityids)
    amortids = dfamort['global_amortization_id'].unique().tolist()

    _clean_tables_by_ids('global_amortization_id', amortids, TABLE_KEY_MAP, app=LOAN_REFERENCEDATA_APP)
    _clean_tables_by_ids('wso_data_facility_id', facilityids, TABLE_KEY_MAP, app=LOAN_REFERENCEDATA_APP)


def _clean_facilitydfs(dfs_by_table):
    for table, df in dfs_by_table.items():
        id_column = TABLE_KEY_MAP[table]
        ids = df[id_column].unique().tolist()
        logging.info(f'deleting from table: {table} id columns: {id_column} ids: {len(ids)}')
        _clean_tables_by_ids(id_column, ids, {table: id_column}, app=LOAN_REFERENCEDATA_APP)


def upsert_facilitydfs(dfs_by_table, overwrite=True, usespark=False):
    if overwrite:
        _clean_facilitydfs(dfs_by_table)

    for table, df in dfs_by_table.items():
        logging.info(f'updating table: {table}, df: {df.shape}, columns: {df.columns}')
        id_column = TABLE_KEY_MAP[table]
        df = df[pd.notnull(df[id_column])]
        ids = df[id_column].unique().tolist()
        existing_ids = _get_existing_ids(table, id_column, ids, app=LOAN_REFERENCEDATA_APP)
        df = df[~df[id_column].isin(existing_ids)]
        if len(df) > 0:
            df = processdf_for_insert(df)
            if 'last_updated' in df.columns:
                df['knowledge_date'] = df['last_updated']
            if table in LOAD_DATE_TABLES:
                df['load_date'] = datetime.datetime.now()
            if usespark:
                sparkmanager.pandastosql(df, table, app=LOAN_REFERENCEDATA_APP)
            else:
                insertdf_batch(table, df, app=LOAN_REFERENCEDATA_APP)
