import configparser
import getpass
import logging
import os

import duckdb
import numpy as np
import pandas as pd
from databricks.sql import connect

ENVIRONMENTS = ['dev', 'prod']
_PACKAGE_CONFIG = os.path.join(os.path.dirname(__file__), 'config_db.ini')
_BOOTSTRAP_ENV = 'APO_CONFIG_BOOTSTRAP_PATH'
_USER_BOOTSTRAP = os.path.join(
    os.path.expanduser('~'),
    '.apo_config',
    'apo_config_bootstrap.ini',
)


def _read_ini(path):
    config = configparser.ConfigParser()
    with open(path, encoding='utf-8') as stream:
        config.read_file(stream)
    return config


def _read_databricks_bootstrap(package_config):
    from pyspark.dbutils import DBUtils
    from pyspark.sql import SparkSession

    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    bootstrap = package_config['bootstrap']
    text = DBUtils(spark).secrets.get(
        scope=bootstrap['databricks_scope'],
        key=bootstrap['databricks_key'],
    )
    config = configparser.ConfigParser()
    config.read_string(text)
    return config


def _read_server_bootstrap(package_config):
    bootstrap = package_config['bootstrap']
    paths = [
        os.environ.get(_BOOTSTRAP_ENV),
        bootstrap.get('server_local_path'),
        _USER_BOOTSTRAP,
    ]
    for path in filter(None, paths):
        if os.path.isfile(path):
            return _read_ini(path)
    raise FileNotFoundError(
        f"apo_config bootstrap not found; set {_BOOTSTRAP_ENV} or restore a configured server path"
    )


def _read_runtime_config(actualuser):
    package_config = _read_ini(_PACKAGE_CONFIG)
    if actualuser == 'root':
        return _read_databricks_bootstrap(package_config)
    return _read_server_bootstrap(package_config)


def _upsert(df1, df2):
    df = pd.concat([df1, df2[~df2.index.isin(df1.index)]])
    df.update(df2)
    return df


class _ConfigDb:
    _CONFIGDBENV = None

    def _update_db_conn_dict(self, config, actualuser):
        self.db_conn_dict = {}
        self.db_conn_dict['server_hostname'] = config[self._CONFIGDBENV]['server_hostname']
        self.db_conn_dict['access_token'] = config[self._CONFIGDBENV]['access_token']
        self.db_conn_dict['catalog'] = config[self._CONFIGDBENV]['catalog']

        if (actualuser == 'root') and self._databricks_uc_enabled(actualuser):
            try:
                self.db_conn_dict['http_path'] = 'sql/protocolv1/o/{}/{}' \
                    .format(config[self._CONFIGDBENV]['server_hostname'].split('adb-')[-1].split('.')[0],
                            self._databricks_notebook_cluster(actualuser))
                connect(**self.db_conn_dict)
            except:
                self.db_conn_dict['http_path'] = config[self._CONFIGDBENV]['http_path']
        else:
            self.db_conn_dict['http_path'] = config[self._CONFIGDBENV]['http_path']

    def init(self, configdbenv, actualuser):
        assert configdbenv.lower() in ENVIRONMENTS, f"{configdbenv} not valid. please choose from: {ENVIRONMENTS}"
        if self._CONFIGDBENV is None:
            self._CONFIGDBENV = configdbenv.lower()

        config = _read_runtime_config(actualuser)

        self.username = config[self._CONFIGDBENV]['username']
        self.password = config[self._CONFIGDBENV]['password']
        self.location = config[self._CONFIGDBENV]['location']

        self._update_db_conn_dict(config, actualuser)

        self.duck_conn = duckdb.connect(':memory:')
        self.duck_conn.sql("SET TimeZone='UTC';")
        self.duck_conn.sql(f"""
                    CREATE SECRET azsecret (
                    TYPE AZURE,
                    CONNECTION_STRING 'DefaultEndpointsProtocol=https;AccountName={self.username};AccountKey={self.password};EndpointSuffix=core.windows.net'
                    );
                    """)

    def getconfigdbenv(self):
        return self._CONFIGDBENV

    def get_write_connection(self):
        return connect(**self.db_conn_dict)

    def _databricks_notebook_cluster(self, user):
        assert user == 'root', f'can use this only on databricks notebook'
        from data_access.spark.api import sparkmanager
        sparkmanager.refresh_spark(is_databricks_env=True)
        return sparkmanager._spark.conf.get("spark.databricks.clusterUsageTags.clusterId")

    def _databricks_uc_enabled(self, user):
        assert user == 'root', f'can use this only on databricks notebook'
        from data_access.spark.api import sparkmanager
        sparkmanager.refresh_spark(is_databricks_env=True)
        is_uc_enabled = sparkmanager._spark.conf.get("spark.databricks.unityCatalog.enabled", "false").lower() == "true"
        return is_uc_enabled

    def getconfigdf(self, env, username, actual_user):
        sql = f"SELECT app, username, configkey, configvalue FROM dbo.app_config " \
              f"WHERE env = '{env}' " \
              f"AND ( username = '{username}' OR username IS NULL ) " \
              f"ORDER  BY app, configkey, username, lastupdated"

        if actual_user == 'root':
            df = pd.read_sql(sql, connect(**self.db_conn_dict))
        else:
            try:
                dt = self.duck_conn.sql(f"select * from delta_scan('{self.location}')")
                self.duck_conn.register('app_config', dt)
                df = self.duck_conn.sql(sql.replace('dbo.app_config', 'app_config')).df()
            except:
                logging.warning(f'failed to read config using duckdb. reading using db')
                df = pd.read_sql(sql, connect(**self.db_conn_dict))

        return df

    def readconfigdict(self, username, actual_user):
        assert self._CONFIGDBENV is not None, f"env is not set"
        df = self.getconfigdf(self._CONFIGDBENV, username, actual_user)
        dfglobal = df[pd.isnull(df['username'])]
        dfuser = df[pd.notnull(df['username'])]

        dfglobal.set_index(['app', 'configkey'], inplace=True)
        if len(dfuser) > 0:
            dfuser.set_index(['app', 'configkey'], inplace=True)
            dfglobal = _upsert(dfglobal, dfuser)

        dfglobal = dfglobal.reset_index()
        configdict = {(dfrow['app'].lower() if pd.notnull(dfrow['app']) else np.nan, dfrow['configkey'].lower()):
                          dfrow['configvalue']
                      for (_, dfrow) in dfglobal.iterrows()}

        return configdict

    def saveappconfigkey(self, env, app, username, configkey, configvalue, isuseroverride=False):
        assert self._CONFIGDBENV is not None, f"config env is not set"
        appinsertclause = f"'{app}'" if pd.notnull(app) else 'NULL'
        userinsertclause = f"'{username}'" if pd.notnull(username) and isuseroverride else 'NULL'
        insertsql = f'INSERT INTO dbo.app_config(env, app, username, configkey, configvalue, lastupdated, lastupdatedby) ' \
                    f"SELECT '{env}', {appinsertclause}, {userinsertclause}, '{configkey}', '{configvalue}', GETDATE(), '{getpass.getuser().lower()}'"
        appdeleteclause = f"app={appinsertclause}" if pd.notnull(app) else "app IS NULL"
        userdeleteclause = f"username='{username}'" if isuseroverride else "username IS NULL"
        deletesql = f'DELETE FROM dbo.app_config ' \
                    f"WHERE env='{env}' AND {appdeleteclause} AND configkey='{configkey}' AND {userdeleteclause}"
        conn = self.get_write_connection()
        pd.io.sql.execute(deletesql, conn)
        if pd.notnull(configvalue):
            if isuseroverride:
                selectsql = f"SELECT count(*) as count FROM dbo.app_config " \
                            f"WHERE env = '{env}' AND app{' IS ' if pd.isnull(app) else ' = ' + appinsertclause} AND configkey = '{configkey}'"
                df = pd.read_sql(selectsql, conn)
                if df.iloc[0]['count'] == 0:
                    self.saveappconfigkey(env, app, username, configkey, configvalue, isuseroverride=False)
            pd.io.sql.execute(insertsql, conn)
        conn.close()

    def saveappconfig(self, env, app, username, configdict, isuseroverride=False):
        for (k, v) in configdict.items():
            self.saveappconfigkey(env, app, username, k, v, isuseroverride=isuseroverride)


configdb = _ConfigDb()
