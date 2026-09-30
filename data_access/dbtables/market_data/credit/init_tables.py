"""
🎯 CDS Index Tranche Table Initialization

Creates the markit_cds_index_tranche table with proper schema in Databricks.

Usage:
    python -m data_access.dbtables.market_data.credit.init_tables
    
Or from Python:
    from data_access.dbtables.market_data.credit.init_tables import init_cds_index_tranche_table
    init_cds_index_tranche_table(overwrite=True)

Table Schema:
    - date: Date from source file
    - asof_date: As-of date (partition key)
    - batch: EOD/Intraday batch indicator
    - index_family: CDX, ITRAXX-European, etc.
    - index_ticker: CDX-NAIG, ITRAXX-Europe, ITRAXX-Xover, etc.
    - index_series: Series number
    - index_version: Version number
    - index_term: 5Y, 7Y, 10Y
    - index_red_code: RED code
    - index_maturity: Maturity date
    - on_the_run: Y/N flag
    - attachment: Tranche attachment point (0-1)
    - detachment: Tranche detachment point (0-1)
    - tranche_coupon: Tranche coupon
    - tranche_upfront_bid/mid/ask: Upfront payments
    - tranche_spread_bid/mid/ask: Spread quotes
    - tranche_price_bid/mid/ask: Price quotes
    - base_correlation: Base correlation
    - tranche_factor: Tranche factor
    - delta: Delta
    - index_pv01: Index PV01
    - tranche_pv01: Tranche PV01
    - dealer_count_all/window: Dealer counts
    - quote_count_all/window: Quote counts
    - fuel_time_window: Time window
    - reference_price/spread: Reference values
    - type: Type field
    - file_name: Source file name
    - knowledge_date: File modification timestamp
    - load_date: Database load timestamp
"""

import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


def create_dummy_cds_index_tranche_data():
    dates = pd.date_range('2025-10-20', '2025-10-24', freq='B')
    knowledge_dt = datetime.now()
    load_dt = datetime.now()
    
    dummy_data = []
    tranche_configs = [
        ('CDX', 'CDX-NAIG', 37, 1, '5Y', '2I65BYDZ5', '2026-12-20', 0.15, 1.0),
        ('CDX', 'CDX-NAIG', 37, 1, '5Y', '2I65BYDZ5', '2026-12-20', 0.0, 0.15),
        ('ITRAXX-European', 'ITRAXX-Europe', 36, 1, '5Y', '2I666VCZ6', '2026-12-20', 0.12, 1.0),
        ('ITRAXX-European', 'ITRAXX-Europe', 38, 1, '5Y', '2I666VDB8', '2027-12-20', 0.06, 0.12),
        ('ITRAXX-European', 'ITRAXX-Xover', 38, 4, '5Y', '2I667KLS5', '2027-12-20', 0.35, 1.0),
        ('ITRAXX-European', 'ITRAXX-Xover', 38, 4, '5Y', '2I667KLS5', '2027-12-20', 0.0, 0.1),
    ]
    
    for date in dates:
        for idx_family, idx_ticker, series, version, term, red_code, maturity, attach, detach in tranche_configs:
            on_the_run = 'Y' if series >= 37 else 'N'
            
            dummy_data.append({
                'date': date,
                'asof_date': date,
                'batch': 'EOD',
                'index_family': idx_family,
                'index_ticker': idx_ticker,
                'index_series': series,
                'index_version': version,
                'index_term': term,
                'index_red_code': red_code,
                'index_maturity': pd.to_datetime(maturity),
                'on_the_run': on_the_run,
                'attachment': attach,
                'detachment': detach,
                'tranche_coupon': 0.01 if attach == 0 else 0.05,
                'tranche_upfront_bid': -0.011 if attach > 0.1 else 0.18,
                'tranche_upfront_mid': -0.0105 if attach > 0.1 else 0.19,
                'tranche_upfront_ask': -0.010 if attach > 0.1 else 0.20,
                'tranche_spread_bid': 0.01,
                'tranche_spread_mid': 0.01,
                'tranche_spread_ask': 0.01,
                'tranche_price_bid': None,
                'tranche_price_mid': None,
                'tranche_price_ask': None,
                'base_correlation': 0.75 if attach > 0 else None,
                'tranche_factor': 1.0 if detach == 1.0 else None,
                'delta': 0.35 if attach > 0.1 else 6.5,
                'index_pv01': 1.01,
                'tranche_pv01': 0.0015 if attach > 0.1 else 0.014,
                'dealer_count_all': 3,
                'dealer_count_window': None,
                'quote_count_all': None,
                'quote_count_window': None,
                'fuel_time_window': 1,
                'reference_price': 1.01,
                'reference_spread': 0.0007,
                'type': None,
                'file_name': f'CDS_Index_Tranche_{date.strftime("%Y%m%d")}.csv',
                'knowledge_date': knowledge_dt,
                'load_date': load_dt
            })
    
    df = pd.DataFrame(dummy_data)
    logging.info('✨ Created dummy markit_cds_index_tranche with shape: %s', df.shape)
    return df


def init_cds_index_tranche_table(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('=' * 70)
    logging.info('🚀 INITIALIZING CDS INDEX TRANCHE TABLE')
    logging.info('=' * 70)
    
    logging.info('📊 Creating markit_cds_index_tranche table with dummy data')
    df_data = create_dummy_cds_index_tranche_data()
    sparkmanager.pandastodelta(df_data, 'markit_cds_index_tranche', 'tables/dbo', 'asof_date', overwrite=overwrite)
    logging.info('✓ markit_cds_index_tranche table created with schema (partitioned by asof_date)')
    
    logging.info('🧹 Deleting dummy data from markit_cds_index_tranche')
    empty_data = pd.DataFrame(columns=df_data.columns)
    sparkmanager.pandastodelta(empty_data.head(0), 'markit_cds_index_tranche', 'tables/dbo', 'asof_date', overwrite=True)
    logging.info('✓ markit_cds_index_tranche table now empty')
    
    logging.info('=' * 70)
    logging.info('✅ CDS INDEX TRANCHE TABLE INITIALIZED (EMPTY) SUCCESSFULLY')
    logging.info('=' * 70)


if __name__ == '__main__':
    init_cds_index_tranche_table(overwrite=True)

