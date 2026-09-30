import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


def create_dummy_haver_series_data():
    dates = pd.date_range('2020-01-01', '2023-12-31', freq='Q')
    knowledge_dt = datetime.now()
    load_dt = datetime.now()
    
    dummy_data = []
    series_list = [
        ('GDP@USECON', 'GDP001', 'GDP', 'USECON', 'Quarterly', 'United States', 'BEA'),
        ('FFED@USECON', 'FFED001', 'Federal Funds Rate', 'USECON', 'Daily', 'United States', 'Federal Reserve'),
        ('CPI@USECON', 'CPI001', 'Consumer Price Index', 'USECON', 'Monthly', 'United States', 'BLS'),
    ]
    
    for series_code, series_id, series_name, database_name, frequency, geography, source_name in series_list:
        for i, date in enumerate(dates):
            dummy_data.append({
                'series_code': series_code,
                'series_id': series_id,
                'series_name': series_name,
                'database_name': database_name,
                'asof_date': date,
                'value': 100.0 + i * 2.5,
                'frequency': frequency,
                'geography': geography,
                'source_name': source_name,
                'last_modified': '2024-01-15 10:30:00',
                'knowledge_date': knowledge_dt,
                'load_date': load_dt
            })
    
    df = pd.DataFrame(dummy_data)
    logging.info('Created dummy haver_series_data with shape: %s', df.shape)
    return df


def create_dummy_haver_databases():
    now = datetime.now()
    
    dummy_databases = [
        {
            'database_name': 'USECON',
            'database_description': 'US Economics - Main US economic indicators',
            'total_series_count': 15000,
            'last_updated': now,
            'is_active': True,
            'knowledge_date': now,
            'load_date': now
        },
        {
            'database_name': 'EMERGE',
            'database_description': 'Emerging Markets - Economic data for emerging markets',
            'total_series_count': 8000,
            'last_updated': now,
            'is_active': True,
            'knowledge_date': now,
            'load_date': now
        },
        {
            'database_name': 'DAILY',
            'database_description': 'Daily US Economic Data',
            'total_series_count': 3000,
            'last_updated': now,
            'is_active': True,
            'knowledge_date': now,
            'load_date': now
        }
    ]
    
    df = pd.DataFrame(dummy_databases)
    logging.info('Created dummy haver_databases with shape: %s', df.shape)
    return df


def create_dummy_haver_series_metadata():
    now = datetime.now()
    
    dummy_metadata = [
        {
            'series_code': 'GDP@USECON',
            'series_id': 'GDP001',
            'series_name': 'GDP',
            'database_name': 'USECON',
            'description': 'Gross Domestic Product, Billions of Dollars',
            'frequency': 'Quarterly',
            'geography': 'United States',
            'geography2': 'US',
            'source_name': 'Bureau of Economic Analysis',
            'data_type': 'Level',
            'magnitude': 'Billions',
            'aggregation_type': 'Sum',
            'difference_type': 'None',
            'decimal_precision': 1,
            'starting_period': '1947Q1',
            'start_date': pd.to_datetime('1947-01-01'),
            'data_point_count': 300,
            'last_modified': '2024-01-15 10:30:00',
            'knowledge_date': now,
            'load_date': now
        },
        {
            'series_code': 'FFED@USECON',
            'series_id': 'FFED001',
            'series_name': 'Federal Funds Rate',
            'database_name': 'USECON',
            'description': 'Federal Funds Effective Rate, Percent',
            'frequency': 'Daily',
            'geography': 'United States',
            'geography2': 'US',
            'source_name': 'Federal Reserve Board',
            'data_type': 'Rate',
            'magnitude': 'Percent',
            'aggregation_type': 'Average',
            'difference_type': 'None',
            'decimal_precision': 2,
            'starting_period': '1954-07-01',
            'start_date': pd.to_datetime('1954-07-01'),
            'data_point_count': 18000,
            'last_modified': '2024-01-15 10:30:00',
            'knowledge_date': now,
            'load_date': now
        },
        {
            'series_code': 'CPI@USECON',
            'series_id': 'CPI001',
            'series_name': 'Consumer Price Index',
            'database_name': 'USECON',
            'description': 'Consumer Price Index for All Urban Consumers',
            'frequency': 'Monthly',
            'geography': 'United States',
            'geography2': 'US',
            'source_name': 'Bureau of Labor Statistics',
            'data_type': 'Index',
            'magnitude': 'Index 1982-84=100',
            'aggregation_type': 'Average',
            'difference_type': 'None',
            'decimal_precision': 1,
            'starting_period': '1913-01',
            'start_date': pd.to_datetime('1913-01-01'),
            'data_point_count': 1300,
            'last_modified': '2024-01-15 10:30:00',
            'knowledge_date': now,
            'load_date': now
        }
    ]
    
    df = pd.DataFrame(dummy_metadata)
    logging.info('Created dummy haver_series_metadata with shape: %s', df.shape)
    return df


def init_haver_tables(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('=' * 70)
    logging.info('INITIALIZING HAVER TABLES')
    logging.info('=' * 70)
    
    logging.info('Creating haver_databases table with dummy data')
    df_databases = create_dummy_haver_databases()
    sparkmanager.pandastodelta(df_databases, 'haver_databases', 'tables/dbo', overwrite=overwrite)
    logging.info('✓ haver_databases table created with schema')
    
    logging.info('Creating haver_series_data table with dummy data')
    df_data = create_dummy_haver_series_data()
    sparkmanager.pandastodelta(df_data, 'haver_series_data', 'tables/dbo', 'asof_date', overwrite=overwrite)
    logging.info('✓ haver_series_data table created with schema')
    
    logging.info('Creating haver_series_metadata table with dummy data')
    df_metadata = create_dummy_haver_series_metadata()
    sparkmanager.pandastodelta(df_metadata, 'haver_series_metadata', 'tables/dbo', overwrite=overwrite)
    logging.info('✓ haver_series_metadata table created with schema')
    
    logging.info('Deleting dummy data from haver_databases')
    empty_databases = pd.DataFrame(columns=df_databases.columns)
    sparkmanager.pandastodelta(empty_databases.head(0), 'haver_databases', 'tables/dbo', overwrite=True)
    logging.info('✓ haver_databases table now empty')
    
    logging.info('Deleting dummy data from haver_series_data')
    empty_data = pd.DataFrame(columns=df_data.columns)
    sparkmanager.pandastodelta(empty_data.head(0), 'haver_series_data', 'tables/dbo', 'asof_date', overwrite=True)
    logging.info('✓ haver_series_data table now empty')
    
    logging.info('Deleting dummy data from haver_series_metadata')
    empty_metadata = pd.DataFrame(columns=df_metadata.columns)
    sparkmanager.pandastodelta(empty_metadata.head(0), 'haver_series_metadata', 'tables/dbo', overwrite=True)
    logging.info('✓ haver_series_metadata table now empty')
    
    logging.info('=' * 70)
    logging.info('✓ HAVER TABLES INITIALIZED (EMPTY) SUCCESSFULLY')
    logging.info('=' * 70)


if __name__ == '__main__':
    init_haver_tables(overwrite=True)

