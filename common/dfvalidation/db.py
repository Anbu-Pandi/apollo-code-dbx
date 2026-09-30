import getpass
from datetime import datetime, date

import numpy as np
import pandas as pd

from common.utils.sqlutil import readdfsql, execsql, insertdf_batch

VALIDATION_APP = 'databricks'

SQL = """
SELECT * 
FROM dbo.validate_error
WHERE 
    [fromdate] <= '{0}'
    AND [todate] >= '{1}'
    {2}
"""

DELETE_SQL = """
DELETE 
FROM dbo.validate_error
WHERE 
    [fromdate] = '{0}'
    AND [todate] = '{1}'
    {2}
"""

INSERT_SQL = """
INSERT 
INTO dbo.validate_error
({0})
VALUES({1}) 
"""

TABLE_CLAUSE = """
    AND [table] = '{0}'
"""


def _todatestr(s, usetimepart=False):
    dateformat = '%Y-%m-%d'
    if usetimepart:
        dateformat += ' %H:%M:%S'
    return pd.to_datetime(s).strftime(dateformat) if isinstance(s, (str, datetime, date, np.datetime64)) \
        else str(s)


def _get_table_clause(table):
    if table is None:
        return ''
    return TABLE_CLAUSE.format(table.lower())


def _exists(asofdate, todate, table):
    df = readdfsql(SQL.format(_todatestr(asofdate), _todatestr(todate), _get_table_clause(table)), app=VALIDATION_APP)
    return len(df) > 0


def _delete(asofdate, todate, table):
    execsql(DELETE_SQL.format(_todatestr(asofdate), _todatestr(todate), _get_table_clause(table)), app=VALIDATION_APP)


def save_errors(df: pd.DataFrame, asofdate, table, groupcolumn, overwrite=False, todate=None):
    if todate is None:
        todate = asofdate
    if overwrite:
        _delete(asofdate, todate, table)
    if not _exists(asofdate, todate, table):
        if len(df) > 0:
            df['group'] = df['group'].astype(str)
            df['fromdate'] = _todatestr(asofdate)
            df['todate'] = _todatestr(todate)
            df['table'] = table.lower()
            df['groupcolumn'] = groupcolumn.lower()
            df['lastupdatedby'] = getpass.getuser().lower()
            df['lastupdated'] = _todatestr(datetime.now(), usetimepart=True)
            insertdf_batch('dbo.validate_error', df, app=VALIDATION_APP)


def get_errors(asofdate, table=None, todate=None):
    if todate is None:
        todate = asofdate
    return readdfsql(SQL.format(_todatestr(asofdate), _todatestr(todate), _get_table_clause(table)), app=VALIDATION_APP)


def get_error_summary(asofdate, table=None, todate=None):
    df = get_errors(asofdate, table, todate)
    return df.groupby(['table', 'column', 'validatetype']) \
        .agg({'failedcount': sum, 'totalcount': sum, 'group': 'count'}) \
        .reset_index()


if __name__ == '__main__':
    df = pd.read_clipboard()
    from apo_config.config import config

    config.init('prod')
    save_errors(df, '10/21/2020', 'trace', 'cusip', True)
    get_errors('10/21/2020', 'trace')
    get_error_summary('10/21/2020', 'trace')
