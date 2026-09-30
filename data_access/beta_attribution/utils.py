from datetime import timedelta

from data_access.beta_attribution import readdfsql_schema
from data_access.dbtables import APP
from data_access.utils import tosqllist, tolist, todate, todatestr, join_where_clauses

MAX_DATE = '2100-12-31'
MIN_DATE = '1900-01-01'


def adddays(dt, days):
    return todate(dt) + timedelta(days=days)


DATE_IN_CLAUSE = """
cast({0} as DATE) IN {1}
"""


def get_date_in_clause(datecolumn, dates, use_databricks=False):
    if use_databricks:
        datecolumn = datecolumn.replace('[', '`').replace(']', '`')
    return DATE_IN_CLAUSE.format(datecolumn, tosqllist([todatestr(dt) for dt in tolist(dates)]))


DATE_BETWEEN_CLAUSE = """
{0} BETWEEN '{1}' AND '{2}'
"""


def get_date_between_clause(datecolumn, fromdate, todate, use_databricks=False):
    if use_databricks:
        datecolumn = datecolumn.replace('[', '`').replace(']', '`')
    fromdate = todatestr(fromdate)
    todate = todatestr(todate)
    mindate = min([fromdate, todate])
    maxdate = max([fromdate, todate])
    return DATE_BETWEEN_CLAUSE.format(datecolumn, mindate, maxdate)


COUNT_SQL = """
select 
    count(*) as rec_count 
FROM 
    {0}
WHERE 
    {1}
"""


def get_count_for_table_by_date(tablename, datecolumn, dates, additionalwhere=None, app=None):
    dateclause = get_date_in_clause(datecolumn, dates)
    sql = COUNT_SQL.format(tablename, join_where_clauses(' AND ', [dateclause, additionalwhere]))
    df = readdfsql_schema(sql, app=app or APP)
    return df
