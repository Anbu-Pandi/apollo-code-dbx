import logging
from datetime import datetime, timedelta

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


def create_dummy_bitcoin_ohlc_data():
    start_date = datetime(2024, 1, 1)
    dates = pd.date_range(start_date, start_date + timedelta(days=2), freq='1min')
    load_dt = datetime.now()
    
    dummy_data = []
    securities = [
        'XBTUSD Curncy',
        'ETHUSD Curncy',
    ]
    
    for security in securities:
        base_price = 50000 if 'XBT' in security else 3000
        
        for i, bar_time in enumerate(dates):
            price_variation = (i % 100) * 10.5
            open_price = float(base_price + price_variation)
            high_price = open_price + 50.25
            low_price = open_price - 30.75
            close_price = open_price + 20.50
            
            dummy_data.append({
                'security': security,
                'bar_time': bar_time,
                'year': bar_time.year,
                'bar_type': 'TRADE',
                'bar_open': open_price,
                'bar_high': high_price,
                'bar_low': low_price,
                'bar_close': close_price,
                'bar_volume': 1000000 + (i * 1000),
                'bar_vwap': (open_price + close_price) / 2,
                'bar_tick_count': 100 + (i % 50),
                'bar_open_time': bar_time,
                'bar_high_time': bar_time + timedelta(seconds=20),
                'bar_low_time': bar_time + timedelta(seconds=40),
                'bar_close_time': bar_time + timedelta(seconds=59),
                'load_date': load_dt
            })
    
    df = pd.DataFrame(dummy_data)
    logging.info('Created dummy bitcoin_ohlc_data with shape: %s', df.shape)
    return df


def init_bitcoin_ohlc_tables(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('=' * 70)
    logging.info('INITIALIZING BITCOIN OHLC TABLES')
    logging.info('=' * 70)
    
    logging.info('Creating bitcoin_ohlc_data table with dummy data')
    df_data = create_dummy_bitcoin_ohlc_data()
    sparkmanager.pandastodelta(df_data, 'bitcoin_ohlc_data', 'tables/dbo', 'year', overwrite=overwrite)
    logging.info('✓ bitcoin_ohlc_data table created with schema (partitioned by year)')
    
    logging.info('Deleting dummy data from bitcoin_ohlc_data')
    empty_data = pd.DataFrame(columns=df_data.columns)
    sparkmanager.pandastodelta(empty_data.head(0), 'bitcoin_ohlc_data', 'tables/dbo', 'year', overwrite=True)
    logging.info('✓ bitcoin_ohlc_data table now empty')
    
    logging.info('=' * 70)
    logging.info('✓ BITCOIN OHLC TABLES INITIALIZED (EMPTY) SUCCESSFULLY')
    logging.info('=' * 70)


if __name__ == '__main__':
    init_bitcoin_ohlc_tables(overwrite=True)

