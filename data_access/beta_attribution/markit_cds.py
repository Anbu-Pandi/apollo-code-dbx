import logging

import pandas as pd

from common.utils import dtutil
from data_access.beta_attribution import readdfsql_schema
from data_access.beta_attribution.utils import get_count_for_table_by_date
from data_access.beta_attribution.utils import get_date_between_clause, get_date_in_clause
from data_access.dbtables import MARKIT_CDS_PRICING_APP
from data_access.utils import tosqllist, get_where_clause, join_where_clauses, tolist
from common.utils.sqlutil import sql_replace_escape_char

cols = """
    date as [Date],
    snap as [Snap],
    ticker as [Ticker],
    short_name as [ShortName],
    red_code as [RedCode],
    sector as [Sector],
    region as [Region],
    country as [Country],
    av_rating as [AvRating],
    implied_rating as [ImpliedRating],
    tier as [Tier],
    ccy as [Ccy],
    doc_clause as [DocClause],
    primary_curve as [PrimaryCurve],
    primary_price_type as [PrimaryPriceType],
    running_coupon as [RunningCoupon],
    primary_coupon as [PrimaryCoupon],
    tenor as [Tenor],
    par_spread as [ParSpread],
    conv_spread as [ConvSpread],
    upfront as [Upfront],
    real_recovery as [RealRecovery],
    assumed_recovery as [AssumedRecovery],
    carried_forward as [CarriedForward],
    composite_depth_5y as [CompositeDepth5y],
    primary_depth_contributed as [PrimaryDepthContributed],
    primary_depth_passed as [PrimaryDepthPassed],
    primary_depth_passed_1w_ma as [PrimaryDepthPassed1WMA],
    primary_depth_passed_1m_ma as [PrimaryDepthPassed1MMA],
    primary_depth_passed_2m_ma as [PrimaryDepthPassed2MMA],
    composite_price_rating as [CompositePriceRating],
    composite_curve_rating as [CompositeCurveRating],
    has_clearing_submissions as [HasClearingSubmissions],
    has_quotes as [HasQuotes],
    has_bank_contributions as [HasBankContributions],
    clearing_submissions_depth_contr as [ClearingSubmissionsDepthContr],
    quotes_depth_contr as [QuotesDepthContr],
    bank_contributions_depth_contr as [BankContributionsDepthContr],
    clearing_submissions_depth_passed as [ClearingSubmissionsDepthPassed],
    quotes_depth_passed as [QuotesDepthPassed],
    bank_contributions_depth_passed as [BankContributionsDepthPassed],
    daily_update_frequency as [DailyUpdateFrequency],
    max_daily_movement as [MaxDailyMovement],
    high_wk52_5y as [52WkHigh5y],
    low_wk52_5y as [52WkLow5y],
    contr_std_dev_passed as [ContrStdDevPassed],
    contr_std_dev_passed_recovery as [ContrStdDevPassedRecovery],
    contr_iq_range_passed as [ContrIQRangePassed],
    contr_iq_range_passed_recovery as [ContrIQRangePassedRecovery],
    contr_10th_pctl_passed as [Contr10thPctlPassed],
    contr_10th_pctl_passed_recovery as [Contr10thPctlPassedRecovery],
    contr_90th_pctl_passed as [Contr90thPctlPassed],
    contr_90th_pctl_passed_recovery as [Contr90thPctlPassedRecovery],
    contr_max_min_range_passed as [ContrMaxMinRangePassed],
    contr_max_min_range_passed_recovery as [ContrMaxMinRangePassedRecovery],
    tier_basis as [TierBasis],
    isda_basis as [ISDABasis],
    ccy_basis as [CcyBasis],
    doc_clause_basis as [DocClauseBasis],
    tier_basis_carried_forward as [TierBasisCarriedForward],
    tier_basis_depth_passed as [TierBasisDepthPassed],
    tier_basis_std_dev_passed as [TierBasisStdDevPassed],
    tier_basis_10th_pctl_passed as [TierBasis10thPctlPassed],
    tier_basis_90th_pctl_passed as [TierBasis90thPctlPassed],
    isda_basis_carried_forward as [ISDABasisCarriedForward],
    isda_basis_depth_passed as [ISDABasisDepthPassed],
    isda_basis_std_dev_passed as [ISDABasisStdDevPassed],
    isda_basis_10th_pctl_passed as [ISDABasis10thPctlPassed],
    isda_basis_90th_pctl_passed as [ISDABasis90thPctlPassed],
    ccy_basis_carried_forward as [CcyBasisCarriedForward],
    ccy_basis_depth_passed as [CcyBasisDepthPassed],
    ccy_basis_std_dev_passed as [CcyBasisStdDevPassed],
    ccy_basis_10th_pctl_passed as [CcyBasis10thPctlPassed],
    ccy_basis_90th_pctl_passed as [CcyBasis90thPctlPassed],
    doc_clause_basis_carried_forward as [DocClauseBasisCarriedForward],
    doc_clause_basis_depth_passed as [DocClauseBasisDepthPassed],
    doc_clause_basis_std_dev_passed as [DocClauseBasisStdDevPassed],
    doc_clause_basis_10th_pctl_passed as [DocClauseBasis10thPctlPassed],
    doc_clause_basis_90th_pctl_passed as [DocClauseBasis90thPctlPassed],
    tenor_basis_depth_passed as [TenorBasisDepthPassed],
    tenor_basis_std_dev_passed as [TenorBasisStdDevPassed],
    tenor_basis_10th_pctl_passed as [TenorBasis10thPctlPassed],
    tenor_basis_90th_pctl_passed as [TenorBasis90thPctlPassed],
    idx_member as [IdxMember],
    dealers_clearing_count_curve as [DealersClearingCountCurve],
    dealers_clearing_count_curve_1w_ma as [DealersClearingCountCurve1WMA],
    dealers_clearing_count_curve_1m_ma as [DealersClearingCountCurve1MMA],
    dealers_clearing_count_curve_2m_ma as [DealersClearingCountCurve2MMA],
    dealers_quotes_count_curve as [DealersQuotesCountCurve],
    dealers_quotes_count_curve_1w_ma as [DealersQuotesCountCurve1WMA],
    dealers_quotes_count_curve_1m_ma as [DealersQuotesCountCurve1MMA],
    dealers_quotes_count_curve_2m_ma as [DealersQuotesCountCurve2MMA],
    quotes_count_curve as [QuotesCountCurve],
    quotes_count_curve_1w_ma as [QuotesCountCurve1WMA],
    quotes_count_curve_1m_ma as [QuotesCountCurve1MMA],
    quotes_count_curve_2m_ma as [QuotesCountCurve2MMA],
    dealers_quotes_count_tenor as [DealersQuotesCountTenor],
    dealers_quotes_count_tenor_1w_ma as [DealersQuotesCountTenor1WMA],
    dealers_quotes_count_tenor_1m_ma as [DealersQuotesCountTenor1MMA],
    dealers_quotes_count_tenor_2m_ma as [DealersQuotesCountTenor2MMA],
    quotes_count_tenor as [QuotesCountTenor],
    quotes_count_tenor_1w_ma as [QuotesCountTenor1WMA],
    quotes_count_tenor_1m_ma as [QuotesCountTenor1MMA],
    quotes_count_tenor_2m_ma as [QuotesCountTenor2MMA],
    dealers_bor_count_curve as [DealersBoRCountCurve],
    dealers_bor_count_curve_1w_ma as [DealersBoRCountCurve1WMA],
    dealers_bor_count_curve_1m_ma as [DealersBoRCountCurve1MMA],
    dealers_bor_count_curve_2m_ma as [DealersBoRCountCurve2MMA],
    upfront_ba_spread_average as [UpfrontBASpreadAverage],
    upfront_ba_spread_average_1w_ma as [UpfrontBASpreadAverage1WMA],
    upfront_ba_spread_average_1m_ma as [UpfrontBASpreadAverage1MMA],
    upfront_ba_spread_average_2m_ma as [UpfrontBASpreadAverage2MMA],
    upfront_ba_spread_high as [UpfrontBASpreadHigh],
    upfront_ba_spread_high_1w_ma as [UpfrontBASpreadHigh1WMA],
    upfront_ba_spread_high_1m_ma as [UpfrontBASpreadHigh1MMA],
    upfront_ba_spread_high_2m_ma as [UpfrontBASpreadHigh2MMA],
    conv_ba_spread_average as [ConvBASpreadAverage],
    conv_ba_spread_average_1w_ma as [ConvBASpreadAverage1WMA],
    conv_ba_spread_average_1m_ma as [ConvBASpreadAverage1MMA],
    conv_ba_spread_average_2m_ma as [ConvBASpreadAverage2MMA],
    conv_ba_spread_high as [ConvBASpreadHigh],
    conv_ba_spread_high_1w_ma as [ConvBASpreadHigh1WMA],
    conv_ba_spread_high_1m_ma as [ConvBASpreadHigh1MMA],
    conv_ba_spread_high_2m_ma as [ConvBASpreadHigh2MMA],
    bid_ask_type_5y as [BidAskType5Y],
    ba_spread_depth_passed as [BASpreadDepthPassed],
    upfront_ba_spread_std_dev_passed as [UpfrontBASpreadStdDevPassed],
    upfront_ba_spread_10th_pctl_passed as [UpfrontBASpread10thPctlPassed],
    upfront_ba_spread_90th_pctl_passed as [UpfrontBASpread90thPctlPassed],
    conv_ba_spread_std_dev_passed as [ConvBASpreadStdDevPassed],
    conv_ba_spread_10th_pctl_passed as [ConvBASpread10thPctlPassed],
    conv_ba_spread_90th_pctl_passed as [ConvBASpread90thPctlPassed],
    estimated_notional as [EstimatedNotional],
    curve_liquidity_score as [CurveLiquidityScore],
    curve_liquidity_score_1w_ma as [CurveLiquidityScore1WMA],
    curve_liquidity_score_1m_ma as [CurveLiquidityScore1MMA],
    curve_liquidity_score_2m_ma as [CurveLiquidityScore2MMA],
    sec4_last_update as [Sec4LastUpdate],
    wkly_gn_sec4 as [WklyGNSec4],
    wkly_gn_sec4_1m_ma as [WklyGNSec41MMA],
    wkly_gn_sec4_2m_ma as [WklyGNSec42MMA],
    wkly_contrct_sec4 as [WklyContrctSec4],
    wkly_contrct_sec4_1m_ma as [WklyContrctSec41MMA],
    wkly_contrct_sec4_2m_ma as [WklyContrctSec42MMA],
    gnx_trd as [GNXTrd],
    gnx_trd_1m_ma as [GNXTrd1MMA],
    gnx_trd_2m_ma as [GNXTrd2MMA],
    tbl6_last_update as [Tbl6LastUpdate],
    wkly_gross_notional as [WklyGrossNotional],
    wkly_gross_notional_1m as [WklyGrossNotional1M],
    wkly_gross_notional_1y as [WklyGrossNotional1Y],
    wkly_net_notional as [WklyNetNotional],
    wkly_net_notional_1m as [WklyNetNotional1M],
    wkly_net_notional_1y as [WklyNetNotional1Y],
    wkly_contr as [WklyContr],
    wkly_contr_1m as [WklyContr1M],
    wkly_contr_1y as [WklyContr1Y],
    credit_dv01 as [CreditDV01],
    risky_pv01 as [RiskyPV01],
    irdv01 as [IRDV01],
    rec01 as [Rec01],
    dp as [DP],
    jtd as [JTD],
    dtz as [DTZ],
    asof_date as [ASOFDATE],
    knowledge_date as [KNOWLEDGEDATE],
    load_date as [LOADDATE],
"""

SQL = """
SELECT {0} 
FROM 
    dbo.MARKIT_CDS
WHERE 
    {1}
    AND red_code in {2}
    AND tenor in {3}
    AND running_coupon in {4}
    AND ccy in {5}
    AND doc_clause in {6}
    AND tier = '{7}'
ORDER BY date, red_code   
"""

UPFRONT_COLUMNS = """
    date as [Date], red_code as [RedCode], tenor as [Tenor], ccy as [CCY], doc_clause as [DocClause], tier as [Tier], 
    running_coupon as [RunningCoupon], upfront as [Upfront]
"""

PARSPREAD_COLUMNS = """
    date as [ASOFDATE], par_spread as [ParSpread]
"""

DATE_COLUMN = 'date'

TABLE_NAME = 'dbo.markit_cds'


def get_count(asofdate):
    df = get_count_for_table_by_date(TABLE_NAME, DATE_COLUMN, asofdate, app=MARKIT_CDS_PRICING_APP)
    return df


def get_upfront_timeseries(dates, redcode, ccy, tenor, tier, docclause, runningcoupon):
    dateclause = get_date_in_clause(DATE_COLUMN, dates, use_databricks=True)
    sql = SQL.format(sql_replace_escape_char(UPFRONT_COLUMNS),
                     dateclause,
                     tosqllist(redcode),
                     tosqllist(tenor),
                     tosqllist(runningcoupon),
                     tosqllist(ccy),
                     tosqllist(docclause),
                     tier)
    df = readdfsql_schema(sql, app=MARKIT_CDS_PRICING_APP)
    return df


def get_parspread_timeseries(startdate, enddate, redcode, ccy, tenor, tier, docclause, runningcoupon):
    dateclause = get_date_between_clause(DATE_COLUMN, startdate, enddate, use_databricks=True)
    sql = SQL.format(sql_replace_escape_char(PARSPREAD_COLUMNS),
                     dateclause,
                     tosqllist(redcode),
                     tosqllist(tenor),
                     tosqllist(runningcoupon),
                     tosqllist(ccy),
                     tosqllist(docclause),
                     tier)
    df = readdfsql_schema(sql, app=MARKIT_CDS_PRICING_APP)
    return df


PRIMARY_INSTRUMENT_CDS_QUERY = '''
select red_code, ccy, running_coupon, doc_clause, 
count(*) as ct
from markit_cds
where primary_curve='Y'
and primary_coupon='Y'
and {}
group by red_code, ccy, running_coupon, doc_clause
order by count(*) desc
'''


def get_primary_cds_instrument_details(dt, red_codes):
    red_code_map = pd.DataFrame([(x, x[:6]) for x in tolist(red_codes) if pd.notnull(x)],
                                columns=['red_code_orig', 'red_code'])
    red_codes_clause = get_where_clause('red_code', red_code_map['red_code'].unique().tolist(), use_databricks=True)
    dt_clause = get_date_between_clause('asof_date', dtutil.dateadd(dt, '-5b'), dt, use_databricks=True)
    where_clause = join_where_clauses(' and ', [dt_clause, red_codes_clause])
    sql = PRIMARY_INSTRUMENT_CDS_QUERY.format(where_clause)
    df = readdfsql_schema(sql, app=MARKIT_CDS_PRICING_APP)

    df = df.drop_duplicates(subset=['red_code', 'ccy', 'running_coupon', 'doc_clause'], keep='first')

    df = pd.merge(df, red_code_map, on='red_code').drop(columns=['red_code']) \
        .rename(columns={'red_code_orig': 'red_code'})
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    dt = '20230927'
    red_codes = ['YZ4CAGAD1', 'YZ4CGWAB4', 'XA435KAB8', 'Y1CI9BAE5']
    df = get_primary_cds_instrument_details(dt, red_codes)

    logging.info(df)
