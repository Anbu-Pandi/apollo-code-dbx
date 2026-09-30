SQL = """
SELECT
    *
FROM
    [dbo].[markit_pricing_data_loan]
WHERE 
    {0}
"""

DATE_COLUMN = 'mark_date'
