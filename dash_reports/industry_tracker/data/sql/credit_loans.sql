WITH latest AS (
    SELECT MAX(asof_date) AS asof_date FROM dbo.jpm_loan_index
),
markit_latest AS (
    SELECT
        loanx_id,
        asof_date AS markit_asof_date,
        bid AS markit_bid,
        evaluated_price AS markit_evaluated,
        close_bid AS markit_close_bid,
        contributed AS markit_contributed
    FROM dbo.markit_pricing_data_loan
    QUALIFY ROW_NUMBER() OVER (PARTITION BY loanx_id ORDER BY asof_date DESC) = 1
)
SELECT
    li.asof_date,
    li.industry AS jpm_industry,
    li.ind_segment AS jpm_ind_segment,
    li.issuer_name,
    li.ticker AS issuer_ticker,
    li.asset_name AS facility_name,
    li.loan_id,
    li.loanx AS loanx_id,
    li.facility_id,
    li.cusip,
    li.maturity_date,
    li.consolidated_rating AS rating,
    li.currency,
    li.country,
    li.country_tier AS region,
    li.original_commitment AS original_amount,
    li.calc_current_outstanding AS current_outstanding,
    li.end_cpn_rate AS coupon,
    li.offer_spread AS offer_spread_bp,
    li.end_price,
    m.markit_asof_date,
    m.markit_bid,
    m.markit_evaluated,
    m.markit_close_bid,
    m.markit_contributed,
    li.fw_3y_yld,
    li.fw_3y_spd,
    li.fw_3y_moddur,
    li.fw_mat_yld,
    li.fw_mat_spd,
    li.fw_mat_moddur,
    li.tot_ret AS month_total_return,
    li.prn_ret AS month_principal_return,
    li.int_ret AS month_interest_return,
    li.leveraged_mkt_wt,
    li.sec_lien_mkt_wt,
    li.liquid_mkt_wt,
    li.bb_b_mkt_wt,
    li.euro_mkt_wt,
    li.covlite AS covenant_light,
    li.libor_floor
FROM dbo.jpm_loan_index li
INNER JOIN latest l ON li.asof_date = l.asof_date
LEFT JOIN markit_latest m ON m.loanx_id = li.loanx
WHERE li.ticker IN ({ticker_list})
  AND (li.leveraged_mkt_wt > 0 OR li.sec_lien_mkt_wt > 0 OR li.liquid_mkt_wt > 0)
ORDER BY li.ticker, li.issuer_name, current_outstanding DESC NULLS LAST
