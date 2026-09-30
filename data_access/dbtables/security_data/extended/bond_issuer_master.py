from common.utils import dtutil
from data_access.utils import tosqllist, tolist

from data_access.dbtables.security_data.bond_issuer_master import *

UPDATE_SQL = '''
UPDATE dbo.bond_issuer_master
SET {0}
WHERE id_bb_company in {1} {2}
'''


def update_by_bbgid(bbgids, set_stmt, where_clauses=[]):
    bbgids = tosqllist(bbgids)
    where_clause = '' if len(where_clauses) == 0 else ' AND ' + ' AND '.join(where_clauses)
    update_sql = UPDATE_SQL.format(set_stmt, bbgids, where_clause)
    execsql(update_sql, app=APP)


LATEST_ISS_SQL = '''
select
	{}
from
	f_get_bond_issuer_master('{}') bim
where bim.{} in {}
'''


def get_issuer_details(ids, id_column='id_bb_company', select_cols=None, knowledge_dt=None):
    knowledge_dt = knowledge_dt or dtutil.todate(dtutil.today())
    select_cols = select_cols or '*'
    ids = tosqllist(ids)

    sql = LATEST_ISS_SQL.format(','.join(tolist(select_cols)), knowledge_dt, id_column, ids)
    df = readdfsql(sql, app=APP)
    return df
