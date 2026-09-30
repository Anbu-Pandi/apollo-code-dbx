from data_access.beta_attribution import readdfsql_schema, updateschema
from data_access.beta_attribution.utils import get_count_for_table_by_date
from data_access.beta_attribution.utils import tosqllist, get_date_between_clause, get_date_in_clause
from data_access.dbtables import ICE_INDEX_APP
from data_access.utils import insert_df_to_db

TABLE_NAME = 'dbo.ice_index_all'

cols = """
    accrued_interest as `Accrued Interest`,
    announce_date as `Announce Date`,
    asof_date as Asofdate,
    asset_swap_mtd_change as `Asset Swap MTD Chg`,
    asset_swap_spread as `Asset Swap Spread`,
    cash as `Cash`,
    convexity_sa as `Convexity (s.a.)`,
    convexity_to_worst_sa as `Convexity to Worst (s.a.)`,
    credit_spread as `Credit Spread`,
    face_value_loc as `Current Face Value`,
    current_period_coupon as `Current Period Coupon`,
    current_period_coupon / price * 100 as `Current Yield`,
    cusip as Cusip,
    description as Description,
    effective_convexity as `Effective Convexity`,
    effective_duration as `Effective Duration`,
    effective_yield as `Effective Yield`,
    equity_div_yield as `Equity Div Yield`,
    equity_market_cap as `Equity MKT Cap`,
    excess_return_pct_mtd as `Excess Return % MTD`,
    face_value_loc as `Face Value_LOC`,
    factor as `Factor`,
    full_market_value_loc as `Full Market Value`,
    ice_index_ticker as `ICE Index Ticker`,
    industry_lvl1_desc as `Industry Lvl 1 Desc`,
    industry_lvl2_desc as `Industry Lvl 2 Desc`,
    industry_lvl3_desc as `Industry Lvl 3 Desc`,
    industry_lvl4_desc as `Industry Lvl 4 Desc`,
    isin_number as `ISIN number`,
    iso_country_code as `ISO Country Code`,
    iso_currency_code as `ISO Currency Code`,
    knowledge_date as `Knowledgedate`,
    libor_oas as `LiborOAS`,
    load_date as `Loaddate`,
    maturity_wal as `Maturity / WAL`,
    maturity_date as `Maturity Date`,
    modified_duration_to_worst_sa as `Mod. Dur to Worst (s.a.)`,
    modified_duration_sa as `Modified Duration (s.a.)`,
    oas as `OAS`,
    oas_mtd_change as `OAS MTD Change`,
    par_wtd_coupon as `Par Wtd Coupon`,
    percent_mkt_value_prev_mend as `% MktVal-PrevMend`,
    percent_mkt_value as `% Mkt Value`,
    price as `Price`,
    prr_pct_mtd as `Price Return % MTD`,
    rating as `Rating`,
    region as `Region`,
    spread_duration as `Spread Duration`,
    subordination_type as `Subordination Type`,
    ticker as Ticker,
    trr_pct_1day_loc as `Total Return % 1-day_LOC`, 
    trr_pct_mtd_loc as `Total Return % MTD_LOC`, 
    years_to_worst as `Years To Worst`,
    yield_to_maturity_conv as `Yield to Maturity (conv.)`,
    yield_to_maturity_sa as `Yield to Maturity (s.a.)`,
    yield_to_worst_conv as `Yield to Worst (conv.)`,
    yield_to_worst_sa as `Yield to Worst (s.a.)`
"""

SQL = """
SELECT {0}
FROM dbo.ice_index_all
WHERE  
    {1}
    AND `ice_index_ticker` IN {2}       
    and lower(frequency)='daily'
"""

DURATION_COLUMNS = """
    asof_date AS `DATE`,
    `isin_number` AS ISIN,
    cusip AS CUSIP,
    price AS PX,
    `par_wtd_coupon` AS COUPON,
    `modified_duration_to_worst_sa` AS DURW
"""

DATE_COLUMN = 'asof_date'

CUSIP_CLAUSE = """
    AND (cusip in {0} or isin_number in {0})
"""


def _get_cusip_clause(cusips=None):
    cusipclause = ""
    if cusips is not None:
        cusipclause = CUSIP_CLAUSE.format(tosqllist(cusips))
    return cusipclause


def get_timeseries(fromdate, todate, index_ticker, cusips=None):
    dateclause = get_date_between_clause(DATE_COLUMN, fromdate, todate)
    cusipclause = _get_cusip_clause(cusips)
    sql = SQL.format(DURATION_COLUMNS + ', ' + cols, dateclause + cusipclause, tosqllist(index_ticker))
    df = readdfsql_schema(sql, app=ICE_INDEX_APP)
    return df


def get_timeseries_by_dates(dates, index_ticker, cusips=None):
    dateclause = get_date_in_clause(DATE_COLUMN, dates)
    cusipclause = _get_cusip_clause(cusips)
    sql = SQL.format(DURATION_COLUMNS + ', ' + cols, dateclause + cusipclause, tosqllist(index_ticker))
    df = readdfsql_schema(sql, app=ICE_INDEX_APP)
    return df


def get_duration_timeseries_by_dates(datelist, index_ticker, cusips=None):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    cusipclause = _get_cusip_clause(cusips)
    sql = SQL.format(DURATION_COLUMNS, dateclause + cusipclause, tosqllist(index_ticker))
    df = readdfsql_schema(sql, app=ICE_INDEX_APP)
    return df


def get_count(asofdate, indexticker):
    df = get_count_for_table_by_date(TABLE_NAME, DATE_COLUMN, asofdate,
                                     additionalwhere=f"`ice_index_ticker` = '{indexticker}' and lower(frequency)='daily'",
                                     app=ICE_INDEX_APP)
    return df


def insertdf(df):
    insert_df_to_db(updateschema(TABLE_NAME), df, app=ICE_INDEX_APP)


def get_ice_index_ticker():
    sql = f"select distinct ice_index_ticker from ice_index_all"
    df = readdfsql_schema(sql, app=ICE_INDEX_APP)
    return df['ice_index_ticker'].to_list()
