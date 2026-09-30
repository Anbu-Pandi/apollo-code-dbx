import json
import logging
import os
import re
import shutil

import deltalake as dl
import duckdb
import pandas as pd
import pyarrow as pa

from apo_config.config import config
from common.utils import dfutil, jobutil, dtutil
from data_access.spark import APP
from data_access.spark.azureblob import BlobDownloader


class AzureDB:
    @staticmethod
    def clean_local(tbl, predicate):
        full_path = os.path.join(AzureDB.get_local_dir(), tbl)
        if dfutil.is_delta(full_path):
            logging.info(f'cleaning local file: {full_path}')
            dt = dl.DeltaTable(full_path)
            dt.delete(predicate=predicate)
            dfutil.optimize_table(full_path)
            dfutil.clear_delta_log(full_path)

    @staticmethod
    def writedf_local(df, tbl, overwrite, partitioncolumn=None, auto_schema_chg=True, ignore_partition=False,
                      ignore_remote_schema=False):
        delta_schema = None
        if not dfutil.is_delta(os.path.join(AzureDB.get_local_dir(), tbl)):
            remotedb = AzureDB(local_dir=None)
            if (not ignore_remote_schema) and (tbl in remotedb.tables):
                dt = remotedb.get_delta_table(tbl)
                delta_schema = dt.schema()
                if not ignore_partition:
                    partitioncolumn = dt.metadata().partition_columns
                logging.info(f'syncing new local table from remote: {AzureDB.get_local_dir()} / {tbl} '
                             f'using schema:\n{delta_schema.to_pyarrow()}')

        dfutil.pandastodelta(df, tbl, AzureDB.get_local_dir(), overwrite=overwrite, delta_schema=delta_schema,
                             partitioncolumn=partitioncolumn, auto_schema_chg=auto_schema_chg)

    @staticmethod
    def get_remote_dir():
        return config.getconfig(APP, 'azuredb_remote_dir')

    @staticmethod
    def get_local_dir():
        return config.getconfig(APP, 'azuredb_local_dir')

    def _init_using_config(self, app=None):
        app = app or APP

        self.blob_storage_name = config.getconfig(app, 'blob_storage_name').split('.')[0]
        self.container_name = config.getconfig(app, 'blob_container_name').split('.')[0]
        self.blob_dir = AzureDB.get_remote_dir()
        logging.info(f'init with: {self.blob_storage_name} {self.container_name} {self.blob_dir}')

        keys = config.getconfig(app, 'spark_conf_keys')
        sep = config.getconfig(app, 'spark_conf_sep') or ';'
        values = config.getconfig(app, 'spark_conf_values')

        for k, v in zip(keys.split(sep), values.split(sep)):
            if k.endswith('dfs.core.windows.net'):
                self.azure_key = v
                break

    def __init__(self, remote_dir=None, local_dir='local', app=None, use_config=True, **manual_configs):
        app = app or APP

        if use_config:
            self._init_using_config(app=app)

        self.blob_storage_name = manual_configs.get('blob_storage_name') or self.blob_storage_name
        self.container_name = manual_configs.get('container') or self.container_name
        self.azure_key = manual_configs.get('azure_key') or self.azure_key

        self.storage_options = {"AZURE_STORAGE_ACCOUNT_NAME": self.blob_storage_name,
                                "AZURE_STORAGE_ACCOUNT_KEY": self.azure_key, }

        self.blob_dir = remote_dir or AzureDB.get_remote_dir()

        logging.info(f'init with: {self.blob_storage_name} {self.container_name} {self.blob_dir}')

        logging.info(f'storage options initialized for account: {self.blob_storage_name}')
        self.azure_blob_fs = BlobDownloader(self.container_name, app)

        self.duck_conn = duckdb.connect(':memory:')
        self.duck_conn.sql("SET TimeZone='UTC';")

        if not local_dir:
            self.duck_conn.sql(f"""
            CREATE SECRET azsecret (
            TYPE AZURE,
            CONNECTION_STRING 'DefaultEndpointsProtocol=https;AccountName={self.blob_storage_name};AccountKey={self.azure_key};EndpointSuffix=core.windows.net'
            );
            """)

            import certifi
            os.environ['SSL_CERT_FILE'] = certifi.where()

            try:
                import platform
                if platform.system().lower() != 'windows':
                    self.duck_conn.execute("SET azure_transport_option_type  = curl")
            except:
                pass

        self.tables = []
        self.remote_tables = []
        self.local_tables = []

        self.read_path = self.remote_path
        self.local_dir = None
        if local_dir:
            if local_dir.lower() == 'local':
                local_dir = AzureDB.get_local_dir()
            self.local_dir = local_dir
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir)
            self.read_path = self.local_path
            self.sync_local_table_names()
        else:
            self.sync_remote_table_names()
        self.is_remote = self.local_dir is None

    def sync_remote_table_names(self):
        for tbl in self.azure_blob_fs.ls_dirs(self.blob_dir):
            if not tbl.startswith('tempfiles') and not tbl in self.tables:
                self.tables.append(tbl)
            if not tbl.startswith('tempfiles') and not tbl in self.local_tables:
                self.remote_tables.append(tbl)
        logging.info(f'synced remote table names: {self.remote_tables}')

    def sync_local_table_names(self):
        for tbl in os.listdir(self.local_path('')):
            if not tbl in self.tables:
                self.tables.append(tbl)
            if not tbl in self.local_tables:
                self.local_tables.append(tbl)
        logging.info(f'synced local table names: {self.local_tables}')

    def register_all_tables(self):
        for tbl in list(self.tables):
            self.register_table(tbl)

    def get_delta_table(self, table):
        try:
            return dl.DeltaTable(self.read_path(table), storage_options=self.storage_options)
        except Exception as e:
            logging.warning(f'{table}: {str(e)}')
            return

    def register_table(self, table):
        logging.info(f'registering table: {table}')
        is_table = False
        try:
            if self.is_remote:
                dt = self.duck_conn.sql(f"select * from delta_scan('{self.read_path(table)}')")
            else:
                dt = self.get_delta_table(table).to_pyarrow_dataset()
            if not dt is None:
                self.duck_conn.register(table, dt)
                is_table = True
                if table not in self.tables:
                    self.tables.append(table)
                if self.local_dir and table not in self.local_tables:
                    self.local_tables.append(table)
        except Exception as e:
            logging.error(f'failed to register table: {table} using read_path: {self.read_path(table)} {str(e)}')

        if not is_table:
            logging.warning(f'failed to register table: {table} using read_path: {self.read_path(table)}')
            self.deregister_table(table)
            if self.local_dir:
                if os.path.exists(self.local_path(table)) and len(list(os.listdir(self.local_path(table)))) == 0:
                    shutil.rmtree(self.local_path(table))

    def deregister_table(self, table):
        if table in self.tables:
            self.tables.remove(table)

        try:
            self.duck_conn.unregister(table)
        except duckdb.CatalogException:
            pass

    def remote_path(self, table):
        return f'abfss://{self.container_name}/{self.blob_dir}/{table}'

    def local_path(self, table):
        return f'{self.local_dir}/{table}'

    def remote_rel_path(self, table):
        return f'{self.blob_dir}/{table}'

    @staticmethod
    def _get_tables_from_query(sql_query):
        pattern = r'\bFROM\b\s+[\w\.]+|\bJOIN\b\s+[\w\.]+|\bUPDATE\b\s+[\w\.]+|\bINTO\b\s+[\w\.]+(?:\s+\w+\s*\(\s*\w+\s*\)\s*)?|\bDELETE\s+FROM\b\s+[\w\.]+'

        matches = re.findall(pattern, sql_query, re.IGNORECASE)

        table_names = set()
        for match in matches:
            table_name = match.split()[-1]
            table_names.add(table_name)

        return list(table_names)

    def read_sql(self, sql):
        for tbl in AzureDB._get_tables_from_query(sql.replace('"', '')):
            if '.' in tbl:
                logging.warning(f"replacing in sql: {tbl} with {tbl.lower().split('.')[-1]}")
                sql = sql.replace(tbl, tbl.lower().split('.')[-1])
            self.register_table(tbl.lower().split('.')[-1])
            if not tbl.lower().split('.')[-1] in self.tables:
                return pd.DataFrame()

        out = self.exec_sql(sql)
        df = out.df()
        logging.info(f"completed conversion to df: {df.shape}")
        df = dtutil.remove_tz(df)

        return df

    def exec_sql(self, sql):
        logging.info(f'reading sql:{sql}')
        logging.info(f"reading sql using path: {self.read_path('')}")
        out = self.duck_conn.sql(sql)
        logging.info(f"completed reading sql")
        return out

    def sync_all_tables_local(self, tables=None):
        tables = tables or self.tables
        for table in tables:
            self.sync_table_local(table)
            self.register_table(table)

    def sync_table_local(self, table_name):
        assert self.local_dir is not None, 'no local_dir set!'
        local_path = self.local_dir + '/'
        logging.info(f'syncing table: {table_name} to {local_path}')
        if os.path.exists(local_path):
            os.remove(local_path)
        remote_rel_path = self.remote_rel_path(table_name)
        self.azure_blob_fs.download(remote_rel_path + '/', local_path)

    def sync_all_tables_local_schema(self, tables=None):
        tables = tables or self.tables
        for table in tables:
            self.sync_table_local_schema(table)

    def sync_table_local_schema(self, table_name):
        if not os.path.exists(self.local_path(table_name)):
            dt2 = dl.DeltaTable(self.remote_path(table_name), storage_options=self.storage_options)
            dl.write_deltalake(self.local_path(table_name), dt2.schema().to_pyarrow().empty_table(),
                               schema=dt2.schema(),
                               configuration={"delta.logRetentionDuration": "interval 1 day"})

    def write_table(self, df, tbl, schema=None, overwrite=False):
        write_mode = 'overwrite' if overwrite else 'append'
        write_engine = 'rust'
        schema_mode = 'merge' if write_engine == 'rust' else 'overwrite'

        dl.write_deltalake(self.read_path(tbl), df, mode=write_mode, schema_mode=schema_mode, engine=write_engine,
                           storage_options=self.storage_options, schema=schema,
                           configuration={"delta.logRetentionDuration": "interval 1 day"})

        if tbl not in self.tables:
            self.tables.append(tbl)

    def add_new_cols_from_df(self, df, tbl):
        dt = self.get_delta_table(tbl)
        if dt is not None:
            source_columns = set(df.columns)
            target_columns = set([x['name'] for x in json.loads(dt.schema().to_json())['fields']])
            new_columns = list(source_columns - target_columns)

        if (dt is not None) and (len(df) > 0) and (len(new_columns) > 0):
            logging.info(f'adding new columns: {new_columns} to {tbl}')
            df = df[new_columns]
            pa_schema = pa.Schema.from_pandas(df)
            delta_schema = dl.Schema.from_pyarrow(pa_schema)

            write_df = df.head(1)
            write_df['_dummy_col'] = 1
            self.write_table(write_df.dropna(how='all', axis=1), tbl, overwrite=False)

            na_cols = list(set(write_df.columns) - set(write_df.dropna(how='all', axis=1).columns))
            if len(na_cols) > 0:
                write_df = write_df[na_cols]
                write_df['_dummy_col'] = 1
                fields = []
                for f in delta_schema.fields:
                    if (f.name in new_columns):
                        fields.append(f)
                delta_schema = dl.Schema(fields)
                write_df = dfutil._updatedf(write_df, delta_schema, keep_new_cols=True)

                self.write_table(write_df, tbl, schema=delta_schema, overwrite=False)

            logging.info(f'deleting dummy rows from {tbl}')
            dt = self.get_delta_table(tbl)
            jobutil.retrycode(lambda: dt.delete('_dummy_col = 1'), retrycount=10, wait_on_error_seconds=5)

    def merge_df(self, df, tbl, id_cols, overwrite=False):
        self.add_new_cols_from_df(df, tbl)
        dt = self.get_delta_table(tbl)
        if dt is not None:
            logging.info(f'merging df: {df.shape} with delta: {tbl} by id: {id_cols} overwrite: {overwrite}')
            dfutil.merge_df_with_delta(df, dt, id_cols, overwrite=overwrite)
        else:
            logging.info(f'writing df: {df.shape} to delta: {tbl}')
            self.write_table(df, tbl, overwrite=overwrite)

    def delete(self, tbl, predicate):
        dt = self.get_delta_table(tbl)
        if dt is not None:
            dt.delete(predicate=predicate)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('dev')  # , overrides={'azuredb_local_dir': 'c:/temp/data/tables'})

    fromdt = '2024-09-09'
    todt = '2024-09-09'
    azdb = AzureDB(local_dir=None)
    df = azdb.read_sql(f"""
    select * from cdx_option_quotes
    where vol_mark_id is not null
    and asof_date between '{fromdt}' and '{todt}'
    """)
    logging.info(df.to_string())
