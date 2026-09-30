"""Production table ownership and schemas for the external sponsor data product."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from data_access.dbtables.sponsor_study.benchmark_v31_sponsor_xref import (
    BENCHMARK_V31_COMMENT_COLUMNS,
    BENCHMARK_V31_HOLDING_XREF_COLUMNS,
    BENCHMARK_V31_PORTCO_COLUMNS,
    BENCHMARK_V31_SPONSOR_XREF_COLUMNS,
)
from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import (
    CAPITAL_STACK_COLUMNS,
)
from data_access.dbtables.sponsor_study.debt_identifier_crosswalk import (
    CROSSWALK_COLUMNS,
)
from data_access.dbtables.sponsor_study.findox_coverage import COVERAGE_COLUMNS
from data_access.dbtables.sponsor_study.financial_history import FINANCIAL_CANDIDATE_COLUMNS, ENTITY_LINEAGE_COLUMNS
from data_access.dbtables.sponsor_study.findox_fundamentals import (
    FINDOX_MATCH_COLUMNS,
)
from data_access.dbtables.sponsor_study.findox_debt import XREF_COLUMNS as FINDOX_INSTRUMENT_COLUMNS
from data_access.dbtables.sponsor_study.fund_vendor import (
    FUND_VENDOR_COMPARISON_COLUMNS,
)
from data_access.dbtables.sponsor_study.internal_joins import (
    EXTERNAL_DEBT_FEATURE_COLUMNS,
    EXTERNAL_DEBT_INSTRUMENT_COLUMNS,
    EXTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS,
    EXTERNAL_FACTSET_DCS_COLUMNS,
    EXTERNAL_FINANCIAL_COLUMNS,
    EXTERNAL_MARKET_COLUMNS,
    XREF_COLUMNS,
)
from data_access.dbtables.sponsor_study.validation import (
    EXTERNAL_VALIDATION_COLUMNS,
)


REFRESH_METADATA_COLUMNS = ("refresh_run_id", "ingested_at")

NORMALIZED_EXTERNAL_SOURCE_TABLES = (
    "source_factset_pevc_fund_summary",
    "source_factset_pevc_portco_investment",
    "source_pitchbook_holding",
    "source_preqin_fund_summary",
    "source_preqin_dry_powder_macro",
    "source_preqin_buyout_deal_flow",
    "fact_external_fund_performance_snapshot",
    "source_external_sponsor_website_holding",
)
CANONICAL_EXTERNAL_TABLES = (
    "dim_external_sponsor",
    "dim_external_fund",
    "dim_external_portco",
    "fact_external_holding",
)
EXTERNAL_SOURCE_XREF_TABLES = (
    "xref_external_fund_source",
    "xref_external_holding_source",
    "xref_external_company_source",
    "xref_external_company_name_decision",
    "xref_external_manual_entity_link",
    "xref_pitchbook_sponsor_name",
    "xref_external_company_internal_id",
    "xref_external_company_debt_identifier",
    "xref_external_company_capital_stack",
    "xref_external_company_findox",
    "xref_external_company_findox_instrument",
)
BENCHMARK_V31_TABLES = (
    "source_benchmark_v31_portco_row",
    "source_benchmark_v31_sponsor_xref",
    "source_benchmark_v31_comment",
    "source_benchmark_v31_holding_xref",
)
EXTERNAL_ENRICHMENT_TABLES = (
    "source_external_debt_issuer_bridge",
    "fact_external_debt_feature_snapshot",
    "source_external_financial_snapshot",
    "fact_external_financial_candidate",
    "fact_external_entity_lineage",
    "source_external_market_trading_snapshot",
    "source_external_debt_instrument_snapshot",
    "source_external_factset_dcs_debt_detail",
    "source_external_findox_document_index",
)
EXTERNAL_RUN_TABLES = (
    "meta_external_refresh_run",
    "validation_external_enrichment",
    "validation_external_source_reconciliation",
    "company_identity_review_queue",
)

EXTERNAL_CURATION_TABLE_ALLOWLIST = (
    *NORMALIZED_EXTERNAL_SOURCE_TABLES,
    *CANONICAL_EXTERNAL_TABLES,
    *EXTERNAL_SOURCE_XREF_TABLES,
    *BENCHMARK_V31_TABLES,
    *EXTERNAL_ENRICHMENT_TABLES,
    *EXTERNAL_RUN_TABLES,
)
EXTERNAL_PROD_TABLE_ALLOWLIST = frozenset(EXTERNAL_CURATION_TABLE_ALLOWLIST)
EXTERNAL_PROD_CATALOG = "apollo_market_data_prod"

EXTERNAL_PROD_PHYSICAL_TABLE_NAMES = {
    name: (
        "source_curated_website_holding"
        if name == "source_external_sponsor_website_holding"
        else name.replace("_external_", "_").replace("external_", "")
    )
    for name in EXTERNAL_CURATION_TABLE_ALLOWLIST
}
if len(set(EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.values())) != len(
    EXTERNAL_PROD_PHYSICAL_TABLE_NAMES
):
    raise RuntimeError("External prod physical table names must be unique")
if any(
    "external" in name
    for name in EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.values()
):
    raise RuntimeError("External prod physical table names cannot contain external")


def external_prod_physical_table_name(
    logical_name: str,
    *,
    catalog: str = "",
) -> str:
    clean = str(logical_name or "").strip()
    if str(catalog).strip().lower() != EXTERNAL_PROD_CATALOG:
        return clean
    return EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.get(clean, clean)

# These ten objects are produced by the website crawler. This cycle-free
# contract intentionally mirrors website_crawl_persistence.WEBSITE_CRAWL_TABLES;
# tests assert the producer and consumer contracts stay identical.
WEBSITE_REFERENCE_ONLY_TABLES = (
    "source_sponsor_website_list",
    "control_website_crawl_run",
    "control_website_crawl_run_sponsor",
    "source_sponsor_site_snapshot",
    "source_sponsor_site_holding_candidate",
    "xref_website_holding_to_portco",
    "source_sponsor_site_portfolio_bridge",
    "source_sponsor_website_holding",
    "validation_website_bridge_reconciliation",
    "validation_website_crawl_sponsor_summary",
)
if len(WEBSITE_REFERENCE_ONLY_TABLES) != 10:
    raise RuntimeError("The website producer contract must contain exactly 10 tables")

EXTERNAL_PROD_BENCHMARK_DENYLIST = frozenset({
    "stage_consolidated_company_snapshot",
    "dim_sponsor",
    "dim_fund",
    "dim_portco",
    "bridge_fund_portco",
    "fact_transaction_entry",
    "fact_fund_performance_snapshot",
    "fact_financial_snapshot",
    "fact_market_trading_snapshot",
    "fact_debt_instrument_snapshot",
    "fact_exit_liquidity_event",
    "fact_internal_debt_feature_snapshot",
    "fact_company_compliance_snapshot",
    "fact_opportunity_screen",
    "source_internal_financial_snapshot",
    "source_internal_market_trading_snapshot",
    "source_internal_debt_instrument_snapshot",
    "source_internal_debt_issuer_bridge",
    "xref_portco_internal_ids",
    "structured_fund_scope",
    "structured_fund_portco_investment",
    "output_company_analysis_snapshot",
    "output_sponsor_summary",
    "output_opportunity_screen",
    "output_structured_universe",
    "output_universe_reconciliation",
    "xref_manual_company_match",
})
EXTERNAL_PROD_BENCHMARK_DENY_PREFIXES = (
    "source_annual_",
    "source_template_",
)
BENCHMARK_TABLE_DENYLIST = EXTERNAL_PROD_BENCHMARK_DENYLIST


DIM_EXTERNAL_PORTCO_COLUMNS = [
    "external_portco_id",
    "company_name",
    "company_slug",
    "company_country",
    "industry",
    "preferred_source_system",
    "first_seen_run_id",
    "last_seen_run_id",
    "is_active",
]
MANUAL_ENTITY_LINK_COLUMNS = [
    "manual_entity_link_decision_id",
    "legacy_holding_key",
    "review_scope",
    "benchmark_company_name",
    "benchmark_sponsor_name",
    "candidate_entity_name",
    "candidate_entity_id",
    "candidate_entity_id_type",
    "relationship_type",
    "decision_status",
    "reviewed_by",
    "reviewed_at",
    "rationale",
    "evidence_url",
    "is_active",
]
XREF_EXTERNAL_HOLDING_SOURCE_COLUMNS = [
    "external_holding_source_xref_id",
    "external_holding_id",
    "external_sponsor_id",
    "external_fund_id",
    "external_portco_id",
    "source_system",
    "source_holding_id",
    "source_sponsor_id",
    "source_fund_id",
    "source_company_id",
    "source_company_name",
    "source_investment_date",
    "source_knowledge_datetime",
    "match_method",
    "match_confidence",
    "canonical_source_flag",
]
XREF_EXTERNAL_COMPANY_SOURCE_COLUMNS = [
    "external_company_source_xref_id",
    "external_portco_id",
    "source_system",
    "source_company_id",
    "source_company_name",
    "source_company_slug",
    "match_method",
    "match_score",
    "match_confidence",
    "review_status",
    "first_seen_run_id",
    "last_seen_run_id",
    "is_active",
]
XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS = [
    "external_company_name_decision_id",
    "external_portco_id",
    "external_sponsor_id",
    "source_external_portco_id",
    "source_system",
    "source_company_id",
    "source_company_name",
    "source_company_slug",
    "canonical_company_name",
    "source_holding_source_system",
    "canonical_holding_source_system",
    "decision_status",
    "decision_method",
    "match_score",
    "source_name_key",
    "canonical_name_key",
    "character_score",
    "token_order_score",
    "token_coverage_score",
    "prefix_match_flag",
    "exact_name_flag",
    "same_fund_flag",
    "same_vintage_flag",
    "independent_fund_match_flag",
    "shared_fund_count",
    "shared_cohort_count",
    "investment_year_distance",
    "industry_match_flag",
    "country_match_flag",
    "company_domain_match_flag",
    "candidate_margin",
    "ownership_episode_conflict",
    "corroboration_count",
    "auto_match_eligible",
    "identity_evidence_type",
    "identity_evidence_value",
    "evidence_summary",
    "rule_version",
    "approved_by",
    "approved_at",
    "rationale",
    "first_seen_run_id",
    "last_seen_run_id",
    "is_active",
    "revoked_at",
    "revoked_by",
]
XREF_PITCHBOOK_SPONSOR_NAME_COLUMNS = [
    "external_sponsor_id",
    "sponsor_name",
    "pitchbook_investor_name",
    "match_method",
    "match_confidence",
    "review_status",
    "first_seen_run_id",
    "last_seen_run_id",
    "is_active",
]
EXTERNAL_REFRESH_RUN_COLUMNS = [
    "refresh_run_id",
    "run_started_at",
    "run_completed_at",
    "status",
    "environment",
    "catalog",
    "schema",
    "code_version",
    "source_cutoff_date",
    "website_crawl_run_id",
    "config_json",
    "validation_status",
    "published_at",
    "supersedes_refresh_run_id",
    "error_message",
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
EXTERNAL_FINDOX_DOCUMENT_COLUMNS = [
    "external_portco_id",
    "findox_issuer_id",
    "document_id",
    "document_name",
    "document_date",
    "document_type",
    "is_private",
    "source_system",
]

FACTSET_FUND_SOURCE_COLUMNS = [
    "factset_fund_entity_id",
    "factset_gp_entity_id",
    "sponsor_name_factset",
    "fund_name_factset",
    "vintage",
    "commitment_usd_m",
    "commitment_reported_m",
    "reported_currency",
    "pevc_fund_status_code",
    "fund_status",
    "fund_strategy",
    "buyout_strategy_flag",
    "as_of_date",
    "paid_in_pct",
    "net_irr",
    "dpi",
    "rvpi",
    "net_moic",
]
FACTSET_HOLDING_SOURCE_COLUMNS = [
    "factset_gp_entity_id",
    "sponsor_name_factset",
    "factset_fund_entity_id",
    "fund_name_factset",
    "vintage",
    "fund_strategy",
    "factset_portco_entity_id",
    "portco_name_factset",
    "portco_country",
    "portco_website",
    "portco_description",
    "portco_profile_json",
    "portco_industry",
    "pevc_trans_deal_id",
    "entry_date",
    "exit_date",
    "retaining_stake",
    "exit_code",
    "entry_type",
    "transaction_close_date",
    "round_of_funding",
    "total_investment_amount",
    "investment_description",
    "amount_invested_by_sponsor",
    "transaction_fund_entity_id",
    "transaction_fund_name",
]
PREQIN_FUND_SOURCE_COLUMNS = [
    "preqin_fund_id",
    "preqin_firm_id",
    "preqin_fund_source_table",
    "preqin_asset_class",
    "sponsor_name_preqin",
    "fund_name_preqin",
    "vintage",
    "fund_strategy",
    "fund_status",
    "commitment_usd_m",
    "paid_in_pct",
    "dpi",
    "rvpi",
    "net_moic",
    "net_irr",
    "as_of_date",
]
PREQIN_DRY_POWDER_COLUMNS = [
    "as_of_date",
    "asset_class",
    "strategy",
    "dry_powder_usd_bn",
]
PREQIN_BUYOUT_DEAL_FLOW_COLUMNS = [
    "deal_id",
    "target_company",
    "target_id",
    "deal_date",
    "deal_type",
    "deal_status",
    "exit_flag",
    "portfolio_company_region",
    "portfolio_company_country",
    "primary_industry",
    "industry_verticals",
    "deal_size_usd_m",
    "enterprise_value_usd_m",
    "equity_usd_m",
    "debt_usd_m",
]
def _externalize(columns: Iterable[str]) -> list[str]:
    return [
        (
            "external_fund_id"
            if column == "fund_id"
            else column
            .replace("portco_id", "external_portco_id")
            .replace("external_company_id", "external_portco_id")
        )
        for column in columns
    ]


def _contract(
    columns: Iterable[str],
    *,
    delta_schema_types: dict[str, str] | None = None,
    refresh_scoped: bool = True,
) -> pd.DataFrame:
    ordered_columns = list(dict.fromkeys(columns))
    if refresh_scoped:
        ordered_columns.extend(
            column
            for column in REFRESH_METADATA_COLUMNS
            if column not in ordered_columns
        )
    out = pd.DataFrame(columns=ordered_columns)
    schema_types = dict(delta_schema_types or {})
    if refresh_scoped:
        schema_types.update({
            "refresh_run_id": "STRING",
            "ingested_at": "TIMESTAMP",
        })
    if schema_types:
        out.attrs["delta_schema_types"] = schema_types
    return out


def is_benchmark_table_denied(table_name: str) -> bool:
    logical_name = table_name.rsplit(".", 1)[-1].lower()
    if logical_name.startswith("pe_sponsor_"):
        logical_name = logical_name.removeprefix("pe_sponsor_")
    if logical_name.startswith("source_benchmark_v31_"):
        return False
    return (
        logical_name in EXTERNAL_PROD_BENCHMARK_DENYLIST
        or logical_name.startswith(EXTERNAL_PROD_BENCHMARK_DENY_PREFIXES)
    )


def external_prod_table_contracts() -> dict[str, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.identity_review import QUEUE_COLUMNS
    from data_importer.sponsor_study.pitchbook import PITCHBOOK_HOLDING_COLUMNS
    """Return all and only curation-owned production Delta table contracts."""
    from data_access.dbtables.sponsor_study.external_universe import (
        DIM_EXTERNAL_FUND_COLUMNS,
        EXTERNAL_WEBSITE_HOLDING_COLUMNS,
        EXTERNAL_WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
        EXTERNAL_SPONSOR_SCOPE_COLUMNS,
        FACT_EXTERNAL_HOLDING_COLUMNS,
        XREF_EXTERNAL_FUND_SOURCE_COLUMNS,
    )

    fund_types = {
        "vintage": "BIGINT",
        "commitment_usd_m": "DOUBLE",
        "as_of_date": "TIMESTAMP",
        "pct_called": "DOUBLE",
        "dpi": "DOUBLE",
        "rvpi": "DOUBLE",
        "net_moic": "DOUBLE",
        "net_irr": "DOUBLE",
        "flagship_flag": "BOOLEAN",
    }
    holding_types = {
        "investment_date": "TIMESTAMP",
        "exit_date": "TIMESTAMP",
        "exit_is_full": "BOOLEAN",
        "transaction_close_date": "TIMESTAMP",
        "knowledge_datetime": "TIMESTAMP",
        "total_investment_amount": "DOUBLE",
        "amount_invested_by_sponsor": "DOUBLE",
        "fund_assignment_is_assumed": "BOOLEAN",
        "source_investment_year": "BIGINT",
        "fund_assignment_score": "DOUBLE",
        "fund_assignment_margin": "DOUBLE",
        "canonical_holding_flag": "BOOLEAN",
    }
    factset_fund_types = {
        "vintage": "BIGINT",
        "commitment_usd_m": "DOUBLE",
        "commitment_reported_m": "DOUBLE",
        "buyout_strategy_flag": "BOOLEAN",
        "as_of_date": "TIMESTAMP",
        "paid_in_pct": "DOUBLE",
        "net_irr": "DOUBLE",
        "dpi": "DOUBLE",
        "rvpi": "DOUBLE",
        "net_moic": "DOUBLE",
    }
    factset_holding_types = {
        "vintage": "BIGINT",
        "entry_date": "TIMESTAMP",
        "exit_date": "TIMESTAMP",
        "retaining_stake": "DOUBLE",
        "exit_code": "BIGINT",
        "transaction_close_date": "TIMESTAMP",
        "total_investment_amount": "DOUBLE",
        "amount_invested_by_sponsor": "DOUBLE",
    }
    pitchbook_types = {
        "pitchbook_deal_count": "BIGINT",
        "investment_date": "TIMESTAMP",
        "latest_deal_date": "TIMESTAMP",
        "knowledge_datetime": "TIMESTAMP",
    }
    contracts = {
        "source_factset_pevc_fund_summary": _contract(
            FACTSET_FUND_SOURCE_COLUMNS,
            delta_schema_types=factset_fund_types,
        ),
        "source_factset_pevc_portco_investment": _contract(
            FACTSET_HOLDING_SOURCE_COLUMNS,
            delta_schema_types=factset_holding_types,
        ),
        "source_pitchbook_holding": _contract(
            PITCHBOOK_HOLDING_COLUMNS,
            delta_schema_types=pitchbook_types,
        ),
        "source_preqin_fund_summary": _contract(
            PREQIN_FUND_SOURCE_COLUMNS,
            delta_schema_types=fund_types,
        ),
        "source_preqin_dry_powder_macro": _contract(
            PREQIN_DRY_POWDER_COLUMNS,
            delta_schema_types={
                "as_of_date": "DATE",
                "dry_powder_usd_bn": "DOUBLE",
            },
        ),
        "source_preqin_buyout_deal_flow": _contract(
            PREQIN_BUYOUT_DEAL_FLOW_COLUMNS,
            delta_schema_types={
                "deal_date": "DATE",
                "exit_flag": "BOOLEAN",
                "deal_size_usd_m": "DOUBLE",
                "enterprise_value_usd_m": "DOUBLE",
                "equity_usd_m": "DOUBLE",
                "debt_usd_m": "DOUBLE",
            },
        ),
        "fact_external_fund_performance_snapshot": _contract(
            _externalize(FUND_VENDOR_COMPARISON_COLUMNS),
            delta_schema_types=fund_types,
        ),
        "source_external_sponsor_website_holding": _contract(
            (
                column
                for column in EXTERNAL_WEBSITE_HOLDING_COLUMNS
                if column != "benchmark_portco_id"
            ),
            delta_schema_types=EXTERNAL_WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
        ),
        "dim_external_sponsor": _contract(
            EXTERNAL_SPONSOR_SCOPE_COLUMNS,
            delta_schema_types={"sponsor_match_score": "DOUBLE"},
        ),
        "dim_external_fund": _contract(
            DIM_EXTERNAL_FUND_COLUMNS,
            delta_schema_types=fund_types,
        ),
        "dim_external_portco": _contract(
            DIM_EXTERNAL_PORTCO_COLUMNS,
            delta_schema_types={"is_active": "BOOLEAN"},
        ),
        "fact_external_holding": _contract(
            FACT_EXTERNAL_HOLDING_COLUMNS,
            delta_schema_types=holding_types,
        ),
        "xref_external_fund_source": _contract(
            XREF_EXTERNAL_FUND_SOURCE_COLUMNS,
            delta_schema_types={
                "match_score": "DOUBLE",
                "source_vintage": "BIGINT",
            },
        ),
        "xref_external_holding_source": _contract(
            XREF_EXTERNAL_HOLDING_SOURCE_COLUMNS,
            delta_schema_types={
                "source_investment_date": "TIMESTAMP",
                "source_knowledge_datetime": "TIMESTAMP",
                "canonical_source_flag": "BOOLEAN",
            },
        ),
        "xref_external_company_source": _contract(
            XREF_EXTERNAL_COMPANY_SOURCE_COLUMNS,
            delta_schema_types={
                "match_score": "DOUBLE",
                "is_active": "BOOLEAN",
            },
        ),
        "xref_external_company_name_decision": _contract(
            XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS,
            delta_schema_types={
                "match_score": "DOUBLE",
                "character_score": "DOUBLE",
                "token_order_score": "DOUBLE",
                "token_coverage_score": "DOUBLE",
                "prefix_match_flag": "BOOLEAN",
                "exact_name_flag": "BOOLEAN",
                "same_fund_flag": "BOOLEAN",
                "same_vintage_flag": "BOOLEAN",
                "independent_fund_match_flag": "BOOLEAN",
                "shared_fund_count": "BIGINT",
                "shared_cohort_count": "BIGINT",
                "investment_year_distance": "BIGINT",
                "industry_match_flag": "BOOLEAN",
                "country_match_flag": "BOOLEAN",
                "company_domain_match_flag": "BOOLEAN",
                "candidate_margin": "DOUBLE",
                "ownership_episode_conflict": "BOOLEAN",
                "corroboration_count": "BIGINT",
                "auto_match_eligible": "BOOLEAN",
                "approved_at": "TIMESTAMP",
                "is_active": "BOOLEAN",
                "revoked_at": "TIMESTAMP",
            },
        ),
        "xref_external_manual_entity_link": _contract(
            MANUAL_ENTITY_LINK_COLUMNS,
            delta_schema_types={
                "reviewed_at": "TIMESTAMP",
                "is_active": "BOOLEAN",
            },
        ),
        "xref_pitchbook_sponsor_name": _contract(
            XREF_PITCHBOOK_SPONSOR_NAME_COLUMNS,
            delta_schema_types={"is_active": "BOOLEAN"},
        ),
        "xref_external_company_internal_id": _contract(_externalize(XREF_COLUMNS)),
        "xref_external_company_debt_identifier": _contract(
            _externalize(CROSSWALK_COLUMNS),
            delta_schema_types={
                "key_priority": "BIGINT",
                "match_score": "DOUBLE",
                "resolved_asof_date": "TIMESTAMP",
                "first_seen_at": "TIMESTAMP",
                "last_seen_at": "TIMESTAMP",
                "is_active": "BOOLEAN",
            },
        ),
        "xref_external_company_capital_stack": _contract(
            _externalize(CAPITAL_STACK_COLUMNS),
            delta_schema_types={
                "capital_stack_rank": "BIGINT",
                "capital_stack_descendant_count": "BIGINT",
                "capital_stack_expansion_truncated": "BOOLEAN",
                "exact_match_flag": "BOOLEAN",
                "match_score": "DOUBLE",
                "path_depth": "BIGINT",
                "obligor_inside_stack": "BOOLEAN",
            },
        ),
        "xref_external_company_findox": _contract(
            _externalize([*FINDOX_MATCH_COLUMNS, *COVERAGE_COLUMNS]),
            delta_schema_types={
                "match_score": "DOUBLE",
                "token_overlap_score": "DOUBLE",
                "exact_match_flag": "BOOLEAN",
                "selected": "BOOLEAN",
                "findox_allowed": "BOOLEAN",
                "findox_coverage_asof": "DATE",
                **{name: "BIGINT" for name in COVERAGE_COLUMNS if name.endswith("_count")},
            },
        ),
        "xref_external_company_findox_instrument": _contract(
            FINDOX_INSTRUMENT_COLUMNS,
            delta_schema_types={"observed_at": "TIMESTAMP", "market_date": "DATE",
                                "company_match_score": "DOUBLE", "price": "DOUBLE"},
        ),
        "source_benchmark_v31_portco_row": _contract(
            BENCHMARK_V31_PORTCO_COLUMNS,
            delta_schema_types={
                "source_excel_row": "BIGINT",
                "vintage": "BIGINT",
                "investment_date": "TIMESTAMP",
                "active_flag": "BOOLEAN",
                "deal_size_tev": "DOUBLE",
                "equity_commitment": "DOUBLE",
                "entry_multiple": "DOUBLE",
            },
        ),
        "source_benchmark_v31_sponsor_xref": _contract(
            BENCHMARK_V31_SPONSOR_XREF_COLUMNS,
        ),
        "source_benchmark_v31_comment": _contract(
            BENCHMARK_V31_COMMENT_COLUMNS,
            delta_schema_types={
                "source_excel_row": "BIGINT",
                "value": "DOUBLE",
            },
        ),
        "source_benchmark_v31_holding_xref": _contract(
            BENCHMARK_V31_HOLDING_XREF_COLUMNS,
        ),
        "source_external_debt_issuer_bridge": _contract(
            EXTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS,
            delta_schema_types={
                "asof_date": "TIMESTAMP", "debt_security_is_physical": "BOOLEAN",
                "debt_attribution_status": "STRING", "debt_relationship_path": "STRING",
                "debt_security_start_date": "TIMESTAMP", "debt_security_end_date": "TIMESTAMP",
                "debt_credit_start_date": "TIMESTAMP", "debt_credit_end_date": "TIMESTAMP",
            },
        ),
        "fact_external_debt_feature_snapshot": _contract(
            EXTERNAL_DEBT_FEATURE_COLUMNS,
            delta_schema_types={"asof_date": "TIMESTAMP"},
        ),
        "source_external_financial_snapshot": _contract(
            EXTERNAL_FINANCIAL_COLUMNS,
            delta_schema_types={
                "asof_date": "TIMESTAMP",
                "period_end_date": "TIMESTAMP",
                "financial_review_required": "BOOLEAN",
            },
        ),
        "fact_external_financial_candidate": _contract(
            FINANCIAL_CANDIDATE_COLUMNS,
            delta_schema_types={"value": "DOUBLE", "source_value": "DOUBLE", "fx_rate": "DOUBLE", "selected": "BOOLEAN",
                                **{column: "TIMESTAMP" for column in (
                                    "period", "asof", "source_loaded_at", "scope_valid_from", "scope_valid_to", "scope_recorded_at")}},
        ),
        "fact_external_entity_lineage": _contract(
            ENTITY_LINEAGE_COLUMNS,
            delta_schema_types={"effective_asof_date": "DATE", "source_knowledge_date": "TIMESTAMP"},
        ),
        "source_external_market_trading_snapshot": _contract(
            EXTERNAL_MARKET_COLUMNS,
            delta_schema_types={"asof_date": "TIMESTAMP"},
        ),
        "source_external_debt_instrument_snapshot": _contract(
            EXTERNAL_DEBT_INSTRUMENT_COLUMNS,
            delta_schema_types={
                "asof_date": "TIMESTAMP",
                "maturity_date": "TIMESTAMP",
                "nearest_maturity_date": "TIMESTAMP",
            },
        ),
        "source_external_factset_dcs_debt_detail": _contract(
            EXTERNAL_FACTSET_DCS_COLUMNS,
            delta_schema_types={
                "report_date": "TIMESTAMP",
                "publication_date": "TIMESTAMP",
                "issue_date": "TIMESTAMP",
                "maturity_date": "TIMESTAMP",
                "dcs_relationship_asof_date": "TIMESTAMP",
                "dcs_selected_filing_flag": "BOOLEAN",
                "dcs_relationship_path": "STRING",
            },
        ),
        "source_external_findox_document_index": _contract(
            EXTERNAL_FINDOX_DOCUMENT_COLUMNS,
            delta_schema_types={
                "document_date": "TIMESTAMP",
                "is_private": "BOOLEAN",
            },
        ),
        "meta_external_refresh_run": _contract(
            EXTERNAL_REFRESH_RUN_COLUMNS,
            refresh_scoped=False,
            delta_schema_types={
                "run_started_at": "TIMESTAMP",
                "run_completed_at": "TIMESTAMP",
                "source_cutoff_date": "TIMESTAMP",
                "published_at": "TIMESTAMP",
            },
        ),
        "validation_external_enrichment": _contract(
            EXTERNAL_VALIDATION_COLUMNS,
            delta_schema_types={
                "actual": "DOUBLE",
                "target": "DOUBLE",
                "difference": "DOUBLE",
            },
        ),
        "validation_external_source_reconciliation": _contract(
            EXTERNAL_SOURCE_RECONCILIATION_COLUMNS,
            delta_schema_types={
                "source_row_count": "BIGINT",
                "normalized_row_count": "BIGINT",
                "canonical_row_count": "BIGINT",
                "duplicate_row_count": "BIGINT",
                "unmatched_row_count": "BIGINT",
            },
        ),
    }
    contracts["company_identity_review_queue"] = _contract(
        QUEUE_COLUMNS,
        delta_schema_types={
            **{name: "DOUBLE" for name in ("match_score", "token_score", "character_score", "token_order_score", "candidate_margin")},
            **{name: "BOOLEAN" for name in ("same_fund", "same_vintage", "episode_conflict")},
        },
    )
    if tuple(contracts) != EXTERNAL_CURATION_TABLE_ALLOWLIST:
        raise RuntimeError("External production contracts do not match the table allowlist")
    return contracts
