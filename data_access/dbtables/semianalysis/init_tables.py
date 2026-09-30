import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


def create_dummy_ai_demand_by_customer():
    now = datetime.now()
    dummy_data = [{
        'customer_company': 'Meta',
        'region': 'North America',
        'year': 2024,
        'quarter': 'Q4',
        'chip_type': 'H100',
        'units_demanded': 100000,
        'training_units': 80000,
        'inference_units': 20000,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy ai_demand_by_customer: {df.shape}')
    return df


def create_dummy_power_requirements_per_chip():
    now = datetime.now()
    dummy_data = [{
        'chip_model': 'H100',
        'manufacturer': 'NVIDIA',
        'tdp_watts': 700,
        'generation': 'Hopper',
        'release_year': 2022,
        'architecture': 'GPU',
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy power_requirements_per_chip: {df.shape}')
    return df


def create_dummy_data_center_facilities_na():
    now = datetime.now()
    dummy_data = [{
        'facility_name': 'Test DC 1',
        'company': 'Meta',
        'city': 'Prineville',
        'state': 'Oregon',
        'country': 'United States',
        'region': 'North America',
        'latitude': 44.3,
        'longitude': -120.8,
        'capacity_mw': 150,
        'status': 'Live',
        'construction_start_date': datetime(2020, 1, 1),
        'live_date': datetime(2022, 6, 1),
        'square_footage': 500000,
        'total_planned_mw': 300,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy data_center_facilities_na: {df.shape}')
    return df


def create_dummy_data_center_facilities_overseas():
    now = datetime.now()
    dummy_data = [{
        'facility_name': 'Test DC Europe 1',
        'company': 'Google',
        'city': 'Dublin',
        'country': 'Ireland',
        'region': 'Europe',
        'latitude': 53.3,
        'longitude': -6.2,
        'capacity_mw': 100,
        'status': 'Under Construction',
        'construction_start_date': datetime(2023, 1, 1),
        'live_date': None,
        'square_footage': 400000,
        'total_planned_mw': 200,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy data_center_facilities_overseas: {df.shape}')
    return df


def create_dummy_global_supply_demand_forecast():
    now = datetime.now()
    dummy_data = [{
        'region': 'North America',
        'year': 2024,
        'quarter': 'Q4',
        'supply_mw': 5000,
        'demand_mw': 4500,
        'gap_mw': 500,
        'utilization_rate': 0.90,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy global_supply_demand_forecast: {df.shape}')
    return df


def create_dummy_regional_forecast():
    now = datetime.now()
    dummy_data = [{
        'region': 'North America',
        'country': 'United States',
        'year': 2024,
        'total_capacity_mw': 4000,
        'ai_capacity_mw': 2000,
        'traditional_capacity_mw': 2000,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy regional_forecast: {df.shape}')
    return df


def create_dummy_country_datacenter_capacity():
    now = datetime.now()
    dummy_data = [{
        'country': 'United States',
        'region': 'North America',
        'year': 2024,
        'quarter': 'Q4',
        'total_capacity_mw': 3500,
        'self_build_mw': 2500,
        'colocation_mw': 1000,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy country_datacenter_capacity: {df.shape}')
    return df


def create_dummy_hyperscaler_capacity():
    now = datetime.now()
    dummy_data = [{
        'company': 'Meta',
        'facility_name': 'Meta DC 1',
        'region': 'North America',
        'year': 2024,
        'quarter': 'Q4',
        'total_capacity_mw': 200,
        'self_build_mw': 150,
        'leased_mw': 50,
        'ai_capacity_mw': 120,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy hyperscaler_capacity: {df.shape}')
    return df


def create_dummy_data_center_capex():
    now = datetime.now()
    dummy_data = [{
        'year': 2024,
        'quarter': 'Q4',
        'region': 'North America',
        'cost_per_mw_million': 15.0,
        'equipment_cost_pct': 0.60,
        'construction_cost_pct': 0.30,
        'land_cost_pct': 0.10,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy data_center_capex: {df.shape}')
    return df


def create_dummy_liquid_cooling_demand():
    now = datetime.now()
    dummy_data = [{
        'year': 2024,
        'quarter': 'Q4',
        'chip_type': 'H100',
        'total_mw_liquid_cooled': 500,
        'penetration_rate': 0.25,
        'liquid_cooling_type': 'Direct-to-Chip',
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy liquid_cooling_demand: {df.shape}')
    return df


def create_dummy_china_datacenters():
    now = datetime.now()
    dummy_data = [{
        'facility_name': 'Beijing DC 1',
        'province': 'Beijing',
        'city': 'Beijing',
        'capacity_mw': 80,
        'year': 2024,
        'status': 'Live',
        'operator': 'Alibaba',
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy china_datacenters: {df.shape}')
    return df


def create_dummy_global_power_generation():
    now = datetime.now()
    dummy_data = [{
        'country': 'United States',
        'region': 'North America',
        'year': 2024,
        'total_generation_gw': 4500,
        'renewable_generation_gw': 1000,
        'renewable_pct': 0.22,
        'datacenter_consumption_gw': 50,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy global_power_generation: {df.shape}')
    return df


def create_dummy_power_prices():
    now = datetime.now()
    dummy_data = [{
        'region': 'North America',
        'state': 'Oregon',
        'city': 'Prineville',
        'year': 2023,
        'avg_price_per_kwh': 0.045,
        'colocation_price_per_kw_month': 150,
        'ppa_available': True,
        'knowledge_date': now,
        'load_date': now
    }]
    df = pd.DataFrame(dummy_data)
    logging.info(f'Created dummy power_prices: {df.shape}')
    return df


def init_semianalysis_tables(overwrite=True):
    setuplogger()
    config.init('dev')
    sparkmanager.init()
    
    logging.info('='*80)
    logging.info('🚀 INITIALIZING SEMIANALYSIS DATA CENTER TABLES')
    logging.info('='*80)
    
    tables = [
        ('semianalysis_ai_demand_by_customer', create_dummy_ai_demand_by_customer, None),
        ('semianalysis_power_requirements_per_chip', create_dummy_power_requirements_per_chip, None),
        ('semianalysis_data_center_facilities_na', create_dummy_data_center_facilities_na, None),
        ('semianalysis_data_center_facilities_overseas', create_dummy_data_center_facilities_overseas, None),
        ('semianalysis_global_supply_demand_forecast', create_dummy_global_supply_demand_forecast, 'year'),
        ('semianalysis_regional_forecast', create_dummy_regional_forecast, 'year'),
        ('semianalysis_country_datacenter_capacity', create_dummy_country_datacenter_capacity, 'year'),
        ('semianalysis_hyperscaler_capacity', create_dummy_hyperscaler_capacity, 'year'),
        ('semianalysis_data_center_capex', create_dummy_data_center_capex, 'year'),
        ('semianalysis_liquid_cooling_demand', create_dummy_liquid_cooling_demand, 'year'),
        ('semianalysis_china_datacenters', create_dummy_china_datacenters, 'year'),
        ('semianalysis_global_power_generation', create_dummy_global_power_generation, 'year'),
        ('semianalysis_power_prices', create_dummy_power_prices, 'year'),
    ]
    
    for table_name, create_func, partition_col in tables:
        logging.info(f'📊 Creating table: {table_name}')
        df = create_func()
        sparkmanager.pandastodelta(df, table_name, 'tables/dbo', partition_col, overwrite=overwrite)
        logging.info(f'✅ {table_name} created with schema')
        
        logging.info(f'🗑️  Clearing dummy data from {table_name}')
        empty_df = pd.DataFrame(columns=df.columns)
        sparkmanager.pandastodelta(empty_df.head(0), table_name, 'tables/dbo', partition_col, overwrite=True)
        logging.info(f'✅ {table_name} now empty')
    
    logging.info('='*80)
    logging.info('🎉 ALL SEMIANALYSIS TABLES INITIALIZED SUCCESSFULLY')
    logging.info('='*80)


if __name__ == '__main__':
    init_semianalysis_tables(overwrite=True)

