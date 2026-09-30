from common.utils.sqlutil import readdfsql
from data_access.dbtables import DASH_ACCESS_APP
from data_access.utils import insert_df_to_db

USER_REQUEST_LOG_TABLE = 'dash_access_logs'
APP_ACCESS_TABLE = 'dash_app_access'
ACCESS_WRITE_APP = 'databricks'


def get_dash_app_access():
    query = f'select * from {APP_ACCESS_TABLE}'
    df = readdfsql(query, app=DASH_ACCESS_APP)
    return df


def get_user_request_log_last_accessed():
    sql = f"select coalesce(max(last_access_ts), CAST('1900-01-01' AS timestamp)) as max_ts from {USER_REQUEST_LOG_TABLE}"
    df = readdfsql(sql, app=ACCESS_WRITE_APP)
    dt = df['max_ts'].iloc[0]
    return dt


def update_user_request_log(df):
    if len(df) > 0:
        insert_df_to_db(USER_REQUEST_LOG_TABLE, df, app=ACCESS_WRITE_APP)


def get_dash_access_logs():
    sql = f"select * from {USER_REQUEST_LOG_TABLE}"
    df = readdfsql(sql, app=DASH_ACCESS_APP)
    return df


if __name__ == '__main__':
    import logging
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    df = get_user_request_log_last_accessed()

    logging.info(df)
