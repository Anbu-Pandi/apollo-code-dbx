import logging

import numpy as np
import pandas as pd

from common.utils.sqlutil import readdfsql, execsql
from data_access.dbtables import APP
from data_access.utils import get_where_clause


def _get_existing_ids(table, id_column, ids, app=None):
    app = app or APP
    SQL = f"SELECT DISTINCT {id_column} FROM dbo.{table} WHERE {get_where_clause(id_column, ids)}"
    df = readdfsql(SQL, app=app)
    return df[id_column].tolist()


def _clean_tables_by_ids(id_column, ids, table_key_map, cast_as_date=False, app=None):
    app = app or APP
    for table, id in table_key_map.items():
        if id == id_column:
            logging.info(f'deleting data from table: {table}, by id: {id} ids:\n{len(ids)}')
            where_clause = get_where_clause(id_column, list(set(ids)))
            if not where_clause is None:
                if cast_as_date:
                    where_clause = where_clause.replace(id_column, f'cast({id_column} as date)')
                SQL = f"DELETE FROM dbo.{table} WHERE {where_clause}"
                execsql(SQL, app=app)


def processdf_for_yn(df):
    def parseyn(x):
        if pd.notnull(x):
            x = x.lower()
            if x == 'yes':
                return 1
            elif x == 'no':
                return 0
        return np.nan

    for c in df.columns:
        if df[c].map(lambda x: (isinstance(x, str) and x.lower() in
                                ['y', 'n', 'yes', 'no', 'not evaluated', 'unknown'])).any():
            df[c] = df[c].map(parseyn)
    return df


def processdf_for_insert(df, parse_yn=True, parse_dates=True, parse_bool=True, parse_bool_col=False):
    if parse_dates:
        df = df.replace(r'^0001-01-01.*', '9999-12-31', regex=True)
    if parse_bool and not parse_bool_col:
        df = df.applymap(lambda x: (1 if x else 0) if isinstance(x, bool) else x)
    if parse_bool and parse_bool_col:
        bool_cols = df.dtypes[df.dtypes == bool].index.values
        df[bool_cols] = df[bool_cols].astype(int)

    if parse_yn:
        df = processdf_for_yn(df)

    return df
