from datetime import datetime

import logging
import pandas as pd

from common.utils.envsetup import get_base_parser, init_app_using_parser
from common.utils.sqlutil import execsql, readdfsql
from data_access.spark.api import sparkmanager
from data_access.dbtables import GENAI_APP


def create_dummy_conversation_logs_data():
    import uuid
    dummy_data = [{
        'log_id': str(uuid.uuid4()),
        'timestamp': datetime.now(),
        'session_id': 'dummy_session',
        'source_system': 'teams_bot',
        'question': 'What is the top portfolio?',
        'sql_query': 'SELECT * FROM portfolios LIMIT 1',
        'text_response': 'Top portfolio is ABC',
        'user_feedback': 'Thanks!',
        'feedback_sentiment': 'positive',
        'feedback_timestamp': datetime.now(),
        'asof_date': datetime.now().date()
    }]
    
    df = pd.DataFrame(dummy_data)
    logging.info('✨ Created dummy genai_conversation_logs with shape: %s', df.shape)
    return df


def create_dummy_run_audit_data():
    now = datetime.now()
    df = pd.DataFrame([{
        'run_id': 'run_schema_seed',
        'timestamp': now,
        'session_id': 'dummy_session',
        'source_system': 'teams_bot',
        'route': 'general_ai',
        'channel_id': 'dummy_channel',
        'user_name': 'dummy_user',
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_write_tokens': 0,
        'estimated_cost': 0.0,
        'latency_ms': 0.0,
        'asof_date': now.date(),
    }])
    logging.info('Created dummy genai_run_audit with shape: %s', df.shape)
    return df


def _table_exists(table_name):
    existing = readdfsql(f"SHOW TABLES LIKE '{table_name}'", app=GENAI_APP)
    return existing is not None and len(existing) > 0


def _create_empty_delta_table(df, table_name, partition='asof_date', overwrite=True):
    if not overwrite and _table_exists(table_name):
        logging.info('%s already exists; leaving existing rows untouched', table_name)
        return
    sparkmanager.pandastodelta(df, table_name, 'tables/dbo', partition, overwrite=overwrite)
    location = sparkmanager._getfilepath(table_name, 'tables/dbo')
    execsql(f"CREATE TABLE IF NOT EXISTS {table_name} USING DELTA LOCATION '{location}'", app=GENAI_APP)
    execsql(f'DELETE FROM {table_name}', app=GENAI_APP)
    logging.info('%s initialized empty at %s', table_name, location)


def init_genai_conversation_logs_table(overwrite=True):
    logging.info('INITIALIZING GENAI CONVERSATION LOGS TABLE')
    _create_empty_delta_table(
        create_dummy_conversation_logs_data(),
        'genai_conversation_logs',
        partition='asof_date',
        overwrite=overwrite,
    )
    logging.info('GENAI CONVERSATION LOGS TABLE INITIALIZED')


def init_genai_run_audit_table(overwrite=True):
    logging.info('INITIALIZING GENAI RUN AUDIT TABLE')
    _create_empty_delta_table(
        create_dummy_run_audit_data(),
        'genai_run_audit',
        partition='asof_date',
        overwrite=overwrite,
    )
    logging.info('GENAI RUN AUDIT TABLE INITIALIZED')


def init_genai_tables(overwrite=True, include_conversation_logs=True, include_run_audit=True):
    sparkmanager.init()
    if include_conversation_logs:
        init_genai_conversation_logs_table(overwrite=overwrite)
    if include_run_audit:
        init_genai_run_audit_table(overwrite=overwrite)


def get_parser():
    parser = get_base_parser('init_genai_tables')
    parser.add_argument('--table', choices=['all', 'conversation_logs', 'run_audit'], default='all')
    parser.add_argument('--overwrite', action='store_true', default=False)
    return parser


if __name__ == '__main__':
    args = init_app_using_parser(get_parser())
    init_genai_tables(
        overwrite=args.overwrite,
        include_conversation_logs=args.table in ('all', 'conversation_logs'),
        include_run_audit=args.table in ('all', 'run_audit'),
    )

