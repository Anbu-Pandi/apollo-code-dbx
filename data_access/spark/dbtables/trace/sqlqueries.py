SELECT_QUERY = """
SELECT th.*, tuh.real_quantity
FROM 
    (
    SELECT tl1.*
    FROM dbo.v_trace tl1
    ) AS th
    LEFT JOIN
            dbo.v_trace_uncapped AS tuh
            ON cast(th.issue_price_id AS INT) = cast(tuh.issue_price_id AS INT)
WHERE
    th.effective_date >= '2000-01-01' 
    AND th.cancel_flag <> 'Y' 
    AND th.asof_flag <> 'R' 
    {1}
"""

SELECT_QUERY_WITHOUT_REALQTY = """
SELECT * 
FROM 
    dbo.trace th
WHERE
    th.effective_date >= '2000-01-01' 
    AND th.cancel_flag <> 'Y' 
    AND th.asof_flag <> 'R' 
    AND th.report_date <= '{0}'
    {1}
"""

REPORTDATE_COLUMN = 'th.report_date'

REALQUANTITY_COLUMN = 'tuh.real_quantity'

DATE_COLUMN = 'effective_date'
TIME_COLUMN = 'effective_time'
