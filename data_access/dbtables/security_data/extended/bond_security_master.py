import logging

from common.utils import dtutil
from data_access.utils import tosqllist, tolist
from data_access.dbtables.security_data.bond_security_master import *

UPDATE_SQL = '''
UPDATE dbo.bond_security_master
SET {0}
WHERE id_bb_global in {1} {2}
'''


def update_by_bbgid(bbgids, set_stmt, where_clauses=[]):
    bbgids = tosqllist(bbgids)
    where_clause = '' if len(where_clauses) == 0 else ' AND ' + ' AND '.join(where_clauses)
    update_sql = UPDATE_SQL.format(set_stmt, bbgids, where_clause)
    execsql(update_sql, app=APP)


LATEST_SEC_SQL = '''
select
	{}
from
	f_get_bond_security_master('{}') bsm
where bsm.{} in {}
'''


def get_sec_details(ids, id_column='id_bb_global', select_cols=None, knowledge_dt=None):
    knowledge_dt = knowledge_dt or dtutil.todate(dtutil.today())
    select_cols = select_cols or '*'
    ids = tosqllist(ids)

    sql = LATEST_SEC_SQL.format(','.join(tolist(select_cols)), knowledge_dt, id_column, ids)
    df = readdfsql(sql, app=APP)
    return df


if __name__ == '__main__':
    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()
    config.init('dev')

    df = get_sec_details(['US653894AA37', 'US653894AA37'],
                         id_column='id_isin',
                         select_cols=['id_isin', 'amt_outstanding'])
    logging.info(df)
