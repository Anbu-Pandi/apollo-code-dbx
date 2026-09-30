"""Connect audited direct ownership to exact debt instruments at the observation date."""
from datetime import date
from dataclasses import dataclass
import json
import re
from typing import Mapping

import pandas as pd

from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.issuer_tree_adapter import CorporateTreeNode, corporate_tree_nodes


@dataclass(frozen=True)
class AuditedGroupClaim:
    company_id: str
    company_entity_id: str
    borrower_entity_id: str
    group_entity_id: str
    borrower_security_id: str
    asof_date: date
    ownership_start_date: date
    ownership_end_date: date | None
    audit_type: int
    audit_id: str
    source_borrower_id: int | None

    def __post_init__(self) -> None:
        for entity in (self.company_entity_id,self.borrower_entity_id,self.group_entity_id):
            FactSetDebtSeed(self.company_id,entity)
            if not re.fullmatch(r'[A-Za-z0-9]+-E',entity):
                raise ValueError('Audited group claim requires FactSet entity identifiers')
        if (self.company_entity_id==self.borrower_entity_id
                or self.group_entity_id not in (self.company_entity_id,self.borrower_entity_id)
                or not re.fullmatch(r'[A-Za-z0-9]+-S',self.borrower_security_id)):
            raise ValueError('Audited group claim requires separate adjacent entities and a physical security')
        if (type(self.asof_date) is not date or type(self.ownership_start_date) is not date
                or self.ownership_start_date>self.asof_date
                or (self.ownership_end_date is not None and self.ownership_end_date<=self.asof_date)
                or self.audit_type<=0 or self.audit_id.strip() in ('','0')):
            raise ValueError('Audited group claim requires an effective ownership record with its source reference')


def _claim(row) -> AuditedGroupClaim:
    proof=json.loads(row.debt_relationship_path)
    return AuditedGroupClaim(str(row.portco_id),str(row.portco_factset_entity_id),
        proof['borrower_entity_id'],proof['group_entity_id'],proof['borrower_security_id'],
        pd.Timestamp(row.asof_date).date(),pd.Timestamp(proof['ownership_start_date']).date(),
        pd.Timestamp(proof['ownership_end_date']).date() if proof.get('ownership_end_date') else None,
        int(proof['ownership_audit_type']),str(proof['ownership_audit_id']),
        None if pd.isna(row.id_bb_company) else int(row.id_bb_company))


def corroborated_audited_group_paths(claim: AuditedGroupClaim,nodes: tuple[CorporateTreeNode,...],
    bloomberg_to_factset: Mapping[int,str],boundary_entities: frozenset[str],
) -> tuple[tuple[int,...],tuple[int,...],tuple[int,...]] | None:
    """Require exact legal identifiers and the same dated group in both vendors.

    Related-entity links only corroborate this audited majority-ownership claim;
    they never independently establish ownership or company identity.
    """
    by_id={node.node_id:node for node in nodes}
    if len(by_id)!=len(nodes) or len(set(bloomberg_to_factset.values()))!=len(bloomberg_to_factset):
        raise ValueError('Group corroboration requires unique company observations and legal-identifier mappings')
    reverse={entity:identity for identity,entity in bloomberg_to_factset.items()}
    entities=(claim.company_entity_id,claim.borrower_entity_id,claim.group_entity_id)
    if any(entity in boundary_entities or entity not in reverse for entity in entities):
        return None
    group_id=reverse[claim.group_entity_id]
    boundaries={identity for identity,entity in bloomberg_to_factset.items() if entity in boundary_entities}

    def path_to_group(start: int) -> tuple[int,...] | None:
        path=[]
        current=start
        while current not in path and current not in boundaries:
            node=by_id.get(current)
            if (node is None or not node.company_status or node.company_status.upper()=='INAC'
                    or node.knowledge_date is None or node.knowledge_date.date()>claim.asof_date):
                return None
            path.append(current)
            if node.parent_id in path[:-1]:
                return None
            if current==group_id:
                return tuple(path)
            if (node.parent_id is None or (node.relationship or '').casefold()
                    not in {'subsidiary','indirect subsidiary','related entities'}):
                return None
            current=node.parent_id
        return None

    starts=(reverse[claim.company_entity_id],reverse[claim.borrower_entity_id],
        claim.source_borrower_id if claim.source_borrower_id is not None else reverse[claim.borrower_entity_id])
    paths=tuple(path_to_group(start) for start in starts)
    return paths if all(path is not None for path in paths) else None


def corroborate_audited_group_rows(candidates: pd.DataFrame,nodes: tuple[CorporateTreeNode,...],
    bloomberg_to_factset: Mapping[int,str],names: Mapping[int,str],boundary_entities: frozenset[str],
) -> pd.DataFrame:
    result=candidates.reset_index(drop=True).copy()
    result['debt_attribution_status']='unverified_factset_direct_group'
    for row in result.itertuples():
        claim=_claim(row)
        paths=corroborated_audited_group_paths(claim,nodes,bloomberg_to_factset,boundary_entities)
        if paths is None:
            continue
        own_path,issuer_path,source_path=paths
        proof=json.loads(row.debt_relationship_path)
        proof['bloomberg_corroboration']={'identity_method':'exact_lei','company_to_group':own_path,
            'security_issuer_to_group':issuer_path,'source_borrower_to_group':source_path}
        result.at[row.Index,'debt_relationship_path']=json.dumps(proof,sort_keys=True,separators=(',',':'))
        result.at[row.Index,'debt_attribution_status']='corroborated_audited_direct_group'
        result.at[row.Index,'debt_group_bbg_company_id']=str(own_path[-1])
        result.at[row.Index,'debt_group_name']=names[own_path[-1]]
        result.at[row.Index,'debt_borrower_bbg_company_id']=str(source_path[0])
        result.at[row.Index,'debt_borrower_name']=names[source_path[0]]
    return result


def validate_audited_group_borrowers(candidates: pd.DataFrame,*,app: str,factset_catalog: str,
    table_name: str,sponsor_entity_ids: frozenset[str],
) -> pd.DataFrame:
    """Read independent, dated borrower affiliations for exact-identifier claims."""
    from data_access.dbtables.sponsor_study.group_debt_sources import _dated_nodes,_factset_nodes,_own_identity_map
    if candidates.empty:
        return candidates
    claims=tuple(_claim(row) for row in candidates.itertuples())
    dates={claim.asof_date for claim in claims}
    if len(dates)!=1:
        raise ValueError('Audited group source must have one observation date')
    observed=next(iter(dates))
    entities=tuple(sorted({entity for claim in claims for entity in
        (claim.company_entity_id,claim.borrower_entity_id,claim.group_entity_id)}))
    factset=_factset_nodes(entities,(),catalog=factset_catalog,app=app)
    leis=tuple(sorted(factset.lei.dropna().astype(str).unique()))
    prefix=table_name.rsplit('.',2)[0]+'.' if table_name.count('.')==2 else ''
    source=f"{prefix}dbo.f_get_bond_issuer_master(DATE '{observed.isoformat()}')"
    borrower_ids=tuple(sorted({claim.source_borrower_id for claim in claims if claim.source_borrower_id is not None}))
    nodes=_dated_nodes(borrower_ids,leis,source=source,app=app)
    frames,seen=[nodes],set()
    while not nodes.empty:
        seen.update(nodes.id_bb_company.astype(int))
        parents=set(nodes.id_bb_parent_co.dropna().astype(int))-seen
        if not parents:
            break
        seen.update(parents)
        nodes=_dated_nodes(tuple(sorted(parents)),(),source=source,app=app)
        frames.append(nodes)
    nodes=pd.concat(frames,ignore_index=True).drop_duplicates()
    if nodes.id_bb_company.duplicated().any():
        raise ValueError('Audited group issuer master contains conflicting observations')
    node_leis=tuple(sorted(nodes.legal_entity_identifier.dropna().astype(str).unique()))
    factset=_factset_nodes(entities,node_leis,catalog=factset_catalog,app=app)
    return corroborate_audited_group_rows(candidates,corporate_tree_nodes(nodes),_own_identity_map(nodes,factset),
        nodes.set_index('id_bb_company').long_comp_name.to_dict(),sponsor_entity_ids)


def direct_factset_group_base_sql(seeds: tuple[FactSetDebtSeed,...],*,table_name: str,
    factset_catalog: str,source_columns: tuple[str,...],asof_cutoff: date | None,
    sponsor_entity_ids: frozenset[str]=frozenset()) -> str:
    """Build exact physical-security matches and unambiguous dated ownership paths."""
    for name in (table_name,factset_catalog):
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',name):
            raise ValueError('Invalid borrower source identifier')
    if not seeds or '.' not in table_name or not {'asof_date','instrument_id_uniq','instrument_type'}.issubset(source_columns):
        raise ValueError('FactSet borrower lookup requires seeds and qualified debt observations')
    values=','.join(f"('{s.portco_id}','{s.factset_entity_id}')" for s in sorted(set(seeds),key=lambda s:(s.portco_id,s.factset_entity_id)))
    for entity in sponsor_entity_ids:
        FactSetDebtSeed('sponsor_boundary',entity)
    boundaries=','.join(f"('{entity}')" for entity in sorted(sponsor_entity_ids)) or "('')"
    cutoff=f"WHERE CAST(asof_date AS DATE)<=DATE '{asof_cutoff.isoformat()}'" if asof_cutoff else ''
    columns=('instrument_id_uniq','instrument_id','isin','bbg_id','instrument_id_fs','bbg_id_fs',
        'instrument_id_index','bbg_id_index','instrument_id_uniq_index')
    keys='\nUNION ALL\n'.join(f"SELECT instrument_id_uniq,'{c}' AS debt_key_column,UPPER(TRIM({c})) AS identifier FROM observations WHERE {c} IS NOT NULL" for c in columns if c in source_columns)
    return f"""WITH seeds(portco_id,factset_entity_id) AS (VALUES {values}),
sponsor_entities(factset_entity_id) AS (VALUES {boundaries}), latest_date AS (
    SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name} {cutoff}
), observations AS (
    SELECT * FROM {table_name} WHERE CAST(asof_date AS DATE)=(SELECT asof_date FROM latest_date)
      AND LOWER(instrument_type) IN ('bond','loan') AND instrument_id_uniq IS NOT NULL
), debt_base AS (
    SELECT * FROM observations QUALIFY COUNT(*) OVER(PARTITION BY UPPER(TRIM(instrument_id_uniq)))=1
), raw_debt_keys AS ({keys}), typed_debt_keys AS (
    SELECT *,CASE WHEN identifier RLIKE '^BBG[A-Z0-9]{{9}}$' THEN 'figi'
        WHEN identifier RLIKE '^LX[0-9]+$' THEN 'lxid' WHEN identifier RLIKE '^BL[0-9]+$' THEN 'blid'
        WHEN identifier RLIKE '^[A-Z]{{2}}[A-Z0-9]{{9}}[0-9]$' THEN 'isin' END AS identifier_type
    FROM raw_debt_keys
), unique_debt_keys AS (
    SELECT * FROM typed_debt_keys WHERE identifier_type IS NOT NULL
    QUALIFY MIN(UPPER(TRIM(instrument_id_uniq))) OVER(PARTITION BY identifier_type,identifier)
        =MAX(UPPER(TRIM(instrument_id_uniq))) OVER(PARTITION BY identifier_type,identifier)
), dated_issuer_history AS (
    SELECT * FROM {factset_catalog}.sym_v1.sym_sec_entity_hist
    WHERE start_date<=(SELECT asof_date FROM latest_date)
      AND (end_date IS NULL OR end_date>=(SELECT asof_date FROM latest_date))
), dated_issuers AS (
    SELECT * FROM dated_issuer_history
    QUALIFY MIN(factset_entity_id) OVER(PARTITION BY fsym_id)=MAX(factset_entity_id) OVER(PARTITION BY fsym_id)
), physical_security_keys AS (
    SELECT c.fsym_id,'isin' AS identifier_type,UPPER(TRIM(i.isin)) AS identifier
    FROM {factset_catalog}.sym_v1.sym_coverage c JOIN {factset_catalog}.sym_v1.sym_isin i ON i.fsym_id=c.fsym_id
    WHERE c.fref_security_type IN ('BANK_LOAN','CORP','MUNI')
    UNION ALL
    SELECT c.fsym_id,'figi' AS identifier_type,UPPER(TRIM(i.bbg_id)) AS identifier
    FROM {factset_catalog}.sym_v1.sym_coverage c JOIN {factset_catalog}.sym_v1.sym_bbg i ON i.fsym_id=c.fsym_id
    WHERE c.fref_security_type IN ('BANK_LOAN','CORP','MUNI')
), target_issuers AS (
    SELECT k.instrument_id_uniq,p.fsym_id,h.factset_entity_id
    FROM unique_debt_keys k JOIN physical_security_keys p
      ON p.identifier_type=k.identifier_type AND p.identifier=k.identifier
    LEFT JOIN dated_issuer_history h ON h.fsym_id=p.fsym_id
    QUALIFY MIN(COALESCE(h.factset_entity_id,'')) OVER(PARTITION BY k.instrument_id_uniq)
        =MAX(COALESCE(h.factset_entity_id,'')) OVER(PARTITION BY k.instrument_id_uniq)
      AND h.factset_entity_id IS NOT NULL
      AND ROW_NUMBER() OVER(PARTITION BY k.instrument_id_uniq ORDER BY p.fsym_id,h.start_date DESC,h.end_date)=1
), dated_direct_owners AS (
    SELECT * FROM {factset_catalog}.ent_v1.ent_entity_str_hist
    WHERE start_date<=(SELECT asof_date FROM latest_date)
      AND (end_date IS NULL OR end_date>(SELECT asof_date FROM latest_date))
    QUALIFY MIN(COALESCE(factset_parent_entity_id,'')) OVER(PARTITION BY factset_entity_id)
        =MAX(COALESCE(factset_parent_entity_id,'')) OVER(PARTITION BY factset_entity_id)
      AND ROW_NUMBER() OVER(PARTITION BY factset_entity_id ORDER BY start_date DESC,end_date)=1
), direct_group_paths AS (
    SELECT s.portco_id,s.factset_entity_id,t.instrument_id_uniq,t.fsym_id AS borrower_security_id,
        t.factset_entity_id AS borrower_entity_id,'dated_parent_borrower' AS relationship,o.start_date,o.end_date
    FROM seeds s JOIN dated_direct_owners o ON o.factset_entity_id=s.factset_entity_id
    JOIN target_issuers t ON t.factset_entity_id=o.factset_parent_entity_id
    UNION ALL
    SELECT s.portco_id,s.factset_entity_id,t.instrument_id_uniq,t.fsym_id AS borrower_security_id,
        t.factset_entity_id AS borrower_entity_id,'dated_owned_borrower' AS relationship,o.start_date,o.end_date
    FROM seeds s JOIN dated_direct_owners o ON o.factset_parent_entity_id=s.factset_entity_id
    JOIN target_issuers t ON t.factset_entity_id=o.factset_entity_id
), direct_groups AS (
    SELECT * FROM direct_group_paths
    WHERE factset_entity_id<>borrower_entity_id
      AND factset_entity_id NOT IN (SELECT factset_entity_id FROM sponsor_entities)
      AND borrower_entity_id NOT IN (SELECT factset_entity_id FROM sponsor_entities)
    QUALIFY COUNT(*) OVER(PARTITION BY portco_id,factset_entity_id,instrument_id_uniq)=1
)"""


def factset_direct_group_debt_sql(seeds: tuple[FactSetDebtSeed,...],*,table_name: str,
    factset_catalog: str,source_columns: tuple[str,...],asof_cutoff: date | None,
    sponsor_entity_ids: frozenset[str]=frozenset()) -> str:
    """Require an ownership-change audit reference; never infer identity or financials.

    Some unreferenced history rows predate the actual acquisition. Those rows
    alone cannot admit this path. Both ownership and security issuance must be
    effective on the debt date, and manager/fund parents remain boundaries.
    """
    base=direct_factset_group_base_sql(seeds,table_name=table_name,factset_catalog=factset_catalog,
        source_columns=source_columns,asof_cutoff=asof_cutoff,sponsor_entity_ids=sponsor_entity_ids)
    return f"""{base}, sponsor_parent_boundaries AS (
    SELECT factset_entity_id FROM sponsor_entities
    UNION SELECT s.factset_parent_entity_id FROM {factset_catalog}.ent_v1.ent_entity_structure s
      JOIN sponsor_entities b ON b.factset_entity_id=s.factset_entity_id
    UNION SELECT s.factset_ultimate_parent_entity_id FROM {factset_catalog}.ent_v1.ent_entity_structure s
      JOIN sponsor_entities b ON b.factset_entity_id=s.factset_entity_id
), audited_paths AS (
    SELECT a.*,h.audit_type,h.audit_id,
        sh.start_date AS security_issuer_start_date,sh.end_date AS security_issuer_end_date,
        CASE WHEN a.relationship='dated_parent_borrower' THEN a.borrower_entity_id
             ELSE a.factset_entity_id END AS group_entity_id
    FROM direct_groups a JOIN {factset_catalog}.ent_v1.ent_entity_str_hist h
      ON h.factset_entity_id=CASE WHEN a.relationship='dated_parent_borrower'
          THEN a.factset_entity_id ELSE a.borrower_entity_id END
      AND h.factset_parent_entity_id=CASE WHEN a.relationship='dated_parent_borrower'
          THEN a.borrower_entity_id ELSE a.factset_entity_id END
      AND h.start_date=a.start_date AND COALESCE(h.end_date,DATE '9999-12-31')=COALESCE(a.end_date,DATE '9999-12-31')
    JOIN dated_issuer_history sh ON sh.fsym_id=a.borrower_security_id AND sh.factset_entity_id=a.borrower_entity_id
    WHERE h.audit_type>0 AND h.audit_id IS NOT NULL AND TRIM(h.audit_id) NOT IN ('','0')
      AND NOT EXISTS (SELECT 1 FROM sponsor_parent_boundaries b
        WHERE b.factset_entity_id IN (a.factset_entity_id,a.borrower_entity_id))
    QUALIFY ROW_NUMBER() OVER(PARTITION BY a.portco_id,a.factset_entity_id,a.instrument_id_uniq
      ORDER BY sh.start_date DESC,sh.end_date,h.audit_type,h.audit_id)=1
)
SELECT b.*,a.portco_id,a.factset_entity_id AS portco_factset_entity_id,
    CAST(NULL AS STRING) AS debt_factset_entity_id,
    CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
    CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,
    CAST(b.instrument_id_uniq AS STRING) AS debt_feature_key,
    'factset_audited_direct_owner_exact_instrument' AS debt_bridge_match_method,
    'high' AS debt_bridge_confidence,'audited_direct_group_ownership' AS debt_bridge_relation,
    'factset_direct_group' AS debt_bridge_source_system,
    'unverified_factset_direct_group' AS debt_attribution_status,'consolidated_group' AS debt_attribution_scope,
    CAST(NULL AS STRING) AS debt_group_bbg_company_id,g.entity_proper_name AS debt_group_name,
    CAST(NULL AS STRING) AS debt_borrower_bbg_company_id,c.entity_proper_name AS debt_borrower_name,
    CONCAT_WS('->',a.factset_entity_id,a.relationship,a.borrower_entity_id,a.borrower_security_id,
        CAST(b.instrument_id_uniq AS STRING),CONCAT('ownership_audit:',CAST(a.audit_type AS STRING),':',a.audit_id)) AS debt_bridge_source,
    TO_JSON(NAMED_STRUCT('asof_date',CAST(b.asof_date AS DATE),'factset_company_id',a.factset_entity_id,
        'borrower_entity_id',a.borrower_entity_id,'borrower_security_id',a.borrower_security_id,
        'group_entity_id',a.group_entity_id,'relationship',a.relationship,
        'ownership_start_date',a.start_date,'ownership_end_date',a.end_date,
        'ownership_audit_type',a.audit_type,'ownership_audit_id',a.audit_id,
        'security_issuer_start_date',a.security_issuer_start_date,'security_issuer_end_date',a.security_issuer_end_date,
        'reference_scope','current_security_identifiers_with_dated_audited_ownership')) AS debt_relationship_path
FROM audited_paths a JOIN debt_base b ON b.instrument_id_uniq=a.instrument_id_uniq
LEFT JOIN {factset_catalog}.ent_v1.ent_entity_coverage c ON c.factset_entity_id=a.borrower_entity_id
LEFT JOIN {factset_catalog}.ent_v1.ent_entity_coverage g ON g.factset_entity_id=a.group_entity_id
"""
