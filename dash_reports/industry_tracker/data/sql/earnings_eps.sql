WITH univ AS (
  SELECT sub_group, ticker_region, name
  FROM (VALUES {universe_values}) AS t(sub_group, ticker_region, name)
),
univ_dedup AS (
  SELECT u.sub_group, u.ticker_region, MIN(u2.name) AS name
  FROM univ u JOIN univ u2 ON u2.ticker_region = u.ticker_region
  GROUP BY u.sub_group, u.ticker_region
),
ids AS (
  SELECT
    ud.sub_group, ud.ticker_region, ud.name AS proper_name,
    SPLIT_PART(ud.ticker_region, '-', 1) AS ticker,
    sm.fsym_id AS fsym_company_id
  FROM univ_dedup ud
    JOIN sym_v1.sym_ticker_region tr ON tr.ticker_region = ud.ticker_region
    JOIN fe_v4.fe_sec_map sm ON sm.fsym_id = tr.fsym_id
),
eps_act AS (
  SELECT
    a.fsym_id,
    a.fe_fp_end,
    a.actual_value,
    a.report_date,
    a.publication_date,
    a.currency
  FROM fe_v4.fe_basic_act_qf a
  WHERE a.fsym_id IN (SELECT fsym_company_id FROM ids)
    AND a.fe_item = 'EPS'
    AND a.actual_value IS NOT NULL
    AND a.fe_fp_end >= DATEADD(year, -1, CURRENT_DATE())
  QUALIFY ROW_NUMBER() OVER (PARTITION BY a.fsym_id ORDER BY a.fe_fp_end DESC) = 1
),
eps_est AS (
  SELECT
    e.fsym_id,
    e.fe_fp_end,
    e.fe_mean,
    e.fe_high,
    e.fe_low,
    e.fe_num_est,
    e.currency AS estimate_currency,
    e.cons_start_date
  FROM fe_v4.fe_basic_conh_qf e
    JOIN eps_act ea ON ea.fsym_id = e.fsym_id AND ea.fe_fp_end = e.fe_fp_end
  WHERE e.fe_item = 'EPS'
    AND e.cons_start_date <= ea.publication_date
  QUALIFY ROW_NUMBER() OVER (PARTITION BY e.fsym_id, e.fe_fp_end
                             ORDER BY e.fe_per_rel DESC, e.cons_start_date DESC) = 1
),
sales_act AS (
  SELECT
    a.fsym_id,
    a.fe_fp_end,
    a.actual_value AS sales_actual_raw,
    a.currency AS sales_currency
  FROM fe_v4.fe_basic_act_qf a
    JOIN eps_act ea ON ea.fsym_id = a.fsym_id AND ea.fe_fp_end = a.fe_fp_end
  WHERE a.fe_item = 'SALES'
    AND a.actual_value IS NOT NULL
),
sales_est AS (
  SELECT
    e.fsym_id,
    e.fe_fp_end,
    e.fe_mean AS sales_estimate_raw,
    e.fe_high AS sales_high_raw,
    e.fe_low AS sales_low_raw,
    e.fe_num_est AS sales_estimate_count
  FROM fe_v4.fe_basic_conh_qf e
    JOIN eps_act ea ON ea.fsym_id = e.fsym_id AND ea.fe_fp_end = e.fe_fp_end
  WHERE e.fe_item = 'SALES'
    AND e.cons_start_date <= ea.publication_date
  QUALIFY ROW_NUMBER() OVER (PARTITION BY e.fsym_id, e.fe_fp_end
                             ORDER BY e.fe_per_rel DESC, e.cons_start_date DESC) = 1
)
SELECT
  i.ticker_region,
  i.ticker,
  i.sub_group,
  ea.fe_fp_end AS fiscal_period_end,
  ea.publication_date AS eps_pub_date,
  ea.report_date AS eps_report_date,
  ea.currency,
  ROUND(ea.actual_value, 3) AS eps_actual,
  ROUND(ee.fe_mean, 3) AS eps_estimate_mean,
  ROUND(ee.fe_high, 3) AS eps_estimate_high,
  ROUND(ee.fe_low, 3) AS eps_estimate_low,
  ee.fe_num_est AS eps_estimate_count,
  ROUND(ea.actual_value - ee.fe_mean, 3) AS eps_surprise_abs,
  ROUND((ea.actual_value - ee.fe_mean) / NULLIF(ABS(ee.fe_mean), 0) * 100, 1) AS eps_surprise_pct,
  ROUND(sa.sales_actual_raw, 1) AS sales_actual,
  ROUND(se.sales_estimate_raw, 1) AS sales_estimate_mean,
  ROUND(se.sales_high_raw, 1) AS sales_estimate_high,
  ROUND(se.sales_low_raw, 1) AS sales_estimate_low,
  se.sales_estimate_count,
  sa.sales_currency,
  ROUND(sa.sales_actual_raw - se.sales_estimate_raw, 1) AS sales_surprise_abs,
  ROUND((sa.sales_actual_raw - se.sales_estimate_raw) / NULLIF(ABS(se.sales_estimate_raw), 0) * 100, 1) AS sales_surprise_pct
FROM ids i
  JOIN eps_act ea ON ea.fsym_id = i.fsym_company_id
  LEFT JOIN eps_est ee ON ee.fsym_id = i.fsym_company_id AND ee.fe_fp_end = ea.fe_fp_end
  LEFT JOIN sales_act sa ON sa.fsym_id = i.fsym_company_id AND sa.fe_fp_end = ea.fe_fp_end
  LEFT JOIN sales_est se ON se.fsym_id = i.fsym_company_id AND se.fe_fp_end = ea.fe_fp_end
ORDER BY ea.fe_fp_end DESC
