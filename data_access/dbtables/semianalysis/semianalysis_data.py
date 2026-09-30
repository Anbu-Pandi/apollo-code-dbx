import logging

from common.utils.sqlutil import readdfsql, insertdf_batch, execsql
from data_access.dbtables import SEMIANALYSIS_APP
from data_access.utils import get_where_clause, join_where_clauses


def get_ai_demand(companies=None, regions=None, start_year=None, end_year=None, *, groups=None):
    sql = """
    SELECT knowledge_date, `group`, category, company, item, unit, period,
           period_type, period_end_date, total_value
    FROM v_semianalysis_ai_demand_by_customer
    """
    where_clauses = []
    if regions:
        raise ValueError("region is not available in the current SemiAnalysis AI-demand model")
    if companies:
        where_clauses.append(get_where_clause("company", companies))
    if groups:
        where_clauses.append(get_where_clause("`group`", groups))
    if start_year:
        where_clauses.append(f"YEAR(period_end_date) >= {int(start_year)}")
    if end_year:
        where_clauses.append(f"YEAR(period_end_date) <= {int(end_year)}")
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    sql += "\nORDER BY company, period_end_date, knowledge_date"
    logging.info("Reading AI demand data from database")
    return readdfsql(sql, app=SEMIANALYSIS_APP)


def get_power_requirements(chip_types=None, manufacturers=None, *, items=None, segments=None):
    sql = """
    SELECT segment, item, unit, period, value, knowledge_date, load_date
    FROM semianalysis_power_requirements_per_chip
    """
    where_clauses = []
    items = items or chip_types
    segments = segments or manufacturers
    if items:
        where_clauses.append(get_where_clause("item", items))
    if segments:
        where_clauses.append(get_where_clause("segment", segments))
    where_clause = join_where_clauses(' AND ', where_clauses)
    if where_clause:
        sql += f"\nWHERE {where_clause}"
    sql += "\nORDER BY segment, item, period, knowledge_date"
    return readdfsql(sql, app=SEMIANALYSIS_APP)


def upsert_data(table_name, df):
    if df.empty:
        logging.warning(f'Empty dataframe provided for {table_name}')
        return
    
    logging.info(f'Upserting {len(df)} rows to {table_name}')
    
    try:
        insertdf_batch(table_name, df, app=SEMIANALYSIS_APP)
    except Exception as e:
        error_msg = str(e)
        if 'TABLE_OR_VIEW_NOT_FOUND' in error_msg or 'table or view' in error_msg.lower():
            logging.info(f'ℹ️  Table {table_name} does not exist, creating it with data')
            from data_access.spark.azuredb import AzureDB
            partition_col = 'year' if 'year' in df.columns else None
            AzureDB.writedf_local(df, table_name, overwrite=True, partitioncolumn=partition_col)
            
            from data_access.spark.api import sparkmanager
            sparkmanager.init()
            local_path = AzureDB.get_local_dir() + '/' + table_name
            sparkmanager.merge_local_file(local_path, table_name, 'tables/dbo', partitioncolumn=partition_col)
            logging.info(f'✅ Created table {table_name} and loaded {len(df)} rows')
        else:
            raise


def sync_local_to_remote(table_name, overwrite=False):
    from data_access.spark.azuredb import AzureDB
    from data_access.spark.api import sparkmanager
    import os
    
    local_path = os.path.join(AzureDB.get_local_dir(), table_name)
    
    if not os.path.exists(local_path):
        raise FileNotFoundError(f'Local table {table_name} not found at {local_path}')
    
    local_db = AzureDB(local_dir=AzureDB.get_local_dir())
    df = local_db.read_sql(f"SELECT * FROM {table_name}")
    
    if df.empty:
        logging.warning(f'Local table {table_name} is empty')
        return 0
    
    logging.info(f'📖 Read {len(df)} rows from local {table_name}')
    
    table_exists = True
    if overwrite:
        try:
            execsql(f'DELETE FROM {table_name} WHERE 1=1', app=SEMIANALYSIS_APP, retrycount=1)
            logging.info(f'🗑️  Cleared existing data from remote {table_name}')
        except Exception as e:
            import traceback
            error_msg = str(e)
            full_trace = traceback.format_exc()
            
            if e.__cause__:
                error_msg += f'\nCaused by: {str(e.__cause__)}'
            
            if 'TABLE_OR_VIEW_NOT_FOUND' in error_msg or 'cannot be found' in error_msg or 'TABLE_OR_VIEW_NOT_FOUND' in full_trace or 'cannot be found' in full_trace:
                logging.info(f'ℹ️  Table {table_name} does not exist yet, will be created during merge')
                table_exists = False
            else:
                raise
    
    if table_exists:
        insertdf_batch(table_name, df, app=SEMIANALYSIS_APP)
        logging.info(f'✅ Inserted {len(df)} rows to remote {table_name}')
    else:
        partition_col = None
        sparkmanager.init()
        sparkmanager.merge_local_file(local_path, table_name, 'tables/dbo', partitioncolumn=partition_col, type='delta', overwrite=True)
        logging.info(f'✅ Created and synced {len(df)} rows to remote {table_name}')
    
    return len(df)


def delete_all_semianalysis_data():
    tables = [
        'semianalysis_ai_demand_by_customer',
        'semianalysis_power_requirements_per_chip',
        'semianalysis_na_datacenter_supply',
        'semianalysis_overseas_datacenter_supply',
        'semianalysis_hyperscalers_ailabs'
    ]
    
    for table in tables:
        logging.info(f'Deleting all data from {table}')
        execsql(f'DELETE FROM {table}', app=SEMIANALYSIS_APP)
    
    logging.info('✅ All SemiAnalysis tables cleaned')

