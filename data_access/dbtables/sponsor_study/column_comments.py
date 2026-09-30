"""Column documentation for the production PE sponsor data product."""

from __future__ import annotations

import re

import pandas as pd

from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables.sponsor_study.external_prod_contracts import (
    EXTERNAL_PROD_PHYSICAL_TABLE_NAMES,
)
from data_access.dbtables.sponsor_study.query import DEFAULT_TABLE_PREFIX, prefixed_name
from data_access.dbtables.sponsor_study.website_crawl_persistence import (
    WEBSITE_CRAWL_TABLES,
)


GOLD_SPONSOR_VIEWS = (
    "v_pe_sponsor_investment_360",
    "v_pe_sponsor_fund_360",
    "v_pe_sponsor_market_monthly_360",
    "v_pe_sponsor_data_quality",
)

TABLE_CONTEXT = {
    "dim_sponsor": ("Canonical external sponsor attributes", "one row per external sponsor"),
    "dim_fund": ("Canonical external fund attributes and performance", "one row per external fund"),
    "dim_portco": ("Canonical portfolio-company attributes", "one row per external portfolio company"),
    "fact_holding": ("Canonical sponsor-fund-company investments", "one row per source-resolved holding"),
    "fact_fund_performance_snapshot": ("Normalized external fund performance observations", "one row per fund observation"),
    "fact_debt_feature_snapshot": ("Long-form debt features", "one row per portfolio company, instrument, and feature"),
    "meta_refresh_run": ("External curation publication controls", "one row per refresh run"),
    "validation_enrichment": ("External enrichment metrics and release gates", "one row per validation metric"),
    "validation_source_reconciliation": ("Source-to-canonical reconciliation", "one row per source table"),
    "source_curated_website_holding": ("Curated website holdings consumed by canonical curation", "one row per website holding and fund assignment"),
    "source_benchmark_v31_portco_row": ("Typed V31 benchmark portfolio-company snapshot", "one row per manually curated V31 holding"),
    "source_benchmark_v31_sponsor_xref": ("V31-to-production sponsor identity crosswalk", "one row per V31 sponsor name and production refresh"),
    "source_benchmark_v31_comment": ("Typed V31 entry-metric comment evidence", "one row per workbook cell comment for deal_size_tev, equity_commitment, or entry_multiple"),
    "source_benchmark_v31_holding_xref": ("Auditable V31-to-external holding crosswalk", "one row per V31 holding and strict sponsor-fund-company match attempt"),
    "v_pe_sponsor_investment_360": ("Dashboard-ready investment, company, fund, and debt attributes", "one row per canonical external holding"),
    "v_pe_sponsor_fund_360": ("Dashboard-ready fund performance and portfolio aggregates", "one row per external fund"),
    "v_pe_sponsor_market_monthly_360": ("Monthly private-market activity and dry-powder trends", "one row per month and geography"),
    "v_pe_sponsor_data_quality": ("Source, refresh, crawl, and validation health", "one row per quality scope and identifier"),
}

EXACT_COLUMN_COMMENTS = {
    "debt_attribution_status": "Dated security-issuer, credit-parent or borrower-ownership evidence for a proposed FactSet debt link. Unsupported rows remain audit evidence and cannot populate debt, price, financial or identity outputs.",
    "debt_security_factset_id": "FactSet security identifier used to verify the debt link; the security type determines whether it identifies physical debt.",
    "debt_security_entity_id": "Unambiguous FactSet security issuer valid at the debt observation date.",
    "debt_credit_parent_entity_id": "Unambiguous FactSet credit parent valid at the debt observation date; a financing role rather than a company identity assertion.",
    "debt_security_is_physical": "True for a FactSet bank loan, corporate bond or municipal bond; equity/ticker references alone cannot establish physical debt ownership.",
    "debt_security_start_date": "Start of the FactSet security-to-issuer relationship supporting this observation.",
    "debt_security_end_date": "End of the FactSet security-to-issuer relationship; null denotes an open interval.",
    "debt_credit_start_date": "Start of the FactSet security-to-credit-parent relationship supporting this observation.",
    "debt_credit_end_date": "End of the FactSet credit-parent relationship; null denotes an open interval.",
    "debt_attribution_scope": "consolidated_group identifies full group debt attributed to this related company; it is not a standalone company debt balance or ownership-weighted allocation.",
    "debt_group_bbg_company_id": "Bloomberg identifier of the corroborated common operating/acquisition group; does not replace the portfolio-company identity.",
    "debt_group_name": "Name of the common operating/acquisition group whose full debt is attributed.",
    "debt_borrower_bbg_company_id": "Bloomberg identifier of the actual debt borrower, retained separately from the portfolio company.",
    "debt_borrower_name": "Name of the actual debt borrower within the attributed group.",
    "debt_relationship_path": "JSON containing dated security issuer/credit-parent evidence, company-to-group and group-to-borrower paths, or company-to-filer paths at the filing report date and source cutoff.",
    "dcs_entity_relationship": "Current FactSet relationship used to propose this filing; it does not independently authorize historical debt attribution.",
    "dcs_attribution_status": "Own-entity, dated parent-filer, dated subsidiary-filer, or unverified related-filer evidence. Unsupported filings remain raw evidence only.",
    "dcs_attribution_reason": "Identifier and dated ownership evidence supporting or preventing use of this filing in debt metrics.",
    "dcs_relationship_asof_date": "Source cutoff at which related-filer ownership was corroborated; ownership must also be supported at the filing report date.",
    "dcs_relationship_path": "JSON retaining Bloomberg ownership paths and dates at both the filing report date and source cutoff.",
    "dcs_selected_filing_flag": "True only for the supported representative filing selected for this company. False rows remain source evidence and do not supply debt or security prices.",
    "debt_attribution_label": "Indicates when the investment includes consolidated group borrowing attributed at its full source amount.",
    "consolidated_group_debt_instrument_count": "Distinct group-debt instruments attributed to this company after company identity and instrument deduplication.",
    "consolidated_group_debt_amount": "USD millions of identified group-debt instruments at full source amounts. The same instruments may appear under other related companies; deduplicate instruments before fund aggregation.",
    "consolidated_group_names": "Names of operating/acquisition groups contributing consolidated borrowing to this company.",
    "consolidated_group_borrowers": "Actual borrower names contributing consolidated borrowing to this company.",
    "external_holding_id": "Stable identifier for a sponsor-fund-company holding in the external curation universe.",
    "external_sponsor_id": "Stable identifier for the canonical external sponsor.",
    "external_fund_id": "Stable identifier for the canonical external fund.",
    "external_portco_id": "Stable identifier for the canonical external portfolio company.",
    "refresh_run_id": "Identifier of the curation run that produced or published the row.",
    "crawl_run_id": "Identifier of the website crawl run that produced the source observation.",
    "ingested_at": "UTC timestamp when the row was added to the PE sponsor data product.",
    "knowledge_datetime": "Point-in-time timestamp when the source information became known to the pipeline.",
    "canonical_holding_flag": "True when the row is the selected canonical holding after applying the source waterfall.",
    "fund_assignment_is_assumed": "True when fund attribution was inferred rather than explicitly stated by the source.",
    "holding_source_system": "Source system selected as the canonical provider for the holding.",
    "contributing_source_systems": "Delimited set of source systems contributing evidence for the holding.",
    "source_systems": "Delimited holding-lineage sources supporting the canonical investment; this does not describe the source of financial or debt metrics.",
    "financial_source_system": "Source selected for current financial metrics: findox, debt_features, debt_features_factset_entity_tree, debt_features_factset_dcs_instrument, debt_features_factset_loan_security, or debt_features_reported_loan_deal.",
    "financial_identifier": "Source-native identifier used to resolve the selected financial record to the portfolio company.",
    "financial_provenance_json": "Per-metric source, identifier, reporting period, source snapshot, line or instrument ID, and reason for debt-features-first or aggregate-FinDox fallback selection.",
    "debt_price_bridge_provenance": "Identifier path used to link the representative priced instrument to the company; Core paths retain asset-level evidence in xref_company_findox_instrument.",
    "debt_features_priced_instrument_count": "Distinct instruments with a price from debt_features on the refresh's common market date.",
    "securities_data_priced_instrument_count": "Distinct instruments priced by the v_securities_data fallback on the same common market date.",
    "financial_identifier_type": "Identifier namespace used for financial resolution, such as findox_issuer_id, Bloomberg company ID, ticker, or instrument identifier.",
    "financial_asof_date": "Pipeline as-of date associated with the selected financial observation.",
    "financial_period_end_date": "Financial reporting period end associated with the selected debt, revenue, and EBITDA metrics.",
    "financial_provenance_status": "Human-readable financial provenance distinguishing Findox matches, direct instruments, FactSet entity-tree paths, and other facilities in an exactly linked loan agreement.",
    "financial_review_required": "True for fuzzy Findox financial matches that have no debt-feature coverage and therefore require manual provenance review.",
    "has_findox_financials": "True when current financial metrics come from structured Findox fundamentals.",
    "findox_has_debt_feature_coverage": "True when a Findox-funded company also has debt-feature rows; this is coverage context, not proof that the Findox name match is correct.",
    "findox_issuer_id": "Findox issuer identifier selected for structured financial retrieval.",
    "findox_matched_input_name": "Portfolio-company name or approved alias used as the input to Findox matching.",
    "matched_findox_name": "Findox issuer or client-issuer spelling selected by the name matcher.",
    "findox_matched_name_field": "Findox registry field that supplied the selected matched name.",
    "findox_match_score": "Name-match score for the selected Findox issuer; inspect together with exact-match and token-overlap fields.",
    "findox_token_overlap_score": "Token overlap between the sponsor-study company name and selected Findox name.",
    "findox_exact_match_flag": "True when normalized company and Findox names matched exactly.",
    "debt_feature_count": "Number of long-form debt-feature rows mapped to the portfolio company; zero does not imply that Findox structured fundamentals are unavailable.",
    "debt_feature_source_systems": "Delimited debt-feature mapping paths: direct identifiers, FactSet entity tree, exact DCS instruments, and/or FactSet loan securities through WSO.",
    "direct_debt_feature_count": "Debt-feature rows mapped through direct company, ticker, Findox, Bloomberg, or instrument identifiers.",
    "entity_tree_debt_feature_count": "Debt-feature rows mapped through the approved downward FactSet entity-tree bridge.",
    "company_name": "Canonical display name of the portfolio company.",
    "sponsor_name": "Canonical display name of the private-equity sponsor.",
    "v31_sponsor_name": "Sponsor house name exactly as curated in the V31 benchmark workbook.",
    "prod_sponsor_name": "Canonical production sponsor display name mapped from the V31 sponsor name.",
    "v31_holding_id": "Stable identifier derived from the V31 workbook, sheet, row, sponsor, fund, and company identity.",
    "source_workbook": "Workbook filename from which the typed V31 benchmark observation was extracted.",
    "source_excel_row": "Original Excel row number supporting traceability to the V31 workbook.",
    "match_method": "Deterministic identity-resolution method or unresolved reason used by the crosswalk.",
    "match_status": "Classification of the V31 holding crosswalk as matched, benchmark_only, ambiguous, or unresolved_sponsor.",
    "deal_size_tev": "Entry total enterprise value in USD millions. Investment 360 uses COALESCE(FactSet total_investment_amount, V31 deal_size_tev).",
    "equity_commitment": "Entry equity commitment in USD millions. Investment 360 uses COALESCE(FactSet amount_invested_by_sponsor, V31 equity_commitment).",
    "entry_multiple": "Entry EBITDA multiple from the V31 benchmark workbook when no external equivalent exists.",
    "comment_text": "Workbook cell comment text preserved as validation evidence for the typed benchmark metric.",
    "field_name": "V31 workbook metric field associated with the preserved comment evidence.",
    "template_comment_id": "Stable identifier for a workbook cell comment in the V31 benchmark extract.",
    "fund_name": "Canonical display name of the investment fund.",
    "investment_date": "Date the sponsor investment began, when available from the source.",
    "exit_date": "Date the sponsor investment was realized or exited; null indicates no observed exit.",
    "commitment_usd_m": "Fund commitment expressed in USD millions.",
    "estimated_dry_powder_usd_m": "Estimated uncalled fund commitment in USD millions, calculated from commitment and percent called.",
    "current_total_debt": "Latest selected total debt for the portfolio company.",
    "current_total_cash": "Latest selected cash balance for the portfolio company.",
    "current_net_debt": "Latest selected total debt less cash for the portfolio company.",
    "current_ltm_revenue": "Latest selected last-twelve-month revenue for the portfolio company.",
    "current_ltm_ebitda": "Latest selected last-twelve-month operating EBITDA for the portfolio company.",
    "current_gross_leverage": "Latest selected gross debt-to-EBITDA multiple.",
    "current_net_leverage": "Latest selected net debt-to-EBITDA multiple.",
    "nearest_maturity_date": "Earliest observed debt maturity date for the portfolio company.",
    "price": "Latest selected traded price for a representative debt instrument.",
    "ytw": "Latest selected yield to worst for a representative debt instrument.",
    "spread": "Latest selected credit spread for a representative debt instrument.",
    "dpi": "Distributions to paid-in capital multiple reported for the fund.",
    "rvpi": "Residual value to paid-in capital multiple reported for the fund.",
    "net_moic": "Net multiple on invested capital reported for the fund.",
    "net_irr": "Net internal rate of return reported for the fund.",
}


def _table_context(table_name: str) -> tuple[str, str]:
    if table_name in TABLE_CONTEXT:
        return TABLE_CONTEXT[table_name]
    if table_name.startswith("source_"):
        return (
            f"Normalized source snapshot for {table_name.removeprefix('source_').replace('_', ' ')}",
            "the native source-record grain",
        )
    if table_name.startswith("xref_"):
        return (
            f"Identity and lineage crosswalk for {table_name.removeprefix('xref_').replace('_', ' ')}",
            "one row per resolved source-to-canonical relationship",
        )
    if table_name.startswith("control_"):
        return ("Website crawler run controls", "one row per crawl run or sponsor attempt")
    if table_name.startswith("validation_"):
        return ("PE sponsor validation results", "one row per validation scope")
    return ("PE sponsor external-data product", "the table's declared business grain")


def _humanize(column_name: str) -> str:
    return re.sub(r"\s+", " ", column_name.replace("_", " ")).strip()


def build_column_comment(
    table_name: str,
    column_name: str,
    data_type: str,
) -> str:
    context, grain = _table_context(table_name)
    if column_name in EXACT_COLUMN_COMMENTS:
        definition = EXACT_COLUMN_COMMENTS[column_name]
    else:
        label = _humanize(column_name)
        if column_name.endswith(("_id", "_key")):
            definition = f"Identifier or source key for {label}."
        elif column_name.endswith("_count"):
            definition = f"Count of {label.removesuffix(' count')} records at this row's grain."
        elif column_name.endswith(("_flag", "_is_active")) or column_name.startswith(
            ("is_", "has_")
        ):
            definition = f"Boolean indicator for {label}."
        elif column_name.endswith(("_date", "_at", "_datetime")):
            definition = f"Date or timestamp for {label}; null means the event was not observed."
        elif column_name.endswith(("_pct", "_percentage")):
            definition = f"Percentage measure for {label}; interpret using the source's stated scale."
        elif column_name.endswith("_usd_m"):
            definition = f"{label.capitalize()} expressed in USD millions."
        elif column_name.endswith("_usd_bn"):
            definition = f"{label.capitalize()} expressed in USD billions."
        elif "confidence" in column_name:
            definition = f"Confidence classification assigned to {label}."
        elif "score" in column_name:
            definition = f"Numeric matching or quality score for {label}; higher values indicate stronger evidence."
        elif "status" in column_name:
            definition = f"Processing or business status for {label}."
        else:
            definition = f"{label.capitalize()} recorded for this row."
    return (
        f"{definition} Context: {context}. Grain: {grain}. "
        f"Databricks type: {str(data_type).upper()}."
    )


def sponsor_comment_targets(
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> tuple[set[str], set[str]]:
    dbo_tables = {
        prefixed_name(name, table_prefix)
        for name in EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.values()
    } | {
        prefixed_name(name, table_prefix)
        for name in WEBSITE_CRAWL_TABLES
    }
    return dbo_tables, set(GOLD_SPONSOR_VIEWS)


def apply_and_capture_sponsor_column_comments(
    *,
    catalog: str,
    dbo_schema: str = "dbo",
    gold_schema: str = "gold",
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    comment_table: str = "dbo.all_column_comments",
    dbo_tables: set[str] | None = None,
    gold_views: set[str] | None = None,
) -> pd.DataFrame:
    default_dbo_tables, default_gold_views = sponsor_comment_targets(table_prefix)
    dbo_tables = default_dbo_tables if dbo_tables is None else set(dbo_tables)
    gold_views = default_gold_views if gold_views is None else set(gold_views)
    records = []
    for schema_name, object_names in (
        (dbo_schema, dbo_tables),
        (gold_schema, gold_views),
    ):
        if not object_names:
            continue
        quoted = ", ".join(
            "'" + name.replace("'", "''") + "'"
            for name in sorted(object_names)
        )
        columns = readdfsql(
            f"""
SELECT table_name, column_name, data_type
FROM {catalog}.information_schema.columns
WHERE table_schema = '{schema_name}'
  AND table_name IN ({quoted})
ORDER BY table_name, ordinal_position
""".strip(),
            app=app,
        )
        found = set(columns["table_name"].astype(str))
        missing = set(object_names) - found
        if missing:
            raise ValueError(
                f"Cannot comment missing {catalog}.{schema_name} objects: "
                f"{sorted(missing)}"
            )
        for row in columns.itertuples(index=False):
            comment = build_column_comment(
                str(row.table_name).removeprefix(table_prefix),
                str(row.column_name),
                str(row.data_type),
            )
            escaped = comment.replace("'", "''")
            execsql(
                f"COMMENT ON COLUMN `{catalog}`.`{schema_name}`."
                f"`{row.table_name}`.`{row.column_name}` IS '{escaped}'",
                app=app,
                msg=f"comment {schema_name}.{row.table_name}.{row.column_name}",
            )
            records.append({
                "col_name": row.column_name,
                "table_name": f"{catalog}.{schema_name}.{row.table_name}",
                "data_type": row.data_type,
                "comment": comment,
            })

    if not records:
        raise ValueError("At least one PE sponsor comment target is required")
    target_names = sorted({record["table_name"] for record in records})
    targets_sql = ", ".join(
        "'" + name.replace("'", "''") + "'" for name in target_names
    )
    execsql(
        f"""
UPDATE {comment_table}
SET is_latest = false
WHERE is_latest = true
  AND table_name IN ({targets_sql})
""".strip(),
        app=app,
        msg="retire old PE sponsor column-comment snapshots",
    )
    capture_conditions = []
    if dbo_tables:
        capture_conditions.append(
            f"(table_schema = '{dbo_schema}' AND table_name IN "
            f"({', '.join(repr(name) for name in sorted(dbo_tables))}))"
        )
    if gold_views:
        capture_conditions.append(
            f"(table_schema = '{gold_schema}' AND table_name IN "
            f"({', '.join(repr(name) for name in sorted(gold_views))}))"
        )
    execsql(
        f"""
INSERT INTO {comment_table}
    (col_name, table_name, data_type, comment, tags, knowledge_date, is_latest)
SELECT
    column_name,
    CONCAT(table_catalog, '.', table_schema, '.', table_name),
    data_type,
    comment,
    '',
    current_timestamp(),
    true
FROM {catalog}.information_schema.columns
WHERE {" OR ".join(capture_conditions)}
""".strip(),
        app=app,
        msg="capture PE sponsor column-comment snapshots",
    )
    return pd.DataFrame(records)
