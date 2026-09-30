import logging
from time import perf_counter

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed

from common.utils.memutil import get_process_memory_gb
from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.consolidated_group_debt import GROUP_DEBT_COLUMNS
from data_access.dbtables.sponsor_study.factset_filing_attribution import DCS_ATTRIBUTION_COLUMNS
from data_access.dbtables.sponsor_study.factset_debt_attribution import (
    DEBT_ATTRIBUTION_COLUMNS, admitted_factset_debt_rows, annotate_factset_debt_attribution,
)
from data_access.dbtables.sponsor_study.findox_coverage import COVERAGE_COLUMNS, fetch_findox_model_coverage
from data_access.dbtables.sponsor_study.fund_vendor import _match_key
from data_importer.sponsor_study.constants import STUDY_ASOF_DATE


FACTSET_ENTITY_SQL = """
SELECT DISTINCT
    CAST(factset_entity_id AS STRING) AS factset_entity_id,
    CAST(entity_proper_name AS STRING) AS factset_entity_name,
    CAST(iso_country AS STRING) AS factset_iso_country,
    CAST(entity_type AS STRING) AS factset_entity_type
FROM {catalog}.ent_v1.ent_entity_coverage
WHERE lower(CAST(entity_proper_name AS STRING)) IN ({names})
"""

TABLE_COLUMNS_SQL = "SELECT * FROM {table_name} LIMIT 0"
FACTSET_ENTITY_STRUCTURE_BATCH_SIZE = 250
FACTSET_ENTITY_TREE_RELATIONS = (
    (
        "factset_entity_id",
        "debt_factset_entity_id",
        "factset_direct_debt_entity",
        "same_entity",
        1,
    ),
    (
        "factset_entity_id",
        "debt_parent_factset_entity_id",
        "factset_debt_entity_descendant",
        "debt_entity_child_of_portco",
        2,
    ),
    (
        "factset_entity_id",
        "debt_ultimate_factset_entity_id",
        "factset_debt_entity_descendant",
        "debt_entity_under_portco_ultimate",
        3,
    ),
    (
        "portco_parent_factset_entity_id",
        "debt_factset_entity_id",
        "factset_portco_under_debt_entity",
        "portco_child_of_debt_entity",
        4,
    ),
    (
        "portco_ultimate_factset_entity_id",
        "debt_factset_entity_id",
        "factset_portco_under_debt_entity",
        "portco_under_debt_entity_ultimate",
        5,
    ),
)

DEBT_FEATURES_NAME_SQL = """
SELECT *
FROM {table_name}
WHERE cast({date_col} as date) = (
    SELECT max(cast({date_col} as date)) FROM {table_name}
    {cutoff_predicate}
)
  AND lower(CAST({issuer_expression} AS STRING)) IN ({names})
"""

LATEST_BY_FILTER_SQL = """
SELECT *
FROM {table_name}
WHERE cast({date_col} as date) = (
    SELECT max(cast({date_col} as date)) FROM {table_name}
    {cutoff_predicate}
)
  AND ({where_clause})
"""

DEBT_FEATURE_ISSUER_INDEX_SQL = """
SELECT DISTINCT
    CAST({issuer_expression} AS STRING) AS issuer_label
FROM {table_name}
WHERE cast({date_col} as date) = (
    SELECT max(cast({date_col} as date)) FROM {table_name}
    {cutoff_predicate}
)
  AND {issuer_expression} IS NOT NULL
"""

DEBT_FEATURES_FACTSET_ENTITY_MAP_SQL = """
WITH latest_date AS (
    SELECT max(cast({date_col} AS date)) AS asof_date
    FROM {table_name}
    {cutoff_predicate}
),
debt_base AS (
    SELECT
        *,
        CAST(COALESCE(
            instrument_id_uniq,
            instrument_id,
            isin,
            bbg_id,
            fsym_id,
            fsym_security_id,
            ticker_region,
            ticker,
            issuer_name
        ) AS STRING) AS debt_feature_key
    FROM {table_name}
    WHERE cast({date_col} AS date) = (SELECT asof_date FROM latest_date)
      AND lower(CAST(COALESCE(instrument_type, '') AS STRING)) IN ('bond', 'loan', 'cds')
),
debt_feature_fsym AS (
    SELECT debt_feature_key, CAST(fsym_id AS STRING) AS fsym_id, 'debt_features.fsym_id' AS debt_bridge_source
    FROM debt_base
    WHERE fsym_id IS NOT NULL
    UNION ALL
    SELECT debt_feature_key, CAST(fsym_security_id AS STRING) AS fsym_id, 'debt_features.fsym_security_id' AS debt_bridge_source
    FROM debt_base
    WHERE fsym_security_id IS NOT NULL
    UNION ALL
    SELECT debt_feature_key, CAST(fsym_primary_equity_id AS STRING) AS fsym_id, 'debt_features.fsym_primary_equity_id' AS debt_bridge_source
    FROM debt_base
    WHERE fsym_primary_equity_id IS NOT NULL
    UNION ALL
    SELECT debt_feature_key, CAST(fsym_regional_id AS STRING) AS fsym_id, 'debt_features.fsym_regional_id' AS debt_bridge_source
    FROM debt_base
    WHERE fsym_regional_id IS NOT NULL
    UNION ALL
    SELECT debt_feature_key, CAST(fsym_id_eq_pricing AS STRING) AS fsym_id, 'debt_features.fsym_id_eq_pricing' AS debt_bridge_source
    FROM debt_base
    WHERE fsym_id_eq_pricing IS NOT NULL
),
debt_isin_fsym AS (
    SELECT db.debt_feature_key, CAST(si.fsym_id AS STRING) AS fsym_id, 'sym_isin.isin' AS debt_bridge_source
    FROM debt_base db
    JOIN {factset_catalog}.sym_v1.sym_isin si
        ON upper(CAST(si.isin AS STRING)) = upper(CAST(db.isin AS STRING))
    WHERE db.isin IS NOT NULL
    UNION ALL
    SELECT db.debt_feature_key, CAST(si.fsym_id AS STRING) AS fsym_id, 'sym_isin.instrument_id' AS debt_bridge_source
    FROM debt_base db
    JOIN {factset_catalog}.sym_v1.sym_isin si
        ON upper(CAST(si.isin AS STRING)) = upper(CAST(db.instrument_id AS STRING))
    WHERE db.instrument_id IS NOT NULL
),
debt_ticker_fsym AS (
    SELECT db.debt_feature_key, CAST(tr.fsym_id AS STRING) AS fsym_id, 'sym_ticker_region.ticker_region' AS debt_bridge_source
    FROM debt_base db
    JOIN {factset_catalog}.sym_v1.sym_ticker_region tr
        ON upper(CAST(tr.ticker_region AS STRING)) = upper(CAST(db.ticker_region AS STRING))
    WHERE db.ticker_region IS NOT NULL
),
debt_bbg_fsym AS (
    SELECT db.debt_feature_key, CAST(sb.fsym_id AS STRING) AS fsym_id, 'sym_bbg.bbg_id' AS debt_bridge_source
    FROM debt_base db
    JOIN {factset_catalog}.sym_v1.sym_bbg sb
        ON upper(CAST(sb.bbg_id AS STRING)) = upper(CAST(db.bbg_id AS STRING))
    WHERE db.bbg_id IS NOT NULL
    UNION ALL
    SELECT db.debt_feature_key, CAST(sb.fsym_id AS STRING) AS fsym_id, 'sym_bbg.instrument_id' AS debt_bridge_source
    FROM debt_base db
    JOIN {factset_catalog}.sym_v1.sym_bbg sb
        ON upper(CAST(sb.bbg_id AS STRING)) = upper(CAST(db.instrument_id AS STRING))
    WHERE db.instrument_id IS NOT NULL
),
debt_fsym_raw AS (
    SELECT DISTINCT debt_feature_key, fsym_id, debt_bridge_source
    FROM (
        SELECT debt_feature_key, fsym_id, debt_bridge_source FROM debt_feature_fsym
        UNION ALL
        SELECT debt_feature_key, fsym_id, debt_bridge_source FROM debt_isin_fsym
        UNION ALL
        SELECT debt_feature_key, fsym_id, debt_bridge_source FROM debt_ticker_fsym
        UNION ALL
        SELECT debt_feature_key, fsym_id, debt_bridge_source FROM debt_bbg_fsym
    ) fsym_union
    WHERE fsym_id IS NOT NULL AND trim(fsym_id) <> ''
),
debt_fsym AS (
    SELECT debt_feature_key, fsym_id, debt_bridge_source
    FROM debt_fsym_raw
    UNION ALL
    SELECT
        raw.debt_feature_key,
        CAST(cov.fsym_security_id AS STRING) AS fsym_id,
        CONCAT(raw.debt_bridge_source, '->sym_coverage.fsym_security_id') AS debt_bridge_source
    FROM debt_fsym_raw raw
    JOIN {factset_catalog}.sym_v1.sym_coverage cov
        ON cov.fsym_id = raw.fsym_id
    WHERE cov.fsym_security_id IS NOT NULL
),
dated_security_issuers AS (
    SELECT * FROM {factset_catalog}.sym_v1.sym_sec_entity_hist
    WHERE (SELECT asof_date FROM latest_date) BETWEEN COALESCE(CAST(start_date AS DATE), DATE '1900-01-01')
        AND COALESCE(CAST(end_date AS DATE), DATE '9999-12-31')
    QUALIFY MIN(factset_entity_id) OVER (PARTITION BY fsym_id) = MAX(factset_entity_id) OVER (PARTITION BY fsym_id)
),
dated_credit_parents AS (
    SELECT * FROM {factset_catalog}.sym_v1.sym_credit_parent_hist
    WHERE (SELECT asof_date FROM latest_date) BETWEEN COALESCE(CAST(start_date AS DATE), DATE '1900-01-01')
        AND COALESCE(CAST(end_date AS DATE), DATE '9999-12-31')
    QUALIFY MIN(factset_cred_parent_entity_id) OVER (PARTITION BY fsym_id) = MAX(factset_cred_parent_entity_id) OVER (PARTITION BY fsym_id)
),
security_evidence AS (
    SELECT f.*, se.factset_entity_id AS current_security_entity_id,
        f.fsym_id AS debt_security_factset_id,
        h.factset_entity_id AS debt_security_entity_id,
        cp.factset_cred_parent_entity_id AS debt_credit_parent_entity_id,
        COALESCE(cov.fref_security_type IN ('BANK_LOAN','CORP','MUNI'), false) AS debt_security_is_physical,
        CAST(h.start_date AS DATE) AS debt_security_start_date, CAST(h.end_date AS DATE) AS debt_security_end_date,
        CAST(cp.start_date AS DATE) AS debt_credit_start_date, CAST(cp.end_date AS DATE) AS debt_credit_end_date
    FROM debt_fsym f
    LEFT JOIN dated_security_issuers h ON h.fsym_id=f.fsym_id
    LEFT JOIN dated_credit_parents cp ON cp.fsym_id=f.fsym_id
    LEFT JOIN {factset_catalog}.sym_v1.sym_sec_entity se ON se.fsym_id=f.fsym_id
    LEFT JOIN {factset_catalog}.sym_v1.sym_coverage cov ON cov.fsym_id=f.fsym_id
),
debt_entities AS (
    SELECT DISTINCT
        e.*,
        CAST(COALESCE(debt_credit_parent_entity_id, current_security_entity_id) AS STRING) AS debt_factset_entity_id
    FROM security_evidence e
    WHERE COALESCE(debt_credit_parent_entity_id, current_security_entity_id) IS NOT NULL
    UNION ALL
    SELECT DISTINCT
        e.*,
        CAST(COALESCE(debt_security_entity_id, current_security_entity_id) AS STRING) AS debt_factset_entity_id
    FROM security_evidence e
    WHERE COALESCE(debt_security_entity_id, current_security_entity_id) IS NOT NULL
      AND debt_security_is_physical
),
entity_family_size AS (
    SELECT factset_entity_id, max(member_count) AS member_count
    FROM (
        SELECT
            CAST(factset_parent_entity_id AS STRING) AS factset_entity_id,
            count(DISTINCT factset_entity_id) AS member_count
        FROM {factset_catalog}.ent_v1.ent_entity_structure
        WHERE factset_parent_entity_id IS NOT NULL
        GROUP BY CAST(factset_parent_entity_id AS STRING)
        UNION ALL
        SELECT
            CAST(factset_ultimate_parent_entity_id AS STRING) AS factset_entity_id,
            count(DISTINCT factset_entity_id) AS member_count
        FROM {factset_catalog}.ent_v1.ent_entity_structure
        WHERE factset_ultimate_parent_entity_id IS NOT NULL
        GROUP BY CAST(factset_ultimate_parent_entity_id AS STRING)
    ) family
    WHERE factset_entity_id IS NOT NULL
    GROUP BY factset_entity_id
)
SELECT
    db.*,
    de.debt_factset_entity_id,
    CAST(es.factset_parent_entity_id AS STRING) AS debt_parent_factset_entity_id,
    CAST(es.factset_ultimate_parent_entity_id AS STRING) AS debt_ultimate_factset_entity_id,
    de.debt_bridge_source,
    de.debt_security_factset_id, de.debt_security_entity_id, de.debt_credit_parent_entity_id,
    de.debt_security_is_physical, de.debt_security_start_date, de.debt_security_end_date,
    de.debt_credit_start_date, de.debt_credit_end_date,
    COALESCE(efs.member_count, 1) AS debt_entity_family_size
FROM debt_base db
JOIN debt_entities de
    ON de.debt_feature_key = db.debt_feature_key
LEFT JOIN {factset_catalog}.ent_v1.ent_entity_structure es
    ON es.factset_entity_id = de.debt_factset_entity_id
LEFT JOIN entity_family_size efs
    ON efs.factset_entity_id = de.debt_factset_entity_id
"""

FACTSET_ENTITY_STRUCTURE_SQL = """
SELECT DISTINCT
    CAST(es.factset_entity_id AS STRING) AS factset_entity_id,
    CAST(es.factset_parent_entity_id AS STRING) AS portco_parent_factset_entity_id,
    CAST(es.factset_ultimate_parent_entity_id AS STRING) AS portco_ultimate_factset_entity_id
FROM {catalog}.ent_v1.ent_entity_structure es
WHERE CAST(es.factset_entity_id AS STRING) IN ({entity_ids})
"""

SECURITY_PRICE_SQL = """
SELECT *
FROM {table_name}
WHERE sec_id IN ({sec_ids})
  {cutoff_predicate}
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY sec_id
    ORDER BY asof_date DESC NULLS LAST
) = 1
"""

FACTSET_DCS_SQL = """
WITH latest_filings AS (
    SELECT * FROM {catalog}.dcs_v2.dcs_filings
    WHERE CAST(factset_entity_id AS STRING) IN ({factset_entity_ids})
      AND CAST(report_date AS DATE) <= DATE '{asof_cutoff}'
      AND CAST(COALESCE(publication_date, report_date) AS DATE) <= DATE '{asof_cutoff}'
    QUALIFY DENSE_RANK() OVER (
        PARTITION BY factset_entity_id ORDER BY report_date DESC
    ) = 1
)
SELECT
    CAST(dd.detail_id AS STRING) AS dcs_detail_id,
    CAST(dd.factset_entity_id AS STRING) AS factset_entity_id,
    CAST(dd.instrument_id AS STRING) AS factset_instrument_id,
    CAST(ec.entity_proper_name AS STRING) AS factset_entity_name,
    CAST(dd.report_date AS DATE) AS report_date,
    CAST(df.publication_date AS DATE) AS publication_date,
    CAST(dd.currency AS STRING) AS currency,
    CAST(COALESCE(df.unit_amount_code, df.dm_unit_amount_code) AS STRING) AS unit_amount_code,
    CAST(dd.instrument_description AS STRING) AS instrument_description,
    CAST(dd.issue_date AS DATE) AS issue_date,
    CASE WHEN COALESCE(dd.maturity_date_range_flag, false) = false
              AND dd.maturity_year IS NOT NULL
              AND dd.maturity_year_month IS NOT NULL
              AND dd.maturity_year_day IS NOT NULL
         THEN TRY_CAST(CONCAT(
             CAST(CAST(dd.maturity_year AS INT) AS STRING), '-',
             LPAD(CAST(CAST(dd.maturity_year_month AS INT) AS STRING), 2, '0'), '-',
             LPAD(CAST(CAST(dd.maturity_year_day AS INT) AS STRING), 2, '0')
         ) AS DATE)
    END AS maturity_date,
    CAST(dd.os_amount AS DOUBLE) AS amount_outstanding,
    CAST(dd.avail_amount AS DOUBLE) AS available_amount,
    CAST(dd.borrowing_base AS DOUBLE) AS borrowing_base,
    CAST(dmap.issue_type AS STRING) AS issue_type,
    CAST(dmap.debt_desc AS STRING) AS debt_description,
    CAST(dsmap.seniority_desc AS STRING) AS seniority_description,
    CAST(isn.isin AS STRING) AS isin,
    CAST(NULL AS STRING) AS bbg_id
FROM {catalog}.dcs_v2.dcs_details dd
JOIN latest_filings df
    ON df.factset_entity_id = dd.factset_entity_id
    AND df.report_date = dd.report_date
    AND df.currency = dd.currency
LEFT JOIN {catalog}.ent_v1.ent_entity_coverage ec
    ON ec.factset_entity_id = dd.factset_entity_id
LEFT JOIN {catalog}.sym_v1.sym_isin isn
    ON isn.fsym_id = dd.instrument_id
LEFT JOIN {catalog}.ref_v2.dcs_debt_map dmap
    ON dmap.debt_code = dd.debt_code
LEFT JOIN {catalog}.ref_v2.dcs_seniority_map dsmap
    ON dsmap.seniority_code = dmap.seniority_code
WHERE dd.active = true
  AND CAST(dd.factset_entity_id AS STRING) IN ({factset_entity_ids})
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dd.factset_entity_id, dd.instrument_id
    ORDER BY dd.report_date DESC NULLS LAST, dd.os_amount DESC NULLS LAST
) = 1
"""

FACTSET_ENTITY_RELATIVES_SQL = """
SELECT DISTINCT
    CAST(es.factset_entity_id AS STRING) AS factset_entity_id,
    CAST(es.factset_parent_entity_id AS STRING) AS factset_parent_entity_id,
    CAST(es.factset_ultimate_parent_entity_id AS STRING) AS factset_ultimate_parent_entity_id
FROM {catalog}.ent_v1.ent_entity_structure es
WHERE CAST(es.factset_entity_id AS STRING) IN ({entity_ids})
   OR CAST(es.factset_parent_entity_id AS STRING) IN ({entity_ids})
   OR CAST(es.factset_ultimate_parent_entity_id AS STRING) IN ({entity_ids})
"""

FINDOX_PUBLIC_DOCUMENT_INDEX_SQL = """
SELECT DISTINCT
    CAST(holding_deal_issuer_id AS STRING) AS findox_issuer_id,
    CAST(deal_doc_id AS STRING) AS document_id,
    CAST(full_filename AS STRING) AS document_name,
    CAST(NULL AS TIMESTAMP) AS document_date,
    CAST(COALESCE(system_folder_name, folder_path) AS STRING) AS document_type,
    CAST(false AS BOOLEAN) AS is_private,
    'findox' AS source_system
FROM dbo.findox_core_documents
WHERE CAST(holding_deal_issuer_id AS STRING) IN ({issuer_ids})
  AND LOWER(TRIM(CAST(is_private AS STRING))) IN ('false', '0', 'no', 'n')
  AND LOWER(TRIM(CAST(deal_doc_is_private AS STRING))) IN ('false', '0', 'no', 'n')
  AND deal_doc_id IS NOT NULL
"""

FACTSET_FX_RATES_USD_SQL = """
SELECT
    CAST(iso_currency AS STRING) AS iso_currency,
    CAST(exch_date AS DATE) AS fx_rate_date,
    CAST(exch_rate_usd AS DOUBLE) AS exch_rate_usd
FROM {catalog}.ref_v2.econ_fx_rates_usd
WHERE CAST(exch_date AS DATE) <= DATE '{asof_cutoff}'
  AND exch_rate_usd IS NOT NULL
  AND exch_rate_usd > 0
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY iso_currency
    ORDER BY CAST(exch_date AS DATE) DESC
) = 1
"""

# Month-end history rather than one rate per currency: a reported figure has to be converted at the
# rate effective on its own period end date, not at the rate on the study cutoff.
FACTSET_FX_RATE_HISTORY_SQL = """
SELECT
    CAST(iso_currency AS STRING) AS iso_currency,
    CAST(exch_date AS DATE) AS fx_rate_date,
    CAST(exch_rate_usd AS DOUBLE) AS exch_rate_usd
FROM {catalog}.ref_v2.econ_fx_rates_usd
WHERE CAST(exch_date AS DATE) <= DATE '{asof_cutoff}'
  AND exch_rate_usd IS NOT NULL
  AND exch_rate_usd > 0
  AND CAST(iso_currency AS STRING) IN ({currencies})
"""

XREF_COLUMNS = [
            "portco_id",
            "company_name",
    "ciq_identifier",
    "factset_entity_id",
            "findox_issuer_id",
    "findox_common_id",
            "lxid",
            "loanx_id",
            "bbg_company_id",
    "bbg_company_name",
            "dealogic_borrower_id",
    "first_lien_debt_identifier",
    "second_lien_unsecured_debt_identifier",
            "match_method",
            "match_confidence",
]

ACCEPTED_COMPANY_ALIAS_STATUSES = {"accepted_seed", "auto_matched", "manual_matched"}
STRICT_DEBT_BRIDGE_ALIAS_STATUSES = {"accepted_seed", "manual_matched"}
STRICT_DEBT_BRIDGE_ALIAS_METHODS = {"factset_pevc_fund_scoped_company_exact"}
PEVC_DEBT_BRIDGE_XREF_METHODS = {"factset_pevc_fund_portco", "factset_pevc_company_alias"}

INTERNAL_DEBT_FEATURE_COLUMNS = [
        "portco_id",
        "instrument_id_uniq",
        "asof_date",
        "feature_name",
        "feature_value",
        "source_system",
    "bridge_provenance",
    *GROUP_DEBT_COLUMNS,
]

INTERNAL_FINANCIAL_COLUMNS = [
    "portco_id",
    "asof_date",
    "period_end_date",
    "company_corp_ticker",
    "financial_identifier",
    "financial_identifier_type",
    "financial_provenance_json",
    "financial_comparability_json",
    "financial_review_required",
    "current_total_debt",
    "current_total_cash",
    "current_net_debt",
    "current_ltm_revenue",
    "current_ltm_ebitda",
    "current_ebitda_margin",
    "current_gross_leverage",
    "current_net_leverage",
    "yoy_revenue_growth",
    "yoy_ebitda_growth",
    "ltv",
    "source_system",
]

INTERNAL_MARKET_COLUMNS = [
    "portco_id",
    "instrument_id_uniq",
    "asof_date",
    "tranche_type",
    "price",
    "ytw",
    "ytm",
    "spread",
    "duration",
    "price_source",
    "source_system",
    "bridge_provenance",
    *GROUP_DEBT_COLUMNS,
]

# `debt_features` publishes yields and duration under Bloomberg's convention-qualified names.
# The bare names are kept ahead of them so a source that does use them still wins.
_YTW_COLUMNS = ["ytw", "yield_to_worst", "yield_to_worst_conv"]
_YTM_COLUMNS = ["ytm", "yield", "yield_to_maturity", "yield_to_maturity_conv"]
_DURATION_COLUMNS = ["duration", "modified_duration", "effective_duration"]

INTERNAL_DEBT_INSTRUMENT_COLUMNS = [
    "portco_id",
    "instrument_id_uniq",
    "asof_date",
    "tranche_type",
    "tranche_size",
    "rate",
    "maturity_date",
    "nearest_maturity_date",
    "nearest_maturity_type",
    "source_system",
    "bridge_provenance",
    *GROUP_DEBT_COLUMNS,
]

INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS = [
    "portco_id",
    "portco_factset_entity_id",
    "debt_factset_entity_id",
    "debt_parent_factset_entity_id",
    "debt_ultimate_factset_entity_id",
    "debt_feature_key",
    "instrument_id_uniq",
    "instrument_id",
    "isin",
    "bbg_id",
    "fsym_id",
    "fsym_security_id",
    "ticker",
    "ticker_region",
    "issuer_name",
    "instrument_type",
    "debt_bridge_match_method",
    "debt_bridge_confidence",
    "debt_bridge_relation",
    "debt_bridge_source",
    "debt_bridge_source_system",
    *DEBT_ATTRIBUTION_COLUMNS,
]

FACTSET_DCS_COLUMNS = [
    "portco_id",
    "factset_entity_id",
    "factset_instrument_id",
    "dcs_detail_id",
    "report_date",
    "publication_date",
    "currency",
    "instrument_description",
    "issue_date",
    "maturity_date",
    "amount_outstanding",
    "available_amount",
    "borrowing_base",
    "issue_type",
    "debt_description",
    "seniority_description",
    "isin",
    "bbg_id",
    *DCS_ATTRIBUTION_COLUMNS,
    *GROUP_DEBT_COLUMNS,
]


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _norm(value) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().split())


def _first_present(row: pd.Series, columns: list[str]):
    for col in columns:
        if col in row and pd.notna(row[col]) and str(row[col]).strip():
            return row[col]
    return pd.NA


def _debt_features_source_system(row: pd.Series) -> str:
    bridge_sys = str(row.get("debt_bridge_source_system") or "").strip().lower()
    bridge_sources = {
        "findox_core_identifier": "debt_features_findox_core_identifier",
        "consolidated_group": "debt_features_consolidated_group",
        "factset_entity_tree": "debt_features_factset_entity_tree",
        "factset_dcs_instrument": "debt_features_factset_dcs_instrument",
        "factset_loan_security": "debt_features_factset_loan_security",
        "factset_loan_borrower": "debt_features_factset_loan_borrower",
        "factset_findox_borrower": "debt_features_factset_findox_borrower",
        "factset_direct_group": "debt_features_factset_direct_group",
        "reported_loan_deal": "debt_features_reported_loan_deal",
    }
    if bridge_sys in bridge_sources:
        return bridge_sources[bridge_sys]
    if pd.notna(row.get("portco_factset_entity_id")) and str(row.get("portco_factset_entity_id")).strip():
        return "debt_features_factset_entity_tree"
    if pd.notna(row.get("debt_factset_entity_id")) and str(row.get("debt_factset_entity_id")).strip():
        return "debt_features_factset_entity_tree"
    return "debt_features"


def _financial_values_from_debt_features_row(row: pd.Series) -> dict:
    from data_access.dbtables.sponsor_study.financial_waterfall import DEBT_METRIC_COLUMNS

    # The producer retains local FF amounts beside its explicit USD conversions.
    # Serving financial amounts are USD millions; preserve the converted values.
    total_debt = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_total_debt"]), errors="coerce")
    cash = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_total_cash"]), errors="coerce")
    net_debt = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_net_debt"]), errors="coerce")
    if pd.isna(net_debt) and pd.notna(total_debt) and pd.notna(cash):
        net_debt = total_debt - cash
    revenue = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_ltm_revenue"]), errors="coerce")
    ebitda = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_ltm_ebitda"]), errors="coerce")
    gross_leverage = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_gross_leverage"]), errors="coerce")
    net_leverage = pd.to_numeric(_first_present(row, DEBT_METRIC_COLUMNS["current_net_leverage"]), errors="coerce")
    if pd.isna(gross_leverage) and pd.notna(total_debt) and pd.notna(ebitda) and ebitda:
        gross_leverage = total_debt / ebitda
    if pd.isna(net_leverage) and pd.notna(net_debt) and pd.notna(ebitda) and ebitda:
        net_leverage = net_debt / ebitda
    enterprise_value = pd.to_numeric(_first_present(row, [
        "ff_entrpr_val_usd", "ff_entrpr_val", "ff_entrpr_val_findox", "ff_entrpr_val_factset",
    ]), errors="coerce")
    ltv = pd.NA
    if pd.notna(total_debt) and pd.notna(enterprise_value) and enterprise_value:
        ltv = total_debt / enterprise_value
    margin = pd.NA
    if pd.notna(ebitda) and pd.notna(revenue) and revenue:
        margin = ebitda / revenue
    return {
        "current_total_debt": total_debt,
        "current_total_cash": cash,
        "current_net_debt": net_debt,
        "current_ltm_revenue": revenue,
        "current_ltm_ebitda": ebitda,
        "current_ebitda_margin": margin,
        "current_gross_leverage": gross_leverage,
        "current_net_leverage": net_leverage,
        "yoy_revenue_growth": pd.NA,
        "yoy_ebitda_growth": pd.NA,
        "ltv": ltv,
    }


def _financial_identifier_from_debt_features_row(row: pd.Series) -> tuple[str | None, str | None]:
    from data_access.dbtables.sponsor_study.issuer_tree_adapter import normalize_bbg_company_id

    for column, identifier_type in [
        ("company_corp_ticker", "company_corp_ticker"),
        ("id_bb_company", "id_bb_company"),
        ("debt_factset_entity_id", "factset_entity_id"),
        ("ticker_region", "factset_ticker_region"),
        ("bbg_id", "bbg_id"),
        ("instrument_id_uniq", "instrument_id_uniq"),
        ("instrument_id", "instrument_id"),
    ]:
        value = row.get(column)
        if pd.notna(value) and str(value).strip():
            identifier = normalize_bbg_company_id(value) if identifier_type == "id_bb_company" else str(value).strip()
            if identifier:
                return identifier, identifier_type
    return None, None


def _first_existing(df: pd.DataFrame, columns: list[str]) -> str | None:
    lowered = {str(col).lower(): col for col in df.columns}
    for col in columns:
        if col.lower() in lowered:
            return lowered[col.lower()]
    return None


def _coalesce_existing(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    existing = [
        actual
        for name in columns
        if (actual := _first_existing(df, [name])) is not None
    ]
    if not existing:
        return pd.Series(pd.NA, index=df.index, dtype="string")
    return df[existing].bfill(axis=1).iloc[:, 0]


def _clean_id_series(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})


def _clean_id_value(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _clean_terminal_identifier(value) -> str:
    text = _clean_id_value(value)
    if not text or text in {"-", "—"}:
        return ""
    lowered = text.lower()
    for suffix in (" corp", " equity"):
        if lowered.endswith(suffix):
            return text[:-len(suffix)].strip()
    return text


def _quote_values(values: list[str]) -> str:
    clean = sorted({str(v).strip().lower().replace("'", "''") for v in values if pd.notna(v) and str(v).strip()})
    return ",".join(f"'{v}'" for v in clean) or "''"


def _quote_id_values(values) -> str:
    clean = sorted({str(v).strip().replace("'", "''") for v in values if pd.notna(v) and str(v).strip()})
    return ",".join(f"'{v}'" for v in clean) or "''"


def _quote_sql_value(value) -> str:
    return "'" + str(value).strip().replace("'", "''") + "'"


def _factset_portco_seed_rows(xref: pd.DataFrame) -> pd.DataFrame:
    columns = ["portco_id", "factset_entity_id"]
    if xref.empty or not set(columns).issubset(xref.columns):
        return pd.DataFrame(columns=columns)
    seed = xref[columns].copy()
    seed["portco_id"] = seed["portco_id"].map(_clean_id_value)
    seed["factset_entity_id"] = seed["factset_entity_id"].map(_clean_id_value)
    seed = seed[seed["portco_id"].ne("") & seed["factset_entity_id"].ne("")]
    return seed.drop_duplicates().reset_index(drop=True)


def _factset_entity_id_batches(entity_ids: list[str]) -> list[list[str]]:
    unique = list(dict.fromkeys(entity_ids))
    size = FACTSET_ENTITY_STRUCTURE_BATCH_SIZE
    batches = [
        unique[index:index + size]
        for index in range(0, len(unique), size)
    ]
    covered = [entity_id for batch in batches for entity_id in batch]
    if covered != unique:
        raise ValueError("FactSet entity-structure batches dropped or duplicated seed IDs")
    return batches


def _factset_debt_entity_map_sql(
    *,
    table_name: str,
    date_col: str,
    factset_catalog: str,
    asof_cutoff: str | pd.Timestamp | None,
) -> str:
    return DEBT_FEATURES_FACTSET_ENTITY_MAP_SQL.format(
        table_name=table_name,
        date_col=date_col,
        factset_catalog=factset_catalog,
        cutoff_predicate=_asof_cutoff_predicate(date_col, asof_cutoff),
    )


def preflight_factset_debt_entity_map_sql(
    sql: str,
    *,
    app: str = "databricks",
) -> None:
    readdfsql(f"EXPLAIN {sql}", app=app)


def resolve_factset_entity_tree_matches(
    seeds: pd.DataFrame,
    debt_features: pd.DataFrame,
    *,
    max_family_size: int = 25,
    keep_candidates: bool = False,
) -> pd.DataFrame:
    required_seed = {
        "portco_id",
        "factset_entity_id",
        "portco_parent_factset_entity_id",
        "portco_ultimate_factset_entity_id",
    }
    required_debt = {
        "debt_feature_key",
        "debt_factset_entity_id",
        "debt_parent_factset_entity_id",
        "debt_ultimate_factset_entity_id",
        "debt_bridge_source",
        "debt_entity_family_size",
    }
    if seeds.empty or debt_features.empty:
        return pd.DataFrame(columns=list(debt_features.columns) + [
            "portco_id",
            "portco_factset_entity_id",
            "debt_bridge_match_method",
            "debt_bridge_confidence",
            "debt_bridge_relation",
            "debt_bridge_source_system",
        ])
    missing_seed = required_seed - set(seeds.columns)
    missing_debt = required_debt - set(debt_features.columns)
    if missing_seed or missing_debt:
        raise ValueError(
            "FactSet entity-tree resolver is missing columns: "
            f"seed={sorted(missing_seed)} debt={sorted(missing_debt)}"
        )

    seed = seeds.copy()
    debt = debt_features.copy()
    id_columns = (
        required_seed
        | {
            "debt_feature_key",
            "debt_factset_entity_id",
            "debt_parent_factset_entity_id",
            "debt_ultimate_factset_entity_id",
        }
    )
    for column in id_columns:
        frame = seed if column in seed else debt
        frame[column] = frame[column].map(
            lambda value: _clean_id_value(value) or pd.NA
        )
    debt["debt_entity_family_size"] = pd.to_numeric(
        debt_features["debt_entity_family_size"],
        errors="coerce",
    ).fillna(1)
    debt["debt_bridge_source"] = debt_features["debt_bridge_source"].fillna("")
    seed_keys = {
        value
        for column in (
            "factset_entity_id",
            "portco_parent_factset_entity_id",
            "portco_ultimate_factset_entity_id",
        )
        for value in seed[column].dropna()
    }
    debt = debt[
        debt["debt_factset_entity_id"].isin(seed_keys)
        | debt["debt_parent_factset_entity_id"].isin(seed_keys)
        | debt["debt_ultimate_factset_entity_id"].isin(seed_keys)
    ]
    if debt.empty:
        return pd.DataFrame(columns=list(debt_features.columns) + [
            "portco_id",
            "portco_factset_entity_id",
            "debt_bridge_match_method",
            "debt_bridge_confidence",
            "debt_bridge_relation",
            "debt_bridge_source_system",
        ])

    empty_matches = pd.DataFrame(columns=list(debt_features.columns) + [
        "portco_id",
        "portco_factset_entity_id",
        "debt_bridge_match_method",
        "debt_bridge_confidence",
        "debt_bridge_relation",
        "debt_bridge_source_system",
    ])
    seed_cols = [
        "portco_id",
        "factset_entity_id",
        "portco_parent_factset_entity_id",
        "portco_ultimate_factset_entity_id",
    ]
    family_limit = int(max_family_size)
    ranked_parts = []
    claimed = pd.DataFrame(columns=["portco_id", "debt_feature_key"])
    for seed_col, debt_col, method, relation, rank in FACTSET_ENTITY_TREE_RELATIONS:
        left = seed.loc[seed[seed_col].notna(), seed_cols].drop_duplicates(
            ["portco_id", seed_col]
        )
        right = debt.loc[debt[debt_col].notna()]
        if rank > 3:
            right = right.loc[right["debt_entity_family_size"].le(family_limit)]
        if left.empty or right.empty:
            continue
        join_keys = pd.Index(left[seed_col]).intersection(right[debt_col])
        if join_keys.empty:
            continue
        left = left.loc[left[seed_col].isin(join_keys)].copy()
        right = right.loc[right[debt_col].isin(join_keys)].copy()
        left["_join_key"] = left[seed_col]
        right["_join_key"] = right[debt_col]
        merged = left.merge(right, on="_join_key", how="inner")
        if merged.empty:
            continue
        merged = merged.drop(columns=["_join_key"])
        if not keep_candidates and not claimed.empty:
            merged = merged.merge(
                claimed.assign(_claimed=True),
                on=["portco_id", "debt_feature_key"],
                how="left",
            )
            merged = merged.loc[merged["_claimed"].isna()].drop(columns=["_claimed"])
            if merged.empty:
                continue
        merged["debt_bridge_match_method"] = method
        merged["debt_bridge_relation"] = relation
        merged["match_rank"] = rank
        merged = merged.sort_values(
            ["portco_id", "debt_feature_key", "debt_entity_family_size", "debt_bridge_source"],
            kind="mergesort",
        )
        if not keep_candidates:
            merged = merged.drop_duplicates(["portco_id", "debt_feature_key"], keep="first")
        ranked_parts.append(merged)
        claimed = pd.concat(
            [claimed, merged[["portco_id", "debt_feature_key"]]],
            ignore_index=True,
        )
    if not ranked_parts:
        return empty_matches
    ranked = pd.concat(ranked_parts, ignore_index=True)
    ranked = ranked[
        ranked["match_rank"].le(3)
        | ranked["debt_entity_family_size"].le(family_limit)
    ]
    if ranked.empty:
        return ranked
    ranked["debt_bridge_confidence"] = ranked["match_rank"].le(3).map(
        {True: "high", False: "medium"}
    )
    ranked["portco_factset_entity_id"] = ranked["factset_entity_id"]
    ranked["debt_bridge_source_system"] = "factset_entity_tree"
    ranked = ranked.sort_values(
        ["portco_id", "debt_feature_key", "match_rank", "debt_entity_family_size", "debt_bridge_source"],
        kind="mergesort",
    )
    keep = [
        col for col in ranked.columns
        if col not in {
            "factset_entity_id",
            "portco_parent_factset_entity_id",
            "portco_ultimate_factset_entity_id",
            "match_rank",
        }
    ]
    if keep_candidates:
        return ranked[keep].reset_index(drop=True)
    return ranked.drop_duplicates(
        ["portco_id", "debt_feature_key"],
        keep="first",
    )[keep].reset_index(drop=True)


def _with_xref_columns(xref: pd.DataFrame) -> pd.DataFrame:
    out = xref.copy()
    for col in XREF_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    return out[XREF_COLUMNS]


def _unambiguous_name_lookup(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=["portco_id", "company_name"])
    out = rows[["portco_id", "company_name"]].dropna(subset=["portco_id", "company_name"]).drop_duplicates().copy()
    out["company_key"] = out["company_name"].map(_norm)
    out = out[out["company_key"].ne("")]
    unique_counts = out.groupby("company_key")["portco_id"].nunique()
    out = out[out["company_key"].isin(unique_counts[unique_counts.eq(1)].index)]
    return out[["portco_id", "company_name"]].drop_duplicates().reset_index(drop=True)


def build_portco_match_name_lookup(dataset: dict[str, pd.DataFrame], xref: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not xref.empty and {"portco_id", "company_name"}.issubset(xref.columns):
        rows.append(xref[["portco_id", "company_name"]])

    aliases = dataset.get("xref_company_alias_name_map", pd.DataFrame())
    if not aliases.empty:
        accepted = aliases[
            aliases["review_status"].isin(ACCEPTED_COMPANY_ALIAS_STATUSES)
            & aliases["source_company_name"].notna()
            & aliases["portco_id"].notna()
        ][["portco_id", "source_company_name"]].copy()
        accepted = accepted.rename(columns={"source_company_name": "company_name"})
        rows.append(accepted)

    if not rows:
        return pd.DataFrame(columns=["portco_id", "company_name"])
    return _unambiguous_name_lookup(pd.concat(rows, ignore_index=True))


def build_factset_debt_bridge_seed_xref(dataset: dict[str, pd.DataFrame], xref: pd.DataFrame) -> pd.DataFrame:
    if xref.empty:
        return _empty(XREF_COLUMNS)
    xref = _with_xref_columns(xref)
    seed_rows = []

    pevc_direct = xref[
        xref["match_method"].isin(PEVC_DEBT_BRIDGE_XREF_METHODS)
        & xref["factset_entity_id"].notna()
    ][["portco_id", "factset_entity_id"]].copy()

    aliases = dataset.get("xref_company_alias_name_map", pd.DataFrame())
    if not aliases.empty:
        required = {"portco_id", "source_system", "source_entity_id", "match_method", "review_status"}
        if required.issubset(aliases.columns):
            pevc_aliases = aliases[
                aliases["source_system"].eq("factset_pevc")
                & aliases["source_entity_id"].notna()
                & (
                    aliases["review_status"].isin(STRICT_DEBT_BRIDGE_ALIAS_STATUSES)
                    | (
                        aliases["review_status"].isin(ACCEPTED_COMPANY_ALIAS_STATUSES)
                        & aliases["match_method"].isin(STRICT_DEBT_BRIDGE_ALIAS_METHODS)
                    )
                )
            ][["portco_id", "source_entity_id"]].copy()
            pevc_aliases = pevc_aliases.rename(columns={"source_entity_id": "factset_entity_id"})
            seed_rows.append(pevc_aliases)
    else:
        seed_rows.append(pevc_direct)

    seed_rows.append(xref[
        xref["match_method"].eq("factset_pevc_fund_portco")
        & xref["factset_entity_id"].notna()
    ][["portco_id", "factset_entity_id"]].copy())

    seed = pd.concat(seed_rows, ignore_index=True) if seed_rows else pevc_direct
    if seed.empty:
        return _empty(XREF_COLUMNS)
    seed["factset_entity_id"] = seed["factset_entity_id"].map(_clean_id_value)
    seed = seed[seed["factset_entity_id"].ne("")]
    unique_counts = seed.groupby("portco_id")["factset_entity_id"].nunique()
    seed = seed[seed["portco_id"].isin(unique_counts[unique_counts.eq(1)].index)].drop_duplicates("portco_id")
    if seed.empty:
        return _empty(XREF_COLUMNS)
    out = xref.drop(columns=["factset_entity_id"], errors="ignore").merge(seed, on="portco_id", how="inner")
    out["match_method"] = "factset_pevc_entity_debt_bridge_seed"
    out["match_confidence"] = "high"
    return _with_xref_columns(out)


def fetch_findox_company_matches(company_names: list[str], app: str = "databricks") -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.findox_fundamentals import (
        fetch_findox_name_index, resolve_companies_to_findox,
    )
    companies = pd.DataFrame({"external_company_id": company_names, "company_name": company_names})
    matches, _ = resolve_companies_to_findox(companies, fetch_findox_name_index(app=app))
    return matches.rename(columns={"company_name": "findox_common_id_name"}).assign(
        findox_common_id=pd.NA)[["findox_issuer_id", "findox_common_id", "findox_common_id_name"]]


def fetch_factset_company_matches(company_names: list[str], catalog: str = "dbrx_factset_prod", app: str = "databricks") -> pd.DataFrame:
    names = _quote_values(company_names)
    logging.info("Fetching FactSet company matches for %s names", len(company_names))
    return readdfsql(FACTSET_ENTITY_SQL.format(catalog=catalog, names=names), app=app)


def fetch_table_columns(table_name: str, app: str = "databricks") -> list[str]:
    return list(readdfsql(TABLE_COLUMNS_SQL.format(table_name=table_name), app=app).columns)


def _asof_cutoff_predicate(date_col: str, asof_cutoff: str | pd.Timestamp | None) -> str:
    if asof_cutoff is None:
        return ""
    cutoff = pd.Timestamp(asof_cutoff).date().isoformat()
    return f"WHERE cast({date_col} as date) <= DATE '{cutoff}'"


def _issuer_expression(available: dict[str, str]) -> str:
    columns = [
        available[col]
        for col in ["issuer_name", "issuer", "company_name"]
        if col in available
    ]
    if not columns:
        return "NULL"
    return columns[0] if len(columns) == 1 else f"COALESCE({', '.join(columns)})"


def fetch_debt_feature_issuer_index(
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    source_columns = fetch_table_columns(table_name, app=app)
    available = {col.lower(): col for col in source_columns}
    if not any(col in available for col in ["issuer_name", "issuer", "company_name"]):
        return pd.DataFrame(columns=["issuer_label"])
    date_col = available.get("asof_date", "asof_date")
    sql = DEBT_FEATURE_ISSUER_INDEX_SQL.format(
        table_name=table_name,
        date_col=date_col,
        issuer_expression=_issuer_expression(available),
        cutoff_predicate=_asof_cutoff_predicate(date_col, asof_cutoff),
    )
    return readdfsql(sql, app=app)


def fetch_latest_debt_features(
    company_names: list[str],
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    *,
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if not company_names:
        return pd.DataFrame()
    source_columns = fetch_table_columns(table_name, app=app)
    available = {col.lower(): col for col in source_columns}
    issuer_expression = _issuer_expression(available)
    if issuer_expression == "NULL":
        return pd.DataFrame(columns=source_columns)
    date_col = available.get("asof_date", "asof_date")
    names = _quote_values(company_names)
    logging.info("Fetching latest debt features for %s names", len(company_names))
    return readdfsql(
        DEBT_FEATURES_NAME_SQL.format(
            table_name=table_name,
            names=names,
            date_col=date_col,
            issuer_expression=issuer_expression,
            cutoff_predicate=_asof_cutoff_predicate(date_col, asof_cutoff),
        ),
        app=app,
    )


def _values_for_filter(xref: pd.DataFrame, columns: list[str]) -> list[str]:
    values = []
    for col in columns:
        if col in xref:
            values.extend(xref[col].dropna().astype(str).str.strip().tolist())
    return [value for value in values if value]


def fetch_latest_debt_features_for_xref(
    xref: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    match_names: pd.DataFrame | pd.Series | list[str] | None = None,
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if xref.empty:
        return pd.DataFrame()
    source_columns = fetch_table_columns(table_name, app=app)
    available = {col.lower(): col for col in source_columns}
    clauses = []
    if match_names is not None:
        if isinstance(match_names, pd.DataFrame):
            company_names = match_names["company_name"].tolist() if "company_name" in match_names else []
        elif isinstance(match_names, pd.Series):
            company_names = match_names.tolist()
        else:
            company_names = list(match_names)
        if company_names:
            for source_col in ["issuer_name", "issuer", "company_name"]:
                if source_col in available:
                    clauses.append(f"lower(CAST({available[source_col]} AS STRING)) IN ({_quote_values(company_names)})")
    for source_col, xref_cols in {
        "issuer_id": ["findox_issuer_id"],
        "findox_issuer_id": ["findox_issuer_id"],
        "factset_entity_id": ["factset_entity_id"],
        "loanx_id": ["loanx_id", "first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
        "lxid": ["lxid"],
        "id_bb_company": ["bbg_company_id"],
        "bbg_company_id": ["bbg_company_id"],
        "bbg_id": ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
        "bbg_id_fs": ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
        "bbg_id_index": ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
        "instrument_id": ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
        "instrument_id_uniq": ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"],
    }.items():
        if source_col not in available:
            continue
        values = _values_for_filter(xref, xref_cols)
        if values:
            clauses.append(f"CAST({available[source_col]} AS STRING) IN ({_quote_id_values(values)})")
    if not clauses:
        return pd.DataFrame(columns=source_columns)
    date_col = available.get("asof_date", "asof_date")
    sql = LATEST_BY_FILTER_SQL.format(
        table_name=table_name,
        date_col=date_col,
        cutoff_predicate=_asof_cutoff_predicate(date_col, asof_cutoff),
        where_clause="\n      OR ".join(clauses),
    )
    return readdfsql(sql, app=app)


@checkpointed
def fetch_factset_entity_structure(
    entity_ids: list[str],
    *,
    catalog: str = "dbrx_factset_prod",
    app: str = "databricks",
) -> pd.DataFrame:
    columns = [
        "factset_entity_id",
        "portco_parent_factset_entity_id",
        "portco_ultimate_factset_entity_id",
    ]
    batches = _factset_entity_id_batches(
        [_clean_id_value(entity_id) for entity_id in entity_ids if _clean_id_value(entity_id)]
    )
    if not batches:
        return pd.DataFrame(columns=columns)
    frames = []
    for index, batch in enumerate(batches, start=1):
        logging.info(
            "FactSet entity-structure batch %s/%s (%s IDs)",
            index,
            len(batches),
            len(batch),
        )
        frame = readdfsql(
            FACTSET_ENTITY_STRUCTURE_SQL.format(
                catalog=catalog,
                entity_ids=_quote_id_values(batch),
            ),
            app=app,
        )
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True).drop_duplicates("factset_entity_id")


def _fetch_factset_entity_tree_debt_features(
    xref: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod",
    max_family_size: int = 25,
    asof_cutoff: str | pd.Timestamp | None = None,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    source_columns = fetch_table_columns(table_name, app=app)
    available = {col.lower(): col for col in source_columns}
    date_col = available.get("asof_date", "asof_date")
    sql = _factset_debt_entity_map_sql(
        table_name=table_name,
        date_col=date_col,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
    )
    logging.info(
        "Preflight FactSet debt-feature entity map for %s PEVC entities",
        len(seeds),
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    logging.info("Fetching latest debt-feature FactSet entities once")
    debt_features = readdfsql(sql, app=app)
    if debt_features.empty:
        return pd.DataFrame(columns=source_columns)
    structure = fetch_factset_entity_structure(
        seeds["factset_entity_id"].tolist(),
        catalog=factset_catalog,
        app=app,
    )
    seeded = seeds.merge(structure, on="factset_entity_id", how="left")
    for column in ("portco_parent_factset_entity_id", "portco_ultimate_factset_entity_id"):
        if column not in seeded:
            seeded[column] = pd.NA
    candidates = resolve_factset_entity_tree_matches(
        seeded,
        debt_features,
        max_family_size=max_family_size,
        keep_candidates=True,
    )
    return annotate_factset_debt_attribution(candidates, app=app, factset_catalog=factset_catalog,
        table_name=table_name, sponsor_entity_ids=sponsor_entity_ids)


def fetch_debt_features_from_factset_dcs(
    xref: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod", asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed, factset_dcs_debt_sql
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    sql = factset_dcs_debt_sql(
        tuple(FactSetDebtSeed(str(row.portco_id), str(row.factset_entity_id)) for row in seeds.itertuples()),
        table_name=table_name, factset_catalog=factset_catalog,
        source_columns=tuple(fetch_table_columns(table_name, app=app)),
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None,
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    return readdfsql(sql, app=app)


def fetch_debt_features_from_factset_loan_securities(
    xref: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod", asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.factset_loan_security_bridge import factset_loan_security_sql
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    sql = factset_loan_security_sql(
        tuple(FactSetDebtSeed(str(row.portco_id), str(row.factset_entity_id)) for row in seeds.itertuples()),
        table_name=table_name, factset_catalog=factset_catalog,
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None,
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    return readdfsql(sql, app=app)


def fetch_debt_features_from_reported_loan_deals(
    features: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.reported_loan_deal import reported_loan_anchors, reported_loan_deal_sql
    anchors = reported_loan_anchors(features)
    if not anchors:
        return pd.DataFrame()
    sql = reported_loan_deal_sql(anchors, table_name=table_name,
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None)
    preflight_factset_debt_entity_map_sql(sql, app=app)
    return readdfsql(sql, app=app)


def fetch_debt_features_from_factset_loan_borrowers(
    xref: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod", asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.factset_loan_borrower import factset_loan_borrower_sql
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    sql = factset_loan_borrower_sql(
        tuple(FactSetDebtSeed(str(row.portco_id), str(row.factset_entity_id)) for row in seeds.itertuples()),
        table_name=table_name, factset_catalog=factset_catalog,
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None,
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    return readdfsql(sql, app=app)


def fetch_debt_features_from_findox_borrowers(
    xref: pd.DataFrame, *, app: str = "databricks",
    table_name: str = "dbo.debt_features", factset_catalog: str = "dbrx_factset_prod",
    asof_cutoff: str | pd.Timestamp | None = None,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.findox_debt_borrower import findox_debt_borrower_sql, corroborate_findox_direct_ownership
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    sql = findox_debt_borrower_sql(
        tuple(FactSetDebtSeed(str(row.portco_id), str(row.factset_entity_id)) for row in seeds.itertuples()),
        table_name=table_name, factset_catalog=factset_catalog,
        source_columns=tuple(fetch_table_columns(table_name, app=app)),
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    candidates = readdfsql(sql, app=app).rename(columns=str.lower)
    from data_access.dbtables.sponsor_study.findox_bloomberg_borrower import validate_findox_bloomberg_borrowers
    corroborated = corroborate_findox_direct_ownership(candidates)
    return validate_findox_bloomberg_borrowers(corroborated, app=app, factset_catalog=factset_catalog,
        table_name=table_name, sponsor_entity_ids=sponsor_entity_ids)


def fetch_debt_features_from_factset_direct_groups(
    xref: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod", asof_cutoff: str | pd.Timestamp | None = None,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.factset_direct_group_debt import factset_direct_group_debt_sql, validate_audited_group_borrowers
    seeds = _factset_portco_seed_rows(xref)
    if seeds.empty:
        return pd.DataFrame()
    sql = factset_direct_group_debt_sql(
        tuple(FactSetDebtSeed(str(row.portco_id), str(row.factset_entity_id)) for row in seeds.itertuples()),
        table_name=table_name, factset_catalog=factset_catalog,
        source_columns=tuple(fetch_table_columns(table_name, app=app)),
        asof_cutoff=pd.Timestamp(asof_cutoff).date() if asof_cutoff is not None else None,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    preflight_factset_debt_entity_map_sql(sql, app=app)
    return validate_audited_group_borrowers(readdfsql(sql, app=app).rename(columns=str.lower),
        app=app, factset_catalog=factset_catalog, table_name=table_name,sponsor_entity_ids=sponsor_entity_ids)


@checkpointed
def fetch_latest_debt_features_for_factset_portcos(
    xref: pd.DataFrame, *, app: str = "databricks", table_name: str = "dbo.debt_features",
    factset_catalog: str = "dbrx_factset_prod", max_family_size: int = 25,
    asof_cutoff: str | pd.Timestamp | None = None,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    tree = _fetch_factset_entity_tree_debt_features(
        xref, app=app, table_name=table_name, factset_catalog=factset_catalog,
        max_family_size=max_family_size, asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    dcs = fetch_debt_features_from_factset_dcs(
        xref, app=app, table_name=table_name, factset_catalog=factset_catalog, asof_cutoff=asof_cutoff,
    )
    # WSO is deprecated. Financing continuity must use maintained, dated evidence.
    exact = [dcs.rename(columns=str.lower)] if not dcs.empty else []
    findox_borrowers = fetch_debt_features_from_findox_borrowers(
        xref, app=app, table_name=table_name, factset_catalog=factset_catalog, asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    direct_groups = fetch_debt_features_from_factset_direct_groups(
        xref, app=app, table_name=table_name, factset_catalog=factset_catalog, asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    frames = [frame.rename(columns=str.lower) for frame in (tree, dcs, findox_borrowers, direct_groups) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    from data_access.dbtables.sponsor_study.debt_instrument_aliases import aliases_from_exact_rows, with_instrument_aliases
    combined = pd.concat(frames, ignore_index=True)
    aliases = aliases_from_exact_rows(pd.concat(exact, ignore_index=True) if exact else pd.DataFrame())
    # Alias metadata survives source ranking; vendor metrics still come from one complete row.
    combined = with_instrument_aliases(combined, aliases)
    evidence_keys = ["portco_id", "debt_feature_key", *[column for column in
        ("portco_factset_entity_id", "debt_factset_entity_id", "debt_security_factset_id",
         "debt_security_entity_id", "debt_credit_parent_entity_id", "debt_bridge_source",
         "debt_bridge_relation", "debt_attribution_status") if column in combined]]
    return combined.drop_duplicates(evidence_keys)


def _asof_cutoff_and_predicate(date_col: str, asof_cutoff: str | pd.Timestamp | None) -> str:
    if asof_cutoff is None:
        return ""
    cutoff = pd.Timestamp(asof_cutoff).date().isoformat()
    return f"AND CAST({date_col} AS DATE) <= DATE '{cutoff}'"


@checkpointed
def fetch_latest_security_prices(
    sec_ids: list[str],
    table_name: str = "dbo.v_securities_data",
    app: str = "databricks",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    values = _quote_id_values(sec_ids)
    if values == "''":
        return pd.DataFrame()
    return readdfsql(
        SECURITY_PRICE_SQL.format(
            table_name=table_name,
            sec_ids=values,
            cutoff_predicate=_asof_cutoff_and_predicate("asof_date", asof_cutoff),
        ),
        app=app,
    )


def fetch_factset_entity_relatives(
    entity_ids: list[str],
    *,
    catalog: str = "dbrx_factset_prod",
    app: str = "databricks",
) -> pd.DataFrame:
    """Entity-structure rows touching the given entities, as themselves, a parent or an ultimate parent."""
    columns = [
        "factset_entity_id", "factset_parent_entity_id", "factset_ultimate_parent_entity_id",
    ]
    values = _quote_id_values(entity_ids)
    if values == "''":
        return _empty(columns)
    return readdfsql(
        FACTSET_ENTITY_RELATIVES_SQL.format(catalog=catalog, entity_ids=values),
        app=app,
    )


def fetch_factset_fx_rates_usd(
    *,
    catalog: str = "dbrx_factset_prod",
    app: str = "databricks",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Latest USD conversion factor per currency at or before the cutoff.

    `exch_rate_usd` is a multiplier: `amount_usd = amount_local * exch_rate_usd`.
    USD itself is absent from the table and must be treated as 1.0 by the caller.
    """
    cutoff = pd.Timestamp(asof_cutoff or pd.Timestamp.today()).date()
    return readdfsql(
        FACTSET_FX_RATES_USD_SQL.format(catalog=catalog, asof_cutoff=cutoff),
        app=app,
    )


def fetch_factset_fx_rate_history(
    *,
    currencies: list[str],
    catalog: str = "dbrx_factset_prod",
    app: str = "databricks",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Every USD conversion factor at or before the cutoff for the given currencies.

    Use when a figure carries its own observation date and must be converted at the rate effective
    then. `exch_rate_usd` is a multiplier: `amount_usd = amount_local * exch_rate_usd`.
    """
    wanted = [c for c in dict.fromkeys(currencies) if c and c.upper() != "USD"]
    if not wanted:
        return _empty(["iso_currency", "fx_rate_date", "exch_rate_usd"])
    cutoff = pd.Timestamp(asof_cutoff or pd.Timestamp.today()).date()
    return readdfsql(
        FACTSET_FX_RATE_HISTORY_SQL.format(
            catalog=catalog,
            asof_cutoff=cutoff,
            currencies=",".join(f"'{c.upper()}'" for c in wanted),
        ),
        app=app,
    )


@checkpointed
def fetch_factset_dcs_detail(
    factset_entity_ids: list[str],
    *,
    catalog: str = "dbrx_factset_prod",
    app: str = "databricks",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    values = _quote_id_values(factset_entity_ids)
    if values == "''":
        return _empty(FACTSET_DCS_COLUMNS[1:])
    cutoff = pd.Timestamp(asof_cutoff or pd.Timestamp.today()).date()
    return readdfsql(FACTSET_DCS_SQL.format(
        catalog=catalog, factset_entity_ids=values, asof_cutoff=cutoff,
    ), app=app)


def fetch_findox_public_document_index(
    findox_issuer_ids: list[str],
    *,
    app: str = "databricks",
) -> pd.DataFrame:
    """Public Findox document metadata only; document bodies are never selected."""
    values = _quote_id_values(findox_issuer_ids)
    if values == "''":
        return _empty(EXTERNAL_FINDOX_DOCUMENT_COLUMNS[1:])
    return readdfsql(
        FINDOX_PUBLIC_DOCUMENT_INDEX_SQL.format(issuer_ids=values),
        app=app,
    )


def build_portco_internal_id_seed(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    portcos = dataset.get("dim_portco", pd.DataFrame())
    if portcos.empty:
        return _empty(XREF_COLUMNS)
    xref = portcos[["portco_id", "company_name"]].drop_duplicates().copy()
    for col in XREF_COLUMNS:
        if col not in xref:
            xref[col] = pd.NA
    xref["match_method"] = "seed_company_name_pending_review"
    xref["match_confidence"] = "unmatched"

    stage = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame())
    template = dataset.get("source_template_portco_row", pd.DataFrame())
    join_cols = {"portco_id", "sponsor", "source_excel_row"}
    if not stage.empty and not template.empty and join_cols.issubset(stage.columns) and {"sponsor", "source_excel_row"}.issubset(template.columns):
        identifier_cols = {
            "ciq_identifier": "ciq_identifier",
            "first_lien_debt_identifier": "first_lien_debt_identifier",
            "second_lien_unsecured_debt_identifier": "second_lien_unsecured_debt_identifier",
            "bbg_company_name": "bbg_company_id",
        }
        available = [source for source in identifier_cols if source in template.columns]
        stage_keys = stage[["portco_id", "sponsor", "source_excel_row"]].drop_duplicates().copy()
        template_keys = template[["sponsor", "source_excel_row", *available]].copy()
        stage_keys["source_excel_row"] = pd.to_numeric(stage_keys["source_excel_row"], errors="coerce").astype("Int64")
        template_keys["source_excel_row"] = pd.to_numeric(template_keys["source_excel_row"], errors="coerce").astype("Int64")
        linked = stage_keys.merge(
            template_keys,
            on=["sponsor", "source_excel_row"],
            how="inner",
        )
        for source_col in available:
            target_col = identifier_cols[source_col]
            values = linked[["portco_id", source_col]].copy()
            values[target_col] = values[source_col].map(_clean_terminal_identifier)
            values = values[values[target_col].ne("")]
            unique_counts = values.groupby("portco_id")[target_col].nunique()
            values = values[values["portco_id"].isin(unique_counts[unique_counts.eq(1)].index)].drop_duplicates("portco_id")
            mapping = dict(zip(values["portco_id"], values[target_col]))
            xref[target_col] = xref[target_col].combine_first(xref["portco_id"].map(mapping))

        has_identifier = xref[[
            "ciq_identifier",
            "first_lien_debt_identifier",
            "second_lien_unsecured_debt_identifier",
            "bbg_company_id",
        ]].notna().any(axis=1)
        seeded = has_identifier & xref["match_confidence"].eq("unmatched")
        xref.loc[seeded, "match_method"] = "template_identifier_seed"
        xref.loc[seeded, "match_confidence"] = "medium"
    return xref[XREF_COLUMNS].reset_index(drop=True)


def _seed_portco_fund_rows(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
    funds = dataset.get("dim_fund", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    portcos = dataset.get("dim_portco", pd.DataFrame())
    if bridge.empty or funds.empty or portcos.empty:
        return pd.DataFrame(columns=["portco_id", "company_name_seed", "sponsor_name_seed", "fund_name_seed", "vintage"])
    out = bridge[["portco_id", "fund_id", "sponsor_id"]].drop_duplicates().copy()
    out = out.merge(portcos[["portco_id", "company_name"]], on="portco_id", how="left")
    out = out.merge(funds[["fund_id", "fund_name", "vintage"]], on="fund_id", how="left")
    if not sponsors.empty:
        out = out.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    else:
        out["sponsor_name"] = pd.NA
    return out.rename(columns={
        "company_name": "company_name_seed",
        "sponsor_name": "sponsor_name_seed",
        "fund_name": "fund_name_seed",
    })[["portco_id", "company_name_seed", "sponsor_name_seed", "fund_name_seed", "vintage"]].drop_duplicates()


def apply_pevc_factset_matches(xref: pd.DataFrame, dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pevc = dataset.get("source_factset_pevc_portco_investment", pd.DataFrame())
    seed = _seed_portco_fund_rows(dataset)
    required = {
        "factset_fund_entity_id",
        "factset_portco_entity_id",
        "sponsor_name_factset",
        "fund_name_factset",
        "portco_name_factset",
    }
    if xref.empty or pevc.empty or seed.empty or not required.issubset(pevc.columns):
        return xref.copy()

    seed = seed.copy()
    seed["sponsor_match_key"] = seed["sponsor_name_seed"].map(_match_key)
    seed["fund_match_key"] = seed["fund_name_seed"].map(_match_key)
    seed["company_key"] = seed["company_name_seed"].map(_norm)
    seed["vintage_seed"] = pd.to_numeric(seed["vintage"], errors="coerce")

    match = pevc.copy()
    match["sponsor_match_key"] = match["sponsor_name_factset"].map(_match_key)
    match["fund_match_key"] = match["fund_name_factset"].map(_match_key)
    match["company_key"] = match["portco_name_factset"].map(_norm)
    match["vintage_factset"] = pd.to_numeric(match.get("vintage", pd.Series(pd.NA, index=match.index)), errors="coerce")
    match = match[match["factset_portco_entity_id"].notna()].copy()

    merged = seed.merge(
        match[[
            "sponsor_match_key",
            "fund_match_key",
            "company_key",
            "vintage_factset",
            "factset_fund_entity_id",
            "factset_portco_entity_id",
        ]],
        on=["sponsor_match_key", "fund_match_key", "company_key"],
        how="inner",
    )
    vintage_match = (
        merged["vintage_seed"].isna()
        | merged["vintage_factset"].isna()
        | merged["vintage_seed"].eq(merged["vintage_factset"])
    )
    merged = merged[vintage_match].copy()
    if merged.empty:
        return xref.copy()

    merged["factset_entity_id_clean"] = merged["factset_portco_entity_id"].map(_clean_id_value)
    merged = merged[merged["factset_entity_id_clean"].ne("")]
    unique_counts = merged.groupby("portco_id")["factset_entity_id_clean"].nunique()
    unambiguous_portcos = unique_counts[unique_counts.eq(1)].index
    merged = merged[merged["portco_id"].isin(unambiguous_portcos)]
    if merged.empty:
        return xref.copy()

    best = (
        merged.sort_values(["portco_id", "factset_fund_entity_id", "factset_entity_id_clean"])
        .drop_duplicates("portco_id")
        [["portco_id", "factset_entity_id_clean"]]
    )
    out = xref.copy()
    out = out.merge(best, on="portco_id", how="left")
    found = out["factset_entity_id_clean"].notna() & out["factset_entity_id"].isna()
    out.loc[found, "factset_entity_id"] = out.loc[found, "factset_entity_id_clean"]
    out.loc[found, "match_method"] = "factset_pevc_fund_portco"
    out.loc[found, "match_confidence"] = "high"
    return out.drop(columns=["factset_entity_id_clean"], errors="ignore")[XREF_COLUMNS]


def apply_findox_matches(xref: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    if xref.empty or matches.empty:
        return xref.copy()
    out = xref.copy()
    match = matches.copy()
    match["company_key"] = match["findox_common_id_name"].map(_norm)
    match = match.sort_values(["company_key", "findox_issuer_id"]).drop_duplicates("company_key")
    out["company_key"] = out["company_name"].map(_norm)
    out = out.merge(match[["company_key", "findox_issuer_id", "findox_common_id"]], on="company_key", how="left")
    found = out["findox_issuer_id_y"].notna()
    out["findox_issuer_id"] = out["findox_issuer_id_y"].combine_first(out["findox_issuer_id_x"])
    out["findox_common_id"] = out["findox_common_id_y"].combine_first(out["findox_common_id_x"])
    out.loc[found, "match_method"] = "findox_exact_company_name"
    out.loc[found, "match_confidence"] = "high"
    return out.drop(
        columns=["company_key", "findox_issuer_id_x", "findox_issuer_id_y", "findox_common_id_x", "findox_common_id_y"],
        errors="ignore",
    )[XREF_COLUMNS]


def apply_factset_matches(xref: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    if xref.empty or matches.empty:
        return xref.copy()
    out = xref.copy()
    match = matches.copy()
    match["company_key"] = match["factset_entity_name"].map(_norm)
    match = match.sort_values(["company_key", "factset_entity_id"]).drop_duplicates("company_key")
    out["company_key"] = out["company_name"].map(_norm)
    out = out.merge(match[["company_key", "factset_entity_id"]], on="company_key", how="left", suffixes=("", "_match"))
    found = out["factset_entity_id_match"].notna() & out["factset_entity_id"].isna()
    out.loc[found, "factset_entity_id"] = out.loc[found, "factset_entity_id_match"]
    unmatched = out["match_confidence"].eq("unmatched") & found
    out.loc[unmatched, "match_method"] = "factset_exact_company_name"
    out.loc[unmatched, "match_confidence"] = "high"
    both = found & out["findox_issuer_id"].notna()
    out.loc[both, "match_method"] = "findox_and_factset_exact_company_name"
    return out.drop(columns=["company_key", "factset_entity_id_match"], errors="ignore")[XREF_COLUMNS]


def _portco_lookup_from_features(
    features: pd.DataFrame,
    xref: pd.DataFrame,
    name_lookup: pd.DataFrame | None = None,
) -> pd.Series:
    if features.empty:
        return pd.Series(dtype="string")
    out = pd.Series(pd.NA, index=features.index, dtype="string")
    portco_col = _first_existing(features, ["portco_id"])
    if portco_col:
        out = out.combine_first(_clean_id_series(features[portco_col]))
    for feature_col, xref_cols in [
        ("issuer_id", ["findox_issuer_id"]),
        ("findox_issuer_id", ["findox_issuer_id"]),
        ("factset_entity_id", ["factset_entity_id"]),
        ("loanx_id", ["loanx_id", "first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
        ("lxid", ["lxid"]),
        ("id_bb_company", ["bbg_company_id"]),
        ("bbg_company_id", ["bbg_company_id"]),
        ("bbg_id", ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
        ("bbg_id_fs", ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
        ("bbg_id_index", ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
        ("instrument_id", ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
        ("instrument_id_uniq", ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier"]),
    ]:
        source_col = _first_existing(features, [feature_col])
        available_xref_cols = [col for col in xref_cols if col in xref]
        if not source_col or not available_xref_cols:
            continue
        identifiers = pd.concat(
            [
                pd.DataFrame({
                    "identifier": xref[col].map(_clean_terminal_identifier).str.upper(),
                    "portco_id": xref["portco_id"],
                })
                for col in available_xref_cols
            ],
            ignore_index=True,
        ).dropna(subset=["identifier", "portco_id"])
        identifiers = identifiers[identifiers["identifier"].ne("")]
        unique_counts = identifiers.groupby("identifier")["portco_id"].nunique()
        identifiers = identifiers[
            identifiers["identifier"].isin(unique_counts[unique_counts.eq(1)].index)
        ].drop_duplicates("identifier")
        mapping = dict(zip(identifiers["identifier"], identifiers["portco_id"]))
        matched = features[source_col].map(_clean_terminal_identifier).str.upper().map(mapping)
        out = out.combine_first(matched.astype("string"))
    if name_lookup is not None:
        for source_col in ["issuer_name", "issuer", "company_name"]:
            col = _first_existing(features, [source_col])
            if not col:
                continue
            names = _unambiguous_name_lookup(name_lookup)
            mapping = dict(zip(names["company_name"].map(_norm), names["portco_id"]))
            matched = features[col].map(_norm).map(mapping)
            out = out.combine_first(matched.astype("string"))
    return out


def build_internal_debt_feature_snapshot(
    debt_features: pd.DataFrame,
    xref: pd.DataFrame,
    name_lookup: pd.DataFrame | None = None,
) -> pd.DataFrame:
    debt_features = admitted_factset_debt_rows(debt_features)
    if debt_features.empty or xref.empty:
        return _empty(INTERNAL_DEBT_FEATURE_COLUMNS)
    features = debt_features.copy()
    features.columns = [str(col).lower() for col in features.columns]
    features["portco_id"] = _portco_lookup_from_features(features, xref, name_lookup)
    features = features[features["portco_id"].notna()].copy()
    if features.empty:
        return _empty(INTERNAL_DEBT_FEATURE_COLUMNS)
    asof_col = _first_existing(features, ["asof_date", "date", "knowledge_date"])
    features["_resolved_instrument_id"] = _coalesce_existing(
        features,
        ["instrument_id_uniq", "instrument_id", "sec_id", "loanx_id", "cusip", "isin"],
    )
    inst_col = "_resolved_instrument_id"
    id_cols = {
        "portco_id",
        asof_col,
        inst_col,
        "issuer_name",
        "issuer",
        "company_name",
        "contributing_bridge_sources",
    } | set(INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS) | set(GROUP_DEBT_COLUMNS)
    value_cols = [col for col in features.columns if col not in id_cols and features[col].notna().any()]
    records = []
    for _, row in features.iterrows():
        for col in value_cols:
            value = row.get(col)
            if pd.isna(value):
                continue
            records.append({
                "portco_id": row["portco_id"],
                "instrument_id_uniq": row.get(inst_col) if inst_col else pd.NA,
                "asof_date": row.get(asof_col) if asof_col else pd.NaT,
                "feature_name": col,
                "feature_value": str(value),
                "source_system": _debt_features_source_system(row),
                "bridge_provenance": row.get("contributing_bridge_sources"),
                **{column: row.get(column) for column in GROUP_DEBT_COLUMNS},
            })
    return pd.DataFrame(records, columns=INTERNAL_DEBT_FEATURE_COLUMNS)


def build_internal_financial_snapshot(
    debt_features: pd.DataFrame,
    findox_financials: pd.DataFrame,
    xref: pd.DataFrame,
    name_lookup: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_financial_outputs(debt_features, findox_financials, xref, name_lookup)[0]


def build_financial_outputs(
    debt_features: pd.DataFrame, findox_financials: pd.DataFrame, xref: pd.DataFrame,
    name_lookup: pd.DataFrame | None = None, *, cutoff: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.financial_waterfall import (
        BASE_METRICS, OTHER_METRICS, DEBT_METRIC_COLUMNS, FinancialCandidate,
        debt_provider_candidates, select_financial_values,
    )
    from data_access.dbtables.sponsor_study.financial_history import candidate_ledger

    candidates: dict[str, list[FinancialCandidate]] = {}

    def text(value) -> str | None:
        return str(value) if pd.notna(value) else None

    def add(candidate: FinancialCandidate) -> None:
        candidates.setdefault(candidate.portco_id, []).append(candidate)

    features = admitted_factset_debt_rows(debt_features).copy()
    if not features.empty and not xref.empty:
        features.columns = features.columns.str.lower()
        if "debt_attribution_scope" in features:
            features = features[features.debt_attribution_scope.ne("consolidated_group").fillna(True)]
        features["portco_id"] = _portco_lookup_from_features(features, xref, name_lookup)
        for _, row in features[features.portco_id.notna()].iterrows():
            from hashlib import sha256
            values = _financial_values_from_debt_features_row(row)
            values['current_enterprise_value'] = pd.to_numeric(
                _first_present(row, DEBT_METRIC_COLUMNS['current_enterprise_value']), errors='coerce')
            statement_id = sha256(row.to_json().encode()).hexdigest()[:32]
            identifier, identifier_type = _financial_identifier_from_debt_features_row(row)
            for metric in (*BASE_METRICS, *OTHER_METRICS, "current_ebitda_margin"):
                if pd.notna(values[metric]):
                    source_column = next((col for col in DEBT_METRIC_COLUMNS.get(metric, ()) if pd.notna(row.get(col))),
                                         "derived_from_debt_features_row")
                    # Recompute ratios only from the selected, compatible source statements.
                    if source_column == "derived_from_debt_features_row":
                        continue
                    candidate = FinancialCandidate(
                        str(row.portco_id), metric, float(values[metric]), _debt_features_source_system(row),
                        identifier, identifier_type,
                        text(_first_present(row, ["period_end_date", "report_date", "financial_period_end_date"])),
                        text(_first_present(row, ["asof_date", "date", "knowledge_date"])),
                        source_column,
                        text(_first_present(row, ["instrument_id_uniq", "instrument_id", "loanx_id"])),
                        company_ticker=text(row.get("company_corp_ticker")),
                        reporting_entity_id=identifier,
                        source_record_id=statement_id,
                    )
                    for observation in debt_provider_candidates(candidate, row):
                        add(observation)
    if not findox_financials.empty and "findox_issuer_id" in xref:
        links = xref[["portco_id", "findox_issuer_id"]].dropna().drop_duplicates().copy()
        links["issuer_id"] = _clean_id_series(links.findox_issuer_id)
        fin = findox_financials.copy()
        fin["issuer_id"] = _clean_id_series(fin.issuer_id)
        fin = fin.merge(links[["portco_id", "issuer_id"]], on="issuer_id", validate="many_to_many")
        metric_map = {"revenue": "current_ltm_revenue", "ebitda_fda": "current_ltm_ebitda",
                      "adj_ebitda": "current_ltm_ebitda", "reported_ebitda": "current_ltm_ebitda",
                      "calc_ebitda": "current_ltm_ebitda", "total_debt": "current_total_debt",
                      "cash_and_equivs": "current_total_cash"}
        for _, row in fin.iterrows():
            value = pd.to_numeric(row.value, errors="coerce")
            if row.line_name in metric_map and pd.notna(value):
                add(FinancialCandidate(
                    str(row.portco_id), metric_map[row.line_name], float(value), "findox",
                    str(row.issuer_id), "findox_issuer_id", text(row.get("period_end_date")),
                    text(row.get("source_snapshot_at", row.get("cut_date"))),
                    str(row.get("source_line_id", row.line_name)),
                    text(row.get("report_id")), text(row.get("currency")),
                    float(row.fx_rate_usd) if pd.notna(row.get("fx_rate_usd")) else None,
                    (float(row.value_as_reported) / float(row.get("source_sign_multiplier", 1))
                     if pd.notna(row.get("value_as_reported")) else None),
                    text(row.get("source_loaded_at")),
                    source_table="dbo.findox_agg_standard_data_actual",
                    aggregate_company_id=text(row.get("aggregate_company_id")),
                    report_issue_date=text(row.get("report_issue_date")),
                    source_unit=text(row.get("source_unit")),
                    source_sign_multiplier=float(row.get("source_sign_multiplier", 1)),
                    eligibility_basis=text(row.get("eligibility_basis")),
                    findox_allowed=bool(row.findox_allowed) if pd.notna(row.get("findox_allowed")) else None,
                    findox_period_allowed=bool(row.findox_period_allowed) if pd.notna(row.get("findox_period_allowed")) else None,
                    findox_model_type=text(row.get("findox_model_type")),
                    findox_access_type=text(row.get("findox_access_type")),
                    findox_access_asof=text(row.get("committed_watermark")),
                    reporting_entity_id=str(row.issuer_id),
                    period_id=text(row.get("period_id")),
                    source_version=text(row.get("source_version")),
                    source_record_id=text(row.get("source_record_id")),
                    metric_preference=int(row.get("preference", 0)),
                    reporting_scope_id=text(row.get("reporting_scope_id")),
                    scope_status=text(row.get("scope_status")) or 'unverified',
                    scope_valid_from=text(row.get("scope_valid_from")),
                    scope_valid_to=text(row.get("scope_valid_to")),
                    scope_evidence_id=text(row.get("scope_evidence_id")),
                    scope_recorded_at=text(row.get("scope_recorded_at")),
                ))
    rows = [select_financial_values(tuple(values), cutoff=cutoff) for values in candidates.values()]
    ledger = candidate_ledger([item for row in rows for item in row.pop('_candidate_ledger', [])])
    result = pd.DataFrame(rows, columns=INTERNAL_FINANCIAL_COLUMNS)
    logging.info("Sponsor fundamentals waterfall companies=%s sources=%s",
                 len(result), result.source_system.value_counts().to_dict() if not result.empty else {})
    logging.info("Sponsor financial candidates=%s decisions=%s", len(ledger),
                 ledger.selection_reason.value_counts().to_dict())
    return result, ledger


def _tranche_type(row: pd.Series) -> str:
    text = " ".join(str(row.get(col) or "") for col in ["tranche_type", "debt_group", "seniority", "security_type", "instrument_type"]).lower()
    if "1l" in text or "first" in text or "senior secured" in text:
        return "1L"
    if "2l" in text or "second" in text or "sub" in text or "unsecured" in text:
        return "2L / Unsecured"
    return "Other"


def build_internal_market_snapshot(
    debt_features: pd.DataFrame,
    prices: pd.DataFrame,
    xref: pd.DataFrame,
    dcs_detail: pd.DataFrame | None = None,
    name_lookup: pd.DataFrame | None = None,
) -> pd.DataFrame:
    debt_features = admitted_factset_debt_rows(debt_features)
    frames = []
    if not debt_features.empty:
        features = debt_features.copy()
        features.columns = [str(col).lower() for col in features.columns]
        features["portco_id"] = _portco_lookup_from_features(features, xref, name_lookup)
        features = features[features["portco_id"].notna()].copy()
        if not features.empty:
            asof_col = _first_existing(features, ["asof_date", "date", "knowledge_date"])
            instrument_ids = _coalesce_existing(
                features,
                ["instrument_id_uniq", "instrument_id", "sec_id", "loanx_id", "cusip", "isin"],
            )
            frames.append(pd.DataFrame({
                "portco_id": features["portco_id"],
                "instrument_id_uniq": instrument_ids,
                "asof_date": features[asof_col] if asof_col else pd.NaT,
                "tranche_type": features.apply(_tranche_type, axis=1),
                "price": pd.to_numeric(features[_first_existing(features, ["price", "bid_price", "mid_price"])], errors="coerce")
                if _first_existing(features, ["price", "bid_price", "mid_price"]) else pd.NA,
                "ytw": pd.to_numeric(features[_first_existing(features, _YTW_COLUMNS)], errors="coerce")
                if _first_existing(features, _YTW_COLUMNS) else pd.NA,
                "ytm": pd.to_numeric(features[_first_existing(features, _YTM_COLUMNS)], errors="coerce")
                if _first_existing(features, _YTM_COLUMNS) else pd.NA,
                "spread": pd.to_numeric(features[_first_existing(features, ["spread", "oas"])], errors="coerce")
                if _first_existing(features, ["spread", "oas"]) else pd.NA,
                "duration": pd.to_numeric(features[_first_existing(features, _DURATION_COLUMNS)], errors="coerce")
                if _first_existing(features, _DURATION_COLUMNS) else pd.NA,
                "price_source": "debt_features",
                "source_system": features.apply(_debt_features_source_system, axis=1),
                "bridge_provenance": features.get("contributing_bridge_sources"),
                **{column: features.get(column) for column in GROUP_DEBT_COLUMNS},
            }))
    if not prices.empty and not xref.empty:
        from data_access.dbtables.sponsor_study.factset_filing_attribution import security_price_owners
        owners = security_price_owners(xref, dcs_detail)
        price = prices.copy()
        price.columns = [str(col).lower() for col in price.columns]
        price["sec_id"] = _clean_id_series(price["sec_id"])
        price = price.merge(owners, on="sec_id", how="inner", validate="many_to_many")
        if not price.empty:
            frames.append(pd.DataFrame({
                "portco_id": price["portco_id"],
                "instrument_id_uniq": price["sec_id"],
                "asof_date": price["asof_date"],
                "tranche_type": "Other",
                "price": pd.to_numeric(price["price"], errors="coerce"),
                "ytw": pd.NA,
                "ytm": pd.to_numeric(price["yield"], errors="coerce"),
                "spread": pd.to_numeric(price["spread"], errors="coerce"),
                "duration": pd.to_numeric(price["duration"], errors="coerce"),
                "price_source": price["source"],
                "source_system": "v_securities_data",
                **{column: price.get(column) for column in GROUP_DEBT_COLUMNS},
            }))
    if not frames:
        return _empty(INTERNAL_MARKET_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["asof_date"] = pd.to_datetime(out["asof_date"], errors="coerce")
    out = out.sort_values(["portco_id", "instrument_id_uniq", "asof_date"], na_position="last")
    return out.drop_duplicates(["portco_id", "instrument_id_uniq"], keep="last").reindex(columns=INTERNAL_MARKET_COLUMNS).reset_index(drop=True)


def build_internal_debt_instrument_snapshot(
    debt_features: pd.DataFrame,
    dcs_detail: pd.DataFrame,
    xref: pd.DataFrame,
    name_lookup: pd.DataFrame | None = None,
) -> pd.DataFrame:
    debt_features = admitted_factset_debt_rows(debt_features)
    frames = []
    if not debt_features.empty:
        features = debt_features.copy()
        features.columns = [str(col).lower() for col in features.columns]
        features["portco_id"] = _portco_lookup_from_features(features, xref, name_lookup)
        features = features[features["portco_id"].notna()].copy()
        if not features.empty:
            asof_col = _first_existing(features, ["asof_date", "date", "knowledge_date"])
            instrument_ids = _coalesce_existing(
                features,
                ["instrument_id_uniq", "instrument_id", "sec_id", "loanx_id", "cusip", "isin"],
            )
            maturity_col = _first_existing(features, ["maturity_date", "maturity"])
            frames.append(pd.DataFrame({
                "portco_id": features["portco_id"],
                "instrument_id_uniq": instrument_ids,
                "asof_date": features[asof_col] if asof_col else pd.NaT,
                "tranche_type": features.apply(_tranche_type, axis=1),
                "tranche_size": pd.to_numeric(_coalesce_existing(
                    features, ["amt_outstanding_usd", "amount_outstanding", "amt_out", "tranche_size"]
                ), errors="coerce"),
                "rate": pd.to_numeric(_coalesce_existing(features, ["current_coupon", "coupon", "rate"]), errors="coerce"),
                "maturity_date": features[maturity_col] if maturity_col else pd.NaT,
                "nearest_maturity_date": features[maturity_col] if maturity_col else pd.NaT,
                "nearest_maturity_type": features.apply(_tranche_type, axis=1),
                "source_system": features.apply(_debt_features_source_system, axis=1),
                "bridge_provenance": features.get("contributing_bridge_sources"),
                **{column: features.get(column) for column in GROUP_DEBT_COLUMNS},
            }))
    if not dcs_detail.empty and not xref.empty:
        from data_access.dbtables.sponsor_study.factset_filing_attribution import admitted_dcs_attribution_rows
        dcs = admitted_dcs_attribution_rows(dcs_detail)
        dcs.columns = [str(col).lower() for col in dcs.columns]
        if "portco_id" not in dcs:
            # Callers that resolved the owning company themselves keep their attribution,
            # which lets debt filed at a subsidiary reach its portfolio company.
            portco_map = dict(zip(_clean_id_series(xref["factset_entity_id"]), xref["portco_id"]))
            dcs["portco_id"] = _clean_id_series(dcs["factset_entity_id"]).map(portco_map)
        dcs = dcs[dcs["portco_id"].notna()].copy()
        if not dcs.empty:
            from data_access.dbtables.sponsor_study.debt_instrument_aliases import canonical_dcs_instrument_ids
            canonical_ids = canonical_dcs_instrument_ids(dcs, features if not debt_features.empty else pd.DataFrame())
            provenance = "factset_dcs:" + dcs["factset_instrument_id"].astype("string") + "->" + canonical_ids.astype("string")
            frames.append(pd.DataFrame({
                "portco_id": dcs["portco_id"],
                "instrument_id_uniq": canonical_ids,
                "asof_date": dcs["report_date"],
                "tranche_type": dcs.apply(_tranche_type, axis=1),
                "tranche_size": pd.to_numeric(dcs["amount_outstanding"], errors="coerce"),
                "rate": pd.NA,
                "maturity_date": dcs["maturity_date"],
                "nearest_maturity_date": dcs["maturity_date"],
                "nearest_maturity_type": dcs.apply(_tranche_type, axis=1),
                "source_system": "factset_dcs",
                "bridge_provenance": provenance.where(canonical_ids.ne(dcs["factset_instrument_id"]), "factset_dcs"),
                **{column: dcs.get(column) for column in GROUP_DEBT_COLUMNS},
            }))
    if not frames:
        return _empty(INTERNAL_DEBT_INSTRUMENT_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["asof_date"] = pd.to_datetime(out["asof_date"], errors="coerce")
    out["maturity_date"] = pd.to_datetime(out["maturity_date"], errors="coerce")
    out["nearest_maturity_date"] = pd.to_datetime(out["nearest_maturity_date"], errors="coerce")
    key = ["portco_id", "instrument_id_uniq", "asof_date"]
    complete = out[key].notna().all(axis=1)
    provenance = (
        out[complete]
        .groupby(key)["bridge_provenance"]
        .apply(
            lambda values: "|".join(sorted({
                token
                for value in values.dropna().astype(str)
                for token in value.split("|")
                if token
            }))
        )
        .reset_index(name="combined_bridge_provenance")
    )
    deduped = (
        out[complete]
        .sort_values(key + ["source_system"])
        .drop_duplicates(key, keep="first")
        .drop(columns=["bridge_provenance"], errors="ignore")
        .merge(provenance, on=key, how="left")
        .rename(columns={"combined_bridge_provenance": "bridge_provenance"})
    )
    missing_key = out[~complete]
    return pd.concat([deduped, missing_key], ignore_index=True).reindex(
        columns=INTERNAL_DEBT_INSTRUMENT_COLUMNS
    ).reset_index(drop=True)


def build_internal_debt_issuer_bridge(debt_features: pd.DataFrame) -> pd.DataFrame:
    if debt_features.empty:
        return _empty(INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS)
    features = debt_features.copy()
    features.columns = [str(col).lower() for col in features.columns]
    if "portco_id" not in features or "debt_factset_entity_id" not in features:
        return _empty(INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS)
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import DCS_DEBT_RELATION
    from data_access.dbtables.sponsor_study.factset_loan_security_bridge import LOAN_SECURITY_RELATION
    from data_access.dbtables.sponsor_study.reported_loan_deal import LOAN_DEAL_RELATION
    direct_dcs = (
        features.get("debt_bridge_relation", pd.Series(index=features.index, dtype="string")).isin({DCS_DEBT_RELATION, LOAN_SECURITY_RELATION, LOAN_DEAL_RELATION})
        & features.get("portco_factset_entity_id", pd.Series(index=features.index, dtype="string")).notna()
    )
    features = features[features["portco_id"].notna() & (features["debt_factset_entity_id"].notna() | direct_dcs)].copy()
    if features.empty:
        return _empty(INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS)
    for col in INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS:
        if col not in features:
            features[col] = pd.NA
    return features[INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS].drop_duplicates().reset_index(drop=True)


def build_factset_dcs_source(dcs_detail: pd.DataFrame, xref: pd.DataFrame) -> pd.DataFrame:
    if dcs_detail.empty or xref.empty:
        return _empty(FACTSET_DCS_COLUMNS)
    dcs = dcs_detail.copy()
    dcs.columns = [str(col).lower() for col in dcs.columns]
    portco_map = dict(zip(_clean_id_series(xref["factset_entity_id"]), xref["portco_id"]))
    resolved = (
        _clean_id_series(dcs["portco_id"])
        if "portco_id" in dcs
        else pd.Series(pd.NA, index=dcs.index, dtype="string")
    )
    mapped = _clean_id_series(dcs["factset_entity_id"]).map(portco_map)
    dcs["portco_id"] = resolved.where(resolved.notna(), mapped)
    dcs = dcs[dcs["portco_id"].notna()].copy()
    for col in FACTSET_DCS_COLUMNS:
        if col not in dcs:
            dcs[col] = pd.NA
    return dcs[FACTSET_DCS_COLUMNS].reset_index(drop=True)


def _security_ids_from_xref(xref: pd.DataFrame, dcs_detail: pd.DataFrame | None = None) -> list[str]:
    from data_access.dbtables.sponsor_study.factset_filing_attribution import admitted_dcs_attribution_rows

    ids = []
    for col in ["first_lien_debt_identifier", "second_lien_unsecured_debt_identifier", "loanx_id"]:
        if col in xref:
            ids.extend(xref[col].dropna().astype(str).str.strip().tolist())
    if dcs_detail is not None and not dcs_detail.empty:
        dcs = admitted_dcs_attribution_rows(dcs_detail)
        dcs.columns = [str(col).lower() for col in dcs.columns]
        for col in ["factset_instrument_id", "isin", "bbg_id"]:
            if col in dcs:
                ids.extend(dcs[col].dropna().astype(str).str.strip().tolist())
    return sorted({sec_id for sec_id in ids if sec_id})


def attach_live_internal_enrichment(
    dataset: dict[str, pd.DataFrame],
    *,
    app: str = "databricks",
    factset_catalog: str = "dbrx_factset_prod",
    debt_features_table: str = "dbo.debt_features",
    prices_table: str = "dbo.v_securities_data",
    findox_financials: pd.DataFrame | None = None,
    debt_bridge_max_family_size: int = 25,
    use_name_fallback: bool = False,
) -> dict[str, pd.DataFrame]:
    out = attach_internal_join_placeholders(dataset)
    xref = out.get("xref_portco_internal_ids", pd.DataFrame())
    xref = _with_xref_columns(xref) if not xref.empty else build_portco_internal_id_seed(out)
    xref = apply_pevc_factset_matches(xref, out)
    if "xref_company_alias_name_map" in out:
        from data_access.dbtables.sponsor_study.company_aliases import apply_company_aliases_to_portco_xref

        xref = _with_xref_columns(apply_company_aliases_to_portco_xref(xref, out["xref_company_alias_name_map"]))
    company_names = xref["company_name"].dropna().astype(str).tolist()
    if company_names:
        xref = apply_findox_matches(xref, fetch_findox_company_matches(company_names, app=app))
        xref = apply_factset_matches(xref, fetch_factset_company_matches(company_names, catalog=factset_catalog, app=app))
    out["xref_portco_internal_ids"] = xref
    debt_bridge_xref = build_factset_debt_bridge_seed_xref(out, xref)
    direct_id_xref = xref.copy()
    if "factset_entity_id" in direct_id_xref:
        direct_id_xref["factset_entity_id"] = pd.NA
    name_lookup = build_portco_match_name_lookup(out, xref) if use_name_fallback else None
    debt_feature_frames = [
        fetch_latest_debt_features_for_factset_portcos(
            debt_bridge_xref,
            app=app,
            table_name=debt_features_table,
            factset_catalog=factset_catalog,
            max_family_size=debt_bridge_max_family_size,
        ),
        fetch_latest_debt_features_for_xref(
            direct_id_xref,
            app=app,
            table_name=debt_features_table,
        ),
    ]
    if use_name_fallback and name_lookup is not None:
        debt_feature_frames.append(fetch_latest_debt_features_for_xref(
            direct_id_xref,
            app=app,
            table_name=debt_features_table,
            match_names=name_lookup,
        ))
    debt_feature_frames = [frame for frame in debt_feature_frames if frame is not None and not frame.empty]
    debt_features = pd.concat(debt_feature_frames, ignore_index=True).drop_duplicates() if debt_feature_frames else pd.DataFrame()
    dcs = fetch_factset_dcs_detail(xref["factset_entity_id"].dropna().astype(str).tolist(), catalog=factset_catalog, app=app)
    sec_ids = _security_ids_from_xref(xref, dcs)
    prices = fetch_latest_security_prices(sec_ids, table_name=prices_table, app=app) if sec_ids else pd.DataFrame()
    out["source_internal_debt_issuer_bridge"] = build_internal_debt_issuer_bridge(debt_features)
    out["fact_internal_debt_feature_snapshot"] = build_internal_debt_feature_snapshot(debt_features, xref, name_lookup)
    out["source_internal_financial_snapshot"] = build_internal_financial_snapshot(
        debt_features,
        findox_financials if findox_financials is not None else pd.DataFrame(),
        xref,
        name_lookup,
    )
    out["source_internal_market_trading_snapshot"] = build_internal_market_snapshot(debt_features, prices, xref, dcs, name_lookup)
    out["source_internal_debt_instrument_snapshot"] = build_internal_debt_instrument_snapshot(debt_features, dcs, xref, name_lookup)
    out["source_factset_dcs_debt_detail"] = build_factset_dcs_source(dcs, xref)
    return out


def attach_internal_join_placeholders(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Add empty but schema-stable tables for internal data enrichments.

    The refresh CLI can populate these once source identifiers and Databricks
    permissions are configured. Keeping the tables present lets the dashboard
    and validation code be deployed before every licensed source is automated.
    """
    out = dict(dataset)
    out.setdefault("xref_portco_internal_ids", build_portco_internal_id_seed(out))
    out.setdefault("fact_internal_debt_feature_snapshot", _empty(INTERNAL_DEBT_FEATURE_COLUMNS))
    out.setdefault("source_internal_financial_snapshot", _empty(INTERNAL_FINANCIAL_COLUMNS))
    out.setdefault("source_internal_market_trading_snapshot", _empty(INTERNAL_MARKET_COLUMNS))
    out.setdefault("source_internal_debt_instrument_snapshot", _empty(INTERNAL_DEBT_INSTRUMENT_COLUMNS))
    out.setdefault("source_internal_debt_issuer_bridge", _empty(INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS))
    out.setdefault("source_factset_dcs_debt_detail", _empty(FACTSET_DCS_COLUMNS))
    out.setdefault("source_document_index", pd.DataFrame(columns=[
        "portco_id",
        "source_system",
        "document_id",
        "document_name",
        "document_date",
        "document_type",
        "blob_path",
        "reviewer_status",
    ]))
    return out


EXTERNAL_DEBT_FEATURE_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in INTERNAL_DEBT_FEATURE_COLUMNS]
EXTERNAL_FINANCIAL_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in INTERNAL_FINANCIAL_COLUMNS]
EXTERNAL_MARKET_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in INTERNAL_MARKET_COLUMNS]
EXTERNAL_DEBT_INSTRUMENT_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in INTERNAL_DEBT_INSTRUMENT_COLUMNS]
EXTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in INTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS]
EXTERNAL_FACTSET_DCS_COLUMNS = [col.replace("portco_id", "external_portco_id") for col in FACTSET_DCS_COLUMNS]
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
EXTERNAL_XREF_COLUMNS = [
    col.replace("portco_id", "external_portco_id") for col in XREF_COLUMNS
]


def _external_xref_from_dataset(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    if holdings.empty:
        return _empty(XREF_COLUMNS)
    required = {"external_portco_id", "company_name"}
    missing = required - set(holdings)
    if missing:
        raise ValueError(
            f"External holdings are missing xref columns: {sorted(missing)}"
        )

    base = holdings[["external_portco_id", "company_name"]].dropna().drop_duplicates()
    factset = dataset.get("xref_external_portco_factset", pd.DataFrame())
    if not factset.empty:
        factset = factset[
            [
                "external_portco_id",
                "factset_portco_entity_id",
                "match_method",
                "match_confidence",
            ]
        ].drop_duplicates("external_portco_id")
        base = base.merge(factset, on="external_portco_id", how="left")
    else:
        base["factset_portco_entity_id"] = pd.NA
        base["match_method"] = pd.NA
        base["match_confidence"] = pd.NA

    xref = base.rename(columns={
        "external_portco_id": "portco_id",
        "factset_portco_entity_id": "factset_entity_id",
    })
    has_factset = xref["factset_entity_id"].notna()
    xref.loc[has_factset & xref["match_method"].isna(), "match_method"] = (
        "factset_pevc_holding"
    )
    xref.loc[
        has_factset & xref["match_confidence"].isna(), "match_confidence"
    ] = "high"
    xref.loc[~has_factset, "match_method"] = "external_company_unresolved"
    xref.loc[~has_factset, "match_confidence"] = "unmatched"
    return _with_xref_columns(xref)


def _external_factset_debt_seeds(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Retain every FactSet key attached to scoped company crosswalk rows."""
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed

    columns = ["portco_id", "factset_entity_id"]
    factset = dataset.get("xref_external_portco_factset", pd.DataFrame())
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    if factset.empty or holdings.empty:
        return pd.DataFrame(columns=columns)
    scoped = factset[
        factset["external_portco_id"].isin(holdings["external_portco_id"])
    ][["external_portco_id", "factset_portco_entity_id"]].dropna().drop_duplicates()
    seeds = {
        FactSetDebtSeed(str(owner), str(entity))
        for owner, entity in scoped.itertuples(index=False, name=None)
    }
    return pd.DataFrame(
        [(seed.portco_id, seed.factset_entity_id) for seed in sorted(
            seeds, key=lambda seed: (seed.portco_id, seed.factset_entity_id)
        )], columns=columns,
    )


def _external_company_candidates(
    dataset: dict[str, pd.DataFrame],
    xref: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "external_company_id",
        "company_name",
        "factset_portco_entity_id",
        "alias_names",
    ]
    if xref.empty:
        return _empty(columns)
    companies = xref[["portco_id", "company_name", "factset_entity_id"]].rename(
        columns={
            "portco_id": "external_company_id",
            "factset_entity_id": "factset_portco_entity_id",
        }
    )
    decisions = dataset.get(
        "xref_external_company_name_decision", pd.DataFrame()
    )
    aliases: dict[str, tuple[str, ...]] = {}
    if not decisions.empty:
        required = {
            "external_portco_id",
            "source_company_name",
            "decision_status",
            "is_active",
        }
        if required.issubset(decisions.columns):
            accepted = decisions[
                decisions["decision_status"].eq("accepted")
                & decisions["is_active"].astype("boolean").fillna(False)
                & decisions["external_portco_id"].notna()
            ]
            aliases = (
                accepted.groupby("external_portco_id")["source_company_name"]
                .apply(
                    lambda values: tuple(
                        dict.fromkeys(values.dropna().astype(str))
                    )
                )
                .to_dict()
            )
    companies["alias_names"] = companies["external_company_id"].map(
        lambda company_id: aliases.get(str(company_id), ())
    )
    return companies[columns].drop_duplicates("external_company_id").reset_index(
        drop=True
    )


def safe_capital_stack_debt_bindings(
    capital_stack: pd.DataFrame,
) -> pd.DataFrame:
    """Return capital-stack rows eligible for debt binding after path admission."""
    if capital_stack.empty:
        return capital_stack
    if "stack_eligibility" not in capital_stack:
        from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
            annotate_capital_stack_admission,
        )
        capital_stack = annotate_capital_stack_admission(capital_stack)
    out = capital_stack[capital_stack["stack_eligibility"].eq("admitted")].copy()
    descendants = pd.to_numeric(
        out.get("path_depth", out["capital_stack_rank"]), errors="coerce"
    ).fillna(0).gt(0)
    if "company_corp_ticker" in out:
        out.loc[descendants, "company_corp_ticker"] = pd.NA
    if "obligor_bbid" in out and "obligor_inside_stack" in out:
        outside = out["obligor_inside_stack"].astype("boolean").fillna(False).eq(False)
        out.loc[outside, "obligor_bbid"] = pd.NA
        if "company_corp_ticker" in out:
            out.loc[outside, "company_corp_ticker"] = pd.NA
    return out


def merge_debt_features_with_provenance(
    factset_features: pd.DataFrame,
    bloomberg_features: pd.DataFrame,
    consolidated_features: pd.DataFrame | None = None,
    findox_features: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Union FactSet and Bloomberg debt rows, deduping instruments with bridge provenance."""
    frames: list[pd.DataFrame] = []
    if factset_features is not None and not factset_features.empty:
        fs = admitted_factset_debt_rows(factset_features.rename(columns=lambda column: str(column).lower()))
        fs["_bridge_rank"] = 0
        if "debt_bridge_source_system" not in fs.columns:
            fs["debt_bridge_source_system"] = "factset_entity_tree"
        frames.append(fs)
    if bloomberg_features is not None and not bloomberg_features.empty:
        bb = bloomberg_features.rename(columns=lambda column: str(column).lower())
        bb["_bridge_rank"] = 1
        bb["debt_bridge_source_system"] = "bloomberg_capital_stack"
        if "debt_bridge_source" not in bb.columns:
            bb["debt_bridge_source"] = "bloomberg_issuer_master"
        frames.append(bb)
    if consolidated_features is not None and not consolidated_features.empty:
        group = consolidated_features.rename(columns=lambda column: str(column).lower()).copy()
        group["_bridge_rank"] = 2
        frames.append(group)
    if findox_features is not None and not findox_features.empty:
        core = findox_features.rename(columns=lambda column: str(column).lower()).copy()
        core["_bridge_rank"] = 3
        frames.append(core)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    if combined.empty:
        return combined.drop(columns=["_bridge_rank"])
    combined.columns = [str(col).lower() for col in combined.columns]
    asof_col = _first_existing(combined, ["asof_date", "date", "knowledge_date"])
    if asof_col is None or "portco_id" not in combined:
        return combined.drop(columns=["_bridge_rank"], errors="ignore")
    combined["_instrument_key"] = _clean_id_series(
        _coalesce_existing(
            combined,
            ["instrument_id_uniq", "instrument_id", "sec_id", "loanx_id", "cusip", "isin"],
        )
    )
    combined["_asof_key"] = pd.to_datetime(combined[asof_col], errors="coerce")
    key_cols = ["portco_id", "_instrument_key", "_asof_key"]
    def bridge_label(row: pd.Series) -> str:
        parts = []
        for column in ("debt_bridge_source_system", "debt_bridge_source"):
            value = row.get(column)
            if value is not None and not pd.isna(value) and str(value).strip():
                parts.append(str(value).strip())
        labels = {"->".join(parts)}
        prior = row.get("contributing_bridge_sources")
        if prior is not None and not pd.isna(prior):
            labels.update(str(prior).split("|"))
        return "|".join(sorted(label for label in labels if label))

    combined["_bridge_label"] = combined.apply(bridge_label, axis=1)
    combined = combined.drop(columns=["contributing_bridge_sources"], errors="ignore")
    complete = combined[key_cols].notna().all(axis=1)
    eligible = combined[complete]
    provenance = eligible.groupby(key_cols)["_bridge_label"].apply(
        lambda labels: "|".join(sorted({part for label in labels for part in label.split("|") if part}))
    )
    ranked = eligible.sort_values(key_cols + ["_bridge_rank"])
    provenance_df = provenance.reset_index(name="contributing_bridge_sources")
    winners = ranked.drop_duplicates(key_cols, keep="first").merge(
        provenance_df,
        on=key_cols,
        how="left",
    )
    incomplete = combined[~complete].copy()
    incomplete["contributing_bridge_sources"] = incomplete["_bridge_label"]
    return pd.concat([winners, incomplete], ignore_index=True).drop(
        columns=["_bridge_rank", "_bridge_label", "_instrument_key", "_asof_key"],
        errors="ignore",
    )


def fetch_external_dcs_capital_structure(
    companies: pd.DataFrame,
    *,
    app: str,
    factset_catalog: str,
    asof_cutoff: str | pd.Timestamp,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Reuse the smart-comparison DCS ownership, PIT, scale, and filing guards."""
    from data_access.dbtables.sponsor_study.smart_comparison import (
        _fetch_dcs_capital_structure,
    )

    return _fetch_dcs_capital_structure(
        companies,
        app=app,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids,
    )


def _rename_internal_to_external(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty:
        return _empty(columns)
    out = df.rename(columns={"portco_id": "external_portco_id"})
    for col in columns:
        if col not in out:
            out[col] = pd.NA
    return out[columns].reset_index(drop=True)


def build_external_debt_issuer_bridge(debt_features: pd.DataFrame) -> pd.DataFrame:
    bridge = build_internal_debt_issuer_bridge(debt_features)
    return _rename_internal_to_external(bridge, EXTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS)


def build_external_debt_feature_snapshot(
    debt_features: pd.DataFrame,
    xref: pd.DataFrame,
) -> pd.DataFrame:
    snapshot = build_internal_debt_feature_snapshot(debt_features, xref, name_lookup=None)
    return _rename_internal_to_external(snapshot, EXTERNAL_DEBT_FEATURE_COLUMNS)


def build_external_financial_snapshot(
    debt_features: pd.DataFrame,
    xref: pd.DataFrame,
    findox_financials: pd.DataFrame | None = None,
) -> pd.DataFrame:
    snapshot = build_internal_financial_snapshot(
        debt_features,
        findox_financials
        if findox_financials is not None
        else pd.DataFrame(),
        xref,
        name_lookup=None,
    )
    return _rename_internal_to_external(snapshot, EXTERNAL_FINANCIAL_COLUMNS)


def build_external_market_snapshot(
    debt_features: pd.DataFrame,
    prices: pd.DataFrame,
    xref: pd.DataFrame,
    dcs_detail: pd.DataFrame | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.debt_instrument_eligibility import identifiable_cash_debt_rows, identifiable_dcs_cash_debt_rows

    dcs = identifiable_dcs_cash_debt_rows(dcs_detail) if dcs_detail is not None else None
    snapshot = build_internal_market_snapshot(identifiable_cash_debt_rows(debt_features), prices, xref, dcs, name_lookup=None)
    return _rename_internal_to_external(snapshot, EXTERNAL_MARKET_COLUMNS)


def build_external_debt_instrument_snapshot(
    debt_features: pd.DataFrame,
    dcs_detail: pd.DataFrame,
    xref: pd.DataFrame,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.debt_instrument_eligibility import identifiable_cash_debt_rows, identifiable_dcs_cash_debt_rows

    snapshot = build_internal_debt_instrument_snapshot(identifiable_cash_debt_rows(debt_features), identifiable_dcs_cash_debt_rows(dcs_detail), xref, name_lookup=None)
    return _rename_internal_to_external(snapshot, EXTERNAL_DEBT_INSTRUMENT_COLUMNS)


def build_external_factset_dcs_source(dcs_detail: pd.DataFrame, xref: pd.DataFrame) -> pd.DataFrame:
    snapshot = build_factset_dcs_source(dcs_detail, xref)
    return _rename_internal_to_external(snapshot, EXTERNAL_FACTSET_DCS_COLUMNS)


def build_external_findox_document_index(
    document_index: pd.DataFrame,
    findox_matches: pd.DataFrame,
) -> pd.DataFrame:
    if document_index.empty or findox_matches.empty:
        return _empty(EXTERNAL_FINDOX_DOCUMENT_COLUMNS)
    documents = document_index.copy()
    documents.columns = [str(col).lower() for col in documents.columns]
    matches = findox_matches[
        ["external_company_id", "findox_issuer_id"]
    ].dropna().drop_duplicates("findox_issuer_id")
    issuer_to_portco = dict(
        zip(
            _clean_id_series(matches["findox_issuer_id"]),
            matches["external_company_id"],
        )
    )
    documents["external_portco_id"] = _clean_id_series(
        documents["findox_issuer_id"]
    ).map(issuer_to_portco)
    documents = documents[documents["external_portco_id"].notna()].copy()
    if "is_private" in documents:
        documents = documents[
            ~documents["is_private"].astype("boolean").fillna(True)
        ].copy()
    for column in EXTERNAL_FINDOX_DOCUMENT_COLUMNS:
        if column not in documents:
            documents[column] = pd.NA
    return documents[EXTERNAL_FINDOX_DOCUMENT_COLUMNS].drop_duplicates(
        ["external_portco_id", "document_id"]
    ).reset_index(drop=True)


def attach_external_join_placeholders(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import (
        CAPITAL_STACK_COLUMNS,
    )
    from data_access.dbtables.sponsor_study.debt_identifier_crosswalk import (
        CROSSWALK_COLUMNS,
    )
    from data_access.dbtables.sponsor_study.findox_fundamentals import (
        FINDOX_MATCH_COLUMNS,
    )

    out.setdefault("xref_external_company_internal_id", _empty(EXTERNAL_XREF_COLUMNS))
    out.setdefault(
        "xref_external_company_debt_identifier",
        _empty([
            col.replace("portco_id", "external_portco_id")
            for col in CROSSWALK_COLUMNS
        ]),
    )
    out.setdefault(
        "xref_external_company_capital_stack",
        _empty([
            col.replace("external_company_id", "external_portco_id")
            for col in CAPITAL_STACK_COLUMNS
        ]),
    )
    out.setdefault(
        "xref_external_company_findox",
        _empty([
            col.replace("external_company_id", "external_portco_id")
            for col in [*FINDOX_MATCH_COLUMNS, *COVERAGE_COLUMNS]
        ]),
    )
    out.setdefault("source_external_debt_issuer_bridge", _empty(EXTERNAL_DEBT_ISSUER_BRIDGE_COLUMNS))
    out.setdefault("fact_external_debt_feature_snapshot", _empty(EXTERNAL_DEBT_FEATURE_COLUMNS))
    out.setdefault("source_external_financial_snapshot", _empty(EXTERNAL_FINANCIAL_COLUMNS))
    out.setdefault("source_external_market_trading_snapshot", _empty(EXTERNAL_MARKET_COLUMNS))
    out.setdefault("source_external_debt_instrument_snapshot", _empty(EXTERNAL_DEBT_INSTRUMENT_COLUMNS))
    out.setdefault("source_external_factset_dcs_debt_detail", _empty(EXTERNAL_FACTSET_DCS_COLUMNS))
    out.setdefault(
        "source_external_findox_document_index",
        _empty(EXTERNAL_FINDOX_DOCUMENT_COLUMNS),
    )
    return out


def _log_external_enrichment_stage(
    stage: str,
    event: str,
    started_at: float,
    **frames: pd.DataFrame | int | None,
) -> None:
    logging.info(
        "External enrichment stage=%s event=%s elapsed_seconds=%.2f "
        "rss_gb=%.2f rows=%s",
        stage,
        event,
        perf_counter() - started_at,
        get_process_memory_gb(),
        {
            name: (
                0
                if frame is None
                else frame
                if isinstance(frame, int)
                else len(frame)
            )
            for name, frame in frames.items()
        },
    )

    from data_access.dbtables.sponsor_study.diagnostic_trace import diagnostic_boundary
    diagnostic_boundary(stage, event, **frames)


@checkpointed
def attach_external_live_enrichment(
    dataset: dict[str, pd.DataFrame],
    *,
    app: str = "databricks",
    factset_catalog: str = "dbrx_factset_prod",
    debt_features_table: str = "dbo.debt_features",
    prices_table: str = "dbo.v_securities_data",
    debt_bridge_max_family_size: int = 25,
    asof_cutoff: str | pd.Timestamp = STUDY_ASOF_DATE,
) -> dict[str, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import (
        build_bloomberg_name_index,
        capital_stack_aliases,
        fetch_bloomberg_capital_stack,
        fetch_bloomberg_issuer_universe,
        fetch_debt_features_for_bloomberg_matches,
        fetch_debt_identifier_universe,
        resolve_companies_to_bloomberg,
    )
    from data_access.dbtables.sponsor_study.debt_identifier_crosswalk import (
        bindings_from_bloomberg_matches,
        bindings_from_xref,
        build_debt_identifier_crosswalk,
    )
    from data_access.dbtables.sponsor_study.findox_fundamentals import (
        attach_findox_issuer_ids,
        fetch_findox_fundamental_lines,
        fetch_findox_name_index,
        resolve_companies_to_findox,
    )
    from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
    from data_access.dbtables.sponsor_study.group_debt_sources import fetch_consolidated_group_debt
    from data_access.dbtables.sponsor_study.findox_debt import (
        fetch_market_date, fetch_identifier_rows, fetch_findox_debt,
        identifier_keys, synchronized_feature_quotes, merge_core_market, core_price_snapshots,
    )

    started_at = perf_counter()
    out = attach_external_join_placeholders(dataset)
    boundary_holdings = out.get("_diagnostic_sponsor_boundaries",
        out.get("fact_external_holding", pd.DataFrame()))
    sponsor_entity_ids = frozenset(boundary_holdings.reindex(
        columns=["factset_gp_entity_id", "factset_fund_entity_id"]).stack().astype(str))
    global_sponsors = out.get("_diagnostic_global_sponsors")
    sponsor_names = frozenset((global_sponsors["sponsor"] if global_sponsors is not None else
        out.get("fact_external_holding", pd.DataFrame()).get("sponsor_name", pd.Series(dtype="object")))
        .dropna().astype(str))
    xref = _external_xref_from_dataset(out)
    if xref.empty:
        return out
    market_date = fetch_market_date(table_name=debt_features_table, app=app, asof_cutoff=str(asof_cutoff))
    logging.info("Sponsor synchronized debt pricing date=%s cutoff=%s", market_date, asof_cutoff)
    companies = _external_company_candidates(out, xref)
    _log_external_enrichment_stage(
        "initialize",
        "complete",
        started_at,
        xref=xref,
        companies=companies,
    )

    debt_bridge_xref = _external_factset_debt_seeds(out)
    debt_bridge_xref["match_method"] = "factset_pevc_fund_portco"
    debt_bridge_xref["match_confidence"] = "high"
    _log_external_enrichment_stage(
        "factset_debt_features",
        "start",
        started_at,
        debt_bridge_xref=debt_bridge_xref,
    )
    factset_features = fetch_latest_debt_features_for_factset_portcos(
        debt_bridge_xref,
        app=app,
        table_name=debt_features_table,
        factset_catalog=factset_catalog,
        max_family_size=debt_bridge_max_family_size,
        asof_cutoff=market_date,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    _log_external_enrichment_stage(
        "factset_debt_features",
        "complete",
        started_at,
        factset_features=factset_features,
    )

    _log_external_enrichment_stage(
        "debt_identifier_universe", "start", started_at
    )
    debt_identifiers = fetch_debt_identifier_universe(
        app=app,
        table_name=debt_features_table,
        asof_cutoff=market_date,
    )
    _log_external_enrichment_stage(
        "debt_identifier_universe",
        "complete",
        started_at,
        debt_identifiers=debt_identifiers,
    )
    bloomberg_universe = fetch_bloomberg_issuer_universe(
        debt_identifiers,
        app=app,
        asof_cutoff=asof_cutoff,
    )
    _log_external_enrichment_stage(
        "bloomberg_universe",
        "complete",
        started_at,
        bloomberg_universe=bloomberg_universe,
    )
    bloomberg_matches, _ = resolve_companies_to_bloomberg(
        companies,
        build_bloomberg_name_index(bloomberg_universe),
        sponsor_names=sponsor_names,
    )
    _log_external_enrichment_stage(
        "bloomberg_matches",
        "complete",
        started_at,
        bloomberg_matches=bloomberg_matches,
    )
    exact_bloomberg = (
        bloomberg_matches[
            bloomberg_matches["exact_match_flag"].astype("boolean").fillna(False)
        ].copy()
        if not bloomberg_matches.empty
        else bloomberg_matches
    )
    capital_stack = fetch_bloomberg_capital_stack(
        exact_bloomberg,
        app=app,
        asof_cutoff=asof_cutoff,
    )
    if not capital_stack.empty and "stack_eligibility" not in capital_stack:
        from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
            annotate_capital_stack_admission,
        )
        capital_stack = annotate_capital_stack_admission(capital_stack, app=app)
    _log_external_enrichment_stage(
        "capital_stack",
        "complete",
        started_at,
        capital_stack=capital_stack,
    )
    bloomberg_bindings = (
        safe_capital_stack_debt_bindings(capital_stack)
        if not capital_stack.empty
        else exact_bloomberg
    )
    bloomberg_features = fetch_debt_features_for_bloomberg_matches(
        bloomberg_bindings,
        app=app,
        table_name=debt_features_table,
        asof_cutoff=market_date,
    )
    _log_external_enrichment_stage(
        "bloomberg_debt_features",
        "complete",
        started_at,
        bloomberg_features=bloomberg_features,
    )
    group_features = fetch_consolidated_group_debt(
        tuple(FactSetDebtSeed(str(owner), str(entity)) for owner, entity
            in debt_bridge_xref[["portco_id", "factset_entity_id"]].dropna().drop_duplicates().itertuples(index=False, name=None)),
        sponsor_entity_ids=sponsor_entity_ids,
        app=app, factset_catalog=factset_catalog, table_name=debt_features_table,
        asof_cutoff=market_date,
    )
    _log_external_enrichment_stage(
        "consolidated_group_debt", "complete", started_at, group_features=group_features,
    )
    debt_features = merge_debt_features_with_provenance(
        factset_features,
        bloomberg_features,
        group_features,
    )

    if not capital_stack.empty:
        stack_aliases = capital_stack_aliases(capital_stack)
        alias_map = (
            stack_aliases.groupby("external_company_id")[
                "capital_stack_alias"
            ]
            .apply(
                lambda values: tuple(
                    dict.fromkeys(values.dropna().astype(str))
                )
            )
            .to_dict()
        )
        companies["capital_stack_alias_names"] = companies[
            "external_company_id"
        ].map(lambda company_id: alias_map.get(str(company_id), ()))

    _log_external_enrichment_stage("dcs", "start", started_at)
    dcs = fetch_external_dcs_capital_structure(
        companies,
        app=app,
        factset_catalog=factset_catalog,
        asof_cutoff=asof_cutoff,
        sponsor_entity_ids=sponsor_entity_ids,
    )
    _log_external_enrichment_stage(
        "dcs", "complete", started_at, dcs=dcs
    )
    sec_ids = _security_ids_from_xref(xref, dcs)
    _log_external_enrichment_stage(
        "security_prices",
        "start",
        started_at,
        security_id_count=len(sec_ids),
    )
    prices = fetch_identifier_rows(
        tuple(sorted(set().union(*(identifier_keys(value) for value in sec_ids)))),
        table_name=prices_table,
        app=app,
        market_date=market_date,
        debt_features=False,
    ) if sec_ids else pd.DataFrame()
    _log_external_enrichment_stage(
        "security_prices", "complete", started_at, prices=prices
    )

    _log_external_enrichment_stage("findox_matches", "start", started_at)
    findox_matches, _ = resolve_companies_to_findox(
        companies,
        fetch_findox_name_index(app=app),
    )
    findox_matches = fetch_findox_model_coverage(
        findox_matches, asof_cutoff=str(pd.Timestamp(asof_cutoff).date()), app=app)
    _log_external_enrichment_stage(
        "findox_matches",
        "complete",
        started_at,
        findox_matches=findox_matches,
    )
    findox_debt = fetch_findox_debt(findox_matches, debt_features_table=debt_features_table,
                                  prices_table=prices_table, app=app, market_date=market_date)
    out["xref_external_company_findox_instrument"] = findox_debt["xref"]
    debt_features = merge_debt_features_with_provenance(
        factset_features, bloomberg_features, group_features, findox_debt["features"])
    _log_external_enrichment_stage(
        "findox_fundamentals", "start", started_at
    )
    findox_lines = fetch_findox_fundamental_lines(
        findox_matches["findox_issuer_id"].dropna().astype(str).tolist()
        if not findox_matches.empty
        else [],
        asof_cutoff=asof_cutoff,
        app=app,
    )
    _log_external_enrichment_stage(
        "findox_fundamentals",
        "complete",
        started_at,
        findox_lines=findox_lines,
    )
    xref = attach_findox_issuer_ids(xref, findox_matches)
    _log_external_enrichment_stage(
        "findox_documents", "start", started_at
    )
    findox_documents = fetch_findox_public_document_index(
        findox_matches["findox_issuer_id"].dropna().astype(str).tolist()
        if not findox_matches.empty
        else [],
        app=app,
    )
    _log_external_enrichment_stage(
        "findox_documents",
        "complete",
        started_at,
        findox_documents=findox_documents,
    )

    identifier_bindings = bindings_from_xref(xref)
    crosswalk_bloomberg = (
        capital_stack if not capital_stack.empty else bloomberg_bindings
    )
    if not crosswalk_bloomberg.empty:
        identifier_bindings.extend(
            bindings_from_bloomberg_matches(crosswalk_bloomberg)
        )
    debt_identifier_xref = build_debt_identifier_crosswalk(
        identifier_bindings,
        resolved_asof_date=asof_cutoff,
    ).rename(columns={"portco_id": "external_portco_id"})

    out["xref_external_company_internal_id"] = xref.rename(
        columns={"portco_id": "external_portco_id"}
    )[EXTERNAL_XREF_COLUMNS]
    out["xref_external_company_debt_identifier"] = debt_identifier_xref
    if not capital_stack.empty:
        out["xref_external_company_capital_stack"] = capital_stack.rename(
            columns={"external_company_id": "external_portco_id"}
        )
    if not findox_matches.empty:
        out["xref_external_company_findox"] = findox_matches.rename(
            columns={"external_company_id": "external_portco_id"}
        )
    out["source_external_debt_issuer_bridge"] = build_external_debt_issuer_bridge(
        pd.concat([factset_features, debt_features], ignore_index=True))
    out["fact_external_debt_feature_snapshot"] = build_external_debt_feature_snapshot(debt_features, xref)
    from data_access.dbtables.sponsor_study.financial_history import build_entity_lineage
    financial, candidates = build_financial_outputs(debt_features, findox_lines, xref,
        cutoff=str(pd.Timestamp(asof_cutoff).normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)))
    out["source_external_financial_snapshot"] = _rename_internal_to_external(financial, EXTERNAL_FINANCIAL_COLUMNS)
    out["fact_external_financial_candidate"] = candidates
    out["fact_external_entity_lineage"] = build_entity_lineage(capital_stack, dcs, group_features,
        cutoff=str(asof_cutoff))
    core_market, core_instruments = core_price_snapshots(findox_debt["prices"])
    market = build_external_market_snapshot(synchronized_feature_quotes(debt_features, market_date), prices, xref, dcs)
    out["source_external_market_trading_snapshot"] = merge_core_market(market, core_market, findox_debt["xref"])
    out["source_external_debt_instrument_snapshot"] = build_external_debt_instrument_snapshot(debt_features, dcs, xref)
    instrument_keys = ["external_portco_id", "instrument_id_uniq"]
    existing_instruments = out["source_external_debt_instrument_snapshot"]
    new_instruments = core_instruments.merge(existing_instruments[instrument_keys].drop_duplicates(),
        on=instrument_keys, how="left", indicator=True).query("_merge == 'left_only'").drop(columns="_merge")
    out["source_external_debt_instrument_snapshot"] = pd.concat(
        [existing_instruments, new_instruments], ignore_index=True)
    out["source_external_factset_dcs_debt_detail"] = build_external_factset_dcs_source(dcs, xref)
    out["source_external_findox_document_index"] = (
        build_external_findox_document_index(findox_documents, findox_matches)
    )
    _log_external_enrichment_stage(
        "finalize",
        "complete",
        started_at,
        debt_features=out["fact_external_debt_feature_snapshot"],
        financial=out["source_external_financial_snapshot"],
        market=out["source_external_market_trading_snapshot"],
        debt_instruments=out["source_external_debt_instrument_snapshot"],
    )
    return out
