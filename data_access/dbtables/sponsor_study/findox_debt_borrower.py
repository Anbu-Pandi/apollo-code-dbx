"""Corroborate financing continuity with exact FactSet, Findox and WSO keys."""
from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Literal

import pandas as pd

from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed


@dataclass(frozen=True)
class BorrowerObservation:
    company_id: str
    factset_entity_id: str
    instrument_id: str
    asof_date: date

    def __post_init__(self) -> None:
        FactSetDebtSeed(self.company_id,self.factset_entity_id)
        if not self.instrument_id.strip() or type(self.asof_date) is not date:
            raise ValueError('Borrower corroboration requires an instrument and observation date')


def _observation(row) -> BorrowerObservation:
    return BorrowerObservation(str(row.portco_id),str(row.portco_factset_entity_id),
        str(row.instrument_id_uniq).strip().upper(),pd.Timestamp(row.asof_date).date())


def corroborate_findox_borrowers(candidates: pd.DataFrame,wso: pd.DataFrame) -> pd.DataFrame:
    """Two independent credit ledgers may support group debt across legal entities."""
    if candidates.empty:
        return candidates
    required=['portco_id','portco_factset_entity_id','instrument_id_uniq','asof_date']
    for frame in (candidates,wso):
        if not frame.empty and frame[required].isna().any().any():
            raise ValueError('Borrower corroboration requires complete observation keys')
    proofs: dict[BorrowerObservation,dict[int,str]] = {}
    ambiguous: set[BorrowerObservation] = set()
    for row in wso.itertuples(index=False):
        if row.debt_attribution_status not in ('exact_wso_borrower','conflicting_wso_borrower'):
            continue
        path=json.loads(row.debt_relationship_path)
        key=_observation(row)
        if path['target_entity_check']=='ambiguous':
            ambiguous.add(key)
            continue
        by_borrower=proofs.setdefault(key,{})
        borrower_id=int(path['wso_issuer_id'])
        by_borrower[borrower_id]=min(by_borrower.get(borrower_id,row.debt_relationship_path),row.debt_relationship_path)
    result=candidates.copy()
    statuses=[]
    paths=[]
    for row in result.itertuples(index=False):
        key=_observation(row)
        choices=proofs.get(key,{})
        wso_path=next(iter(choices.values())) if len(choices)==1 and key not in ambiguous else None
        path=json.loads(row.debt_relationship_path)
        statuses.append('corroborated_findox_wso_borrower' if wso_path else 'unverified_findox_borrower')
        if wso_path:
            path['wso_borrower_evidence']=json.loads(wso_path)
        paths.append(json.dumps(path,sort_keys=True,separators=(',',':')))
    result['debt_attribution_status']=statuses
    result['debt_relationship_path']=paths
    return result


@dataclass(frozen=True)
class DirectBorrowerOwnership:
    company_entity_id: str
    borrower_entity_id: str
    borrower_security_id: str
    relationship: Literal['dated_parent_borrower','dated_owned_borrower']
    start_date: date
    end_date: date | None
    asof_date: date

    def __post_init__(self) -> None:
        FactSetDebtSeed('company',self.company_entity_id)
        FactSetDebtSeed('borrower',self.borrower_entity_id)
        if any(not re.fullmatch(r'[A-Za-z0-9]+-E',value) for value in (self.company_entity_id,self.borrower_entity_id)):
            raise ValueError('Direct borrower ownership requires FactSet entity identifiers')
        if self.relationship not in ('dated_parent_borrower','dated_owned_borrower'):
            raise ValueError('Findox corroboration requires direct borrower ownership')
        if self.company_entity_id==self.borrower_entity_id or not re.fullmatch(r'[A-Za-z0-9]+-S',self.borrower_security_id):
            raise ValueError('Direct group ownership requires separate company and borrower entities')
        if type(self.start_date) is not date or type(self.asof_date) is not date:
            raise ValueError('Direct borrower ownership requires dated evidence')
        if self.start_date>self.asof_date or (self.end_date is not None and self.end_date<=self.asof_date):
            raise ValueError('Direct borrower ownership must be effective on the debt observation date')


def corroborate_findox_direct_ownership(candidates: pd.DataFrame) -> pd.DataFrame:
    """A separate dated ownership edge may corroborate the exact Findox credit pool."""
    if candidates.empty:
        return candidates
    result=candidates.reset_index(drop=True).copy()
    selected=result.debt_attribution_status.eq('unverified_findox_borrower') & result.findox_direct_relationship.notna()
    for row in result[selected].itertuples():
        proof=DirectBorrowerOwnership(str(row.portco_factset_entity_id),str(row.findox_borrower_entity_id),
            str(row.findox_borrower_security_id),row.findox_direct_relationship,
            pd.Timestamp(row.findox_ownership_start_date).date(),
            None if pd.isna(row.findox_ownership_end_date) else pd.Timestamp(row.findox_ownership_end_date).date(),
            pd.Timestamp(row.asof_date).date())
        path=json.loads(row.debt_relationship_path)
        path['factset_direct_ownership']={
            'company_entity_id':proof.company_entity_id,'borrower_entity_id':proof.borrower_entity_id,
            'borrower_security_id':proof.borrower_security_id,'relationship':proof.relationship,
            'start_date':proof.start_date.isoformat(),'end_date':proof.end_date.isoformat() if proof.end_date else None,
            'asof_date':proof.asof_date.isoformat(),
        }
        result.at[row.Index,'debt_attribution_status']='corroborated_findox_direct_group'
        result.at[row.Index,'debt_relationship_path']=json.dumps(path,sort_keys=True,separators=(',',':'))
    return result


def findox_debt_borrower_sql(seeds: tuple[FactSetDebtSeed,...],*,table_name: str,
    factset_catalog: str,source_columns: tuple[str,...],asof_cutoff: date | None,
    sponsor_entity_ids: frozenset[str]=frozenset()) -> str:
    """Return candidates; a Findox issuer pool alone does not admit group borrowing."""
    from data_access.dbtables.sponsor_study.factset_direct_group_debt import direct_factset_group_base_sql
    base=direct_factset_group_base_sql(seeds,table_name=table_name,factset_catalog=factset_catalog,
        source_columns=source_columns,asof_cutoff=asof_cutoff,sponsor_entity_ids=sponsor_entity_ids)
    schema=table_name.rsplit('.',1)[0]
    return f"""{base}, own_securities AS (
    SELECT s.*,h.fsym_id,h.start_date AS issuer_start_date,h.end_date AS issuer_end_date
    FROM seeds s JOIN dated_issuers h ON h.factset_entity_id=s.factset_entity_id
    JOIN {factset_catalog}.sym_v1.sym_coverage c ON c.fsym_id=h.fsym_id
    WHERE c.fref_security_type IN ('BANK_LOAN','CORP','MUNI')
), anchor_keys AS (
    SELECT a.*,'isin' AS identifier_type,UPPER(TRIM(i.isin)) AS identifier
    FROM own_securities a JOIN {factset_catalog}.sym_v1.sym_isin i ON i.fsym_id=a.fsym_id
    UNION ALL
    SELECT a.*,'figi' AS identifier_type,UPPER(TRIM(i.bbg_id)) AS identifier
    FROM own_securities a JOIN {factset_catalog}.sym_v1.sym_bbg i ON i.fsym_id=a.fsym_id
), reference AS (
    SELECT LOWER(identifier_type_name) AS identifier_type,UPPER(SPLIT_PART(TRIM(identifier),' ',1)) AS identifier,
        MIN(issuer_id) AS findox_issuer_id,MIN(created_on_date_time) AS first_known,MIN(load_date) AS first_loaded
    FROM {schema}.findox_models_common_ids
    WHERE CAST(created_on_date_time AS DATE)<=(SELECT asof_date FROM latest_date)
      AND CAST(load_date AS DATE)<=(SELECT asof_date FROM latest_date)
      AND LOWER(identifier_type_name) IN ('isin','figi','lxid','blid')
    GROUP BY LOWER(identifier_type_name),UPPER(SPLIT_PART(TRIM(identifier),' ',1))
    HAVING COUNT(DISTINCT issuer_id)=1
), anchors AS (
    SELECT a.*,r.findox_issuer_id,r.first_known AS anchor_first_known,r.first_loaded AS anchor_first_loaded
    FROM anchor_keys a JOIN reference r ON r.identifier_type=a.identifier_type AND r.identifier=a.identifier
    QUALIFY MIN(r.findox_issuer_id) OVER(PARTITION BY a.portco_id,a.factset_entity_id)
        =MAX(r.findox_issuer_id) OVER(PARTITION BY a.portco_id,a.factset_entity_id)
)
SELECT b.*,a.portco_id,a.factset_entity_id AS portco_factset_entity_id,
    g.borrower_entity_id AS findox_borrower_entity_id,g.borrower_security_id AS findox_borrower_security_id,
    g.relationship AS findox_direct_relationship,g.start_date AS findox_ownership_start_date,g.end_date AS findox_ownership_end_date,
    CAST(NULL AS STRING) AS debt_factset_entity_id,CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
    CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,b.instrument_id_uniq AS debt_feature_key,
    'factset_findox_borrower' AS debt_bridge_source_system,'factset_findox_instrument_exact' AS debt_bridge_match_method,
    'same_findox_issuer_as_company_security' AS debt_bridge_relation,'high' AS debt_bridge_confidence,
    'unverified_findox_borrower' AS debt_attribution_status,'consolidated_group' AS debt_attribution_scope,
    CAST(CAST(b.id_bb_company AS BIGINT) AS STRING) AS debt_group_bbg_company_id,b.issuer_name AS debt_group_name,
    CAST(CAST(b.id_bb_company AS BIGINT) AS STRING) AS debt_borrower_bbg_company_id,b.issuer_name AS debt_borrower_name,
    CONCAT_WS('->',a.factset_entity_id,a.fsym_id,CONCAT(a.identifier_type,':',a.identifier),
        CONCAT('findox_issuer:',CAST(a.findox_issuer_id AS STRING)),CONCAT(r.identifier_type,':',r.identifier)) AS debt_bridge_source,
    TO_JSON(NAMED_STRUCT('asof_date',CAST(b.asof_date AS DATE),'findox_issuer_id',a.findox_issuer_id,
        'factset_company_id',a.factset_entity_id,'factset_anchor_security_id',a.fsym_id,
        'anchor_identifier_type',a.identifier_type,'anchor_identifier',a.identifier,
        'anchor_issuer_start_date',a.issuer_start_date,'anchor_issuer_end_date',a.issuer_end_date,
        'anchor_first_known',a.anchor_first_known,'anchor_first_loaded',a.anchor_first_loaded,
        'target_identifier_type',r.identifier_type,'target_identifier',r.identifier,
        'target_first_known',r.first_known,'target_first_loaded',r.first_loaded,
        'debt_key_column',k.debt_key_column)) AS debt_relationship_path
FROM anchors a JOIN reference r ON r.findox_issuer_id=a.findox_issuer_id
JOIN unique_debt_keys k ON k.identifier_type=r.identifier_type AND k.identifier=r.identifier
JOIN debt_base b ON b.instrument_id_uniq=k.instrument_id_uniq
LEFT JOIN direct_groups g ON g.portco_id=a.portco_id AND g.factset_entity_id=a.factset_entity_id
  AND g.instrument_id_uniq=b.instrument_id_uniq
QUALIFY ROW_NUMBER() OVER(PARTITION BY a.portco_id,a.factset_entity_id,b.instrument_id_uniq
    ORDER BY a.fsym_id,a.identifier_type,a.identifier,r.identifier_type,r.identifier,k.debt_key_column)=1
"""
