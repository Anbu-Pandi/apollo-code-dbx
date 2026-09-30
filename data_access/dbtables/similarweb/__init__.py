from data_access.dbtables.similarweb.similarweb_data import (
    get_data,
    get_domains,
    upsert_data,
    delete_data
)
from data_access.dbtables.similarweb.init_tables import init_similarweb_tables
from data_access.dbtables.similarweb.compare_similarweb_sources import (
    run_comparison,
    get_databricks_similarweb_data,
    get_snowflake_daily_data,
    get_snowflake_monthly_data,
    compare_traffic_metrics,
    align_and_compare_values,
    AI_DOMAINS,
    SNOWFLAKE_DOMAINS,
    DOMAIN_MAPPING,
    SIMILARWEB_SNOWFLAKE_APP
)

__all__ = [
    'get_data',
    'get_domains',
    'upsert_data',
    'delete_data',
    'init_similarweb_tables',
    'run_comparison',
    'get_databricks_similarweb_data',
    'get_snowflake_daily_data',
    'get_snowflake_monthly_data',
    'compare_traffic_metrics',
    'align_and_compare_values',
    'AI_DOMAINS',
    'SNOWFLAKE_DOMAINS',
    'DOMAIN_MAPPING',
    'SIMILARWEB_SNOWFLAKE_APP'
]

