"""Other facilities in the same WSO agreement as an exactly linked company loan."""
from dataclasses import dataclass
from datetime import date
import re

import pandas as pd

from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import DCS_DEBT_RELATION
from data_access.dbtables.sponsor_study.factset_loan_security_bridge import LOAN_SECURITY_RELATION
from data_access.dbtables.sponsor_study.debt_instrument_aliases import loan_debt_key_ctes_sql

LOAN_DEAL_RELATION = "same_loan_deal_as_reported_instrument"


@dataclass(frozen=True)
class ReportedLoanAnchor:
    portco_id: str
    factset_entity_id: str
    loanx_id: str
    asof_date: date
    source_path: str

    def __post_init__(self) -> None:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (self.portco_id, self.factset_entity_id, self.loanx_id)):
            raise ValueError("Invalid reported-loan anchor identifier")
        if not self.source_path.strip() or type(self.asof_date) is not date:
            raise ValueError("Reported-loan anchors require a date and source path")


def reported_loan_anchors(features: pd.DataFrame) -> tuple[ReportedLoanAnchor, ...]:
    if features.empty:
        return ()
    rows = features.rename(columns=str.lower)
    rows = rows[rows.debt_bridge_relation.isin([DCS_DEBT_RELATION, LOAN_SECURITY_RELATION])
                & rows.instrument_type.str.lower().eq("loan") & rows.instrument_id_uniq.notna()]
    if rows[["portco_id", "portco_factset_entity_id", "asof_date", "debt_bridge_source"]].isna().any().any():
        raise ValueError("Reported-loan anchors have incomplete source lineage")
    return tuple(sorted({ReportedLoanAnchor(str(row.portco_id), str(row.portco_factset_entity_id),
        str(row.instrument_id_uniq), pd.Timestamp(row.asof_date).date(), str(row.debt_bridge_source))
        for row in rows.itertuples()}, key=lambda item: (item.portco_id, item.loanx_id, item.source_path)))


def reported_loan_deal_sql(
    anchors: tuple[ReportedLoanAnchor, ...], *, table_name: str, asof_cutoff: date | None,
) -> str:
    if not anchors or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", table_name):
        raise ValueError("Loan agreement matching requires anchors and a qualified debt table")
    schema = table_name.rsplit(".", 1)[0]
    values = ",\n".join(
        f"('{a.portco_id}', '{a.factset_entity_id}', '{a.loanx_id}', DATE '{a.asof_date.isoformat()}', "
        + "'" + a.source_path.replace("'", "''") + "')" for a in anchors
    )
    cutoff = f"WHERE CAST(asof_date AS DATE) <= DATE '{asof_cutoff.isoformat()}'" if asof_cutoff else ""
    return f"""
WITH anchors(portco_id, factset_entity_id, anchor_loanx_id, anchor_asof_date, anchor_source) AS (VALUES {values}),
latest_date AS (
    SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name} {cutoff}
), debt_observations AS (
    SELECT * FROM {table_name}
    WHERE CAST(asof_date AS DATE) = (SELECT asof_date FROM latest_date)
      AND LOWER(instrument_type) = 'loan' AND instrument_id_uniq IS NOT NULL
), {loan_debt_key_ctes_sql()}, loanx_facility AS (
    SELECT UPPER(TRIM(value)) AS loanx_id, MIN(wso_data_facility_id) AS wso_data_facility_id
    FROM {schema}.wso_loan_identifiers
    WHERE UPPER(type) = 'LOANX' AND TRIM(value) <> ''
    GROUP BY UPPER(TRIM(value)) HAVING COUNT(DISTINCT wso_data_facility_id) = 1
), unique_loanx AS (
    SELECT * FROM loanx_facility
    QUALIFY COUNT(*) OVER (PARTITION BY wso_data_facility_id) = 1
), facility_deal AS (
    SELECT wso_data_facility_id, MIN(wso_data_bank_deal_id) AS wso_data_bank_deal_id
    FROM {schema}.wso_loan_facility GROUP BY wso_data_facility_id
    HAVING COUNT(DISTINCT wso_data_bank_deal_id) = 1
), deal_issuer AS (
    SELECT wso_data_bank_deal_id, MIN(wso_data_issuer_id) AS wso_data_issuer_id
    FROM {schema}.wso_loan_bank_deal GROUP BY wso_data_bank_deal_id
    HAVING COUNT(DISTINCT wso_data_issuer_id) = 1
), verified_deals AS (
    SELECT a.*, f.wso_data_bank_deal_id, i.wso_data_issuer_id
    FROM anchors a JOIN debt_base b ON UPPER(TRIM(b.instrument_id_uniq)) = UPPER(a.anchor_loanx_id)
        AND CAST(b.asof_date AS DATE) = a.anchor_asof_date
    JOIN debt_loanx_keys k ON k.canonical_instrument_id=UPPER(TRIM(b.instrument_id_uniq))
    JOIN unique_loanx x ON x.loanx_id=k.loanx_id
    JOIN facility_deal f ON f.wso_data_facility_id = x.wso_data_facility_id
    JOIN deal_issuer i ON i.wso_data_bank_deal_id = f.wso_data_bank_deal_id
)
SELECT b.*, a.portco_id, a.factset_entity_id AS portco_factset_entity_id,
       CAST(NULL AS STRING) AS debt_factset_entity_id,
       CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
       CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,
       CAST(b.instrument_id_uniq AS STRING) AS debt_feature_key,
       'reported_loan_deal_exact' AS debt_bridge_match_method,
       'high' AS debt_bridge_confidence, '{LOAN_DEAL_RELATION}' AS debt_bridge_relation,
       'reported_loan_deal' AS debt_bridge_source_system,
       CONCAT_WS('->', a.anchor_source, CONCAT('anchor_loan:', a.anchor_loanx_id),
           CONCAT('wso_deal:', CAST(a.wso_data_bank_deal_id AS STRING)),
           CONCAT('wso_issuer:', CAST(a.wso_data_issuer_id AS STRING)),
           CONCAT('wso_facility:', CAST(f.wso_data_facility_id AS STRING)), x.loanx_id) AS debt_bridge_source
FROM verified_deals a
JOIN facility_deal f ON f.wso_data_bank_deal_id = a.wso_data_bank_deal_id
JOIN unique_loanx x ON x.wso_data_facility_id = f.wso_data_facility_id
JOIN debt_loanx_keys k ON k.loanx_id=x.loanx_id
JOIN debt_base b ON UPPER(TRIM(b.instrument_id_uniq))=k.canonical_instrument_id
WHERE UPPER(TRIM(b.instrument_id_uniq)) <> UPPER(TRIM(a.anchor_loanx_id))
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.portco_id, b.instrument_id_uniq
    ORDER BY a.factset_entity_id, a.anchor_loanx_id, a.anchor_source
) = 1
"""
