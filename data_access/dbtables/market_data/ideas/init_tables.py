import logging

from apo_config.config import config
from common.utils.sqlutil import execsql
from data_access.dbtables import GS_MARKETDATA_APP as APP

COLUMNS = '''
    idea_id STRING,
    version INT,
    asof_date DATE,
    asset_id STRING,
    rating STRING,
    conviction STRING,
    horizon STRING,
    idea_activity_type STRING,
    idea_source STRING,
    idea_status STRING,
    owner_id STRING,
    owner_name STRING,
    rationale STRING,
    comment STRING,
    source STRING,
    last_updated STRING,
    knowledge_date TIMESTAMP,
    load_date TIMESTAMP
'''


def init_gs_trade_ideas(env=None):
    catalog = config.getconfig(APP, 'databricks_catalog')
    storage = config.getconfig(APP, 'blob_storage_name')
    container = config.getconfig(APP, 'blob_container_name') or 'marketanalytics'
    qn = f'{catalog}.dbo.gs_trade_ideas'
    loc = f'abfss://{container}@{storage}/tables/dbo/gs_trade_ideas'
    logging.info(f'creating external {qn} at {loc}')
    execsql(f"CREATE TABLE IF NOT EXISTS {qn} ({COLUMNS}) USING DELTA LOCATION '{loc}'", app=APP)


if __name__ == '__main__':
    import sys

    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()
    env = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    config.init(env)
    init_gs_trade_ideas(env)
