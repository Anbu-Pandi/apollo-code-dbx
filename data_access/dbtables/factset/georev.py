import logging

import pandas as pd

from common.utils import dfutil, sqlutil
from common.utils import dtutil
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.utils import normalize_df
from data_access.utils import tosqllist

GEO_SQL = '''
WITH date_param AS (
  SELECT '{}'::DATE AS dt
),
isin AS (
  SELECT DISTINCT i.isin, i.fsym_id, sc.FSYM_PRIMARY_EQUITY_ID, primary_sc.FSYM_REGIONAL_ID, tr.ticker_region, se.FACTSET_ENTITY_ID
FROM FACTSET_DB.sym_v1.sym_isin i
LEFT JOIN FACTSET_DB.sym_v1.sym_credit_parent c ON i.FSYM_ID = c.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_sec_entity parent_se  ON i.FSYM_ID = parent_se.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.SYM_SEC_ENTITY se ON COALESCE(c.FACTSET_CRED_PARENT_ENTITY_ID, parent_se.FACTSET_ENTITY_ID) = se.FACTSET_ENTITY_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage sc ON sc.FSYM_ID = se.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage primary_sc ON sc.FSYM_PRIMARY_EQUITY_ID = primary_sc.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region tr ON tr.FSYM_ID = primary_sc.FSYM_REGIONAL_ID
WHERE ISIN IN {}
),
latest_reports AS (
  SELECT r.factset_entity_id,
         r.period_start_date,
         r.period_end_date
  FROM gr_v2.gr_report r
  INNER JOIN (
    SELECT factset_entity_id, MAX(period_end_date) AS max_period_end_date
    FROM gr_v2.gr_report
    CROSS JOIN date_param
    WHERE dt BETWEEN start_date AND COALESCE(end_date, CURRENT_DATE())
    GROUP BY factset_entity_id
  ) latest ON r.factset_entity_id = latest.factset_entity_id
  AND r.period_end_date = latest.max_period_end_date
),
georev AS (
  SELECT DISTINCT
    i.isin,
    e.entity_proper_name,
    lr.period_start_date,
    lr.period_end_date,
    cr.country_name,
    cr.path,
    ri.est_pct,
    ri.certainty_rank,
    ri.conf,
    ri.certainty_class
  FROM latest_reports lr
  JOIN gr_v2.gr_report r ON lr.factset_entity_id = r.factset_entity_id
  AND lr.period_end_date = r.period_end_date
  JOIN gr_v2.gr_item ri ON ri.report_id = r.report_id
  JOIN gr_v2.gr_country_region cr ON ri.iso_country = cr.iso_country
  JOIN FACTSET_DB.SYM_V1.SYM_ENTITY e ON r.factset_entity_id = e.factset_entity_id
  JOIN isin i ON i.factset_entity_id = r.factset_entity_id
  CROSS JOIN date_param
  WHERE dt BETWEEN r.start_date AND COALESCE(r.end_date, CURRENT_DATE())
  AND dt BETWEEN ri.start_date AND COALESCE(ri.end_date, CURRENT_DATE())
  AND dt BETWEEN cr.effective_start_date AND COALESCE(cr.effective_end_date, CURRENT_DATE())
),
ranked_data AS (
  SELECT 
    *,
    ROW_NUMBER() OVER (PARTITION BY isin, period_end_date ORDER BY est_pct DESC) as rank,
    SUM(est_pct) OVER (PARTITION BY isin, period_end_date ORDER BY est_pct DESC) AS cumulative_pct
  FROM georev
),
countries_for_50_percent AS (
  SELECT 
    isin, 
    period_end_date, 
    COUNT(*) AS num_countries_for_50_percent_revenue
  FROM (
    SELECT 
      isin, 
      period_end_date, 
      cumulative_pct,
      LAG(cumulative_pct) OVER (PARTITION BY isin, period_end_date ORDER BY est_pct DESC) AS prev_cumulative_pct
    FROM ranked_data
  )
  WHERE cumulative_pct >= 50 AND (prev_cumulative_pct IS NULL OR prev_cumulative_pct < 50)
  GROUP BY isin, period_end_date
),
aggregated_data AS (
  SELECT
    rd.isin,
    rd.entity_proper_name,
    rd.period_start_date,
    rd.period_end_date,
    MAX(CASE WHEN rd.rank = 1 THEN rd.country_name END) AS top_1_country,
    MAX(CASE WHEN rd.rank = 2 THEN rd.country_name END) AS top_2_country,
    MAX(CASE WHEN rd.rank = 3 THEN rd.country_name END) AS top_3_country,
    MAX(CASE WHEN rd.rank = 4 THEN rd.country_name END) AS top_4_country,
    MAX(CASE WHEN rd.rank = 5 THEN rd.country_name END) AS top_5_country,
    MAX(CASE WHEN rd.rank = 1 THEN rd.est_pct END) AS top_1_percentage,
    MAX(CASE WHEN rd.rank = 2 THEN rd.est_pct END) AS top_2_percentage,
    MAX(CASE WHEN rd.rank = 3 THEN rd.est_pct END) AS top_3_percentage,
    MAX(CASE WHEN rd.rank = 4 THEN rd.est_pct END) AS top_4_percentage,
    MAX(CASE WHEN rd.rank = 5 THEN rd.est_pct END) AS top_5_percentage,
    MAX(CASE WHEN rd.rank = 1 THEN rd.conf END) AS top_1_confidence,
    MAX(CASE WHEN rd.rank = 2 THEN rd.conf END) AS top_2_confidence,
    MAX(CASE WHEN rd.rank = 3 THEN rd.conf END) AS top_3_confidence,
    MAX(CASE WHEN rd.rank = 4 THEN rd.conf END) AS top_4_confidence,
    MAX(CASE WHEN rd.rank = 5 THEN rd.conf END) AS top_5_confidence,
    SUM(POWER(rd.est_pct, 2)) AS hhi_index,
    SUM(CASE WHEN rd.rank BETWEEN 6 AND 10 THEN rd.est_pct END) AS top_6_10_sum_percentage,
    SUM(CASE WHEN rd.rank <= 10 THEN rd.est_pct END) AS top_10_sum_percentage,
    c50.num_countries_for_50_percent_revenue,
    SUM(rd.est_pct) AS total_percentage
  FROM ranked_data rd
  LEFT JOIN countries_for_50_percent c50 ON rd.isin = c50.isin AND rd.period_end_date = c50.period_end_date
  GROUP BY rd.isin, rd.entity_proper_name, rd.period_start_date, rd.period_end_date, c50.num_countries_for_50_percent_revenue
)
SELECT
  ad.isin,
  ad.entity_proper_name,
  ad.period_start_date,
  ad.period_end_date,
  ad.top_1_country,
  ad.top_2_country,
  ad.top_3_country,
  ad.top_4_country,
  ad.top_5_country,
  ad.top_1_percentage,
  ad.top_2_percentage,
  ad.top_3_percentage,
  ad.top_4_percentage,
  ad.top_5_percentage,
  ad.top_1_confidence,
  ad.top_2_confidence,
  ad.top_3_confidence,
  ad.top_4_confidence,
  ad.top_5_confidence,
  ad.hhi_index,
  ad.top_6_10_sum_percentage,
  ad.num_countries_for_50_percent_revenue,
  ad.top_10_sum_percentage,
  (ad.total_percentage - ad.top_10_sum_percentage) AS top_10_to_other_sum_percentage,
FROM aggregated_data ad
JOIN ranked_data rd ON ad.isin = rd.isin AND ad.period_end_date = rd.period_end_date
GROUP BY ALL
ORDER BY ad.isin, ad.period_end_date;
'''


def get_georev_from_isin(dt, isins):
    dt = dtutil.todate(dt)
    dfs = []

    for _isins in dfutil.chunks_list(dfutil.tolist(isins), 10_000):
        _df = sqlutil.readdfsql(GEO_SQL.format(dt, tosqllist(_isins)), app=APP)
        dfs.append(_df)
    df = pd.concat(dfs)
    df = normalize_df(df)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    isins = ['US037833EW60', 'DE0007236101', 'JP3435000009']
    df = get_georev_from_isin('2024-10-10', isins)
    logging.info(df.to_string())
