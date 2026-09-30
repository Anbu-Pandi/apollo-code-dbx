import json
import logging

import pandas as pd
from pyspark.sql.types import StringType

from apo_config.config import config
from common.utils import sqlutil
from data_access.dbtables import FINDOX_APP
from data_access.spark.api import sparkmanager
from data_access.utils import tosqllist, join_where_clauses


def delete_before_insert(dfs):
    for k, v in dfs.items():
        if len(v) > 0:
            where_clauses = []
            if ('asof_date' in v.columns) and ('company_ticker' in v.columns):
                v['asof_date'] = pd.to_datetime(v['asof_date'], format='mixed').dt.date
                company_dt = v.groupby('company_ticker')['asof_date'].min().reset_index()
                company_dt = [f"{x['company_ticker']}_{x['asof_date']}" for _, x in company_dt.iterrows()]
                where_clauses.append(f"concat(company_ticker, '_', asof_date) IN {tosqllist(company_dt)}")

            where_clause = ''
            if len(where_clauses) > 0:
                where_clause = 'WHERE ' + join_where_clauses(' AND ', where_clauses)

            del_sql = f"DELETE FROM {k} {where_clause}"
            sqlutil.execsql(del_sql, app=FINDOX_APP)


def snapshot_predicate(table, df):
    if table == 'findox_agg_universe':
        return ' OR '.join(
            f"(id = {int(row.id)} AND financial_data_updated = TIMESTAMP '{pd.Timestamp(row.financial_data_updated).isoformat(sep=' ')}')"
            for row in df[['id', 'financial_data_updated']].drop_duplicates().itertuples(index=False))
    pairs = df[['company_id', 'asof_date']].drop_duplicates()
    if pairs.isna().any().any():
        raise ValueError(f'Missing company or snapshot date for {table}')
    return ' OR '.join(
        f"(asof_date = DATE '{pd.Timestamp(date).date()}' AND company_id IN ({','.join(str(int(i)) for i in group.company_id)}))"
        for date, group in pairs.groupby('asof_date'))


def prepare_agg_snapshot(table, df, schema):
    df = df.copy()
    for column in df.select_dtypes(include='object'):
        df[column] = df[column].map(lambda value: json.dumps(value) if isinstance(value, (list, dict)) else value)
    if 'asof_date' in df:
        df['asof_date'] = pd.to_datetime(df['asof_date']).dt.date
    predicate = snapshot_predicate(table, df)
    strings = {field.name: df[field.name].map(lambda value: None if pd.isna(value) else str(value))
               for field in schema.fields if isinstance(field.dataType, StringType) and field.name in df}
    prepared, prepared_schema = sparkmanager._prepare_for_pandastospark(df, table, schema, FINDOX_APP, None)
    for name, values in strings.items():
        prepared[name] = values
    return prepared, prepared_schema, predicate


def write_agg_snapshot(table, df, schema_name=None):
    if df.empty:
        return
    sparkmanager.refresh_spark()
    sparkmanager._set_databricks_catalog_schema(FINDOX_APP)
    target = f"{schema_name or config.getconfig(FINDOX_APP, 'schema') or 'dbo'}.{table}"
    schema = sparkmanager._spark.table(target).schema
    prepared, _, predicate = prepare_agg_snapshot(table, df, schema)
    staged = sparkmanager._create_dataframe_using_blob(prepared)
    del prepared
    staged = sparkmanager.cast_to_schema(staged, schema)
    staged = staged.select(*[field.name for field in schema.fields])
    staged = staged.dropDuplicates([name for name in staged.columns if name != 'load_date'])
    (staged.write.format('delta').mode('overwrite').option('partitionOverwriteMode', 'static')
     .option('replaceWhere', predicate).saveAsTable(target))
    logging.info('Committed Findox snapshot table=%s rows=%s', target, len(df))


def dfs_to_db(dfs, overwrite=False, schema_name=None):
    other_dfs = {k: v for k, v in dfs.items() if not k.startswith('findox_agg_')}
    if overwrite and other_dfs:
        delete_before_insert(other_dfs)

    for k, v in dfs.items():
        if k.startswith('findox_agg_'):
            write_agg_snapshot(k, v, schema_name=schema_name)
        else:
            sparkmanager.pandastosql(v, k, app=FINDOX_APP)


def get_current_tickers():
    TICKER_SQL = '''
        select distinct company_ticker from findox_agg_standard_data_actual
        intersect
        select distinct company_ticker from findox_agg_reported_data_actual
    '''
    current_tickers = sqlutil.readdfsql(TICKER_SQL, app=FINDOX_APP)['company_ticker'].unique().tolist()

    return current_tickers


def get_last_updated():
    db_last_updated = sqlutil.readdfsql('''
            select id as company_id, cast(max(financial_data_updated) as string) as last_updated from dbo.findox_agg_universe
            group by id
            ''', app=FINDOX_APP)
    return db_last_updated
