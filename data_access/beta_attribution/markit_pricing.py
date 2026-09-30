from pyspark.sql.functions import to_date

from data_access.beta_attribution import readdfsql
from data_access.beta_attribution.utils import get_count_for_table_by_date
from data_access.beta_attribution.utils import tosqllist, get_date_in_clause, get_date_between_clause
from data_access.dbtables import MARKIT_BOND_PRICING_APP
from common.utils.sqlutil import sql_replace_escape_char

cols = """
    date as [Date],
    batch as [Batch],
    isin as [ISIN],
    cusip as [CUSIP],
    ticker as [Ticker],
    short_name_of_issuer as [Short name of Issuer],
    currency as [Currency] [nvarchar],
    classification as [Classification],
    region as [Region],
    tier as [Tier],
    maturity as [Maturity],
    assumed_maturity as [Assumed Maturity],
    coupon as [Coupon],
    frequency as [Frequency],
    coupon_type as [Coupon Type],
    defaulted as [Defaulted],
    perpetual as [Perpetual],
    fuel_1 as [Fuel 1],
    fuel_2 as [Fuel 2],
    fuel_3 as [Fuel 3],
    fuel_4 as [Fuel 4],
    fuel_5 as [Fuel 5],
    bid_price as [Bid Price],
    mid_price as [Mid Price],
    ask_price as [Ask Price],
    bid_ask_price_spread as [Bid Ask Price Spread],
    accrued_interest as [Accrued Interest],
    dirty_bid_price as [Dirty Bid Price],
    dirty_mid_price as [Dirty Mid Price],
    dirty_ask_price as [Dirty Ask Price],
    bid_ytm as [Bid YTM],
    mid_ytm as [Mid YTM],
    ask_ytm as [Ask YTM],
    bid_ask_yield_spread as [Bid Ask Yield Spread],
    bid_ytw as [Bid YTW],
    mid_ytw as [Mid YTW],
    ask_ytw as [Ask YTW],
    ytw_date as [YTW Date],
    bid_stw as [Bid STW],
    bid_discount_margin as [Bid Discount Margin],
    mid_discount_margin as [Mid Discount Margin],
    ask_discount_margin as [Ask Discount Margin],
    benchmark_isin as [Benchmark ISIN],
    benchmark_cusip as [Benchmark CUSIP],
    benchmark_name as [Benchmark Name],
    benchmark_bid_ytm as [Benchmark Bid YTM],
    benchmark_mid_ytm as [Benchmark Mid YTM],
    benchmark_ask_ytm as [Benchmark Ask YTM],
    spread_vs_benchmark_bid as [Spread vs Benchmark Bid],
    spread_vs_benchmark_mid as [Spread vs Benchmark Mid],
    spread_vs_benchmark_ask as [Spread vs Benchmark Ask],
    bid_asw_spread as [Bid ASW Spread],
    mid_asw_spread as [Mid ASW Spread],
    ask_asw_spread as [Ask ASW Spread],
    bid_z_spread as [Bid Z Spread],
    mid_z_spread as [Mid Z Spread],
    ask_z_spread as [Ask Z Spread],
    g_spread_bid as [gSpread Bid],
    g_spread_mid as [gSpread Mid],
    g_spread_ask as [gSpread Ask],
    i_spread_mid as [iSpread Mid],
    pv01 as [PV01],
    bid_macaulay_duration as [Bid Macaulay Duration],
    mid_macaulay_duration as [Mid Macaulay Duration],
    ask_macaulay_duration as [Ask Macaulay Duration],
    bid_modified_duration as [Bid Modified Duration],
    mid_modified_duration as [Mid Modified Duration],
    ask_modified_duration as [Ask Modified Duration],
    bid_convexity as [Bid Convexity],
    mid_convexity as [Mid Convexity],
    ask_convexity as [Ask Convexity],
    bid_oas as [Bid OAS],
    mid_oas as [Mid OAS],
    ask_oas as [Ask OAS],
    bid_effective_duration as [Bid Effective Duration],
    mid_effective_duration as [Mid Effective Duration],
    ask_effective_duration as [Ask Effective Duration],
    bid_effective_convexity as [Bid Effective Convexity],
    mid_effective_convexity as [Mid Effective Convexity],
    ask_effective_convexity as [Ask Effective Convexity],
    bond_agency_data as [Bond Agency Data],
    iboxx_data as [iBoxx Data],
    markit_composite_data as [Markit Composite Data],
    markit_contribution_data as [Markit Contribution Data],
    quote_data as [Quote Data],
    trace_data as [Trace Data],
    interdealer_broker_data as [Interdealer broker data],
    primary_issuer_curve as [Primary Issuer Curve],
    liquidity_asof as [Liquidity asof],
    liquidity_score as [Liquidity Score],
    depth_merit as [Depth Merit],
    bid_ask_merit as [Bid Ask Merit],
    maturity_merit as [Maturity Merit],
    shadow_liquidity as [Shadow Liquidity],
    total_number_of_merits as [Total number of Merits],
    source_count as [Source Count],
    quotes_dealer_count_1day as [Quotes Dealer Count 1 Day],
    instrument_depth_1day as [Instrument Depth 1 Day],
    quotes_dealer_count_10day as [Quotes Dealer Count 10 Day],
    instrument_depth_10day as [Instrument Depth 10 Day],
    trace_last_trade_date as [Trace Last Trade Date],
    trace_entity_trade_volume_1day as [Trace Entity Trade Volume 1 Day],
    trace_entity_trade_volume_10day as [Trace Entity Trade Volume 10 Day],
    trace_entity_trade_volume_30day as [Trace Entity Trade Volume 30 Day],
    trace_entity_trade_volume_60day as [Trace Entity Trade Volume 60 Day],
    trace_entity_trade_volume_90day as [Trace Entity Trade Volume 90 Day],
    trace_sector_trade_volume_1day as [Trace Sector Trade Volume 1 Day],
    trace_sector_trade_volume_10day as [Trace Sector Trade Volume 10 Day],
    trace_sector_trade_volume_30day as [Trace Sector Trade Volume 30 Day],
    trace_sector_trade_volume_60day as [Trace Sector Trade Volume 60 Day],
    trace_sector_trade_volume_90day as [Trace Sector Trade Volume 90 Day],
    trace_trade_count_1day as [Trace Trade Count 1 Day],
    trace_trade_count_10day as [Trace Trade Count 10 Day],
    trace_trade_count_30day as [Trace Trade Count 30 Day],
    trace_trade_count_60day as [Trace Trade Count 60 Day],
    trace_trade_count_90day as [Trace Trade Count 90 Day],
    trace_trade_volume_1day as [Trace Trade Volume 1 Day],
    trace_trade_volume_10day as [Trace Trade Volume 10 Day],
    trace_trade_volume_30day as [Trace Trade Volume 30 Day],
    trace_trade_volume_60day as [Trace Trade Volume 60 Day],
    trace_trade_volume_90day as [Trace Trade Volume 90 Day],
    trace_last_trade_lot_date as [Trace Last Trade Lot Date],
    set_id as [Set Id],
    actual_cds_bond_basis_mid as [Actual CDS-Bond Basis Mid],
    actual_imm_cds_bond_basis as [Actual IMM CDS-Bond Basis],
    cds_bond_basis_asof as [CDS-Bond Basis asof],
    date_3m as [3M],
    ytm_3m as [3MYTM],
    date_6m as [6M],
    ytm_6m as [6MYTM],
    date_1y as [1Y],
    ytm_1y as [1YYTM],
    date_2y as [2Y],
    ytm_2y as [2YYTM],
    date_3y as [3Y],
    ytm_3y as [3YYTM],
    date_4y as [4Y],
    ytm_4y as [4YYTM],
    date_5y as [5Y],
    ytm_5y as [5YYTM],
    date_6y as [6Y],
    ytm_6y as [6YYTM],
    date_7y as [7Y],
    ytm_7y as [7YYTM],
    date_8y as [8Y],
    ytm_8y as [8YYTM],
    date_9y as [9Y],
    ytm_9y as [9YYTM],
    date_10y as [10Y],
    ytm_10y as [10YYTM],
    date_15y as [15Y],
    ytm_15y as [15YYTM],
    date_20y as [20Y],
    ytm_20y as [20YYTM],
    date_25y as [25Y],
    ytm_25y as [25YYTM],
    date_30y as [30Y],
    ytm_30y as [30YYTM],
    date_40y as [40Y],
    ytm_40y as [40YYTM],
    product_type as [Product Type],
    asof_date as [AsOfdate],
    knowledge_date as [Knowledgedate],
    load_date as [Loaddate],
"""

SQL = """
SELECT 
    {0}
FROM 
    dbo.markit_pricing_data
WHERE
    {1}
    AND (isin IN {2} OR cusip IN {2}) 
ORDER BY isin, cusip, date
"""

RETURN_COLUMNS = """
    isin,
    RTRIM(ticker + ' ' + CONVERT(VARCHAR(4), YEAR(maturity)) + ' ' + STR(coupon, 6, 3)) AS SEC_NAME,
    date as [Date],
    LAG(date) OVER (PARTITION BY isin ORDER BY isin, date) PREV_DATE,
    DATEDIFF(DAY, LAG(date) OVER (PARTITION BY isin ORDER BY isin, date), date) AS DAYS_TO_PREV_DATE,
    [mid_price] AS CUR_PX,
    LAG([mid_price]) OVER (PARTITION BY isin ORDER BY isin, date) PREV_PX,
        (
            [mid_price] - 
            LAG([mid_price]) OVER (PARTITION BY isin ORDER BY isin, date) + 
            coupon / 365.25 * DATEDIFF(DAY, LAG(date) OVER (PARTITION BY isin ORDER BY isin, date), date)
        ) / LAG([mid_price]) OVER (PARTITION BY isin ORDER BY isin, date)
            AS SEC_TOTAL_RET,
    ticker as TICKER,
    short_name_of_issuer as [Short name of Issuer],
    currency as Currency,
    coupon as Coupon,
    coupon_type as [Coupon Type],
    mid_price as [Mid Price],
    accrued_interest as [Accrued Interest],
    dirty_mid_price as [Dirty Mid Price],
    Coupon / 365.25 AS DAILY_ACCRUAL,
    mid_modified_duration as [Mid Modified Duration]
"""

DURATION_COLUMNS = """
    isin as ISIN, cusip as CUSIP, date as [DATE], mid_price AS PX, coupon as COUPON, mid_modified_duration AS DURW
"""

DATE_COLUMN = 'date'

TABLE_NAME = 'dbo.markit_pricing_data'


def get_count(asofdate):
    df = get_count_for_table_by_date(TABLE_NAME, DATE_COLUMN, asofdate, app=MARKIT_BOND_PRICING_APP)
    return df


def _get_formatted_sql(sql, dateclause, isins, columns=cols):
    columns = sql_replace_escape_char(columns)
    formattedsql = sql_replace_escape_char(sql).format(columns,
                                                       dateclause,
                                                       tosqllist(isins))
    return formattedsql


def get_return_timeseries(startdate, enddate, isins):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate)
    sql = _get_formatted_sql(SQL, dateclause, isins, columns=RETURN_COLUMNS)
    df = readdfsql(sql, app=MARKIT_BOND_PRICING_APP)
    return df


def get_timeseries(startdate, enddate, isins):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate)
    sql = _get_formatted_sql(SQL, dateclause, isins)
    df = readdfsql(sql, app=MARKIT_BOND_PRICING_APP)
    return df


def get_duration_timeseries_by_dates(datelist, isins):
    dateclause = get_date_in_clause(DATE_COLUMN, datelist)
    sql = _get_formatted_sql(SQL, dateclause, isins, columns=DURATION_COLUMNS)
    df = readdfsql(sql, app=MARKIT_BOND_PRICING_APP)
    return df
