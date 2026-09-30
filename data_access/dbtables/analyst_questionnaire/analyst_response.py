from common.utils.sqlutil import readdfsql
from common.utils.dtutil import today
from data_access.dbtables import ANALYST_QUEST_APP
from data_access.utils import tosqllist, update_to_db_whereclause
import logging


def get_analyst_response_by_company(company_id, question):
    sql = f"Select * " \
          f"from analyst_questionnaire_response " \
          f"where company_id = {company_id} and question in {tosqllist(question)} " \
          f"order by last_update_ts desc"

    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    if df.empty:
        logging.info(f'No response found for company id - {company_id}')
    return df


def get_analyst_response_stats():
    sql = f'''
WITH company_response AS (
SELECT 
round(datediff(DAY, last_update_ts, max(last_update_ts) OVER (PARTITION BY company_id)) / 90, 0) AS qtr_num,
max(last_update_ts) OVER (PARTITION BY company_id) AS last_updated,
ROW_NUMBER() OVER (PARTITION BY company_id
ORDER BY last_update_ts DESC) AS row_num,
question, company_id, last_updated_by
FROM analyst_questionnaire_response
), ques AS (
SELECT count(*) AS ct
FROM analyst_questions
where report_name='analyst_questionnaire'
)
SELECT cr.company_id, cr_latest.last_updated, cr_latest.last_updated_by, count(DISTINCT cr.question) * 100.0 / q.ct AS [%_complete]
FROM company_response cr
JOIN company_response cr_latest ON
cr.company_id = cr_latest.company_id
and cr.qtr_num = cr_latest.qtr_num
JOIN ques q ON
1 = 1
AND cr_latest.row_num = 1
GROUP BY cr.company_id, cr.qtr_num, cr_latest.last_updated, cr_latest.last_updated_by, q.ct
ORDER BY cr_latest.last_updated
    '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    df['company_id'] = df['company_id'].apply(lambda x: float(x))

    if df.empty:
        logging.info(f'Analyst Response Stats DF Empty')

    return df


def get_analyst_response_data():
    sql = f"Select * from analyst_questionnaire_response order by company_id, company_name, question, last_update_ts"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_latest_analyst_response():
    sql = '''
WITH analyst_response_latest AS (
SELECT ROW_NUMBER() OVER(PARTITION BY company_id, question
ORDER BY last_update_ts DESC) AS row_num, *
FROM analyst_questionnaire_response
)
SELECT ar.*
FROM analyst_response_latest ar
JOIN analyst_questions aq ON
ar.question = aq.qid
WHERE row_num = 1
ORDER BY aq.SECTION, aq.[order]
    '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_analyst_questions():
    sql = '''
    select * from analyst_questions where disable is null or disable = 0
    '''

    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_analyst_company_detail():
    sql = '''
        select * from analyst_company_detail
        '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_analyst_company_id_by_name(company_name):
    sql = f'''
            select company_id from analyst_company_detail where company_name = '{company_name}'
            '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    id = None

    if not df.empty:
        id = df['company_id'].iloc[-1]

    return id


def get_anayst_updated_company_by_key(sys_key):
    sql = f'''
            select company_id from analyst_company_detail where sys_key = '{sys_key}'
            '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    id = df['company_id'].iloc[-1]

    return id


def get_analyst_request_id_by_key(sys_key):
    sql = f"select request_id from analyst_request_tracker where sys_key = '{sys_key}'"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    id = df['request_id'].iloc[-1]
    return id


def get_request_tracker_id_by_company(company_id, status=None, request_type=None):
    status = tosqllist(status, isstr=True)

    sql = f''' select request_id from analyst_request_tracker where ult_company_id = '{company_id}' '''

    if status is not None:
        sql = sql + f" and status in {status}"
    if request_type is not None:
        sql = sql + f" and request_type = '{request_type}'"

    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    request_ids = df['request_id'].to_list()

    return request_ids


def get_go_private_response(company_id, status, request_type):
    request_ids = get_request_tracker_id_by_company(company_id, status, request_type)
    request_ids = tosqllist(request_ids, isstr=False)

    sql = f'''select * from v_analyst_response where request_type='{request_type}' and request_id in {request_ids} '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    return df


def get_request_tracker_data(request_type):
    sql = f" select * from analyst_request_tracker where request_type='{request_type}'"
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    df = df.dropna(how='all')
    return df


def update_request_tracker_status(request_id, status, last_updated):
    logging.info(f'Update request id - {request_id} with status - {status}')
    set_clause = f"status='{status}', last_status_update='{last_updated.strftime('%Y-%m-%d %H:%M:%S')}'"
    where_clause = f"request_id in {tosqllist(request_id, isstr=False)}"
    update_to_db_whereclause('analyst_request_tracker', set_clause, where_clause)


def get_private_request_stats(status):
    sql = f'''
    select ult_company_name as Company,
    count(request_id) as [Request Count], 
    max(last_requested) as [Last Requested]
    from analyst_request_tracker
    where status in {tosqllist(status)}
    and request_type='go_private'
    group by ult_company_name
    '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)
    return df


def get_fundamental_score():
    dt = today()
    sql = f'''
        select *
        from apo_fundamental_score
        where start_date <= '{dt}'
        and end_date >= '{dt}'
        '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    FUNDAMENTAL_SCORE_MAP = {1: 'Upgrade', 2: 'Upgrade', 3: 'Unchanged', 4: 'Downgrade', 5: 'Downgrade'}

    df['score'] = df['score'].map(lambda x: FUNDAMENTAL_SCORE_MAP[x] if x in FUNDAMENTAL_SCORE_MAP.keys() else x)
    return df


def get_apo_ice_industry_map():
    sql = f'''
            select *
            from apo_ice_industry_map
            '''
    df = readdfsql(sql, app=ANALYST_QUEST_APP)

    return df


if __name__ == '__main__':
    from apo_config.config import config

    # config.init('dev')
    config.init('prod')
    company_name = 11225586
