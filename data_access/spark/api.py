import base64
import datetime
import glob
import logging
import math
import os
import pickle
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import List, Tuple, Any

import certifi
import deltalake as dl
import findspark
import numpy as np
import pandas as pd
import pyarrow as pa
from pyspark.sql.functions import lit
from pyspark.sql.functions import udf, col
from pyspark.sql.types import StringType

try:
    from databricks.connect import DatabricksSession as SparkSession
    from databricks.sdk.core import Config
    from databricks.sdk import WorkspaceClient
except:
    from pyspark.sql import SparkSession

from databricksapi.Clusters import Clusters
from databricksapi.Libraries import Libraries
from delta.tables import DeltaTable
from pyspark.sql import functions as sparkfuncs
from pyspark.sql import types as sparktypes

from apo_config.config import config
from common.utils import fileutil, dtutil, dfutil
from data_access.spark import APP
from data_access.spark.azureblob import BlobDownloader
import uuid
from common.utils.jobutil import JobExecutor
from data_access.spark.azuredb import AzureDB

DBCONNECT_KEYS = {
    'host': 'DATABRICKS_ADDRESS',
    'token': 'DATABRICKS_API_TOKEN',
    'cluster_id': 'DATABRICKS_CLUSTER_ID',
}

MAX_STRING_LENGTH = 3500
WRITE_DF_CHUNK_SIZE = 1_000_000


def needs_spark_refresh(func):
    def wrapper(self, *args, **kwargs):
        self.refresh_spark()
        return func(self, *args, **kwargs)

    return wrapper


class SparkManager:
    def __init__(self):
        self.isinit = False
        self._spark = None

    def _setupdbconnect(self):
        params = {k: config.getconfig(APP, k) or config.getconfig(APP, f'spark_{k}') for k in DBCONNECT_KEYS}
        logging.info(f'databricks connect configuration: {params}')
        for (k, v) in DBCONNECT_KEYS.items():
            os.environ[v] = params[k]

        return params

    def refresh_spark(self, is_databricks_env=False):
        if is_databricks_env or not (self.isdriverlocal or self.ishostlocal):
            if self._spark is None:
                from pyspark.sql import SparkSession as SparkSession_Temp
                self._spark = SparkSession_Temp.getActiveSession()
                self._spark_conf_set(usepyarrow=True, is_databricks_env=is_databricks_env)

            return

        def _set_spark():
            spark_builder = SparkSession.builder
            if self.isdriverlocal:
                params = self._setupdbconnect()
                dbconnect_config = Config(**params)
                spark_builder = spark_builder.sdkConfig(dbconnect_config).header("next", str(uuid.uuid4()))
                logging.info(f'connecting to remote spark (databricks config)')

            if self.ishostlocal:
                assert 'SPARK_HOME_LOCAL' in os.environ, "local spark environment not set; environment variable: SPARK_HOME_LOCAL"
                assert 'JDBC_DRIVERS' in os.environ, "local spark environment not set; environment variable: JDBC_DRIVERS"
                findspark.init(os.environ['SPARK_HOME_LOCAL'])
                spark_builder = spark_builder \
                    .config('spark.driver.extraClassPath', f'{os.environ["JDBC_DRIVERS"]}/sqljdbc42.jar') \
                    .config('spark.executor.extraClassPath', f'{os.environ["JDBC_DRIVERS"]}/sqljdbc42.jar')
                logging.info(f'connecting to local spark: {os.environ["SPARK_HOME_LOCAL"]}')

            self._spark = spark_builder.getOrCreate()
            if self.isdriverlocal:
                if self.managecluster('state') in ['TERMINATING', 'TERMINATED']:
                    self.managecluster('start')
                self.waitforlibs()

        while (1):
            if self._spark is None:
                _set_spark()
                self._spark_conf_set(usepyarrow=True, is_databricks_env=False)
            try:
                tempconf = self._spark.conf.get("spark.sql.execution.arrow.pyspark.enabled")
                break
            except:
                if not self._spark is None:
                    self.close_spark()
                logging.warning(f'session lost: rebuild spark. sleeping 60 seconds')

    def close_spark(self):
        try:
            self._spark.client.close()
            self._spark.stop()
        except:
            logging.warning(f'spark stop failed.')
        self._spark = None

    def get_jdbc_url(self, app=None):
        app = app or APP
        jdbcurl = f'jdbc:sqlserver://{config.getconfig(app, "server")};databaseName={config.getconfig(app, "database_name")};'
        if config.getconfig(app, "user") is None:
            jdbcurl = f'{jdbcurl}integratedSecurity=true'
        else:
            jdbcurl = f'{jdbcurl}user={config.getconfig(app, "user")};password={config.getconfig(app, "password")}'
        logging.info(f"jdbc url set to: {jdbcurl}")
        return jdbcurl

    @needs_spark_refresh
    def get_dbutils(self):
        dbutils = None
        try:
            from pyspark.dbutils import DBUtils
            dbutils = DBUtils(self._spark)
        except:
            logging.warning(traceback.format_exc())
            try:
                import IPython
                dbutils = IPython.get_ipython().user_ns.get["dbutils"]
            except:
                logging.warning(traceback.format_exc())

        if dbutils is None:
            logging.error('dbutils could not be initialized!')

        return dbutils

    def _get_packages(self):
        folder = config.getconfig(APP, 'package_folder')
        if folder == '':
            return []
        if not Path(folder).is_absolute():
            actual_path = (Path(__file__).parent / folder).resolve()
        else:
            actual_path = Path(folder).resolve()
        return glob.glob(os.path.join(actual_path, '*.whl'))

    def init(self, start_cluster=False, waitforlibs=False, **kwargs):
        if self.isinit:
            return
        self._waitforlibs = waitforlibs
        self._spark = None
        logging.debug(f"app {APP} config values:\n" +
                      "\n\t\t".join([f"{k}={v}" for k, v in config.getappconfig(APP).items()]))

        # databricks connect client version mismatch with cluster version ignored
        os.environ['DEBUG_IGNORE_VERSION_MISMATCH'] = '1'
        os.environ['GRPC_DEFAULT_SSL_ROOTS_FILE_PATH'] = certifi.where()

        sparkhost = config.getconfig(APP, 'spark_host')
        sparkdriver = config.getconfig(APP, 'spark_driver')
        self.ishostlocal = (sparkhost is None) or ('localhost' in sparkhost.lower())
        self.isdriverlocal = (sparkdriver is None) or (sparkdriver == 'local')

        if start_cluster and self.isdriverlocal:
            self.managecluster('start')

        self.blob_dloader = BlobDownloader()

        self.isinit = True

    def getlibstatus(self):
        cluster = config.getconfig(APP, 'cluster_id')
        url = config.getconfig(APP, 'spark_host')
        token = config.getconfig(APP, 'token')
        if not self.ishostlocal:
            try:
                db_api = Libraries(url, token)
                status = db_api.clusterStatus(cluster)
                return status
            except:
                logging.warning(f"attempt failed check status of the cluster: {cluster}")
                logging.warning(traceback.format_exc())

    def waitforlibs(self):
        if not self._waitforlibs:
            return
        MAX_RETRY_COUNT = 30 * 6
        cluster = config.getconfig(APP, 'cluster_id')
        retrycount = MAX_RETRY_COUNT
        completed_status = ['INSTALLED', 'FAILED', 'UNINSTALL_ON_RESTART']
        while (retrycount > 0):
            retrycount -= 1
            status = self.getlibstatus()
            if (status is None) or \
                    (not 'library_statuses' in status) or \
                    np.all([x['status'] in completed_status for x in status['library_statuses']]):
                logging.info(f'installation of libraries completed on cluster: {cluster}')
                return
            logging.info(f'waiting for installation of libraries on cluster: {cluster}')
            time.sleep(10)

    def managecluster(self, command):
        if not self.ishostlocal:
            try:
                url = config.getconfig(APP, 'spark_host')
                token = config.getconfig(APP, 'token')
                db_api = Clusters(url, token)
                cluster_id = config.getconfig(APP, 'cluster_id')
                if command == 'start':
                    db_api.startCluster(cluster_id)
                if command == 'stop':
                    db_api.terminateCluster(cluster_id)
                if command == 'state':
                    states = {x['cluster_id']: x['state'] for x in db_api.listClusters()['clusters']}
                    return states.get(cluster_id, 'UNKNOWN')

            except:
                logging.warning(f"attempt failed to {command} cluster: {config.getconfig(APP, 'cluster_id')}")
                logging.warning(traceback.format_exc())

    def stop(self):
        self.managecluster('stop')

    def _optimize_pyarrow(self, usepyarrow):
        opt = "true" if usepyarrow else "false"
        self._spark.conf.set("spark.sql.execution.arrow.pyspark.enabled", opt)
        self._spark.conf.set("spark.sql.execution.arrow.pyspark.fallback.enabled", opt)

    def _spark_conf_using_config(self):
        sep = config.getconfig(APP, 'spark_conf_sep') or ';'
        keys = (config.getconfig(APP, 'spark_conf_keys') or '').split(sep)
        values = (config.getconfig(APP, 'spark_conf_values') or '').split(sep)
        if len(keys) != len(values):
            logging.error(f"please check config: spark_config for APP: {APP}. "
                          f"length of keys / values don' match: {len(keys)} != {len(values)}")
        else:
            for k, v in zip(keys, values):
                self._spark.conf.set(k, v)

    def _spark_conf_set(self, usepyarrow, is_databricks_env):
        self._spark.conf.set("spark.sql.session.timeZone", "Etc/UTC")
        self._spark.conf.set('spark.sql.legacy.timeParserPolicy', 'CORRECTED')
        self._spark.conf.set('spark.sql.parquet.datetimeRebaseModeInWrite', 'CORRECTED')
        self._spark.conf.set('spark.sql.parquet.datetimeRebaseModeInRead', 'CORRECTED')
        self._spark.conf.set('spark.shuffle.spillAfterRead', 'true')
        self._spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
        self._spark.conf.set("spark.databricks.delta.autoCompact.enabled", "auto")
        self._spark.conf.set('spark.sql.shuffle.numPartitions', 'auto')
        self._spark.conf.set('spark.sql.shuffle.partitions', 'auto')

        self._optimize_pyarrow(usepyarrow)
        if (not is_databricks_env) and (not self.ishostlocal):
            self._spark_conf_using_config()

    @staticmethod
    def partitiondf(sparkdf, numpartitions, partitioncolumn):
        logging.info(f"partition info: numpartition: {numpartitions}, partitioncolumn:{partitioncolumn}")
        if numpartitions > 0 and partitioncolumn is not None:
            sparkdf = sparkdf.repartition(numpartitions, partitioncolumn)
        elif numpartitions > 0:
            sparkdf = sparkdf.repartition(numpartitions)
        elif partitioncolumn is not None:
            sparkdf = sparkdf.repartition(partitioncolumn)
        return sparkdf

    def _set_synapse_options(self, spark_options, app):
        blob_storage_name = config.getconfig(app, 'blob_storage_name')
        blob_container_name = config.getconfig(app, 'blob_container_name')
        temp_dir = config.getconfig(app, 'temp_dir')
        synapse_sqldw_temp_dir = "abfss://" + blob_container_name + "@" + blob_storage_name + "/" + temp_dir

        spark_options = spark_options \
            .option("url", self.get_jdbc_url(app)) \
            .option("driver", "com.microsoft.sqlserver.jdbc.SQLServerDriver") \
            .option("tempDir", synapse_sqldw_temp_dir) \
            .option("maxStrLength", MAX_STRING_LENGTH) \
            .option("useAzureMSI", "true")

        return spark_options

    def _set_sqlserver_options(self, spark_read_options, app):
        host = config.getconfig(app, 'host')
        port = config.getconfig(app, 'port') or 1433
        username = config.getconfig(app, 'username')
        password = config.getconfig(app, 'password')
        database = config.getconfig(app, 'database')

        spark_read_options = spark_read_options \
            .option("host", host) \
            .option("port", port) \
            .option("user", username) \
            .option("password", password) \
            .option("database", database)

        return spark_read_options

    def _set_jdbc_options(self, spark_read_options, app):
        spark_read_options = spark_read_options \
            .option("url", self.get_jdbc_url(app)) \
            .option("driver", "com.microsoft.sqlserver.jdbc.SQLServerDriver")
        return spark_read_options

    def _set_snowflake_options(self, spark_read_options, app):
        host_url = config.getconfig(app, 'server')
        username = config.getconfig(app, 'user')
        password = config.getconfig(app, 'password')
        private_key = config.getconfig(app, 'private_key')
        database = config.getconfig(app, 'database_name')
        warehouse = config.getconfig(app, 'warehouse')

        if not host_url.endswith('.snowflakecomputing.com'):
            host_url = f'{host_url}.snowflakecomputing.com'

        spark_read_options = spark_read_options \
            .option("sfUrl", host_url) \
            .option("sfUser", username) \
            .option("sfDatabase", database) \
            .option("sfWarehouse", warehouse) \
            .option("autopushdown", "on")

        if private_key:
            from apo_config.connutils import _prepare_snowflake_private_key
            pk_bytes = _prepare_snowflake_private_key(private_key)
            spark_read_options = spark_read_options.option("pem_private_key", base64.b64encode(pk_bytes).decode())
        elif password:
            spark_read_options = spark_read_options.option("sfPassword", password)

        return spark_read_options

    def _set_databricks_options(self, spark_read_options, app):
        host_url = config.getconfig(app, 'databricks_host')
        http_path = config.getconfig(app, 'databricks_http_path')
        pat = config.getconfig(app, 'databricks_pat')
        catalog = config.getconfig(app, 'databricks_catalog')

        spark_read_options = spark_read_options \
            .option("host", host_url) \
            .option("httpPath", http_path) \
            .option("personalAccessToken", pat) \
            .option("catalog", catalog)

        return spark_read_options

    def _set_databricks_catalog_schema(self, app=None):
        app = app or APP
        catalog = config.getconfig(app, 'databricks_catalog')
        schema = config.getconfig(app, 'schema') or 'dbo'

        logging.info(f'setting default catalog/schema to: {catalog}/{schema}')

        if catalog:
            self._spark.sql(f'USE CATALOG {catalog}')
        self._spark.sql(f'USE SCHEMA {schema}')

        logging.info(f'completed setting default catalog/schema to: {catalog}/{schema}')

    def _readsql_databricks(self, query, app=None):
        self._set_databricks_catalog_schema(app)
        return self._spark.sql(query)

    @needs_spark_refresh
    def readsql(self, query, numpartitions=-1, partitioncolumn=None, lowerbound=None, upperbound=None, app=None):

        app = app or APP
        logging.info(f"running spark query: \n{query}")

        sparkreadformat = config.getconfig(app, 'spark_read_format') or 'jdbc'
        logging.info(f"using spark read format: {sparkreadformat}")
        assert sparkreadformat in ['jdbc', 'com.databricks.spark.sqldw', 'databricks', 'snowflake', 'sqlserver',
                                   'databricks_jdbc', ]

        spark_read_options = self._spark.read.format(sparkreadformat).option("query", query)

        if sparkreadformat == 'databricks':
            return self._readsql_databricks(query, app)

        if sparkreadformat == 'jdbc':
            spark_read_options = self._set_jdbc_options(spark_read_options, app)

        elif sparkreadformat == 'com.databricks.spark.sqldw':
            spark_read_options = self._set_synapse_options(spark_read_options, app)

        elif sparkreadformat == 'snowflake':
            spark_read_options = self._set_snowflake_options(spark_read_options, app)

        elif sparkreadformat == 'databricks_jdbc':
            spark_read_options = self._set_databricks_options(spark_read_options, app)

        elif sparkreadformat == 'sqlserver':
            spark_read_options = self._set_sqlserver_options(spark_read_options, app)

        if numpartitions > 0:
            spark_read_options = spark_read_options.option("numPartitions", numpartitions)

        if not (partitioncolumn is None or lowerbound is None or upperbound is None):
            spark_read_options = spark_read_options.option("partitionColumn", partitioncolumn)
            spark_read_options = spark_read_options.option("lowerBound", lowerbound)
            spark_read_options = spark_read_options.option("upperBound", upperbound)

        sparkdf = spark_read_options.load()
        sparkdf = self.partitiondf(sparkdf, numpartitions, partitioncolumn)
        logging.info(f"completed spark query")
        return sparkdf

    def _getdfschema(self, tablename, app=None):
        app = app or APP
        if config.getconfig(app, 'spark_read_format') == 'databricks':
            schema = self.readsql(f'select * from {tablename} limit 1', app=app).schema
        else:
            schema = self.readsql(f'select top 1 * from {tablename}', app=app).schema
        return schema

    @staticmethod
    def pandas_dtype_to_spark_dtype(pandas_dtype):
        mapping = {
            'int32': sparktypes.IntegerType(),
            'int': sparktypes.IntegerType(),
            'int64': sparktypes.LongType(),
            'float32': sparktypes.FloatType(),
            'float': sparktypes.FloatType(),
            'float64': sparktypes.DoubleType(),
            'string': sparktypes.StringType(),
            '': sparktypes.StringType(),
            'object': sparktypes.StringType(),
            'bool': sparktypes.BooleanType(),
            'date': sparktypes.DateType(),
            'datetime': sparktypes.TimestampType(),
            'datetime64[ns]': sparktypes.TimestampType(),
            'datetime64[us]': sparktypes.TimestampType(),
            'timedelta[ns]': sparktypes.LongType()  # Not a perfect mapping
        }
        return mapping.get(str(pandas_dtype), sparktypes.StringType())

    @staticmethod
    def _updateschema(df, schema, strict, keep_new_cols, new_schema):
        sfields = []
        if (schema is None):
            for col in df.columns:
                if col in (new_schema or {}):
                    sfields += [sparktypes.StructField(col,
                                                       SparkManager.pandas_dtype_to_spark_dtype(new_schema[col]),
                                                       True)]
        else:
            for sfield in schema.fields:
                if sfield.name in df.columns:
                    if sfield.name in (new_schema or {}):
                        sfields += [sparktypes.StructField(sfield.name,
                                                           SparkManager.pandas_dtype_to_spark_dtype(
                                                               new_schema[sfield.name]),
                                                           True)]
                    elif (not strict) and sfield.dataType in (sparktypes.TimestampType(), sparktypes.DateType()):
                        sfields += [sparktypes.StructField(sfield.name, sparktypes.StringType(), sfield.nullable)]
                    else:
                        sfields += [sfield]

            if keep_new_cols:
                for col in df.columns:
                    if col not in [x.name for x in schema.fields]:
                        if col in (new_schema or {}):
                            sfields += [sparktypes.StructField(col,
                                                               SparkManager.pandas_dtype_to_spark_dtype(
                                                                   new_schema[col]),
                                                               True)]
                        elif df.loc[:, col].isna().all():
                            sfields += [sparktypes.StructField(col, sparktypes.StringType(), True)]
                        else:
                            sfields += [sparktypes.StructField(col,
                                                               SparkManager.pandas_dtype_to_spark_dtype(
                                                                   df.loc[:, col].dtype),
                                                               sfield.nullable)]

        if len(sfields) > 0:
            retschema = sparktypes.StructType([SparkManager._get_schema_field_by_name(x, sfields) for x in df.columns if
                                               SparkManager._get_schema_field_by_name(x,
                                                                                      sfields) and SparkManager._is_valid_col_name(
                                                   x)])
            return retschema

    @staticmethod
    def _updatedf_dtypes(df):
        num_cols = df.select_dtypes(include=['number', 'bool']).columns
        date_cols = df.select_dtypes(include=['datetime']).columns
        str_cols = df.select_dtypes(exclude=['number', 'bool', 'datetime']).columns
        for c in num_cols:
            df.loc[:, c] = pd.to_numeric(df.loc[:, c], errors='coerce')
        for c in date_cols:
            df.loc[:, c] = pd.to_datetime(df.loc[:, c], errors='coerce', format='mixed')
        for c in str_cols:
            df.loc[:, c] = df.loc[:, c].fillna(np.nan).astype(str).replace('nan', None)
        return df

    @staticmethod
    def _get_schema_field_by_name(col_name, schema_fields):
        field_for_col = [x for x in schema_fields if x.name == col_name]
        if len(field_for_col) == 1:
            return field_for_col[0]
        logging.warning(f'schema mismatch {col_name}: {len(field_for_col)} matches in schema')

    @staticmethod
    def _updatedf(df, schema, strict, keep_new_cols):
        df = SparkManager._updatedf_dtypes(df)
        if schema is None:
            return df

        sel_cols = []
        for col in df.columns:
            sfield = SparkManager._get_schema_field_by_name(col, schema.fields)
            if sfield is None:
                if keep_new_cols:
                    sel_cols += [col]
            else:
                try:
                    sel_cols += [col]
                    if df.loc[:, col].dtype == 'object':
                        df.loc[:, col] = df.loc[:, col].replace(' ', np.nan)
                    if sfield.dataType in (sparktypes.IntegerType(),):
                        df.loc[:, col] = np.floor(pd.to_numeric(df.loc[:, col], errors='coerce')).astype('Int64')
                    elif sfield.dataType in (sparktypes.BooleanType(),):
                        df.loc[:, col] = np.floor(pd.to_numeric(df.loc[:, col], errors='coerce')).astype(
                            'Int64').astype('boolean')
                    elif sfield.dataType in (sparktypes.DoubleType(), sparktypes.FloatType()):
                        df.loc[:, col] = pd.to_numeric(df.loc[:, col], errors='coerce').astype(float)
                    elif sfield.dataType in (sparktypes.DateType(),):
                        df.loc[:, col] = pd.to_datetime(df.loc[:, col], errors='coerce', format='mixed').dt.date
                        if not strict:
                            df.loc[:, col] = df.loc[:, col].map(
                                lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None)
                    elif sfield.dataType in (sparktypes.TimestampType(),):
                        df.loc[:, col] = pd.to_datetime(df.loc[:, col], errors='coerce', format='mixed')
                        if not strict:
                            df.loc[:, col] = df.loc[:, col].map(
                                lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) else None)
                    elif sfield.dataType in (sparktypes.StringType(),):
                        df.loc[:, col] = np.where(pd.isnull(df.loc[:, col]), df.loc[:, col],
                                                  df.loc[:, col].astype(str).str.slice(0, MAX_STRING_LENGTH))

                except Exception as e:
                    raise Exception(f"error converting column: {col} to "
                                    f"datatype: {sfield.dataType} original: {df.loc[:, col].dtype}") \
                        from e

        df = df[sel_cols]
        for field in schema.fields:
            if field.name not in df.columns and field.nullable:
                df[field.name] = None

        cols_order = [x.name for x in schema.fields]
        if keep_new_cols:
            cols_order += [x for x in sel_cols if x not in cols_order]
        df = df[[x for x in cols_order if x in df.columns]]
        return df

    @staticmethod
    def _is_valid_col_name(x):
        return not (x.lower().startswith('unnamed') or x.startswith('__'))

    def _prepare_for_pandastospark(self, df, table, schema, app, new_schema):
        app = app or APP
        strict = True
        keep_new_cols = (new_schema is not None) or ((table is None) and (schema is not None))
        if (schema is None) and (not table is None):
            schema = self._getdfschema(table, app=app)

        df = SparkManager._updatedf(df, schema, strict, keep_new_cols)
        schema = SparkManager._updateschema(df, schema, strict, keep_new_cols, new_schema)

        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.replace({np.nan: None})
        df = df[[x for x in df.columns if SparkManager._is_valid_col_name(x)]]

        return df, schema

    @staticmethod
    def cast_to_schema(spark_df, schema):
        if schema is None:
            return spark_df
        schema_dict = {field.name: field.dataType for field in schema.fields}

        casted_columns = [sparkfuncs.col(column).cast(schema_dict[column]).alias(column)
                          if column in schema_dict else sparkfuncs.col(column)
                          for column in spark_df.columns
                          if SparkManager._is_valid_col_name(column)]
        new_df = spark_df.select(*casted_columns)
        return new_df

    @needs_spark_refresh
    def pandastospark(self, df, table=None, schema=None, app=None, use_blob=True, new_schema=None):
        logging.info(f'starting pandastospark: {df.shape} {table} {schema}')
        app = app or APP

        df, schema = self._prepare_for_pandastospark(df, table, schema, app, new_schema)

        if not use_blob:
            if not schema is None:
                for x in schema.fields:
                    if x.name not in df.columns:
                        df[x.name] = None
            sparkdf = self._spark.createDataFrame(df)
        else:
            sparkdf = self._create_dataframe_using_blob(df)

        sparkdf = sparkdf.replace(float('nan'), None)
        sparkdf = self.cast_to_schema(sparkdf, schema=schema)
        logging.info(f'completed pandastospark: {df.shape} {table} {schema}')
        return sparkdf

    def _create_dataframe_using_blob(self, df):
        logging.info(f'creating spark df using blob: {df.shape}')

        blob_dir = f'data/_cache/_pandastoblob/{dtutil.today()}'
        with fileutil.TempFolder() as tempdir:
            parq_file = 'pandastospark'
            pa_schema = dfutil.schema_from_pyarrow(pa.Schema.from_pandas(df))
            dl.write_deltalake(f'{tempdir}/{parq_file}', df, schema=pa_schema.to_pyarrow())
            self.blob_dloader.upload_dir(tempdir, blob_dir)
            sparkdf = self.readfile(parq_file, blob_dir + '/' + os.path.split(tempdir)[-1], type='delta')

        logging.info(f'completed creating spark df using blob: {df.shape}')
        return sparkdf

    def pandastosql(self, df, table, app=None):
        app = app or APP

        logging.info(f"writing df to table: {table}, size: {df.shape}")
        for i, _df in enumerate(dfutil.chunks_df(df, WRITE_DF_CHUNK_SIZE)):
            logging.info(f"writing {i}th chunk of size: {WRITE_DF_CHUNK_SIZE} of df to "
                         f"table: {table}, chunk size: {_df.shape} / {df.shape}")

            sparkdf = self.pandastospark(_df, table, app=app)
            self.writetosql(sparkdf, table, app)

    def pandastodelta_batches(self, batches, table, blob_dir, partitioncolumn=None, overwrite=True):
        schema = None
        combined = None
        rows = 0
        for batch in batches:
            if batch.empty:
                continue
            if combined is None and self.fileexists(table, blob_dir):
                schema = self.readfile(table, blob_dir, type='delta').schema
            rows += len(batch)
            staged = self.pandastospark(batch, schema=schema)
            combined = staged if combined is None else combined.unionByName(staged, allowMissingColumns=True)
            del batch, staged
        if combined is not None:
            if partitioncolumn is not None:
                combined = combined.repartition(partitioncolumn)
            self.writefile(combined, table, blob_dir, partitioncolumn=partitioncolumn,
                           overwrite=overwrite, append=not overwrite, type='delta')
        return rows

    def pandastodelta(self, df, table, blob_dir, partitioncolumn=None, overwrite=True, new_schema=None):
        if len(df) == 0:
            logging.warning(f'skipping writing df due to zero length: {df.shape}')
            return

        logging.info(f"writing df to delta file: "
                     f"{blob_dir}/{table}, size: {df.shape} params: {overwrite}")

        curr_file_exist = self.fileexists(table, blob_dir)

        schema = None
        if curr_file_exist:
            curr_sparkdf = self.readfile(table, blob_dir, type='delta')
            schema = curr_sparkdf.schema

        logging.info(f'writing df: {df.shape}')
        for i, _df in enumerate(dfutil.chunks_df(df, WRITE_DF_CHUNK_SIZE)):
            logging.info(
                f"writing {i}th chunk of size: {WRITE_DF_CHUNK_SIZE} of df to table: {table}, chunk size: {_df.shape} / {df.shape}")
            sparkdf = self.pandastospark(_df, schema=schema, new_schema=new_schema)
            if partitioncolumn is not None:
                sparkdf = sparkdf.repartition(partitioncolumn)
            self.writefile(sparkdf, table, blob_dir,
                           partitioncolumn=partitioncolumn, overwrite=overwrite, append=not overwrite,
                           type='delta')
            overwrite = False

    def _writetosql_databricks(self, sparkdf, table, app):
        self._set_databricks_catalog_schema(app)
        partitioncol = DeltaTable.forName(self._spark, table).detail().select('partitionColumns').collect()[0][0]
        logging.info(f'using partition col info to write: {table} partitioned by: {partitioncol}')
        sparkdf.write.format('delta') \
            .partitionBy(partitioncol) \
            .mode("append") \
            .saveAsTable(table)

    @needs_spark_refresh
    def writetosql(self, sparkdf, table, app=None):

        app = app or APP
        sparkreadformat = config.getconfig(app, 'spark_read_format') or 'jdbc'
        logging.info(f"writing spark df to table: {table} using: {sparkreadformat}")

        schema = self._getdfschema(table, app)
        drop_auto_id_col = [x.name for x in schema.fields
                            if (x.name not in sparkdf.columns) and (schema.fields[0].name == x.name) and (
                                x.name.endswith('_id'))]
        sparkdf = sparkdf.select(*[sparkfuncs.col(x.name).cast(x.dataType).alias(x.name)
                                   if (x.name in sparkdf.columns) else
                                   sparkfuncs.lit(None).cast(x.dataType).alias(x.name)
                                   for x in schema.fields
                                   if x.name not in drop_auto_id_col])

        if sparkreadformat == 'databricks':
            self._writetosql_databricks(sparkdf, table, app)

        else:
            sparkoptions = sparkdf.write.format(sparkreadformat) \
                .option("dbTable", table)

            if sparkreadformat == 'jdbc':
                sparkoptions = self._set_jdbc_options(sparkoptions, app)

            elif sparkreadformat == 'com.databricks.spark.sqldw':
                sparkoptions = self._set_synapse_options(sparkoptions, app)

            elif sparkreadformat == 'snowflake':
                sparkoptions = self._set_snowflake_options(sparkoptions, app)

            elif sparkreadformat == 'databricks_jdbc':
                sparkoptions = self._set_databricks_options(sparkoptions, app)

            elif sparkreadformat == 'sqlserver':
                sparkoptions = self._set_sqlserver_options(sparkoptions, app)

            sparkoptions.mode("append").save()

        logging.info(f"write completed spark df to table: {table}")

    @needs_spark_refresh
    def submitjob(self, func, params: List[Tuple], initconfig: bool = False, params_per_task: int = 1, **funcargs) -> \
            List[Any]:
        func_name = getattr(func, '__name__', str(func))
        job_start_time = time.time()

        logging.info(f"Starting Spark job submission for function: {func_name}")
        logging.info(
            f"Job parameters - Function: {func_name}, Param count: {len(params)}, Params per task: {params_per_task}, Init config: {initconfig}")
        logging.info(f"Additional function args: {list(funcargs.keys()) if funcargs else 'None'}")

        config_for_workers = None
        if initconfig:
            logging.info("Preparing configuration data for workers")
            config_data = {
                'configdict': config._CONFIGDICT,
                'env': config.getenv()
            }
            config_for_workers = base64.b64encode(pickle.dumps(config_data)).decode()
            logging.info(f"Configuration serialized for workers (size: {len(config_for_workers)} chars)")

        def call_func_i(func_i, paramdict, config_data_str, **kw_funcargs):
            def _func():
                from common.utils.logutil import setuplogger
                setuplogger(logconsole=True)

                if config_data_str:
                    config_obj = pickle.loads(base64.b64decode(config_data_str))
                    from apo_config.config import config
                    config.init(config_obj['env'], configdict=config_obj['configdict'])

                return func_i(*paramdict, **kw_funcargs)

            return JobExecutor.execute_func_in_error_block(_func)

        @udf(returnType=StringType())
        def execute_on_executor(serialized_param, config_param):
            param = pickle.loads(base64.b64decode(serialized_param))
            result = call_func_i(func, param, config_param, **funcargs)
            return base64.b64encode(pickle.dumps(result)).decode()

        logging.info("Serializing parameters for Spark job with row indices")
        # Create a list of tuples (param_id, serialized_param)
        serialized_params = [
            (idx, base64.b64encode(pickle.dumps(p)).decode())
            for idx, p in enumerate(params)
        ]
        logging.info(f"Parameters serialized successfully (count: {len(serialized_params)})")

        logging.info("Creating Spark DataFrame from parameters with param_id")
        df = self._spark.createDataFrame(serialized_params, ["param_id", "param"])

        num_tasks = math.ceil(len(params) / params_per_task)
        logging.info(f"Creating {num_tasks} tasks ({params_per_task} params per task)")

        logging.info(f"Repartitioning DataFrame to create {num_tasks} partitions by param_id")
        df = df.repartition(num_tasks, col("param_id"))

        job_tags = [
            f"function_{func_name}",
            f"param_count_{len(params)}",
            f"params_per_task_{params_per_task}",
            f"num_tasks_{num_tasks}",
            "submitjob_execution"
        ]

        if funcargs:
            job_tags.append("with_funcargs")
            for arg_name in funcargs.keys():
                job_tags.append(f"arg_{arg_name}")

        if initconfig:
            job_tags.append("with_config")

        logging.info(f"Job tags for monitoring: {job_tags}")

        job_group_id = f"submitjob_{func_name}_{int(job_start_time)}"
        job_description = f"Spark execution of {func_name} with {len(params)} parameters in {num_tasks} tasks"

        logging.info(f"Job identifier: {job_group_id}")
        logging.info(f"Job description: {job_description}")

        logging.info("Executing Spark transformation and starting job")
        execution_start_time = time.time()

        out = df.select(
            col("param_id"),
            execute_on_executor(col("param"), lit(config_for_workers)).alias("result")
        )

        logging.info("Collecting results from Spark executors")

        blob_dir = f'data/_cache/_submitjob_results/{dtutil.today()}'
        temp_file_id = str(uuid.uuid4()).replace('-', '')
        self.writefile(out, temp_file_id, blob_dir, type='delta', overwrite=True)

        db = AzureDB(remote_dir=blob_dir, local_dir=None)
        if temp_file_id[0].isalpha():
            outdf = db.read_sql(f'select * from {temp_file_id} ORDER BY param_id')
        else:
            outdf = db.read_sql(f'select * from "{temp_file_id}" ORDER BY param_id')
        self.blob_dloader.delete_dir(f'{blob_dir}/{temp_file_id}')
        del db

        res = []
        for i, _r in outdf.iterrows():
            result_obj = pickle.loads(base64.b64decode(_r['result']))
            res.append(result_obj)
            if (i + 1) % 100 == 0:
                logging.info(f"Processed {i + 1}/{len(params)} results")

        execution_end_time = time.time()
        execution_duration = execution_end_time - execution_start_time

        success_count = sum(1 for r in res if r.get('error') is None)
        failure_count = len(res) - success_count

        logging.info(f"Spark job execution completed in {execution_duration:.2f} seconds")
        logging.info(f"Job results - Total: {len(res)}, Successful: {success_count}, Failed: {failure_count}")
        logging.info(f"Success rate: {(success_count / len(res) * 100):.1f}%")

        if failure_count > 0:
            logging.warning(f"Job had {failure_count} failures out of {len(res)} total tasks")
            error_samples = [r.get('error') for r in res if r.get('error') is not None][:3]
            for i, error in enumerate(error_samples):
                logging.error(f"Sample error {i + 1}: {error}")

        total_duration = time.time() - job_start_time
        logging.info(
            f"Total submitjob duration: {total_duration:.2f} seconds (including setup: {execution_start_time - job_start_time:.2f}s)")
        logging.info(f"Returning results for function: {func_name}")

        return res

    def merge_local_file(self, lfile, filename, dirname, partitioncolumn=None, type='parquet',
                         next_cloud_write_time=None, overwrite=True, align_existing_schema=False):
        curr_dttime = datetime.datetime.now()
        if (next_cloud_write_time is not None) and (curr_dttime <= next_cloud_write_time):
            logging.info(f'skipping merge local file: {lfile} to {dirname}. partition: {partitioncolumn}.'
                         f'next write time: {next_cloud_write_time}')
            return False

        logging.info(f'starting merge local file: {lfile} to {dirname}. partition: {partitioncolumn}.'
                     f'next write time: {next_cloud_write_time}')

        lfilename = os.path.split(lfile)[-1]
        self.blob_dloader.delete_dir(f'{dirname}/tempfiles/{lfilename}/')
        self.blob_dloader.upload_dir(lfile, f'{dirname}/tempfiles/')
        localdf = self.readfile(lfilename, f'{dirname}/tempfiles/', type=type)
        overwrite = (partitioncolumn is not None) and overwrite

        if align_existing_schema:
            targetdf = self.readfile(filename, dirname, type=type)
            if targetdf is not None:
                target_types = {field.name: field.dataType for field in targetdf.schema.fields}
                for field in localdf.schema.fields:
                    target_type = target_types.get(field.name)
                    if target_type is not None and field.dataType != target_type:
                        logging.info('casting %s from %s to existing %s', field.name, field.dataType, target_type)
                        localdf = localdf.withColumn(field.name, sparkfuncs.col(field.name).cast(target_type))

        logging.info(f'writing remote for merge local file: {lfile} to {dirname}')
        self.writefile(localdf, filename, dirname, partitioncolumn,
                       overwrite=overwrite, append=not overwrite, type=type)

        if type == 'delta':
            dlfile = dl.DeltaTable(lfile)
            if overwrite:
                max_val = localdf.agg(sparkfuncs.max(partitioncolumn)).collect()[0][0]
                if max_val is not None:
                    logging.info(f'pruning: {lfile} by {partitioncolumn} before: {max_val}')
                    dlfile.delete(predicate=f"cast({partitioncolumn} as string) < '{max_val}'")
            else:
                logging.info(f'pruning: {lfile} by {partitioncolumn}')
                dlfile.delete()
            dlfile.vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
            dlfile.optimize.compact()
        else:
            shutil.rmtree(lfile)

        logging.info(f'merge local file successful: {lfile} to {dirname}')
        return True

    def writefile(self, df, filename, dirname, partitioncolumn=None, overwrite=False, append=True, type='parquet'):
        fullpath = self._getfilepath(filename, dirname)
        logging.info(f"writing file: {df}")
        assert overwrite or append or not self.fileexists(filename, dirname), \
            f'file exists! please use overwrite=True or append=True: {fullpath}'

        logging.info(f"writing file type: {type}, file: {fullpath}")
        dfwriter = df.write.format(type)

        if overwrite:
            logging.info(f"write file mode overwrite: {fullpath}")
            dfwriter = dfwriter.mode("overwrite")
        elif append:
            logging.info(f"write file mode append: {fullpath}")
            dfwriter = dfwriter.mode("append")

        if not partitioncolumn is None:
            logging.info(f"writing partitioned: {partitioncolumn}")
            dfwriter = dfwriter.partitionBy(partitioncolumn)

        if type == 'csv':
            dfwriter = dfwriter.option('header', 'true')
        elif type == 'delta':
            dfwriter = dfwriter.option('mergeSchema', 'true')
        dfwriter.option("timestampFormat", "yyyy-MM-dd'T'HH:mm:ss.SSS").save(fullpath)

        logging.info(f"writing file completed type: {type}, file: {fullpath}")

    def convert_to_delta(self, filename, dirname=''):
        fullpath = self._getfilepath(filename, dirname)
        delta_table = DeltaTable.convertToDelta(self._spark, f"parquet.`{fullpath}`")
        return delta_table

    @staticmethod
    def adjust_datetime_cols(spark_df):
        timestamp_cols = []
        for column in spark_df.schema:
            if column.dataType == sparktypes.TimestampType():
                timestamp_cols.append(column.name)
                spark_df = spark_df.withColumn(column.name, sparkfuncs.col(column.name).astype('string'))
        return spark_df

    @staticmethod
    def convert_to_pandas(spark_df):
        timestamp_cols = [x for x in spark_df.schema if x.dataType == sparktypes.TimestampType()]
        spark_df = SparkManager.adjust_datetime_cols(spark_df)
        pandas_df = spark_df.toPandas()
        for column_header in timestamp_cols:
            pandas_df[column_header.name] = pd.to_datetime(pandas_df[column_header.name],
                                                           format='mixed')

        return pandas_df

    def listfiles(self, dirname):
        return sorted(self.blob_dloader.ls_files(dirname))

    @needs_spark_refresh
    def readfile(self, filename, dirname, type='parquet'):

        fullpath = self._getfilepath(filename, dirname)
        logging.info(f"reading type: {type}, file: {fullpath}")
        if self.fileexists(filename, dirname):
            dfreader = self._spark.read.format(type).option("timestampFormat", "yyyy-MM-dd'T'HH:mm:ss.SSS")
            if type == 'csv':
                dfreader = dfreader.option('header', 'true') \
                    .option('inferSchema', 'true') \
                    .option('multiLine', 'true').csv(fullpath)
            return dfreader.load(fullpath)

        logging.info(f"file not read; doesn't exist: {fullpath}")

    def readfile_pandas(self, filename, dirname, type='parquet'):
        fullpath = self._getfilepath(filename, dirname)
        blobpath = "/".join(Path(fullpath).parts[2:])
        dload_dir = tempfile.mkdtemp()
        logging.info(f'downloading blob to dest: {dload_dir}, source: {blobpath}')
        self.blob_dloader.download(blobpath, dload_dir + '/')
        df = None
        logging.info(f'reading file {type} as pandas: {dload_dir}')
        if type == 'parquet':
            df = pd.read_parquet(dload_dir)
        elif type == 'csv':
            df = pd.read_csv(dload_dir)
        else:
            logging.info(f"file not read; doesn't exist: {fullpath}")

        shutil.rmtree(dload_dir)
        return df

    def fileexists(self, filename, dirname=''):
        return self.blob_dloader.blob_exists(f'{dirname}/{filename}')

    def _getfilepath(self, filename, dirname='', app=APP):
        if '@' in filename:
            return filename

        dirname = dirname or ''
        if not '@' in dirname:
            blob_storage_name = config.getconfig(app, 'blob_storage_name')
            blob_container_name = config.getconfig(app, 'blob_container_name')
            dirname = "abfss://" + blob_container_name + "@" + blob_storage_name + \
                      (("/" + dirname) if dirname != '' else '')

        return f"{dirname}/{filename}"

    @needs_spark_refresh
    def create_spark_df(self, df, **kwargs):
        return self._spark.createDataFrame(df, **kwargs)

    @staticmethod
    def _get_client(app=APP):
        return WorkspaceClient(
            host=config.getconfig(app, 'databricks_host'),
            token=config.getconfig(app, 'databricks_pat')
        )

    @staticmethod
    def _process_job_ids(job_ids, app=APP):
        if not isinstance(job_ids, list):
            return [config.getconfig(app, job_ids) or job_ids]
        return [config.getconfig(app, jid) or jid for jid in job_ids]

    @staticmethod
    def start_workflow_job(job_ids, app=APP, **kwargs):
        try:
            client = SparkManager._get_client(app)
            processed_ids = SparkManager._process_job_ids(job_ids, app)

            results = {}
            for jid in processed_ids:
                run = client.jobs.run_now(job_id=jid, **kwargs)
                logging.info(f"started databricks workflow job: {jid} with run_id: {run.run_id}")
                results[jid] = run.run_id

            return results if isinstance(job_ids, list) else results[processed_ids[0]]
        except:
            logging.error(f"error starting databricks workflow job: {job_ids}")
            logging.error(traceback.format_exc())
            return {} if isinstance(job_ids, list) else None

    @staticmethod
    def get_active_runs_for_job(job_ids, app=APP):
        try:
            client = SparkManager._get_client(app)
            processed_ids = SparkManager._process_job_ids(job_ids, app)

            results = {}
            for jid in processed_ids:
                runs = list(client.jobs.list_runs(job_id=jid, active_only=True))
                active_run_ids = [run.run_id for run in runs]
                logging.info(f"found {len(active_run_ids)} active runs for job: {jid}")
                results[jid] = active_run_ids

            return results if isinstance(job_ids, list) else results[processed_ids[0]]
        except:
            logging.error(f"error getting active runs for job: {job_ids}")
            logging.error(traceback.format_exc())
            return {} if isinstance(job_ids, list) else []

    @staticmethod
    def get_workflow_job_status(job_ids, app=APP):
        try:
            client = SparkManager._get_client(app)
            processed_ids = SparkManager._process_job_ids(job_ids, app)

            results = {}
            for jid in processed_ids:
                runs_list = list(client.jobs.list_runs(job_id=jid, limit=1))

                if not runs_list:
                    logging.warning(f"no runs found for job: {jid}")
                    results[jid] = None
                    continue

                run_details = runs_list[0]
                life_cycle_state = run_details.state.life_cycle_state
                logging.info(f"job id: {jid}, run_id: {run_details.run_id}, status: {life_cycle_state}")
                results[jid] = life_cycle_state

            return results if isinstance(job_ids, list) else results[processed_ids[0]]
        except Exception as e:
            logging.error(f"error getting status for job: {job_ids}: {str(e)}")
            logging.error(traceback.format_exc())
            return {} if isinstance(job_ids, list) else None

    @staticmethod
    def wait_for_workflow_job_completion(job_ids, app=APP, timeout_minutes=60, poll_interval_seconds=30):
        try:
            client = SparkManager._get_client(app)
            processed_ids = SparkManager._process_job_ids(job_ids, app)

            start_time = time.time()
            timeout_seconds = timeout_minutes * 60

            all_run_ids = {}
            for jid in processed_ids:
                active_runs = list(client.jobs.list_runs(job_id=jid, active_only=True))
                if not active_runs:
                    logging.warning(f"no active runs found for job: {jid}")
                    all_run_ids[jid] = []
                    continue

                run_ids = [run.run_id for run in active_runs]
                logging.info(f"waiting for completion of runs: {run_ids} for job: {jid}")
                all_run_ids[jid] = run_ids

            results = {jid: True for jid in processed_ids}

            while (time.time() - start_time) < timeout_seconds:
                all_completed = True

                for jid, run_ids in all_run_ids.items():
                    if not run_ids:
                        continue

                    for run_id in run_ids:
                        run_details = client.jobs.get_run(run_id=run_id)
                        current_state = run_details.state.life_cycle_state

                        if current_state not in ["TERMINATED", "SKIPPED", "INTERNAL_ERROR"]:
                            all_completed = False
                            logging.info(f"job run_id: {run_id} still running with status: {current_state}")
                        elif run_details.state.result_state != "SUCCESS":
                            results[jid] = False
                            logging.warning(
                                f"job run_id: {run_id} failed with result: {run_details.state.result_state}")

                if all_completed:
                    logging.info(f"all jobs completed with results: {results}")
                    break

                time.sleep(poll_interval_seconds)

            if (time.time() - start_time) >= timeout_seconds:
                logging.error(f"jobs timed out after {timeout_minutes} minutes")
                for jid in processed_ids:
                    results[jid] = False

            return results if isinstance(job_ids, list) else results[processed_ids[0]]
        except Exception as e:
            logging.error(f"error waiting for job completion: {str(e)}")
            logging.error(traceback.format_exc())
            return {} if isinstance(job_ids, list) else False


sparkmanager = SparkManager()

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger('spark.api.main2', logconsole=True)
    config.init('dev')  # , overrides={('apo_data_access.spark', 'spark_read_format'): 'databricks'})

    # SparkManager.start_workflow_job('282601299481949', job_parameters={'env': 'dev', 'fromdt': '2025-02-27'})

    # out = SparkManager.get_workflow_job_status('282601299481949')

    sparkmanager.init()
    # df = pd.read_parquet(f'c:/temp/temp.parquet')
    # sparkmanager.pandastosql(df, 'dbo.findox_core_holdings', app='databricks')

    # new_schema = {'exp_beta_contribution': 'float', 'ir_exp_beta_contrib': 'float',
    #               'exp_beta_contrib_mult': 'float', 'tsy_10y_exp_beta': 'float'}
    # df = pd.read_clipboard()
    # sparkmanager.pandastodelta(df, 'pnl_enhanced', 'tables/dbo', overwrite=True, new_schema=new_schema)
    #
    # sparkdf = sparkmanager.readsql("SELECT * FROM dbo.trace limit 10", app='databricks')
    # logging.info(sparkdf)
    # logging.info(sparkdf.toPandas())

    sdf = sparkmanager.readsql("SELECT * FROM dbo.trace limit 100", app='databricks')
    logging.info(f'sdf: {sdf.count()}')
    psdf = sdf.pandas_api()
    logging.info(f'psdf: {psdf.shape}')

    tempdf = psdf.apply(lambda x: x.shape, axis=1)
    logging.info(f'tempdf: {tempdf.to_pandas()}')

    params = [(i,) for i in range(100)]


    def _dateadd(x):
        from common.utils import dtutil
        import time
        import random
        time.sleep(int(random.random() * 5))
        return {'dt': dtutil.dateadd(dtutil.today(), f'{x}d')}


    out = sparkmanager.submitjob(_dateadd, params, params_per_task=5)
    logging.info(out)

    pass
