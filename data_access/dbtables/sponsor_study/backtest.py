"""PE sponsor replication backtest: benchmark freeze, match spine, holdings, metrics, scale."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.company_aliases import (
    ACCEPTED_COMPANY_ALIAS_STATUSES,
    _company_key,
)
from data_access.dbtables.sponsor_study.fund_vendor import REVIEWED_FUND_ALIAS_STATUSES
from data_access.dbtables.sponsor_study.validation import STUDY_TARGETS, build_reconciliation
from data_importer.sponsor_study.constants import STUDY_ASOF_DATE


ACCEPTED_FUND_STATUSES = {"auto_matched", "manual_matched"}
INCLUSIVE_FUND_STATUSES = {"auto_matched", "manual_matched", "needs_review"}

TIER1_ID = "tier1_id"
TIER2_ALIAS = "tier2_alias"
TIER3_EXACT_NAME = "tier3_exact_name"
TIER4_NEEDS_REVIEW = "tier4_needs_review"
TIER_AMBIGUOUS = "ambiguous"
TIER_UNMATCHED_BENCHMARK = "unmatched_benchmark"
TIER_EXTERNAL_ONLY = "external_only"

ACCEPTED_MATCH_TIERS = {TIER1_ID, TIER2_ALIAS, TIER3_EXACT_NAME}
INCLUSIVE_MATCH_TIERS = ACCEPTED_MATCH_TIERS | {TIER4_NEEDS_REVIEW}

MATCH_TIER_RANK = {
    TIER1_ID: 1,
    TIER2_ALIAS: 2,
    TIER3_EXACT_NAME: 3,
    TIER4_NEEDS_REVIEW: 4,
    TIER_AMBIGUOUS: 5,
    TIER_UNMATCHED_BENCHMARK: 6,
    TIER_EXTERNAL_ONLY: 7,
}

MISS_REASONS = (
    "matched",
    "fund_mismatch",
    "missing_factset_company_id",
    "company_name_mismatch",
    "transaction_fund_assignment_mismatch",
    "ambiguous_mapping",
    "true_source_absence",
    "external_only",
)

FINANCIAL_METRIC_SPECS: list[tuple[str, str, str]] = [
    ("current_total_debt", "current_total_debt", "usd_m"),
    ("current_total_cash", "current_total_cash", "usd_m"),
    ("current_net_debt", "current_net_debt", "usd_m"),
    ("current_ltm_revenue", "current_ltm_revenue", "usd_m"),
    ("current_ltm_ebitda", "current_ltm_ebitda", "usd_m"),
    ("current_ebitda_margin", "current_ebitda_margin", "ratio"),
    ("current_gross_leverage", "current_gross_leverage", "multiple"),
    ("current_net_leverage", "current_net_leverage", "multiple"),
]

DEBT_METRIC_SPECS: list[tuple[str, str, str, str]] = [
    ("first_lien", "tranche_size", "first_lien_tranche_size", "usd_m"),
    ("first_lien", "price", "first_lien_price", "price"),
    ("first_lien", "rate", "first_lien_rate", "ratio"),
    ("first_lien", "ytw", "first_lien_ytw", "ratio"),
    ("first_lien", "ytm", "first_lien_ytm", "ratio"),
    ("first_lien", "maturity_date", "first_lien_maturity", "date"),
    ("second_lien_unsecured", "tranche_size", "second_lien_unsecured_tranche_size", "usd_m"),
    ("second_lien_unsecured", "price", "second_lien_unsecured_price", "price"),
    ("second_lien_unsecured", "rate", "second_lien_unsecured_rate", "ratio"),
    ("second_lien_unsecured", "ytw", "second_lien_unsecured_ytw", "ratio"),
    ("second_lien_unsecured", "ytm", "second_lien_unsecured_ytm", "ratio"),
    ("second_lien_unsecured", "maturity_date", "second_lien_unsecured_maturity", "date"),
]

DEFAULT_METRIC_TOLERANCES: dict[str, dict[str, float | None]] = {
    "usd_m": {"pct": 0.10, "abs": 5.0},
    "ratio": {"pct": 0.15, "abs": 0.02},
    "multiple": {"pct": 0.10, "abs": 0.25},
    "price": {"pct": 0.05, "abs": 2.0},
    "date": {"pct": None, "abs": 30.0},
}


@dataclass(frozen=True)
class BacktestScope:
    label: str
    fund_statuses: frozenset[str]
    match_tiers: frozenset[str]


ACCEPTED_SCOPE = BacktestScope("accepted", frozenset(ACCEPTED_FUND_STATUSES), frozenset(ACCEPTED_MATCH_TIERS))
INCLUSIVE_SCOPE = BacktestScope("inclusive", frozenset(INCLUSIVE_FUND_STATUSES), frozenset(INCLUSIVE_MATCH_TIERS))

DEFAULT_EXCLUDED_SPONSOR_TOKENS = ("apollo",)


def normalize_sponsor_exclusion_token(value: Any) -> str:
    return slug(value).replace("_", " ").strip()


def sponsor_matches_exclusion(sponsor_name: Any, exclusions: tuple[str, ...]) -> bool:
    if not exclusions or pd.isna(sponsor_name):
        return False
    name_key = normalize_sponsor_exclusion_token(sponsor_name)
    if not name_key:
        return False
    name_tokens = set(name_key.split())
    for token in exclusions:
        token_key = normalize_sponsor_exclusion_token(token)
        if not token_key:
            continue
        if token_key in name_tokens or token_key in name_key:
            return True
    return False


def build_sponsor_exclusion_report(
    dataset: dict[str, pd.DataFrame],
    exclusions: tuple[str, ...],
) -> pd.DataFrame:
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    holdings = build_benchmark_holdings(dataset)
    if not exclusions:
        return pd.DataFrame(columns=["sponsor_id", "sponsor_name", "excluded", "benchmark_holding_count"])
    if sponsors.empty:
        return pd.DataFrame(columns=["sponsor_id", "sponsor_name", "excluded", "benchmark_holding_count"])
    counts = (
        holdings.groupby(["sponsor_id", "sponsor_name"], dropna=False)
        .size()
        .reset_index(name="benchmark_holding_count")
    )
    out = sponsors[["sponsor_id", "sponsor_name"]].merge(counts, on=["sponsor_id", "sponsor_name"], how="left")
    out["benchmark_holding_count"] = out["benchmark_holding_count"].fillna(0).astype(int)
    out["excluded"] = out["sponsor_name"].map(lambda name: sponsor_matches_exclusion(name, exclusions))
    out["exclusion_token"] = ", ".join(exclusions)
    return out.sort_values(["excluded", "benchmark_holding_count"], ascending=[False, False]).reset_index(drop=True)


def filter_sponsor_rows(
    df: pd.DataFrame,
    exclusions: tuple[str, ...],
    *,
    sponsor_name_col: str = "sponsor_name",
) -> pd.DataFrame:
    if df.empty or not exclusions or sponsor_name_col not in df.columns:
        return df.copy()
    keep = ~df[sponsor_name_col].map(lambda name: sponsor_matches_exclusion(name, exclusions))
    return df.loc[keep].reset_index(drop=True)


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _clean_id(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _study_cutoff(dataset: dict[str, pd.DataFrame]) -> pd.Timestamp:
    stage = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame())
    if not stage.empty and "study_asof_date" in stage:
        value = stage["study_asof_date"].dropna().iloc[0]
        return pd.Timestamp(value)
    return pd.Timestamp(STUDY_ASOF_DATE)


def build_benchmark_baseline(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Freeze template benchmark holdings and reconcile sponsor/fund/ownership/company totals."""
    study_cutoff = _study_cutoff(dataset)
    holdings = build_benchmark_holdings(dataset)
    ownership_events = build_benchmark_ownership_events(dataset)
    reconciliation = build_reconciliation(dataset)
    totals = pd.DataFrame([
        {"metric": "study_asof_date", "actual": str(study_cutoff.date()), "target": STUDY_ASOF_DATE,
         "difference": None, "status": "match" if str(study_cutoff.date()) == STUDY_ASOF_DATE else "review"},
        {"metric": "sponsor_count", "actual": int(dataset.get("dim_sponsor", pd.DataFrame())["sponsor_id"].nunique()),
         "target": None, "difference": None, "status": "reference_only"},
        {"metric": "fund_count", "actual": int(dataset.get("dim_fund", pd.DataFrame())["fund_id"].nunique()),
         "target": None, "difference": None, "status": "reference_only"},
        {"metric": "ownership_event_count", "actual": len(ownership_events),
         "target": None, "difference": None, "status": "reference_only"},
        {"metric": "holding_grain_count", "actual": len(holdings),
         "target": None, "difference": None, "status": "reference_only"},
        {"metric": "portco_count", "actual": int(holdings["portco_id"].nunique()) if not holdings.empty else 0,
         "target": STUDY_TARGETS.get("portfolio_companies_total"),
         "difference": None if not STUDY_TARGETS.get("portfolio_companies_total") else
         (int(holdings["portco_id"].nunique()) - STUDY_TARGETS["portfolio_companies_total"]),
         "status": "reference_only"},
    ])
    return {
        "backtest_study_cutoff": pd.DataFrame([{"study_asof_date": study_cutoff}]),
        "backtest_benchmark_holdings": holdings,
        "backtest_benchmark_ownership_events": ownership_events,
        "backtest_benchmark_reconciliation": reconciliation,
        "backtest_benchmark_totals": totals,
    }


def build_benchmark_holdings(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    funds = dataset.get("dim_fund", pd.DataFrame())
    portcos = dataset.get("dim_portco", pd.DataFrame())
    if bridge.empty or sponsors.empty or funds.empty or portcos.empty:
        return _empty([
            "holding_key", "sponsor_id", "sponsor_name", "fund_id", "fund_name", "vintage",
            "portco_id", "company_name", "company_key", "ownership_count", "ownership_ids",
            "current_status", "active_flag",
        ])

    base = bridge.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    base = base.merge(funds[["fund_id", "fund_name", "vintage"]], on="fund_id", how="left")
    base = base.merge(portcos[["portco_id", "company_name"]], on="portco_id", how="left")
    base["company_key"] = base["company_name"].map(_company_key)
    base["holding_key"] = base.apply(
        lambda row: stable_id("hold", row["sponsor_id"], row["fund_id"], row["portco_id"]),
        axis=1,
    )
    grouped = (
        base.groupby(
            ["holding_key", "sponsor_id", "sponsor_name", "fund_id", "fund_name", "vintage",
             "portco_id", "company_name", "company_key"],
            dropna=False,
            as_index=False,
        )
        .agg(
            ownership_count=("ownership_id", "nunique"),
            ownership_ids=("ownership_id", lambda s: sorted({str(v) for v in s.dropna()})),
            current_status=("current_status", "first"),
            active_flag=("active_flag", "max"),
        )
    )
    return grouped.reset_index(drop=True)


def build_benchmark_ownership_events(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    funds = dataset.get("dim_fund", pd.DataFrame())
    portcos = dataset.get("dim_portco", pd.DataFrame())
    if bridge.empty:
        return _empty([
            "ownership_id", "holding_key", "sponsor_id", "fund_id", "portco_id",
            "company_name", "investment_date", "current_status",
        ])
    out = bridge.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    out = out.merge(funds[["fund_id", "fund_name", "vintage"]], on="fund_id", how="left")
    out = out.merge(portcos[["portco_id", "company_name"]], on="portco_id", how="left")
    out["holding_key"] = out.apply(
        lambda row: stable_id("hold", row["sponsor_id"], row["fund_id"], row["portco_id"]),
        axis=1,
    )
    cols = [
        "ownership_id", "holding_key", "sponsor_id", "sponsor_name", "fund_id", "fund_name",
        "vintage", "portco_id", "company_name", "investment_date", "current_status", "active_flag",
    ]
    return out[cols].drop_duplicates("ownership_id").reset_index(drop=True)


def build_fund_scope(dataset: dict[str, pd.DataFrame], *, inclusive: bool = True) -> pd.DataFrame:
    funds = dataset.get("dim_fund", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    xref = dataset.get("xref_fund_alias_name_map", pd.DataFrame())
    if funds.empty:
        return _empty([
            "fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage",
            "source_system", "source_fund_id", "fund_match_status", "fund_scope_status",
        ])

    seed = funds.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    allowed = INCLUSIVE_FUND_STATUSES if inclusive else ACCEPTED_FUND_STATUSES
    if xref.empty:
        seed["source_system"] = pd.NA
        seed["source_fund_id"] = pd.NA
        seed["fund_match_status"] = "unmatched"
        seed["fund_scope_status"] = "benchmark_only"
        return seed[[
            "fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage",
            "source_system", "source_fund_id", "fund_match_status", "fund_scope_status",
        ]]

    reviewed = xref[
        xref["review_status"].isin(REVIEWED_FUND_ALIAS_STATUSES)
        & xref["source_entity_id"].notna()
    ].copy()
    reviewed["source_fund_id"] = reviewed["source_entity_id"].map(_clean_id)
    reviewed = reviewed[reviewed["source_fund_id"].ne("")]
    reviewed = reviewed.sort_values(
        ["fund_id", "match_score", "fund_name_similarity"],
        ascending=[True, False, False],
        na_position="last",
    )
    best = reviewed.drop_duplicates(["fund_id", "source_system"], keep="first")
    best = best.rename(columns={"review_status": "fund_match_status"})
    best["fund_scope_status"] = np.where(
        best["fund_match_status"].isin(allowed),
        "in_scope",
        "out_of_scope",
    )
    out = seed.merge(
        best[[
            "fund_id", "source_system", "source_fund_id", "fund_match_status", "fund_scope_status",
        ]],
        on="fund_id",
        how="left",
    )
    out["fund_match_status"] = out["fund_match_status"].fillna("unmatched")
    out["fund_scope_status"] = out["fund_scope_status"].fillna("benchmark_only")
    return out.reset_index(drop=True)


def _normalize_external_holdings(external: pd.DataFrame) -> pd.DataFrame:
    out = external.copy()
    if "company_key" not in out.columns and "company_name" in out.columns:
        out["company_key"] = out["company_name"].map(_company_key)
    if "external_holding_key" not in out.columns:
        out["external_holding_key"] = out.apply(
            lambda row: stable_id(
                "ext_hold",
                row.get("fund_id"),
                _clean_id(row.get("factset_portco_entity_id")) or row.get("company_name"),
            ),
            axis=1,
        )
    return out


def build_external_holdings_from_dataset(
    dataset: dict[str, pd.DataFrame],
    fund_scope: pd.DataFrame | None = None,
) -> pd.DataFrame:
    structured = dataset.get("structured_fund_portco_investment", pd.DataFrame())
    scope = fund_scope if fund_scope is not None else build_fund_scope(dataset, inclusive=True)
    in_scope_funds = set(scope.loc[scope["fund_scope_status"].eq("in_scope"), "fund_id"])
    if structured.empty or not in_scope_funds:
        return _empty([
            "external_holding_key", "fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage",
            "factset_portco_entity_id", "company_name", "company_key", "benchmark_portco_id",
            "investment_date", "source_system", "match_method", "source_confidence",
        ])

    rows = structured[structured["fund_id"].isin(in_scope_funds)].copy()
    rows["factset_portco_entity_id"] = rows["factset_portco_entity_id"].map(_clean_id)
    rows["company_key"] = rows["company_name"].map(_company_key)
    rows["external_holding_key"] = rows.apply(
        lambda row: stable_id(
            "ext_hold",
            row.get("fund_id"),
            row.get("factset_portco_entity_id") or row.get("company_name"),
        ),
        axis=1,
    )
    cols = [
        "external_holding_key", "fund_id", "sponsor_id", "sponsor_name", "fund_name", "vintage",
        "factset_portco_entity_id", "company_name", "company_key", "benchmark_portco_id",
        "investment_date", "source_system", "match_method", "source_confidence",
        "structured_investment_id", "transaction_fund_entity_id", "transaction_fund_name",
    ]
    return rows[cols].drop_duplicates("external_holding_key").reset_index(drop=True)


def _fund_factset_map(scope: pd.DataFrame) -> dict[str, set[str]]:
    scoped = scope[scope["fund_scope_status"].eq("in_scope") & scope["source_fund_id"].notna()].copy()
    out: dict[str, set[str]] = {}
    for _, row in scoped.iterrows():
        out.setdefault(str(row["fund_id"]), set()).add(_clean_id(row["source_fund_id"]))
    return out


def _portco_id_map(dataset: dict[str, pd.DataFrame]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    structured = dataset.get("structured_fund_portco_investment", pd.DataFrame())
    if not structured.empty:
        linked = structured.dropna(subset=["benchmark_portco_id", "factset_portco_entity_id"])
        for _, row in linked.iterrows():
            mapping[_clean_id(row["factset_portco_entity_id"])] = str(row["benchmark_portco_id"])
    xref = dataset.get("xref_portco_internal_ids", pd.DataFrame())
    if not xref.empty and "factset_entity_id" in xref:
        for _, row in xref.dropna(subset=["portco_id", "factset_entity_id"]).iterrows():
            mapping.setdefault(_clean_id(row["factset_entity_id"]), str(row["portco_id"]))
    aliases = dataset.get("xref_company_alias_name_map", pd.DataFrame())
    if not aliases.empty:
        accepted = aliases[
            aliases["source_system"].eq("factset_pevc")
            & aliases["review_status"].isin(ACCEPTED_COMPANY_ALIAS_STATUSES)
            & aliases["source_entity_id"].notna()
        ]
        for _, row in accepted.iterrows():
            mapping.setdefault(_clean_id(row["source_entity_id"]), str(row["portco_id"]))
    return mapping


def _company_alias_lookup(dataset: dict[str, pd.DataFrame]) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    accepted: dict[tuple[str, str], str] = {}
    review: dict[tuple[str, str], str] = {}
    aliases = dataset.get("xref_company_alias_name_map", pd.DataFrame())
    if aliases.empty:
        return accepted, review
    pevc = aliases[aliases["source_system"].eq("factset_pevc") & aliases["source_entity_id"].notna()].copy()
    if "fund_id" not in pevc.columns:
        bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
        if not bridge.empty and "ownership_id" in pevc:
            pevc = pevc.merge(
                bridge[["ownership_id", "fund_id"]].drop_duplicates(),
                on="ownership_id",
                how="left",
            )
    for _, row in pevc.iterrows():
        if pd.isna(row.get("fund_id")):
            continue
        key = (str(row["fund_id"]), str(row["portco_id"]))
        entity = _clean_id(row["source_entity_id"])
        if row["review_status"] in ACCEPTED_COMPANY_ALIAS_STATUSES:
            accepted[key] = entity
        elif row["review_status"] == "needs_review":
            review[key] = entity
    return accepted, review


def _exact_name_lookup(external: pd.DataFrame) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for _, row in external.iterrows():
        key = (str(row["fund_id"]), str(row.get("company_key") or ""))
        entity = _clean_id(row.get("factset_portco_entity_id"))
        if key[1] and entity:
            out.setdefault(key, entity)
    return out


def build_match_spine(
    dataset: dict[str, pd.DataFrame],
    benchmark_holdings: pd.DataFrame | None = None,
    external_holdings: pd.DataFrame | None = None,
    *,
    fund_scope: pd.DataFrame | None = None,
    enforce_transaction_fund_scope: bool = True,
) -> pd.DataFrame:
    """Tiered sponsor/fund/company mapping with auditable match status and miss reasons.

    Set `enforce_transaction_fund_scope=False` to keep a company match when the external
    source books the deal to a different fund of the same sponsor than the benchmark does.
    """
    benchmark = benchmark_holdings if benchmark_holdings is not None else build_benchmark_holdings(dataset)
    scope = fund_scope if fund_scope is not None else build_fund_scope(dataset, inclusive=True)
    external = (
        _normalize_external_holdings(external_holdings)
        if external_holdings is not None
        else build_external_holdings_from_dataset(dataset, scope)
    )
    portco_map = _portco_id_map(dataset)
    alias_accepted, alias_review = _company_alias_lookup(dataset)
    exact_names = _exact_name_lookup(external)

    external_by_fund_entity: dict[tuple[str, str], pd.Series] = {}
    external_by_fund_name: dict[tuple[str, str], pd.Series] = {}
    for _, row in external.iterrows():
        fund_id = str(row["fund_id"])
        entity = _clean_id(row.get("factset_portco_entity_id"))
        if entity:
            external_by_fund_entity[(fund_id, entity)] = row
        name_key = (fund_id, str(row.get("company_key") or ""))
        if name_key[1]:
            external_by_fund_name[name_key] = row

    records: list[dict[str, Any]] = []
    matched_external_keys: set[str] = set()

    def _append_match(
        bench_row: pd.Series,
        ext_row: pd.Series | None,
        *,
        match_tier: str,
        match_status: str,
        miss_reason: str,
        match_method: str,
    ) -> None:
        ext_key = None if ext_row is None else str(ext_row["external_holding_key"])
        if ext_key:
            matched_external_keys.add(ext_key)
        records.append({
            "holding_key": bench_row["holding_key"],
            "benchmark_side": "benchmark",
            "external_holding_key": ext_key,
            "sponsor_id": bench_row["sponsor_id"],
            "sponsor_name": bench_row["sponsor_name"],
            "fund_id": bench_row["fund_id"],
            "fund_name": bench_row["fund_name"],
            "vintage": bench_row["vintage"],
            "portco_id": bench_row["portco_id"],
            "company_name": bench_row["company_name"],
            "company_key": bench_row["company_key"],
            "factset_portco_entity_id": None if ext_row is None else _clean_id(ext_row.get("factset_portco_entity_id")),
            "external_company_name": None if ext_row is None else ext_row.get("company_name"),
            "match_tier": match_tier,
            "match_status": match_status,
            "miss_reason": miss_reason,
            "match_method": match_method,
            "fund_scope_status": scope.set_index("fund_id")["fund_scope_status"].get(bench_row["fund_id"], "benchmark_only"),
            "fund_match_status": scope.set_index("fund_id")["fund_match_status"].get(bench_row["fund_id"], "unmatched"),
        })

    for _, bench in benchmark.iterrows():
        fund_id = str(bench["fund_id"])
        portco_id = str(bench["portco_id"])
        fund_status = scope.set_index("fund_id")["fund_scope_status"].get(fund_id, "benchmark_only")
        if fund_status != "in_scope":
            _append_match(
                bench, None,
                match_tier=TIER_UNMATCHED_BENCHMARK,
                match_status="unmatched",
                miss_reason="fund_mismatch",
                match_method="fund_out_of_scope",
            )
            continue

        entity = alias_accepted.get((fund_id, portco_id))
        match_method = "accepted_company_alias"
        match_tier = TIER2_ALIAS if entity else None
        ext_row = external_by_fund_entity.get((fund_id, entity)) if entity else None

        if ext_row is None:
            for factset_id, mapped_portco in portco_map.items():
                if mapped_portco == portco_id:
                    candidate = external_by_fund_entity.get((fund_id, factset_id))
                    if candidate is not None:
                        entity = factset_id
                        ext_row = candidate
                        match_tier = TIER1_ID
                        match_method = "benchmark_portco_id"
                        break

        if ext_row is None:
            name_key = (fund_id, str(bench.get("company_key") or ""))
            entity = exact_names.get(name_key, "")
            ext_row = external_by_fund_name.get(name_key)
            if ext_row is not None:
                match_tier = TIER3_EXACT_NAME
                match_method = "exact_normalized_company_name"

        if ext_row is None:
            review_entity = alias_review.get((fund_id, portco_id))
            if review_entity:
                ext_row = external_by_fund_entity.get((fund_id, review_entity))
                if ext_row is not None:
                    match_tier = TIER4_NEEDS_REVIEW
                    match_method = "needs_review_company_alias"

        if ext_row is not None and match_tier is not None:
            txn_fund = _clean_id(ext_row.get("transaction_fund_entity_id"))
            scoped_funds = _fund_factset_map(scope).get(fund_id, set())
            if (
                enforce_transaction_fund_scope
                and txn_fund
                and scoped_funds
                and txn_fund not in scoped_funds
            ):
                _append_match(
                    bench, ext_row,
                    match_tier=TIER4_NEEDS_REVIEW,
                    match_status="needs_review",
                    miss_reason="transaction_fund_assignment_mismatch",
                    match_method="transaction_fund_assignment_mismatch",
                )
                continue
            status = "accepted" if match_tier in ACCEPTED_MATCH_TIERS else "needs_review"
            _append_match(
                bench, ext_row,
                match_tier=match_tier,
                match_status=status,
                miss_reason="matched",
                match_method=match_method,
            )
            continue

        if not entity and not portco_map:
            miss_reason = "missing_factset_company_id"
        elif entity or portco_id in {v for v in portco_map.values()}:
            miss_reason = "company_name_mismatch"
        else:
            miss_reason = "true_source_absence"
        _append_match(
            bench, None,
            match_tier=TIER_UNMATCHED_BENCHMARK,
            match_status="unmatched",
            miss_reason=miss_reason,
            match_method="no_external_match",
        )

    for _, ext in external.iterrows():
        ext_key = str(ext["external_holding_key"])
        if ext_key in matched_external_keys:
            continue
        records.append({
            "holding_key": pd.NA,
            "benchmark_side": "external",
            "external_holding_key": ext_key,
            "sponsor_id": ext["sponsor_id"],
            "sponsor_name": ext["sponsor_name"],
            "fund_id": ext["fund_id"],
            "fund_name": ext["fund_name"],
            "vintage": ext["vintage"],
            "portco_id": ext.get("benchmark_portco_id"),
            "company_name": ext.get("company_name"),
            "company_key": ext.get("company_key"),
            "factset_portco_entity_id": _clean_id(ext.get("factset_portco_entity_id")),
            "external_company_name": ext.get("company_name"),
            "match_tier": TIER_EXTERNAL_ONLY,
            "match_status": "external_only",
            "miss_reason": "external_only",
            "match_method": ext.get("match_method"),
            "fund_scope_status": scope.set_index("fund_id")["fund_scope_status"].get(ext["fund_id"], "benchmark_only"),
            "fund_match_status": scope.set_index("fund_id")["fund_match_status"].get(ext["fund_id"], "unmatched"),
        })

    out = pd.DataFrame(records)
    if out.empty:
        return _empty([
            "holding_key", "benchmark_side", "external_holding_key", "match_tier", "match_status", "miss_reason",
        ])
    dup_benchmark = out[
        out["benchmark_side"].eq("benchmark")
        & out["match_status"].isin(["accepted", "needs_review"])
    ].duplicated(subset=["holding_key", "match_tier"], keep=False)
    if dup_benchmark.any():
        out.loc[dup_benchmark, "match_tier"] = TIER_AMBIGUOUS
        out.loc[dup_benchmark, "match_status"] = "ambiguous"
        out.loc[dup_benchmark, "miss_reason"] = "ambiguous_mapping"
    return out.reset_index(drop=True)


def _replication_counts(match_spine: pd.DataFrame, scope: BacktestScope) -> dict[str, int]:
    if match_spine.empty:
        return {
            "benchmark_total": 0,
            "external_total": 0,
            "matched_benchmark": 0,
            "matched_external": 0,
            "unmatched_benchmark": 0,
            "external_only": 0,
        }
    benchmark = match_spine[match_spine["benchmark_side"].eq("benchmark")].copy()
    benchmark = benchmark[benchmark["fund_match_status"].isin(scope.fund_statuses)]
    external = match_spine[match_spine["benchmark_side"].eq("external")].copy()
    external = external[external["fund_match_status"].isin(scope.fund_statuses)]

    matched = benchmark[
        benchmark["match_tier"].isin(scope.match_tiers)
        & benchmark["miss_reason"].eq("matched")
    ]
    matched_external_keys = set(matched["external_holding_key"].dropna().astype(str))
    external_only_keys = set(
        external.loc[external["match_tier"].eq(TIER_EXTERNAL_ONLY), "external_holding_key"].dropna().astype(str)
    )
    external_total_keys = matched_external_keys | external_only_keys
    return {
        "benchmark_total": int(benchmark["holding_key"].nunique()),
        "external_total": int(len(external_total_keys)),
        "matched_benchmark": int(matched["holding_key"].nunique()),
        "matched_external": int(len(matched_external_keys)),
        "unmatched_benchmark": int(benchmark.loc[
            ~benchmark["holding_key"].isin(matched["holding_key"]), "holding_key"
        ].nunique()),
        "external_only": int(external[external["match_tier"].eq(TIER_EXTERNAL_ONLY)]["external_holding_key"].nunique()),
    }


def _rate_metrics(counts: dict[str, int]) -> dict[str, float | None]:
    benchmark_total = counts["benchmark_total"]
    external_total = counts["external_total"]
    matched_benchmark = counts["matched_benchmark"]
    matched_external = counts["matched_external"]
    recall = None if not benchmark_total else matched_benchmark / benchmark_total
    precision = None if not external_total else matched_external / external_total
    f1 = None
    if recall is not None and precision is not None and (recall + precision) > 0:
        f1 = 2 * recall * precision / (recall + precision)
    union = matched_benchmark + counts["external_only"]
    jaccard = None if not (benchmark_total or external_total) else matched_benchmark / union if union else 0.0
    return {"recall": recall, "precision": precision, "f1": f1, "jaccard": jaccard}


def measure_holding_replication(
    match_spine: pd.DataFrame,
    *,
    scopes: Iterable[BacktestScope] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scopes = list(scopes or [ACCEPTED_SCOPE, INCLUSIVE_SCOPE])
    if match_spine.empty:
        return (
            _empty([
                "scope_label", "rollup_level", "benchmark_total", "external_total",
                "matched_benchmark", "matched_external", "recall", "precision", "f1", "jaccard",
            ]),
            _empty(["match_tier", "miss_reason", "row_count"]),
        )
    records: list[dict[str, Any]] = []
    for scope in scopes:
        for rollup, group_cols in [
            ("overall", []),
            ("sponsor", ["sponsor_id", "sponsor_name"]),
            ("fund", ["fund_id", "fund_name", "vintage"]),
        ]:
            if group_cols:
                keys = match_spine.dropna(subset=group_cols).groupby(group_cols, dropna=False)
                groups = [(name, keys.get_group(name)) for name in keys.groups]
            else:
                groups = [(("all",), match_spine)]
            for key, group in groups:
                counts = _replication_counts(group, scope)
                rates = _rate_metrics(counts)
                row = {
                    "scope_label": scope.label,
                    "rollup_level": rollup,
                    **dict(zip(group_cols, key if group_cols else [])),
                    **counts,
                    **rates,
                }
                records.append(row)
    miss = (
        match_spine[match_spine["benchmark_side"].eq("benchmark")]
        .groupby(["match_tier", "miss_reason"], dropna=False)
        .size()
        .reset_index(name="row_count")
    )
    return pd.DataFrame(records), miss


def normalize_metric_value(value: Any, unit: str) -> float | None:
    if pd.isna(value):
        return None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    number = float(numeric)
    if unit == "ratio" and abs(number) > 1:
        return number / 100.0
    return number


def metric_within_tolerance(
    benchmark_value: float | None,
    external_value: float | None,
    *,
    unit: str,
    tolerances: dict[str, dict[str, float | None]] | None = None,
) -> bool | None:
    if benchmark_value is None or external_value is None:
        return None
    tol = (tolerances or DEFAULT_METRIC_TOLERANCES).get(unit, {})
    abs_tol = tol.get("abs")
    pct_tol = tol.get("pct")
    diff = abs(external_value - benchmark_value)
    if abs_tol is not None and diff <= abs_tol:
        return True
    if pct_tol is not None and benchmark_value != 0 and diff / abs(benchmark_value) <= pct_tol:
        return True
    if benchmark_value == 0 and external_value == 0:
        return True
    return False


def _benchmark_financial_frame(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    financial = dataset.get("fact_financial_snapshot", pd.DataFrame())
    if financial.empty:
        return financial
    return financial.drop_duplicates("portco_id", keep="first")


def _benchmark_debt_frame(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    debt = dataset.get("fact_debt_instrument_snapshot", pd.DataFrame())
    market = dataset.get("fact_market_trading_snapshot", pd.DataFrame())
    if debt.empty:
        return debt
    if market.empty:
        return debt
    merged = debt.merge(
        market[[
            "debt_instrument_id", "price", "ytw", "ytm",
        ]],
        on="debt_instrument_id",
        how="left",
        suffixes=("", "_mkt"),
    )
    return merged


def _external_metric_frame(external_holdings: pd.DataFrame) -> pd.DataFrame:
    return external_holdings.copy()


def compare_pit_metrics(
    dataset: dict[str, pd.DataFrame],
    match_spine: pd.DataFrame,
    external_holdings: pd.DataFrame | None = None,
    *,
    scope: BacktestScope = ACCEPTED_SCOPE,
    tolerances: dict[str, dict[str, float | None]] | None = None,
    external_cutoff: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare benchmark vs external financial/debt metrics using cutoff-safe observations."""
    cutoff = external_cutoff or _study_cutoff(dataset)
    external = (
        _normalize_external_holdings(external_holdings)
        if external_holdings is not None
        else build_external_holdings_from_dataset(dataset)
    )
    benchmark_fin = _benchmark_financial_frame(dataset)
    benchmark_debt = _benchmark_debt_frame(dataset)

    matched = match_spine[
        match_spine["benchmark_side"].eq("benchmark")
        & match_spine["match_tier"].isin(scope.match_tiers)
        & match_spine["miss_reason"].eq("matched")
    ].drop_duplicates("holding_key")

    records: list[dict[str, Any]] = []
    for _, row in matched.iterrows():
        portco_id = row["portco_id"]
        ext = external[external["external_holding_key"].eq(row["external_holding_key"])]
        if ext.empty:
            continue
        ext_row = ext.iloc[0]
        fin_row = benchmark_fin[benchmark_fin["portco_id"].eq(portco_id)]
        fin = fin_row.iloc[0] if not fin_row.empty else None

        for bench_col, ext_col, unit in FINANCIAL_METRIC_SPECS:
            bench_val = None if fin is None else normalize_metric_value(fin.get(bench_col), unit)
            ext_val = normalize_metric_value(ext_row.get(ext_col, ext_row.get(bench_col)), unit)
            if bench_val is None and ext_val is None:
                continue
            abs_err = None if bench_val is None or ext_val is None else abs(ext_val - bench_val)
            pct_err = None if bench_val in (None, 0) or ext_val is None else abs(ext_val - bench_val) / abs(bench_val)
            records.append({
                "holding_key": row["holding_key"],
                "portco_id": portco_id,
                "metric_group": "financial",
                "metric_name": bench_col,
                "unit": unit,
                "benchmark_value": bench_val,
                "external_value": ext_val,
                "absolute_error": abs_err,
                "percent_error": pct_err,
                "direction_agreement": None if bench_val is None or ext_val is None else np.sign(bench_val) == np.sign(ext_val),
                "tolerance_pass": metric_within_tolerance(bench_val, ext_val, unit=unit, tolerances=tolerances),
                "external_cutoff": cutoff.date(),
                "match_tier": row["match_tier"],
            })

        debt_rows = benchmark_debt[benchmark_debt["portco_id"].eq(portco_id)] if not benchmark_debt.empty else pd.DataFrame()
        for lien_bucket, field, bench_col, unit in DEBT_METRIC_SPECS:
            prefix = "first_lien" if lien_bucket == "first_lien" else "second_lien_unsecured"
            tranche_label = "1L" if lien_bucket == "first_lien" else "2L / Unsecured"
            debt_row = debt_rows[debt_rows["tranche_type"].eq(tranche_label)]
            debt = debt_row.iloc[0] if not debt_row.empty else None
            ext_field = f"current_{prefix.replace('second_lien_unsecured', '2l_unsecured')}_{field}"
            if field == "tranche_size":
                ext_field = f"current_{prefix.replace('second_lien_unsecured', '2l_unsecured')}_tranche_size_usd"
            bench_val = None if debt is None else normalize_metric_value(
                debt.get("tranche_size" if field == "tranche_size" else field if field != "maturity_date" else "maturity_date"),
                unit,
            )
            if debt is not None and field in {"price", "ytw", "ytm"}:
                bench_val = normalize_metric_value(debt.get(field), unit)
            ext_val = normalize_metric_value(ext_row.get(ext_field), unit)
            if bench_val is None and ext_val is None:
                continue
            abs_err = None if bench_val is None or ext_val is None else abs(ext_val - bench_val)
            pct_err = None if bench_val in (None, 0) or ext_val is None else abs(ext_val - bench_val) / abs(bench_val)
            records.append({
                "holding_key": row["holding_key"],
                "portco_id": portco_id,
                "metric_group": "debt",
                "metric_name": bench_col,
                "unit": unit,
                "benchmark_value": bench_val,
                "external_value": ext_val,
                "absolute_error": abs_err,
                "percent_error": pct_err,
                "direction_agreement": None if bench_val is None or ext_val is None else np.sign(bench_val) == np.sign(ext_val),
                "tolerance_pass": metric_within_tolerance(bench_val, ext_val, unit=unit, tolerances=tolerances),
                "external_cutoff": cutoff.date(),
                "match_tier": row["match_tier"],
            })

    detail = pd.DataFrame(records)
    if detail.empty:
        summary = _empty([
            "metric_group", "metric_name", "pair_count", "benchmark_fill", "external_fill",
            "median_abs_error", "median_pct_error", "tolerance_pass_rate",
        ])
        return detail, summary

    summary = (
        detail.groupby(["metric_group", "metric_name"], dropna=False)
        .agg(
            pair_count=("holding_key", "nunique"),
            benchmark_fill=("benchmark_value", lambda s: int(s.notna().sum())),
            external_fill=("external_value", lambda s: int(s.notna().sum())),
            median_abs_error=("absolute_error", "median"),
            median_pct_error=("percent_error", "median"),
            tolerance_pass_rate=("tolerance_pass", lambda s: float(pd.Series(s).dropna().mean()) if pd.Series(s).notna().any() else None),
        )
        .reset_index()
    )
    return detail, summary


def analyze_scale(
    dataset: dict[str, pd.DataFrame],
    match_spine: pd.DataFrame,
    fund_scope: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Quantify incremental external funds, holdings, and metric coverage beyond benchmark."""
    scope = fund_scope if fund_scope is not None else build_fund_scope(dataset, inclusive=True)
    benchmark_holdings = build_benchmark_holdings(dataset)
    benchmark_funds = set(benchmark_holdings["fund_id"])
    structured = dataset.get("structured_fund_portco_investment", pd.DataFrame())
    pevc_funds = dataset.get("source_factset_pevc_fund_summary", pd.DataFrame())

    replicated_keys = set(
        match_spine.loc[
            match_spine["miss_reason"].eq("matched") & match_spine["external_holding_key"].notna(),
            "external_holding_key",
        ].astype(str)
    )
    external_only = match_spine[
        match_spine["match_tier"].eq(TIER_EXTERNAL_ONLY)
    ].copy()

    records: list[dict[str, Any]] = []
    for sponsor_id, sponsor_group in scope.groupby("sponsor_id", dropna=False):
        sponsor_name = sponsor_group["sponsor_name"].iloc[0]
        in_scope_funds = set(sponsor_group.loc[sponsor_group["fund_scope_status"].eq("in_scope"), "fund_id"])
        benchmark_count = int(benchmark_holdings[benchmark_holdings["fund_id"].isin(in_scope_funds)]["holding_key"].nunique())
        incremental_holdings = external_only[external_only["fund_id"].isin(in_scope_funds)]
        incremental_count = int(incremental_holdings["external_holding_key"].nunique())
        high_conf = int(incremental_holdings[
            incremental_holdings.get("source_confidence", pd.Series(dtype=object)).eq("high")
        ]["external_holding_key"].nunique()) if "source_confidence" in incremental_holdings else 0

        extra_funds = 0
        if not pevc_funds.empty and "factset_gp_entity_id" in pevc_funds:
            sponsor_factset_ids = set(
                sponsor_group.dropna(subset=["source_fund_id"]).loc[
                    sponsor_group["source_system"].eq("factset_pevc"), "source_fund_id"
                ].map(_clean_id)
            )
            extra_funds = int(pevc_funds[
                ~pevc_funds["factset_fund_entity_id"].astype(str).isin(sponsor_factset_ids)
            ]["factset_fund_entity_id"].nunique()) if sponsor_factset_ids else 0

        metric_ready = 0
        if not structured.empty:
            structured_scope = structured[structured["fund_id"].isin(in_scope_funds)].copy()
            structured_scope["external_holding_key"] = structured_scope.apply(
                lambda row: stable_id(
                    "ext_hold",
                    row.get("fund_id"),
                    _clean_id(row.get("factset_portco_entity_id")) or row.get("company_name"),
                ),
                axis=1,
            )
            structured_scope = structured_scope[~structured_scope["external_holding_key"].astype(str).isin(replicated_keys)]
            metric_ready = int(structured_scope.dropna(subset=["benchmark_portco_id"])["structured_investment_id"].nunique())

        records.append({
            "sponsor_id": sponsor_id,
            "sponsor_name": sponsor_name,
            "benchmark_fund_count": int(sponsor_group["fund_id"].isin(benchmark_funds).sum()),
            "in_scope_fund_count": len(in_scope_funds),
            "benchmark_holding_count": benchmark_count,
            "incremental_external_holding_count": incremental_count,
            "incremental_high_confidence_holding_count": high_conf,
            "incremental_usable_metric_holding_count": metric_ready,
            "additional_factset_fund_count": extra_funds,
        })
    return pd.DataFrame(records)


def assert_no_lookahead(
    external_rows: pd.DataFrame,
    cutoff: pd.Timestamp,
    *,
    report_date_col: str = "current_financial_report_published_date",
    asof_date_col: str = "current_snapshot_date",
) -> None:
    if external_rows.empty:
        return
    if report_date_col in external_rows:
        published = pd.to_datetime(external_rows[report_date_col], errors="coerce")
        if published.notna().any() and (published.dropna() > cutoff).any():
            raise ValueError(f"Look-ahead detected in {report_date_col}")
    if asof_date_col in external_rows:
        asof = pd.to_datetime(external_rows[asof_date_col], errors="coerce")
        if asof.notna().any() and (asof.dropna() > cutoff).any():
            raise ValueError(f"Look-ahead detected in {asof_date_col}")


def validate_match_spine_grain(match_spine: pd.DataFrame) -> None:
    accepted = match_spine[
        match_spine["benchmark_side"].eq("benchmark")
        & match_spine["match_status"].eq("accepted")
    ]
    if accepted.duplicated("holding_key").any():
        raise ValueError("Duplicate accepted benchmark holding mappings detected")


def run_backtest(
    dataset: dict[str, pd.DataFrame],
    external_holdings: pd.DataFrame | None = None,
    *,
    exclude_sponsors: tuple[str, ...] = DEFAULT_EXCLUDED_SPONSOR_TOKENS,
) -> dict[str, pd.DataFrame]:
    baseline = build_benchmark_baseline(dataset)
    fund_scope = build_fund_scope(dataset, inclusive=True)
    external = external_holdings if external_holdings is not None else build_external_holdings_from_dataset(dataset, fund_scope)

    benchmark_holdings = filter_sponsor_rows(baseline["backtest_benchmark_holdings"], exclude_sponsors)
    ownership_events = filter_sponsor_rows(baseline["backtest_benchmark_ownership_events"], exclude_sponsors)
    fund_scope = filter_sponsor_rows(fund_scope, exclude_sponsors)
    external = filter_sponsor_rows(external, exclude_sponsors)
    exclusion_report = build_sponsor_exclusion_report(dataset, exclude_sponsors)

    match_spine = build_match_spine(
        dataset,
        benchmark_holdings,
        external,
        fund_scope=fund_scope,
    )
    validate_match_spine_grain(match_spine)
    replication, miss_reasons = measure_holding_replication(match_spine)
    metric_detail_accepted, metric_summary_accepted = compare_pit_metrics(
        dataset, match_spine, external, scope=ACCEPTED_SCOPE,
    )
    metric_detail_inclusive, metric_summary_inclusive = compare_pit_metrics(
        dataset, match_spine, external, scope=INCLUSIVE_SCOPE,
    )
    scale = analyze_scale(dataset, match_spine, fund_scope)
    filtered_totals = baseline["backtest_benchmark_totals"].copy()
    if exclude_sponsors:
        filtered_totals = pd.concat([
            filtered_totals,
            pd.DataFrame([
                {
                    "metric": "excluded_sponsor_tokens",
                    "actual": ", ".join(exclude_sponsors),
                    "target": None,
                    "difference": None,
                    "status": "reference_only",
                },
                {
                    "metric": "benchmark_holding_count_after_exclusions",
                    "actual": len(benchmark_holdings),
                    "target": None,
                    "difference": None,
                    "status": "reference_only",
                },
                {
                    "metric": "excluded_sponsor_count",
                    "actual": int(exclusion_report["excluded"].sum()) if not exclusion_report.empty else 0,
                    "target": None,
                    "difference": None,
                    "status": "reference_only",
                },
            ]),
        ], ignore_index=True)
    return {
        **baseline,
        "backtest_benchmark_holdings": benchmark_holdings,
        "backtest_benchmark_ownership_events": ownership_events,
        "backtest_benchmark_totals": filtered_totals,
        "backtest_sponsor_exclusions": exclusion_report,
        "backtest_fund_scope": fund_scope,
        "backtest_external_holdings": external,
        "backtest_match_spine": match_spine,
        "backtest_holding_replication": replication,
        "backtest_miss_reasons": miss_reasons,
        "backtest_metric_detail_accepted": metric_detail_accepted,
        "backtest_metric_summary_accepted": metric_summary_accepted,
        "backtest_metric_detail_inclusive": metric_detail_inclusive,
        "backtest_metric_summary_inclusive": metric_summary_inclusive,
        "backtest_scale_analysis": scale,
    }
