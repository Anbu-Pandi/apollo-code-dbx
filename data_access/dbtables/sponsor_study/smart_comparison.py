"""Auditable PEVC + website sponsor replication with strict PIT enrichment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed

from data_access.dbtables.sponsor_study.backtest import (
    ACCEPTED_FUND_STATUSES,
    ACCEPTED_MATCH_TIERS,
    INCLUSIVE_FUND_STATUSES,
    INCLUSIVE_MATCH_TIERS,
    TIER_EXTERNAL_ONLY,
    TIER_UNMATCHED_BENCHMARK,
    BacktestScope,
    assert_no_lookahead,
    build_benchmark_baseline,
    build_external_holdings_from_dataset,
    build_fund_scope,
    build_match_spine,
    build_sponsor_exclusion_report,
    compare_pit_metrics,
    filter_sponsor_rows,
    measure_holding_replication,
    validate_match_spine_grain,
)
from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import (
    BLOOMBERG_BRIDGE_METHOD,
    BLOOMBERG_BRIDGE_TIER,
    MAX_CAPITAL_STACK_DESCENDANTS,
    build_bloomberg_name_index,
    capital_stack_aliases,
    fetch_bloomberg_issuer_universe,
    fetch_bloomberg_capital_stack,
    fetch_debt_features_for_bloomberg_matches,
    fetch_debt_identifier_universe,
    resolve_companies_to_bloomberg,
)
from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.company_aliases import (
    AMBIGUITY_MARGIN,
    AUTO_MATCH_MIN_SCORE,
    CONTAINMENT_MATCH_SCORE,
    REVIEW_MATCH_MIN_SCORE,
    TOKEN_OVERLAP_AUTO_MIN,
    _company_key,
    score_capital_structure_name,
    score_company_name_with_aliases,
)
from data_access.dbtables.sponsor_study.debt_identifier_crosswalk import (
    CROSSWALK_COLUMNS,
    DebtIdentifierBinding,
    bindings_from_bloomberg_matches,
    build_debt_identifier_crosswalk,
)
from data_access.dbtables.sponsor_study.findox_fundamentals import (
    attach_findox_issuer_ids,
    fetch_findox_fundamental_lines,
    fetch_findox_name_index,
    resolve_companies_to_findox,
)
from data_access.dbtables.sponsor_study.internal_joins import (
    build_internal_debt_feature_snapshot,
    build_internal_debt_instrument_snapshot,
    build_internal_financial_snapshot,
    build_internal_market_snapshot,
    fetch_debt_feature_issuer_index,
    fetch_factset_dcs_detail,
    fetch_factset_entity_relatives,
    fetch_factset_fx_rates_usd,
    fetch_latest_debt_features,
    fetch_latest_debt_features_for_factset_portcos,
)
from data_access.dbtables.sponsor_study.source_priority import (
    HOLDING_SOURCE_PRIORITY_BY_SYSTEM,
)
from data_importer.sponsor_study.constants import STUDY_ASOF_DATE


TIER5_FUZZY_AUTO = "tier5_fuzzy_auto"
TIER6_FUZZY_REVIEW = "tier6_fuzzy_review"
TIER7_ISSUER_MASTER = "tier7_issuer_master"
TIER8_MANUAL_COMPANY = "tier8_manual_company"
TIER9_MANUAL_ISSUER = "tier9_manual_issuer"

ISSUER_MASTER_SOURCE = "bloomberg_issuer_master"

DCS_AGGREGATED_SOURCE = "factset_dcs_aggregated"

DCS_SECOND_LIEN_RE = re.compile(r"\b(?:2nd|second)\s+lien\b", flags=re.IGNORECASE)
DCS_REVOLVING_FACILITY_RE = re.compile(
    r"revolv|letter of credit|overdraft|short term borrowings", flags=re.IGNORECASE
)

ALL_FUND_STATUSES = frozenset(ACCEPTED_FUND_STATUSES | INCLUSIVE_FUND_STATUSES | {"unmatched"})
SMART_ACCEPTED_MATCH_TIERS = frozenset(
    ACCEPTED_MATCH_TIERS
    | {
        TIER5_FUZZY_AUTO,
        TIER7_ISSUER_MASTER,
        TIER8_MANUAL_COMPANY,
        TIER9_MANUAL_ISSUER,
    }
)
SMART_INCLUSIVE_MATCH_TIERS = frozenset(
    INCLUSIVE_MATCH_TIERS
    | {
        TIER5_FUZZY_AUTO,
        TIER6_FUZZY_REVIEW,
        TIER7_ISSUER_MASTER,
        TIER8_MANUAL_COMPANY,
        TIER9_MANUAL_ISSUER,
    }
)
SMART_ACCEPTED_SCOPE = BacktestScope("smart_accepted", ALL_FUND_STATUSES, SMART_ACCEPTED_MATCH_TIERS)
SMART_INCLUSIVE_SCOPE = BacktestScope("smart_inclusive", ALL_FUND_STATUSES, SMART_INCLUSIVE_MATCH_TIERS)

SOURCE_PRIORITY = HOLDING_SOURCE_PRIORITY_BY_SYSTEM
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2, "unmatched": 3}
LEGAL_SHORT_SUFFIXES = {
    "ab", "ag", "bv", "co", "company", "corp", "corporation", "gmbh", "inc",
    "holdings", "incorporated", "limited", "llc", "lp", "ltd", "plc", "pty", "pvt", "sa",
    "sas", "sasu", "spa", "srl",
}
EXTERNAL_HOLDING_COLUMNS = [
    "external_holding_key",
    "external_company_id",
    "fund_id",
    "sponsor_id",
    "sponsor_name",
    "fund_name",
    "vintage",
    "factset_portco_entity_id",
    "company_name",
    "external_company_aliases",
    "company_key",
    "benchmark_portco_id",
    "investment_date",
    "source_system",
    "contributing_source_systems",
    "match_method",
    "source_confidence",
    "fund_match_confidence",
    "holding_review_status",
    "match_eligibility",
    "structured_investment_id",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "crawl_run_id",
    "knowledge_datetime",
    "source_url",
    "pitchbook_company_id",
    "pitchbook_deal_id",
    "pitchbook_deal_ids",
    "pitchbook_investor_names",
]


@dataclass(frozen=True)
class FuzzyMatchResult:
    candidate_index: int | None
    match_score: float
    score_margin: float
    match_tier: str | None
    match_status: str | None
    match_method: str | None

    @property
    def matched(self) -> bool:
        return self.match_tier is not None


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _text(value: Any) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _confidence(value: Any, default: str = "medium") -> str:
    text = _text(value).lower()
    return text if text in CONFIDENCE_RANK else default


def _safe_short_exact(left_name: Any, right_name: Any) -> bool:
    key = _company_key(left_name)
    tokens = key.split()
    if len(tokens) != 1 or len(tokens[0]) > 4:
        return True
    base = tokens[0]

    def extras(value: Any) -> set[str]:
        raw = [token for token in slug(value).split("_") if token]
        return set(raw[1:]) if raw and raw[0] == base else set(raw)

    return extras(left_name).issubset(LEGAL_SHORT_SUFFIXES) and extras(right_name).issubset(
        LEGAL_SHORT_SUFFIXES
    )


def classify_fuzzy_match(
    top_score: float,
    score_margin: float,
    *,
    reverse_margin: float | None = None,
    token_overlap: float = 1.0,
    exact_match: bool = True,
    short_key_safe: bool = True,
    auto_eligible: bool = True,
) -> tuple[str | None, str | None, str | None]:
    reverse = score_margin if reverse_margin is None else reverse_margin
    auto = (
        auto_eligible
        and top_score >= AUTO_MATCH_MIN_SCORE
        and score_margin >= AMBIGUITY_MARGIN
        and reverse >= AMBIGUITY_MARGIN
        and (exact_match or token_overlap >= TOKEN_OVERLAP_AUTO_MIN)
        and short_key_safe
    )
    if auto:
        return TIER5_FUZZY_AUTO, "accepted", "fuzzy_company_name_mutual_auto"
    if top_score >= REVIEW_MATCH_MIN_SCORE:
        return TIER6_FUZZY_REVIEW, "needs_review", "fuzzy_company_name_mutual_review"
    return None, None, None


def _name_tokens(value: Any) -> set[str]:
    return {token for token in _company_key(value).split() if len(token) >= 3}


def _pair_edges(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_id: str,
    right_id: str,
    left_name: str = "company_name",
    right_name: str = "company_name",
    left_alias_name: str | None = None,
    right_alias_name: str | None = None,
    block_by_token: bool = False,
    score_func=score_company_name_with_aliases,
) -> pd.DataFrame:
    columns = [
        left_id, right_id, "match_score", "token_overlap_score", "exact_match_flag",
        "matched_left_name", "matched_right_name",
        "forward_margin", "reverse_margin", "mutual_best",
    ]
    if left.empty or right.empty:
        return _empty(columns)

    def row_names(row: pd.Series, name_col: str, alias_col: str | None) -> list[Any]:
        names = [row[name_col]]
        aliases = row.get(alias_col) if alias_col else None
        if isinstance(aliases, (list, tuple, set)):
            names.extend(aliases)
        elif _text(aliases):
            names.extend(_text(aliases).split("|"))
        return names

    token_index: dict[str, set[int]] = {}
    if block_by_token:
        for right_idx, right_row in right.iterrows():
            for name in row_names(right_row, right_name, right_alias_name):
                for token in _name_tokens(name):
                    token_index.setdefault(token, set()).add(right_idx)

    records: list[dict[str, Any]] = []
    for _, left_row in left.iterrows():
        candidates = right
        if block_by_token:
            indices: set[int] = set()
            for name in row_names(left_row, left_name, left_alias_name):
                for token in _name_tokens(name):
                    indices.update(token_index.get(token, set()))
            candidates = right.loc[sorted(indices)] if indices else right.iloc[0:0]
        for _, right_row in candidates.iterrows():
            score, overlap, exact, matched_left, matched_right = max(
                (
                    (*score_func(left_value, right_value), left_value, right_value)
                    for left_value in row_names(left_row, left_name, left_alias_name)
                    for right_value in row_names(
                        right_row, right_name, right_alias_name
                    )
                ),
                key=lambda result: result[0],
            )
            if score < REVIEW_MATCH_MIN_SCORE:
                continue
            records.append({
                left_id: left_row[left_id],
                right_id: right_row[right_id],
                "left_name": left_row[left_name],
                "right_name": right_row[right_name],
                "match_score": float(score),
                "token_overlap_score": float(overlap),
                "exact_match_flag": bool(exact),
                "matched_left_name": matched_left,
                "matched_right_name": matched_right,
            })
    if not records:
        return _empty(columns)

    edges = pd.DataFrame(records)
    edges = edges.sort_values(
        [left_id, "match_score", "token_overlap_score", "exact_match_flag", right_id],
        ascending=[True, False, False, False, True],
    )
    edges["left_rank"] = edges.groupby(left_id).cumcount() + 1
    left_second = (
        edges[edges["left_rank"].eq(2)].set_index(left_id)["match_score"].to_dict()
    )
    edges["forward_margin"] = edges["match_score"] - edges[left_id].map(left_second).fillna(0.0)

    edges = edges.sort_values(
        [right_id, "match_score", "token_overlap_score", "exact_match_flag", left_id],
        ascending=[True, False, False, False, True],
    )
    edges["right_rank"] = edges.groupby(right_id).cumcount() + 1
    right_second = (
        edges[edges["right_rank"].eq(2)].set_index(right_id)["match_score"].to_dict()
    )
    edges["reverse_margin"] = edges["match_score"] - edges[right_id].map(right_second).fillna(0.0)
    edges["mutual_best"] = edges["left_rank"].eq(1) & edges["right_rank"].eq(1)
    return edges.reset_index(drop=True)


def _classify_edges(
    edges: pd.DataFrame,
    right: pd.DataFrame,
    *,
    right_id: str,
    left_name: str = "left_name",
    right_name: str = "right_name",
) -> pd.DataFrame:
    if edges.empty:
        return edges
    records = []
    for _, edge in edges[edges["mutual_best"]].iterrows():
        tier, status, method = classify_fuzzy_match(
            edge["match_score"],
            edge["forward_margin"],
            reverse_margin=edge["reverse_margin"],
            token_overlap=edge["token_overlap_score"],
            exact_match=edge["exact_match_flag"],
            short_key_safe=_safe_short_exact(edge[left_name], edge[right_name]),
        )
        if tier is None:
            continue
        records.append({
            **edge.to_dict(),
            "match_tier": tier,
            "match_status": status,
            "match_method": method,
        })
    return pd.DataFrame(records)


def best_fuzzy_company_match(seed_name: Any, candidate_names: pd.Series) -> FuzzyMatchResult:
    left = pd.DataFrame([{"left_id": "seed", "company_name": seed_name}])
    right = pd.DataFrame({
        "right_id": [str(index) for index in range(len(candidate_names))],
        "company_name": candidate_names.reset_index(drop=True),
        "match_eligibility": "accepted",
    })
    matches = _classify_edges(
        _pair_edges(left, right, left_id="left_id", right_id="right_id"),
        right,
        right_id="right_id",
    )
    if matches.empty:
        return FuzzyMatchResult(None, 0.0, 0.0, None, None, None)
    match = matches.iloc[0]
    return FuzzyMatchResult(
        int(match["right_id"]),
        float(match["match_score"]),
        float(match["forward_margin"]),
        match["match_tier"],
        match["match_status"],
        match["match_method"],
    )


def _smart_fund_scope(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    scope = build_fund_scope(dataset, inclusive=True).copy()
    if scope.empty:
        return scope
    status_rank = {"auto_matched": 0, "manual_matched": 0, "needs_review": 1, "unmatched": 2}
    scope["_source_rank"] = scope["source_system"].map({"factset_pevc": 0}).fillna(1)
    scope["_status_rank"] = scope["fund_match_status"].map(status_rank).fillna(9)
    scope = (
        scope.sort_values(["fund_id", "_source_rank", "_status_rank"])
        .drop_duplicates("fund_id", keep="first")
        .drop(columns=["_source_rank", "_status_rank"])
    )
    scope["source_fund_scope_status"] = scope["fund_scope_status"]
    scope["source_fund_match_status"] = scope["fund_match_status"]
    scope["fund_scope_status"] = "in_scope"
    return scope


def _fund_status_map(scope: pd.DataFrame) -> dict[str, str]:
    if scope.empty:
        return {}
    rank = {"auto_matched": 0, "manual_matched": 0, "needs_review": 1, "unmatched": 2}
    values = scope[["fund_id", "fund_match_status"]].copy()
    values["_rank"] = values["fund_match_status"].map(rank).fillna(9)
    values = values.sort_values(["fund_id", "_rank"]).drop_duplicates("fund_id")
    return dict(zip(values["fund_id"].astype(str), values["fund_match_status"]))


def _normalize_external_rows(
    rows: pd.DataFrame,
    scope: pd.DataFrame,
    *,
    source_system: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if rows.empty:
        return _empty(EXTERNAL_HOLDING_COLUMNS), _empty(EXTERNAL_HOLDING_COLUMNS)
    out = rows.copy()
    for col in [
        "fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage", "company_name",
        "benchmark_portco_id", "investment_date", "factset_portco_entity_id",
        "transaction_fund_entity_id", "transaction_fund_name",
    ]:
        if col not in out:
            out[col] = pd.NA

    out["source_system"] = source_system
    out["company_key"] = out["company_name"].map(_company_key)
    out["fund_id"] = out["fund_id"].astype("string")
    out["investment_date"] = pd.to_datetime(out["investment_date"], errors="coerce")
    if "knowledge_datetime" in out:
        out["knowledge_datetime"] = pd.to_datetime(out["knowledge_datetime"], errors="coerce")
    benchmark_funds = set(scope["fund_id"].astype(str))
    benchmark_sponsors = set(scope["sponsor_id"].astype(str))
    if source_system == "pitchbook":
        out = out[out["sponsor_id"].astype(str).isin(benchmark_sponsors)].copy()
    else:
        out = out[out["fund_id"].astype(str).isin(benchmark_funds)].copy()

    if source_system == "sponsor_website":
        out["source_confidence"] = out.get(
            "source_confidence", pd.Series("medium", index=out.index)
        ).map(_confidence)
        out["fund_match_confidence"] = out.get(
            "fund_match_confidence", pd.Series("unmatched", index=out.index)
        ).map(lambda value: _confidence(value, "unmatched"))
        out["holding_review_status"] = out.get(
            "holding_review_status", pd.Series("needs_fund_review", index=out.index)
        ).astype("string")
        out["match_eligibility"] = "accepted"
        out["match_method"] = out.get(
            "fund_match_method", pd.Series("sponsor_website", index=out.index)
        )
        out["structured_investment_id"] = out.get("website_holding_id")
        key_values = out.apply(
            lambda row: _text(row.get("company_slug")) or _text(row.get("company_name")),
            axis=1,
        )
    elif source_system == "pitchbook":
        out["source_confidence"] = out.get(
            "source_confidence", pd.Series("high", index=out.index)
        ).map(_confidence)
        out["fund_match_confidence"] = "unmatched"
        out["holding_review_status"] = "needs_fund_review"
        out["match_eligibility"] = "accepted"
        out["match_method"] = "pitchbook_sponsor_investor_deal"
        out["structured_investment_id"] = out.get("pitchbook_holding_id")
        key_values = out.apply(
            lambda row: _text(row.get("pitchbook_company_id"))
            or _text(row.get("company_name")),
            axis=1,
        )
    else:
        status_map = _fund_status_map(scope)
        status = out["fund_id"].astype(str).map(status_map).fillna("unmatched")
        out["source_confidence"] = out.get(
            "source_confidence", pd.Series("medium", index=out.index)
        ).map(_confidence)
        out["fund_match_confidence"] = status.map({
            "auto_matched": "high",
            "manual_matched": "high",
            "needs_review": "medium",
        }).fillna("unmatched")
        out["holding_review_status"] = status.map({
            "auto_matched": "auto_mapped",
            "manual_matched": "manual_mapped",
            "needs_review": "needs_review",
        }).fillna("needs_fund_review")
        # Name recall cares whether the company exists in external sources, not whether
        # the fund attribution is confirmed. Fund review status stays on
        # holding_review_status / fund_match_confidence.
        out["match_eligibility"] = status.map({
            "auto_matched": "accepted",
            "manual_matched": "accepted",
            "needs_review": "accepted",
        }).fillna("excluded")
        out["match_method"] = out.get(
            "match_method", pd.Series("factset_pevc", index=out.index)
        )
        out["structured_investment_id"] = out.get("structured_investment_id")
        key_values = out.apply(
            lambda row: _text(row.get("factset_portco_entity_id"))
            or _text(row.get("company_name")),
            axis=1,
        )

    if "external_holding_key" not in out:
        out["external_holding_key"] = [
            stable_id("ext_hold", fund_id or sponsor_id, key)
            for fund_id, sponsor_id, key in zip(
                out["fund_id"].map(_text),
                out["sponsor_id"].map(_text),
                key_values,
            )
        ]
    out["contributing_source_systems"] = source_system
    out["external_company_id"] = out.apply(
        lambda row: stable_id(
            "ext_portco",
            _text(row.get("factset_portco_entity_id"))
            or _text(row.get("sponsor_id")),
            _text(row.get("factset_portco_entity_id")) or row.get("company_key"),
        ),
        axis=1,
    )
    for col in EXTERNAL_HOLDING_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    excluded = out[out["match_eligibility"].eq("excluded")][EXTERNAL_HOLDING_COLUMNS].copy()
    eligible = out[~out["match_eligibility"].eq("excluded")][EXTERNAL_HOLDING_COLUMNS].copy()
    return eligible.reset_index(drop=True), excluded.reset_index(drop=True)


def _dedupe_scope_ids(external: pd.DataFrame) -> list[str]:
    """Dedupe scope per source: sponsor for website rows, fund for PEVC rows.

    Website fund assignments are inferred from page text and routinely fan the same
    company across several funds, so they collapse at sponsor level. PEVC fund
    attribution is a real data point and stays fund-scoped.
    """
    sponsors = (
        external["sponsor_id"] if "sponsor_id" in external
        else pd.Series("", index=external.index)
    )
    external["_sponsor_scope"] = sponsors.map(_text)
    return [
        f"sponsor:{sponsor_id}"
        if source in {"sponsor_website", "pitchbook"}
        else f"fund:{_text(fund_id)}"
        for source, sponsor_id, fund_id in zip(
            external["source_system"], external["_sponsor_scope"], external["fund_id"]
        )
    ]


def _dedupe_external_holdings(external: pd.DataFrame) -> pd.DataFrame:
    if external.empty:
        return external
    out = external.copy()
    out["_source_rank"] = out["source_system"].map(SOURCE_PRIORITY).fillna(9)
    out["_fund_rank"] = out["fund_match_confidence"].map(CONFIDENCE_RANK).fillna(9)
    out["_source_conf_rank"] = out["source_confidence"].map(CONFIDENCE_RANK).fillna(9)
    out = out.sort_values(
        [
            "_source_rank", "_fund_rank", "_source_conf_rank", "investment_date",
            "external_holding_key",
        ],
        ascending=[True, True, True, False, True],
        na_position="last",
    )
    entity = out["factset_portco_entity_id"].map(_text)
    out["_dedupe_scope"] = _dedupe_scope_ids(out)
    out["_source_dedupe_key"] = [
        f"entity:{scope}:{entity_id}" if entity_id else f"name:{scope}:{company_key}"
        for entity_id, scope, company_key in zip(entity, out["_dedupe_scope"], out["company_key"])
    ]
    out = out.drop_duplicates(["source_system", "_source_dedupe_key"], keep="first")

    canonical = out.iloc[0:0].copy()
    source_updates: dict[str, set[str]] = {}
    alias_updates: dict[str, set[str]] = {}
    for source in sorted(SOURCE_PRIORITY, key=SOURCE_PRIORITY.get):
        lower = out[out["source_system"].eq(source)].copy()
        if lower.empty:
            continue
        if canonical.empty:
            canonical = lower
            continue

        claimed_lower: set[str] = set()
        shared_sponsors = sorted(
            set(canonical["_sponsor_scope"].dropna().astype(str))
            & set(lower["_sponsor_scope"].dropna().astype(str))
        )
        for sponsor_id in shared_sponsors:
            left = lower[lower["_sponsor_scope"].astype(str).eq(sponsor_id)].rename(
                columns={"external_holding_key": "lower_holding_key"}
            )
            right = (
                canonical[canonical["_sponsor_scope"].astype(str).eq(sponsor_id)]
                .sort_values(["_source_rank", "external_holding_key"])
                .drop_duplicates("company_key")
                .rename(columns={"external_holding_key": "canonical_holding_key"})
            )
            matches = _classify_edges(
                _pair_edges(
                    left,
                    right,
                    left_id="lower_holding_key",
                    right_id="canonical_holding_key",
                    right_alias_name="external_company_aliases",
                    block_by_token=True,
                ),
                right,
                right_id="canonical_holding_key",
            )
            if matches.empty:
                continue
            accepted = matches[matches["match_tier"].eq(TIER5_FUZZY_AUTO)]
            for _, match in accepted.iterrows():
                lower_key = str(match["lower_holding_key"])
                canonical_key = str(match["canonical_holding_key"])
                claimed_lower.add(lower_key)
                source_updates.setdefault(canonical_key, set()).add(source)
                lower_name = _text(
                    lower.loc[
                        lower["external_holding_key"].astype(str).eq(lower_key),
                        "company_name",
                    ].iloc[0]
                )
                canonical_name = _text(
                    canonical.loc[
                        canonical["external_holding_key"].astype(str).eq(canonical_key),
                        "company_name",
                    ].iloc[0]
                )
                if (
                    lower_name
                    and slug(lower_name) != slug(canonical_name)
                ):
                    alias_updates.setdefault(canonical_key, set()).add(lower_name)
                    target = canonical["external_holding_key"].astype(str).eq(
                        canonical_key
                    )
                    existing = {
                        alias
                        for value in canonical.loc[
                            target, "external_company_aliases"
                        ].dropna()
                        for alias in str(value).split("|")
                        if alias
                    }
                    canonical.loc[target, "external_company_aliases"] = "|".join(
                        sorted(existing | {lower_name})
                    )

        if claimed_lower:
            lower = lower[
                ~lower["external_holding_key"].astype(str).isin(claimed_lower)
            ].copy()
        canonical = pd.concat([canonical, lower], ignore_index=True)

    for key, aliases in alias_updates.items():
        target = canonical["external_holding_key"].astype(str).eq(key)
        current = set(
            "|".join(
                canonical.loc[target, "external_company_aliases"]
                .dropna()
                .astype(str)
            ).split("|")
        )
        canonical.loc[target, "external_company_aliases"] = "|".join(
            sorted((current | aliases) - {""})
        )
    for key, added_sources in source_updates.items():
        target = canonical["external_holding_key"].astype(str).eq(key)
        current = set(
            "|".join(
                canonical.loc[target, "contributing_source_systems"]
                .dropna()
                .astype(str)
            ).split("|")
        )
        sources = current | added_sources
        ordered = sorted(sources - {""}, key=lambda value: SOURCE_PRIORITY.get(value, 9))
        canonical.loc[target, "contributing_source_systems"] = "|".join(ordered)
    return canonical.drop(
        columns=[
            "_source_rank", "_fund_rank", "_source_conf_rank", "_source_dedupe_key",
            "_dedupe_scope", "_sponsor_scope",
        ],
        errors="ignore",
    ).reset_index(drop=True)


def build_combined_external_universe(
    dataset: dict[str, pd.DataFrame],
    fund_scope: pd.DataFrame | None = None,
    additional_external_holdings: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scope = fund_scope if fund_scope is not None else _smart_fund_scope(dataset)
    base_scope = build_fund_scope(dataset, inclusive=True)
    pevc_source = build_external_holdings_from_dataset(dataset, base_scope)
    if additional_external_holdings is not None and not additional_external_holdings.empty:
        pevc_source = pd.concat([pevc_source, additional_external_holdings], ignore_index=True)
    pevc, pevc_excluded = _normalize_external_rows(pevc_source, scope, source_system="factset_pevc")
    website, website_excluded = _normalize_external_rows(
        dataset.get("source_sponsor_website_holding", pd.DataFrame()),
        scope,
        source_system="sponsor_website",
    )
    pitchbook, pitchbook_excluded = _normalize_external_rows(
        dataset.get("source_pitchbook_holding", pd.DataFrame()),
        scope,
        source_system="pitchbook",
    )
    eligible_frames = [frame for frame in [pevc, website, pitchbook] if not frame.empty]
    excluded_frames = [
        frame
        for frame in [pevc_excluded, website_excluded, pitchbook_excluded]
        if not frame.empty
    ]
    combined = (
        pd.concat(eligible_frames, ignore_index=True)
        if eligible_frames
        else _empty(EXTERNAL_HOLDING_COLUMNS)
    )
    excluded = (
        pd.concat(excluded_frames, ignore_index=True)
        if excluded_frames
        else _empty(EXTERNAL_HOLDING_COLUMNS)
    )
    return _dedupe_external_holdings(combined), excluded


def build_combined_external_holdings(
    dataset: dict[str, pd.DataFrame],
    fund_scope: pd.DataFrame | None = None,
    additional_external_holdings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    combined, _ = build_combined_external_universe(
        dataset,
        fund_scope,
        additional_external_holdings,
    )
    return combined


def _external_lookup(external: pd.DataFrame) -> pd.DataFrame:
    return external.drop_duplicates("external_holding_key").set_index("external_holding_key")


def _write_external_provenance(out: pd.DataFrame, idx, ext: pd.Series) -> None:
    out.loc[idx, "external_source_system"] = ext.get("source_system")
    out.loc[idx, "contributing_source_systems"] = ext.get(
        "contributing_source_systems", ext.get("source_system")
    )
    out.loc[idx, "external_source_confidence"] = ext.get("source_confidence")
    out.loc[idx, "external_fund_match_confidence"] = ext.get("fund_match_confidence")
    out.loc[idx, "external_match_eligibility"] = ext.get("match_eligibility", "accepted")
    out.loc[idx, "external_fund_id"] = ext.get("fund_id")
    out.loc[idx, "external_fund_name"] = ext.get("fund_name")


def _fuzzy_assignment_pass(
    benchmark_rows: pd.DataFrame,
    external_holdings: pd.DataFrame,
    *,
    scope_column: str,
    claimed_external_keys: set[str],
) -> pd.DataFrame:
    """Mutual-best one-to-one fuzzy assignment within a single scope (fund, then sponsor)."""
    if benchmark_rows.empty:
        return _empty(["holding_key", "external_holding_key"])
    frames: list[pd.DataFrame] = []
    for scope_value, group in benchmark_rows.groupby(scope_column, dropna=False):
        candidates = external_holdings[
            external_holdings[scope_column].astype(str).eq(str(scope_value))
            & ~external_holdings["external_holding_key"].astype(str).isin(claimed_external_keys)
        ].copy()
        matches = _classify_edges(
            _pair_edges(
                group,
                candidates,
                left_id="holding_key",
                right_id="external_holding_key",
                right_alias_name="external_company_aliases",
            ),
            candidates,
            right_id="external_holding_key",
        )
        if not matches.empty:
            frames.append(matches)
    if not frames:
        return _empty(["holding_key", "external_holding_key"])
    return pd.concat(frames, ignore_index=True).sort_values(
        ["match_status", "match_score", "holding_key", "external_holding_key"],
        ascending=[True, False, True, True],
    )


def _global_company_assignment(
    benchmark_rows: pd.DataFrame,
    external_holdings: pd.DataFrame,
) -> pd.DataFrame:
    """High-confidence company-existence matches under the source waterfall."""
    if benchmark_rows.empty or external_holdings.empty:
        return _empty(["holding_key", "external_holding_key"])
    remaining = set(benchmark_rows["holding_key"].dropna().astype(str))
    accepted_records: list[dict[str, Any]] = []
    review_records: dict[str, dict[str, Any]] = {}
    for source in sorted(SOURCE_PRIORITY, key=SOURCE_PRIORITY.get):
        group = benchmark_rows[
            benchmark_rows["holding_key"].astype(str).isin(remaining)
        ]
        if group.empty:
            break
        candidates = (
            external_holdings[external_holdings["source_system"].eq(source)]
            .sort_values("external_holding_key")
            .drop_duplicates("company_key")
        )
        edges = _pair_edges(
            group,
            candidates,
            left_id="holding_key",
            right_id="external_holding_key",
            right_alias_name="external_company_aliases",
            block_by_token=True,
        )
        if edges.empty:
            continue
        for _, edge in edges[edges["left_rank"].eq(1)].iterrows():
            tier, status, _ = classify_fuzzy_match(
                edge["match_score"],
                edge["forward_margin"],
                reverse_margin=1.0,
                token_overlap=edge["token_overlap_score"],
                exact_match=edge["exact_match_flag"],
                short_key_safe=_safe_short_exact(
                    edge["left_name"], edge["right_name"]
                ),
            )
            if tier is None:
                continue
            record = {
                **edge.to_dict(),
                "match_tier": tier,
                "match_status": status,
                "match_method": (
                    "fuzzy_company_name_global_auto"
                    if tier == TIER5_FUZZY_AUTO
                    else "fuzzy_company_name_global_review"
                ),
            }
            holding_key = str(edge["holding_key"])
            if tier == TIER5_FUZZY_AUTO:
                accepted_records.append(record)
                remaining.discard(holding_key)
            else:
                review_records.setdefault(holding_key, record)
    return pd.DataFrame(
        accepted_records
        + [review_records[key] for key in sorted(remaining & review_records.keys())]
    )


def _apply_fuzzy_assignment(
    out: pd.DataFrame,
    assignment: pd.DataFrame,
    lookup: pd.DataFrame,
    matched_external_keys: set[str],
    *,
    allow_external_reuse: bool = False,
    allow_review_override: bool = False,
) -> None:
    if assignment.empty:
        return
    open_mask = out["match_tier"].eq(TIER_UNMATCHED_BENCHMARK)
    if allow_review_override:
        open_mask = ~out["match_tier"].isin(SMART_ACCEPTED_MATCH_TIERS)
    open_holdings = set(
        out.loc[
            out["benchmark_side"].eq("benchmark") & open_mask,
            "holding_key",
        ]
        .dropna()
        .astype(str)
    )
    for _, match in assignment.iterrows():
        holding_key = str(match["holding_key"])
        ext_key = str(match["external_holding_key"])
        if (
            holding_key not in open_holdings
            or (ext_key in matched_external_keys and not allow_external_reuse)
        ):
            continue
        positions = out.index[out["holding_key"].astype(str).eq(holding_key)]
        if positions.empty:
            continue
        idx = positions[0]
        ext = lookup.loc[ext_key]
        out.loc[idx, "external_holding_key"] = ext_key
        out.loc[idx, "factset_portco_entity_id"] = ext["factset_portco_entity_id"]
        out.loc[idx, "external_company_name"] = ext["company_name"]
        out.loc[idx, "match_tier"] = match["match_tier"]
        out.loc[idx, "match_status"] = match["match_status"]
        out.loc[idx, "miss_reason"] = "matched"
        out.loc[idx, "match_method"] = match["match_method"]
        out.loc[idx, "match_score"] = match["match_score"]
        out.loc[idx, "token_overlap_score"] = match["token_overlap_score"]
        out.loc[idx, "forward_score_margin"] = match["forward_margin"]
        out.loc[idx, "reverse_score_margin"] = match["reverse_margin"]
        _write_external_provenance(out, idx, ext)
        benchmark_fund = _text(out.loc[idx, "fund_id"])
        external_fund = _text(ext.get("fund_id"))
        same_sponsor = _text(out.loc[idx, "sponsor_id"]) == _text(ext.get("sponsor_id"))
        same_fund = bool(
            benchmark_fund and external_fund and benchmark_fund == external_fund
        )
        out.loc[idx, "fund_attribution_status"] = (
            "same_fund"
            if same_fund
            else "cross_fund_within_sponsor"
            if same_sponsor
            else "cross_sponsor_company_exists"
        )
        matched_external_keys.add(ext_key)
        open_holdings.discard(holding_key)


def apply_fuzzy_benchmark_matches(
    match_spine: pd.DataFrame,
    external_holdings: pd.DataFrame,
) -> pd.DataFrame:
    if match_spine.empty or external_holdings.empty:
        return match_spine
    out = match_spine.copy()
    for col in [
        "match_score", "token_overlap_score", "forward_score_margin", "reverse_score_margin",
        "external_source_system", "contributing_source_systems", "external_source_confidence",
        "external_fund_match_confidence", "external_match_eligibility", "external_fund_id",
        "external_fund_name", "fund_attribution_status",
    ]:
        if col not in out:
            out[col] = pd.NA
    lookup = _external_lookup(external_holdings)

    linked_benchmark = (
        out["benchmark_side"].eq("benchmark")
        & out["external_holding_key"].notna()
    )
    for idx in out[linked_benchmark].index:
        ext_key = _text(out.loc[idx, "external_holding_key"])
        if not ext_key or ext_key not in lookup.index:
            continue
        ext = lookup.loc[ext_key]
        _write_external_provenance(out, idx, ext)
        if out.loc[idx, "miss_reason"] == "matched":
            benchmark_fund = _text(out.loc[idx, "fund_id"])
            external_fund = _text(ext.get("fund_id"))
            out.loc[idx, "fund_attribution_status"] = (
                "same_fund"
                if benchmark_fund and external_fund and benchmark_fund == external_fund
                else "cross_fund_within_sponsor"
                if _text(out.loc[idx, "sponsor_id"]) == _text(ext.get("sponsor_id"))
                else "cross_sponsor_company_exists"
            )

    matched_external_keys = set(
        out.loc[
            out["benchmark_side"].eq("benchmark") & out["miss_reason"].eq("matched"),
            "external_holding_key",
        ].dropna().astype(str)
    )
    for scope_column in ["fund_id", "sponsor_id"]:
        unmatched = out[
            out["benchmark_side"].eq("benchmark")
            & out["match_tier"].eq(TIER_UNMATCHED_BENCHMARK)
        ]
        assignment = _fuzzy_assignment_pass(
            unmatched,
            external_holdings,
            scope_column=scope_column,
            claimed_external_keys=matched_external_keys,
        )
        _apply_fuzzy_assignment(out, assignment, lookup, matched_external_keys)

    unmatched = out[
        out["benchmark_side"].eq("benchmark")
        & ~out["match_tier"].isin(SMART_ACCEPTED_MATCH_TIERS)
    ]
    _apply_fuzzy_assignment(
        out,
        _global_company_assignment(unmatched, external_holdings),
        lookup,
        matched_external_keys,
        allow_external_reuse=True,
        allow_review_override=True,
    )

    for idx in out[out["benchmark_side"].eq("external")].index:
        ext_key = _text(out.loc[idx, "external_holding_key"])
        if not ext_key or ext_key not in lookup.index:
            continue
        _write_external_provenance(out, idx, lookup.loc[ext_key])

    external_only = (
        out["benchmark_side"].eq("external")
        & out["match_tier"].eq(TIER_EXTERNAL_ONLY)
        & out["external_holding_key"].astype(str).isin(matched_external_keys)
    )
    out = out.loc[~external_only].copy()
    represented_external = set(
        out.loc[
            out["benchmark_side"].eq("external"), "external_holding_key"
        ].dropna().astype(str)
    )
    review_claims = out[
        out["benchmark_side"].eq("benchmark")
        & out["external_holding_key"].notna()
        & ~out["miss_reason"].eq("matched")
    ]
    extra_rows = []
    for _, claim in review_claims.iterrows():
        ext_key = str(claim["external_holding_key"])
        if ext_key in represented_external or ext_key in matched_external_keys:
            continue
        row = claim.to_dict()
        row.update({
            "holding_key": pd.NA,
            "benchmark_side": "external",
            "portco_id": claim.get("portco_id"),
            "company_name": claim.get("external_company_name"),
            "match_tier": TIER_EXTERNAL_ONLY,
            "match_status": "external_only",
            "miss_reason": "external_only",
            "match_method": "review_mapping_external_only",
        })
        extra_rows.append(row)
        represented_external.add(ext_key)
    if extra_rows:
        out = pd.concat([out, pd.DataFrame(extra_rows)], ignore_index=True)
    return out.reset_index(drop=True)


def apply_manual_company_matches(
    match_spine: pd.DataFrame,
    external_holdings: pd.DataFrame,
    approvals: pd.DataFrame,
) -> pd.DataFrame:
    """Promote only explicitly reviewed holding/external pairs."""
    if match_spine.empty or external_holdings.empty or approvals.empty:
        return match_spine
    required = {
        "holding_key",
        "external_holding_key",
        "benchmark_company_name",
        "external_company_name",
        "review_status",
    }
    missing = required - set(approvals)
    if missing:
        raise ValueError(
            f"Manual company matches are missing columns: {sorted(missing)}"
        )
    if not approvals["review_status"].astype(str).eq("manual_matched").all():
        raise ValueError("Manual company matches must have review_status=manual_matched")

    out = match_spine.copy()
    benchmark_keys = set(
        out.loc[out["benchmark_side"].eq("benchmark"), "holding_key"]
        .dropna()
        .astype(str)
    )
    external_keys = set(
        external_holdings["external_holding_key"].dropna().astype(str)
    )
    approved_holding_keys = set(approvals["holding_key"].astype(str))
    approved_external_keys = set(approvals["external_holding_key"].astype(str))
    if missing_holdings := approved_holding_keys - benchmark_keys:
        raise ValueError(
            f"Manual company matches reference missing holdings: {sorted(missing_holdings)}"
        )
    if missing_external := approved_external_keys - external_keys:
        raise ValueError(
            f"Manual company matches reference missing external rows: {sorted(missing_external)}"
        )

    text_columns = [
        "external_company_name",
        "manual_review_status",
        "manual_approved_by",
        "manual_approved_at",
        "manual_rationale",
        "external_source_system",
        "contributing_source_systems",
        "external_source_confidence",
        "external_fund_match_confidence",
        "external_match_eligibility",
        "external_fund_id",
        "external_fund_name",
        "fund_attribution_status",
    ]
    for column in text_columns:
        out[column] = out.get(
            column, pd.Series(pd.NA, index=out.index, dtype="object")
        ).astype("object")

    lookup = _external_lookup(external_holdings)
    for approval in approvals.itertuples(index=False):
        holding_key = str(approval.holding_key)
        external_key = str(approval.external_holding_key)
        positions = out.index[
            out["benchmark_side"].eq("benchmark")
            & out["holding_key"].astype(str).eq(holding_key)
        ]
        if len(positions) != 1:
            raise ValueError(
                f"Manual company match requires one benchmark row for {holding_key}"
            )
        idx = positions[0]
        ext = lookup.loc[external_key]
        benchmark_name = _text(out.loc[idx, "company_name"])
        external_name = _text(ext.get("company_name"))
        if benchmark_name != _text(approval.benchmark_company_name):
            raise ValueError(
                f"Manual benchmark name drift for {holding_key}: {benchmark_name}"
            )
        if external_name != _text(approval.external_company_name):
            raise ValueError(
                f"Manual external name drift for {external_key}: {external_name}"
            )

        out.loc[idx, "external_holding_key"] = external_key
        out.loc[idx, "factset_portco_entity_id"] = ext.get(
            "factset_portco_entity_id"
        )
        out.loc[idx, "external_company_name"] = external_name
        out.loc[idx, "match_tier"] = TIER8_MANUAL_COMPANY
        out.loc[idx, "match_status"] = "accepted"
        out.loc[idx, "miss_reason"] = "matched"
        out.loc[idx, "match_method"] = "manual_company_match"
        out.loc[idx, "manual_review_status"] = "manual_matched"
        out.loc[idx, "manual_approved_by"] = getattr(
            approval, "approved_by", pd.NA
        )
        out.loc[idx, "manual_approved_at"] = getattr(
            approval, "approved_at", pd.NA
        )
        out.loc[idx, "manual_rationale"] = getattr(
            approval, "rationale", pd.NA
        )
        _write_external_provenance(out, idx, ext)
        benchmark_fund = _text(out.loc[idx, "fund_id"])
        external_fund = _text(ext.get("fund_id"))
        same_sponsor = _text(out.loc[idx, "sponsor_id"]) == _text(
            ext.get("sponsor_id")
        )
        out.loc[idx, "fund_attribution_status"] = (
            "same_fund"
            if benchmark_fund
            and external_fund
            and benchmark_fund == external_fund
            else "cross_fund_within_sponsor"
            if same_sponsor
            else "cross_sponsor_company_exists"
        )

    claimed = out[
        out["benchmark_side"].eq("benchmark")
        & out["match_tier"].eq(TIER8_MANUAL_COMPANY)
    ]["external_holding_key"].dropna().astype(str)
    duplicate_external_rows = (
        out["benchmark_side"].eq("external")
        & out["external_holding_key"].astype(str).isin(set(claimed))
    )
    return out.loc[~duplicate_external_rows].reset_index(drop=True)


def build_smart_match_spine(
    dataset: dict[str, pd.DataFrame],
    benchmark_holdings: pd.DataFrame | None = None,
    external_holdings: pd.DataFrame | None = None,
    *,
    fund_scope: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scope = fund_scope if fund_scope is not None else _smart_fund_scope(dataset)
    external = (
        external_holdings
        if external_holdings is not None
        else build_combined_external_holdings(dataset, scope)
    )
    spine = build_match_spine(
        dataset,
        benchmark_holdings,
        external,
        fund_scope=scope,
        enforce_transaction_fund_scope=False,
    )
    spine = apply_fuzzy_benchmark_matches(spine, external)
    spine = apply_manual_company_matches(
        spine,
        external,
        dataset.get("xref_manual_company_match", pd.DataFrame()),
    )
    return spine, external


def _benchmark_alias_names(
    match_spine: pd.DataFrame,
    external: pd.DataFrame,
) -> pd.DataFrame:
    """v31 names of matched holdings, keyed by external company for issuer-name matching."""
    columns = ["external_company_id", "benchmark_company_name"]
    if match_spine.empty or external.empty:
        return _empty(columns)
    matched = match_spine[
        match_spine["benchmark_side"].eq("benchmark")
        & match_spine["external_holding_key"].notna()
    ][["external_holding_key", "company_name"]].copy()
    if matched.empty:
        return _empty(columns)
    matched["external_holding_key"] = matched["external_holding_key"].astype(str)
    bridge = external[["external_holding_key", "external_company_id"]].copy()
    bridge["external_holding_key"] = bridge["external_holding_key"].astype(str)
    out = matched.merge(bridge, on="external_holding_key", how="inner").rename(
        columns={"company_name": "benchmark_company_name"}
    )
    return (
        out[columns].dropna().drop_duplicates("external_company_id").reset_index(drop=True)
    )


def validate_smart_match_spine(match_spine: pd.DataFrame) -> None:
    validate_match_spine_grain(match_spine)
    accepted = match_spine[
        match_spine["benchmark_side"].eq("benchmark")
        & match_spine["match_status"].eq("accepted")
        & match_spine["external_holding_key"].notna()
    ]
    duplicate_groups = accepted[
        accepted.duplicated("external_holding_key", keep=False)
    ].copy()
    invalid_duplicates = (
        duplicate_groups.assign(
            _attributed=~(
                duplicate_groups["match_method"]
                .astype(str)
                .str.startswith("fuzzy_company_name_global_")
                | duplicate_groups["match_method"].astype(str).eq(
                    "manual_company_match"
                )
            )
        )
        .groupby("external_holding_key")["_attributed"]
        .sum()
        .gt(1)
    )
    if invalid_duplicates.any():
        raise ValueError("Duplicate accepted external holding mappings detected")


def unmatched_benchmark_companies(match_spine: pd.DataFrame) -> pd.DataFrame:
    """Benchmark holdings no external source reached, keyed for the issuer-master resolver."""
    if match_spine.empty:
        return _empty(["external_company_id", "company_name", "sponsor_name"])
    benchmark = match_spine[match_spine["benchmark_side"].eq("benchmark")]
    missed = benchmark[benchmark["miss_reason"].ne("matched")]
    return (
        missed[["holding_key", "company_name", "sponsor_name"]]
        .dropna(subset=["company_name"])
        .drop_duplicates("holding_key")
        .rename(columns={"holding_key": "external_company_id"})
        .reset_index(drop=True)
    )


def apply_issuer_master_source(
    match_spine: pd.DataFrame, resolutions: pd.DataFrame
) -> pd.DataFrame:
    """Credit a benchmark holding whose only witness is the Bloomberg issuer master.

    FactSet PEVC and the website crawl are the sources recall is measured against, so a company
    neither of them carries counts as a miss however well its name is spelled. The issuer master
    is an independent witness that a company exists and is financeable, and it is what reaches
    `debt_features`, so an exact name resolution there is treated as replication from a third
    source. No external holding exists to point at, so `external_holding_key` stays null and the
    precision denominator is untouched; only recall moves. Containment resolutions are left out
    because a token-prefix hit against the whole issuer universe is far weaker evidence than one
    inside a single sponsor's portfolio.
    """
    if match_spine.empty or resolutions.empty:
        return match_spine
    exact = resolutions[resolutions["exact_match_flag"].astype(bool)]
    if exact.empty:
        return match_spine
    credited = exact.set_index("external_company_id")
    out = match_spine.copy()
    target = (
        out["benchmark_side"].eq("benchmark")
        & ~out["match_tier"].isin(SMART_ACCEPTED_MATCH_TIERS)
        & out["holding_key"].isin(credited.index)
    )
    if not target.any():
        return out
    keys = out.loc[target, "holding_key"]
    out.loc[target, "match_tier"] = TIER7_ISSUER_MASTER
    out.loc[target, "miss_reason"] = "matched"
    out.loc[target, "match_status"] = "accepted"
    out.loc[target, "match_method"] = BLOOMBERG_BRIDGE_METHOD
    out.loc[target, "external_holding_key"] = pd.NA
    out.loc[target, "factset_portco_entity_id"] = pd.NA
    out.loc[target, "external_source_system"] = ISSUER_MASTER_SOURCE
    out.loc[target, "contributing_source_systems"] = ISSUER_MASTER_SOURCE
    out.loc[target, "external_match_eligibility"] = "accepted"
    out.loc[target, "external_company_name"] = keys.map(credited["bbg_primary_name"])
    if "match_score" in out.columns and "match_score" in credited.columns:
        out.loc[target, "match_score"] = keys.map(credited["match_score"])
    return out


def apply_manual_issuer_matches(
    match_spine: pd.DataFrame,
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    """Apply reviewed Bloomberg issuer identities without an external holding row."""
    if match_spine.empty or decisions.empty:
        return match_spine
    required = {
        "holding_key",
        "benchmark_company_name",
        "matched_bbg_name",
        "id_bb_company",
        "decision_status",
    }
    missing = required - set(decisions)
    if missing:
        raise ValueError(
            f"Manual issuer decisions are missing columns: {sorted(missing)}"
        )
    out = match_spine.copy()
    for column in (
        "external_company_name",
        "manual_review_status",
        "manual_approved_by",
        "manual_approved_at",
        "manual_rationale",
        "external_source_system",
        "contributing_source_systems",
        "external_match_eligibility",
    ):
        out[column] = out.get(
            column, pd.Series(pd.NA, index=out.index, dtype="object")
        ).astype("object")
    for decision in decisions.itertuples(index=False):
        holding_key = str(decision.holding_key)
        positions = out.index[
            out["benchmark_side"].eq("benchmark")
            & out["holding_key"].astype(str).eq(holding_key)
        ]
        if len(positions) != 1:
            raise ValueError(
                f"Manual issuer decision requires one benchmark row for {holding_key}"
            )
        idx = positions[0]
        benchmark_name = _text(out.at[idx, "company_name"])
        if benchmark_name != _text(decision.benchmark_company_name):
            raise ValueError(
                f"Manual issuer benchmark name drift for {holding_key}: {benchmark_name}"
            )
        status = _text(decision.decision_status)
        out.at[idx, "manual_review_status"] = f"manual_issuer_{status}"
        out.at[idx, "manual_approved_by"] = getattr(
            decision, "approved_by", pd.NA
        )
        out.at[idx, "manual_approved_at"] = getattr(
            decision, "approved_at", pd.NA
        )
        out.at[idx, "manual_rationale"] = getattr(
            decision, "rationale", pd.NA
        )
        if status != "accepted" or out.at[idx, "match_tier"] in SMART_ACCEPTED_MATCH_TIERS:
            continue
        out.at[idx, "match_tier"] = TIER9_MANUAL_ISSUER
        out.at[idx, "match_status"] = "accepted"
        out.at[idx, "miss_reason"] = "matched"
        out.at[idx, "match_method"] = "manual_bloomberg_issuer_review"
        out.at[idx, "external_holding_key"] = pd.NA
        out.at[idx, "factset_portco_entity_id"] = pd.NA
        out.at[idx, "external_company_name"] = _text(decision.matched_bbg_name)
        out.at[idx, "external_source_system"] = ISSUER_MASTER_SOURCE
        out.at[idx, "contributing_source_systems"] = ISSUER_MASTER_SOURCE
        out.at[idx, "external_match_eligibility"] = "accepted"
        out.at[idx, "match_score"] = 1.0
    return out


def issuer_master_crosswalk(
    resolutions: pd.DataFrame, *, resolved_asof_date: str = STUDY_ASOF_DATE
) -> pd.DataFrame:
    """Crosswalk rows recording how every issuer-master credit reaches `debt_features`.

    A tier7 credit is the one route to structured data that no external holding witnesses, so
    without this it would raise recall while leaving no auditable record of the identifier it
    resolved through. Rows carry the same schema and the same binding rules as the persisted
    crosswalk, keyed by v31 holding rather than by external company because these are precisely
    the companies no external source carried. Only the exact resolutions credited to the spine
    are recorded, so the artifact and the recall number cannot disagree.
    """
    if resolutions.empty or "exact_match_flag" not in resolutions:
        return pd.DataFrame(columns=CROSSWALK_COLUMNS)
    exact = resolutions[resolutions["exact_match_flag"].astype(bool)]
    return build_debt_identifier_crosswalk(
        bindings_from_bloomberg_matches(exact), resolved_asof_date=resolved_asof_date
    )


def manual_issuer_crosswalk(
    decisions: pd.DataFrame,
    *,
    resolved_asof_date: str = STUDY_ASOF_DATE,
) -> pd.DataFrame:
    if decisions.empty:
        return pd.DataFrame(columns=CROSSWALK_COLUMNS)
    accepted = decisions[decisions["decision_status"].eq("accepted")]
    bindings = [
        DebtIdentifierBinding(
            portco_id=str(row.holding_key),
            company_name=str(row.benchmark_company_name),
            key_type="id_bb_company",
            key_value=str(row.id_bb_company),
            resolution_source="manual_bloomberg_issuer_review",
            match_method="manual_bloomberg_issuer_review",
            match_confidence="high",
            review_status="manual_matched",
            match_score=1.0,
            evidence_source=str(row.matched_bbg_name),
        )
        for row in accepted.itertuples(index=False)
    ]
    return build_debt_identifier_crosswalk(
        bindings,
        resolved_asof_date=resolved_asof_date,
    )


def resolve_unmatched_to_issuer_master(
    match_spine: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    asof_cutoff: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve the holdings no external source reached against the Bloomberg issuer master."""
    companies = unmatched_benchmark_companies(match_spine)
    if companies.empty:
        return _empty(["external_company_id"]), _empty(["external_company_id"])
    identifiers = fetch_debt_identifier_universe(
        app=app, table_name=table_name, asof_cutoff=asof_cutoff
    )
    name_index = build_bloomberg_name_index(
        fetch_bloomberg_issuer_universe(identifiers, app=app, asof_cutoff=asof_cutoff)
    )
    matches, audit = resolve_companies_to_bloomberg(companies, name_index)
    if not matches.empty:
        matches = matches.merge(
            companies[["external_company_id", "company_name", "sponsor_name"]],
            on="external_company_id",
            how="left",
            suffixes=("", "_benchmark"),
        )
    return matches, audit


def measure_smart_replication(
    match_spine: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted_spine = match_spine.copy()
    accepted, accepted_miss = measure_holding_replication(
        accepted_spine,
        scopes=[SMART_ACCEPTED_SCOPE],
    )
    inclusive, inclusive_miss = measure_holding_replication(
        match_spine,
        scopes=[SMART_INCLUSIVE_SCOPE],
    )
    miss_reasons = pd.concat([accepted_miss, inclusive_miss], ignore_index=True).drop_duplicates()
    return pd.concat([accepted, inclusive], ignore_index=True), miss_reasons


def _source_attribution(match_spine: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for scope in [SMART_ACCEPTED_SCOPE, SMART_INCLUSIVE_SCOPE]:
        matched = match_spine[
            match_spine["benchmark_side"].eq("benchmark")
            & match_spine["miss_reason"].eq("matched")
            & match_spine["match_tier"].isin(scope.match_tiers)
        ].copy()
        matched["source_bucket"] = matched["contributing_source_systems"].fillna(
            matched["external_source_system"]
        )
        for rollup, group_cols in [
            ("overall", []),
            ("sponsor", ["sponsor_id", "sponsor_name"]),
            ("fund", ["fund_id", "fund_name", "vintage"]),
        ]:
            groups = [((), matched)] if not group_cols else matched.groupby(group_cols, dropna=False)
            for key, group in groups:
                keys = key if isinstance(key, tuple) else (key,)
                for source_bucket, source_group in group.groupby("source_bucket", dropna=False):
                    records.append({
                        "scope_label": scope.label,
                        "rollup_level": rollup,
                        **dict(zip(group_cols, keys)),
                        "source_bucket": source_bucket,
                        "matched_benchmark": source_group["holding_key"].nunique(),
                    })
    return pd.DataFrame(records)


def _filter_included_sponsors(
    frame: pd.DataFrame,
    sponsor_names: tuple[str, ...],
) -> pd.DataFrame:
    if frame.empty or not sponsor_names or "sponsor_name" not in frame:
        return frame.copy()
    allowed = {name.strip().casefold() for name in sponsor_names if name.strip()}
    return frame[
        frame["sponsor_name"].astype("string").str.strip().str.casefold().isin(allowed)
    ].reset_index(drop=True)


def _filter_included_sponsor_ids(
    frame: pd.DataFrame,
    sponsor_ids: set[str],
) -> pd.DataFrame:
    if frame.empty or "sponsor_id" not in frame:
        return frame.copy()
    if not sponsor_ids:
        return frame.iloc[0:0].copy()
    return frame[frame["sponsor_id"].astype(str).isin(sponsor_ids)].reset_index(drop=True)


def _issuer_label(row: pd.Series) -> str:
    for col in ["issuer_name", "issuer", "company_name"]:
        value = _text(row.get(col))
        if value:
            return value
    return ""


def _debt_feature_key(row: pd.Series) -> str:
    for col in [
        "debt_feature_key", "instrument_id_uniq", "instrument_id", "isin", "bbg_id",
        "fsym_id", "fsym_security_id", "ticker_region", "ticker",
    ]:
        value = _text(row.get(col))
        if value:
            return value
    return stable_id("debt_feature", _issuer_label(row), row.name)


def _issuer_alias_names(
    companies: pd.DataFrame,
    benchmark_aliases: pd.DataFrame | None,
) -> pd.Series:
    """Extra names each company may be scored under when matching debt issuers.

    Debt is often filed under a name the external universe never carries: the Viasat
    holding is listed as `Inmarsat Group Holdings Ltd.` in PEVC while its debt is issued by
    `ViaSat Inc.`. Carrying the matched v31 name as an alias lets the scorer try both names
    without loosening the score threshold.
    """
    empty = pd.Series([() for _ in range(len(companies))], index=companies.index)
    if benchmark_aliases is None or benchmark_aliases.empty:
        return empty
    alias_by_company = (
        benchmark_aliases.dropna(subset=["external_company_id", "benchmark_company_name"])
        .groupby(benchmark_aliases["external_company_id"].astype(str))
        ["benchmark_company_name"].first().to_dict()
    )
    aliases = companies["external_company_id"].astype(str).map(alias_by_company).map(_text)
    distinct = aliases.ne("") & aliases.map(_company_key).ne(
        companies["company_name"].map(_company_key)
    )
    return pd.Series(
        [(alias,) if keep else () for alias, keep in zip(aliases, distinct)],
        index=companies.index,
    )


def _company_index(
    external: pd.DataFrame,
    benchmark_aliases: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "external_company_id", "company_name", "company_key", "factset_portco_entity_id",
        "source_system", "source_confidence",
    ]
    if external.empty:
        return _empty([*columns, "alias_names"])
    out = external[columns].copy()
    out["_entity_rank"] = out["factset_portco_entity_id"].notna().astype(int)
    out = out.sort_values(
        ["external_company_id", "_entity_rank", "source_confidence"],
        ascending=[True, False, True],
    )
    out = out.drop_duplicates("external_company_id").drop(columns="_entity_rank")
    out["alias_names"] = _issuer_alias_names(out, benchmark_aliases)
    return out


def _hold_containment_issuer_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Keep prefix-containment issuer matches at review instead of auto.

    A containment match means one name is a token-prefix of the other, which for debt
    issuers routinely lands on a parent or affiliate (`Orange Switzerland` against
    `Orange SA`). Attaching the wrong issuer corrupts the metric comparison, so these
    are recorded as evidence but withheld from hydration until a human confirms them.
    """
    if matches.empty:
        return matches
    out = matches.copy()
    containment_only = (
        ~out["exact_match_flag"].astype(bool)
        & out["match_score"].sub(CONTAINMENT_MATCH_SCORE).abs().lt(1e-9)
    )
    out.loc[containment_only, "match_tier"] = TIER6_FUZZY_REVIEW
    out.loc[containment_only, "match_status"] = "needs_review"
    out.loc[containment_only, "match_method"] = "fuzzy_issuer_containment_needs_review"
    return out


def _map_fuzzy_debt_rows(
    company_index: pd.DataFrame,
    debt_features: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if company_index.empty or debt_features.empty:
        return debt_features.iloc[0:0].copy(), _empty([
            "external_company_id", "company_name", "matched_issuer_label", "match_score",
            "forward_score_margin", "reverse_score_margin", "match_tier", "match_status",
            "match_method",
        ])
    company_index = company_index.copy()
    if "alias_names" not in company_index:
        company_index["alias_names"] = [() for _ in range(len(company_index))]
    issuer_rows = debt_features.copy()
    issuer_rows["issuer_label"] = issuer_rows.apply(_issuer_label, axis=1)
    issuer_index = issuer_rows[["issuer_label"]].drop_duplicates()
    issuer_index = issuer_index[issuer_index["issuer_label"].ne("")].copy()
    issuer_index["issuer_id"] = issuer_index["issuer_label"].map(
        lambda value: stable_id("debt_issuer", _company_key(value))
    )
    normal_matches = _classify_edges(
        _pair_edges(
            company_index,
            issuer_index.rename(columns={"issuer_label": "company_name"}),
            left_id="external_company_id",
            right_id="issuer_id",
            left_alias_name="alias_names",
            block_by_token=True,
        ),
        issuer_index.assign(match_eligibility="accepted"),
        right_id="issuer_id",
    )
    normal_matches = _hold_containment_issuer_matches(normal_matches)

    capital_matches = pd.DataFrame()
    if "capital_stack_alias_names" in company_index:
        capital_rows = company_index[
            company_index["capital_stack_alias_names"].map(
                lambda value: isinstance(value, (tuple, list, set)) and bool(value)
            )
        ]
        if not capital_rows.empty:
            capital_matches = _classify_edges(
                _pair_edges(
                    capital_rows,
                    issuer_index.rename(columns={"issuer_label": "company_name"}),
                    left_id="external_company_id",
                    right_id="issuer_id",
                    left_alias_name="capital_stack_alias_names",
                    block_by_token=True,
                    score_func=score_capital_structure_name,
                ),
                issuer_index.assign(match_eligibility="accepted"),
                right_id="issuer_id",
            )
            if not capital_matches.empty:
                capital_matches["match_method"] = capital_matches["match_tier"].map({
                    TIER5_FUZZY_AUTO: "bloomberg_capital_stack_alias_fuzzy_auto",
                    TIER6_FUZZY_REVIEW: "bloomberg_capital_stack_alias_fuzzy_review",
                })

    normal_accepted_ids = set(
        normal_matches.loc[
            normal_matches["match_tier"].eq(TIER5_FUZZY_AUTO),
            "external_company_id",
        ].astype(str)
    ) if not normal_matches.empty else set()
    if not capital_matches.empty:
        capital_matches = capital_matches[
            ~capital_matches["external_company_id"].astype(str).isin(
                normal_accepted_ids
            )
        ]
    capital_accepted_ids = set(
        capital_matches.loc[
            capital_matches["match_tier"].eq(TIER5_FUZZY_AUTO),
            "external_company_id",
        ].astype(str)
    ) if not capital_matches.empty else set()
    if not normal_matches.empty and capital_accepted_ids:
        normal_matches = normal_matches[
            ~(
                normal_matches["external_company_id"].astype(str).isin(
                    capital_accepted_ids
                )
                & ~normal_matches["match_tier"].eq(TIER5_FUZZY_AUTO)
            )
        ]
    match_frames = [
        frame for frame in [normal_matches, capital_matches] if not frame.empty
    ]
    matches = (
        pd.concat(match_frames, ignore_index=True)
        if match_frames
        else pd.DataFrame()
    )
    if matches.empty:
        return debt_features.iloc[0:0].copy(), matches
    accepted = matches[matches["match_tier"].eq(TIER5_FUZZY_AUTO)].copy()
    issuer_name_map = dict(zip(issuer_index["issuer_id"], issuer_index["issuer_label"]))
    accepted["matched_issuer_label"] = accepted["issuer_id"].map(issuer_name_map)
    label_to_company = dict(
        zip(
            accepted["matched_issuer_label"].map(lambda value: _company_key(value)),
            accepted["external_company_id"],
        )
    )
    mapped = issuer_rows[
        issuer_rows["issuer_label"].map(lambda value: _company_key(value)).isin(label_to_company)
    ].copy()
    mapped["portco_id"] = mapped["issuer_label"].map(
        lambda value: label_to_company.get(_company_key(value))
    )
    evidence = matches.assign(
        matched_issuer_label=matches["issuer_id"].map(issuer_name_map)
    ).rename(columns={
        "left_name": "company_name",
        "forward_margin": "forward_score_margin",
        "reverse_margin": "reverse_score_margin",
    })[[
        "external_company_id", "company_name", "matched_left_name",
        "matched_issuer_label", "match_score",
        "forward_score_margin", "reverse_score_margin", "match_tier", "match_status",
        "match_method",
    ]]
    return mapped, evidence


def match_companies_to_debt_features(
    holdings: pd.DataFrame,
    debt_features: pd.DataFrame,
    *,
    holding_key_col: str = "external_holding_key",
    company_name_col: str = "company_name",
) -> pd.DataFrame:
    if holdings.empty or debt_features.empty:
        return _empty([
            holding_key_col, company_name_col, "debt_feature_key", "matched_issuer_label",
            "match_score", "score_margin", "match_tier", "match_status", "match_method",
        ])
    companies = holdings[[holding_key_col, company_name_col]].rename(columns={
        holding_key_col: "external_company_id",
        company_name_col: "company_name",
    })
    companies["company_key"] = companies["company_name"].map(_company_key)
    companies["factset_portco_entity_id"] = pd.NA
    companies["source_system"] = "unknown"
    companies["source_confidence"] = "medium"
    mapped, evidence = _map_fuzzy_debt_rows(companies, debt_features)
    if mapped.empty:
        return _empty([
            holding_key_col, company_name_col, "debt_feature_key", "matched_issuer_label",
            "match_score", "score_margin", "match_tier", "match_status", "match_method",
        ])
    mapped["debt_feature_key"] = mapped.apply(_debt_feature_key, axis=1)
    out = mapped[["portco_id", "debt_feature_key"]].drop_duplicates().merge(
        evidence,
        left_on="portco_id",
        right_on="external_company_id",
        how="inner",
    )
    return out.rename(columns={
        "portco_id": holding_key_col,
        "company_name": company_name_col,
        "forward_score_margin": "score_margin",
    })[[
        holding_key_col, company_name_col, "debt_feature_key", "matched_issuer_label",
        "match_score", "score_margin", "match_tier", "match_status", "match_method",
    ]]


def _representative_bucket(
    frame: pd.DataFrame,
    *,
    value_columns: list[str],
) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out["_completeness"] = out[value_columns].notna().sum(axis=1)
    if "tranche_size" not in out:
        out["tranche_size"] = pd.NA
    if "maturity_date" not in out:
        out["maturity_date"] = pd.NaT
    out = out.sort_values(
        ["portco_id", "tranche_type", "_completeness", "tranche_size", "maturity_date", "instrument_id_uniq"],
        ascending=[True, True, False, False, True, True],
        na_position="last",
    )
    return out.drop_duplicates(["portco_id", "tranche_type"], keep="first")


def hydrate_external_holdings_pit_metrics(
    external: pd.DataFrame,
    financial: pd.DataFrame,
    market: pd.DataFrame,
    instruments: pd.DataFrame,
) -> pd.DataFrame:
    out = external.copy()
    if not financial.empty:
        fin = financial.rename(columns={"portco_id": "external_company_id"}).copy()
        fin = fin.sort_values(["external_company_id", "asof_date"]).drop_duplicates(
            "external_company_id", keep="last"
        )
        fin["current_snapshot_date"] = fin["asof_date"]
        fin["current_financial_report_published_date"] = fin["asof_date"]
        columns = [
            "external_company_id", "current_total_debt", "current_total_cash", "current_net_debt",
            "current_ltm_revenue", "current_ltm_ebitda", "current_ebitda_margin",
            "current_gross_leverage", "current_net_leverage", "current_snapshot_date",
            "current_financial_report_published_date",
        ]
        out = out.merge(fin[[col for col in columns if col in fin]], on="external_company_id", how="left")

    debt = instruments.copy()
    if not market.empty:
        join_cols = ["portco_id", "instrument_id_uniq", "tranche_type"]
        if debt.empty:
            debt = market.copy()
        else:
            debt = debt.merge(
                market,
                on=join_cols,
                how="outer",
                suffixes=("", "_market"),
            )
    if debt.empty:
        return out
    for col in ["tranche_size", "rate", "maturity_date", "price", "ytw", "ytm"]:
        if col not in debt:
            debt[col] = pd.NA
    representative = _representative_bucket(
        debt,
        value_columns=["tranche_size", "rate", "maturity_date", "price", "ytw", "ytm"],
    )
    records = []
    for portco_id, group in representative.groupby("portco_id", dropna=False):
        record: dict[str, Any] = {"external_company_id": portco_id}
        for _, row in group.iterrows():
            if row["tranche_type"] == "1L":
                prefix = "current_first_lien"
            elif row["tranche_type"] == "2L / Unsecured":
                prefix = "current_2l_unsecured"
            else:
                continue
            record[f"{prefix}_tranche_size_usd"] = row.get("tranche_size")
            record[f"{prefix}_rate"] = row.get("rate")
            record[f"{prefix}_maturity_date"] = row.get("maturity_date")
            record[f"{prefix}_price"] = row.get("price")
            record[f"{prefix}_ytw"] = row.get("ytw")
            record[f"{prefix}_ytm"] = row.get("ytm")
        records.append(record)
    if records:
        out = out.merge(pd.DataFrame(records), on="external_company_id", how="left")
    asof_columns = [col for col in ["asof_date", "asof_date_market"] if col in debt]
    if asof_columns:
        debt_asof = debt[["portco_id", *asof_columns]].copy()
        debt_asof["_debt_snapshot_date"] = pd.concat(
            [pd.to_datetime(debt_asof[col], errors="coerce") for col in asof_columns],
            axis=1,
        ).max(axis=1)
        debt_asof = (
            debt_asof.groupby("portco_id", dropna=False)["_debt_snapshot_date"]
            .max()
            .reset_index()
            .rename(columns={"portco_id": "external_company_id"})
        )
        out = out.merge(debt_asof, on="external_company_id", how="left")
        if "current_snapshot_date" in out:
            out["current_snapshot_date"] = pd.concat(
                [
                    pd.to_datetime(out["current_snapshot_date"], errors="coerce"),
                    pd.to_datetime(out["_debt_snapshot_date"], errors="coerce"),
                ],
                axis=1,
            ).max(axis=1)
        else:
            out["current_snapshot_date"] = out["_debt_snapshot_date"]
        out = out.drop(columns="_debt_snapshot_date")
    return out


def _debt_outputs_from_features(
    external: pd.DataFrame,
    debt_features: pd.DataFrame,
    *,
    fuzzy_evidence: pd.DataFrame | None = None,
    dcs_detail: pd.DataFrame | None = None,
    findox_matches: pd.DataFrame | None = None,
    findox_lines: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    dcs = dcs_detail if dcs_detail is not None else _empty([])
    findox_fundamentals = findox_lines if findox_lines is not None else _empty([])
    if debt_features.empty and dcs.empty and findox_fundamentals.empty:
        return external, {
            "smart_debt_issuer_matches": fuzzy_evidence if fuzzy_evidence is not None else _empty([]),
            "smart_holding_debt_instrument_bridge": _empty([]),
            "smart_debt_feature_snapshot": _empty([]),
            "smart_financial_snapshot": _empty([]),
            "smart_market_snapshot": _empty([]),
            "smart_debt_instrument_snapshot": _empty([]),
            "smart_factset_dcs_detail": _empty([]),
        }
    features = debt_features.copy()
    if not features.empty:
        features.columns = [str(col).lower() for col in features.columns]
        features["debt_feature_key"] = features.apply(_debt_feature_key, axis=1)
    companies = _company_index(external)
    xref = companies.rename(columns={
        "external_company_id": "portco_id",
        "factset_portco_entity_id": "factset_entity_id",
    })
    xref["match_method"] = "smart_external_company"
    xref["match_confidence"] = "high"
    xref = attach_findox_issuer_ids(
        xref, findox_matches if findox_matches is not None else _empty([])
    )
    feature_snapshot = build_internal_debt_feature_snapshot(features, xref)
    financial = build_internal_financial_snapshot(features, findox_fundamentals, xref)
    market = build_internal_market_snapshot(features, pd.DataFrame(), xref, dcs_detail=dcs)
    instruments = _aggregate_dcs_tranches(
        build_internal_debt_instrument_snapshot(features, _dcs_term_facilities(dcs), xref)
    )
    hydrated = hydrate_external_holdings_pit_metrics(external, financial, market, instruments)
    if features.empty:
        return hydrated, {
            "smart_debt_issuer_matches": fuzzy_evidence if fuzzy_evidence is not None else _empty([]),
            "smart_holding_debt_instrument_bridge": _empty([]),
            "smart_debt_feature_snapshot": feature_snapshot,
            "smart_financial_snapshot": financial,
            "smart_market_snapshot": market,
            "smart_debt_instrument_snapshot": instruments,
            "smart_factset_dcs_detail": dcs,
        }
    company_bridge = features[["portco_id", "debt_feature_key"]].dropna().drop_duplicates()
    holding_bridge = external[["external_holding_key", "external_company_id"]].merge(
        company_bridge,
        left_on="external_company_id",
        right_on="portco_id",
        how="inner",
    ).drop(columns="portco_id")
    evidence = fuzzy_evidence if fuzzy_evidence is not None else _empty([])
    if not evidence.empty:
        holding_bridge = holding_bridge.merge(
            evidence[[
                "external_company_id", "matched_issuer_label", "match_tier",
                "match_status", "match_method",
            ]].drop_duplicates("external_company_id"),
            on="external_company_id",
            how="left",
        )
    return hydrated, {
        "smart_debt_issuer_matches": evidence,
        "smart_holding_debt_instrument_bridge": holding_bridge,
        "smart_debt_feature_snapshot": feature_snapshot,
        "smart_financial_snapshot": financial,
        "smart_market_snapshot": market,
        "smart_debt_instrument_snapshot": instruments,
        "smart_factset_dcs_detail": dcs,
    }


def enrich_external_holdings_from_frame(
    external: pd.DataFrame,
    debt_features: pd.DataFrame,
    *,
    benchmark_aliases: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    companies = _company_index(external, benchmark_aliases)
    mapped, evidence = _map_fuzzy_debt_rows(companies, debt_features)
    return _debt_outputs_from_features(external, mapped, fuzzy_evidence=evidence)


def _dcs_seniority(detail: pd.DataFrame) -> pd.Series:
    """Refine DCS's coarse seniority using the instrument description.

    `seniority_description` labels both first- and second-lien term loans `Senior Secured`,
    which drops second-lien debt into the 1L tranche and inflates it. The instrument
    description carries the lien level explicitly, so it takes precedence when present.
    """
    seniority = detail["seniority_description"].map(_text)
    description = detail.get(
        "instrument_description", pd.Series("", index=detail.index)
    ).map(_text)
    return seniority.mask(description.str.contains(DCS_SECOND_LIEN_RE), "Second Lien")


def _restate_dcs_amounts(
    detail: pd.DataFrame,
    *,
    app: str,
    factset_catalog: str,
    asof_cutoff: str | pd.Timestamp,
) -> pd.DataFrame:
    """Convert FactSet's normalized local-currency millions into USD millions.

    DCS details already report amounts in millions. The filing's unit_amount_code
    describes the original document and must not rescale these normalized fields.
    Preserve source values for audit and exclude currencies with no conversion rate.
    """
    if detail.empty:
        return detail
    out = detail.copy()
    rates = fetch_factset_fx_rates_usd(
        catalog=factset_catalog, app=app, asof_cutoff=asof_cutoff
    )
    rate_by_currency = {"USD": 1.0}
    if not rates.empty:
        rate_by_currency.update(dict(zip(
            rates["iso_currency"].map(lambda value: _text(value).upper()),
            pd.to_numeric(rates["exch_rate_usd"], errors="coerce"),
        )))
    out["fx_rate_usd"] = out["currency"].map(
        lambda value: rate_by_currency.get(_text(value).upper())
    ) if "currency" in out else 1.0
    for column in ["amount_outstanding", "available_amount", "borrowing_base"]:
        if column in out:
            reported = pd.to_numeric(out[column], errors="coerce")
            out[f"{column}_as_reported"] = reported
            out[column] = reported * out["fx_rate_usd"]
    return out[out["fx_rate_usd"].notna()].copy()


def _dcs_term_facilities(dcs_detail: pd.DataFrame) -> pd.DataFrame:
    """Drop revolving facilities so DCS tranche totals compare against v31 term-loan sizes.

    The v31 benchmark records a tranche as its term-loan facility size, whereas DCS
    itemizes revolvers, letters of credit and overdrafts alongside term debt. Their drawn
    balances fluctuate and are not part of the tranche being compared. The rows stay in the
    published DCS detail for audit; only the tranche rollup excludes them.
    """
    if dcs_detail.empty or "instrument_description" not in dcs_detail:
        return dcs_detail
    revolving = dcs_detail["instrument_description"].map(_text).str.contains(
        DCS_REVOLVING_FACILITY_RE
    )
    return dcs_detail[~revolving].copy()


def _aggregate_dcs_tranches(instruments: pd.DataFrame) -> pd.DataFrame:
    """Roll FactSet DCS instrument rows up to one row per company and tranche bucket.

    DCS itemizes every facility an issuer carries (revolvers, several term loans, small
    subsidised loans) while the v31 benchmark records one size per tranche, so picking a
    representative instrument understates the tranche. Negative amounts are contra items
    such as unamortized discount and issue costs and are excluded from the total. The
    maturity is taken from the largest facility in the bucket, because a summed tranche
    has no single maturity. Per-instrument detail stays available in the raw DCS output.
    """
    if instruments.empty or "source_system" not in instruments:
        return instruments
    is_dcs = instruments["source_system"].astype(str).eq("factset_dcs")
    if not is_dcs.any():
        return instruments
    others = instruments[~is_dcs]
    dcs = instruments[is_dcs].copy()
    dcs["tranche_size"] = pd.to_numeric(dcs["tranche_size"], errors="coerce")
    dcs = dcs[dcs["tranche_size"].notna() & dcs["tranche_size"].ge(0)]
    if dcs.empty:
        return others.reset_index(drop=True)
    keys = ["portco_id", "tranche_type"]
    dominant = (
        dcs.sort_values([*keys, "tranche_size"], ascending=[True, True, False])
        .drop_duplicates(keys, keep="first")
        .set_index(keys)
    )
    rolled = dcs.groupby(keys, dropna=False).agg(
        tranche_size=("tranche_size", "sum"),
        asof_date=("asof_date", "max"),
    )
    rolled["maturity_date"] = dominant["maturity_date"]
    rolled = rolled.reset_index()
    rolled["instrument_id_uniq"] = [
        f"dcs_tranche:{portco}:{tranche}"
        for portco, tranche in zip(rolled["portco_id"], rolled["tranche_type"])
    ]
    rolled["rate"] = pd.NA
    rolled["nearest_maturity_date"] = rolled["maturity_date"]
    rolled["nearest_maturity_type"] = rolled["tranche_type"]
    rolled["source_system"] = DCS_AGGREGATED_SOURCE
    rolled["bridge_provenance"] = "factset_dcs"
    from data_access.dbtables.sponsor_study.consolidated_group_debt import GROUP_DEBT_COLUMNS
    rolled = rolled.merge(dominant.reindex(columns=GROUP_DEBT_COLUMNS).reset_index(),
        on=keys, how="left", validate="one_to_one")
    return pd.concat(
        [others, rolled.reindex(columns=instruments.columns)], ignore_index=True
    )


def _dcs_entity_scope(
    companies: pd.DataFrame,
    *,
    app: str,
    factset_catalog: str,
) -> pd.DataFrame:
    """Propose own and related filers; dated attribution is checked after retrieval.

    A shared parent can supply explicitly labeled group debt to multiple companies.
    Current corporate-tree membership alone never authorizes that attribution.
    """
    own = (
        companies.assign(entity_id=companies["factset_portco_entity_id"].map(_text))
        [["external_company_id", "entity_id"]]
    )
    own = own[own["entity_id"].ne("")].drop_duplicates()
    if own.empty:
        return _empty(["factset_entity_id", "external_company_id", "entity_relationship"])
    relatives = fetch_factset_entity_relatives(
        sorted(own["entity_id"].unique()), catalog=factset_catalog, app=app
    )

    claims: list[pd.DataFrame] = [own.assign(entity_relationship="portco_entity")]
    if not relatives.empty:
        entity = relatives["factset_entity_id"].map(_text)
        for parent_column, descendant_label, ancestor_label in [
            ("factset_parent_entity_id", "subsidiary_of_portco", "parent_of_portco"),
            ("factset_ultimate_parent_entity_id", "under_portco_ultimate", "ultimate_of_portco"),
        ]:
            ancestor = relatives[parent_column].map(_text)
            relationships = pd.DataFrame({"entity_id": entity, "ancestor_id": ancestor})
            descendants = relationships.merge(own.rename(columns={"entity_id": "ancestor_id"}),
                on="ancestor_id").assign(entity_relationship=descendant_label).drop(columns="ancestor_id")
            ancestors = relationships.rename(columns={"entity_id": "own_id", "ancestor_id": "entity_id"}).merge(
                own.rename(columns={"entity_id": "own_id"}), on="own_id"
            ).assign(entity_relationship=ancestor_label).drop(columns="own_id")
            claims.extend([descendants, ancestors])

    scope = pd.concat(claims, ignore_index=True)
    scope = scope[scope["entity_id"].ne("") & scope["external_company_id"].notna()]
    scope = scope.drop_duplicates(["entity_id", "external_company_id", "entity_relationship"])

    scope = scope.sort_values(
        "entity_relationship", key=lambda col: col.ne("portco_entity")
    ).drop_duplicates(["entity_id", "external_company_id"], keep="first")
    return scope.rename(columns={"entity_id": "factset_entity_id"}).reset_index(drop=True)


def _single_dcs_filing_per_company(detail: pd.DataFrame) -> pd.DataFrame:
    """Keep one filing entity per company, since DCS filings are consolidated, not incremental.

    A parent's filing already contains the debt of the subsidiaries beneath it, so summing a
    company's own filing together with a relative's counts the same tranches twice. Related
    entities therefore serve as a fallback that reaches companies with no filing of their
    own, never as an addition: the company's own entity wins, then the most complete filing.
    """
    ranked = detail.assign(
        _own=detail["dcs_entity_relationship"].eq("portco_entity"),
        _size=pd.to_numeric(detail["amount_outstanding"], errors="coerce").fillna(0.0).abs(),
    )
    per_filer = ranked.groupby(["portco_id", "dcs_filer_entity_id"]).agg(
        own=("_own", "max"), instruments=("_size", "size"), total=("_size", "sum")
    ).reset_index()
    chosen = (
        per_filer.sort_values(
            ["portco_id", "own", "instruments", "total", "dcs_filer_entity_id"],
            ascending=[True, False, False, False, True],
        )
        .drop_duplicates("portco_id", keep="first")
        [["portco_id", "dcs_filer_entity_id"]]
    )
    return detail.merge(chosen, on=["portco_id", "dcs_filer_entity_id"], how="inner")


@checkpointed
def _fetch_dcs_capital_structure(
    companies: pd.DataFrame,
    *,
    app: str,
    factset_catalog: str,
    asof_cutoff: str | pd.Timestamp,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Pull FactSet DCS capital structure for each portfolio company's entity family.

    DCS is a separate source from `debt_features` and covers private issuers that have no
    traded instrument, so it reaches companies the issuer-name path cannot. Debt is
    routinely issued a level away from the portfolio company itself, at a finance
    subsidiary or a holding company, so related entities are included subject to the
    dated ownership and selected-filing guards. Unsupported candidates remain in the
    source evidence with a false selected-filing flag.
    """
    scope = _dcs_entity_scope(companies, app=app, factset_catalog=factset_catalog)
    if scope.empty:
        return _empty([])
    detail = fetch_factset_dcs_detail(
        sorted(scope["factset_entity_id"].unique()), catalog=factset_catalog, app=app,
        asof_cutoff=asof_cutoff,
    )
    if detail.empty:
        return detail
    filer = detail["factset_entity_id"].map(_text)
    detail["dcs_filer_entity_id"] = filer
    detail["factset_entity_id"] = filer
    detail = detail.drop(columns=["portco_id", "dcs_entity_relationship"], errors="ignore").merge(
        scope.rename(columns={"external_company_id": "portco_id", "entity_relationship": "dcs_entity_relationship"}),
        on="factset_entity_id", how="inner", validate="many_to_many")
    if detail.empty:
        return detail
    cutoff = pd.Timestamp(asof_cutoff)
    date_columns = [col for col in ["report_date", "publication_date"] if col in detail]
    for col in date_columns:
        detail[col] = pd.to_datetime(detail[col], errors="coerce")
    if date_columns:
        observed = detail[date_columns].max(axis=1)
        detail = detail[observed.notna() & observed.le(cutoff)].copy()
    if detail.empty:
        return detail
    from data_access.dbtables.sponsor_study.factset_filing_attribution import (
        ADMITTED_DCS_STATUSES, annotate_filing_attribution,
    )
    own = scope[scope.entity_relationship.eq("portco_entity")]
    detail = annotate_filing_attribution(detail, dict(zip(own.external_company_id, own.factset_entity_id)),
        app=app, factset_catalog=factset_catalog, asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids)
    supported = detail[detail.dcs_attribution_status.isin(ADMITTED_DCS_STATUSES)]
    chosen = _single_dcs_filing_per_company(supported)[["portco_id", "dcs_filer_entity_id"]].drop_duplicates()
    chosen["dcs_selected_filing_flag"] = True
    detail = detail.merge(chosen, on=["portco_id", "dcs_filer_entity_id"], how="left")
    detail["dcs_selected_filing_flag"] = detail.dcs_selected_filing_flag.astype("boolean").fillna(False)
    if "seniority_description" in detail:
        detail["seniority"] = _dcs_seniority(detail)
    detail = _restate_dcs_amounts(
        detail,
        app=app,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
    )
    return detail.reset_index(drop=True)


def _hold_bridge_containment_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Record prefix-containment bridge matches without letting them hydrate debt.

    The bridge resolves names permissively, so a token-prefix hit lands on a different
    company more often than not: `Foods Connected` reaches Kroger and `Max Stock` reaches
    Markel. This mirrors `_hold_containment_issuer_matches` for the fuzzy tier, keeping the
    candidate visible for review instead of attaching another company's instruments.
    """
    if matches.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "external_company_id": matches["external_company_id"],
        "company_name": matches["company_name"],
        "matched_issuer_label": matches["bbg_primary_name"],
        "match_score": matches["match_score"],
        "forward_score_margin": matches["match_score"],
        "reverse_score_margin": matches["match_score"],
        "match_tier": TIER6_FUZZY_REVIEW,
        "match_status": "needs_review",
        "match_method": "bloomberg_bridge_containment_needs_review",
    })


def _bloomberg_bridge_debt_rows(
    companies: pd.DataFrame,
    sponsor_names: frozenset[str],
    *,
    app: str,
    table_name: str,
    asof_cutoff: str | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reach debt filed under names the external universe never carries.

    Loan-only issuers are invisible to issuer-name scoring: Palex Medical's loans are filed
    under the acquisition vehicle `Pique Bidco SL`. Resolving the company name through
    Bloomberg's issuer master yields a corporate ticker, which `debt_features` populates on
    every loan row, unlike `id_bb_company` which is null on 90% of them.
    """
    if companies.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    debt_identifiers = fetch_debt_identifier_universe(
        app=app, table_name=table_name, asof_cutoff=asof_cutoff
    )
    universe = fetch_bloomberg_issuer_universe(debt_identifiers, app=app, asof_cutoff=asof_cutoff)
    name_index = build_bloomberg_name_index(universe)
    matches, audit = resolve_companies_to_bloomberg(
        companies, name_index, sponsor_names=sponsor_names
    )
    if matches.empty:
        return pd.DataFrame(), pd.DataFrame(), audit, pd.DataFrame()
    exact = matches[matches["exact_match_flag"].astype(bool)].copy()
    held = _hold_bridge_containment_matches(matches[~matches["exact_match_flag"].astype(bool)])
    capital_stack = fetch_bloomberg_capital_stack(exact, app=app, asof_cutoff=asof_cutoff)
    debt_bindings = (
        _capital_stack_identifier_bindings(capital_stack)
        if not capital_stack.empty
        else exact
    )
    features = fetch_debt_features_for_bloomberg_matches(
        debt_bindings, app=app, table_name=table_name, asof_cutoff=asof_cutoff
    )
    if features.empty:
        return pd.DataFrame(), held, audit, capital_stack
    reached = set(features["portco_id"].dropna().astype(str))
    evidence = exact[exact["external_company_id"].astype(str).isin(reached)][
        ["external_company_id", "company_name", "match_score"]
    ].copy()
    issuer_by_company = (
        features.assign(_issuer_label=features.apply(_issuer_label, axis=1))
        .groupby("portco_id", dropna=False)["_issuer_label"]
        .first()
        .to_dict()
    )
    evidence["matched_issuer_label"] = evidence["external_company_id"].map(issuer_by_company)
    evidence["forward_score_margin"] = evidence["match_score"]
    evidence["reverse_score_margin"] = evidence["match_score"]
    evidence["match_tier"] = BLOOMBERG_BRIDGE_TIER
    evidence["match_status"] = "accepted"
    evidence["match_method"] = BLOOMBERG_BRIDGE_METHOD
    frames = [frame for frame in [evidence, held] if not frame.empty]
    return features, pd.concat(frames, ignore_index=True), audit, capital_stack


def _capital_stack_identifier_bindings(stack: pd.DataFrame) -> pd.DataFrame:
    """Identifiers safe for deterministic hydration from a downward expansion.

    Bloomberg company and obligor ids identify the descendant node. Corporate tickers can
    span unrelated issuers, so only the original seed retains its ticker binding; descendant
    names reach ticker-only loan rows through the strict issuer-name matcher instead.
    """
    if stack.empty:
        return stack
    out = (
        stack[stack["stack_eligibility"].eq("admitted")].copy()
        if "stack_eligibility" in stack
        else stack.copy()
    )
    descendants = pd.to_numeric(
        out.get("path_depth", out["capital_stack_rank"]), errors="coerce"
    ).fillna(99).gt(0)
    if "company_corp_ticker" in out:
        out.loc[descendants, "company_corp_ticker"] = pd.NA
    if "obligor_bbid" in out and "obligor_inside_stack" in out:
        outside = out["obligor_inside_stack"].astype("boolean").fillna(False).eq(False)
        out.loc[outside, "obligor_bbid"] = pd.NA
        if "company_corp_ticker" in out:
            out.loc[outside, "company_corp_ticker"] = pd.NA
    return out


def _attach_capital_stack_aliases(
    companies: pd.DataFrame,
    stack: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    aliases = capital_stack_aliases(stack)
    if companies.empty or aliases.empty:
        return companies, aliases
    grouped = (
        aliases.groupby("external_company_id")["capital_stack_alias"]
        .apply(lambda values: tuple(dict.fromkeys(values.dropna().astype(str))))
        .to_dict()
    )
    out = companies.copy()
    out["capital_stack_alias_names"] = [
        grouped.get(str(company_id), ())
        for company_id in out["external_company_id"]
    ]
    return out, aliases


def fetch_and_enrich_external_holdings(
    external: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod",
    asof_cutoff: str | pd.Timestamp = STUDY_ASOF_DATE,
    benchmark_aliases: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.group_debt_sources import fetch_consolidated_group_debt
    from data_access.dbtables.sponsor_study.internal_joins import merge_debt_features_with_provenance
    from data_access.dbtables.sponsor_study.factset_debt_attribution import admitted_factset_debt_rows

    companies = _company_index(external, benchmark_aliases)
    xref = companies.rename(columns={
        "external_company_id": "portco_id",
        "factset_portco_entity_id": "factset_entity_id",
    })
    xref["match_method"] = "factset_pevc_fund_portco"
    xref["match_confidence"] = "high"
    id_xref = xref[xref["factset_entity_id"].notna()].copy()
    id_features = fetch_latest_debt_features_for_factset_portcos(
        id_xref,
        app=app,
        table_name=table_name,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
        sponsor_entity_ids=frozenset(external.reindex(
            columns=["factset_gp_entity_id", "factset_fund_entity_id"]).stack().astype(str)),
    )
    id_features = admitted_factset_debt_rows(id_features)
    matched_ids = set(id_features.get("portco_id", pd.Series(dtype="object")).dropna().astype(str))
    id_evidence = pd.DataFrame()
    if matched_ids:
        id_evidence = companies[
            companies["external_company_id"].astype(str).isin(matched_ids)
        ][["external_company_id", "company_name"]].copy()
        issuer_by_company = (
            id_features.assign(_issuer_label=id_features.apply(_issuer_label, axis=1))
            .groupby("portco_id", dropna=False)["_issuer_label"]
            .first()
            .to_dict()
        )
        id_evidence["matched_issuer_label"] = id_evidence["external_company_id"].map(
            issuer_by_company
        )
        id_evidence["match_score"] = 1.0
        id_evidence["forward_score_margin"] = 1.0
        id_evidence["reverse_score_margin"] = 1.0
        id_evidence["match_tier"] = "debt_tier1_factset_id"
        id_evidence["match_status"] = "accepted"
        id_evidence["match_method"] = "factset_entity_tree"
    bridge_features, bridge_evidence, bridge_audit, capital_stack = (
        _bloomberg_bridge_debt_rows(
        companies,
        frozenset(external.get("sponsor_name", pd.Series(dtype="object")).dropna().astype(str)),
        app=app,
        table_name=table_name,
        asof_cutoff=asof_cutoff,
        )
    )
    companies, stack_aliases = _attach_capital_stack_aliases(companies, capital_stack)
    bridged_ids = set(
        bridge_features.get("portco_id", pd.Series(dtype="object")).dropna().astype(str)
    )
    resolved_ids = matched_ids | bridged_ids
    unresolved = companies[
        ~companies["external_company_id"].astype(str).isin(resolved_ids)
    ].copy()
    fuzzy_features = pd.DataFrame()
    fuzzy_evidence = pd.DataFrame()
    if not unresolved.empty:
        issuer_index = fetch_debt_feature_issuer_index(
            app=app,
            table_name=table_name,
            asof_cutoff=asof_cutoff,
        )
    else:
        issuer_index = pd.DataFrame()
    if not issuer_index.empty:
        issuer_stub = issuer_index.rename(columns={"issuer_label": "issuer_name"})
        mapped_stub, fuzzy_evidence = _map_fuzzy_debt_rows(unresolved, issuer_stub)
        accepted_names = (
            fuzzy_evidence[fuzzy_evidence["match_status"].eq("accepted")]["matched_issuer_label"]
            .dropna().astype(str).tolist()
            if not fuzzy_evidence.empty
            else []
        )
        if accepted_names:
            fuzzy_features = fetch_latest_debt_features(
                accepted_names,
                app=app,
                table_name=table_name,
                asof_cutoff=asof_cutoff,
            )
            accepted_evidence = fuzzy_evidence[fuzzy_evidence["match_status"].eq("accepted")]
            name_map = dict(zip(
                accepted_evidence["matched_issuer_label"].map(_company_key),
                accepted_evidence["external_company_id"],
            ))
            fuzzy_features["portco_id"] = fuzzy_features.apply(
                lambda row: name_map.get(_company_key(_issuer_label(row))),
                axis=1,
            )
            fuzzy_features = fuzzy_features[fuzzy_features["portco_id"].notna()].copy()
    frames = [
        frame for frame in [id_features, bridge_features, fuzzy_features] if not frame.empty
    ]
    features = pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame()
    group_features = fetch_consolidated_group_debt(
        tuple(FactSetDebtSeed(str(owner), str(entity)) for owner, entity
            in id_xref[["portco_id", "factset_entity_id"]].dropna().drop_duplicates().itertuples(index=False, name=None)),
        sponsor_entity_ids=frozenset(external.reindex(
            columns=["factset_gp_entity_id", "factset_fund_entity_id"]).stack().astype(str)),
        app=app, factset_catalog=factset_catalog, table_name=table_name,
        asof_cutoff=asof_cutoff,
    )
    if not group_features.empty:
        if not features.empty and "debt_bridge_source_system" not in features:
            features["debt_bridge_source_system"] = "debt_features"
        features = merge_debt_features_with_provenance(features, pd.DataFrame(), group_features)
    evidence_frames = [
        frame for frame in [id_evidence, bridge_evidence, fuzzy_evidence] if not frame.empty
    ]
    evidence = (
        pd.concat(evidence_frames, ignore_index=True)
        if evidence_frames
        else pd.DataFrame()
    )
    dcs_detail = _fetch_dcs_capital_structure(
        companies,
        app=app,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
        sponsor_entity_ids=frozenset(external.reindex(
            columns=["factset_gp_entity_id", "factset_fund_entity_id"]).stack().astype(str)),
    )
    findox_matches, findox_audit, findox_lines = _resolve_findox_fundamentals(
        companies, app=app, asof_cutoff=asof_cutoff
    )
    enriched, outputs = _debt_outputs_from_features(
        external,
        features,
        fuzzy_evidence=evidence,
        dcs_detail=dcs_detail,
        findox_matches=findox_matches,
        findox_lines=findox_lines,
    )
    if not bridge_audit.empty:
        outputs["smart_bloomberg_issuer_bridge"] = bridge_audit
    if not capital_stack.empty:
        outputs["smart_bloomberg_capital_stack"] = capital_stack
        outputs["smart_bloomberg_capital_stack_aliases"] = stack_aliases
        binding_frame = capital_stack.copy()
        binding_frame["matched_bbg_name"] = binding_frame["capital_stack_name"]
        binding_frame["match_method"] = binding_frame["capital_stack_match_method"]
        binding_frame["resolution_source"] = pd.to_numeric(
            binding_frame["capital_stack_rank"], errors="coerce"
        ).map(
            lambda rank: (
                "bloomberg_issuer_master"
                if rank == 0
                else "bloomberg_capital_stack"
            )
        )
        outputs["smart_debt_identifier_crosswalk"] = build_debt_identifier_crosswalk(
            bindings_from_bloomberg_matches(binding_frame),
            resolved_asof_date=asof_cutoff,
        )
    if not findox_audit.empty:
        outputs["smart_findox_fundamental_bridge"] = findox_audit
    return enriched, outputs


def _resolve_findox_fundamentals(
    companies: pd.DataFrame, *, app: str, asof_cutoff: str | pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Resolve companies to Findox issuers and pull their structured fundamental lines.

    Containment matches are accepted, not just exact ones. Findox names a deal after the borrowing
    entity, so the study's name is routinely a token prefix of it (`TK Elevator` against
    `TK Elevator U.S. Newco, Inc.`), and holding those out discarded fundamentals for companies
    Findox had plainly modelled. Every resolution is written to the bridge audit with its score so
    a wrong leverage figure can be traced back to the name that produced it.
    """
    if companies.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    name_index = fetch_findox_name_index(app=app)
    matches, audit = resolve_companies_to_findox(companies, name_index)
    if matches.empty:
        return matches, audit, pd.DataFrame()
    lines = fetch_findox_fundamental_lines(
        matches["findox_issuer_id"].dropna().astype(str).tolist(),
        asof_cutoff=asof_cutoff,
        app=app,
    )
    return matches, audit, lines


def run_smart_comparison(
    dataset: dict[str, pd.DataFrame],
    external_holdings: pd.DataFrame | None = None,
    debt_features: pd.DataFrame | None = None,
    *,
    fetch_debt_features_live: bool = False,
    debt_features_app: str = "databricks",
    debt_features_table: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod",
    exclude_sponsors: tuple[str, ...] = (),
    include_sponsors: tuple[str, ...] = (),
) -> dict[str, pd.DataFrame]:
    baseline = build_benchmark_baseline(dataset)
    fund_scope = _smart_fund_scope(dataset)
    combined_external, external_exclusions = build_combined_external_universe(
        dataset,
        fund_scope,
        additional_external_holdings=external_holdings,
    )
    benchmark_holdings = filter_sponsor_rows(
        baseline["backtest_benchmark_holdings"], exclude_sponsors
    )
    ownership_events = filter_sponsor_rows(
        baseline["backtest_benchmark_ownership_events"], exclude_sponsors
    )
    fund_scope = filter_sponsor_rows(fund_scope, exclude_sponsors)
    combined_external = filter_sponsor_rows(combined_external, exclude_sponsors)
    external_exclusions = filter_sponsor_rows(external_exclusions, exclude_sponsors)
    benchmark_holdings = _filter_included_sponsors(benchmark_holdings, include_sponsors)
    fund_scope = _filter_included_sponsors(fund_scope, include_sponsors)
    included_sponsor_ids = set(benchmark_holdings["sponsor_id"].dropna().astype(str))
    ownership_events = _filter_included_sponsor_ids(
        ownership_events, included_sponsor_ids
    )
    combined_external = _filter_included_sponsor_ids(combined_external, included_sponsor_ids)
    external_exclusions = _filter_included_sponsor_ids(
        external_exclusions, included_sponsor_ids
    )
    exclusion_report = build_sponsor_exclusion_report(dataset, exclude_sponsors)

    # The spine is built before debt enrichment so that the v31 name of each matched
    # holding can join the issuer name pool; debt is frequently filed under a name the
    # external universe never carries.
    match_spine, external = build_smart_match_spine(
        dataset,
        benchmark_holdings,
        combined_external,
        fund_scope=fund_scope,
    )
    benchmark_aliases = _benchmark_alias_names(match_spine, external)

    debt_outputs: dict[str, pd.DataFrame] = {}
    if debt_features is not None:
        external, debt_outputs = enrich_external_holdings_from_frame(
            external, debt_features, benchmark_aliases=benchmark_aliases
        )
    elif fetch_debt_features_live:
        external, debt_outputs = fetch_and_enrich_external_holdings(
            external,
            app=debt_features_app,
            table_name=debt_features_table,
            factset_catalog=factset_catalog,
            asof_cutoff=STUDY_ASOF_DATE,
            benchmark_aliases=benchmark_aliases,
        )
    if not external.empty:
        assert_no_lookahead(external, pd.Timestamp(STUDY_ASOF_DATE))

    issuer_master_source = _empty(["external_company_id"])
    issuer_master_audit = _empty(["external_company_id"])
    if fetch_debt_features_live:
        issuer_master_source, issuer_master_audit = resolve_unmatched_to_issuer_master(
            match_spine,
            app=debt_features_app,
            table_name=debt_features_table,
            asof_cutoff=STUDY_ASOF_DATE,
        )
        match_spine = apply_issuer_master_source(match_spine, issuer_master_source)
        match_spine = apply_manual_issuer_matches(
            match_spine,
            dataset.get("xref_manual_issuer_match", pd.DataFrame()),
        )

    validate_smart_match_spine(match_spine)
    replication, miss_reasons = measure_smart_replication(match_spine)
    metric_detail_accepted, metric_summary_accepted = compare_pit_metrics(
        dataset,
        match_spine,
        external,
        scope=SMART_ACCEPTED_SCOPE,
        external_cutoff=pd.Timestamp(STUDY_ASOF_DATE),
    )
    metric_detail_inclusive, metric_summary_inclusive = compare_pit_metrics(
        dataset,
        match_spine,
        external,
        scope=SMART_INCLUSIVE_SCOPE,
        external_cutoff=pd.Timestamp(STUDY_ASOF_DATE),
    )
    totals = baseline["backtest_benchmark_totals"].copy()
    totals.loc[len(totals)] = {
        "metric": "benchmark_holding_count_after_exclusions",
        "actual": len(benchmark_holdings),
        "target": None,
        "difference": None,
        "status": "reference_only",
    }
    issuer_crosswalk = pd.concat(
        [
            issuer_master_crosswalk(issuer_master_source),
            manual_issuer_crosswalk(
                dataset.get("xref_manual_issuer_match", pd.DataFrame())
            ),
        ],
        ignore_index=True,
    ).drop_duplicates("debt_identifier_id")
    return {
        **baseline,
        "backtest_benchmark_holdings": benchmark_holdings,
        "backtest_benchmark_ownership_events": ownership_events,
        "backtest_benchmark_totals": totals,
        "backtest_fund_scope": fund_scope,
        "backtest_external_holdings": external,
        "backtest_external_exclusions": external_exclusions,
        "backtest_match_spine": match_spine,
        "backtest_holding_replication": replication,
        "backtest_miss_reasons": miss_reasons,
        "backtest_source_attribution": _source_attribution(match_spine),
        "backtest_metric_detail_accepted": metric_detail_accepted,
        "backtest_metric_summary_accepted": metric_summary_accepted,
        "backtest_metric_detail_inclusive": metric_detail_inclusive,
        "backtest_metric_summary_inclusive": metric_summary_inclusive,
        "backtest_sponsor_exclusions": exclusion_report,
        "smart_issuer_master_benchmark_source": issuer_master_source,
        "smart_issuer_master_benchmark_audit": issuer_master_audit,
        "smart_issuer_master_crosswalk": issuer_crosswalk,
        **debt_outputs,
        "smart_comparison_config": pd.DataFrame([
            {"setting": "study_cutoff", "value": STUDY_ASOF_DATE},
            {"setting": "auto_match_min_score", "value": AUTO_MATCH_MIN_SCORE},
            {"setting": "review_match_min_score", "value": REVIEW_MATCH_MIN_SCORE},
            {"setting": "token_overlap_auto_min", "value": TOKEN_OVERLAP_AUTO_MIN},
            {"setting": "ambiguity_margin", "value": AMBIGUITY_MARGIN},
            {"setting": "containment_match_score", "value": CONTAINMENT_MATCH_SCORE},
            {"setting": "recall_denominator", "value": "all_v31_benchmark_holdings"},
            {
                "setting": "external_universe",
                "value": "factset_pevc|sponsor_website|pitchbook",
            },
            {
                "setting": "external_source_priority",
                "value": "factset_pevc>sponsor_website>pitchbook",
            },
            {
                "setting": "issuer_master_benchmark_source",
                "value": "exact_name_resolutions_count_toward_recall",
            },
            {"setting": "website_snapshot_policy", "value": "latest_available_run_per_sponsor"},
            {"setting": "website_fund_confidence_gate", "value": "disabled"},
            {
                "setting": "company_match_scope",
                "value": "fund_then_sponsor_then_global_company_existence",
            },
            {"setting": "transaction_fund_scope_enforced", "value": False},
            {"setting": "former_name_alias_matching", "value": "both_sides"},
            {"setting": "website_dedupe_scope", "value": "sponsor"},
            {"setting": "dcs_tranche_aggregation", "value": "sum_by_seniority_excl_negative"},
            {"setting": "dcs_tranche_facilities", "value": "term_only_excl_revolving"},
            {"setting": "dcs_unit_scale_basis", "value": "source_local_millions_converted_to_usd"},
            {"setting": "dcs_entity_scope", "value": "portco_plus_single_owner_relatives"},
            {"setting": "dcs_filing_selection", "value": "one_entity_per_company_own_first"},
            {"setting": "dcs_currency_basis", "value": "usd_at_cutoff_factset_econ_fx"},
            {"setting": "debt_issuer_name_pool", "value": "external_plus_matched_v31_alias"},
            {"setting": "debt_bloomberg_bridge", "value": "v_bond_issuer_master_name_to_bbg_ids"},
            {
                "setting": "bloomberg_capital_stack_expansion",
                "value": "seed_containing_corporate_paths_obligor_gated_separately",
            },
            {
                "setting": "bloomberg_capital_stack_max_descendants",
                "value": MAX_CAPITAL_STACK_DESCENDANTS,
            },
            {
                "setting": "bloomberg_capital_alias_matcher",
                "value": "strict_structural_name_key_then_fuzzy",
            },
            {
                "setting": "bloomberg_descendant_ticker_hydration",
                "value": "disabled_shared_ticker_guard",
            },
            {"setting": "debt_bloomberg_bridge_min_score", "value": REVIEW_MATCH_MIN_SCORE},
            {"setting": "debt_bloomberg_bridge_selection", "value": "single_best_company_per_portco"},
        ]),
    }
