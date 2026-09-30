import logging

from common.utils.envsetup import get_base_parser, init_app_using_parser
from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables import GENAI_APP
from data_access.spark.api import sparkmanager

SCHEMA = 'dbo'
BLOB_DIR = 'tables/dbo'
LOCK_TABLE = 'genai_flight_coordination_lock'
LOCK_SHARDS = 256

TABLES = {
    LOCK_TABLE: """
        shard_id INT NOT NULL,
        version BIGINT NOT NULL,
        updated_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_node': """
        node_id STRING NOT NULL,
        boot_id STRING NOT NULL,
        internal_host STRING NOT NULL,
        internal_port INT NOT NULL,
        internal_tls BOOLEAN NOT NULL,
        state STRING NOT NULL,
        active_runs INT NOT NULL,
        max_active_runs INT NOT NULL,
        heartbeat_utc TIMESTAMP NOT NULL,
        started_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_client': """
        runtime_client_id STRING NOT NULL,
        user_name STRING NOT NULL,
        device_id STRING NOT NULL,
        process_instance_id STRING NOT NULL,
        client_type STRING NOT NULL,
        client_label STRING NOT NULL,
        client_alias STRING NOT NULL,
        owner_node_id STRING NOT NULL,
        owner_boot_id STRING NOT NULL,
        generation BIGINT NOT NULL,
        status STRING NOT NULL,
        tools_json STRING NOT NULL,
        tools_hash STRING NOT NULL,
        capabilities_json STRING NOT NULL,
        last_seen_utc TIMESTAMP NOT NULL,
        updated_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_session': """
        session_id STRING NOT NULL,
        user_name STRING NOT NULL,
        runtime_client_id STRING,
        owner_node_id STRING NOT NULL,
        owner_boot_id STRING NOT NULL,
        fencing_token BIGINT NOT NULL,
        state STRING NOT NULL,
        active_request_id STRING,
        lease_until_utc TIMESTAMP NOT NULL,
        retain_until_utc TIMESTAMP NOT NULL,
        updated_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_request': """
        request_id STRING NOT NULL,
        admission_id STRING,
        session_id STRING NOT NULL,
        user_name STRING NOT NULL,
        action STRING NOT NULL,
        identity_hash STRING NOT NULL,
        owner_node_id STRING NOT NULL,
        owner_boot_id STRING NOT NULL,
        fencing_token BIGINT NOT NULL,
        state STRING NOT NULL,
        result_relative_path STRING,
        result_manifest_hash STRING,
        result_bytes BIGINT,
        result_read_lease_until_utc TIMESTAMP,
        terminal_code STRING,
        created_utc TIMESTAMP NOT NULL,
        updated_utc TIMESTAMP NOT NULL,
        expires_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_tool_call': """
        tool_call_id STRING NOT NULL,
        parent_request_id STRING NOT NULL,
        session_id STRING NOT NULL,
        session_fencing_token BIGINT NOT NULL,
        target_client_id STRING NOT NULL,
        target_generation BIGINT NOT NULL,
        tool_name STRING NOT NULL,
        identity_hash STRING NOT NULL,
        owner_node_id STRING NOT NULL,
        owner_boot_id STRING NOT NULL,
        state STRING NOT NULL,
        terminal_code STRING,
        terminal_detail STRING,
        created_utc TIMESTAMP NOT NULL,
        updated_utc TIMESTAMP NOT NULL,
        expires_utc TIMESTAMP NOT NULL
    """,
    'genai_flight_role': """
        role_name STRING NOT NULL,
        owner_node_id STRING NOT NULL,
        owner_boot_id STRING NOT NULL,
        fencing_token BIGINT NOT NULL,
        lease_until_utc TIMESTAMP NOT NULL,
        updated_utc TIMESTAMP NOT NULL
    """,
}


def qualified(table):
    return f'{SCHEMA}.{table}'


def location(table):
    return sparkmanager._getfilepath(table, BLOB_DIR)


def create_table(table):
    target = location(table)
    columns = ' '.join(TABLES[table].split())
    execsql(
        f"CREATE TABLE IF NOT EXISTS {qualified(table)} ({columns}) USING DELTA LOCATION '{target}'",
        app=GENAI_APP,
    )
    logging.info('%s ready at %s', qualified(table), target)


def verify_tables(tables):
    names = tuple(tables)
    literals = ', '.join(f"'{table}'" for table in names)
    rows = readdfsql(
        f'SELECT table_name, table_type, data_source_format, storage_path '
        f'FROM information_schema.tables '
        f"WHERE table_schema = '{SCHEMA}' AND table_name IN ({literals})",
        app=GENAI_APP,
    ).set_index('table_name')
    for table in names:
        if table not in rows.index:
            raise RuntimeError(f'{qualified(table)} was not created')
        row = rows.loc[table]
        actual = (
            str(row['table_type']).upper(),
            str(row['data_source_format']).upper(),
            str(row['storage_path']).rstrip('/'),
        )
        expected = ('EXTERNAL', 'DELTA', location(table).rstrip('/'))
        if actual != expected:
            raise RuntimeError(f'{qualified(table)} metadata invalid: expected {expected}, got {actual}')


def seed_lock_shards():
    target = qualified(LOCK_TABLE)
    execsql(
        f"INSERT INTO {target} (shard_id, version, updated_utc) "
        f"SELECT s.id, 0, current_timestamp() "
        f"FROM (SELECT explode(sequence(0, {LOCK_SHARDS - 1})) AS id) s "
        f"WHERE NOT EXISTS (SELECT 1 FROM {target} l WHERE l.shard_id = s.id)",
        app=GENAI_APP,
    )
    rows = readdfsql(
        f'SELECT count(*) AS total, count(DISTINCT shard_id) AS distinct_shards, '
        f'min(shard_id) AS min_shard, max(shard_id) AS max_shard FROM {target}',
        app=GENAI_APP,
    ).iloc[0]
    actual = tuple(int(rows[key]) for key in ('total', 'distinct_shards', 'min_shard', 'max_shard'))
    expected = (LOCK_SHARDS, LOCK_SHARDS, 0, LOCK_SHARDS - 1)
    if actual != expected:
        raise RuntimeError(f'{target} lock shards invalid: expected {expected}, got {actual}')
    logging.info('%s holds %s valid lock shards', target, LOCK_SHARDS)


def ensure_request_admission_id():
    from databricks.sql.exc import ServerOperationError

    try:
        execsql(
            f"ALTER TABLE {qualified('genai_flight_request')} ADD COLUMN admission_id STRING",
            app=GENAI_APP,
        )
    except ServerOperationError as exc:
        if "FIELD_ALREADY_EXISTS" not in str(exc):
            raise


def init_flight_tables(tables=None):
    selected = tuple(tables or TABLES)
    for table in selected:
        create_table(table)
    verify_tables(selected)
    if LOCK_TABLE in selected:
        seed_lock_shards()
    if 'genai_flight_request' in selected:
        ensure_request_admission_id()
    logging.info('flight coordination tables initialized')


def get_parser():
    parser = get_base_parser('init_flight_coordination_tables')
    parser.add_argument('--table', choices=('all',) + tuple(TABLES), default='all')
    return parser


if __name__ == '__main__':
    args = init_app_using_parser(get_parser())
    init_flight_tables(None if args.table == 'all' else [args.table])
