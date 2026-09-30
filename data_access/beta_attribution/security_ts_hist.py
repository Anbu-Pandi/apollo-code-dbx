import logging
import re

import pandas as pd

from data_access.beta_attribution import readdfsql_schema, updateschema
from data_access.beta_attribution.utils import tosqllist, get_date_between_clause, todatestr, get_date_in_clause, tolist
from data_access.dbtables import BETA_EST_SUBAPP as APP
from data_access.spark.api import sparkmanager
from data_access.utils import insert_list_to_db, insert_df_to_db, delete_from_db, get_where_clause, delete_df_from_db_databricks

TABLE_NAME = 'dbo.security_ts_hist'

SQL = """
SELECT
    sec_id as SEC_ID, 
    asof_date as ASOFDATE, 
    attribute as ATTRIBUTE, 
    frequency as FREQUENCY,
    source as SOURCE,
    value as VALUE{4}
FROM dbo.security_ts_hist
WHERE 
    {0}
    AND VALUE IS NOT NULL
    AND (lower(sec_id) IN {1})
    AND lower(attribute) IN {2}{3}
ORDER BY asof_date        
"""

RETURN_COLUMNS = """
    LAG(value) OVER (PARTITION BY sec_id, attribute, frequency, source ORDER BY asof_date) AS PREV_VALUE,
    VALUE AS CUR_VALUE,
    VALUE / LAG(value) OVER (PARTITION BY sec_id, attribute, frequency, source ORDER BY asof_date) - 1 AS RETURN                
"""

FREQUENCY_CLAUSE = """
AND lower(frequency) = '{0}'
"""

SOURCE_CLAUSE = """
AND lower(source) IN {0}
"""

DATE_COLUMN = 'asof_date'


def _get_formatted_sql(sql, dateclause, secids, attribute, frequency=None, source=None, addtnl_columns=None):
    additional_where_clause = ""
    if not frequency is None:
        frequency = str(frequency).lower()
        additional_where_clause += FREQUENCY_CLAUSE.format(frequency)
    if not source is None:
        source = tolist(source)
        source = [str(x).lower() for x in source]
        additional_where_clause += SOURCE_CLAUSE.format(tosqllist(source))

    attribute = tolist(attribute)
    attribute = [str(x).lower() for x in attribute]

    secids = tolist(secids, replacenone=True)
    secids = [str(x).lower() for x in secids]

    sec_ids_like = [x for x in secids if '%' in str(x)]
    sec_ids_in = [x for x in secids if '%' not in str(x)]
    sec_id_clause = tosqllist(sec_ids_in)
    if len(sec_ids_like) > 0:
        sec_id_like_clause = get_where_clause('sec_id', sec_ids_like, use_databricks=True)
        sec_id_clause += ' OR ' + sec_id_like_clause
    formattedsql = sql.format(dateclause,
                              sec_id_clause,
                              tosqllist(attribute),
                              additional_where_clause,
                              addtnl_columns or "")

    return formattedsql


def _get_df_by_source(df, source_order=None):
    if source_order is None:
        source_order = df.fillna({'SOURCE': ''}).groupby('SOURCE')['VALUE'].count() \
            .sort_values(ascending=False).index.tolist()
    df['_SOURCE_ORDER'] = df['SOURCE'].fillna('').map(source_order.index)
    df['_SOURCE_ORDER_TO_USE'] = df.groupby(['SEC_ID', 'ASOFDATE', 'ATTRIBUTE', 'FREQUENCY'])['_SOURCE_ORDER'] \
        .transform(min)
    df = df[df['_SOURCE_ORDER_TO_USE'] == df['_SOURCE_ORDER']].drop(columns=['_SOURCE_ORDER', '_SOURCE_ORDER_TO_USE'])
    return df


def get_return_timeseries(startdate, enddate, secids, attribute, frequency=None, source=None):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate, use_databricks=True)
    sql = _get_formatted_sql(SQL, dateclause, secids, attribute,
                             frequency=frequency,
                             source=source,
                             addtnl_columns="," + RETURN_COLUMNS)
    df = readdfsql_schema(sql, app=APP)
    df = _get_df_by_source(df, source)
    return df


def _make_sec_ids_map(sec_ids):
    def make_new_cds_key(x):
        pattern = r"([^\-]+)-([^\-]+)-([^\-]+)-([^\-]+)-([^\-]+)"

        match = re.match(pattern, str(x))
        if match:
            part1, part2, part3, part4, part5 = match.groups()
            part1 = part1[:6] + '%'
            output_string = "-".join([part1, part2, part3, part4, part5])
            return output_string
        return str(x)

    sec_ids_map_df = pd.DataFrame([(x, make_new_cds_key(x).upper()) for x in sec_ids], columns=['sec_id', 'gen_id'])
    return sec_ids_map_df


def get_timeseries(startdate, enddate, secids, attribute, frequency=None, source=None):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate, use_databricks=True)
    secids = tolist(secids)
    sec_ids_map_df = _make_sec_ids_map(secids)
    sql = _get_formatted_sql(SQL, dateclause, sec_ids_map_df['gen_id'].unique().tolist(), attribute,
                             frequency=frequency,
                             source=source,
                             addtnl_columns="")
    df = readdfsql_schema(sql, app=APP)
    df = _get_df_by_source(df, source)
    db_sec_ids_map_df = _make_sec_ids_map(df['SEC_ID'].unique().tolist())
    db_sec_ids_map_df = pd.merge(db_sec_ids_map_df, sec_ids_map_df, on='gen_id', suffixes=('_db', '_input'))
    db_sec_ids_map_df = db_sec_ids_map_df[['sec_id_db', 'sec_id_input']] \
        .rename(columns={'sec_id_db': 'DB_SEC_ID', 'sec_id_input': 'SEC_ID'})
    df = pd.merge(df.rename(columns={'SEC_ID': 'DB_SEC_ID'}), db_sec_ids_map_df, on='DB_SEC_ID')
    return df.sort_values(['SEC_ID', 'ASOFDATE'])


def get_timeseries_by_dates(datelist, secids, attribute, frequency=None, source=None):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    sql = _get_formatted_sql(SQL, dateclause, secids, attribute,
                             frequency=frequency,
                             source=source,
                             addtnl_columns="")
    df = readdfsql_schema(sql, app=APP)
    df = _get_df_by_source(df, source)
    return df


CUSIP_SQL = """
SELECT
    STH.sec_id as SEC_ID, 
    STH.attribute as ATTRIBUTE, 
    STH.frequency as FREQUENCY, 
    STH.source as SOURCE, 
    STH.asof_date as ASOFDATE, 
    STH.value as VALUE, 
    CLM.cusip as CUSIP, 
    CLM.loanx as LOANX
    {4}
FROM 
    dbo.security_ts_hist STH
LEFT JOIN
    dbo.cusip_loanx_mapping CLM
    ON STH.sec_id = CLM.loanx  
WHERE 
    {0}
    AND lower(CLM.cusip) IN {1}
    AND lower(STH.attribute) IN {2}{3}
ORDER BY asof_date
"""

LOANX_SQL = """
SELECT
    STH.sec_id as SEC_ID, 
    STH.attribute as ATTRIBUTE, 
    STH.frequency as FREQUENCY, 
    STH.source as SOURCE, 
    STH.asof_date as ASOFDATE, 
    STH.value as VALUE
    {4}
FROM 
    dbo.security_ts_hist STH  
WHERE 
    {0}
    AND lower(STH.sec_id) IN {1}
    AND lower(STH.attribute) IN {2}{3}
ORDER BY asof_date
"""


def get_timeseries_by_loanxid(startdate, enddate, cusips, attribute, frequency=None, source=None,
                              use_cusip_loanx_mapping_tbl=True):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate, use_databricks=True)
    if use_cusip_loanx_mapping_tbl:
        sql_statement = CUSIP_SQL
    else:
        sql_statement = LOANX_SQL

    sql = _get_formatted_sql(sql_statement, dateclause, cusips, attribute,
                             frequency=frequency,
                             source=source,
                             addtnl_columns="")
    df = readdfsql_schema(sql, app=APP)
    df = _get_df_by_source(df, source)
    return df


COLUMNS = ['sec_id', 'attribute', 'frequency', 'asof_date', 'value', 'source']
KEY_COLS = ['sec_id', 'attribute', 'frequency', 'asof_date', 'source']


def insert(secid, attribute, frequency, asofdate, value, source):
    if pd.isnull(value):
        return
    security_ts_hist_row = [[secid, attribute, frequency, todatestr(asofdate), value, source]]
    insert_list_to_db(updateschema(updateschema('dbo.security_ts_hist')), COLUMNS, security_ts_hist_row, app=APP)


def insertdf(df, usespark=False):
    df = df.rename(columns={'ASOFDATE': 'asof_date', 'asofdate': 'asof_date'})

    df = df.dropna()
    df.columns = [str(col).lower() for col in df.columns]
    if len(df) > 0:
        if usespark:
            sparkmanager.pandastosql(df, updateschema('dbo.security_ts_hist'), app=APP)
        else:
            insert_df_to_db(updateschema('dbo.security_ts_hist'), df, app=APP)


def deletedf(df):
    df = df.rename(columns={'ASOFDATE': 'asof_date', 'asofdate': 'asof_date'})

    df = df.dropna()
    df.columns = [str(col).lower() for col in df.columns]
    delete_df_from_db_databricks(df, updateschema('dbo.security_ts_hist'), key_cols=KEY_COLS, app=APP)


def delete(secid=None, attribute=None, frequency=None, asofdate=None, value=None, source=None):
    filter_values = [secid, attribute, frequency, asofdate, value, source]
    delete_from_db(updateschema('dbo.security_ts_hist'), COLUMNS, filter_values, app=APP)


def get_benchmark_beta_cdxhy(start_dt, end_dt, include_exp_beta=False):
    attributes = ["'BETA_WITH_ALPHA_12M_CDXHY'"]
    column_selections = [
        "SEC_ID as `BMK_IDX`",
        "asof_date",
        "avg(case when attribute = 'BETA_WITH_ALPHA_12M_CDXHY' then value end) as `Beta to CDX HY`"
    ]
    if include_exp_beta:
        attributes.append("'EXP_BETA_WITH_ALPHA_12M_CDXHY'")
        column_selections.append(
            "avg(case when attribute = 'EXP_BETA_WITH_ALPHA_12M_CDXHY' then value end) as `Exp Beta to CDX HY`")

    benchmarks = ["'iboxx_usloan_trs'", "'SPY US EQUITY'", "'tsy_10y'"]

    query = f"""
        SELECT {', '.join(column_selections)}
        FROM dbo.SECURITY_TS_HIST
        WHERE SEC_ID IN ({', '.join(benchmarks)})
            AND ATTRIBUTE IN ({', '.join(attributes)})
            AND asof_date BETWEEN '{start_dt}' and '{end_dt}'
        GROUP BY SEC_ID, asof_date
        ORDER BY asof_date DESC
    """

    df = readdfsql_schema(query, app=APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    df = get_timeseries('20230101', '20230921', 'TSY_10Y', 'BETA_WITH_ALPHA_12M_CDXHY', 'WEEKLY', 'JPM')
    logging.info(df)
