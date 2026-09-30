import logging
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pyarrow as pa
try:
    pa.unregister_extension_type("arrow.py_extension_type")
except (KeyError, pa.lib.ArrowKeyError):
    pass

from common.utils.envsetup import init_app

init_app('dev')

import pandas as pd

from ml_analytics.text_match import TextMatcher
from ml_analytics.industry_classifier.data_fetcher import fetch_bbg_reference

EXCEL_PATH = os.path.join(os.path.dirname(__file__), "company_list.xlsx")
BOX_DIR = r"C:\Users\agite\Box\Systematic Strategy\Shared Files\industry_classifier"

N_NEIGHBORS = 5
SCORE_GAP_THRESHOLD = 0.05

REMOVE_WORDS = [
    "inc", "llc", "ltd", "corp", "corporation", "company", "co",
    "group", "holdings", "holding", "limited", "sa", "spa", "plc",
    "ag", "gmbh", "nv", "se", "lp", "the",
]

BBG_MATCH_COLS = [
    "bbg_id", "bbg_long_comp_name", "bbg_match_score",
    "bbg_country", "classification_level_1", "classification_level_2",
]


def match_companies(names: list, bbg_ref: pd.DataFrame) -> pd.DataFrame:
    bbg_names = bbg_ref["long_comp_name"].tolist()

    logging.info(f"Building TextMatcher on {len(bbg_names)} BBG companies...")
    matcher = TextMatcher(bbg_names, n=3, remove_words=REMOVE_WORDS)
    matcher.tfidf_fit()

    logging.info(f"Matching {len(names)} companies (top {N_NEIGHBORS}) via sparse dot...")
    distances, indices, query_list = matcher.get_topk_matches(names, k=N_NEIGHBORS)

    bbg_vals = bbg_ref.reset_index(drop=True)[
        ["id_bb_company", "long_comp_name", "cntry_of_domicile",
         "classification_level_name1", "classification_level_name2"]
    ].values

    rows = []
    for i, name in enumerate(query_list):
        candidates = []
        for rank in range(distances.shape[1]):
            idx = indices[i][rank]
            r = bbg_vals[idx]
            candidates.append({
                "score": distances[i][rank],
                "id_bb_company": r[0], "long_comp_name": r[1],
                "cntry": r[2] or "", "lvl1": r[3] or "", "lvl2": r[4] or "",
            })

        best = _pick_best(candidates)
        rows.append({
            "company_clean": name,
            "bbg_id": best.get("id_bb_company", ""),
            "bbg_long_comp_name": best.get("long_comp_name", ""),
            "bbg_match_score": round(best.get("score", 1.0), 4),
            "bbg_country": best.get("cntry", ""),
            "classification_level_1": best.get("lvl1", ""),
            "classification_level_2": best.get("lvl2", ""),
        })

    return pd.DataFrame(rows)


def _pick_best(candidates: list) -> dict:
    if not candidates:
        return {}
    best = candidates[0]
    if len(candidates) < 2:
        return best

    gap = candidates[1]["score"] - best["score"]
    if gap < SCORE_GAP_THRESHOLD and best["cntry"] != "US":
        for alt in candidates[1:]:
            alt_gap = alt["score"] - best["score"]
            if alt_gap > SCORE_GAP_THRESHOLD:
                break
            if alt["cntry"] == "US":
                return alt
    return best


def main():
    logging.info(f"Reading {EXCEL_PATH}")
    df = pd.read_excel(EXCEL_PATH)
    logging.info(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

    if "company_clean" not in df.columns:
        logging.error("company_clean column missing -- run run_excel.py first")
        return

    all_names = df["company_clean"].dropna().tolist()
    logging.info(f"Matching all {len(all_names)} company names")

    bbg_ref = fetch_bbg_reference()
    logging.info(f"BBG reference: {len(bbg_ref)} companies")

    unique_names = list(dict.fromkeys(all_names))
    matches = match_companies(unique_names, bbg_ref)
    match_lookup = matches.set_index("company_clean")
    logging.info(f"Matched {len(matches)} unique companies")

    for col in BBG_MATCH_COLS:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    for col in BBG_MATCH_COLS:
        df[col] = df["company_clean"].map(match_lookup[col])

    matched = df["classification_level_1"].notna().sum()
    logging.info(f"BBG classified: {matched}/{len(df)}")
    logging.info(f"BBG Level 1 distribution:\n{df['classification_level_1'].value_counts().to_string()}")

    score_stats = df["bbg_match_score"].describe()
    logging.info(f"Match score stats:\n{score_stats.to_string()}")

    df.to_excel(EXCEL_PATH, index=False)
    logging.info(f"Updated {EXCEL_PATH}")

    os.makedirs(BOX_DIR, exist_ok=True)
    dest = os.path.join(BOX_DIR, "company_list.xlsx")
    shutil.copy2(EXCEL_PATH, dest)
    logging.info(f"Copied to {dest}")

    logging.info("Done!")


if __name__ == "__main__":
    main()
