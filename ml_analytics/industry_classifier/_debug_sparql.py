import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from common.utils.logutil import setuplogger

setuplogger(logconsole=True, loglevel=logging.INFO)

from ml_analytics.industry_classifier.data_fetcher import fetch_companies_with_industries, fetch_wiki_description
from ml_analytics.industry_classifier.gics import map_industry_to_gics

logging.info("Step 1: Fetching companies from DBpedia (small batch)...")
df = fetch_companies_with_industries(max_companies=500)
logging.info(f"Got {len(df)} rows, {df['name'].nunique()} unique companies")
logging.info(f"Sample:\n{df.head(20).to_string()}")

df["gics_sector"] = df["industry_raw"].apply(map_industry_to_gics)
mapped = df.dropna(subset=["gics_sector"])
logging.info(f"\nMapped to GICS: {len(mapped)}/{len(df)} rows")
logging.info(f"Sector distribution:\n{mapped['gics_sector'].value_counts().to_string()}")

logging.info("\nStep 2: Testing Wikipedia fetch for a few companies...")
for name in mapped["name"].unique()[:5]:
    desc = fetch_wiki_description(name)
    status = f"{len(desc)} chars" if desc else "NOT FOUND"
    logging.info(f"  {name}: {status}")
