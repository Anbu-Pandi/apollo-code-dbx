SQL = """
SELECT  id,
        asof_date,
        issuer,
        facility_name,
        maturity_date,
        original_amount,
        current_outstanding,
        libor_spread,
        --original_libor_spread,
        launch_date,
        initial_rating,
        current_rating,
        lcd_broad_industry,
        narrow_industry,
        weights,
        market_return,
        interest_return,
        total_return,
        currency,
        country,
        region,
        facility_id,
        pricing_id,
        sponsored,
        ccr_rating,
        libor_floor,
        fl_sl,
        covenant_light

FROM
    dbo.sp_loan_index
WHERE 
    {0}
"""

DATE_COLUMN = 'asof_date'
