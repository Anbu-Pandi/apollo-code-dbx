from common.utils.sqlutil import readdfsql
from data_access.dbtables import ANALYST_QUEST_APP


def compliance_restrictions():
    sql = f" select * from compliance_restrictions"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_request_restriction_id_map():
    sql = f" select * from analyst_request_compliance_restrictions_map"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_request_event_id_map():
    sql = f" select * from compliance_request_event_id_map"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_compliance_restriction_id(sys_key):
    sql = f'''
                select restriction_id from compliance_restrictions where sys_key = '{sys_key}'
            '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    id = df['restriction_id'].values

    return id


def get_compliance_event_id(sys_key):
    sql = f'''
                select event_id from compliance_event_id_tracker where sys_key = '{sys_key}'
            '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    id = df['event_id'].iloc[-1]

    return id
