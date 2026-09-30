import logging
import threading
import traceback

import databricks.sql
import numpy as np
import pandas as pd
import psycopg2
import pyarrow as pa
import pyodbc
import sqlalchemy

from apo_config.config import config
from apo_config.connutils import get_jdbc_connection, get_conndict, get_snowflake_dict, get_databricks_dict, \
    get_postgres_dict
from common.utils.dbinsertutil import enlist
from common.utils.dtutil import remove_tz
from common.utils.jobutil import retrycode

_azuredb_cache = {}
# AzureDB owns one DuckDB connection per cached local/remote mode.  DuckDB
# relations are tied to the connection's active query, so concurrent Dash
# callbacks must not register tables/execute/fetch through the same instance.
# Keep the cache (for the expensive Azure/Delta setup), but serialize each
# operation against that cached connection.
_azuredb_lock = threading.RLock()


def _get_azuredb(local_dir):
    with _azuredb_lock:
        if local_dir not in _azuredb_cache:
            from data_access.spark.azuredb import AzureDB
            _azuredb_cache[local_dir] = AzureDB(local_dir=local_dir)
        return _azuredb_cache[local_dir]


def _get_conndict(app=None, add_driver=True, auto_commit=True, use_type=None):
    driver = config.getconfig(app, 'driver')
    server = config.getconfig(app, 'server')
    database_name = config.getconfig(app, 'database_name')
    authentication_type = config.getconfig(app, 'connection_type')
    user = config.getconfig(app, 'user')
    password = config.getconfig(app, 'password')
    conndict = get_conndict(driver, server, database_name, add_driver, auto_commit,
                            authentication_type, user, password, use_type)
    return conndict


def _get_postgres_dict(app=None):
    host = config.getconfig(app, 'host')
    port = config.getconfig(app, 'port')
    dbname = config.getconfig(app, 'dbname')
    user = config.getconfig(app, 'user')
    password = config.getconfig(app, 'password')
    sslmode = config.getconfig(app, 'sslmode')

    conndict = get_postgres_dict(dbname, user, password, host, port=port, sslmode=sslmode)
    return conndict


def _get_snowflake_dict(app=None, auto_commit=True):
    server = config.getconfig(app, 'server')
    database_name = config.getconfig(app, 'database_name')
    warehouse = config.getconfig(app, 'warehouse')
    okta_application = config.getconfig(app, 'application')
    password = config.getconfig(app, 'password')
    private_key = config.getconfig(app, 'private_key')

    if (okta_application is not None) and (password is None) and (private_key is None):
        user = config.getuser() + '@apollolp.com'
    else:
        user = config.getconfig(app, 'user')


    conndict = get_snowflake_dict(server, database_name, warehouse,
                                  auto_commit,
                                  okta_application,
                                  user, password, private_key)
    return conndict


def _databricks_notebook_cluster():
    assert config.getuser(actual=True) == 'root', f'can use this only on databricks notebook'
    from data_access.spark.api import sparkmanager
    sparkmanager.refresh_spark(is_databricks_env=True)
    return sparkmanager._spark.conf.get("spark.databricks.clusterUsageTags.clusterId")


def _databricks_uc_enabled():
    assert config.getuser(actual=True) == 'root', f'can use this only on databricks notebook'
    from data_access.spark.api import sparkmanager
    sparkmanager.refresh_spark(is_databricks_env=True)
    is_uc_enabled = sparkmanager._spark.conf.get("spark.databricks.unityCatalog.enabled", "false").lower() == "true"
    return is_uc_enabled


def _use_cluster_sqldw():
    return (config.getuser(actual=True) == 'root') and _databricks_uc_enabled()


def _get_databricks_dict(app=None, force_sql_dw=False):
    server = config.getconfig(app, 'databricks_host')
    if (not force_sql_dw) and _use_cluster_sqldw():
        http_path = 'sql/protocolv1/o/{}/{}' \
            .format(server.split('adb-')[-1].split('.')[0], _databricks_notebook_cluster())
    else:
        http_path = config.getconfig(app, 'databricks_http_path')

    access_token = config.getconfig(app, 'databricks_pat')
    catalog = config.getconfig(app, 'databricks_catalog')

    conndict = get_databricks_dict(server, http_path, access_token, catalog)
    return conndict


def getsqlalchemyconn(app=None):
    connstr = getsqlconnstr(app)
    import urllib
    quoted = urllib.parse.quote_plus(connstr)
    conn_executemany = sqlalchemy.create_engine('mssql+pyodbc:///?odbc_connect={}'.format(quoted),
                                                fast_executemany=True)
    return conn_executemany


def getconnstr(app=None, add_driver=True, auto_commit=True):
    conndict = _get_conndict(use_type='connection_string', app=app, add_driver=add_driver, auto_commit=auto_commit)
    connstr = ";".join([f"{k}={v}" for k, v in conndict.items()])
    logging.info(f"Created connection string for app: {app}")
    return connstr


def getsqlconnstr(app=None, add_driver=True, auto_commit=True):
    conndict = _get_conndict(app=app, add_driver=add_driver, auto_commit=auto_commit)
    connstr = ";".join([f"{k}={v}" for k, v in conndict.items()])
    logging.info(f"Created SQL connection string for app: {app}")
    return connstr


def _closeconn(conn, app):
    try:
        if not config.getconfig(app, 'driver') in ['jdbc', 'postgres', 'databricks']:
            conn.close()
    except:
        logging.warning(f'exception on closing connection:\n{traceback.format_exc()}')


def getconn(app=None):
    driver = config.getconfig(app, 'driver') or 'jdbc'
    if driver.startswith('jdbc'):
        return getjdbcconn(app)
    elif driver == 'snowflake':
        return getsnowflakeconn(app)
    elif driver == 'databricks':
        return getdatabricksconn(app)
    elif driver == 'postgres':
        return getpostgresconn(app)
    else:
        return getnonjdbcconn(app=app)


def is_databricks(app=None):
    driver = config.getconfig(app, 'driver') or 'jdbc'
    return driver in ('databricks')


def is_azuredb(app=None):
    return bool(app and (app.startswith('local') or app.startswith('remote')))


def is_snowflake(app=None):
    driver = config.getconfig(app, 'driver') or 'jdbc'
    return driver == 'snowflake'

def _read_jdbc_fallback(sql, app):
    import pymssql
    server = config.getconfig(app, 'server')
    database = config.getconfig(app, 'database_name')
    user = config.getconfig(app, 'user')
    password = config.getconfig(app, 'password')
    with pymssql.connect(server=server, database=database, user=user, password=password) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            return pd.DataFrame(cursor.fetchall(), columns=columns)


def getsnowflakeconn(app=None):
    import snowflake.connector

    snowflake_config = _get_snowflake_dict(app=app)
    logging.info(f'Opening Snowflake connection for app: {app}')
    return snowflake.connector.connect(**snowflake_config)


def getdatabricksconn(app=None):
    catalog = config.getconfig(app, 'databricks_catalog')
    schema = config.getconfig(app, 'schema') or 'dbo'
    logging.info(f'setting default catalog/schema to: {catalog}/{schema}')

    try:
        databricks_config = _get_databricks_dict(app=app)
        databricks_config.update({'catalog': catalog, 'schema': schema})
        logging.info(f'Opening Databricks connection for app: {app}')
        if _use_cluster_sqldw():
            db_conn = databricks.sql.connect(**databricks_config)
        else:
            db_conn = retrycode(lambda: databricks.sql.connect(**databricks_config), retrycount=10,
                                wait_on_error_seconds=30)
    except:
        databricks_config = _get_databricks_dict(app=app, force_sql_dw=True)
        databricks_config.update({'catalog': catalog, 'schema': schema})
        logging.info(f'Opening Databricks fallback connection for app: {app}')
        db_conn = retrycode(lambda: databricks.sql.connect(**databricks_config), retrycount=10,
                            wait_on_error_seconds=30)

    return db_conn


def getpostgresconn(app=None):
    pgres_config = _get_postgres_dict(app=app)
    logging.info(f'Opening Postgres connection for app: {app}')
    conn = psycopg2.connect(**pgres_config)
    return conn


def getjdbcconn(app=None):
    server = config.getconfig(app, 'server')
    databasename = config.getconfig(app, 'database_name')
    connection_type = config.getconfig(app, 'connection_type')
    username = config.getconfig(app, 'user')
    password = config.getconfig(app, 'password')

    return get_jdbc_connection(server, databasename, connection_type, username, password)


def getnonjdbcconn(app=None):
    dbconfig = _get_conndict(app=app)
    logging.info(f'Opening ODBC connection for app: {app}')
    return pyodbc.connect(**dbconfig)


def pa_to_pandas(patable):
    def get_new_schema(schema):
        new_fields = []
        for field in schema:
            if pa.types.is_decimal(field.type):
                new_fields.append((field.name, pa.float64()))
            else:
                new_fields.append((field.name, field.type))
        return pa.schema(new_fields)

    df = patable.cast(get_new_schema(patable.schema)).to_pandas()
    df = remove_tz(df)
    return df


def fetch_data_with_arrow(query, conn, chunk_size=10_000):
    cursor = conn.cursor()
    cursor.execute(query)

    tables = []
    while True:
        batch = cursor.fetchmany_arrow(chunk_size)
        tables.append(batch)
        if batch.num_rows == 0:
            break

    final_table = pa.concat_tables(tables, promote_options='permissive')
    final_df = pa_to_pandas(final_table)

    cursor.close()
    return final_df


def fetch_data_standard(query, conn):
    return pd.read_sql(query, conn)


def snowflake_fetch_arrow_all(query, conn):
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        final_table = cursor.fetch_arrow_all()
        final_df = pa_to_pandas(final_table)
    finally:
        cursor.close()
    return final_df


def _arrow_fetch_fn(query, conn, app, chunk_size):
    if is_snowflake(app):
        return lambda: snowflake_fetch_arrow_all(query, conn)
    if is_databricks(app):
        return lambda: fetch_data_with_arrow(query, conn, chunk_size)
    return None


def fetch_data(query, conn, app=None, chunk_size=10_000, retrycount=3, use_arrow=True):
    """Read a query into a DataFrame. For drivers with an Arrow path (databricks, snowflake)
    fetch via Arrow with retries; otherwise plain pandas read_sql. The strategy is derived
    solely from `app` (a single source of truth), so caller and callee can't disagree.
    """
    fetch_fn = _arrow_fetch_fn(query, conn, app, chunk_size) if use_arrow else None
    if fetch_fn is None:
        return fetch_data_standard(query, conn)
    try:
        return retrycode(fetch_fn, retrycount, wait_on_error_seconds=5)
    except Exception:
        logging.warning("Arrow fetch failed; falling back to standard pandas read_sql.", exc_info=True)
        return fetch_data_standard(query, conn)


def readdfsql(sql, msg=None, app=None, chunk_size=10_000, retrycount=3, use_arrow=True):
    msg = msg or ''
    if msg: msg = ' ' + str(msg)

    if is_azuredb(app):
        local_dir = 'local' if app.startswith('local') else None
        logging.info(f'Executing sql{msg} via AzureDB {"local" if local_dir else "remote"}:\n{sql}')
        with _azuredb_lock:
            azdb = _get_azuredb(local_dir)
            df = azdb.read_sql(sql)
        logging.info(f'Retrieved df shape{msg}: {df.shape}')
        return df

    logging.info(f'Executing sql{msg}:\n{sql}')
    try:
        with getconn(app) as conn:
            df = fetch_data(
                sql,
                conn,
                app=app,
                chunk_size=chunk_size,
                retrycount=retrycount,
                use_arrow=use_arrow,
            )
            _closeconn(conn, app)
    except Exception as e:
        if config.getuser(actual=True) == 'root':
            logging.warning(f'Connection failed for sql{msg}: {e}\n{traceback.format_exc()}')
            df = _read_jdbc_fallback(sql, app)
        else:
            raise

    logging.info(f'Retrieved df shape{msg}: {df.shape}')
    logging.info(f'Completed sql{msg}!')
    return df


def execsql(sql, msg=None, app=None, retrycount=3):
    if not msg is None:
        logging.info(msg)
    logging.info(f'Executing sql {msg}: {sql}')
    with getconn(app) as conn:
        with conn.cursor() as crsr:
            retrycode(lambda: crsr.execute(sql), retrycount=retrycount, wait_on_error_seconds=5)
        _closeconn(conn, app)

    logging.info(f'Completed sql {msg}!')


def execmanysql(sql, params, msg=None, app=None, maxrow_batch=4000):
    if not msg is None:
        logging.info(msg)
    logging.info(f'Executing sql {msg}: {sql}')
    rowcount = len(params)
    numbatches = int(rowcount / maxrow_batch) + 1
    npparams = np.array(params, dtype='object')
    npparams[np.where(pd.isnull(npparams))] = None
    logging.info(f'Executing sql {msg} with params shape: {npparams.shape}')
    for (i, batch) in enumerate(np.array_split(npparams, numbatches)):
        with getconn(app) as conn:
            with conn.cursor() as crsr:
                crsr.fast_executemany = True
                logging.info(f'Executing sql {msg} batch: '
                             f'{min(rowcount, (i + 1) * maxrow_batch)}/{rowcount}')
                crsr.executemany(sql, batch.tolist())
            _closeconn(conn, app)

    logging.info(f'Completed sql {msg}!')


def get_schema_table(tablename):
    schema = None
    tarr = tablename.split('.')
    if len(tarr) == 2:
        schema = tarr[0]
        tablename = tarr[1]
    return (schema, tablename)


def insertdf_tosql(tablename, df, msg=None, app=None):
    df = _cleandf(df)

    (schema, tablename) = get_schema_table(tablename)
    logging.info(f'Executing df insert to table: {tablename} {msg} for shape: {df.shape}')

    engine = getsqlalchemyconn(app)
    df = df.replace(np.nan, None)

    insertfunc = lambda: df.to_sql(tablename, engine,
                                   schema=schema,
                                   index=False,
                                   if_exists='append')
    retrycode(insertfunc, 3)


def get_turboconn(app=None):
    import turbodbc
    turboconn = turbodbc.connect(connection_string=getsqlconnstr(app))
    return turboconn


def insertdf_bcp(tablename, df, msg=None, app=None):
    df = _cleandf(df)

    (schema, tablename) = get_schema_table(tablename)
    from bcpandas import SqlCreds, to_sql
    conndict = _get_conndict(app=app)

    sampledf = readdfsql(f'select top 1 * from {schema}.{tablename} where 1 <> 1', app=app)
    df = df[sampledf.columns]

    creds = SqlCreds(conndict['server'], conndict['database'], conndict['uid'], conndict['pwd'], odbc_kwargs=conndict)
    logging.info(f'Executing df insert bcp to table: {tablename} {msg} for shape: {df.shape}')

    to_sql(df, tablename, creds, index=False, if_exists='append', schema=schema)


def insertdf_execmany(tablename, df, msg=None, app=None):
    df = _cleandf(df)
    df = _preparedf(tablename, df, app=app)
    sql = _prepareinsertsql(tablename, df, app=app)
    execmanysql(sql, df.values.tolist(), msg=msg, app=app)


INSERT_SQL = """
INSERT INTO {0}
    ({1}) 
VALUES 
    ({2})
"""


def _cleandf(df):
    df = df.dropna(how='all', axis=0)
    df = df.dropna(how='all', axis=1)
    return df


def _prepareinsertsql(tablename, df, app=None):
    columns = ",".join([f'[{x}]' for x in df.columns])
    if is_databricks(app):
        columns = sql_replace_escape_char(columns)
    questions = ",".join(['?' for x in df.columns])
    sql = INSERT_SQL.format(tablename, columns, questions)
    return sql


def _preparedf(tablename, df, app=None):
    if is_databricks(app) or is_azuredb(app):
        table_cols = [x.lower() for x in readdfsql(f'select * from {tablename} limit 1', app=app).columns.tolist()]
    else:
        table_cols = [x.lower() for x in readdfsql(f'select top 1 * from {tablename}', app=app).columns.tolist()]

    df = df[[x for x in df.columns if x.lower() in table_cols]]
    return df


def _insertdf_azuredb(tablename, df, app, cleantable=False, msg=None):
    local_dir = 'local' if app.startswith('local') else None
    _, tbl = get_schema_table(tablename)
    logging.info(f'Executing df insert via AzureDB {"local" if local_dir else "remote"}: '
                 f'{tbl} shape={df.shape}, overwrite={cleantable}, {msg or ""}')
    with _azuredb_lock:
        azdb = _get_azuredb(local_dir)
        azdb.write_table(df, tbl, overwrite=cleantable)


def insertdf_batch(tablename, df, msg=None, app=None, maxrow_batch=1000, cleantable=False):
    df = _cleandf(df)

    logging.info(f"Executing df insert batch to table: {tablename} for shape: {df.shape}, {msg or ''}")
    df = _preparedf(tablename, df, app=app)
    if is_azuredb(app):
        _insertdf_azuredb(tablename, df, app, cleantable=cleantable, msg=msg)
        return

    insertsql = _prepareinsertsql(tablename, df, app=app)
    params = df.values.tolist()
    logging.info(f'Executing sql {msg}: {insertsql}')
    npparams = np.array(params, dtype='object')
    npparams[np.where(pd.isnull(npparams))] = None

    rowcount = len(params)
    is_databricks_app = is_databricks(app)

    logging.info(f'Executing sql {msg} with params shape: {npparams.shape}')
    with getconn(app) as conn:
        if cleantable:
            DELETE_SQL = f'DELETE FROM {tablename}'
            execsql(DELETE_SQL, msg, app)
        with conn.cursor() as crsr:
            for i, data in enumerate(enlist(npparams.tolist(), maxrow_batch, is_databricks_app=is_databricks_app)):
                logging.info(f'Executing sql {msg} batch: '
                             f'{min(rowcount, (i + 1) * maxrow_batch)}/{rowcount}')
                insertstmt = insertsql.split('VALUES')[0] + data
                logging.debug(f'insert statement:\n{insertstmt}')
                crsr.execute(insertstmt)
        _closeconn(conn, app)


def script_create_table_df(table, df):
    bins = [5, 10, 15, 20, 25, 50, 100, 250, 500, 1000, 3500, 10000]
    df_defn = df.dtypes.astype(str).str.replace('object', 'varchar').str.replace('64', '') \
        .str.replace('boolean', 'bit').str.replace('bool', 'bit')
    date_types = pd.Series(df_defn.index.map(lambda x: 'datetime' if 'date_time' in x.lower()
    else ('date' if 'date' in x.lower() else np.nan)).values, index=df_defn.index)
    df_defn = date_types.combine_first(df_defn)
    dflen = df.apply(lambda x: x.dropna().astype(str).map(len).max())
    df_defn = pd.merge(df_defn.reset_index(), dflen.reset_index(), how='left', on='index')
    df_defn['0_y'] = [
        np.nan if pd.isnull(i) else bins[min(len(bins) - 1, x)] if (bins[min(len(bins) - 1, x)] - i > 10) else
        bins[min(len(bins) - 1, x + 1)] for i, x in zip(df_defn['0_y'].values, np.digitize(df_defn['0_y'], bins))]
    df_defn['0'] = df_defn['0_x'] + df_defn['0_y'].map(lambda x: f'({int(x)})' if pd.notnull(x) else '')
    df_defn = df_defn.drop(columns=['0_x', '0_y'])
    df_defn = df_defn.rename(columns={'index': f'{table}', '0': f'{table}_types'})

    df_defn[f'{table}_cols'] = None
    df_defn[f'{table}_not_null'] = None
    df_defn[f'{table}_gap'] = None
    df_defn[f'{table}_types'] = df_defn[f'{table}_types'].map(
        lambda x: x if x.startswith('varchar') else x.split('(')[0]) \
        .replace('varchar(10000)', 'varchar(max)')
    df_defn = df_defn[[f'{table}', f'{table}_cols', f'{table}_types', f'{table}_not_null', f'{table}_gap']]
    return df_defn


def script_create_table_dfs(dfs):
    dflist = []
    for k, df in dfs.items():
        logging.info(f'creating scripts for: {k} {df.shape}')
        df_defn = script_create_table_df(k, df)
        dflist.append(df_defn)
    return pd.concat(dflist, axis=1)


def sql_replace_escape_char(sql, replace_char='`', replace_char2=None):
    return sql.replace('[', replace_char).replace(']', replace_char2 or replace_char)


def sql_escape_literal(value):
    return str(value).replace("'", "''")


if __name__ == '__main__':

    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('dev')

    getsnowflakeconn('factset')
    getsnowflakeconn('snowflake_metis')
    getsnowflakeconn('snowflake_portfolio')
    getsnowflakeconn('snowflake_reference_db')
    # getsnowflakeconn('factset_password')


    sql = "Select * from dbo.etf_constituents limit 100"
    # df = readdfsql(sql, app='databricks')

    df = readdfsql('select max(asof_date) from dbo.markit_parsing_quote', app='remote')

    logging.info(df.to_string())
    logging.info(df.shape)
