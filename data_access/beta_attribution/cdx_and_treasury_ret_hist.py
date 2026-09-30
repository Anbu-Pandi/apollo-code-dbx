from data_access.beta_attribution import readdfsql_schema
from data_access.beta_attribution import APP
from data_access.beta_attribution.utils import get_date_between_clause

SQL = """
SELECT
    {0}            
FROM dbo.CDX_AND_TREASURY_RET_HIST
WHERE
    {0}
ORDER BY 
    DATE            
"""


ALL_COLUMNS = """
    asof_date as [Date], 
    1_month_bill_current_moddur as [1 month bill Current MODDUR], 
    3_month_bill_current_moddur as [3 month bill Current MODDUR], 
    12_month_bill_current_moddur as [12 month bill Current MODDUR], 
    2_year_note_current_moddur as [2 year note Current MODDUR], 
    3_year_note_current_moddur as [3 year note Current MODDUR], 
    5_year_note_current_moddur as [5 year note Current MODDUR], 
    7_year_note_current_moddur as [7 year note Current MODDUR], 
    10_year_note_current_moddur as [10 year note Current MODDUR], 
    30_year_bond_current_moddur as [30 year bond Current MODDUR], 
    1_month_bill_current_realized_return as [1 month bill Current Realized Return], 
    3_month_bill_current_realized_return as [3 month bill Current Realized Return], 
    12_month_bill_current_realized_return as [12 month bill Current Realized Return], 
    2_year_note_current_realized_return as [2 year note Current Realized Return], 
    3_year_note_current_realized_return as [3 year note Current Realized Return], 
    5_year_note_current_realized_return as [5 year note Current Realized Return], 
    7_year_note_current_realized_return as [7 year note Current Realized Return], 
    10_year_note_current_realized_return as [10 year note Current Realized Return], 
    30_year_bond_current_realized_return as [30 year bond Current Realized Return], 
    cdx_hy_otr_roll_adj_level as [CDX HY OTR ROLL ADJ LEVEL], 
    DATENAME(DW, [asof_date]) AS DAY_OF_WEEK,
    DATEPART(WEEK, [asof_date]) AS WEEK_INDEX,
    YEAR([asof_date]) AS CUR_YEAR
"""

LEVEL_COLUMNS = """
    [asof_date] as Date,
    DATENAME(DW, [asof_date]) AS DAY_OF_WEEK,
    DATEPART(WEEK, [asof_date]) AS WEEK_INDEX,
    YEAR([asof_date]) AS CUR_YEAR,
    [CDX HY OTR ROLL ADJ LEVEL] AS CUR_LEVEL
"""

DATE_COLUMN = '[asof_date]'


def get_timeseries(startdate, enddate):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate)
    sql = SQL.format(ALL_COLUMNS, dateclause)
    df = readdfsql_schema(sql, app=APP)
    return df

def get_level_timeseries(startdate, enddate):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate)
    sql = SQL.format(LEVEL_COLUMNS, dateclause)
    df = readdfsql_schema(sql, app=APP)
    return df
