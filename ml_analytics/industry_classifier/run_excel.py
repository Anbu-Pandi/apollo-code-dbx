import logging
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from common.utils.logutil import setuplogger

setuplogger(logconsole=True, loglevel=logging.INFO)

import pandas as pd
from ml_analytics.industry_classifier.classifier import IndustryClassifier
from ml_analytics.industry_classifier.data_fetcher import CACHE_DIR, fetch_wiki_search_descriptions, fetch_llm_descriptions

EXCEL_PATH = os.path.join(os.path.dirname(__file__), "company_list.xlsx")
BOX_DIR = r"C:\Users\agite\Box\Systematic Strategy\Shared Files\industry_classifier"

INSTRUMENT_PATTERNS = [
    r"\s*-\s*(?:Term (?:Loan|Debt)|Subordinated Notes?|Class [A-Z]\d? Notes?|"
    r"Senior (?:Secured|Unsecured|Loan)|First Lien|Second Lien|Mezzanine|"
    r"Revolver|Delayed Draw|Common Equity|(?:Series [A-Z] )?Preferred (?:Shares|Equity|Stock)|"
    r"Warrants?|PIK|Initial Term Loan|Series [A-Z] Preferred).*$",
    r"\s+(?:Senior Loan Fund|Class [A-Z]\d? Note|Initial Term Loan|Term Loan [A-Z]).*$",
]
INSTRUMENT_RE = re.compile("|".join(INSTRUMENT_PATTERNS), re.IGNORECASE)

PAREN_TRAIL_RE = re.compile(
    r"(?:\s*[\^+])?"
    r"(?:\s*\([a-z0-9/\s.,]+\))*\s*$",
    re.IGNORECASE,
)


def clean_company_name(raw: str) -> str:
    name = str(raw).strip()
    name = INSTRUMENT_RE.sub("", name)
    name = PAREN_TRAIL_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def load_cached_wiki() -> dict:
    cache_path = os.path.join(CACHE_DIR, "wiki_descriptions.parquet")
    if not os.path.exists(cache_path):
        return {}
    cached = pd.read_parquet(cache_path)
    return dict(zip(cached["name"], cached["description"]))


def main():
    logging.info(f"Reading {EXCEL_PATH}")
    original = pd.read_excel(EXCEL_PATH)
    df = original[["name"]].copy() if "name" in original.columns else original.iloc[:, :1].copy()
    df.columns = ["name"]
    logging.info(f"Loaded {len(df)} rows")

    df["company_clean"] = df["name"].apply(clean_company_name)
    unique_names = df["company_clean"].unique().tolist()
    logging.info(f"Unique company names after cleaning: {len(unique_names)}")

    clf = IndustryClassifier()
    if not os.path.exists(clf.model_path):
        logging.info("No model found, training first...")
        clf.fit(max_companies=3000)
    else:
        clf.load()

    wiki_cache = load_cached_wiki()
    logging.info(f"Wiki cache (exact): {len(wiki_cache)} descriptions available")

    results = {}
    remaining = []

    for name in unique_names:
        desc = wiki_cache.get(name)
        if desc and len(desc) > 50:
            results[name] = {"description": desc, "source": "wikipedia"}
        else:
            remaining.append(name)

    logging.info(f"Tier 1 - Wiki exact: {len(results)}, remaining: {len(remaining)}")

    if remaining:
        wiki_search_df = fetch_wiki_search_descriptions(remaining)
        ws_cache = dict(zip(wiki_search_df["name"], wiki_search_df["description"]))
        still_remaining = []
        for name in remaining:
            desc = ws_cache.get(name)
            if desc and len(desc) > 50:
                results[name] = {"description": desc, "source": "wiki_search"}
            else:
                still_remaining.append(name)
        remaining = still_remaining
        logging.info(f"Tier 2 - Wiki search: {len(ws_cache)}, remaining: {len(remaining)}")

    if remaining:
        llm_df = fetch_llm_descriptions(remaining)
        llm_cache = dict(zip(llm_df["name"], llm_df["description"]))
        for name in remaining:
            desc = llm_cache.get(name)
            if desc and len(desc) > 20:
                results[name] = {"description": desc, "source": "llm"}
        logging.info(f"Tier 3 - LLM descriptions: {len(results)}/{len(unique_names)} have descriptions")

    desc_items = [(n, r["description"]) for n, r in results.items() if "description" in r]
    if desc_items:
        names_list = [nd[0] for nd in desc_items]
        descs = [nd[1] for nd in desc_items]
        sectors = clf.predict(descs)
        proba = clf.predict_proba_approx(descs)
        for i, name in enumerate(names_list):
            results[name]["sector"] = sectors[i]
            results[name]["confidence"] = round(float(proba.max(axis=1).iloc[i]), 3)
        logging.info(f"Classified {len(desc_items)} companies from descriptions")

    df["company_description"] = df["company_clean"].map(lambda n: results.get(n, {}).get("description", ""))
    df["gics_sector"] = df["company_clean"].map(lambda n: results.get(n, {}).get("sector"))
    df["confidence"] = df["company_clean"].map(lambda n: results.get(n, {}).get("confidence"))
    df["source"] = df["company_clean"].map(lambda n: results.get(n, {}).get("source"))

    classified = df["gics_sector"].notna().sum()
    src_counts = df["source"].value_counts()
    breakdown = ", ".join(f"{k}: {v}" for k, v in src_counts.items())
    logging.info(f"Classified: {classified}/{len(df)} ({breakdown})")
    logging.info(f"Sector distribution:\n{df['gics_sector'].value_counts().to_string()}")

    df.to_excel(EXCEL_PATH, index=False)
    logging.info(f"Updated {EXCEL_PATH}")

    os.makedirs(BOX_DIR, exist_ok=True)
    dest = os.path.join(BOX_DIR, "company_list.xlsx")
    shutil.copy2(EXCEL_PATH, dest)
    logging.info(f"Copied to {dest}")

    logging.info("Done!")


if __name__ == "__main__":
    main()
