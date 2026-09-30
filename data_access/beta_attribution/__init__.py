DEFAULT_SCHEMA_NAME = 'dbo'
from apo_config.config import config
from common.utils.sqlutil import readdfsql
from data_access.dbtables import BETA_EST_SUBAPP as APP


def _getschema():
    return config.getconfig(APP, 'schema') or DEFAULT_SCHEMA_NAME


def updateschema(sql):
    return sql.replace(DEFAULT_SCHEMA_NAME + '.', _getschema() + '.') \
        .replace(f'[{DEFAULT_SCHEMA_NAME}]' + '.', _getschema() + '.')


def readdfsql_schema(sql, msg=None, app=None):
    sql = updateschema(sql)
    return readdfsql(sql, msg=msg, app=app)
