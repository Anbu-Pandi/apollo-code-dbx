import logging
import uuid
from datetime import datetime, timezone
import pytz
import pyspark.sql.functions as pysfunc

from common.utils import dtutil
from common.utils.dtutil import tz_convert
from common.utils.sqlutil import readdfsql, execsql
from apo_config.config import config
from data_access.dbtables import APP, BETA_EST_SUBAPP, SNOWFLAKE_PORTFOLIO_APP
from data_access.dbtables.model_detail.model_detail import get_model_name
from data_access.dbtables.risk_pnl_data.col_name_mapper import to_db_col_name, _SOURCE_OVERRIDES
from data_access.spark.api import sparkmanager
from data_access.utils import tosqllist
from data_access.dbtables.risk_pnl_data.snowflake_pnl_risk_query import risk_query_template, pnl_query_template, \
    latest_vpm_issuer_id_template


def get_risk_data(start_date, end_date, fund=None):
    start_date = dtutil.todate(start_date)
    end_date = dtutil.todate(end_date)

    if start_date > end_date:
        start_dt = end_date
        end_dt = start_date
    else:
        start_dt = start_date
        end_dt = end_date

    sql = f"Select * " \
          f"from v_risk_enhanced " \
          f"where asof_date between '{start_dt}' and '{end_dt}'"

    if fund is not None:
        sql = f'{sql} and fund_entity_code in {tosqllist(fund)}'

    df = readdfsql(sql, app=BETA_EST_SUBAPP)
    if df.empty:
        logging.info(f'No data Fetched for start date: {start_date} and end date: {end_date} and fund: {fund}')
    return df


def get_max_risk_date():
    sql = f"Select max(asof_date) from v_risk_enhanced"
    df = readdfsql(sql, app=BETA_EST_SUBAPP)
    return df.iat[0, 0].date()


def get_apo_fund_list():
    sql = f"select distinct fund_entity_code from v_risk_enhanced"
    df = readdfsql(sql, app=BETA_EST_SUBAPP)
    return df['fund_entity_code'].to_list()


def get_spac_betas(start_date, end_date):
    sql = f"select * from v_spac_beta where start_date between '{start_date}' and '{end_date}' order by start_date desc"
    df = readdfsql(sql, app=APP)
    return df


def delete_enhanced_data(start_dt, end_dt, model_name, tbl_name):
    logging.info(f'Deleting data from {tbl_name} from date {start_dt} to {end_dt} and model_name: {model_name}')
    sql = f"delete from {tbl_name} where asof_date between '{start_dt}' and '{end_dt}' and model_name= '{model_name}'"
    execsql(sql, app=BETA_EST_SUBAPP)


def _prepare_insert_metadata(source):
    model_name = get_model_name('beta_attribution')
    now = tz_convert(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    src_overrides = _SOURCE_OVERRIDES.get(source, {})
    return model_name, now, src_overrides


_INSERT_META_COLS = frozenset({'model_name', 'knowledge_date', 'load_date'})


def get_enhanced_table_schema(tbl_name, exclude_insert_meta=True):
    """Return column names and pandas dtypes from target table (zero rows)."""
    schema_df = readdfsql(f'select * from {tbl_name} where 1=0', app=BETA_EST_SUBAPP)
    if exclude_insert_meta:
        schema_df = schema_df.drop(
            columns=[c for c in _INSERT_META_COLS if c in schema_df.columns]
        )
    return {
        'columns': list(schema_df.columns),
        'dtypes': {c: schema_df[c].dtype for c in schema_df.columns},
    }


def insert_sparkdf_to_db(spark_df, tbl_name, start_dt, end_dt, source):
    model_name, now, src_overrides = _prepare_insert_metadata(source)

    spark_df = (spark_df
                .withColumn('model_name', pysfunc.lit(model_name))
                .withColumn('knowledge_date', pysfunc.lit(now))
                .withColumn('load_date', pysfunc.lit(now)))

    delete_enhanced_data(start_dt, end_dt, model_name, tbl_name)
    sparkmanager.writetosql(spark_df, tbl_name, app=BETA_EST_SUBAPP)


def _prepare_sparkdf_staging(spark_df, tbl_name, source):
    """Add insert metadata and align columns to the target table schema."""
    model_name, now, _ = _prepare_insert_metadata(source)
    spark_df = (spark_df
                .withColumn('model_name', pysfunc.lit(model_name))
                .withColumn('knowledge_date', pysfunc.lit(now))
                .withColumn('load_date', pysfunc.lit(now)))

    schema = sparkmanager._getdfschema(tbl_name, BETA_EST_SUBAPP)
    drop_auto_id_col = [x.name for x in schema.fields
                        if (x.name not in spark_df.columns) and (schema.fields[0].name == x.name) and (
                            x.name.endswith('_id'))]
    spark_df = spark_df.select(*[pysfunc.col(x.name).cast(x.dataType).alias(x.name)
                                 if (x.name in spark_df.columns) else
                                 pysfunc.lit(None).cast(x.dataType).alias(x.name)
                                 for x in schema.fields
                                 if x.name not in drop_auto_id_col])
    return spark_df, model_name


def merge_insert_sparkdfs_to_db(start_dt, end_dt, table_dfs):
    """
    Load enhanced data for start_dt..end_dt using a single MERGE per table.

    Each table's load is one atomic Delta MERGE that (a) deletes all existing
    target rows for the asof_date range and model_name, and (b) inserts every
    source row. Matched rows are never updated — all source rows are reinserted.
    Writes run independently per table (per-table atomicity, like
    atomic_insert_sparkdfs_to_db).

    Parameters
    ----------
    start_dt, end_dt : date-like
        Date range deleted/replaced in the target table (asof_date between).
    table_dfs : list of (tbl_name, spark_df, source) tuples
        e.g. [('pnl_enhanced', pnl_sdf, 'pnl'), ('risk_enhanced', risk_sdf, 'risk')]
    """
    if not table_dfs:
        raise ValueError('table_dfs must contain at least one (tbl_name, spark_df, source) tuple')

    sparkmanager.init()
    sparkmanager.refresh_spark(is_databricks_env=(config.getuser(actual=True) == 'root'))
    sparkmanager._set_databricks_catalog_schema(BETA_EST_SUBAPP)

    start_dt = dtutil.todate(start_dt)
    end_dt = dtutil.todate(end_dt)

    spark = sparkmanager._spark
    model_name = None
    merge_jobs = []
    src_views = []
    logging.info(f'MERGE load for {start_dt} to {end_dt}')

    for tbl_name, spark_df, source in table_dfs:
        staged_df, model_name = _prepare_sparkdf_staging(spark_df, tbl_name, source)

        model_name_lit = model_name.replace("'", "''")
        src_view = f'_merge_src_{tbl_name.split(".")[-1]}_{uuid.uuid4().hex}'
        staged_df.createOrReplaceTempView(src_view)
        src_views.append(src_view)

        insert_cols = staged_df.columns
        insert_col_list = ', '.join(f'`{c}`' for c in insert_cols)
        insert_val_list = ', '.join(f's.`{c}`' for c in insert_cols)

        merge_sql = f"""
            MERGE INTO {tbl_name} AS t
            USING {src_view} AS s
            ON 1 = 0
            WHEN NOT MATCHED THEN
                INSERT ({insert_col_list}) VALUES ({insert_val_list})
            WHEN NOT MATCHED BY SOURCE
                AND t.asof_date >= DATE '{start_dt}'
                AND t.asof_date <= DATE '{end_dt}'
                AND t.model_name = '{model_name_lit}'
            THEN DELETE
        """
        merge_jobs.append((tbl_name, merge_sql))

    logging.info(f'temp_views created for insertion {start_dt} to {end_dt}, model_name={model_name}')
    try:
        for tbl_name, merge_sql in merge_jobs:
            logging.info(f'MERGE into {tbl_name}:\n{merge_sql}')
            spark.sql(merge_sql)
    finally:
        for src_view in src_views:
            spark.catalog.dropTempView(src_view)

    logging.info(f'MERGE load completed for {start_dt} to {end_dt}, model_name={model_name}')


def insert_enhanced_df_to_db(df, tbl_name, start_dt, end_dt, source):
    model_name, now, src_overrides = _prepare_insert_metadata(source)

    df.columns = [to_db_col_name(c, overrides=src_overrides) for c in df.columns]

    sparkmanager.init()
    spark_df = sparkmanager.pandastospark(df, table=tbl_name, app=BETA_EST_SUBAPP)
    merge_insert_sparkdfs_to_db(start_dt, end_dt, [(tbl_name, spark_df, source)])


def get_pnl_data(start_date, end_date, fund=None):
    start_date = dtutil.todate(start_date)
    end_date = dtutil.todate(end_date)

    sql = f"Select * " \
          f"from v_pnl_enhanced " \
          f"where asof_date between '{start_date}' and '{end_date}'"

    if fund is not None:
        sql = f'{sql} and fund_entity_code in {tosqllist(fund)}'

    df = readdfsql(sql, app=BETA_EST_SUBAPP)
    if df.empty:
        logging.info(f'No data Fetched for start date: {start_date} and end date: {end_date} and fund: {fund}')
    return df


def get_fund_beta_ts(fund, by_strategy=False):
    grp_key = ['fund_entity_code']

    if by_strategy:
        grp_key.append('fo_strategy_broad_category')

    grp_key_str = ', '.join(grp_key)

    sql = f"""
        SELECT asof_date, {grp_key_str}, sum(COALESCE(beta_contribution,0)+COALESCE(ir_beta_contrib,0)) as net_beta  
        FROM v_risk_enhanced 
        WHERE fund_entity_code in {tosqllist(fund)}
        AND asof_date >= '2018-10-1'
        group by asof_date, {grp_key_str}
        order by asof_date desc    
    """

    df = readdfsql(sql, app=BETA_EST_SUBAPP)

    return df


def refresh_risk_pnl_view():
    execsql('REFRESH gold.v_risk_pnl_enhanced', app=BETA_EST_SUBAPP)
    execsql('REFRESH gold.v_risk_pnl_enhanced_latest', app=BETA_EST_SUBAPP)
    logging.info(f'refreshed view: v_risk_pnl_enhanced')


def get_snowflake_positions_data(start_date, end_date, fund_entity_code=None, columns=None, filter_condition=None):
    fund_entity_filter = ''
    if fund_entity_code is not None:
        fund_entity_filter = f' and a12.FUND_ENTITY_CODE in {tosqllist(fund_entity_code)}'

    select_clause = '*' if columns is None else ', '.join(f'"{col}"' for col in columns)
    where_clause = f"""WHERE {filter_condition}""" if filter_condition else ''

    query = f"""
    WITH base_data AS (
    {pnl_query_template.format(start_dt=start_date, end_dt=end_date, fund_entity_filter=fund_entity_filter)}
    )
    SELECT {select_clause}
    FROM base_data
    {where_clause}
    """
    df = readdfsql(query, app=SNOWFLAKE_PORTFOLIO_APP)

    if columns is None or 'As Of Coupon' in columns:
        df['As Of Coupon'] = df['As Of Coupon'].div(100)
    return df


def get_snowflake_risk_data(start_date, end_date, fund_entity_code=None, columns=None, filter_condition=None):
    fund_entity_filter = ''
    if fund_entity_code is not None:
        fund_entity_filter = f' and a14.FUND_ENTITY_CODE in {tosqllist(fund_entity_code)}'

    select_clause = '*' if columns is None else ', '.join(f'"{col}"' for col in columns)

    where_clause = f"""WHERE {filter_condition}""" if filter_condition else ''

    sql = f"""
    WITH base_data AS (
    {risk_query_template.format(start_dt=start_date, end_dt=end_date, fund_entity_filter=fund_entity_filter)}
    )
    select {select_clause}
    from base_data
    {where_clause}
    """
    df = readdfsql(sql, app=SNOWFLAKE_PORTFOLIO_APP)

    if columns is None or 'Coupon' in columns:
        df['Coupon'] = df['Coupon'].div(100)

    return df


def get_snowflake_latest_vpm_issuer_id(start_date, end_date, fund_entity_code=None):
    fund_entity_filter = ''
    if fund_entity_code is not None:
        fund_entity_filter = f' and a13.FUND_ENTITY_CODE in {tosqllist(fund_entity_code)}'

    query = latest_vpm_issuer_id_template.format(start_dt=start_date, end_dt=end_date,
                                                 fund_entity_filter=fund_entity_filter)
    df = readdfsql(query, app=SNOWFLAKE_PORTFOLIO_APP)
    return df


def snowflake_max_pnl_n_risk_date():
    risk_query = 'SELECT PORTFOLIO_DB.portfolio_l1.FN_GET_PREV_BUSINESS_DATE_RISK_LOOK_THRU() AS max_risk_date'
    pnl_query = 'SELECT PORTFOLIO_DB.PORTFOLIO_L1.FN_GET_PREV_BUSINESS_DATE_DAILY_PNL() AS max_pnl_date'

    risk_df = readdfsql(risk_query, app=SNOWFLAKE_PORTFOLIO_APP)
    pnl_df = readdfsql(pnl_query, app=SNOWFLAKE_PORTFOLIO_APP)

    return risk_df['MAX_RISK_DATE'].iloc[0], pnl_df['MAX_PNL_DATE'].iloc[0]


def get_snowflake_job_status():
    position_job_query = "select * from portfolio.v_portfolio_job_status where TABLE_NAME ='daily_position_pnl_itd'"
    risk_job_query = "select * from portfolio.v_portfolio_job_status where TABLE_NAME ='daily_position_risk_look_thru'"

    risk_status_df = readdfsql(risk_job_query, app=SNOWFLAKE_PORTFOLIO_APP)
    position_status_df = readdfsql(position_job_query, app=SNOWFLAKE_PORTFOLIO_APP)

    risk_status_df.columns = [col.lower() for col in risk_status_df.columns]
    position_status_df.columns = [col.lower() for col in position_status_df.columns]

    return risk_status_df, position_status_df


def qc_snowflake_positions_data(start_date, end_date, fund_entity_code='CSTRAT'):
    fund_entity_filter = ''
    if fund_entity_code is not None:
        fund_entity_filter = f' and a12.FUND_ENTITY_CODE in {tosqllist(fund_entity_code)}'

    query = f"""
    WITH base_data AS (
    {pnl_query_template.format(start_dt=start_date, end_dt=end_date, fund_entity_filter=fund_entity_filter)}
    )
    SELECT "As Of Date" as asof_date, sum("MV Equivalent USD") as mv_equivalent_usd
    FROM base_data
    where "As Of Date" between '{start_date}' and '{end_date}'
    group by "As Of Date"
    """
    df = readdfsql(query, app=SNOWFLAKE_PORTFOLIO_APP)
    return (df['MV_EQUIVALENT_USD'].isnull()).all() or (df['MV_EQUIVALENT_USD'] == 0).all()


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')

    # refresh_risk_pnl_view()
    start_dt = '2026-07-09'
    end_dt = '2026-07-09'
    fund_entity_code = 'CSTRAT'

    required_cols = ["As Of Date", "Security", "Security Type", "CUSIP", "ISIN", "LoanX ID", "Red Code",
                     "Issuer Ult GICS Industry Group", "Position Currency", "Coupon", "Maturity Date"]

    # df = get_snowflake_risk_data(start_dt, end_dt, fund_entity_code, columns=required_cols, filter_condition='"Quantity" != 0')
    # risk_status_df, pnl_status_df = get_snowflake_job_status()
    df = qc_snowflake_positions_data(start_dt, end_dt, fund_entity_code='CSTRAT')
    logging.info(df.shape)
