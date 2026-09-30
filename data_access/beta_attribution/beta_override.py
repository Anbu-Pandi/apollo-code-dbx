from datetime import datetime
from enum import Enum

import numpy as np

from data_access.beta_attribution import readdfsql_schema, updateschema
from data_access.beta_attribution.utils import adddays, todatestr
from data_access.utils import insert_list_to_db, update_to_db_whereclause, delete_from_db_whereclause, get_where_clause, \
    join_where_clauses
from common.utils.sqlutil import sql_replace_escape_char
from data_access.dbtables import BETA_EST_SUBAPP as APP

SQL_END_DATE = '9999-12-31'


class RefIndexType(Enum):
    Oil = 1
    Market = 2


def gettablename(refindex=RefIndexType.Market):
    assert type(refindex) is RefIndexType, f"reference index is not expected type: {RefIndexType}"
    if refindex == RefIndexType.Market:
        return "ba_sec_beta_override"
    elif refindex == RefIndexType.Oil:
        return "ba_sec_oil_beta_override"


def getbetaoverride(tablename, securities=None, dt=None):
    params = locals()

    dt = datetime.today() if dt is None else dt

    security_clause = None
    if not securities is None:
        security_clause = get_where_clause('security', securities, use_databricks=True)

    dt_clause = "( coalesce(start_date, '1900-01-01') <= '{0}' AND '{0}' <= coalesce(end_date, '2200-12-31') )".format(
        todatestr(dt))
    where_clause = join_where_clauses(' AND ', [security_clause, dt_clause])

    overridesql = '''
    SELECT security as [SECURITY],
           coalesce(start_date, '1900-01-01') as START_DATE, 
           coalesce(end_date, '2200-12-31') as END_DATE, 
           beta as [BETA],
           {2} as [REFERENCE_INDEX],
           {3} as [SHADOW_SECURITY]
    FROM dbo.{0}
    WHERE {1}
    ORDER BY coalesce(start_date, '1900-01-01') desc
    '''

    df = readdfsql_schema(sql_replace_escape_char(overridesql).format(tablename,
                                                                      where_clause,
                                                                'reference_index' if tablename == gettablename(
                                                                    RefIndexType.Market) else 'null',
                                                                'shadow_security' if tablename == gettablename(
                                                                    RefIndexType.Market) else 'null'),
                          msg=f'get beta overrides: {params}',
                          app=APP)
    df = df.drop_duplicates(subset=['SECURITY'], keep='first')
    return {x['SECURITY']: (x['BETA'], x['REFERENCE_INDEX'], x['SHADOW_SECURITY']) for (_, x) in df.iterrows()}


def insertbetaoverride(tablename, security, fromdt, todt, beta, ref_index):
    if not np.isnan(beta):
        insert_list_to_db(updateschema(f'dbo.{tablename}'),
                          columns=['security', 'start_date', 'end_date', 'beta', 'reference_index'],
                          insertvalues=[[security, todatestr(fromdt), todatestr(todt), beta, ref_index]],
                          app=APP)


def updatebetadateoverride(tablename, security, fromdt, todt):
    updateclause = f"cast(end_date as date) = '{todatestr(adddays(fromdt, -1))}'"
    whereclause = f"security = '{security}' AND cast(start_date as DATE) <= '{todatestr(fromdt)}' AND '{todatestr(fromdt)}' <= cast(end_date as DATE)"
    update_to_db_whereclause(updateschema(tablename), updateclause, whereclause, app=APP)

    updateclause = f"start_date = '{todatestr(adddays(todt, 1))}'"
    whereclause = f"security = '{security}' AND cast(start_date as DATE) <=  '{todatestr(todt)}' AND '{todatestr(todt)}' <= cast(end_date as DATE)"
    update_to_db_whereclause(updateschema(tablename), updateclause, whereclause, app=APP)


def deletebetaoverride(tablename, security, fromdt, todt):
    whereclause = f"security = '{security}' AND '{todatestr(fromdt)}' <= cast(start_date as DATE) AND cast(end_date as DATE) <= '{todatestr(todt)}'"
    delete_from_db_whereclause(updateschema(tablename), whereclause, app=APP)
