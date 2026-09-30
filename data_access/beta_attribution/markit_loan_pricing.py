from data_access.beta_attribution import readdfsql_schema
from data_access.beta_attribution.utils import get_count_for_table_by_date
from data_access.beta_attribution.utils import tosqllist, get_date_in_clause
from data_access.dbtables import MARKIT_LOAN_PRICING_APP
from common.utils.sqlutil import sql_replace_escape_char

cols = """
    loanx_id as `LoanX ID`,
    mark_date as `Mark Date`,
    bid as `Bid`,
    offer as `Offer`,
    depth as `Depth`,
    evaluated_price as `Evaluated Price`,
    close_bid as `Close Bid`,
    close_offer as `Close Offer`,
    close_date as `Close Date`,
    contributed as `Contributed`,
    asof_date as `ASOFDATE`,
    knowledge_date as `KNOWLEDGEDATE`,
    load_date as `LOADDATE`
"""

SQL = """
SELECT 
    {0}
FROM 
    dbo.`markit_pricing_data_loan`
WHERE
    {1}
    AND `loanx_id` in {2}
"""

PX_COLUMNS = """
    `mark_date` as Date,
    loanx_id as `LoanX ID`,
    evaluated_price as PX
"""

DATE_COLUMN = '`mark_date`'

TABLE_NAME = 'dbo.markit_pricing_data_loan'


def get_count(asofdate):
    df = get_count_for_table_by_date(TABLE_NAME, sql_replace_escape_char(DATE_COLUMN), asofdate, app=MARKIT_LOAN_PRICING_APP)
    return df


def get_px_timeseries_by_loanxid(datelist, loanxid):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    sql = sql_replace_escape_char(SQL).format(PX_COLUMNS, dateclause, tosqllist(loanxid))
    df = readdfsql_schema(sql, app=MARKIT_LOAN_PRICING_APP)
    return df

def get_px_timeseries_by_loanxid_retired__was_backup(datelist, loanxid):
    from data_access.dbtables import JPM_LL_APP
    JP_SQL = """
    SELECT 
        asof_date as Date,
        loanx as `LoanX ID`,
        end_price as PX
    FROM 
        dbo.jpm_loan_index
    WHERE
        {1}
        AND `loanx` in {2}
    """
    dateclause = get_date_in_clause('asof_date', datelist)
    sql = sql_replace_escape_char(JP_SQL).format(PX_COLUMNS, dateclause, tosqllist(loanxid))
    df = readdfsql_schema(sql, app=JPM_LL_APP)
    return df


def get_timeseries_by_loanxid(datelist, loanxid):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    sql = sql_replace_escape_char(SQL).format(cols, dateclause, tosqllist(loanxid))
    df = readdfsql_schema(sql, app=MARKIT_LOAN_PRICING_APP)
    return df
