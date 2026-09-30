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
    sc.fsym_primary_listing_id, sm.fsym_id AS fsym_company_id
  FROM univ_dedup ud
    JOIN sym_v1.sym_ticker_region tr ON tr.ticker_region = ud.ticker_region
    JOIN fe_v4.fe_sec_map sm ON sm.fsym_id = tr.fsym_id
    JOIN sym_v1.sym_coverage sc ON sc.fsym_id = sm.fsym_id
),
ind AS (
  SELECT ud.ticker_region, im.factset_industry_desc AS industry
  FROM univ_dedup ud
    JOIN sym_v1.sym_ticker_region tr ON tr.ticker_region = ud.ticker_region
    JOIN sym_v1.sym_coverage sc2
      ON sc2.fsym_regional_id = tr.fsym_id AND sc2.fsym_id LIKE '%-S'
    JOIN sym_v1.sym_sec_entity sse ON sse.fsym_id = sc2.fsym_id
    JOIN sym_v1.sym_entity_sector es ON es.factset_entity_id = sse.factset_entity_id
    JOIN ref_v2.factset_industry_map im ON im.factset_industry_code = es.industry_code
),
px_raw AS (
  SELECT
    i.ticker_region, i.proper_name, p.price_date, p.price,
    p.price_high, p.price_low, p.currency AS price_ccy
  FROM fgp_v1.fgp_global_prices p
    JOIN ids i ON i.fsym_primary_listing_id = p.fsym_id
  WHERE p.price_date >= DATEADD(year, -1, CURRENT_DATE())
),
px_latest AS (
  SELECT ticker_region, proper_name, price AS price_now,
         price_date AS latest_date, price_ccy
  FROM px_raw
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_region ORDER BY price_date DESC) = 1
),
px_1m AS (
  SELECT px.ticker_region, px.price AS price_1m
  FROM px_raw px JOIN px_latest pl ON pl.ticker_region = px.ticker_region
  WHERE px.price_date <= DATEADD(day, -30, pl.latest_date)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY px.ticker_region ORDER BY px.price_date DESC) = 1
),
px_3m AS (
  SELECT px.ticker_region, px.price AS price_3m
  FROM px_raw px JOIN px_latest pl ON pl.ticker_region = px.ticker_region
  WHERE px.price_date <= DATEADD(month, -3, pl.latest_date)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY px.ticker_region ORDER BY px.price_date DESC) = 1
),
px_6m AS (
  SELECT px.ticker_region, px.price AS price_6m
  FROM px_raw px JOIN px_latest pl ON pl.ticker_region = px.ticker_region
  WHERE px.price_date <= DATEADD(month, -6, pl.latest_date)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY px.ticker_region ORDER BY px.price_date DESC) = 1
),
px_1y AS (
  SELECT px.ticker_region, px.price AS price_1y
  FROM px_raw px JOIN px_latest pl ON pl.ticker_region = px.ticker_region
  WHERE px.price_date <= DATEADD(year, -1, pl.latest_date)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY px.ticker_region ORDER BY px.price_date DESC) = 1
),
px_52w AS (
  SELECT ticker_region, MAX(price_high) AS hi52, MIN(price_low) AS lo52
  FROM px_raw GROUP BY ticker_region
),
fx_latest AS (
  SELECT iso_currency, exch_rate_usd
  FROM ref_v2.econ_fx_rates_usd
  QUALIFY ROW_NUMBER() OVER (PARTITION BY iso_currency ORDER BY exch_date DESC) = 1
),
fund_ltm AS (
  SELECT
    b.fsym_id, b.ff_sales, b.ff_net_income, b.ff_com_shs_out,
    a.ff_sell_exp, a.ff_oper_lease_exp AS lease_exp_annual,
    a.ff_capex_fix, a.ff_capex_oth,
    bd.ff_ebitda_oper AS ff_ebitda, bd.ff_net_debt, bd.ff_gross_mgn,
    d.ff_ebitda_oper_mgn, d.ff_ebit_oper_mgn, d.ff_sga_sales,
    d.ff_sales_gr, d.ff_fcf_yld, d.ff_div_yld, d.ff_eps_dil_aft_xord,
    b.ff_oper_inc
  FROM ff_v3.ff_basic_af b
    LEFT JOIN ff_v3.ff_advanced_af a ON a.fsym_id = b.fsym_id AND a.date = b.ff_fiscal_date
    LEFT JOIN ff_v3.ff_basic_der_af bd ON bd.fsym_id = b.fsym_id AND bd.date = b.ff_fiscal_date
    LEFT JOIN ff_v3.ff_advanced_der_af d ON d.fsym_id = b.fsym_id AND d.date = b.ff_fiscal_date
  WHERE b.fsym_id IN (SELECT fsym_company_id FROM ids)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY b.fsym_id ORDER BY b.ff_fiscal_date DESC) = 1
),
lease_ltm AS (
  SELECT a.fsym_id, a.ff_oper_lease_exp AS lease_exp_ltm
  FROM ff_v3.ff_advanced_ltm a
  WHERE a.fsym_id IN (SELECT fsym_company_id FROM ids)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY a.fsym_id ORDER BY a.date DESC) = 1
),
fund_prior AS (
  SELECT b.fsym_id, b.ff_sales AS prior_sales
  FROM ff_v3.ff_basic_af b
  WHERE b.fsym_id IN (SELECT fsym_company_id FROM ids) AND b.ff_sales IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY b.fsym_id ORDER BY b.ff_fiscal_date DESC) = 2
),
fund_hist AS (
  SELECT
    bd2.fsym_id,
    AVG(d2.ff_ebitda_oper_mgn) AS avg_ebitda_margin,
    AVG(bd2.ff_gross_mgn) AS avg_gross_margin,
    AVG(d2.ff_sga_sales) AS avg_sga_pct,
    AVG(bd2.ff_debt_ebitda_oper) AS avg_gross_leverage,
    AVG(bd2.ff_ebitda_oper) AS avg_ebitda_10y_local
  FROM (
    SELECT bd.*, ROW_NUMBER() OVER (PARTITION BY bd.fsym_id ORDER BY bd.date DESC) AS rn
    FROM ff_v3.ff_basic_der_af bd
    WHERE bd.fsym_id IN (SELECT fsym_company_id FROM ids)
  ) bd2
    LEFT JOIN ff_v3.ff_advanced_der_af d2 ON d2.fsym_id = bd2.fsym_id AND d2.date = bd2.date
  WHERE bd2.rn <= 11
  GROUP BY bd2.fsym_id
),
est_all AS (
  SELECT af.fsym_id, af.fe_item, af.fe_per_rel, af.fe_mean, af.currency
  FROM fe_v4.fe_basic_conh_af af
  WHERE af.fsym_id IN (SELECT fsym_company_id FROM ids)
    AND af.fe_item IN ('SALES', 'EPS')
    AND af.fe_per_rel IN (1, 2)
    AND af.cons_end_date IS NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY af.fsym_id, af.fe_item, af.fe_per_rel
                             ORDER BY af.cons_start_date DESC) = 1
  UNION ALL
  SELECT af.fsym_id, af.fe_item, af.fe_per_rel, af.fe_mean, af.currency
  FROM fe_v4.fe_advanced_conh_af af
  WHERE af.fsym_id IN (SELECT fsym_company_id FROM ids)
    AND af.fe_item IN ('EBITDA', 'EBIT', 'CAPEX')
    AND af.fe_per_rel IN (1, 2)
    AND af.cons_end_date IS NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY af.fsym_id, af.fe_item, af.fe_per_rel
                             ORDER BY af.cons_start_date DESC) = 1
),
est_pivot AS (
  SELECT
    fsym_id,
    MAX(CASE WHEN fe_item='SALES'  AND fe_per_rel=1 THEN currency END) AS sales_currency,
    MAX(CASE WHEN fe_item='EBITDA' AND fe_per_rel=1 THEN currency END) AS ebitda_currency,
    MAX(CASE WHEN fe_item='EPS'    AND fe_per_rel=1 THEN currency END) AS eps_currency,
    MAX(CASE WHEN fe_item='CAPEX'  AND fe_per_rel=1 THEN currency END) AS capex_currency,
    MAX(CASE WHEN fe_item='SALES'  AND fe_per_rel=1 THEN fe_mean END) AS sales_fy1,
    MAX(CASE WHEN fe_item='SALES'  AND fe_per_rel=2 THEN fe_mean END) AS sales_fy2,
    MAX(CASE WHEN fe_item='EBITDA' AND fe_per_rel=1 THEN fe_mean END) AS ebitda_fy1,
    MAX(CASE WHEN fe_item='EBITDA' AND fe_per_rel=2 THEN fe_mean END) AS ebitda_fy2,
    MAX(CASE WHEN fe_item='EPS'    AND fe_per_rel=1 THEN fe_mean END) AS eps_fy1,
    MAX(CASE WHEN fe_item='EPS'    AND fe_per_rel=2 THEN fe_mean END) AS eps_fy2,
    MAX(CASE WHEN fe_item='EBIT'   AND fe_per_rel=1 THEN fe_mean END) AS ebit_fy1,
    MAX(CASE WHEN fe_item='EBIT'   AND fe_per_rel=2 THEN fe_mean END) AS ebit_fy2,
    MAX(CASE WHEN fe_item='CAPEX'  AND fe_per_rel=1 THEN fe_mean END) AS capex_fy1,
    MAX(CASE WHEN fe_item='CAPEX'  AND fe_per_rel=2 THEN fe_mean END) AS capex_fy2
  FROM est_all GROUP BY fsym_id
),
mult_hist AS (
  SELECT d.fsym_id, AVG(d.ff_entrpr_val_ebitda_oper) AS avg_ev_ebitda_10y
  FROM ff_v3.ff_advanced_der_af d
  WHERE d.fsym_id IN (SELECT fsym_company_id FROM ids)
    AND d.date >= DATEADD(year, -10, CURRENT_DATE())
    AND d.ff_entrpr_val_ebitda_oper IS NOT NULL
  GROUP BY d.fsym_id
)
SELECT DISTINCT
  i.sub_group AS sub_group,
  i.proper_name AS name,
  ind.industry AS industry,
  pl.proper_name AS company,
  i.ticker_region AS ticker_region,
  pl.price_ccy AS currency,
  ROUND(pl.price_now, 2) AS share_price,
  ROUND((pl.price_now / NULLIF(p1.price_1m, 0) - 1) * 100, 1) AS chg_1m,
  ROUND((pl.price_now / NULLIF(p3.price_3m, 0) - 1) * 100, 1) AS chg_3m,
  ROUND((pl.price_now / NULLIF(p6.price_6m, 0) - 1) * 100, 1) AS chg_6m,
  ROUND((pl.price_now / NULLIF(py.price_1y, 0) - 1) * 100, 1) AS chg_1y,
  ROUND(CASE WHEN pw.hi52 > 0 THEN pl.price_now / pw.hi52 * 100 END, 1) AS pct_52w_high,
  ROUND(CASE WHEN pw.lo52 > 0 THEN pl.price_now / pw.lo52 * 100 END, 1) AS pct_52w_low,
  ROUND(pl.price_now * f.ff_com_shs_out, 0) AS mkt_cap_local_m,
  ROUND(pl.price_now * f.ff_com_shs_out * COALESCE(fx.exch_rate_usd, 1), 0) AS mkt_cap_usd_m,
  ROUND(f.ff_net_debt, 0) AS net_debt_local_m,
  ROUND(f.ff_net_debt * COALESCE(fx.exch_rate_usd, 1), 0) AS net_debt_usd_m,
  ROUND(pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0), 0) AS tev_local_m,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1), 0) AS tev_usd_m,
  ROUND(f.ff_sales_gr, 1) AS ltm_rev_growth,
  ROUND((ep.sales_fy2 / NULLIF(ep.sales_fy1, 0) - 1) * 100, 1) AS ntm_rev_cagr,
  ROUND((ep.ebitda_fy2 / NULLIF(ep.ebitda_fy1, 0) - 1) * 100, 1) AS ntm_ebitda_cagr,
  ROUND(f.ff_ebitda_oper_mgn, 1) AS ltm_ebitda_margin,
  ROUND(ep.ebitda_fy1 / NULLIF(ep.sales_fy1, 0) * 100, 1) AS ntm_ebitda_margin,
  ROUND(fh.avg_ebitda_margin, 1) AS avg_10y_ebitda_mgn,
  ROUND(f.ff_gross_mgn, 1) AS ltm_gross_margin,
  ROUND(fh.avg_gross_margin, 1) AS avg_10y_gross_mgn,
  ROUND(f.ff_sga_sales, 1) AS sga_pct,
  ROUND(fh.avg_sga_pct, 1) AS avg_10y_sga,
  ROUND(f.ff_net_debt / NULLIF(f.ff_ebitda, 0), 1) AS net_leverage,
  ROUND(fh.avg_gross_leverage, 1) AS avg_10y_gross_leverage,
  ROUND(f.ff_sales, 0) AS revenue_ltm_m,
  ROUND(ep.sales_fy1, 0) AS revenue_ntm_m,
  ROUND(f.ff_ebitda, 0) AS ebitda_ltm_m,
  ROUND(ep.ebitda_fy1, 0) AS ebitda_ntm_m,
  ROUND(fh.avg_ebitda_10y_local, 0) AS avg_ebitda_10y_local_m,
  ROUND(f.ff_ebitda - COALESCE(ll.lease_exp_ltm, f.lease_exp_annual, 0), 0) AS adj_ebitda_ltm_m,
  ROUND(ep.ebitda_fy1 - COALESCE(f.lease_exp_annual, 0), 0) AS adj_ebitda_ntm_m,
  ROUND(f.ff_oper_inc, 0) AS ebit_ltm_m,
  ROUND(ep.ebit_fy1, 0) AS ebit_ntm_m,
  ROUND(f.ff_net_income, 0) AS net_income_ltm_m,
  ROUND(f.ff_eps_dil_aft_xord, 2) AS eps_ltm,
  ROUND(ep.eps_fy1, 2) AS eps_cur_year,
  ROUND(ep.eps_fy2, 2) AS eps_next_year,
  ROUND(f.ff_fcf_yld * 100, 1) AS fcf_yield,
  ROUND(f.ff_div_yld, 1) AS div_yield,
  ROUND(COALESCE(ll.lease_exp_ltm, f.lease_exp_annual), 0) AS oper_lease_exp_ltm_m,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        / NULLIF(f.ff_sales, 0), 1) AS ev_rev_ltm,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.sales_fy1 * COALESCE(sfx.exch_rate_usd, 1), 0), 1) AS ev_rev_ntm,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.sales_fy2 * COALESCE(sfx.exch_rate_usd, 1), 0), 1) AS ev_rev_next,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        / NULLIF(f.ff_ebitda, 0), 1) AS ev_ebitda_ltm,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.ebitda_fy1 * COALESCE(ebfx.exch_rate_usd, 1), 0), 1) AS ev_ebitda_ntm,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.ebitda_fy2 * COALESCE(ebfx.exch_rate_usd, 1), 0), 1) AS ev_ebitda_next,
  ROUND(mh.avg_ev_ebitda_10y, 1) AS avg_10y_ev_ebitda,
  ROUND(mh.avg_ev_ebitda_10y
        * (NULLIF(f.ff_ebitda * COALESCE(fx.exch_rate_usd, 1), 0)
           / NULLIF(ep.ebitda_fy1 * COALESCE(ebfx.exch_rate_usd, 1), 0)), 1)
    AS avg_10y_ev_ebitda_ntm,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.ebitda_fy1 * COALESCE(ebfx.exch_rate_usd, 1)
                 - ep.capex_fy1 * COALESCE(cfx.exch_rate_usd, 1), 0), 1)
    AS ev_ebitda_capex_cur,
  ROUND((pl.price_now * f.ff_com_shs_out + COALESCE(f.ff_net_debt, 0))
        * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.ebitda_fy2 * COALESCE(ebfx.exch_rate_usd, 1)
                 - ep.capex_fy2 * COALESCE(cfx.exch_rate_usd, 1), 0), 1)
    AS ev_ebitda_capex_next,
  ROUND(pl.price_now / NULLIF(f.ff_eps_dil_aft_xord, 0), 1) AS pe_ltm,
  ROUND(pl.price_now * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.eps_fy1 * COALESCE(epsfx.exch_rate_usd, 1), 0), 1) AS pe_cur_year,
  ROUND(pl.price_now * COALESCE(fx.exch_rate_usd, 1)
        / NULLIF(ep.eps_fy2 * COALESCE(epsfx.exch_rate_usd, 1), 0), 1) AS pe_next_year
FROM ids i
  JOIN px_latest pl ON pl.ticker_region = i.ticker_region
  LEFT JOIN ind ON ind.ticker_region = i.ticker_region
  LEFT JOIN px_1m p1 ON p1.ticker_region = i.ticker_region
  LEFT JOIN px_3m p3 ON p3.ticker_region = i.ticker_region
  LEFT JOIN px_6m p6 ON p6.ticker_region = i.ticker_region
  LEFT JOIN px_1y py ON py.ticker_region = i.ticker_region
  LEFT JOIN px_52w pw ON pw.ticker_region = i.ticker_region
  LEFT JOIN fund_ltm f ON f.fsym_id = i.fsym_company_id
  LEFT JOIN lease_ltm ll ON ll.fsym_id = i.fsym_company_id
  LEFT JOIN fund_prior fp ON fp.fsym_id = i.fsym_company_id
  LEFT JOIN fund_hist fh ON fh.fsym_id = i.fsym_company_id
  LEFT JOIN est_pivot ep ON ep.fsym_id = i.fsym_company_id
  LEFT JOIN mult_hist mh ON mh.fsym_id = i.fsym_company_id
  LEFT JOIN fx_latest fx ON fx.iso_currency = pl.price_ccy
  LEFT JOIN fx_latest sfx ON sfx.iso_currency = ep.sales_currency
  LEFT JOIN fx_latest ebfx ON ebfx.iso_currency = ep.ebitda_currency
  LEFT JOIN fx_latest epsfx ON epsfx.iso_currency = ep.eps_currency
  LEFT JOIN fx_latest cfx ON cfx.iso_currency = ep.capex_currency
ORDER BY mkt_cap_usd_m DESC NULLS LAST
