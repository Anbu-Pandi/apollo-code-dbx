WITH univ AS (
  SELECT sub_group, ticker_region, name
  FROM (VALUES {universe_values}) AS t(sub_group, ticker_region, name)
),
ids AS (
  SELECT u.sub_group, u.ticker_region, u.name,
         SPLIT_PART(u.ticker_region, '-', 1) AS ticker,
         sm.fsym_id AS fsym_company_id
  FROM univ u
    JOIN sym_v1.sym_ticker_region tr ON tr.ticker_region = u.ticker_region
    JOIN ff_v3.ff_sec_map sm ON sm.fsym_id = tr.fsym_id
),
qraw AS (
  SELECT
    i.sub_group, i.name, i.ticker, i.fsym_company_id,
    b.date AS qtr_end,
    CONCAT(YEAR(b.date), 'Q', QUARTER(b.date)) AS period,
    b.ff_sales, b.ff_gross_inc, b.ff_oper_inc, b.ff_oper_lease_repay,
    d.ff_ebitda_oper_mgn, d.ff_ebit_oper_mgn, d.ff_roea, d.ff_roic,
    d.ff_debt_eq, d.ff_net_cf_debt,
    d.ff_entrpr_val_ebitda_oper AS ev_ebitda_ltm,
    d.ff_entrpr_val_sales AS ev_sales,
    d.ff_pe_dil AS pe_dil, d.ff_div_yld AS div_yld,
    COALESCE(fx.exch_rate_usd, CASE WHEN b.currency='USD' THEN 1 END) AS fx_usd,
    (b.ff_com_shs_out * b.ff_price_close_fp)
      * COALESCE(fx.exch_rate_usd, CASE WHEN b.currency='USD' THEN 1 END) AS mkt_cap,
    (COALESCE(b.ff_debt, 0) - COALESCE(b.ff_cash_st, b.ff_cash_only, 0))
      * COALESCE(fx.exch_rate_usd, CASE WHEN b.currency='USD' THEN 1 END) AS net_debt_usd,
    (b.ff_sales * d.ff_ebitda_oper_mgn / 100.0) AS ebitda_oper
  FROM ids i
    JOIN ff_v3.ff_basic_qf b ON b.fsym_id = i.fsym_company_id
    JOIN ff_v3.ff_advanced_der_qf d ON d.fsym_id = i.fsym_company_id AND d.date = b.date
    LEFT JOIN ref_v2.econ_fx_rates_usd fx
      ON fx.iso_currency = b.currency AND fx.exch_date = b.date
  WHERE b.date >= DATEADD(year, -12, CURRENT_DATE())
),
q AS (
  SELECT *,
    SUM(ebitda_oper) OVER (PARTITION BY fsym_company_id ORDER BY qtr_end
                           ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS ebitda_ltm,
    CASE WHEN ev_ebitda_ltm IS NOT NULL THEN
      ev_ebitda_ltm
        * SUM(ebitda_oper) OVER (PARTITION BY fsym_company_id ORDER BY qtr_end
                                 ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) END AS ent_val
  FROM qraw
),
fy1 AS (
  SELECT q.ticker, q.qtr_end,
    af.fe_mean AS ebitda_fy1, af.fe_fp_end AS fy1_end
  FROM q
    JOIN fe_v4.fe_advanced_conh_af af
      ON af.fsym_id = q.fsym_company_id
     AND af.fe_item = 'EBITDA' AND af.fe_per_rel = 1
     AND af.cons_start_date <= q.qtr_end
     AND COALESCE(af.cons_end_date, DATE '9999-12-31') > q.qtr_end
  QUALIFY ROW_NUMBER() OVER (PARTITION BY q.ticker, q.qtr_end ORDER BY af.cons_start_date DESC) = 1
),
fy2 AS (
  SELECT q.ticker, q.qtr_end, af.fe_mean AS ebitda_fy2
  FROM q
    JOIN fe_v4.fe_advanced_conh_af af
      ON af.fsym_id = q.fsym_company_id
     AND af.fe_item = 'EBITDA' AND af.fe_per_rel = 2
     AND af.cons_start_date <= q.qtr_end
     AND COALESCE(af.cons_end_date, DATE '9999-12-31') > q.qtr_end
  QUALIFY ROW_NUMBER() OVER (PARTITION BY q.ticker, q.qtr_end ORDER BY af.cons_start_date DESC) = 1
),
ntm AS (
  SELECT f1.ticker, f1.qtr_end,
    LEAST(GREATEST(DATEDIFF(MONTH, f1.qtr_end, f1.fy1_end) / 12.0, 0), 1) * f1.ebitda_fy1
    + (1 - LEAST(GREATEST(DATEDIFF(MONTH, f1.qtr_end, f1.fy1_end) / 12.0, 0), 1)) * f2.ebitda_fy2
      AS ebitda_ntm
  FROM fy1 f1
    JOIN fy2 f2 ON f2.ticker = f1.ticker AND f2.qtr_end = f1.qtr_end
),
qn AS (
  SELECT q.*, n.ebitda_ntm,
    CASE WHEN n.ebitda_ntm > 0 THEN q.ent_val / n.ebitda_ntm END AS ev_ebitda_ntm_co
  FROM q LEFT JOIN ntm n ON n.ticker = q.ticker AND n.qtr_end = q.qtr_end
  WHERE q.qtr_end >= DATEADD(year, -10, CURRENT_DATE())
),
agg AS (
  SELECT
    sub_group, period,
    MAX(qtr_end) AS qtr_end,
    COUNT(DISTINCT ticker) AS n_cos,
    MEDIAN(mkt_cap) / 1000.0 AS mkt_cap_m,
    MEDIAN(net_debt_usd) / 1000.0 AS net_debt_m,
    MEDIAN(mkt_cap + COALESCE(net_debt_usd, 0)) / 1000.0 AS tev_m,
    MEDIAN((ff_gross_inc / NULLIF(ff_sales, 0)) * 100) AS gross_mgn,
    MEDIAN(ff_ebitda_oper_mgn) AS ebitda_mgn,
    MEDIAN(ff_ebit_oper_mgn) AS ebit_mgn,
    MEDIAN(ff_roea) AS roe,
    MEDIAN(ff_roic) AS roic,
    MEDIAN(ff_debt_eq) AS de,
    MEDIAN(ff_net_cf_debt) AS net_debt_cf,
    MEDIAN(ev_ebitda_ltm) AS ev_ebitda_ltm,
    MEDIAN(ev_ebitda_ntm_co) AS ev_ebitda_ntm,
    MEDIAN(ev_sales) AS ev_sales,
    MEDIAN(pe_dil) AS pe_dil,
    MEDIAN(div_yld) AS div_yld
  FROM qn
  GROUP BY sub_group, period
)
SELECT
  sub_group, period, qtr_end, n_cos,
  mkt_cap_m, net_debt_m, tev_m,
  gross_mgn, ebitda_mgn, ebit_mgn, roe, roic, de, net_debt_cf,
  ev_ebitda_ltm, ev_ebitda_ntm, ev_sales, pe_dil, div_yld,
  AVG(mkt_cap_m) OVER (PARTITION BY sub_group) AS mkt_cap_10y,
  AVG(net_debt_m) OVER (PARTITION BY sub_group) AS net_debt_10y,
  AVG(tev_m) OVER (PARTITION BY sub_group) AS tev_10y,
  AVG(gross_mgn) OVER (PARTITION BY sub_group) AS gross_mgn_10y,
  AVG(ebitda_mgn) OVER (PARTITION BY sub_group) AS ebitda_mgn_10y,
  AVG(ebit_mgn) OVER (PARTITION BY sub_group) AS ebit_mgn_10y,
  AVG(roe) OVER (PARTITION BY sub_group) AS roe_10y,
  AVG(roic) OVER (PARTITION BY sub_group) AS roic_10y,
  AVG(de) OVER (PARTITION BY sub_group) AS de_10y,
  AVG(net_debt_cf) OVER (PARTITION BY sub_group) AS net_debt_cf_10y,
  AVG(ev_ebitda_ltm) OVER (PARTITION BY sub_group) AS ev_ebitda_ltm_10y,
  AVG(ev_ebitda_ntm) OVER (PARTITION BY sub_group) AS ev_ebitda_ntm_10y,
  AVG(ev_sales) OVER (PARTITION BY sub_group) AS ev_sales_10y,
  AVG(pe_dil) OVER (PARTITION BY sub_group) AS pe_10y,
  AVG(div_yld) OVER (PARTITION BY sub_group) AS div_yld_10y
FROM agg
ORDER BY sub_group, qtr_end
