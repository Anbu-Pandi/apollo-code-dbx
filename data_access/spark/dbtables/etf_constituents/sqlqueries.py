SQL = """
SELECT
    etfg_date
    ,asof_date
    ,composite_ticker
    ,constituent_ticker
    ,constituent_name
    ,weight
    ,market_value
    ,cusip
    ,isin
    ,figi
    ,sedol
    ,country_of_exchange
    ,exchange
    ,shares_held
    ,asset_class
    ,security_type
    ,currency_traded
FROM
    dbo.etf_constituents
WHERE 
    {0}
"""

DATE_COLUMN = 'asof_date'
