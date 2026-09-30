"""
SimilarWeb Data Comparison Script
Compares data from SQL Server (Databricks) vs Snowflake for specific domains.

Usage:
    python compare_similarweb_sources.py

Configuration:
    - Databricks: Uses HAVER_APP for dbo.similarweb_data
    - Snowflake: Uses SIMILARWEB_SNOWFLAKE_APP for apollo_similarweb tables
    
    Set the Snowflake app based on your configuration:
    - 'factset' - Uses FactSet service account
    - 'snowflake_similarweb' - If configured separately
    - Or configure in apo_config for your environment

Author: Data Access Team
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from common.utils.sqlutil import readdfsql, getsnowflakeconn
from common.utils.logutil import setuplogger
from apo_config.config import config
from data_access.dbtables import HAVER_APP

# Default Snowflake app for SimilarWeb - can be overridden
# Options: 'factset', 'snowflake_metis', 'snowflake_portfolio', 'snowflake_reference_db'
SIMILARWEB_SNOWFLAKE_APP = 'factset'

# Target AI domains for comparison (Databricks uses these exact names)
AI_DOMAINS = [
    'chatgpt.com',
    'claude.ai', 
    'gemini.google.com',
    'perplexity.ai',
    'grok.com',
    'deepseek.com'
]

# Snowflake domain list (same as AI_DOMAINS for V2 tables)
SNOWFLAKE_DOMAINS = [
    'chatgpt.com',
    'claude.ai', 
    'gemini.google.com',
    'perplexity.ai',
    'grok.com',
    'deepseek.com'
]

# Domain mapping: Databricks name -> Snowflake name (empty for V2 tables)
DOMAIN_MAPPING = {}


def get_databricks_similarweb_data(
    domains: List[str],
    start_date: str = '2025-10-01'
) -> pd.DataFrame:
    """
    Query SimilarWeb data from Databricks (SQL Server)
    
    Args:
        domains: List of domains to query
        start_date: Start date for the query
        
    Returns:
        DataFrame with SimilarWeb data from Databricks
    """
    domain_list = ", ".join([f"'{d}'" for d in domains])
    
    sql = f"""
    SELECT * 
    FROM dbo.similarweb_data 
    WHERE domain IN ({domain_list})
    AND asof_date > '{start_date}'
    ORDER BY domain, asof_date DESC
    """
    
    logging.info(f"Querying Databricks SimilarWeb data for {len(domains)} domains since {start_date}")
    df = readdfsql(sql, app=HAVER_APP)
    
    # Normalize column names to lowercase for consistency
    df.columns = df.columns.str.lower()
    
    logging.info(f"Retrieved {len(df)} rows from Databricks")
    return df


def get_snowflake_daily_data(
    domains: List[str],
    start_date: str = '2025-10-01',
    end_date: str = '2026-01-19',
    snowflake_app: str = None
) -> pd.DataFrame:
    """
    Query daily SimilarWeb data from Snowflake using the FactSet service account
    
    Args:
        domains: List of domains to query
        start_date: Start date for the query
        end_date: End date for the query
        snowflake_app: Snowflake app configuration to use (defaults to SIMILARWEB_SNOWFLAKE_APP)
        
    Returns:
        DataFrame with daily SimilarWeb data from Snowflake
    """
    snowflake_app = snowflake_app or SIMILARWEB_SNOWFLAKE_APP
    domain_list = ", ".join([f"'{d}'" for d in domains])
    
    sql = f"""
    WITH base AS (
      SELECT
        date AS asof_date,
        created_date,
        domain,
        country,
        all_traffic_visits,
        desktop_visits,
        mobile_visits
      FROM apollo_similarweb.public.apollo_daily_ggs_sample_subdomain_agg_10215125
      WHERE date BETWEEN '{start_date}' AND '{end_date}'
      QUALIFY created_date = MAX(created_date) OVER (
        PARTITION BY date, domain, country
      )
    ),
    agg AS (
      SELECT
        asof_date,
        domain,
        SUM(all_traffic_visits)  AS all_traffic_visits,
        SUM(desktop_visits)      AS desktop_visits,
        SUM(mobile_visits)       AS mobile_visits
      FROM base
      GROUP BY 1, 2
    ),
    domain_window AS (
      SELECT
        domain,
        AVG(all_traffic_visits) AS avg_daily_all_traffic_visits
      FROM agg
      GROUP BY 1
    ),
    ranked_domains AS (
      SELECT
        domain,
        avg_daily_all_traffic_visits,
        DENSE_RANK() OVER (ORDER BY avg_daily_all_traffic_visits DESC) AS traffic_rank_avg_daily
      FROM domain_window
    ),
    ranked_sites AS (
      SELECT
        a.*,
        rd.avg_daily_all_traffic_visits,
        rd.traffic_rank_avg_daily
      FROM agg a
      JOIN ranked_domains rd
        USING (domain)
    )
    SELECT *
    FROM ranked_sites
    WHERE domain IN ({domain_list})
    ORDER BY asof_date DESC, avg_daily_all_traffic_visits DESC, all_traffic_visits DESC
    """
    
    logging.info(f"Querying Snowflake daily data for {len(domains)} domains from {start_date} to {end_date}")
    logging.info(f"Using Snowflake app: {snowflake_app}")
    
    # Use configured Snowflake app for connection
    df = readdfsql(sql, app=snowflake_app)
    
    # Normalize column names to lowercase (Snowflake returns uppercase)
    df.columns = df.columns.str.lower()
    
    logging.info(f"Retrieved {len(df)} rows from Snowflake daily table")
    return df


def get_snowflake_monthly_data(
    domains: List[str],
    start_date: str = '2025-01-01',
    end_date: str = '2026-01-19',
    snowflake_app: str = None
) -> pd.DataFrame:
    """
    Query monthly SimilarWeb data from Snowflake using the FactSet service account
    
    Args:
        domains: List of domains to query
        start_date: Start date for the query
        end_date: End date for the query
        snowflake_app: Snowflake app configuration to use (defaults to SIMILARWEB_SNOWFLAKE_APP)
        
    Returns:
        DataFrame with monthly SimilarWeb data from Snowflake
    """
    snowflake_app = snowflake_app or SIMILARWEB_SNOWFLAKE_APP
    domain_list = ", ".join([f"'{d}'" for d in domains])
    
    sql = f"""
    WITH base AS (
      SELECT
        domain,
        country,
        yearmonth,
        created_date,
        all_traffic_visits,
        deduplicated_audience,
        desktop_visits,
        mobile_visits,
        total_new_visitors,
        total_returning_visitors
      FROM apollo_similarweb.public.apollo_monthly_ggs_sample_v2_10215125
      WHERE TO_DATE(yearmonth || '-01') BETWEEN '{start_date}' AND '{end_date}'
    ),
    latest_snapshot AS (
      -- First get latest created_date per domain/country/yearmonth
      SELECT *
      FROM base
      QUALIFY created_date = MAX(created_date) OVER (PARTITION BY domain, country, yearmonth)
    ),
    deduped AS (
      -- Then dedupe by taking one row per domain/country/yearmonth (ranked by age if exists, else arbitrary)
      SELECT *
      FROM latest_snapshot
      QUALIFY ROW_NUMBER() OVER (PARTITION BY domain, country, yearmonth ORDER BY created_date DESC) = 1
    ),
    domain_window AS (
      SELECT
        domain,
        AVG(all_traffic_visits) AS avg_monthly_all_traffic_visits
      FROM deduped
      GROUP BY 1
    ),
    ranked_domains AS (
      SELECT
        domain,
        avg_monthly_all_traffic_visits,
        DENSE_RANK() OVER (ORDER BY avg_monthly_all_traffic_visits DESC) AS traffic_rank_avg_monthly
      FROM domain_window
    )
    SELECT
      d.*,
      rd.avg_monthly_all_traffic_visits,
      rd.traffic_rank_avg_monthly
    FROM deduped d
    JOIN ranked_domains rd
      USING (domain)
    WHERE d.domain IN ({domain_list})
    ORDER BY TO_DATE(d.yearmonth || '-01') DESC, rd.avg_monthly_all_traffic_visits DESC, d.all_traffic_visits DESC
    """
    
    logging.info(f"Querying Snowflake monthly data for {len(domains)} domains from {start_date} to {end_date}")
    logging.info(f"Using Snowflake app: {snowflake_app}")
    
    # Use configured Snowflake app for connection
    df = readdfsql(sql, app=snowflake_app)
    
    # Normalize column names to lowercase (Snowflake returns uppercase)
    df.columns = df.columns.str.lower()
    
    logging.info(f"Retrieved {len(df)} rows from Snowflake monthly table")
    return df


def compare_traffic_metrics(
    db_df: pd.DataFrame,
    sf_daily_df: pd.DataFrame,
    sf_monthly_df: pd.DataFrame,
    domains: List[str]
) -> Dict[str, pd.DataFrame]:
    """
    Compare traffic metrics between Databricks and Snowflake data
    
    Args:
        db_df: DataFrame from Databricks
        sf_daily_df: Daily DataFrame from Snowflake
        sf_monthly_df: Monthly DataFrame from Snowflake
        domains: List of domains to analyze
        
    Returns:
        Dictionary containing comparison DataFrames
    """
    results = {}
    
    # Summary stats for each source
    logging.info("\n" + "="*80)
    logging.info("DATA SOURCE COMPARISON SUMMARY")
    logging.info("="*80)
    
    # 1. Domain coverage comparison
    db_domains = set(db_df['domain'].unique()) if not db_df.empty else set()
    sf_daily_domains = set(sf_daily_df['domain'].unique()) if not sf_daily_df.empty else set()
    sf_monthly_domains = set(sf_monthly_df['domain'].unique()) if not sf_monthly_df.empty else set()
    
    domain_coverage = pd.DataFrame({
        'domain': domains,
        'in_databricks': [d in db_domains for d in domains],
        'in_snowflake_daily': [d in sf_daily_domains for d in domains],
        'in_snowflake_monthly': [d in sf_monthly_domains for d in domains]
    })
    results['domain_coverage'] = domain_coverage
    
    logging.info("\n--- Domain Coverage ---")
    logging.info(f"\n{domain_coverage.to_string()}")
    
    # 2. Date range comparison
    date_ranges = []
    
    if not db_df.empty:
        db_dates = pd.to_datetime(db_df['asof_date'])
        date_ranges.append({
            'source': 'Databricks',
            'min_date': db_dates.min(),
            'max_date': db_dates.max(),
            'record_count': len(db_df)
        })
    
    if not sf_daily_df.empty:
        sf_daily_dates = pd.to_datetime(sf_daily_df['asof_date'])
        date_ranges.append({
            'source': 'Snowflake Daily',
            'min_date': sf_daily_dates.min(),
            'max_date': sf_daily_dates.max(),
            'record_count': len(sf_daily_df)
        })
    
    if not sf_monthly_df.empty:
        # Monthly data uses yearmonth format
        sf_monthly_dates = pd.to_datetime(sf_monthly_df['yearmonth'] + '-01')
        date_ranges.append({
            'source': 'Snowflake Monthly',
            'min_date': sf_monthly_dates.min(),
            'max_date': sf_monthly_dates.max(),
            'record_count': len(sf_monthly_df)
        })
    
    date_range_df = pd.DataFrame(date_ranges)
    results['date_ranges'] = date_range_df
    
    logging.info("\n--- Date Ranges ---")
    logging.info(f"\n{date_range_df.to_string()}")
    
    # 3. Traffic metrics comparison by domain (for Snowflake daily data)
    if not sf_daily_df.empty:
        daily_summary = sf_daily_df.groupby('domain').agg({
            'all_traffic_visits': ['mean', 'sum', 'min', 'max'],
            'desktop_visits': ['mean', 'sum'],
            'mobile_visits': ['mean', 'sum']
        }).round(0)
        daily_summary.columns = ['_'.join(col).strip() for col in daily_summary.columns.values]
        daily_summary = daily_summary.reset_index()
        results['snowflake_daily_summary'] = daily_summary
        
        logging.info("\n--- Snowflake Daily Traffic Summary by Domain ---")
        logging.info(f"\n{daily_summary.to_string()}")
    
    # 4. Monthly traffic summary
    if not sf_monthly_df.empty:
        monthly_summary = sf_monthly_df.groupby(['domain', 'yearmonth']).agg({
            'all_traffic_visits': 'sum',
            'deduplicated_audience': 'sum'
        }).round(0).reset_index()
        
        # Pivot for easier comparison
        monthly_pivot = monthly_summary.pivot(
            index='yearmonth', 
            columns='domain', 
            values='all_traffic_visits'
        ).fillna(0)
        results['snowflake_monthly_pivot'] = monthly_pivot
        
        logging.info("\n--- Snowflake Monthly Traffic by Domain (All Traffic Visits) ---")
        logging.info(f"\n{monthly_pivot.to_string()}")
    
    # 5. Databricks data structure analysis
    if not db_df.empty:
        db_metrics = db_df.groupby(['domain', 'metric_name']).agg({
            'metric_value': ['mean', 'min', 'max', 'count']
        }).round(2)
        db_metrics.columns = ['_'.join(col).strip() for col in db_metrics.columns.values]
        db_metrics = db_metrics.reset_index()
        results['databricks_metrics_summary'] = db_metrics
        
        logging.info("\n--- Databricks Metrics Summary ---")
        logging.info(f"\n{db_metrics.to_string()}")
        
        # Show unique metrics available
        unique_metrics = db_df['metric_name'].unique().tolist()
        logging.info(f"\n--- Available Metrics in Databricks ---")
        logging.info(f"Metrics: {unique_metrics}")
    
    return results


def align_and_compare_values(
    db_df: pd.DataFrame,
    sf_daily_df: pd.DataFrame,
    metric_name: str = 'all_traffic_visits'
) -> pd.DataFrame:
    """
    Align Databricks and Snowflake daily data by date and domain for direct comparison
    
    Args:
        db_df: DataFrame from Databricks
        sf_daily_df: Daily DataFrame from Snowflake
        metric_name: Metric to compare
        
    Returns:
        DataFrame with aligned comparison
    """
    if db_df.empty or sf_daily_df.empty:
        logging.warning("One or both DataFrames are empty - cannot perform alignment")
        return pd.DataFrame()
    
    # Filter Databricks data for the specific metric
    db_filtered = db_df[db_df['metric_name'].str.lower() == metric_name.lower()].copy()
    
    if db_filtered.empty:
        logging.warning(f"No '{metric_name}' metric found in Databricks data")
        return pd.DataFrame()
    
    # Standardize date columns
    db_filtered['date'] = pd.to_datetime(db_filtered['asof_date']).dt.date
    sf_daily_df['date'] = pd.to_datetime(sf_daily_df['asof_date']).dt.date
    
    # Merge on date and domain
    merged = pd.merge(
        db_filtered[['date', 'domain', 'metric_value']].rename(columns={'metric_value': 'db_value'}),
        sf_daily_df[['date', 'domain', 'all_traffic_visits']].rename(columns={'all_traffic_visits': 'sf_value'}),
        on=['date', 'domain'],
        how='outer',
        indicator=True
    )
    
    # Calculate difference
    merged['difference'] = merged['db_value'] - merged['sf_value']
    merged['pct_diff'] = ((merged['db_value'] - merged['sf_value']) / merged['sf_value'] * 100).round(2)
    
    logging.info("\n--- Value Alignment Comparison ---")
    logging.info(f"\n{merged.to_string()}")
    
    return merged


def run_comparison(
    domains: List[str] = None,
    db_start_date: str = '2025-10-01',
    sf_daily_start_date: str = '2025-10-01',
    sf_daily_end_date: str = '2026-01-19',
    sf_monthly_start_date: str = '2025-01-01',
    sf_monthly_end_date: str = '2026-01-19',
    snowflake_app: str = None,
    output_excel: bool = False,
    output_path: str = None
) -> Dict[str, pd.DataFrame]:
    """
    Run the full comparison between Databricks and Snowflake SimilarWeb data
    
    Args:
        domains: List of domains to compare (defaults to AI_DOMAINS)
        db_start_date: Start date for Databricks query
        sf_daily_start_date: Start date for Snowflake daily query
        sf_daily_end_date: End date for Snowflake daily query
        sf_monthly_start_date: Start date for Snowflake monthly query
        sf_monthly_end_date: End date for Snowflake monthly query
        snowflake_app: Snowflake app to use for connection (defaults to SIMILARWEB_SNOWFLAKE_APP)
        output_excel: Whether to output results to Excel
        output_path: Path for Excel output
        
    Returns:
        Dictionary of comparison results
    """
    domains = domains or AI_DOMAINS
    snowflake_app = snowflake_app or SIMILARWEB_SNOWFLAKE_APP
    
    logging.info("="*80)
    logging.info("SIMILARWEB DATA COMPARISON: DATABRICKS vs SNOWFLAKE")
    logging.info("="*80)
    logging.info(f"Target domains: {domains}")
    logging.info(f"Databricks date filter: > {db_start_date}")
    logging.info(f"Snowflake daily date range: {sf_daily_start_date} to {sf_daily_end_date}")
    logging.info(f"Snowflake monthly date range: {sf_monthly_start_date} to {sf_monthly_end_date}")
    logging.info(f"Snowflake app: {snowflake_app}")
    
    all_results = {}
    
    # Query Databricks
    try:
        db_df = get_databricks_similarweb_data(domains, db_start_date)
        all_results['databricks_raw'] = db_df
    except Exception as e:
        logging.error(f"Error querying Databricks: {e}")
        db_df = pd.DataFrame()
    
    # Query Snowflake Daily
    try:
        sf_daily_df = get_snowflake_daily_data(domains, sf_daily_start_date, sf_daily_end_date, snowflake_app)
        all_results['snowflake_daily_raw'] = sf_daily_df
    except Exception as e:
        logging.error(f"Error querying Snowflake daily: {e}")
        sf_daily_df = pd.DataFrame()
    
    # Query Snowflake Monthly
    try:
        sf_monthly_df = get_snowflake_monthly_data(domains, sf_monthly_start_date, sf_monthly_end_date, snowflake_app)
        all_results['snowflake_monthly_raw'] = sf_monthly_df
    except Exception as e:
        logging.error(f"Error querying Snowflake monthly: {e}")
        sf_monthly_df = pd.DataFrame()
    
    # Run comparisons
    comparison_results = compare_traffic_metrics(db_df, sf_daily_df, sf_monthly_df, domains)
    all_results.update(comparison_results)
    
    # Align and compare values if both sources have data
    if not db_df.empty and not sf_daily_df.empty:
        aligned_df = align_and_compare_values(db_df, sf_daily_df)
        if not aligned_df.empty:
            all_results['aligned_comparison'] = aligned_df
    
    # Export to Excel
    if output_excel:
        output_path = output_path or f'similarweb_comparison_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        try:
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                for sheet_name, df in all_results.items():
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        # Truncate sheet name to Excel's 31-char limit
                        safe_name = sheet_name[:31]
                        df.to_excel(writer, sheet_name=safe_name, index=True)
            logging.info(f"\nResults exported to: {output_path}")
        except Exception as e:
            logging.error(f"Error exporting to Excel: {e}")
    
    return all_results


if __name__ == '__main__':
    # Setup logging
    setuplogger('similarweb_comparison', loglevel=logging.INFO)
    
    # Initialize config - use 'dev' or 'prod' environment
    # The factset app should be configured in apo_config for Snowflake access
    config.init('dev')
    
    # Available Snowflake apps (check your apo_config for available options):
    # - 'factset' - FactSet service account (default for this script)
    # - 'snowflake_metis' - Metis database
    # - 'snowflake_portfolio' - Portfolio database
    # - 'snowflake_reference_db' - Reference database
    
    # Run comparison with FactSet service account
    results = run_comparison(
        domains=AI_DOMAINS,
        snowflake_app='factset',  # Uses FactSet service account for Snowflake
        output_excel=True
    )
    
    # Print final summary
    logging.info("\n" + "="*80)
    logging.info("COMPARISON COMPLETE")
    logging.info("="*80)
    logging.info(f"Total result sets generated: {len(results)}")
    for name, df in results.items():
        if isinstance(df, pd.DataFrame):
            logging.info(f"  - {name}: {df.shape}")
    
    # Example: Access raw data for further analysis
    # databricks_data = results.get('databricks_raw', pd.DataFrame())
    # snowflake_daily = results.get('snowflake_daily_raw', pd.DataFrame())
    # snowflake_monthly = results.get('snowflake_monthly_raw', pd.DataFrame())
