"""
Initialize CreditSights Covenant Scores Table

This script creates the Delta table schema for storing Covenant Review data.
Run this once to create the table structure in Databricks.
"""

import logging
from datetime import datetime

import pandas as pd

from data_access.spark.api import sparkmanager
from common.utils.logutil import setuplogger
from apo_config.config import config


TABLE_NAME = 'creditsights_covenant_scores'
BLOB_DIR = 'tables/dbo'


def create_dummy_covenant_data():
    """Create dummy data with the expected schema."""
    now = datetime.now()
    
    dummy_data = [{
        # Identifiers
        'lxid': 'LX000000',
        'id_cusip': '00000000',
        'borrower': 'Sample Borrower Inc.',
        'status': 'Final',
        'version_count': '1',
        'record_priority': 1,
        'tranche_name': 'Term Loan B',
        'lxid_tracking_status': 'Active',
        
        # Dates
        'date_launch': pd.to_datetime('2024-01-15'),
        'date_maturity_current': pd.to_datetime('2031-01-15'),
        'date_most_recent_doc': pd.to_datetime('2024-01-10'),
        'date_research': pd.to_datetime('2024-01-20'),
        'time_quarter': '2024Q1',
        
        # Deal information
        'arrangers': 'Sample Bank',
        'industry_lfi': 'Technology',
        'purpose_lfi': 'LBO',
        'sponsor_lfi': 'Sample PE',
        'sponsored': True,
        'size_current': 500.0,
        'current_term': 7.0,
        'tenor_updated': 7.0,
        'spread_current': 350.0,
        'call_current': 101.0,
        'ytm_current': 8.5,
        'ratings_corp': 'B',
        'ratings_facility': 'B+',
        'data_coverage': 'Full',
        
        # EBITDA
        'ebitda': 100.0,
        'ebitda_adj_acquisitions': 10.0,
        'ebitda_adj_additional': 5.0,
        'ebitda_adj_synergies_cost_savings': 15.0,
        'ebitda_reported_all': 80.0,
        'firstlienlev': 4.5,
        
        # Composite scores
        'scoring_composite_n': 4.0,
        'scoring_composite_s': 4.0,
        
        # Builder basket
        'bb_appl': 'Available',
        'bb_hc': 50.0,
        'bb_sc': 25.0,
        'bb_score_starter': 0.5,
        
        # Accordion
        'accordion_hc': 100.0,
        'accordion_sc': 50.0,
        'accordion_inside_mat_exists': True,
        'accordion_ratio_net_fl': 4.5,
        'accordion_ratio_net_sec': 5.0,
        'accordion_ratio_net_total': 5.5,
        
        # Debt covenants
        'debt_gen_hc': 100.0,
        'debt_gen_hc_turns': 0.5,
        'debt_gen_sc_ebitda': 25.0,
        'debt_nlp_hc': 50.0,
        'debt_nlp_sc': 25.0,
        'debt_ratio_net_fl': 4.5,
        'debt_ratio_net_total': 5.5,
        
        # Investment covenants
        'inv_gen_hc': 100.0,
        'inv_gen_hc_turns': 0.5,
        'inv_gen_sc_ebitda': 25.0,
        'inv_ratio_net_fl': 4.5,
        'inv_ratio_net_sec': 5.0,
        'inv_ratio_net_total': 5.5,
        
        # Restricted payments
        'rp_gen_hc': 50.0,
        'rp_gen_hc_turns': 0.25,
        'rp_gen_sc_ebitda': 25.0,
        'rp_ratio_net_fl': 4.0,
        'rp_ratio_net_sec': 4.5,
        'rp_ratio_net_total': 5.0,
        
        # Maintenance covenants
        'maintenance_cov_lite': True,
        'maintenance_cov_lite_clo': True,
        'maintenance_ratio_lvl_1': 5.0,
        'maintenance_ratio_net_fl': 4.5,
        
        # MFN
        'mfn_bps': 50.0,
        'mfn_carveout_hc': 25.0,
        'mfn_sunset': '18 months',
        
        # Scores dataset columns
        'adjusted_ebitda_n': 4.0,
        'adjusted_ebitda_s': 4.0,
        'amendments_n': 3.5,
        'amendments_s': 3.5,
        'builder_basket_n': 4.0,
        'builder_basket_s': 4.0,
        'collateral_protection_n': 4.0,
        'collateral_protection_s': 4.0,
        'composite_score_n': 4.0,
        'composite_score_s': 4.0,
        'default_protection_n': 4.0,
        'default_protection_s': 4.0,
        'lenders_repricing_optionality_n': 3.5,
        'lenders_repricing_optionality_s': 3.5,
        'mandatory_prepayments_n': 4.0,
        'mandatory_prepayments_s': 4.0,
        'mfn_protection': 'Strong',
        
        # Metadata
        '_load_timestamp': now,
        '_source': 'covenant_review_api',
    }]
    
    df = pd.DataFrame(dummy_data)
    logging.info('Created dummy creditsights_covenant_scores with shape: %s', df.shape)
    return df


def init_covenant_scores_table(overwrite=True):
    """
    Initialize the creditsights_covenant_scores table.
    
    Creates the table with proper schema, then empties it.
    """
    setuplogger('covreview_init', logconsole=True)
    config.init('dev')
    sparkmanager.init()
    
    logging.info('=' * 70)
    logging.info('INITIALIZING CREDITSIGHTS COVENANT SCORES TABLE')
    logging.info('=' * 70)
    
    # Create table with dummy data to establish schema
    logging.info('Creating %s table with schema...', TABLE_NAME)
    df = create_dummy_covenant_data()
    
    sparkmanager.pandastodelta(
        df=df, 
        table_name=TABLE_NAME, 
        blob_dir=BLOB_DIR, 
        overwrite=overwrite
    )
    logging.info('✓ Table created with schema')
    
    # Empty the table (keep schema)
    logging.info('Clearing dummy data...')
    empty_df = pd.DataFrame(columns=df.columns)
    sparkmanager.pandastodelta(
        df=empty_df.head(0), 
        table_name=TABLE_NAME, 
        blob_dir=BLOB_DIR, 
        overwrite=True
    )
    logging.info('✓ Table emptied')
    
    logging.info('=' * 70)
    logging.info('✓ %s TABLE INITIALIZED SUCCESSFULLY', TABLE_NAME.upper())
    logging.info('=' * 70)
    
    print(f'''
Table created: dbo.{TABLE_NAME}

You can verify in Databricks with:
    SELECT * FROM dbo.{TABLE_NAME} LIMIT 10;
    DESCRIBE TABLE dbo.{TABLE_NAME};
''')


# SQL to create the table manually (alternative approach)
CREATE_TABLE_SQL = '''
-- Run this in Databricks SQL to create the table manually if needed

CREATE TABLE IF NOT EXISTS dbo.creditsights_covenant_scores (
    -- Identifiers
    lxid STRING,
    id_cusip STRING,
    borrower STRING,
    status STRING,
    version_count STRING,
    record_priority INT,
    tranche_name STRING,
    lxid_tracking_status STRING,
    
    -- Dates
    date_launch TIMESTAMP,
    date_maturity_current TIMESTAMP,
    date_most_recent_doc TIMESTAMP,
    date_research TIMESTAMP,
    time_quarter STRING,
    
    -- Deal information
    arrangers STRING,
    industry_lfi STRING,
    purpose_lfi STRING,
    sponsor_lfi STRING,
    sponsored BOOLEAN,
    size_current DOUBLE,
    current_term DOUBLE,
    tenor_updated DOUBLE,
    spread_current DOUBLE,
    call_current DOUBLE,
    ytm_current DOUBLE,
    ratings_corp STRING,
    ratings_facility STRING,
    data_coverage STRING,
    
    -- EBITDA
    ebitda DOUBLE,
    ebitda_adj_acquisitions DOUBLE,
    ebitda_adj_additional DOUBLE,
    ebitda_adj_synergies_cost_savings DOUBLE,
    ebitda_reported_all DOUBLE,
    firstlienlev DOUBLE,
    
    -- Composite scores
    scoring_composite_n DOUBLE,
    scoring_composite_s DOUBLE,
    
    -- Builder basket
    bb_appl STRING,
    bb_hc DOUBLE,
    bb_sc DOUBLE,
    bb_score_starter DOUBLE,
    
    -- Accordion
    accordion_hc DOUBLE,
    accordion_sc DOUBLE,
    accordion_inside_mat_exists BOOLEAN,
    accordion_ratio_net_fl DOUBLE,
    accordion_ratio_net_sec DOUBLE,
    accordion_ratio_net_total DOUBLE,
    
    -- Debt covenants
    debt_gen_hc DOUBLE,
    debt_gen_hc_turns DOUBLE,
    debt_gen_sc_ebitda DOUBLE,
    debt_nlp_hc DOUBLE,
    debt_nlp_sc DOUBLE,
    debt_ratio_net_fl DOUBLE,
    debt_ratio_net_total DOUBLE,
    
    -- Investment covenants
    inv_gen_hc DOUBLE,
    inv_gen_hc_turns DOUBLE,
    inv_gen_sc_ebitda DOUBLE,
    inv_ratio_net_fl DOUBLE,
    inv_ratio_net_sec DOUBLE,
    inv_ratio_net_total DOUBLE,
    
    -- Restricted payments
    rp_gen_hc DOUBLE,
    rp_gen_hc_turns DOUBLE,
    rp_gen_sc_ebitda DOUBLE,
    rp_ratio_net_fl DOUBLE,
    rp_ratio_net_sec DOUBLE,
    rp_ratio_net_total DOUBLE,
    
    -- Maintenance covenants
    maintenance_cov_lite BOOLEAN,
    maintenance_cov_lite_clo BOOLEAN,
    maintenance_ratio_lvl_1 DOUBLE,
    maintenance_ratio_net_fl DOUBLE,
    
    -- MFN
    mfn_bps DOUBLE,
    mfn_carveout_hc DOUBLE,
    mfn_sunset STRING,
    
    -- Scores
    adjusted_ebitda_n DOUBLE,
    adjusted_ebitda_s DOUBLE,
    amendments_n DOUBLE,
    amendments_s DOUBLE,
    builder_basket_n DOUBLE,
    builder_basket_s DOUBLE,
    collateral_protection_n DOUBLE,
    collateral_protection_s DOUBLE,
    composite_score_n DOUBLE,
    composite_score_s DOUBLE,
    default_protection_n DOUBLE,
    default_protection_s DOUBLE,
    lenders_repricing_optionality_n DOUBLE,
    lenders_repricing_optionality_s DOUBLE,
    mandatory_prepayments_n DOUBLE,
    mandatory_prepayments_s DOUBLE,
    mfn_protection STRING,
    
    -- Metadata
    _load_timestamp TIMESTAMP,
    _source STRING
)
USING DELTA
LOCATION 'abfss://apollo-market-data@apolloaboraborabo.dfs.core.windows.net/tables/dbo/creditsights_covenant_scores'
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact' = 'true'
);
'''


if __name__ == '__main__':
    init_covenant_scores_table(overwrite=True)

