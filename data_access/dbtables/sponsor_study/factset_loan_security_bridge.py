"""Exact own-company loan securities through the producer's WSO facility keys."""
from datetime import date
import re

from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.debt_instrument_aliases import loan_debt_key_ctes_sql

LOAN_SECURITY_RELATION = "company_security_to_loan_facility"


def factset_loan_security_sql(
    seeds: tuple[FactSetDebtSeed, ...], *, table_name: str,
    factset_catalog: str, asof_cutoff: date | None,
) -> str:
    for name in (table_name, factset_catalog):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", name):
            raise ValueError("Invalid source table identifier")
    if not seeds or "." not in table_name:
        raise ValueError("Loan security matching requires seeds and a qualified debt table")
    wso_table = table_name.rsplit(".", 1)[0] + ".wso_loan_identifiers"
    seed_values = ",\n".join(
        f"('{seed.portco_id}', '{seed.factset_entity_id}')"
        for seed in sorted(set(seeds), key=lambda item: (item.portco_id, item.factset_entity_id))
    )
    cutoff = f"WHERE CAST(asof_date AS DATE) <= DATE '{asof_cutoff.isoformat()}'" if asof_cutoff else ""
    loan_keys = "\nUNION ALL\n".join(
        f"SELECT portco_id, factset_entity_id, fsym_id, '{column}' AS identifier_type, "
        f"UPPER(TRIM({column})) AS cusip FROM company_loans WHERE {column} IS NOT NULL"
        for column in ("cusip", "ppn_cusip", "pcl_cusip")
    )
    return f"""
WITH seeds(portco_id, factset_entity_id) AS (VALUES {seed_values}),
latest_date AS (
    SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name} {cutoff}
), debt_observations AS (
    SELECT * FROM {table_name}
    WHERE CAST(asof_date AS DATE) = (SELECT asof_date FROM latest_date)
      AND LOWER(instrument_type) = 'loan' AND instrument_id_uniq IS NOT NULL
), {loan_debt_key_ctes_sql()}, company_loans AS (
    SELECT DISTINCT s.*, c.fsym_id, c.cusip, c.ppn_cusip, c.pcl_cusip
    FROM seeds s
    JOIN {factset_catalog}.sym_v1.sym_sec_entity e ON e.factset_entity_id = s.factset_entity_id
    JOIN {factset_catalog}.sym_v1.sym_cusip_loan c ON c.fsym_id = e.fsym_id
), loan_keys AS ({loan_keys}), unique_cusips AS (
    SELECT UPPER(TRIM(value)) AS cusip, MIN(wso_data_facility_id) AS wso_data_facility_id
    FROM {wso_table} WHERE UPPER(type) IN ('CUSIP', 'PPN') AND TRIM(value) <> ''
    GROUP BY UPPER(TRIM(value))
    HAVING COUNT(DISTINCT wso_data_facility_id) = 1
), unique_loanx AS (
    SELECT wso_data_facility_id, MIN(UPPER(TRIM(value))) AS loanx_id
    FROM {wso_table} WHERE UPPER(type) = 'LOANX' AND TRIM(value) <> ''
    GROUP BY wso_data_facility_id
    HAVING COUNT(DISTINCT UPPER(TRIM(value))) = 1
), matches AS (
    SELECT k.*, c.wso_data_facility_id, x.loanx_id
    FROM loan_keys k JOIN unique_cusips c ON c.cusip = k.cusip
    JOIN unique_loanx x ON x.wso_data_facility_id = c.wso_data_facility_id
)
SELECT b.*, m.portco_id, m.factset_entity_id AS portco_factset_entity_id,
       CAST(NULL AS STRING) AS debt_factset_entity_id,
       CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
       CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,
       CAST(b.instrument_id_uniq AS STRING) AS debt_feature_key,
       m.fsym_id AS factset_instrument_id, m.wso_data_facility_id,
       'factset_loan_cusip_wso_exact' AS debt_bridge_match_method,
       'high' AS debt_bridge_confidence,
       '{LOAN_SECURITY_RELATION}' AS debt_bridge_relation,
       'factset_loan_security' AS debt_bridge_source_system,
       CONCAT_WS('->', m.factset_entity_id, m.fsym_id,
           CONCAT_WS(':', m.identifier_type, m.cusip),
           CONCAT_WS(':', 'wso_facility', CAST(m.wso_data_facility_id AS STRING)), m.loanx_id)
           AS debt_bridge_source
FROM matches m JOIN debt_loanx_keys k ON k.loanx_id=m.loanx_id
JOIN debt_base b ON UPPER(TRIM(b.instrument_id_uniq))=k.canonical_instrument_id
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY m.portco_id, b.instrument_id_uniq
    ORDER BY m.factset_entity_id, m.fsym_id, m.identifier_type, m.cusip, m.wso_data_facility_id
) = 1
"""
