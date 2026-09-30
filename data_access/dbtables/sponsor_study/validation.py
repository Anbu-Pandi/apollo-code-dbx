from dataclasses import dataclass
import logging

import pandas as pd

from data_access.dbtables.sponsor_study.benchmark_v31_sponsor_xref import (
    v31_holding_xref_unclassified_count,
    v31_sponsor_xref_orphan_count,
)
from data_access.dbtables.sponsor_study.external_identity import (
    AUTO_CANDIDATE_MARGIN_MIN,
)
from data_access.dbtables.sponsor_study.sponsor_identity import sponsor_identity_conflicts, sponsor_lineage_errors


STUDY_TARGETS = {
    "portfolio_companies_total": 1839,
    "portfolio_companies_active": 1339,
    "debt_trading_coverage": 645,
    "leverage_coverage": 512,
    "maturity_coverage": 796,
    "equity_commitment_coverage": 861,
    "yoy_financials_coverage": 221,
}


def build_reconciliation(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    stage = dataset["stage_consolidated_company_snapshot"]
    bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
    financial = dataset.get("fact_financial_snapshot", pd.DataFrame())
    internal_financial = dataset.get("source_internal_financial_snapshot", pd.DataFrame())
    market = dataset.get("fact_market_trading_snapshot", pd.DataFrame())
    internal_market = dataset.get("source_internal_market_trading_snapshot", pd.DataFrame())
    debt = dataset.get("fact_debt_instrument_snapshot", pd.DataFrame())
    internal_debt = dataset.get("source_internal_debt_instrument_snapshot", pd.DataFrame())
    from data_access.dbtables.sponsor_study.factset_debt_attribution import admitted_factset_debt_rows
    internal_debt_bridge = admitted_factset_debt_rows(dataset.get("source_internal_debt_issuer_bridge", pd.DataFrame()))
    xref = dataset.get("xref_portco_internal_ids", pd.DataFrame())
    debt_features = dataset.get("fact_internal_debt_feature_snapshot", pd.DataFrame())
    transaction = dataset.get("fact_transaction_entry", pd.DataFrame())
    screens = dataset.get("fact_opportunity_screen", pd.DataFrame())
    compliance = dataset.get("fact_company_compliance_snapshot", pd.DataFrame())
    structured = dataset.get("structured_fund_portco_investment", pd.DataFrame())
    total_portcos = stage["portco_id"].nunique()
    matched_portcos = int(xref[~xref.get("match_confidence", pd.Series(dtype="object")).eq("unmatched")]["portco_id"].nunique()) if not xref.empty else 0
    actuals = {
        "portfolio_companies_total": total_portcos,
        "portfolio_companies_active": int(bridge["current_status"].astype("string").str.lower().eq("active").sum()) if not bridge.empty else 0,
        "debt_trading_coverage": int(market["portco_id"].nunique()) if not market.empty else 0,
        "leverage_coverage": int(financial.dropna(subset=["current_net_leverage"])["portco_id"].nunique()) if not financial.empty else 0,
        "maturity_coverage": int(debt.dropna(subset=["nearest_maturity_date"])["portco_id"].nunique()) if not debt.empty else 0,
        "equity_commitment_coverage": int(transaction.dropna(subset=["equity_commitment"])["ownership_id"].nunique()) if not transaction.empty else 0,
        "yoy_financials_coverage": int(financial.dropna(subset=["yoy_revenue_growth", "yoy_ebitda_growth"], how="all")["portco_id"].nunique()) if not financial.empty else 0,
        "internal_id_match_coverage": matched_portcos,
        "internal_debt_issuer_bridge_coverage": int(internal_debt_bridge["portco_id"].nunique()) if not internal_debt_bridge.empty else 0,
        "internal_debt_feature_coverage": int(debt_features["portco_id"].nunique()) if not debt_features.empty else 0,
        "internal_financial_coverage": int(internal_financial["portco_id"].nunique()) if not internal_financial.empty else 0,
        "internal_market_price_coverage": int(internal_market.dropna(subset=["price"], how="all")["portco_id"].nunique()) if not internal_market.empty else 0,
        "internal_maturity_coverage": int(internal_debt.dropna(subset=["nearest_maturity_date"], how="all")["portco_id"].nunique()) if not internal_debt.empty else 0,
        "compliance_private_data_count": int(compliance["private_data_flag"].map(lambda x: bool(x)).sum()) if not compliance.empty and "private_data_flag" in compliance.columns else 0,
        "compliance_direct_lending_only_count": int(compliance["direct_lending_only_flag"].map(lambda x: bool(x)).sum()) if not compliance.empty and "direct_lending_only_flag" in compliance.columns else 0,
        "compliance_pe_restricted_count": int(compliance["pe_investment_team_restricted_flag"].map(lambda x: bool(x)).sum()) if not compliance.empty and "pe_investment_team_restricted_flag" in compliance.columns else 0,
        "compliance_disqualified_lender_blocked_count": int(compliance["disqualified_lender_blocked_flag"].map(lambda x: bool(x)).sum()) if not compliance.empty and "disqualified_lender_blocked_flag" in compliance.columns else 0,
        "structured_investment_count": int(structured["structured_investment_id"].nunique()) if not structured.empty else 0,
        "structured_fund_count": int(structured["fund_id"].nunique()) if not structured.empty else 0,
        "structured_linked_benchmark_portco_count": int(structured.dropna(subset=["benchmark_portco_id"])["structured_portco_id"].nunique()) if not structured.empty else 0,
    }
    records = []
    for metric, actual in actuals.items():
        target = STUDY_TARGETS.get(metric)
        records.append({
            "metric": metric,
            "actual": actual,
            "target": target,
            "difference": None if target is None else actual - target,
            "status": "reference_only" if target is None else "match" if actual == target else "review",
        })
    if not screens.empty:
        for screen_name, count in screens.groupby("screen_name")["portco_id"].nunique().items():
            records.append({
                "metric": f"screen_count_{screen_name}",
                "actual": int(count),
                "target": None,
                "difference": None,
                "status": "reference_only",
            })
    if total_portcos:
        for metric in [
            "internal_id_match_coverage",
            "internal_debt_issuer_bridge_coverage",
            "internal_debt_feature_coverage",
            "internal_financial_coverage",
            "internal_market_price_coverage",
            "internal_maturity_coverage",
        ]:
            actual = actuals[metric]
            records.append({
                "metric": f"{metric}_pct",
                "actual": actual / total_portcos,
                "target": None,
                "difference": None,
                "status": "reference_only",
            })
        blocked = actuals.get("compliance_disqualified_lender_blocked_count", 0)
        records.append({
            "metric": "compliance_disqualified_lender_blocked_pct",
            "actual": blocked / total_portcos,
            "target": None,
            "difference": None,
            "status": "reference_only",
        })
    return pd.DataFrame(records)


def add_validation_outputs(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    out["validation_reconciliation"] = build_reconciliation(out)
    return out


EXTERNAL_VALIDATION_COLUMNS = [
    "metric",
    "scope",
    "actual",
    "target",
    "difference",
    "status",
]

EXTERNAL_SOURCE_RECONCILIATION_COLUMNS = [
    "source_system",
    "source_table",
    "source_row_count",
    "normalized_row_count",
    "canonical_row_count",
    "duplicate_row_count",
    "unmatched_row_count",
    "status",
    "detail",
]


@dataclass(frozen=True)
class ExternalSourceReconciliationSpec:
    source_system: str
    source_table: str
    source_key: str
    normalized_key: str
    canonical_key: str
    key_columns: tuple[str, ...]
    canonical_source_system: str = ""
    required_in_prod: bool = True


EXTERNAL_SOURCE_RECONCILIATION_SPECS = (
    ExternalSourceReconciliationSpec(
        "sponsor_website",
        "source_sponsor_site_holding_candidate",
        "source_sponsor_site_holding_candidate",
        "source_external_sponsor_website_holding",
        "fact_external_holding",
        ("crawl_run_id", "website_holding_candidate_id"),
        "sponsor_website",
    ),
    ExternalSourceReconciliationSpec(
        "factset_pevc_fund",
        "source_factset_pevc_fund_summary",
        "source_factset_pevc_fund_summary",
        "source_factset_pevc_fund_summary",
        "dim_external_fund",
        ("factset_fund_entity_id",),
    ),
    ExternalSourceReconciliationSpec(
        "factset_pevc",
        "source_factset_pevc_portco_investment",
        "source_factset_pevc_portco_investment",
        "source_factset_pevc_portco_investment",
        "fact_external_holding",
        (
            "factset_fund_entity_id",
            "factset_portco_entity_id",
            "pevc_trans_deal_id",
        ),
        "factset_pevc",
    ),
    ExternalSourceReconciliationSpec(
        "pitchbook",
        "source_pitchbook_holding",
        "source_pitchbook_holding",
        "source_pitchbook_holding",
        "fact_external_holding",
        ("pitchbook_holding_id",),
        "pitchbook",
    ),
    ExternalSourceReconciliationSpec(
        "preqin",
        "source_preqin_fund_summary",
        "source_preqin_fund_summary",
        "source_preqin_fund_summary",
        "dim_external_fund",
        ("preqin_fund_id", "preqin_fund_source_table"),
    ),
    ExternalSourceReconciliationSpec(
        "preqin",
        "source_preqin_dry_powder_macro",
        "source_preqin_dry_powder_macro",
        "source_preqin_dry_powder_macro",
        "source_preqin_dry_powder_macro",
        ("as_of_date", "asset_class", "strategy"),
    ),
    ExternalSourceReconciliationSpec(
        "preqin",
        "source_preqin_buyout_deal_flow",
        "source_preqin_buyout_deal_flow",
        "source_preqin_buyout_deal_flow",
        "source_preqin_buyout_deal_flow",
        ("deal_id",),
    ),
    ExternalSourceReconciliationSpec(
        "debt_features",
        "fact_external_debt_feature_snapshot",
        "fact_external_debt_feature_snapshot",
        "fact_external_debt_feature_snapshot",
        "fact_external_debt_feature_snapshot",
        ("external_portco_id", "instrument_id_uniq", "feature_name"),
        required_in_prod=False,
    ),
    ExternalSourceReconciliationSpec(
        "factset_dcs",
        "source_external_factset_dcs_debt_detail",
        "source_external_factset_dcs_debt_detail",
        "source_external_factset_dcs_debt_detail",
        "source_external_debt_instrument_snapshot",
        ("external_portco_id", "dcs_detail_id"),
        required_in_prod=False,
    ),
    ExternalSourceReconciliationSpec(
        "findox",
        "source_external_financial_snapshot",
        "source_external_financial_snapshot",
        "source_external_financial_snapshot",
        "source_external_financial_snapshot",
        ("external_portco_id", "period_end_date"),
        required_in_prod=False,
    ),
    ExternalSourceReconciliationSpec(
        "findox",
        "source_external_findox_document_index",
        "source_external_findox_document_index",
        "source_external_findox_document_index",
        "source_external_findox_document_index",
        ("external_portco_id", "document_id"),
        required_in_prod=False,
    ),
)


def _duplicate_count(frame: pd.DataFrame, columns: tuple[str, ...]) -> int:
    if frame.empty or not set(columns).issubset(frame.columns):
        return 0
    return int(frame.duplicated(list(columns), keep=False).sum())


def _canonical_source_count(
    frame: pd.DataFrame,
    source_system: str,
) -> int:
    if frame.empty or not source_system:
        return len(frame)
    column = (
        "holding_source_system"
        if "holding_source_system" in frame
        else "source_system"
        if "source_system" in frame
        else ""
    )
    if not column:
        return 0
    return int(frame[column].astype("string").eq(source_system).sum())


def build_external_source_reconciliation(
    dataset: dict[str, pd.DataFrame],
    *,
    production: bool = False,
) -> pd.DataFrame:
    records = []
    for spec in EXTERNAL_SOURCE_RECONCILIATION_SPECS:
        source = dataset.get(spec.source_key, pd.DataFrame())
        normalized = dataset.get(spec.normalized_key, pd.DataFrame())
        canonical = dataset.get(spec.canonical_key, pd.DataFrame())
        source_count = len(source)
        normalized_count = len(normalized)
        canonical_count = _canonical_source_count(
            canonical, spec.canonical_source_system
        )
        duplicates = _duplicate_count(source, spec.key_columns)
        unmatched = max(source_count - normalized_count, 0)
        missing = production and spec.required_in_prod and source_count == 0
        status = "blocked" if missing else "review" if duplicates else "match"
        detail = (
            "required production source is empty"
            if missing
            else f"keys={','.join(spec.key_columns)}"
        )
        records.append({
            "source_system": spec.source_system,
            "source_table": spec.source_table,
            "source_row_count": source_count,
            "normalized_row_count": normalized_count,
            "canonical_row_count": canonical_count,
            "duplicate_row_count": duplicates,
            "unmatched_row_count": unmatched,
            "status": status,
            "detail": detail,
        })
    return pd.DataFrame(
        records, columns=EXTERNAL_SOURCE_RECONCILIATION_COLUMNS
    )


def _release_gate_record(
    metric: str,
    passed: bool,
    *,
    actual: int,
    target: int = 1,
) -> dict:
    return {
        "metric": metric,
        "scope": "release_gate",
        "actual": actual,
        "target": target,
        "difference": actual - target,
        "status": "pass" if passed else "blocked",
    }


def _accepted_obligor_outside_stack_count(dataset: dict[str, pd.DataFrame]) -> int:
    stack = dataset.get("xref_external_company_capital_stack", pd.DataFrame())
    crosswalk = dataset.get("xref_external_company_debt_identifier", pd.DataFrame())
    if crosswalk.empty:
        return 0
    accepted_obligors = crosswalk[
        crosswalk["key_type"].eq("obligor_bbid")
        & crosswalk["review_status"].isin({"auto_matched", "accepted_seed", "manual_matched"})
        & crosswalk["is_active"].astype("boolean").fillna(False)
    ]
    if accepted_obligors.empty:
        return 0
    from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
        normalize_bbg_company_id,
    )

    accepted_keys = accepted_obligors[
        ["external_portco_id", "key_value"]
    ].copy()
    accepted_keys["resolved_obligor_company_id"] = accepted_keys[
        "key_value"
    ].map(normalize_bbg_company_id)
    accepted_keys = accepted_keys[accepted_keys["resolved_obligor_company_id"].notna()]
    required_stack = {
        "external_portco_id",
        "resolved_obligor_company_id",
        "obligor_inside_stack",
    }
    if stack.empty or not required_stack.issubset(stack.columns):
        return int(len(accepted_keys.drop_duplicates()))
    admitted = stack[
        stack["obligor_inside_stack"].astype("boolean").fillna(False).eq(True)
    ]
    if admitted.empty:
        return int(len(accepted_keys.drop_duplicates()))
    admitted_keys = admitted[
        ["external_portco_id", "resolved_obligor_company_id"]
    ].copy()
    admitted_keys["resolved_obligor_company_id"] = admitted_keys[
        "resolved_obligor_company_id"
    ].map(normalize_bbg_company_id)
    return len(accepted_keys.drop_duplicates().merge(
        admitted_keys.drop_duplicates(),
        on=["external_portco_id", "resolved_obligor_company_id"],
        how="left",
        indicator=True,
    ).query("_merge == 'left_only'"))


def _invalid_portco_dedup_decision_count(dataset: dict[str, pd.DataFrame]) -> int:
    decisions = dataset.get("xref_external_company_name_decision", pd.DataFrame())
    if decisions.empty:
        return 0
    required = {
        "decision_status",
        "is_active",
        "source_company_id",
        "external_portco_id",
    }
    if not required.issubset(decisions.columns):
        return 0
    active = decisions[decisions["is_active"].astype("boolean").fillna(False)]
    if active.empty:
        return 0
    errors = 0
    dedup_methods = {
        "website_factset_normalized_name",
        "website_factset_sponsor_evidence_v1",
        "cross_source_direct_entity_v1",
    }
    dedup = active[
        active.get(
            "decision_method",
            pd.Series("", index=active.index),
        ).isin(dedup_methods)
    ]
    accepted = dedup[dedup["decision_status"].eq("accepted")]
    if not accepted.empty:
        conflicting = (
            accepted.groupby("source_company_id")["external_portco_id"]
            .nunique()
            .gt(1)
        )
        errors += int(conflicting.sum())
        if conflicting.any():
            logging.warning(
                "Accepted dedup source_company_id maps to multiple portcos: %s",
                conflicting[conflicting].index.astype(str).tolist()[:20],
            )
        system_accepted = accepted[
            accepted.get(
                "approved_by",
                pd.Series("", index=accepted.index),
            ).eq("sponsor_stripped_name_identity")
        ]
        evidence_required = {
            "auto_match_eligible",
            "same_fund_flag",
            "same_vintage_flag",
            "candidate_margin",
            "ownership_episode_conflict",
            "rule_version",
        }
        if (
            not system_accepted.empty
            and evidence_required.issubset(system_accepted.columns)
        ):
            gated = system_accepted[system_accepted["auto_match_eligible"].notna()]
            invalid_evidence = gated[
                ~gated["auto_match_eligible"]
                .astype("boolean")
                .fillna(False)
                | ~gated["same_fund_flag"]
                .astype("boolean")
                .fillna(False)
                | ~gated["same_vintage_flag"]
                .astype("boolean")
                .fillna(False)
                | gated["candidate_margin"]
                .fillna(0)
                .astype(float)
                .lt(AUTO_CANDIDATE_MARGIN_MIN)
                | gated["ownership_episode_conflict"]
                .astype("boolean")
                .fillna(True)
                | gated["rule_version"]
                .astype("string")
                .fillna("")
                .str.strip()
                .eq("")
            ] if not gated.empty else gated
            invalid_evidence_n = (
                0
                if invalid_evidence.empty
                else int(
                    invalid_evidence["external_company_name_decision_id"].nunique()
                )
            )
            if invalid_evidence_n:
                logging.warning(
                    "System-accepted dedup failed evidence gates: %s",
                    invalid_evidence_n,
                )
            errors += invalid_evidence_n
        holdings = dataset.get("fact_external_holding", pd.DataFrame())
        holding_lineage = dataset.get(
            "xref_external_holding_source", pd.DataFrame()
        )
        required_holdings = {
            "external_holding_id",
            "external_sponsor_id",
            "external_portco_id",
        }
        required_lineage = {"external_holding_id", "source_company_id"}
        if (
            required_holdings.issubset(holdings.columns)
            and required_lineage.issubset(holding_lineage.columns)
        ):
            source_sponsors = holding_lineage[
                ["external_holding_id", "source_company_id"]
            ].merge(
                holdings[["external_holding_id", "external_sponsor_id"]],
                on="external_holding_id",
                how="left",
            )
            factset_holdings = (
                holdings[holdings["holding_source_system"].eq("factset_pevc")]
                if "holding_source_system" in holdings
                else holdings
            )
            canonical_sponsors = factset_holdings[
                ["external_portco_id", "external_sponsor_id"]
            ].drop_duplicates()
            checked = accepted[
                ["external_company_name_decision_id", "source_company_id",
                 "external_portco_id"]
            ].merge(
                source_sponsors[["source_company_id", "external_sponsor_id"]],
                on="source_company_id",
                how="left",
            ).rename(columns={"external_sponsor_id": "source_sponsor_id"}).merge(
                canonical_sponsors.rename(
                    columns={"external_sponsor_id": "canonical_sponsor_id"}
                ),
                on="external_portco_id",
                how="left",
            )
            mismatch_logged = 0
            for decision_id, group in checked.groupby(
                "external_company_name_decision_id", sort=False
            ):
                source_sponsors = set(group["source_sponsor_id"].dropna())
                canonical_sponsors = set(group["canonical_sponsor_id"].dropna())
                if not source_sponsors:
                    continue
                if (
                    len(source_sponsors) != 1
                    or not source_sponsors.issubset(canonical_sponsors)
                ):
                    errors += 1
                    mismatch_logged += 1
                    if mismatch_logged <= 20:
                        logging.warning(
                            "Accepted dedup %s sponsor mismatch source=%s canonical=%s",
                            decision_id,
                            sorted(map(str, source_sponsors)),
                            sorted(map(str, canonical_sponsors)),
                        )
    candidates = dedup[
        dedup["decision_status"].eq("candidate")
        & dedup["decision_method"].isin({
            "website_factset_normalized_name",
            "website_factset_sponsor_evidence_v1",
        })
    ]
    company_lineage = dataset.get("xref_external_company_source", pd.DataFrame())
    if (
        not candidates.empty
        and {"source_company_id", "external_portco_id"}.issubset(
            company_lineage.columns
        )
    ):
        candidate_lineage = candidates[
            ["external_company_name_decision_id", "source_company_id"]
        ].merge(
            company_lineage[["source_company_id", "external_portco_id"]],
            on="source_company_id",
            how="left",
        )
        mutated = candidate_lineage[
            candidate_lineage["external_portco_id"].notna()
            & candidate_lineage["external_portco_id"].astype(str).ne(
                candidate_lineage["source_company_id"].astype(str)
            )
        ]
        errors += mutated["external_company_name_decision_id"].nunique()
    return errors


def _association_cutoffs_match(
    candidate: pd.Timestamp, published: tuple[pd.Timestamp, ...],
) -> bool:
    """Compare publication clocks; an unknown baseline cannot waive preservation."""
    if len(published) != 1 or pd.isna(published[0]):
        raise ValueError("Association preservation requires one valid published source cutoff")
    return published[0].normalize() == candidate.normalize()


def _lost_accepted_association_count(
    dataset: dict[str, pd.DataFrame],
    source_cutoff: str | pd.Timestamp | None = None,
) -> int:
    decisions = dataset.get(
        "xref_external_company_name_decision", pd.DataFrame()
    )
    accepted = (
        decisions[
            decisions["decision_status"].eq("accepted")
            & decisions["is_active"].astype("boolean").fillna(False)
        ]
        if {"decision_status", "is_active"}.issubset(decisions.columns)
        else pd.DataFrame()
    )
    redirects = {
        str(source): str(canonical)
        for source, canonical in zip(
            accepted.get("source_company_id", pd.Series(dtype="object")),
            accepted.get("external_portco_id", pd.Series(dtype="object")),
        )
        if pd.notna(source) and pd.notna(canonical)
    }
    lost = 0
    cutoff = (
        pd.Timestamp(source_cutoff).normalize()
        if source_cutoff is not None
        else None
    )
    has_baseline = any(not dataset.get(name, pd.DataFrame()).empty for name in (
        "_baseline_accepted_portco_association", "_baseline_accepted_instrument_association",
    ))
    if cutoff is not None and has_baseline:
        metadata = dataset.get("_baseline_meta_external_refresh_run", pd.DataFrame())
        baseline_dates = tuple(pd.to_datetime(
            metadata.get("source_cutoff_date", pd.Series(dtype="object")), errors="coerce",
        ))
        if not _association_cutoffs_match(cutoff, baseline_dates):
            return 0
    comparisons = (
        (
            "_baseline_accepted_portco_association",
            "fact_external_holding",
            ("external_portco_id",),
        ),
        (
            "_baseline_accepted_instrument_association",
            "source_external_debt_instrument_snapshot",
            ("external_portco_id", "instrument_id_uniq", "asof_date"),
        ),
    )
    for baseline_name, current_name, key_columns in comparisons:
        baseline = dataset.get(baseline_name, pd.DataFrame())
        current = dataset.get(current_name, pd.DataFrame())
        if baseline.empty or not set(key_columns).issubset(baseline.columns):
            continue
        if current.empty or not set(key_columns).issubset(current.columns):
            lost += len(baseline[list(key_columns)].drop_duplicates())
            continue
        expected = baseline[list(key_columns)].copy()
        expected["external_portco_id"] = expected["external_portco_id"].map(
            lambda value: redirects.get(str(value), value)
        )
        if "asof_date" in key_columns:
            expected["asof_date"] = pd.to_datetime(
                expected["asof_date"], errors="coerce"
            )
            current = current.copy()
            current["asof_date"] = pd.to_datetime(
                current["asof_date"], errors="coerce"
            )
        lost += len(expected.drop_duplicates().merge(
            current[list(key_columns)].drop_duplicates(),
            on=list(key_columns),
            how="left",
            indicator=True,
        ).query("_merge == 'left_only'"))
    return lost


def _duplicate_debt_feature_instrument_count(dataset: dict[str, pd.DataFrame]) -> int:
    instruments = dataset.get("source_external_debt_instrument_snapshot", pd.DataFrame())
    if instruments.empty:
        return 0
    key_cols = [
        col for col in ["external_portco_id", "instrument_id_uniq", "asof_date"]
        if col in instruments.columns
    ]
    if len(key_cols) < 3:
        return 0
    grouped = instruments.groupby(key_cols).size()
    return int(grouped.gt(1).sum())


def _canonical_duplicate_count(dataset: dict[str, pd.DataFrame]) -> int:
    keys = {
        "dim_external_sponsor": ("external_sponsor_id",),
        "dim_external_fund": ("external_fund_id",),
        "dim_external_portco": ("external_portco_id",),
        "fact_external_holding": ("external_holding_id",),
    }
    return sum(
        _duplicate_count(dataset.get(table, pd.DataFrame()), columns)
        for table, columns in keys.items()
    )


def _manual_decision_error_count(dataset: dict[str, pd.DataFrame]) -> int:
    decisions = dataset.get(
        "xref_external_company_name_decision", pd.DataFrame()
    )
    if decisions.empty:
        return 0
    if "holding_key" in decisions:
        keyed = (
            decisions["holding_key"].notna()
            & decisions["holding_key"].astype("string").str.strip().ne("")
        )
        if keyed.any():
            return int(keyed.sum())
    required = {
        "external_company_name_decision_id",
        "external_portco_id",
        "source_company_name",
        "canonical_company_name",
        "decision_status",
        "is_active",
    }
    if not required.issubset(decisions):
        return len(decisions)
    active = decisions[
        decisions["decision_status"].eq("accepted")
        & decisions["is_active"].astype("boolean").fillna(False)
    ]
    errors = int(active["external_portco_id"].isna().sum())
    errors += int(
        active["external_company_name_decision_id"].duplicated().sum()
    )
    return errors


def _future_observation_count(
    dataset: dict[str, pd.DataFrame],
    source_cutoff: str | pd.Timestamp | None,
) -> int:
    if source_cutoff is None:
        return 0
    cutoff = pd.Timestamp(source_cutoff).normalize()
    date_columns = {
        "as_of_date",
        "asof_date",
        "report_date",
        "publication_date",
        "period_end_date",
        "document_date",
    }
    observation_tables = {
        "source_external_financial_snapshot",
        "source_external_market_trading_snapshot",
        "source_external_debt_instrument_snapshot",
        "fact_external_debt_feature_snapshot",
        "source_external_factset_dcs_debt_detail",
    }
    future = 0
    for key in observation_tables:
        frame = dataset.get(key, pd.DataFrame())
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for column in date_columns & set(frame.columns):
            observed = pd.to_datetime(frame[column], errors="coerce")
            n_future = int(observed.dt.normalize().gt(cutoff).sum())
            if n_future:
                logging.info(
                    "PIT future dates table=%s column=%s rows=%s cutoff=%s",
                    key,
                    column,
                    n_future,
                    cutoff.date().isoformat(),
                )
            future += n_future
    return future


def _benchmark_dependency_count(dataset: dict[str, pd.DataFrame]) -> int:
    denied = {
        "stage_consolidated_company_snapshot",
        "dim_sponsor",
        "dim_fund",
        "dim_portco",
        "bridge_fund_portco",
        "xref_manual_company_match",
        "fact_financial_snapshot",
        "fact_market_trading_snapshot",
        "fact_debt_instrument_snapshot",
    }
    return sum(
        1
        for key in dataset
        if not key.startswith("source_benchmark_v31_")
        and (
            key in denied
            or key.startswith("source_annual_")
            or key.startswith("source_template_")
        )
    )


def build_external_release_gate(
    dataset: dict[str, pd.DataFrame],
    *,
    production: bool = False,
    source_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    source_reconciliation = dataset.get(
        "validation_external_source_reconciliation", pd.DataFrame()
    )
    required_source_failures = (
        int(source_reconciliation["status"].eq("blocked").sum())
        if not source_reconciliation.empty
        else int(production)
    )
    duplicate_count = _canonical_duplicate_count(dataset)
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    lineage = dataset.get("xref_external_holding_source", pd.DataFrame())
    sponsor_errors = (
        sponsor_identity_conflicts(holdings).external_sponsor_id.nunique()
        if {'external_sponsor_id', 'sponsor_name'}.issubset(holdings.columns)
        else len(holdings)
    )
    source_sponsor_errors = len(sponsor_lineage_errors(dataset))
    from data_access.dbtables.sponsor_study.identity_review_policy import score_policy_error_count
    score_policy_errors = score_policy_error_count(dataset)
    lineage_missing = (
        max(
            holdings["external_holding_id"].nunique()
            - lineage["external_holding_id"].nunique(),
            0,
        )
        if not holdings.empty
        and "external_holding_id" in holdings
        and not lineage.empty
        and "external_holding_id" in lineage
        else len(holdings)
    )
    manual_errors = _manual_decision_error_count(dataset)
    future_count = _future_observation_count(dataset, source_cutoff)
    benchmark_dependencies = _benchmark_dependency_count(dataset)
    v31_orphans = v31_sponsor_xref_orphan_count(
        dataset,
        require_snapshot=production,
    )
    v31_holding_unclassified = v31_holding_xref_unclassified_count(dataset)
    enrichment_keys = (
        "fact_external_debt_feature_snapshot",
        "source_external_financial_snapshot",
        "source_external_market_trading_snapshot",
        "source_external_debt_instrument_snapshot",
        "source_external_factset_dcs_debt_detail",
        "source_external_findox_document_index",
    )
    empty_enrichments = sum(
        dataset.get(key, pd.DataFrame()).empty for key in enrichment_keys
    )
    obligor_outside_stack = _accepted_obligor_outside_stack_count(dataset)
    invalid_dedup_decisions = _invalid_portco_dedup_decision_count(dataset)
    duplicate_instruments = _duplicate_debt_feature_instrument_count(dataset)
    lost_associations = _lost_accepted_association_count(
        dataset,
        source_cutoff=source_cutoff,
    )

    records = [
        _release_gate_record(
            "required_sources_nonzero",
            not production or required_source_failures == 0,
            actual=max(1 - required_source_failures, 0),
        ),
        _release_gate_record(
            "canonical_key_uniqueness",
            duplicate_count == 0,
            actual=int(duplicate_count == 0),
        ),
        _release_gate_record(
            "sponsor_identity_consistent",
            sponsor_errors == 0,
            actual=sponsor_errors,
            target=0,
        ),
        _release_gate_record(
            "sponsor_source_lineage_preserved",
            source_sponsor_errors == 0,
            actual=source_sponsor_errors,
            target=0,
        ),
        _release_gate_record(
            "score_identity_policy_valid",
            score_policy_errors == 0,
            actual=score_policy_errors,
            target=0,
        ),
        _release_gate_record(
            "canonical_holding_lineage_complete",
            lineage_missing == 0,
            actual=int(lineage_missing == 0),
        ),
        _release_gate_record(
            "manual_decisions_bound_without_benchmark_keys",
            manual_errors == 0,
            actual=int(manual_errors == 0),
        ),
        _release_gate_record(
            "point_in_time_dates_valid",
            future_count == 0,
            actual=int(future_count == 0),
        ),
        _release_gate_record(
            "zero_benchmark_dependencies",
            benchmark_dependencies == 0,
            actual=int(benchmark_dependencies == 0),
        ),
        _release_gate_record(
            "v31_sponsor_xref_complete",
            v31_orphans == 0,
            actual=int(v31_orphans == 0),
        ),
        _release_gate_record(
            "v31_holding_xref_classified",
            v31_holding_unclassified == 0,
            actual=int(v31_holding_unclassified == 0),
        ),
        _release_gate_record(
            "minimum_enrichment_coverage",
            not production or empty_enrichments == 0,
            actual=int(empty_enrichments == 0),
        ),
        _release_gate_record(
            "accepted_obligor_inside_admitted_stack",
            obligor_outside_stack == 0,
            actual=int(obligor_outside_stack == 0),
        ),
        _release_gate_record(
            "portco_dedup_decisions_valid",
            invalid_dedup_decisions == 0,
            actual=int(invalid_dedup_decisions),
            target=0,
        ),
        _release_gate_record(
            "debt_feature_instrument_deduped",
            duplicate_instruments == 0,
            actual=int(duplicate_instruments == 0),
        ),
        _release_gate_record(
            "accepted_associations_preserved",
            lost_associations == 0,
            actual=int(lost_associations == 0),
        ),
    ]
    return pd.DataFrame(records, columns=EXTERNAL_VALIDATION_COLUMNS)


def build_external_validation(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    scope = dataset.get("source_external_sponsor_scope", pd.DataFrame())
    funds = dataset.get("dim_external_fund", pd.DataFrame())
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    xref = dataset.get("xref_external_portco_factset", pd.DataFrame())
    from data_access.dbtables.sponsor_study.factset_debt_attribution import admitted_factset_debt_rows
    debt_bridge = admitted_factset_debt_rows(dataset.get("source_external_debt_issuer_bridge", pd.DataFrame()))
    financial = dataset.get("source_external_financial_snapshot", pd.DataFrame())
    market = dataset.get("source_external_market_trading_snapshot", pd.DataFrame())
    debt = dataset.get("source_external_debt_instrument_snapshot", pd.DataFrame())
    website = dataset.get("source_external_sponsor_website_holding", pd.DataFrame())

    sponsor_count = int(scope["external_sponsor_id"].nunique()) if not scope.empty else 0
    fund_count = int(funds["external_fund_id"].nunique()) if not funds.empty else 0
    holding_count = int(holdings["external_holding_id"].nunique()) if not holdings.empty else 0
    factset_holdings = holdings[holdings["holding_source_system"].eq("factset_pevc")] if not holdings.empty else pd.DataFrame()
    website_holdings = holdings[holdings["holding_source_system"].eq("sponsor_website")] if not holdings.empty else pd.DataFrame()
    assumed_fund = holdings[holdings["fund_assignment_is_assumed"].eq(True)] if not holdings.empty else pd.DataFrame()

    actuals = {
        "external_sponsor_count": sponsor_count,
        "external_fund_count": fund_count,
        "external_holding_count": holding_count,
        "external_factset_holding_count": int(factset_holdings["external_holding_id"].nunique()) if not factset_holdings.empty else 0,
        "external_website_holding_count": int(website_holdings["external_holding_id"].nunique()) if not website_holdings.empty else 0,
        "external_assumed_flagship_fund_assignment_count": int(assumed_fund["external_holding_id"].nunique()) if not assumed_fund.empty else 0,
        "external_factset_identity_count": int(xref["external_portco_id"].nunique()) if not xref.empty else 0,
        "external_debt_bridge_coverage": int(debt_bridge["external_portco_id"].nunique()) if not debt_bridge.empty else 0,
        "external_financial_coverage": int(financial["external_portco_id"].nunique()) if not financial.empty else 0,
        "external_market_price_coverage": int(market.dropna(subset=["price"], how="all")["external_portco_id"].nunique()) if not market.empty else 0,
        "external_maturity_coverage": int(debt.dropna(subset=["nearest_maturity_date"], how="all")["external_portco_id"].nunique()) if not debt.empty else 0,
        "external_flagship_fund_count": int(funds["flagship_flag"].map(lambda value: bool(value)).sum()) if not funds.empty and "flagship_flag" in funds.columns else 0,
        "external_preqin_matched_sponsor_count": int(scope.dropna(subset=["preqin_firm_id"])["external_sponsor_id"].nunique()) if not scope.empty else 0,
        "external_factset_gp_matched_sponsor_count": int(scope.dropna(subset=["factset_gp_entity_id"])["external_sponsor_id"].nunique()) if not scope.empty else 0,
        "external_website_explicit_fund_match_count": int(website[website["fund_match_method"].eq("website_explicit_fund_match")]["website_holding_id"].nunique()) if not website.empty else 0,
    }
    records = []
    for metric, actual in actuals.items():
        records.append({
            "metric": metric,
            "scope": "external_only",
            "actual": actual,
            "target": None,
            "difference": None,
            "status": "reference_only",
        })
    if holding_count:
        for metric in [
            "external_factset_identity_count",
            "external_debt_bridge_coverage",
            "external_financial_coverage",
            "external_market_price_coverage",
            "external_maturity_coverage",
        ]:
            records.append({
                "metric": f"{metric}_pct",
                "scope": "external_only",
                "actual": actuals[metric] / holding_count,
                "target": None,
                "difference": None,
                "status": "reference_only",
            })
    return pd.DataFrame(records, columns=EXTERNAL_VALIDATION_COLUMNS)


def add_external_validation_outputs(
    dataset: dict[str, pd.DataFrame],
    *,
    production: bool = False,
    source_cutoff: str | pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    out["validation_external_source_reconciliation"] = (
        build_external_source_reconciliation(out, production=production)
    )
    out["validation_external_enrichment"] = pd.concat(
        [
            build_external_validation(out),
            build_external_release_gate(
                out,
                production=production,
                source_cutoff=source_cutoff,
            ),
        ],
        ignore_index=True,
    )
    return out
