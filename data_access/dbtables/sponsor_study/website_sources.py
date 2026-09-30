import json
import re
from difflib import SequenceMatcher
from typing import Any

import pandas as pd

from data_access.dbtables.sponsor_study.benchmark_v31_sponsor_xref import SPONSOR_ALIAS_KEYS
from data_access.dbtables.sponsor_study.canonical import normalize_exit_type, slug, stable_id
from data_access.dbtables.sponsor_study.website_evidence import HOLDING_CANDIDATE_COLUMNS
from data_access.dbtables.sponsor_study.website_company_classification import (
    accepted_company_candidates, classify_holding_candidates,
)


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, default=str)
    except TypeError:
        return "[]"


def clean_site_snapshots(site_snapshots: pd.DataFrame | None) -> pd.DataFrame:
    if site_snapshots is None or site_snapshots.empty:
        empty = pd.DataFrame(columns=[
            "crawl_run_id",
            "crawl_run_started_at",
            "site_snapshot_id",
            "sponsor",
            "snapshot_kind",
            "extraction_profile",
            "extraction_adapter",
            "source_url",
            "final_url",
            "page_title",
            "body_text_hash",
            "body_text",
            "body_text_preview",
            "links_json",
            "portfolio_cards_json",
            "portfolio_pattern",
            "expansion_diagnostics_json",
            "link_count",
            "page_type",
            "extraction_allowed",
            "captured_at",
            "crawl_status",
            "error_message",
            "block_reason",
            "fallback_attempted",
            "page_limit_reached",
        ])
        empty.attrs["delta_schema_types"] = {
            "crawl_run_started_at": "TIMESTAMP",
            "captured_at": "TIMESTAMP",
            "extraction_allowed": "BOOLEAN",
            "fallback_attempted": "BOOLEAN",
            "page_limit_reached": "BOOLEAN",
            "link_count": "BIGINT",
        }
        return empty
    out = site_snapshots.copy()
    if "crawl_run_id" not in out:
        out["crawl_run_id"] = pd.NA
    out["body_text_preview"] = out.get("body_text", pd.Series(dtype="string")).astype("string").str.slice(0, 5000)
    for raw_column in ("links", "portfolio_cards", "expansion_diagnostics"):
        json_column = f"{raw_column}_json"
        if raw_column in out:
            out[json_column] = out[raw_column].map(_safe_json)
        elif json_column not in out:
            out[json_column] = "[]"
    out = out.reindex(columns=clean_site_snapshots(None).columns).drop_duplicates(
        subset=["crawl_run_id", "site_snapshot_id"], keep="last"
    )
    out.attrs["delta_schema_types"] = {
        "crawl_run_started_at": "TIMESTAMP",
        "captured_at": "TIMESTAMP",
        "extraction_allowed": "BOOLEAN",
        "fallback_attempted": "BOOLEAN",
        "page_limit_reached": "BOOLEAN",
        "link_count": "BIGINT",
    }
    return out


HOLDING_CANDIDATE_DELTA_SCHEMA_TYPES = {
    "employee_count": "BIGINT",
    "investment_year": "BIGINT",
    "investment_date": "TIMESTAMP",
    "exit_date": "TIMESTAMP",
    "extracted_at": "TIMESTAMP",
    "knowledge_datetime": "TIMESTAMP",
}


def clean_holding_candidates(holding_candidates: pd.DataFrame | None) -> pd.DataFrame:
    if holding_candidates is None or holding_candidates.empty:
        empty = pd.DataFrame(columns=HOLDING_CANDIDATE_COLUMNS)
        empty.attrs["delta_schema_types"] = HOLDING_CANDIDATE_DELTA_SCHEMA_TYPES
        return empty
    out = holding_candidates.copy()
    for column in ["investment_date", "exit_date", "extracted_at", "knowledge_datetime"]:
        if column in out:
            out[column] = pd.to_datetime(out[column], errors="coerce")
    for column in ["employee_count", "investment_year"]:
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce").astype("Int64")
    out.attrs["delta_schema_types"] = HOLDING_CANDIDATE_DELTA_SCHEMA_TYPES
    return out


WEBSITE_XREF_COLUMNS = [
    "crawl_run_id",
    "website_holding_candidate_id",
    "portco_id",
    "ownership_id",
    "sponsor_id",
    "match_method",
    "match_confidence",
    "seed_company_name",
    "website_company_name",
    "sponsor",
    "fund",
]


def match_website_holdings_to_seed(
    holding_candidates: pd.DataFrame | None,
    company_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    if holding_candidates is None or holding_candidates.empty or company_snapshot.empty:
        return pd.DataFrame(columns=WEBSITE_XREF_COLUMNS)

    seed = company_snapshot[[
        "portco_id",
        "ownership_id",
        "sponsor_id",
        "sponsor",
        "company",
        "fund_used",
        "fund",
    ]].dropna(subset=["sponsor", "company"]).copy()
    seed["sponsor_key"] = seed["sponsor"].map(_sponsor_keys)
    seed = seed.explode("sponsor_key")
    seed["company_key"] = seed["company"].map(_company_keys)
    seed = seed.explode("company_key")
    seed["seed_fund"] = seed["fund_used"].where(seed["fund_used"].notna(), seed["fund"])

    candidates = accepted_company_candidates(holding_candidates)
    if candidates.empty:
        return pd.DataFrame(columns=WEBSITE_XREF_COLUMNS)
    if "crawl_run_id" not in candidates:
        candidates["crawl_run_id"] = pd.NA
    candidates["sponsor_key"] = candidates["sponsor"].map(_sponsor_keys)
    candidates = candidates.explode("sponsor_key")
    candidates["company_key"] = candidates["company_name"].map(_company_keys)
    candidates = candidates.explode("company_key")

    matched = candidates.merge(seed, on=["sponsor_key", "company_key"], how="inner", suffixes=("_website", "_seed"))
    if matched.empty:
        return pd.DataFrame(columns=WEBSITE_XREF_COLUMNS)
    matched["match_method"] = "exact_sponsor_company_slug"
    matched["match_confidence"] = "high"
    matched["seed_company_name"] = matched["company"]
    matched["website_company_name"] = matched["company_name"]
    matched["sponsor"] = matched["sponsor_website"].combine_first(matched["sponsor_seed"])
    matched["fund"] = matched["seed_fund"]
    return matched[WEBSITE_XREF_COLUMNS].drop_duplicates().reset_index(drop=True)


WEBSITE_BRIDGE_COLUMNS = [
    "crawl_run_id",
    "website_bridge_id",
    "website_holding_candidate_id",
    "knowledge_datetime",
    "sponsor",
    "sponsor_id",
    "fund_id",
    "fund_name",
    "portco_id",
    "ownership_id",
    "company_name",
    "company_slug",
    "investment_date",
    "investment_year",
    "current_status",
    "active_flag",
    "exit_date",
    "exit_type",
    "sector",
    "geography",
    "source_url",
    "final_url",
    "company_url",
    "external_company_url",
    "company_description",
    "evidence_text",
    "extraction_method",
    "confidence",
    "match_method",
    "match_confidence",
    "matched_seed_ownership_id",
    "matched_seed_fund",
]

WEBSITE_HOLDING_COLUMNS = [
    "website_holding_id",
    "crawl_run_id",
    "knowledge_datetime",
    "source_system",
    "source_confidence",
    "sponsor_id",
    "sponsor_name",
    "fund_id",
    "fund_name",
    "fund_name_raw",
    "vintage",
    "website_holding_candidate_id",
    "site_snapshot_id",
    "structured_portco_id",
    "benchmark_portco_id",
    "company_name",
    "company_slug",
    "company_country",
    "industry",
    "investment_date",
    "exit_date",
    "entry_type",
    "transaction_close_date",
    "round_of_funding",
    "total_investment_amount",
    "investment_description",
    "amount_invested_by_sponsor",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "portfolio_status",
    "sector",
    "geography",
    "employee_count",
    "company_url",
    "external_company_url",
    "company_description",
    "source_url",
    "final_url",
    "evidence_text",
    "extraction_method",
    "fund_match_method",
    "fund_match_confidence",
    "holding_review_status",
]
WEBSITE_HOLDING_DELTA_SCHEMA_TYPES = {
    "knowledge_datetime": "TIMESTAMP",
    "investment_date": "TIMESTAMP",
    "exit_date": "TIMESTAMP",
    "transaction_close_date": "TIMESTAMP",
    "total_investment_amount": "DOUBLE",
    "amount_invested_by_sponsor": "DOUBLE",
    "employee_count": "BIGINT",
}

SPONSOR_SUFFIX_TOKENS = {
    "advisers",
    "advisors",
    "asset",
    "capital",
    "co",
    "equity",
    "group",
    "inc",
    "international",
    "llc",
    "limited",
    "lp",
    "ltd",
    "management",
    "partners",
    "private",
}
COMPANY_SUFFIX_TOKENS = {
    "ag",
    "as",
    "brand",
    "brands",
    "co",
    "company",
    "corp",
    "corporation",
    "group",
    "holdings",
    "inc",
    "llc",
    "ltd",
    "limited",
    "plc",
    "sa",
    "sas",
    "services",
    "systems",
    "technologies",
    "technology",
}


def _sponsor_keys(value) -> list[str]:
    full = slug(value)
    if not full:
        return []
    parts = [part for part in full.split("_") if part and part not in SPONSOR_SUFFIX_TOKENS]
    keys = [full]
    if parts:
        compact = "_".join(parts)
        keys.append(compact)
        keys.append(parts[0])
    extra: list[str] = []
    for key in keys:
        extra.extend(SPONSOR_ALIAS_KEYS.get(key, []))
    return sorted(set(keys + extra))


def _company_keys(value) -> list[str]:
    full = slug(value)
    if not full:
        return []
    parts = [part for part in full.split("_") if part and part not in COMPANY_SUFFIX_TOKENS]
    keys = [full]
    if parts:
        keys.append("_".join(parts))
        if len(parts[0]) > 2:
            keys.append(parts[0])
    return sorted(set(keys))


def _clean_text(value: Any) -> str:
    return "" if pd.isna(value) else " ".join(str(value).strip().split())


def _fund_key(value) -> str:
    text = slug(value)
    stop = {"fund", "partners", "capital", "private", "equity", "the"}
    tokens = [token for token in text.split("_") if token and token not in stop]
    return "_".join(tokens)


def _fund_match_score(seed_name: Any, source_name: Any) -> float:
    seed_key = _fund_key(seed_name)
    source_key = _fund_key(source_name)
    if not seed_key or not source_key:
        return 0.0
    if seed_key == source_key:
        return 1.0
    seed_tokens = set(seed_key.split("_"))
    source_tokens = set(source_key.split("_"))
    overlap = len(seed_tokens & source_tokens) / max(len(seed_tokens | source_tokens), 1)
    ratio = SequenceMatcher(None, seed_key, source_key).ratio()
    return (0.65 * ratio) + (0.35 * overlap)


def _company_match_score(seed_name: Any, source_name: Any) -> float:
    seed_keys = set(_company_keys(seed_name))
    source_keys = set(_company_keys(source_name))
    if seed_keys & source_keys:
        return 1.0
    seed_key = _fund_key(seed_name)
    source_key = _fund_key(source_name)
    if not seed_key or not source_key:
        return 0.0
    seed_tokens = set(seed_key.split("_"))
    source_tokens = set(source_key.split("_"))
    overlap = len(seed_tokens & source_tokens) / max(len(seed_tokens | source_tokens), 1)
    ratio = SequenceMatcher(None, seed_key, source_key).ratio()
    return (0.75 * ratio) + (0.25 * overlap)


def _dim_id_frame(
    frame: pd.DataFrame | None,
    *,
    id_column: str,
    required: tuple[str, ...],
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(required))
    out = frame.copy()
    if id_column not in out.columns and f"external_{id_column}" in out.columns:
        out[id_column] = out[f"external_{id_column}"]
    if "sponsor_id" in required and "sponsor_id" not in out.columns and "external_sponsor_id" in out.columns:
        out["sponsor_id"] = out["external_sponsor_id"]
    missing = [column for column in required if column not in out.columns]
    if missing:
        return pd.DataFrame(columns=list(required))
    return out


def _sponsor_lookup(dim_sponsor: pd.DataFrame) -> pd.DataFrame:
    columns = ["sponsor_id", "sponsor_name", "sponsor_key"]
    out = _dim_id_frame(
        dim_sponsor,
        id_column="sponsor_id",
        required=("sponsor_id", "sponsor_name"),
    )
    if out.empty:
        return pd.DataFrame(columns=columns)
    out = out[["sponsor_id", "sponsor_name"]].dropna(subset=["sponsor_name"]).drop_duplicates().copy()
    out["sponsor_key"] = out["sponsor_name"].map(_sponsor_keys)
    return out.explode("sponsor_key")[columns].drop_duplicates()


def _fund_lookup(dim_fund: pd.DataFrame, dim_sponsor: pd.DataFrame) -> pd.DataFrame:
    columns = ["fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage"]
    funds = _dim_id_frame(
        dim_fund,
        id_column="fund_id",
        required=("fund_id", "sponsor_id", "fund_name", "vintage"),
    )
    if funds.empty:
        return pd.DataFrame(columns=columns)
    funds = funds[["fund_id", "sponsor_id", "fund_name", "vintage"]].dropna(subset=["fund_name"]).drop_duplicates().copy()
    sponsors = _dim_id_frame(
        dim_sponsor,
        id_column="sponsor_id",
        required=("sponsor_id", "sponsor_name"),
    )
    if not sponsors.empty:
        funds = funds.merge(
            sponsors[["sponsor_id", "sponsor_name"]].drop_duplicates(),
            on="sponsor_id",
            how="left",
        )
    else:
        funds["sponsor_name"] = pd.NA
    return funds[columns]


def _match_sponsor_id(sponsor: Any, sponsor_lookup: pd.DataFrame) -> tuple[Any, str]:
    keys = set(_sponsor_keys(sponsor))
    if not keys or sponsor_lookup.empty:
        return stable_id("sponsor", sponsor), "generated_from_website_sponsor"
    matched = sponsor_lookup[sponsor_lookup["sponsor_key"].isin(keys)]
    if matched.empty:
        return stable_id("sponsor", sponsor), "generated_from_website_sponsor"
    return matched.sort_values("sponsor_name").iloc[0]["sponsor_id"], "sponsor_slug_xref"


def _match_fund(row: pd.Series, funds: pd.DataFrame) -> dict:
    fund_raw = _clean_text(row.get("fund"))
    sponsor_id = row.get("sponsor_id")
    if not fund_raw:
        return {
            "fund_id": pd.NA,
            "fund_name": pd.NA,
            "vintage": pd.NA,
            "fund_match_method": "missing_website_fund",
            "fund_match_confidence": "unmatched",
            "holding_review_status": "needs_fund_review",
        }
    scoped = funds[funds["sponsor_id"].eq(sponsor_id)] if not funds.empty else pd.DataFrame()
    if scoped.empty:
        return {
            "fund_id": stable_id("fund", row.get("sponsor"), fund_raw, None),
            "fund_name": fund_raw,
            "vintage": pd.NA,
            "fund_match_method": "generated_from_website_fund",
            "fund_match_confidence": "unmatched",
            "holding_review_status": "needs_fund_review",
        }
    candidates = scoped.copy()
    candidates["_score"] = candidates["fund_name"].map(lambda value: _fund_match_score(value, fund_raw))
    candidates = candidates.sort_values(["_score", "fund_name"], ascending=[False, True])
    top = candidates.iloc[0]
    second_score = float(candidates.iloc[1]["_score"]) if len(candidates) > 1 else 0.0
    score = float(top["_score"])
    auto_match = score >= 0.92 and (score - second_score) >= 0.03
    return {
        "fund_id": top["fund_id"] if auto_match else stable_id("fund", row.get("sponsor"), fund_raw, None),
        "fund_name": top["fund_name"] if auto_match else fund_raw,
        "vintage": top.get("vintage") if auto_match else pd.NA,
        "fund_match_method": "website_fund_name_sponsor_scoped" if auto_match else "generated_from_website_fund",
        "fund_match_confidence": "high" if auto_match else "unmatched",
        "holding_review_status": "auto_mapped" if auto_match else "needs_fund_review",
    }


FACTSET_INHERITED_COLUMNS = [
    "fund_id",
    "fund_name",
    "vintage",
    "structured_portco_id",
    "benchmark_portco_id",
    "company_country",
    "industry",
    "investment_date",
    "exit_date",
    "entry_type",
    "transaction_close_date",
    "round_of_funding",
    "total_investment_amount",
    "investment_description",
    "amount_invested_by_sponsor",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "fund_match_method",
    "fund_match_confidence",
    "holding_review_status",
]


def _empty_factset_inference() -> dict:
    return {
        "structured_portco_id": pd.NA,
        "benchmark_portco_id": pd.NA,
        "company_country": pd.NA,
        "industry": pd.NA,
        "investment_date": pd.NaT,
        "exit_date": pd.NaT,
        "entry_type": pd.NA,
        "transaction_close_date": pd.NaT,
        "round_of_funding": pd.NA,
        "total_investment_amount": pd.NA,
        "investment_description": pd.NA,
        "amount_invested_by_sponsor": pd.NA,
        "transaction_fund_entity_id": pd.NA,
        "transaction_fund_name": pd.NA,
    }


def _structured_factset_lookup(structured_investments: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "sponsor_id",
        "fund_id",
        "fund_name",
        "vintage",
        "structured_portco_id",
        "benchmark_portco_id",
        "company_name",
        "company_country",
        "industry",
        "investment_date",
        "exit_date",
        "entry_type",
        "transaction_close_date",
        "round_of_funding",
        "total_investment_amount",
        "investment_description",
        "amount_invested_by_sponsor",
        "transaction_fund_entity_id",
        "transaction_fund_name",
    ]
    if structured_investments is None or structured_investments.empty:
        return pd.DataFrame(columns=columns + ["company_key"])
    cols = [col for col in columns if col in structured_investments.columns]
    out = structured_investments[cols].dropna(subset=["sponsor_id", "company_name"]).drop_duplicates().copy()
    out["company_key"] = out["company_name"].map(_company_keys)
    return out.explode("company_key").dropna(subset=["company_key"]).drop_duplicates()


def _factset_company_fund_matches(row: pd.Series, factset: pd.DataFrame) -> list[dict]:
    if factset.empty:
        return []
    scoped = factset[factset["sponsor_id"].eq(row.get("sponsor_id"))].copy()
    if scoped.empty:
        return []
    website_company = row.get("company_name")
    scoped["_score"] = scoped["company_name"].map(lambda value: _company_match_score(value, website_company))
    scoped = scoped.sort_values(["_score", "company_name"], ascending=[False, True])
    if scoped.empty:
        return []
    top_score = float(scoped.iloc[0]["_score"])
    second_score = float(scoped.iloc[1]["_score"]) if len(scoped) > 1 else 0.0
    if top_score < 0.8 or (top_score < 1.0 and top_score - second_score < 0.03):
        return []
    top_company = scoped.iloc[0]["company_name"]
    matched_company = scoped[scoped["company_name"].eq(top_company)].copy()
    method = "factset_pevc_company_exact_sponsor_scoped" if top_score >= 1.0 else "factset_pevc_company_fuzzy_sponsor_scoped"
    confidence = "high" if top_score >= 1.0 else "medium"
    rows = []
    for _, factset_row in matched_company.drop_duplicates(["fund_id", "structured_portco_id"]).iterrows():
        inherited = _empty_factset_inference()
        for col in inherited:
            if col in factset_row:
                inherited[col] = factset_row.get(col)
        rows.append({
            **inherited,
            "fund_id": factset_row.get("fund_id"),
            "fund_name": factset_row.get("fund_name"),
            "vintage": factset_row.get("vintage"),
            "fund_match_method": method,
            "fund_match_confidence": confidence,
            "holding_review_status": "auto_mapped",
        })
    return rows


def _vintage_window_fund_matches(row: pd.Series, funds: pd.DataFrame) -> list[dict]:
    investment_year = pd.to_numeric(row.get("investment_year"), errors="coerce")
    if pd.isna(investment_year) or funds.empty:
        return []
    scoped = funds[funds["sponsor_id"].eq(row.get("sponsor_id"))].copy()
    if scoped.empty:
        return []
    scoped["_vintage"] = pd.to_numeric(scoped["vintage"], errors="coerce")
    scoped = scoped[scoped["_vintage"].notna()].copy()
    year = int(investment_year)
    candidates = scoped[(scoped["_vintage"] <= year) & (year <= scoped["_vintage"] + 6)].copy()
    if candidates.empty:
        return []
    candidates["_distance"] = (year - candidates["_vintage"]).abs()
    candidates = candidates.sort_values(["_distance", "_vintage", "fund_name"])
    confidence = "medium" if len(candidates) == 1 else "low"
    return [
        {
            **_empty_factset_inference(),
            "fund_id": row.get("fund_id"),
            "fund_name": row.get("fund_name"),
            "vintage": row.get("vintage"),
            "fund_match_method": "inferred_from_investment_year_vintage_window",
            "fund_match_confidence": confidence,
            "holding_review_status": "needs_fund_review",
        }
        for _, row in candidates.iterrows()
    ]


def _missing_fund_match() -> dict:
    return {
        **_empty_factset_inference(),
        "fund_id": pd.NA,
        "fund_name": pd.NA,
        "vintage": pd.NA,
        "fund_match_method": "missing_website_fund",
        "fund_match_confidence": "unmatched",
        "holding_review_status": "needs_fund_review",
    }


def _fund_matches_for_candidate(row: pd.Series, funds: pd.DataFrame, factset: pd.DataFrame) -> list[dict]:
    explicit = _match_fund(row, funds)
    if explicit["fund_match_method"] != "missing_website_fund":
        return [{**_empty_factset_inference(), **explicit}]
    factset_matches = _factset_company_fund_matches(row, factset)
    if factset_matches:
        return factset_matches
    vintage_matches = _vintage_window_fund_matches(row, funds)
    if vintage_matches:
        return vintage_matches
    return [_missing_fund_match()]


def _normalize_status(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"active", "current", "portfolio"} or "unrealised" in text or "unrealized" in text:
        return "Active"
    if any(marker in text for marker in ("exited", "exit", "realised", "realized", "former", "sold")):
        return "Exited"
    return "Unknown"


def _normalize_confidence(value) -> str:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric):
        if float(numeric) >= 0.85:
            return "high"
        if float(numeric) >= 0.60:
            return "medium"
        return "low"
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "low"} else "medium"


def _coerce_investment_date(value):
    # Year-only evidence is retained on the source candidate; it is not an
    # assertion that the transaction closed on January 1.
    if isinstance(value, str) and not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", value):
        return pd.NaT
    return pd.to_datetime(value, errors="coerce")


def _seed_lookup(company_snapshot: pd.DataFrame) -> pd.DataFrame:
    if company_snapshot is None or company_snapshot.empty:
        return pd.DataFrame(columns=[
            "seed_sponsor_key",
            "seed_company_key",
            "sponsor_id",
            "fund_id",
            "portco_id",
            "ownership_id",
            "seed_fund",
        ])
    seed = company_snapshot[[
        "sponsor",
        "company",
        "sponsor_id",
        "fund_id",
        "portco_id",
        "ownership_id",
        "fund_used",
        "fund",
    ]].dropna(subset=["sponsor", "company"]).copy()
    seed["seed_sponsor_key"] = seed["sponsor"].map(_sponsor_keys)
    seed = seed.explode("seed_sponsor_key")
    seed["seed_company_key"] = seed["company"].map(_company_keys)
    seed = seed.explode("seed_company_key")
    seed["seed_fund"] = seed["fund_used"].where(seed["fund_used"].notna(), seed["fund"])
    return seed[[
        "seed_sponsor_key",
        "seed_company_key",
        "sponsor_id",
        "fund_id",
        "portco_id",
        "ownership_id",
        "seed_fund",
    ]].drop_duplicates()


def build_website_portfolio_bridge(
    holding_candidates: pd.DataFrame | None,
    company_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    if holding_candidates is None or holding_candidates.empty:
        return pd.DataFrame(columns=WEBSITE_BRIDGE_COLUMNS)

    candidates = accepted_company_candidates(holding_candidates)
    if candidates.empty:
        return pd.DataFrame(columns=WEBSITE_BRIDGE_COLUMNS)
    candidates["candidate_sponsor_key"] = candidates["sponsor"].map(_sponsor_keys)
    candidates = candidates.explode("candidate_sponsor_key")
    candidates["candidate_company_key"] = candidates["company_name"].map(_company_keys)
    candidates = candidates.explode("candidate_company_key")
    seed = _seed_lookup(company_snapshot)
    if not seed.empty:
        matched = candidates.merge(
            seed,
            left_on=["candidate_sponsor_key", "candidate_company_key"],
            right_on=["seed_sponsor_key", "seed_company_key"],
            how="left",
        )
    else:
        matched = candidates.copy()
        for col in ["sponsor_id", "fund_id", "portco_id", "ownership_id", "seed_fund"]:
            matched[col] = pd.NA

    has_llm_candidates = candidates["extraction_method"].astype("string").eq("llm_snapshot").any()
    records = []
    for _, row in matched.iterrows():
        seed_matched = pd.notna(row.get("seed_fund"))
        if (
            has_llm_candidates
            and not seed_matched
            and str(row.get("extraction_method") or "") == "link_text_heuristic"
        ):
            continue
        status = _normalize_status(row.get("portfolio_status"))
        investment_date = _coerce_investment_date(row.get("investment_date"))
        exit_date = pd.to_datetime(row.get("exit_date"), errors="coerce")
        fund_name = row.get("fund") or row.get("seed_fund")
        sponsor_id = row.get("sponsor_id")
        fund_id = row.get("fund_id")
        portco_id = row.get("portco_id")
        ownership_id = row.get("ownership_id")
        if pd.isna(sponsor_id):
            sponsor_id = stable_id("sponsor", row.get("sponsor"))
        if pd.isna(fund_id) and pd.notna(fund_name):
            fund_id = stable_id("fund", row.get("sponsor"), fund_name, None)
        if pd.isna(portco_id):
            portco_id = stable_id("portco", row.get("company_name"), None, row.get("sponsor"))
        if pd.isna(ownership_id):
            ownership_id = stable_id("own", row.get("sponsor"), fund_name, row.get("company_name"), investment_date)
        active_flag = True if status == "Active" else False if status == "Exited" else pd.NA
        records.append({
            "crawl_run_id": row.get("crawl_run_id"),
            "website_bridge_id": stable_id(
                "web_bridge",
                row.get("website_holding_candidate_id"),
                ownership_id,
                fund_name,
                row.get("knowledge_datetime"),
            ),
            "website_holding_candidate_id": row.get("website_holding_candidate_id"),
            "knowledge_datetime": row.get("knowledge_datetime") or row.get("extracted_at"),
            "sponsor": row.get("sponsor"),
            "sponsor_id": sponsor_id,
            "fund_id": fund_id,
            "fund_name": fund_name,
            "portco_id": portco_id,
            "ownership_id": ownership_id,
            "company_name": row.get("company_name"),
            "company_slug": row.get("company_slug"),
            "investment_date": investment_date,
            "investment_year": row.get("investment_year"),
            "current_status": status,
            "active_flag": active_flag,
            "exit_date": exit_date,
            "exit_type": normalize_exit_type(row.get("exit_type")) if pd.notna(row.get("exit_type")) else pd.NA,
            "sector": row.get("sector"),
            "geography": row.get("geography"),
            "source_url": row.get("source_url"),
            "final_url": row.get("final_url"),
            "company_url": row.get("company_url"),
            "external_company_url": row.get("external_company_url"),
            "company_description": row.get("company_description"),
            "evidence_text": row.get("evidence_text"),
            "extraction_method": row.get("extraction_method"),
            "confidence": row.get("confidence"),
            "match_method": "normalized_sponsor_company_slug" if seed_matched else "website_generated_pending_review",
            "match_confidence": "high" if seed_matched else "unmatched",
            "matched_seed_ownership_id": row.get("ownership_id") if seed_matched else pd.NA,
            "matched_seed_fund": row.get("seed_fund"),
        })
    if not records:
        return pd.DataFrame(columns=WEBSITE_BRIDGE_COLUMNS)
    out = pd.DataFrame(records)[WEBSITE_BRIDGE_COLUMNS].drop_duplicates()
    method_rank = {"llm_snapshot": 0, "page_title_heuristic": 1, "link_text_heuristic": 2}
    out["_method_rank"] = out["extraction_method"].map(method_rank).fillna(9)
    out = out.sort_values(["match_confidence", "_method_rank"], ascending=[True, True]).drop_duplicates(
        subset=["sponsor", "company_slug", "ownership_id", "fund_name", "current_status"],
        keep="first",
    )
    return out.drop(columns=["_method_rank"]).reset_index(drop=True)


def build_sponsor_website_holdings(
    holding_candidates: pd.DataFrame | None,
    dim_sponsor: pd.DataFrame,
    dim_fund: pd.DataFrame,
    structured_investments: pd.DataFrame | None = None,
) -> pd.DataFrame:
    candidates = accepted_company_candidates(holding_candidates) if holding_candidates is not None else pd.DataFrame()
    if candidates.empty:
        empty = pd.DataFrame(columns=WEBSITE_HOLDING_COLUMNS)
        empty.attrs["delta_schema_types"] = WEBSITE_HOLDING_DELTA_SCHEMA_TYPES
        return empty

    sponsor_map = _sponsor_lookup(dim_sponsor)
    funds = _fund_lookup(dim_fund, dim_sponsor)
    factset = _structured_factset_lookup(structured_investments)
    candidates[["sponsor_id", "sponsor_match_method"]] = candidates["sponsor"].apply(
        lambda sponsor: pd.Series(_match_sponsor_id(sponsor, sponsor_map))
    )

    expanded_rows = []
    for _, candidate in candidates.iterrows():
        for fund_match in _fund_matches_for_candidate(candidate, funds, factset):
            expanded_rows.append({**candidate.to_dict(), **fund_match})
    candidates = pd.DataFrame(expanded_rows)

    investment_date = candidates.apply(
        lambda row: row.get("investment_date")
        if pd.notna(row.get("investment_date")) and not isinstance(row.get("investment_date"), str)
        else _coerce_investment_date(row.get("investment_date")),
        axis=1,
    )
    exit_date = pd.to_datetime(candidates.get("exit_date"), errors="coerce")
    company_slug = candidates.get("company_slug", candidates["company_name"].map(slug))
    source_confidence = candidates.get("confidence", pd.Series("", index=candidates.index)).map(_normalize_confidence)

    out = pd.DataFrame({
        "website_holding_id": candidates.apply(
            lambda row: stable_id(
                "web_hold",
                row.get("crawl_run_id"),
                row.get("sponsor"),
                _clean_text(row.get("fund_id")) or _clean_text(row.get("fund")),
                slug(row.get("company_name")),
                row.get("source_url"),
            ),
            axis=1,
        ),
        "crawl_run_id": candidates.get("crawl_run_id"),
        "knowledge_datetime": candidates.get("knowledge_datetime").where(
            candidates.get("knowledge_datetime").notna(),
            candidates.get("extracted_at"),
        ),
        "source_system": "sponsor_website",
        "source_confidence": source_confidence,
        "sponsor_id": candidates["sponsor_id"],
        "sponsor_name": candidates.get("sponsor"),
        "fund_id": candidates["fund_id"],
        "fund_name": candidates["fund_name"],
        "fund_name_raw": candidates.get("fund"),
        "vintage": candidates["vintage"],
        "website_holding_candidate_id": candidates.get("website_holding_candidate_id"),
        "site_snapshot_id": candidates.get("site_snapshot_id"),
        "structured_portco_id": candidates.apply(
            lambda row: row.get("structured_portco_id")
            if pd.notna(row.get("structured_portco_id"))
            else stable_id("portco_struct", "sponsor_website", row.get("sponsor"), row.get("company_name")),
            axis=1,
        ),
        "benchmark_portco_id": candidates.get("benchmark_portco_id"),
        "company_name": candidates.get("company_name"),
        "company_slug": company_slug,
        "company_country": candidates.get("company_country").where(candidates.get("company_country").notna(), candidates.get("geography")),
        "industry": candidates.get("industry").where(candidates.get("industry").notna(), candidates.get("sector")),
        "employee_count": candidates.get("employee_count"),
        "investment_date": investment_date,
        "exit_date": exit_date,
        "entry_type": candidates.get("entry_type").where(candidates.get("entry_type").notna(), "Website Portfolio Holding"),
        "transaction_close_date": pd.to_datetime(candidates.get("transaction_close_date"), errors="coerce"),
        "round_of_funding": candidates.get("round_of_funding"),
        "total_investment_amount": candidates.get("total_investment_amount"),
        "investment_description": candidates.get("investment_description").where(
            candidates.get("investment_description").notna(),
            candidates.get("evidence_text"),
        ),
        "amount_invested_by_sponsor": candidates.get("amount_invested_by_sponsor"),
        "transaction_fund_entity_id": candidates.get("transaction_fund_entity_id"),
        "transaction_fund_name": candidates.get("transaction_fund_name").where(
            candidates.get("transaction_fund_name").notna(),
            candidates.get("fund"),
        ),
        "portfolio_status": candidates.get("portfolio_status"),
        "sector": candidates.get("sector"),
        "geography": candidates.get("geography"),
        "company_url": candidates.get("company_url"),
        "external_company_url": candidates.get("external_company_url"),
        "company_description": candidates.get("company_description"),
        "source_url": candidates.get("source_url"),
        "final_url": candidates.get("final_url"),
        "evidence_text": candidates.get("evidence_text"),
        "extraction_method": candidates.get("extraction_method"),
        "fund_match_method": candidates["fund_match_method"],
        "fund_match_confidence": candidates["fund_match_confidence"],
        "holding_review_status": candidates["holding_review_status"],
    })
    out["source_confidence"] = out["source_confidence"].astype("string").fillna("medium")
    out = out[WEBSITE_HOLDING_COLUMNS].drop_duplicates(
        subset=["crawl_run_id", "website_holding_id"]
    ).reset_index(drop=True)
    out.attrs["delta_schema_types"] = WEBSITE_HOLDING_DELTA_SCHEMA_TYPES
    return out


WEBSITE_RECONCILIATION_COLUMNS = [
    "crawl_run_id",
    "sponsor",
    "knowledge_datetime",
    "seed_ownership_count",
    "website_bridge_count",
    "matched_seed_ownership_count",
    "website_unmatched_count",
    "matched_seed_pct",
]


def build_website_bridge_reconciliation(
    website_bridge: pd.DataFrame,
    company_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    if website_bridge is None or website_bridge.empty:
        return pd.DataFrame(columns=WEBSITE_RECONCILIATION_COLUMNS)
    group_cols = ["crawl_run_id", "sponsor"] if "crawl_run_id" in website_bridge else ["sponsor"]
    if company_snapshot is None or company_snapshot.empty or not {"sponsor", "ownership_id"}.issubset(company_snapshot.columns):
        bridge_counts = website_bridge.groupby(group_cols, dropna=False).agg(
            knowledge_datetime=("knowledge_datetime", "max"),
            website_bridge_count=("website_bridge_id", "nunique"),
            matched_seed_ownership_count=("matched_seed_ownership_id", lambda x: int(pd.Series(x).dropna().nunique())),
            website_unmatched_count=("match_confidence", lambda x: int((pd.Series(x) == "unmatched").sum())),
        ).reset_index()
        bridge_counts["seed_ownership_count"] = 0
        bridge_counts["matched_seed_pct"] = pd.NA
        return bridge_counts[WEBSITE_RECONCILIATION_COLUMNS]
    seed = company_snapshot[["sponsor", "ownership_id"]].drop_duplicates().copy()
    seed["sponsor_key"] = seed["sponsor"].map(_sponsor_keys)
    seed = seed.explode("sponsor_key")
    website_sponsors = website_bridge[group_cols].drop_duplicates().copy()
    website_sponsors["sponsor_key"] = website_sponsors["sponsor"].map(_sponsor_keys)
    website_sponsors = website_sponsors.explode("sponsor_key")
    seed_for_website = website_sponsors.merge(
        seed.rename(columns={"sponsor": "seed_sponsor"}),
        on="sponsor_key",
        how="left",
    )
    seed_counts = (
        seed_for_website.groupby(group_cols, dropna=False)["ownership_id"]
        .nunique()
        .rename("seed_ownership_count")
        .reset_index()
    )
    bridge_counts = website_bridge.groupby(group_cols, dropna=False).agg(
        knowledge_datetime=("knowledge_datetime", "max"),
        website_bridge_count=("website_bridge_id", "nunique"),
        matched_seed_ownership_count=("matched_seed_ownership_id", lambda x: int(pd.Series(x).dropna().nunique())),
        website_unmatched_count=("match_confidence", lambda x: int((pd.Series(x) == "unmatched").sum())),
    ).reset_index()
    out = bridge_counts.merge(seed_counts, on=group_cols, how="left")
    out["seed_ownership_count"] = out["seed_ownership_count"].fillna(0).astype(int)
    out["matched_seed_pct"] = out["matched_seed_ownership_count"] / out["seed_ownership_count"].where(out["seed_ownership_count"] > 0)
    return out[WEBSITE_RECONCILIATION_COLUMNS]


def add_website_source_outputs(
    dataset: dict[str, pd.DataFrame],
    *,
    sponsor_websites: pd.DataFrame | None = None,
    site_snapshots: pd.DataFrame | None = None,
    holding_candidates: pd.DataFrame | None = None,
    structured_investments: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    out["source_sponsor_website_list"] = (
        sponsor_websites.copy()
        if sponsor_websites is not None
        else pd.DataFrame(columns=["crawl_run_id", "sponsor_website_id", "sponsor", "website", "portfolio_page"])
    )
    out["source_sponsor_site_snapshot"] = clean_site_snapshots(site_snapshots)
    holding_candidates = classify_holding_candidates(
        holding_candidates if holding_candidates is not None else pd.DataFrame(columns=HOLDING_CANDIDATE_COLUMNS),
        site_snapshots,
    )
    out["source_sponsor_site_holding_candidate"] = clean_holding_candidates(holding_candidates)
    out["xref_website_holding_to_portco"] = match_website_holdings_to_seed(
        holding_candidates,
        out.get("stage_consolidated_company_snapshot", pd.DataFrame()),
    )
    out["source_sponsor_site_portfolio_bridge"] = build_website_portfolio_bridge(
        holding_candidates,
        out.get("stage_consolidated_company_snapshot", pd.DataFrame()),
    )
    out["source_sponsor_website_holding"] = build_sponsor_website_holdings(
        holding_candidates,
        out.get("dim_sponsor", pd.DataFrame()),
        out.get("dim_fund", pd.DataFrame()),
        structured_investments=structured_investments,
    )
    out["validation_website_bridge_reconciliation"] = build_website_bridge_reconciliation(
        out["source_sponsor_site_portfolio_bridge"],
        out.get("stage_consolidated_company_snapshot", pd.DataFrame()),
    )
    return out
