import logging

import pandas as pd

from common.utils import sqlutil
from common.utils.sqlutil import sql_replace_escape_char
from data_access.beta_attribution.utils import todatestr
from data_access.dbtables import ICE_INDEX_APP, BETA_EST_SUBAPP as APP

SECURITY_TS_HIST_SQL = """
SELECT sec_id AS SEC_ID, 
       attribute AS ATTRIBUTE, 
       asof_date AS ASOFDATE,
       value AS BETA_12M_CDXHY,
       source AS SOURCE
FROM dbo.security_ts_hist
WHERE attribute = 'BETA_WITH_ALPHA_12M_CDXHY'
  AND asof_date = '{0}'
  AND frequency = 'WEEKLY'
  AND source = 'ICE'
"""

ICE_SQL = """
SELECT asof_date AS Expr1, 
       cusip AS Cusip, 
       isin_number AS `ISIN number`, 
       description AS Description, 
       ticker AS Ticker, 
       par_wtd_coupon AS `Par Wtd Coupon`,
       maturity_date AS `Maturity Date`, 
       rating AS Rating, 
       iso_currency_code AS `ISO Currency Code`, 
       iso_country_code AS `ISO Country Code`, 
       industry_lvl1_desc AS `Industry Lvl 1 Desc`, 
       industry_lvl2_desc AS `Industry Lvl 2 Desc`, 
       industry_lvl3_desc AS `Industry Lvl 3 Desc`, 
       industry_lvl4_desc AS `Industry Lvl 4 Desc`, 
       face_value_loc AS `Face Value_LOC`, 
       price AS Price,
       accrued_interest AS `Accrued Interest`, 
       percent_mkt_value AS `% Mkt Value`, 
       percent_mkt_value_prev_mend AS `% MktVal-PrevMend`, 
       cash AS Cash, 
       oas AS OAS, 
       oas_mtd_change AS `OAS MTD Change`, 
       asset_swap_spread AS `Asset Swap Spread`, 
       asset_swap_mtd_change AS `Asset Swap MTD Chg`, 
       effective_yield AS `Effective Yield`, 
       yield_to_worst_sa AS `Yield to Worst (s.a.)`,
       yield_to_maturity_sa AS `Yield to Maturity (s.a.)`, 
       trr_pct_mtd_loc AS `Total Return % MTD_LOC`, 
       excess_return_pct_mtd AS `Excess Return % MTD`, 
       effective_duration AS `Effective Duration`, 
       modified_duration_to_worst_sa AS `Mod. Dur to Worst (s.a.)`, 
       modified_duration_sa AS `Modified Duration (s.a.)`, 
       effective_convexity AS `Effective Convexity`,
       convexity_to_worst_sa AS `Convexity to Worst (s.a.)`, 
       convexity_sa AS `Convexity (s.a.)`,
       DATEDIFF(DAY, asof_date, maturity_date)/365.25 AS Tenor,
       case when ice_index_ticker='C0A0' then 1 else 0 end as Is_IG
FROM dbo.ice_index_all 
WHERE asof_date = '{0}'
  AND `ice_index_ticker` IN ('H0A0', 'C0A0')
  AND `description` <> 'CASH'
  and lower(frequency)='daily'
"""


def get_alpha_factor_analysis_universe(asofdate):
    df_security_ts_hist = sqlutil.readdfsql(SECURITY_TS_HIST_SQL.format(todatestr(asofdate)), app=APP)
    df_ice_index = sqlutil.readdfsql(sql_replace_escape_char(ICE_SQL).format(todatestr(asofdate)), app=ICE_INDEX_APP)

    df = pd.merge(df_security_ts_hist, df_ice_index, left_on="SEC_ID", right_on="Cusip", suffixes=('', '_ice'))

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    asofdate = '2024-07-26'
    df = get_alpha_factor_analysis_universe(asofdate)
    logging.info(df)
