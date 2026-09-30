"""Website observations contain published evidence, without vendor or fund inference."""

import re

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.website_company_classification import accepted_company_candidates
from data_access.dbtables.sponsor_study.website_crawl_persistence import website_crawl_contract_dataset
from data_access.dbtables.sponsor_study.website_sources import clean_holding_candidates, clean_site_snapshots


def build_website_observations(candidates: pd.DataFrame) -> pd.DataFrame:
    contract = website_crawl_contract_dataset()["source_sponsor_website_holding"]
    accepted = accepted_company_candidates(candidates)
    if accepted.empty:
        return contract
    required = {"website_holding_candidate_id", "crawl_run_id", "company_name", "sponsor", "knowledge_datetime"}
    missing = required - set(accepted)
    if missing or accepted[list(required)].isna().any().any():
        raise ValueError(f"Website observations require complete identity/capture evidence: {sorted(missing)}")
    if accepted.website_holding_candidate_id.duplicated().any():
        raise ValueError("Website observations require one row per source candidate")
    out = accepted.reindex(columns=contract.columns).copy()
    out["website_holding_id"] = accepted.website_holding_candidate_id.map(lambda value: stable_id("web_hold", value))
    out["sponsor_name"] = accepted.sponsor
    out["sponsor_id"] = accepted.sponsor.map(lambda value: stable_id("sponsor_web", value))
    out["company_slug"] = accepted.company_name.map(slug)
    out["source_system"] = "sponsor_website"
    out["source_confidence"] = accepted.get("confidence", pd.Series("medium", index=accepted.index)).fillna("medium")
    out["fund_name_raw"] = accepted.get("fund", pd.Series(pd.NA, index=accepted.index))
    out["fund_match_method"] = "not_enriched"
    out["fund_match_confidence"] = "unmatched"
    out["holding_review_status"] = "awaiting_enrichment"
    # No inferred fund, vendor company identity, or financial fields enter this
    # producer. External refresh enriches the retained candidates separately.
    for column in ("knowledge_datetime", "investment_date", "exit_date"):
        if column != "knowledge_datetime":
            precise = out[column].map(lambda value: not isinstance(value, str) or bool(
                re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", value)
            ))
            out[column] = out[column].where(precise)
        out[column] = pd.to_datetime(out[column], errors="raise", utc=True)
    out.attrs["delta_schema_types"] = contract.attrs["delta_schema_types"]
    return out.reset_index(drop=True)


def website_observation_dataset(
    seeds: pd.DataFrame, snapshots: pd.DataFrame, candidates: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Keep contract-shaped enrichment tables empty until the downstream refresh."""
    dataset = website_crawl_contract_dataset()
    dataset["source_sponsor_website_list"] = seeds.copy()
    dataset["source_sponsor_site_snapshot"] = clean_site_snapshots(snapshots)
    dataset["source_sponsor_site_holding_candidate"] = clean_holding_candidates(candidates)
    dataset["source_sponsor_website_holding"] = build_website_observations(candidates)
    return dataset
