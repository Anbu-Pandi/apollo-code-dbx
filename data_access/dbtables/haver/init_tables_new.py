import logging
from datetime import datetime

import pandas as pd

from apo_config.config import config
from common.utils.logutil import setuplogger
from common.utils.sqlutil import execsql
from data_access.dbtables import HAVER_APP
from data_access.dbtables.haver.init_tables import (
    create_dummy_haver_databases,
    create_dummy_haver_series_data,
    create_dummy_haver_series_metadata,
)
from data_access.spark.api import sparkmanager


def create_dummy_haver_file_manifest():
    now = datetime.now()
    return pd.DataFrame([{
        'filename': '__schema_seed__',
        'size_bytes': 0,
        'mtime': now,
        'sha256': '',
        'ingested_at': now,
        'status': 'pending',
        'error_message': '',
        'row_count': 0,
    }])


def create_dummy_haver_recon():
    now = datetime.now()
    return pd.DataFrame([{
        'run_date': now,
        'series_code': 'SCHEMA@SEED',
        'asof_date': now,
        'value_legacy': 0.0,
        'value_new': 0.0,
        'diff_abs': 0.0,
        'diff_pct': 0.0,
        'status': 'match',
    }])


def _create_empty_table(df, table, partition=None, overwrite=True):
    sparkmanager.pandastodelta(df, table, 'tables/dbo', partition, overwrite=overwrite)
    location = sparkmanager._getfilepath(table, 'tables/dbo')
    execsql(f"CREATE TABLE IF NOT EXISTS {table} USING DELTA LOCATION '{location}'", app=HAVER_APP)
    execsql(f'DELETE FROM {table}', app=HAVER_APP)
    logging.info('%s initialized empty', table)


def init_haver_tables_new(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()

    logging.info('=' * 70)
    logging.info('INITIALIZING HAVER _NEW TABLES')
    logging.info('=' * 70)

    _create_empty_table(create_dummy_haver_databases(), 'haver_databases_new', overwrite=overwrite)
    _create_empty_table(create_dummy_haver_series_data(), 'haver_series_data_new', partition='asof_date', overwrite=overwrite)
    _create_empty_table(create_dummy_haver_series_metadata(), 'haver_series_metadata_new', overwrite=overwrite)
    _create_empty_table(create_dummy_haver_file_manifest(), 'haver_file_manifest', overwrite=overwrite)
    _create_empty_table(create_dummy_haver_recon(), 'haver_recon', overwrite=overwrite)

    logging.info('=' * 70)
    logging.info('HAVER _NEW TABLES INITIALIZED SUCCESSFULLY')
    logging.info('=' * 70)


if __name__ == '__main__':
    init_haver_tables_new(overwrite=True)
