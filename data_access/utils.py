import logging
import uuid
from calendar import monthrange
from datetime import datetime, date

import numpy as np
import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.dfutil import tolist, chunks_list
from common.utils.sqlutil import \
    execsql, insertdf_tosql, insertdf_batch, insertdf_bcp, insertdf_execmany, readdfsql, sql_replace_escape_char, \
    getconn, _closeconn


def join_where_clauses(joinwith, strlist):
    whereclause = joinwith.join([x for x in strlist if x is not None and len(x) > 0])
    if len(whereclause) > 0:
        whereclause = "(" + whereclause + ")"
    return whereclause


def _valueifnone(x, v):
    if x is None:
        return v
    return x


def _parsedate(k, v):
    v = str(v)
    k = k.replace('from_', '').replace('to_', '')
    assert len(k) == len(v), f"check date filters: {k}={v}"
    ret = [None, None, None]
    for i, token in enumerate(('yyyy', 'mm', 'dd')):
        idx = k.find(token)
        if idx > -1:
            ret[i] = int(v[idx:idx + len(token)])

    return ret


def _parsedatefilters(datefilters, datecolumn, databricks_format=False):
    '''
    eg datefilters: {from_mm:<mm>, to_mm:<mm>}
    '''
    if ('[' not in datecolumn) and (not '.' in datecolumn):
        datecolumn = f'[{datecolumn}]'
    if databricks_format:
        datecolumn = sql_replace_escape_char(datecolumn)
    from_dt = [None, None, None]
    to_dt = [None, None, None]
    for (k, v) in datefilters.items():
        (yyyy, mm, dd) = _parsedate(k, v)
        if k.find('from') == 0:
            from_dt[0] = _valueifnone(from_dt[0], yyyy)
            from_dt[1] = _valueifnone(from_dt[1], mm)
            from_dt[2] = _valueifnone(from_dt[2], dd)
        if k.find('to') == 0:
            to_dt[0] = _valueifnone(to_dt[0], yyyy)
            to_dt[1] = _valueifnone(to_dt[1], mm)
            to_dt[2] = _valueifnone(to_dt[2], dd)

    from_dt[0] = _valueifnone(from_dt[0], 1900)
    from_dt[1] = _valueifnone(from_dt[1], 1)
    from_dt[2] = _valueifnone(from_dt[2], 1)

    to_dt[0] = _valueifnone(to_dt[0], 2100)
    to_dt[1] = _valueifnone(to_dt[1], 12)
    to_dt[2] = _valueifnone(to_dt[2], monthrange(to_dt[0], to_dt[1])[1])

    dateclause = f"{datecolumn} >= '{from_dt[0]}-{from_dt[1]:02}-{from_dt[2]:02}' AND " + \
                 f"{datecolumn} <= '{to_dt[0]}-{to_dt[1]:02}-{to_dt[2]:02}'"

    return dateclause


def tosqllist(s, isstr=True):
    s = tolist(s) or []
    if len(s) > 0:
        if isstr:
            s = [f"'{x}'" for x in s if pd.notnull(x)]
        else:
            s = [f"{x}" for x in s if pd.notnull(x)]
        s = "(" + ",".join(s) + ")"
    else:
        s = "(NULL)"
    return s


def todate(dt):
    if not isinstance(dt, (datetime, date)):
        dt = pd.to_datetime(str(dt))
    return dt


def todatestr(dt):
    return todate(dt).strftime('%Y-%m-%d')


def insert_df_to_db(tablename, df, msg=None, app=None, method='batch'):
    logging.info(f"insert df: {df.shape} to {tablename} with method: {method}")
    if method == 'sqlalchemy':
        insertdf_tosql(tablename=tablename, df=df, msg=msg, app=app)
    elif method == 'sql':
        insertdf_execmany(tablename=tablename, df=df, msg=msg, app=app)
    elif method == 'batch':
        insertdf_batch(tablename=tablename, df=df, msg=msg, app=app)
    elif method == 'bcp':
        insertdf_bcp(tablename=tablename, df=df, msg=msg, app=app)
    else:
        raise Exception('Invalid insert method.')


def insert_list_to_db(tablename, columns, insertvalues, msg=None, app=None, method='batch'):
    df = pd.DataFrame(insertvalues, columns=columns)
    insert_df_to_db(tablename, df, msg=msg, app=app, method=method)


STRING_EQUAL_CLAUSE = "{0} = '{1}'"

STRING_LIKE_CLAUSE = "{0} LIKE '{1}'"

NUMBER_EQUAL_CLAUSE = "{0} = {1}"

IN_CLAUSE = "{0} IN {1}"


def get_where_clause(column, value, use_databricks=False):
    strike_like_clause = STRING_LIKE_CLAUSE
    strike_equal_clause = STRING_EQUAL_CLAUSE

    if use_databricks:
        strike_like_clause = strike_like_clause.replace(' LIKE ', ' ILIKE ')
        strike_equal_clause = strike_equal_clause.replace('{0}', 'UPPER({0})') \
            .replace("'{1}'", "UPPER('{1}')")
        column = sql_replace_escape_char(column)

    def update_clause(clause_str, column, value, addnot=False):
        if isinstance(value, str):
            value = value.strip()
        if value[:1] == '!':
            return 'NOT (' + clause_str.format(column, value[1:]) + ')'
        elif addnot:
            return 'NOT (' + clause_str.format(column, value) + ')'
        else:
            return clause_str.format(column, value)

    def str_adjust(x):
        return str(x).replace("'", "''")

    if isinstance(value, list) and (len([x for x in value if pd.notnull(x)]) > 0):
        value = [x for x in value if pd.notnull(x)]
        if isinstance(value[0], (date, datetime, np.datetime64)):
            value = [todatestr(x) for x in value]
        elif isinstance(value[0], (str,)):
            value = [str_adjust(x) for x in value]

        if np.any(['%' in str(x) for x in value]):
            addnot = str(value[0])[:1] == '!'
            conj = ' AND ' if addnot else ' OR '
            v_clauses = [update_clause(strike_like_clause, column, str_adjust(x), addnot) for x in value]
            return '(' + conj.join(v_clauses) + ')'
        else:
            addnot = np.any(['!' == str(x)[:1] for x in value])
            value = [x[1:] if '!' == str(x)[:1] else x for x in value]
            value = tosqllist(value, isstr=isinstance(value[0], str))
            return update_clause(IN_CLAUSE, column, value, addnot)

    if isinstance(value, (date, datetime)):
        value = todatestr(value)

    if isinstance(value, str):
        if '%' in value:
            return update_clause(strike_like_clause, column, str_adjust(value))
        else:
            return update_clause(strike_equal_clause, column, str_adjust(value))

    elif (not isinstance(value, list)) and pd.notnull(value):
        return update_clause(NUMBER_EQUAL_CLAUSE, column, str(value))


DELETE_SQL = """
DELETE FROM {0}
WHERE
    {1}
"""


def delete_from_db(tablename, columns, filtervalues, msg=None, app=None):
    columns = tolist(columns)
    if len(columns) == 1:
        filtervalues = tolist(filtervalues)
    assert len(columns) == len(filtervalues), 'columns and filtervalues should of same size!'

    whereclauses = []
    for (col, value) in zip(columns, filtervalues):
        whereclauses += [get_where_clause(col, value)]

    whereclause = join_where_clauses(' AND ', whereclauses)
    if len(whereclause) == 0:
        logging.info(f'Please check delete for table: {tablename}! skipping delete since no filter conditions!')
    else:
        delete_from_db_whereclause(tablename, whereclause, msg=msg, app=app)


def delete_from_db_whereclause(tablename, whereclause, msg=None, app=None):
    sql = DELETE_SQL.format(tablename, whereclause)
    execsql(sql, msg=msg, app=app)


UPDATE_SQL = """
    UPDATE {0}
    SET {1}
    WHERE
        {2}
"""


def delete_df_from_db(df, tablename, msg=None, app=None, chunk_size=500):
    if df.empty:
        return

    total_rows = len(df)

    for start_idx in range(0, total_rows, chunk_size):
        end_idx = min(start_idx + chunk_size, total_rows)
        chunk_df = df.iloc[start_idx:end_idx]

        columns = chunk_df.columns.tolist()
        filters = []

        for _, row in chunk_df.iterrows():
            row_conditions = []
            for col in columns:
                if pd.notna(row[col]):
                    row_conditions.append(get_where_clause(col, row[col]))

            filters.append(join_where_clauses(' AND ', row_conditions))

        where_clause = join_where_clauses(' OR\n', filters)
        if len(where_clause) == 0:
            logging.warning(f'Please check delete for table: {tablename}! skipping delete since no filter conditions!')
            return

        del_sql = f"DELETE FROM {tablename}\nWHERE {where_clause}"
        execsql(del_sql, msg=msg, app=app)


def delete_df_from_db_spark(df, tablename, key_cols, msg=None, app=None):
    sparkmanager.refresh_spark()

    spark_df = sparkmanager.pandastospark(df, table=tablename, app=app)

    tmp_view = f"_delete_keys_tmp_{uuid.uuid4().hex}"
    spark_df.createOrReplaceTempView(tmp_view)

    join_cond = " AND ".join([f"t.{col} = k.{col}" for col in key_cols])
    sql = f"""
        DELETE FROM {tablename} t
        WHERE EXISTS (
            SELECT 1 FROM {tmp_view} k
            WHERE {join_cond}
        )
    """
    sparkmanager._spark.sql(sql)
    sparkmanager._spark.catalog.dropTempView(tmp_view)


def _fmt_sql_value(v):
    if pd.isna(v):
        return 'NULL'
    if isinstance(v, (datetime, date)):
        return f"'{todatestr(v)}'"
    if isinstance(v, str):
        return f"'{v.replace(chr(39), chr(39) * 2)}'"
    return str(v)


def _get_table_key_schema(cursor, tablename, key_cols):
    cursor.execute(f"DESCRIBE TABLE {tablename}")
    rows = cursor.fetchall()
    schema = {row[0]: row[1] for row in rows if row[0] in key_cols}
    missing = set(key_cols) - set(schema.keys())
    if missing:
        raise ValueError(f"Key columns not found in table {tablename}: {missing}")
    return schema


def delete_df_from_db_databricks(df, tablename, key_cols, msg=None, app=None, batch_size=5000):
    """
    Deletes rows from a Databricks Delta table matching key_cols.

    Uses a session-scoped temp table + DELETE EXISTS (single Delta transaction):
      - DESCRIBE TABLE for exact key column types (avoids MERGE/type mismatch issues)
      - Batched INSERT into temp table, then one DELETE join
      - No PySpark session or blob round-trip (unlike delete_df_from_db_spark)

    Requires app with driver='databricks'. Must run all statements on one connection
    so the temp table persists through INSERT and DELETE.
    """
    if df is None or df.empty:
        return

    key_cols = list(key_cols)
    df_keys = df[key_cols].dropna(subset=key_cols).drop_duplicates().reset_index(drop=True)
    if df_keys.empty:
        logging.warning(f'delete df using temp table: all key rows are NULL, skipping for {tablename}')
        return

    escaped_cols = [sql_replace_escape_char(f'[{c}]') for c in key_cols]
    temp_table = f"_delete_keys_tmp_{uuid.uuid4().hex}"

    if msg:
        logging.info(msg)
    logging.info(f'delete df using temp table: deleting up to {len(df_keys)} keys from {tablename}')

    with getconn(app) as conn:
        with conn.cursor() as cursor:
            col_sql_types = _get_table_key_schema(cursor, tablename, key_cols)
            logging.info(f'delete df using temp table: key column types: {col_sql_types}')

            col_defs = ', '.join(f'{ec} {col_sql_types[c]}' for c, ec in zip(key_cols, escaped_cols))

            cursor.execute(f"CREATE TEMPORARY TABLE {temp_table} ({col_defs})")

            for start_idx in range(0, len(df_keys), batch_size):
                batch = df_keys.iloc[start_idx: min(start_idx + batch_size, len(df_keys))]
                values = ', '.join(
                    '({})'.format(', '.join(_fmt_sql_value(val) for val in row))
                    for row in batch.itertuples(index=False, name=None)
                )
                cursor.execute(f"INSERT INTO {temp_table} VALUES {values}")
                logging.info(f'delete df using temp table: inserted {len(batch)} keys into {temp_table}')

            join_cond = ' AND '.join(f't.{ec} = k.{ec}' for ec in escaped_cols)
            cursor.execute(f"""
                DELETE FROM {tablename} t
                WHERE EXISTS (
                    SELECT 1 FROM {temp_table} k
                    WHERE {join_cond}
                )
            """)
            logging.info(f'delete df using temp table: completed delete from {tablename}')

        _closeconn(conn, app)


def update_to_db(tablename, setclause, columns, filtervalues, msg=None, app=None):
    columns = tolist(columns)
    if len(columns) == 1:
        filtervalues = tolist(filtervalues)
    assert len(columns) == len(filtervalues), 'columns and filtervalues should of same size!'

    whereclauses = []
    for (col, value) in zip(columns, filtervalues):
        whereclauses += [get_where_clause(col, value)]

    whereclause = join_where_clauses(' AND ', whereclauses)
    if len(whereclause) == 0:
        logging.info(f'Please check update for table: {tablename}! skipping delete since no filter conditions!')
    else:
        update_to_db_whereclause(tablename, setclause, whereclause, msg=msg, app=app)


def update_to_db_whereclause(tablename, setclause, whereclause, msg=None, app=None):
    sql = UPDATE_SQL.format(tablename, setclause, whereclause)
    execsql(sql, msg=msg, app=app)


def chunk_query(sql, query_list, app=None):
    query_list = tolist(query_list)
    dfs = []
    for _l in chunks_list(query_list, 10_000):
        _l = tosqllist(_l)
        _df = readdfsql(sql.format(_l), app=app)
        dfs.append(_df)
    if len(dfs) > 0:
        return pd.concat(dfs, ignore_index=True)
