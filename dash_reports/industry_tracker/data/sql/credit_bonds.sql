WITH bsm AS (
    SELECT
        bsm.id_isin AS isin,
        bsm.id_cusip AS cusip,
        bsm.id_bb_global,
        bsm.ticker AS bsm_ticker,
        bsm.long_comp_name AS issuer_name,
        bsm.maturity,
        bsm.amt_outstanding,
        bsm.cpn AS bsm_coupon,
        bsm.cpn_typ AS coupon_type,
        bsm.crncy AS bsm_currency,
        bsm.senior,
        bsm.junior,
        bsm.industry_subgroup AS bsm_subgroup,
        bsm.floater,
        bsm.reset_idx,
        bsm.basic_spread,
        bsm.flt_bench_multiplier
    FROM dbo.v_bond_security_master bsm
    WHERE bsm.ticker IN ({ticker_list})
      AND bsm.id_isin IS NOT NULL
      AND COALESCE(bsm.update_type, 0) <> -1
      AND COALESCE(bsm.called, FALSE) <> TRUE
      AND COALESCE(bsm.defaulted, FALSE) <> TRUE
      AND COALESCE(bsm.structured_note, FALSE) <> TRUE
      AND COALESCE(bsm.contrib_data_indicator, FALSE) <> TRUE
      AND bsm.amt_outstanding > 50000000
      AND (bsm.maturity IS NULL OR bsm.maturity > current_date())
),
latest_markit AS (
    SELECT MAX(date) AS date FROM dbo.markit_pricing_data
),
mp AS (
    SELECT
        m.date AS markit_date,
        m.isin,
        m.coupon AS markit_coupon,
        m.currency AS markit_currency,
        m.bid_price, m.mid_price, m.ask_price,
        m.mid_ytm, m.mid_ytw,
        m.mid_oas, m.mid_z_spread,
        m.mid_modified_duration,
        m.liquidity_score,
        m.tier
    FROM dbo.markit_pricing_data m
    INNER JOIN latest_markit lm ON m.date = lm.date
),
priced AS (
    SELECT
        b.bsm_ticker AS issuer_ticker,
        b.issuer_name,
        b.isin, b.cusip, b.id_bb_global,
        b.maturity, b.amt_outstanding,
        b.senior, b.junior, b.bsm_subgroup,
        COALESCE(mp.markit_coupon, b.bsm_coupon) AS coupon_pct,
        b.coupon_type,
        b.floater, b.reset_idx, b.basic_spread, b.flt_bench_multiplier,
        COALESCE(mp.markit_currency, b.bsm_currency) AS currency,
        mp.markit_date,
        mp.bid_price, mp.mid_price, mp.ask_price,
        mp.mid_ytm, mp.mid_ytw, mp.mid_oas, mp.mid_z_spread,
        mp.mid_modified_duration, mp.liquidity_score, mp.tier
    FROM bsm b
    LEFT JOIN mp ON mp.isin = b.isin
),
twin_keyed AS (
    SELECT *,
        COUNT(*) OVER (PARTITION BY issuer_ticker, coupon_pct, maturity, currency) AS twin_count,
        ROW_NUMBER() OVER (
            PARTITION BY issuer_ticker, coupon_pct, maturity, currency
            ORDER BY amt_outstanding DESC NULLS LAST,
                     CASE WHEN isin LIKE 'US%' THEN 0 ELSE 1 END,
                     isin
        ) AS twin_rn
    FROM priced
)
SELECT
    issuer_ticker,
    issuer_name,
    isin, cusip, id_bb_global,
    maturity, amt_outstanding,
    senior, junior, bsm_subgroup,
    coupon_pct, coupon_type, currency,
    floater, reset_idx, basic_spread, flt_bench_multiplier,
    markit_date,
    bid_price, mid_price, ask_price,
    mid_ytm, mid_ytw, mid_oas, mid_z_spread,
    mid_modified_duration, liquidity_score, tier,
    twin_count
FROM twin_keyed
WHERE twin_rn = 1
ORDER BY issuer_ticker, amt_outstanding DESC NULLS LAST
