import logging
import pandas as pd
from common.utils import sqlutil
from data_access.beta_attribution import APP

MAX_RESULT_ROWS = 10_000

TABLE_CONFIG = {
    'JPM Loan Index': {
        'table': 'jpm_loan_index',
        'date_column': 'asof_date',
        'filters': []
    },
    
    'ETF Constituents': {
        'table': 'dbo.etf_constituents',
        'date_column': 'etfg_date',
        'order_by': 'weight DESC',
        'filters': [
            {
                'type': 'multi_dropdown',
                'label': 'Composite Ticker',
                'column': 'composite_ticker',
                'options': ['HYG', 'BKLN', 'SRLN'],
                'default': ['HYG']
            }
        ]
    },
    
    # 'Example Table With Filters': {
    #     'table': 'example_table',
    #     'date_column': 'trade_date',
    #     'filters': [
    #         {
    #             'type': 'dropdown',
    #             'label': 'Rating',
    #             'column': 'rating',
    #             'options': ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC']
    #         },
    #         {
    #             'type': 'multi_dropdown',
    #             'label': 'Sector',
    #             'column': 'sector',
    #             'options_query': 'SELECT DISTINCT sector FROM example_table ORDER BY sector'
    #         },
    #         {
    #             'type': 'text',
    #             'label': 'Issuer Name',
    #             'column': 'issuer_name',
    #             'placeholder': 'Enter issuer name...'
    #         },
    #         {
    #             'type': 'date_range',
    #             'label': 'Maturity Date',
    #             'column': 'maturity_date'
    #         },
    #         {
    #             'type': 'numeric_range',
    #             'label': 'Spread',
    #             'column': 'spread_bps',
    #             'min': 0,
    #             'max': 1000
    #         }
    #     ]
    # }
}

def get_available_tables():
    return list(TABLE_CONFIG.keys())

def get_table_config(table_name):
    return TABLE_CONFIG.get(table_name, {})

def get_filter_options(table_name, filter_config):
    if 'options' in filter_config:
        return filter_config['options']
    
    if 'options_query' in filter_config:
        df = sqlutil.readdfsql(filter_config['options_query'], app=APP)
        return df.iloc[:, 0].tolist()
    
    return []

def build_where_clause(filters_dict, date_col, dt):
    conditions = [f"{date_col} = '{dt}'"]
    
    for col, value in filters_dict.items():
        if not value or value == 'All':
            continue
        
        if isinstance(value, list):
            if value:
                quoted_values = [f"'{v}'" for v in value]
                conditions.append(f"{col} IN ({', '.join(quoted_values)})")
        elif isinstance(value, dict):
            if 'start' in value and value['start']:
                conditions.append(f"{col} >= '{value['start']}'")
            if 'end' in value and value['end']:
                conditions.append(f"{col} <= '{value['end']}'")
        else:
            if '*' in str(value) or '%' in str(value):
                conditions.append(f"{col} LIKE '{value}'")
            else:
                conditions.append(f"{col} = '{value}'")
    
    return ' AND '.join(conditions)

def get_table_data(table_name, dt, filters=None):
    logging.info(f"Fetching data for table: {table_name}, date: {dt}, filters: {filters}")
    
    if table_name not in TABLE_CONFIG:
        return pd.DataFrame()
    
    config = TABLE_CONFIG[table_name]
    table = config['table']
    date_col = config['date_column']
    order_by = config.get('order_by', date_col)
    
    filters_dict = filters or {}
    where_clause = build_where_clause(filters_dict, date_col, dt)
    
    query = f"""
        SELECT *
        FROM {table}
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT {MAX_RESULT_ROWS + 1}
    """
    
    logging.info(f"Query: {query}")
    df = sqlutil.readdfsql(query, app=APP)
    truncated = len(df) > MAX_RESULT_ROWS
    if truncated:
        df = df.iloc[:MAX_RESULT_ROWS].copy()
    df.attrs["truncated"] = truncated
    logging.info(f"Retrieved {len(df)} rows for {table_name}")
    
    return df
