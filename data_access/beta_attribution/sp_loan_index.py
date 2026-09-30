from data_access.beta_attribution import readdfsql_schema, updateschema
from data_access.beta_attribution.utils import get_count_for_table_by_date
from data_access.beta_attribution.utils import tosqllist, get_date_in_clause, get_date_between_clause
from data_access.utils import insert_df_to_db
from data_access.dbtables import SNP_APP as APP

cols = """
    id as `ID`,
    asof_date as `AS_OF_DATE`,
    issuer as `ISSUER`,
    facility_name as `FACILITY_NAME`,
    maturity_date as `MATURITY_DATE`,
    original_amount as `ORIGINAL_AMOUNT`,
    current_outstanding as `CURRENT_OUTSTANDING`,
    libor_spread as `LIBOR_SPREAD`,
    original_libor_spread as `ORIGINAL_LIBOR_SPREAD`,
    launch_date as `LAUNCH_DATE`,
    initial_rating as `INITIAL_RATING`,
    current_rating as `CURRENT_RATING`,
    lcd_broad_industry as `LCD_BROAD_INDUSTRY`,
    narrow_industry as `NARROW_INDUSTRY`,
    weights as `WEIGHTS`,
    market_return as `MARKET_RETURN`,
    interest_return as `INTEREST_RETURN`,
    total_return as `TOTAL_RETURN`,
    currency as `CURRENCY`,
    country as `COUNTRY`,
    region as `REGION`,
    facility_id as `FACILITY_ID`,
    pricing_id as `PRICING_ID`,
    sponsored as `SPONSORED`,
    ccr_rating as `CCR_RATING`,
    libor_floor as `LIBOR_FLOOR`,
    fl_sl as `FL_SL`,
    covenant_light as `COVENANT_LIGHT`,
    load_date as `LOAD_DATE`,
    knowledge_date as `KNOWLEDGE_DATE`
"""

SQL = """
SELECT 
    {0}
FROM 
    dbo.sp_loan_index sll
INNER JOIN 
    dbo.loan_id_mapping lim
    ON sll.facility_id = lim.facility_id   
WHERE
    {1}  
    AND LIM.[loanx_id] in {2}
ORDER BY sll.asof_date
"""

DATE_SELECT_COLUMNS = """
`asof_date` as DATE
"""

DATE_COLUMN = 'asof_date'

TABLE_NAME = 'dbo.sp_loan_index'


def get_count(asofdate):
    df = get_count_for_table_by_date(TABLE_NAME, DATE_COLUMN, asofdate)
    return df


def insertdf(df):
    insert_df_to_db(updateschema(TABLE_NAME), df, app=APP)


def get_timeseries_by_loanxid(startdate, enddate, loanxid):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate)
    sql = SQL.format(cols, dateclause, tosqllist(loanxid))
    df = readdfsql_schema(sql, app=APP)
    return df


def get_date_by_loanxid(datelist, loanxid):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    sql = SQL.format(DATE_SELECT_COLUMNS, dateclause, tosqllist(loanxid))
    df = readdfsql_schema(sql, app=APP)
    return df
