import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config

TABLE_NAME = 'covenant_review_scores'


def create_dummy_covenant_review_data():
    now = datetime.now()
    
    dummy_data = [{
        'lxid': 'LX999999',
        'lfiid': '9999',
        'borrower': 'Test Company Inc',
        'status': 'Final',
        'version_count': '1',
        'scoring_composite_n': 3.5,
        'scoring_composite_s': 'B+',
        'scoring_documentation_n': 3.0,
        'scoring_flexibility_n': 4.0,
        'scoring_restrictiveness_n': 3.5,
        '_load_timestamp': now
    }]
    
    df = pd.DataFrame(dummy_data)
    logging.info('Created dummy %s: %s', TABLE_NAME, df.shape)
    return df


def init_covenant_review_tables(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('=' * 70)
    logging.info('🚀 INITIALIZING COVENANT REVIEW TABLES')
    logging.info('=' * 70)
    
    logging.info('📊 Creating %s table with dummy data', TABLE_NAME)
    df = create_dummy_covenant_review_data()
    sparkmanager.pandastodelta(df, TABLE_NAME, 'tables/dbo', overwrite=overwrite)
    logging.info('✅ %s table created with schema', TABLE_NAME)
    
    logging.info('🗑️  Deleting dummy data from %s', TABLE_NAME)
    empty_df = pd.DataFrame(columns=df.columns)
    sparkmanager.pandastodelta(empty_df.head(0), TABLE_NAME, 'tables/dbo', overwrite=True)
    logging.info('✅ %s table now empty', TABLE_NAME)
    
    logging.info('=' * 70)
    logging.info('🎉 COVENANT REVIEW TABLES INITIALIZED (EMPTY) SUCCESSFULLY')
    logging.info('=' * 70)


if __name__ == '__main__':
    init_covenant_review_tables(overwrite=True)

