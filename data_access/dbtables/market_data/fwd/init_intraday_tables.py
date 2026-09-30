import logging

from apo_config.config import config
from common.utils.sqlutil import execsql
from data_access.dbtables import GS_MARKETDATA_APP as APP

COLUMNS = '''
    asof_date DATE,
    ccy_pair STRING,
    fwd_tenor STRING,
    obs_time STRING,
    forward_point DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    source STRING,
    last_updated STRING,
    knowledge_date TIMESTAMP,
    load_date TIMESTAMP
'''


def init_fx_forward_points_intraday(env=None):
    catalog = config.getconfig(APP, 'databricks_catalog')
    storage = config.getconfig(APP, 'blob_storage_name')
    container = config.getconfig(APP, 'blob_container_name') or 'marketanalytics'
    qn = f'{catalog}.dbo.gs_fx_forward_points_intraday'
    loc = f'abfss://{container}@{storage}/tables/dbo/gs_fx_forward_points_intraday'
    logging.info(f'creating external {qn} at {loc}')
    execsql(f"CREATE TABLE IF NOT EXISTS {qn} ({COLUMNS}) USING DELTA LOCATION '{loc}'", app=APP)


if __name__ == '__main__':
    import sys

    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()
    env = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    config.init(env)
    init_fx_forward_points_intraday(env)
