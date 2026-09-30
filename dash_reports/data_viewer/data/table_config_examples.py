"""
Examples of table configurations with different filter types
Copy these examples into table_data.py TABLE_CONFIG as needed
"""

EXAMPLE_CONFIGS = {
    
    # Example 1: Simple table with just date filter (like JPM Loan Index)
    'Simple Table': {
        'table': 'your_table_name',
        'date_column': 'asof_date',
        'filters': []
    },
    
    # Example 2: Table with dropdown filter (static options)
    'Table With Dropdown': {
        'table': 'bond_data',
        'date_column': 'trade_date',
        'filters': [
            {
                'type': 'dropdown',
                'label': 'Rating',
                'column': 'rating',
                'options': ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC']
            }
        ]
    },
    
    # Example 3: Table with multi-select dropdown (dynamic options from DB)
    'Table With Multi Select': {
        'table': 'bond_data',
        'date_column': 'trade_date',
        'filters': [
            {
                'type': 'multi_dropdown',
                'label': 'Sectors',
                'column': 'sector',
                'options_query': 'SELECT DISTINCT sector FROM bond_data WHERE sector IS NOT NULL ORDER BY sector'
            },
            {
                'type': 'multi_dropdown',
                'label': 'Issuers',
                'column': 'issuer_name',
                'options_query': 'SELECT DISTINCT issuer_name FROM bond_data WHERE issuer_name IS NOT NULL ORDER BY issuer_name'
            }
        ]
    },
    
    # Example 4: Table with text search (supports wildcards)
    'Table With Text Search': {
        'table': 'loan_data',
        'date_column': 'asof_date',
        'filters': [
            {
                'type': 'text',
                'label': 'Borrower Name',
                'column': 'borrower_name',
                'placeholder': 'Enter borrower name (use % for wildcard)...'
            },
            {
                'type': 'text',
                'label': 'CUSIP',
                'column': 'cusip',
                'placeholder': 'Enter CUSIP...'
            }
        ]
    },
    
    # Example 5: Table with date range filter
    'Table With Date Range': {
        'table': 'bond_data',
        'date_column': 'trade_date',
        'filters': [
            {
                'type': 'date_range',
                'label': 'Maturity Date',
                'column': 'maturity_date'
            }
        ]
    },
    
    # Example 6: Table with numeric range filter
    'Table With Numeric Range': {
        'table': 'bond_pricing',
        'date_column': 'pricing_date',
        'filters': [
            {
                'type': 'numeric_range',
                'label': 'Spread (bps)',
                'column': 'spread_bps',
                'min': 0,
                'max': 1000
            },
            {
                'type': 'numeric_range',
                'label': 'Coupon %',
                'column': 'coupon_rate',
                'min': 0,
                'max': 20
            }
        ]
    },
    
    # Example 7: Full featured table with all filter types
    'Comprehensive Example': {
        'table': 'credit_portfolio',
        'date_column': 'snapshot_date',
        'filters': [
            {
                'type': 'dropdown',
                'label': 'Asset Class',
                'column': 'asset_class',
                'options': ['Corporate Bond', 'Loan', 'CDS', 'Equity']
            },
            {
                'type': 'multi_dropdown',
                'label': 'Sectors',
                'column': 'sector',
                'options_query': 'SELECT DISTINCT sector FROM credit_portfolio WHERE sector IS NOT NULL ORDER BY sector'
            },
            {
                'type': 'multi_dropdown',
                'label': 'Ratings',
                'column': 'rating',
                'options': ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC', 'D']
            },
            {
                'type': 'text',
                'label': 'Issuer',
                'column': 'issuer_name',
                'placeholder': 'Search issuer (% for wildcard)...'
            },
            {
                'type': 'date_range',
                'label': 'Maturity Range',
                'column': 'maturity_date'
            },
            {
                'type': 'numeric_range',
                'label': 'Spread (bps)',
                'column': 'spread',
                'min': 0,
                'max': 2000
            },
            {
                'type': 'numeric_range',
                'label': 'Notional (MM)',
                'column': 'notional_mm',
                'min': 0,
                'max': 1000
            }
        ]
    },
    
    # Example 8: Real-world JPM Loan Index with filters (commented out for future use)
    # 'JPM Loan Index Enhanced': {
    #     'table': 'jpm_loan_index',
    #     'date_column': 'asof_date',
    #     'filters': [
    #         {
    #             'type': 'multi_dropdown',
    #             'label': 'Facility Type',
    #             'column': 'facility_type',
    #             'options_query': 'SELECT DISTINCT facility_type FROM jpm_loan_index WHERE facility_type IS NOT NULL ORDER BY facility_type'
    #         },
    #         {
    #             'type': 'dropdown',
    #             'label': 'Rating',
    #             'column': 'rating',
    #             'options': ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC']
    #         },
    #         {
    #             'type': 'text',
    #             'label': 'Borrower',
    #             'column': 'borrower_name',
    #             'placeholder': 'Search borrower...'
    #         },
    #         {
    #             'type': 'numeric_range',
    #             'label': 'Spread (bps)',
    #             'column': 'spread_bps',
    #             'min': 0,
    #             'max': 1500
    #         }
    #     ]
    # }
}


FILTER_TYPE_EXAMPLES = """
FILTER TYPES AVAILABLE:

1. DROPDOWN (single select with "All" option)
   {
       'type': 'dropdown',
       'label': 'Display Name',
       'column': 'db_column_name',
       'options': ['Option1', 'Option2', 'Option3']
   }

2. MULTI_DROPDOWN (multiple selections)
   {
       'type': 'multi_dropdown',
       'label': 'Display Name',
       'column': 'db_column_name',
       'options': ['Option1', 'Option2']  # Static options
       # OR
       'options_query': 'SELECT DISTINCT column FROM table ORDER BY column'  # Dynamic from DB
   }

3. TEXT (free text input, supports wildcards % and *)
   {
       'type': 'text',
       'label': 'Display Name',
       'column': 'db_column_name',
       'placeholder': 'Enter text...'
   }

4. DATE_RANGE (start and end date pickers)
   {
       'type': 'date_range',
       'label': 'Display Name',
       'column': 'db_column_name'
   }

5. NUMERIC_RANGE (min and max numeric inputs)
   {
       'type': 'numeric_range',
       'label': 'Display Name',
       'column': 'db_column_name',
       'min': 0,
       'max': 1000
   }

USAGE TIPS:
- Filters are applied with AND logic
- Text filters support SQL wildcards (% and *)
- Multi-dropdowns allow selecting multiple values (uses IN clause)
- Date/numeric ranges can have only start OR end OR both
- Empty/unselected filters are ignored in the query
"""
