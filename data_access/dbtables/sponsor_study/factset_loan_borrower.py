"""Follow dated issuer/credit-parent loan securities to the same WSO borrower."""
from datetime import date
import re

from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.debt_instrument_aliases import loan_debt_key_ctes_sql

LOAN_BORROWER_RELATION = "same_wso_borrower_as_company_security"


def factset_loan_borrower_sql(
    seeds: tuple[FactSetDebtSeed, ...], *, table_name: str,
    factset_catalog: str, asof_cutoff: date | None,
) -> str:
    """Use dated loan observations and current reference IDs as financing evidence.

    Conflicting security entities need a dated direct-subsidiary relationship.
    Borrower continuity does not establish company identity equivalence.
    """
    for name in (table_name, factset_catalog):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name):
            raise ValueError("Invalid loan borrower source identifier")
    if not seeds or "." not in table_name:
        raise ValueError("Loan borrower matching requires seeds and a qualified debt table")
    schema = table_name.rsplit(".", 1)[0]
    values = ",".join(f"('{s.portco_id}','{s.factset_entity_id}')"
        for s in sorted(set(seeds), key=lambda s: (s.portco_id, s.factset_entity_id)))
    cutoff = f"WHERE CAST(asof_date AS DATE) <= DATE '{asof_cutoff.isoformat()}'" if asof_cutoff else ""
    loan_keys = "\nUNION ALL\n".join(
        f"SELECT s.*, 'cusip' AS identifier_kind, '{key}' AS key_type, UPPER(TRIM(c.{key})) AS identifier FROM dated_loan_securities s "
        f"JOIN {factset_catalog}.sym_v1.sym_cusip_loan c ON c.fsym_id=s.fsym_id WHERE c.{key} IS NOT NULL"
        for key in ("cusip", "ppn_cusip", "pcl_cusip"))
    loan_keys += f"""
UNION ALL SELECT s.*,'figi' AS identifier_kind,'figi' AS key_type,UPPER(TRIM(c.bbg_id)) AS identifier
FROM dated_loan_securities s JOIN {factset_catalog}.sym_v1.sym_bbg c ON c.fsym_id=s.fsym_id
WHERE c.bbg_id IS NOT NULL"""
    return f"""
WITH seeds(portco_id,factset_entity_id) AS (VALUES {values}), latest_date AS (
    SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name} {cutoff}
), debt_observations AS (
    SELECT * FROM {table_name} WHERE CAST(asof_date AS DATE)=(SELECT asof_date FROM latest_date)
      AND LOWER(instrument_type)='loan' AND instrument_id_uniq IS NOT NULL
), {loan_debt_key_ctes_sql()}, dated_issuer_history AS (
    SELECT * FROM {factset_catalog}.sym_v1.sym_sec_entity_hist
    WHERE start_date <= (SELECT asof_date FROM latest_date)
      AND (end_date IS NULL OR end_date >= (SELECT asof_date FROM latest_date))
), dated_issuers AS (
    SELECT * FROM dated_issuer_history
    QUALIFY MIN(factset_entity_id) OVER (PARTITION BY fsym_id)=MAX(factset_entity_id) OVER (PARTITION BY fsym_id)
), dated_credit_history AS (
    SELECT * FROM {factset_catalog}.sym_v1.sym_credit_parent_hist
    WHERE start_date <= (SELECT asof_date FROM latest_date)
      AND (end_date IS NULL OR end_date >= (SELECT asof_date FROM latest_date))
), dated_credit_parents AS (
    SELECT * FROM dated_credit_history
    QUALIFY MIN(factset_cred_parent_entity_id) OVER (PARTITION BY fsym_id)
        =MAX(factset_cred_parent_entity_id) OVER (PARTITION BY fsym_id)
), dated_loan_securities AS (
    SELECT DISTINCT c.fsym_id,h.factset_entity_id AS security_issuer_id,
        p.factset_cred_parent_entity_id AS security_credit_parent_id
    FROM {factset_catalog}.sym_v1.sym_coverage c
    LEFT JOIN dated_issuer_history h ON h.fsym_id=c.fsym_id
    LEFT JOIN dated_credit_history p ON p.fsym_id=c.fsym_id
    WHERE c.fref_security_type='BANK_LOAN'
), security_anchors AS (
    SELECT s.*,h.fsym_id,'dated_security_issuer' AS anchor_relationship,
        h.start_date AS anchor_relationship_start_date,h.end_date AS anchor_relationship_end_date
    FROM seeds s JOIN dated_issuers h ON h.factset_entity_id=s.factset_entity_id
    UNION ALL
    SELECT s.*,h.fsym_id,'dated_credit_parent' AS anchor_relationship,h.start_date,h.end_date
    FROM seeds s JOIN dated_credit_parents h ON h.factset_cred_parent_entity_id=s.factset_entity_id
), own_securities AS (
    SELECT DISTINCT a.* FROM security_anchors a
    JOIN dated_loan_securities c ON c.fsym_id=a.fsym_id
), security_keys AS ({loan_keys}), loan_keys AS (
    SELECT DISTINCT a.*,h.factset_entity_id AS security_issuer_id,
        p.factset_cred_parent_entity_id AS security_credit_parent_id,k.identifier_kind,k.key_type,k.identifier
    FROM own_securities a JOIN security_keys k ON k.fsym_id=a.fsym_id
    LEFT JOIN dated_issuers h ON h.fsym_id=a.fsym_id
    LEFT JOIN dated_credit_parents p ON p.fsym_id=a.fsym_id
), unique_facility_keys AS (
    SELECT CASE WHEN UPPER(type)='FIGI' THEN 'figi' ELSE 'cusip' END AS identifier_kind,
        UPPER(TRIM(value)) AS identifier,MIN(wso_data_facility_id) AS wso_data_facility_id
    FROM {schema}.wso_loan_identifiers WHERE UPPER(type) IN ('CUSIP','PPN','FIGI') AND TRIM(value)<>''
    GROUP BY CASE WHEN UPPER(type)='FIGI' THEN 'figi' ELSE 'cusip' END,UPPER(TRIM(value))
    HAVING COUNT(DISTINCT wso_data_facility_id)=1
), dated_direct_owners AS (
    SELECT DISTINCT factset_entity_id,factset_parent_entity_id
    FROM {factset_catalog}.ent_v1.ent_entity_str_hist
    WHERE start_date <= (SELECT asof_date FROM latest_date)
      AND (end_date IS NULL OR end_date > (SELECT asof_date FROM latest_date))
    QUALIFY MIN(COALESCE(factset_parent_entity_id,'')) OVER (PARTITION BY factset_entity_id)
        =MAX(COALESCE(factset_parent_entity_id,'')) OVER (PARTITION BY factset_entity_id)
), target_entities AS (
    SELECT f.wso_data_facility_id,
        MIN(k.security_issuer_id) AS target_issuer_min,MAX(k.security_issuer_id) AS target_issuer_max,
        MIN(k.security_credit_parent_id) AS target_parent_min,MAX(k.security_credit_parent_id) AS target_parent_max,
        MIN(o.factset_parent_entity_id) AS target_issuer_owner
    FROM unique_facility_keys f JOIN security_keys k
        ON k.identifier=f.identifier AND k.identifier_kind=f.identifier_kind
    LEFT JOIN dated_direct_owners o ON o.factset_entity_id=k.security_issuer_id
    GROUP BY f.wso_data_facility_id
), dated_facilities AS (
    SELECT DISTINCT wso_data_facility_id,wso_data_bank_deal_id,CAST(asset_effective_date AS DATE) AS effective_date
    FROM {schema}.wso_loan_facility
    QUALIFY MIN(wso_data_bank_deal_id) OVER (PARTITION BY wso_data_facility_id)
        =MAX(wso_data_bank_deal_id) OVER (PARTITION BY wso_data_facility_id)
        AND MIN(asset_effective_date) OVER (PARTITION BY wso_data_facility_id)
        =MAX(asset_effective_date) OVER (PARTITION BY wso_data_facility_id)
        AND effective_date > DATE '1900-01-01' AND effective_date <= (SELECT asof_date FROM latest_date)
        AND COALESCE(is_synthetic,FALSE)=FALSE
), dated_deals AS (
    SELECT DISTINCT wso_data_bank_deal_id,wso_data_issuer_id,CAST(credit_date AS DATE) AS credit_date
    FROM {schema}.wso_loan_bank_deal
    QUALIFY MIN(wso_data_issuer_id) OVER (PARTITION BY wso_data_bank_deal_id)
        =MAX(wso_data_issuer_id) OVER (PARTITION BY wso_data_bank_deal_id)
        AND MIN(credit_date) OVER (PARTITION BY wso_data_bank_deal_id)
        =MAX(credit_date) OVER (PARTITION BY wso_data_bank_deal_id)
        AND credit_date > DATE '1900-01-01' AND credit_date <= (SELECT asof_date FROM latest_date)
), loanx_facilities AS (
    SELECT UPPER(TRIM(value)) AS loanx_id,MIN(wso_data_facility_id) AS wso_data_facility_id
    FROM {schema}.wso_loan_identifiers WHERE UPPER(type)='LOANX' AND TRIM(value)<>''
    GROUP BY UPPER(TRIM(value)) HAVING COUNT(DISTINCT wso_data_facility_id)=1
), unique_loanx AS (
    SELECT * FROM loanx_facilities QUALIFY COUNT(*) OVER (PARTITION BY wso_data_facility_id)=1
), anchors AS (
    SELECT k.*,c.wso_data_facility_id AS anchor_facility_id,f.wso_data_bank_deal_id AS anchor_deal_id,
        f.effective_date AS anchor_effective_date,i.credit_date AS anchor_credit_date,i.wso_data_issuer_id
    FROM loan_keys k JOIN unique_facility_keys c ON c.identifier=k.identifier AND c.identifier_kind=k.identifier_kind
    JOIN dated_facilities f ON f.wso_data_facility_id=c.wso_data_facility_id
    JOIN dated_deals i ON i.wso_data_bank_deal_id=f.wso_data_bank_deal_id
), borrower_facilities AS (
    SELECT a.*,f.wso_data_facility_id AS target_facility_id,i.wso_data_bank_deal_id AS target_deal_id,
        f.effective_date AS target_effective_date,i.credit_date AS target_credit_date,
        t.target_issuer_min,t.target_issuer_max,t.target_parent_min,t.target_parent_max,t.target_issuer_owner,
        CASE WHEN t.target_issuer_min<>t.target_issuer_max OR t.target_parent_min<>t.target_parent_max THEN 'ambiguous'
            WHEN COALESCE(t.target_issuer_min,t.target_parent_min) IS NULL THEN 'not_available'
            WHEN t.target_issuer_min IN (a.factset_entity_id,a.security_issuer_id,a.security_credit_parent_id)
                OR t.target_parent_min IN (a.factset_entity_id,a.security_issuer_id,a.security_credit_parent_id) THEN 'consistent'
            WHEN t.target_issuer_owner IN (a.factset_entity_id,a.security_issuer_id,a.security_credit_parent_id)
                THEN 'dated_direct_subsidiary'
            ELSE 'conflicting' END AS target_entity_check
    FROM anchors a JOIN dated_deals i ON i.wso_data_issuer_id=a.wso_data_issuer_id
    JOIN dated_facilities f ON f.wso_data_bank_deal_id=i.wso_data_bank_deal_id
    LEFT JOIN target_entities t ON t.wso_data_facility_id=f.wso_data_facility_id
)
SELECT b.*,a.portco_id,a.factset_entity_id AS portco_factset_entity_id,
    CAST(NULL AS STRING) AS debt_factset_entity_id,
    CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
    CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,
    CAST(b.instrument_id_uniq AS STRING) AS debt_feature_key,
    'factset_security_wso_borrower_exact' AS debt_bridge_match_method,
    'high' AS debt_bridge_confidence,'{LOAN_BORROWER_RELATION}' AS debt_bridge_relation,
    'factset_loan_borrower' AS debt_bridge_source_system,
    CASE WHEN a.target_entity_check IN ('conflicting','ambiguous') THEN 'conflicting_wso_borrower'
        ELSE 'exact_wso_borrower' END AS debt_attribution_status,
    'consolidated_group' AS debt_attribution_scope,
    CAST(CAST(b.id_bb_company AS BIGINT) AS STRING) AS debt_group_bbg_company_id,
    b.issuer_name AS debt_group_name,
    CAST(CAST(b.id_bb_company AS BIGINT) AS STRING) AS debt_borrower_bbg_company_id,
    b.issuer_name AS debt_borrower_name,
    CONCAT_WS('->',a.factset_entity_id,CONCAT(a.anchor_relationship,':',a.fsym_id),CONCAT(a.key_type,':',a.identifier),
        CONCAT('wso_facility:',CAST(a.anchor_facility_id AS STRING)),
        CONCAT('wso_issuer:',CAST(a.wso_data_issuer_id AS STRING)),
        CONCAT('wso_facility:',CAST(a.target_facility_id AS STRING)),x.loanx_id) AS debt_bridge_source,
    TO_JSON(NAMED_STRUCT(
        'asof_date',CAST(b.asof_date AS DATE),'factset_company_id',a.factset_entity_id,
        'factset_anchor_security_id',a.fsym_id,'anchor_facility_id',a.anchor_facility_id,
        'anchor_deal_id',a.anchor_deal_id,'wso_issuer_id',a.wso_data_issuer_id,
        'target_facility_id',a.target_facility_id,'target_deal_id',a.target_deal_id,
        'anchor_effective_date',a.anchor_effective_date,'target_effective_date',a.target_effective_date,
        'anchor_credit_date',a.anchor_credit_date,'target_credit_date',a.target_credit_date,
        'anchor_relationship',a.anchor_relationship,
        'anchor_relationship_start_date',a.anchor_relationship_start_date,
        'anchor_relationship_end_date',a.anchor_relationship_end_date,
        'anchor_issuer_id',a.security_issuer_id,'anchor_credit_parent_id',a.security_credit_parent_id,
        'target_issuer_min',a.target_issuer_min,'target_issuer_max',a.target_issuer_max,
        'target_parent_min',a.target_parent_min,'target_parent_max',a.target_parent_max,
        'target_issuer_owner',a.target_issuer_owner,
        'target_entity_check',a.target_entity_check,
        'reference_scope','current_identifiers_with_dated_loan_observations')) AS debt_relationship_path
FROM borrower_facilities a
JOIN unique_loanx x ON x.wso_data_facility_id=a.target_facility_id
JOIN debt_loanx_keys k ON k.loanx_id=x.loanx_id
JOIN debt_base b ON UPPER(TRIM(b.instrument_id_uniq))=k.canonical_instrument_id
QUALIFY ROW_NUMBER() OVER (PARTITION BY a.portco_id,a.factset_entity_id,b.instrument_id_uniq
    ORDER BY CASE WHEN a.target_entity_check IN ('conflicting','ambiguous') THEN 1 ELSE 0 END,
        CASE WHEN a.anchor_facility_id=a.target_facility_id THEN 0 ELSE 1 END,
        CASE WHEN a.anchor_deal_id=a.target_deal_id THEN 0 ELSE 1 END,
        CASE WHEN a.anchor_relationship='dated_security_issuer' THEN 0 ELSE 1 END,
        a.anchor_effective_date DESC,a.fsym_id,a.key_type,a.identifier,a.anchor_facility_id)=1
"""
