import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


def create_dummy_similarweb_data():
    dates = pd.date_range('2024-01-01', '2024-01-31', freq='D')
    now = datetime.now()
    
    dummy_data = []
    domains = [
        ('chatgpt.com', 'AI Tools', 'General'),
        ('claude.ai', 'AI Tools', 'General'),
        ('gemini.google.com', 'AI Tools', 'General'),
        ('midjourney.com', 'AI Tools', 'Design & Image Generation'),
        ('canva.com', 'Design Platforms', 'Canva')
    ]
    
    metrics = [
        'all_traffic_visits',
        'desktop_visits',
        'mobile_visits',
        'deduplicated_audience',
        'all_traffic_average_visit_duration',
        'desktop_unique_visitors',
        'mobile_unique_visitors'
    ]
    
    for domain, sector, subsector in domains:
        for date in dates:
            for metric in metrics:
                base_value = 1000000 if 'visits' in metric or 'visitors' in metric or 'audience' in metric else 50.0
                dummy_data.append({
                    'asof_date': date,
                    'domain': domain,
                    'sector': sector,
                    'subsector': subsector,
                    'frequency': 'daily',
                    'metric_name': metric,
                    'metric_value': base_value + (date.day * 1000),
                    'knowledge_date': now,
                    'load_date': now
                })
    
    df = pd.DataFrame(dummy_data)
    logging.info('✨ Created dummy similarweb_data: %s rows', len(df))
    return df


def init_similarweb_tables(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('🚀 INITIALIZING SIMILARWEB TABLES')
    
    logging.info('📊 Creating similarweb_data table with dummy data')
    df_data = create_dummy_similarweb_data()
    sparkmanager.pandastodelta(df_data, 'similarweb_data', 'tables/dbo', 'asof_date', overwrite=overwrite)
    logging.info('✅ similarweb_data table created with schema')
    
    logging.info('🗑️  Deleting dummy data from similarweb_data')
    empty_data = pd.DataFrame(columns=df_data.columns)
    sparkmanager.pandastodelta(empty_data.head(0), 'similarweb_data', 'tables/dbo', 'asof_date', overwrite=True)
    logging.info('✅ similarweb_data table now empty')
    
    logging.info('🎉 SIMILARWEB TABLES INITIALIZED (EMPTY) SUCCESSFULLY')


if __name__ == '__main__':
    init_similarweb_tables(overwrite=True)

