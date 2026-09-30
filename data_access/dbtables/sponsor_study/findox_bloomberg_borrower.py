"""Corroborate exact FinDox credit references with dated Bloomberg ownership."""
from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Mapping

import pandas as pd

from data_access.dbtables.sponsor_study.factset_filing_attribution import FilingClaim,dated_filer_ownership
from data_access.dbtables.sponsor_study.findox_debt_borrower import BorrowerObservation
from data_access.dbtables.sponsor_study.issuer_tree_adapter import CorporateTreeNode,corporate_tree_nodes


@dataclass(frozen=True)
class FindoxOwnershipClaim:
    observation: BorrowerObservation
    issuer_id: int
    anchor_security_id: str
    anchor_start_date: date
    anchor_end_date: date | None
    reference_dates: tuple[date,date,date,date]
    borrower_id: int

    def __post_init__(self) -> None:
        observed=self.observation.asof_date
        if (self.issuer_id<=0 or self.borrower_id<=0
                or not re.fullmatch(r'[A-Za-z0-9]+-S',self.anchor_security_id)):
            raise ValueError('FinDox ownership requires an exact physical security and borrower')
        if (type(self.anchor_start_date) is not date or self.anchor_start_date>observed
                or (self.anchor_end_date is not None and self.anchor_end_date<observed)
                or len(self.reference_dates)!=4
                or any(type(value) is not date or value>observed for value in self.reference_dates)):
            raise ValueError('FinDox security ownership and reference evidence must precede the debt observation')


def _claim(row) -> FindoxOwnershipClaim:
    proof=json.loads(row.debt_relationship_path)
    observed=pd.Timestamp(row.asof_date).date()
    if (proof['factset_company_id']!=row.portco_factset_entity_id
            or pd.Timestamp(proof['asof_date']).date()!=observed):
        raise ValueError('FinDox proof must identify the same company and debt date')
    patterns={'figi':r'BBG[A-Z0-9]{9}','isin':r'[A-Z]{2}[A-Z0-9]{9}[0-9]',
        'lxid':r'LX[0-9]+','blid':r'BL[0-9]+'}
    for prefix in ('anchor','target'):
        kind=proof[prefix+'_identifier_type']
        if (kind not in patterns or (prefix=='anchor' and kind not in ('figi','isin'))
                or not re.fullmatch(patterns[kind],proof[prefix+'_identifier'])):
            raise ValueError('FinDox proof requires typed, exact security identifiers')
    column=proof['debt_key_column']
    if (column not in {'instrument_id_uniq','instrument_id','isin','bbg_id','instrument_id_fs','bbg_id_fs',
            'instrument_id_index','bbg_id_index','instrument_id_uniq_index'}
            or str(getattr(row,column)).strip().upper()!=proof['target_identifier']):
        raise ValueError('FinDox target identifier must match the original debt row')
    return FindoxOwnershipClaim(
        BorrowerObservation(str(row.portco_id),str(row.portco_factset_entity_id),
            str(row.instrument_id_uniq),observed),int(proof['findox_issuer_id']),
        proof['factset_anchor_security_id'],pd.Timestamp(proof['anchor_issuer_start_date']).date(),
        pd.Timestamp(proof['anchor_issuer_end_date']).date() if proof.get('anchor_issuer_end_date') else None,
        tuple(pd.Timestamp(proof[field]).date() for field in
            ('anchor_first_known','anchor_first_loaded','target_first_known','target_first_loaded')),
        int(row.id_bb_company))


def corroborate_findox_bloomberg_rows(candidates: pd.DataFrame,nodes: tuple[CorporateTreeNode,...],
    bloomberg_to_factset: Mapping[int,str],names: Mapping[int,str],boundary_entities: frozenset[str],
) -> pd.DataFrame:
    """Require an exact own-company legal ID and explicit parent/subsidiary path.

    FinDox independently connects the owned security and target instrument to one
    credit issuer. Shared parents, related-entity edges and missing borrower IDs
    are insufficient; successful attribution remains consolidated group debt.
    """
    if len(set(bloomberg_to_factset.values()))!=len(bloomberg_to_factset):
        raise ValueError('FinDox ownership requires unambiguous own-company identities')
    reverse={entity:identity for identity,entity in bloomberg_to_factset.items()}
    boundaries=frozenset(identity for identity,entity in bloomberg_to_factset.items() if entity in boundary_entities)
    by_id={node.node_id:node for node in nodes}
    if len(by_id)!=len(nodes):
        raise ValueError('FinDox ownership requires unique dated company observations')
    result=candidates.reset_index(drop=True).copy()
    selected=result.debt_attribution_status.eq('unverified_findox_borrower') & result.id_bb_company.notna()
    for row in result[selected].itertuples():
        claim=_claim(row)
        observation=claim.observation
        if observation.factset_entity_id in boundary_entities:
            continue
        company_id=reverse.get(observation.factset_entity_id)
        ownership=dated_filer_ownership(FilingClaim(observation.company_id,observation.factset_entity_id,
            observation.factset_entity_id,observation.asof_date),nodes,company_id=company_id,
            filer_id=claim.borrower_id,boundary_ids=boundaries)
        if ownership is None:
            continue
        group_id=claim.borrower_id if ownership.relationship=='dated_parent_filer' else company_id
        proof=json.loads(row.debt_relationship_path)
        proof['bloomberg_borrower_ownership']={'identity_method':'exact_own_company_lei',
            'company_id':company_id,'borrower_id':claim.borrower_id,'group_id':group_id,
            'relationship':ownership.relationship,'path':ownership.path,
            'node_observations':[{'id':identity,'parent_id':by_id[identity].parent_id,
                'relationship':by_id[identity].relationship,
                'knowledge_date':by_id[identity].knowledge_date.isoformat()} for identity in ownership.path]}
        result.at[row.Index,'debt_relationship_path']=json.dumps(proof,sort_keys=True,separators=(',',':'))
        result.at[row.Index,'debt_attribution_status']='corroborated_findox_bloomberg_group'
        result.at[row.Index,'debt_attribution_scope']='consolidated_group'
        result.at[row.Index,'debt_group_bbg_company_id']=str(group_id)
        result.at[row.Index,'debt_group_name']=names[group_id]
        result.at[row.Index,'debt_borrower_bbg_company_id']=str(claim.borrower_id)
        result.at[row.Index,'debt_borrower_name']=names[claim.borrower_id]
    return result


def validate_findox_bloomberg_borrowers(candidates: pd.DataFrame,*,app: str,factset_catalog: str,
    table_name: str,sponsor_entity_ids: frozenset[str],
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.group_debt_sources import _dated_nodes,_factset_nodes,_own_identity_map
    if candidates.empty:
        return candidates
    selected=candidates[candidates.debt_attribution_status.eq('unverified_findox_borrower') & candidates.id_bb_company.notna()]
    if selected.empty:
        return candidates
    claims=tuple(_claim(row) for row in selected.itertuples())
    dates={claim.observation.asof_date for claim in claims}
    if len(dates)!=1:
        raise ValueError('FinDox ownership requires one debt observation date')
    observed=next(iter(dates))
    entities=tuple(sorted({claim.observation.factset_entity_id for claim in claims}))
    factset=_factset_nodes(entities,(),catalog=factset_catalog,app=app)
    leis=tuple(sorted(factset.lei.dropna().astype(str).unique()))
    borrower_ids=tuple(sorted({claim.borrower_id for claim in claims}))
    prefix=table_name.rsplit('.',2)[0]+'.' if table_name.count('.')==2 else ''
    source=f"{prefix}dbo.f_get_bond_issuer_master(DATE '{observed.isoformat()}')"
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
        raise ValueError('FinDox ownership source contains conflicting observations')
    node_leis=tuple(sorted(nodes.legal_entity_identifier.dropna().astype(str).unique()))
    factset=_factset_nodes(entities,node_leis,catalog=factset_catalog,app=app)
    return corroborate_findox_bloomberg_rows(candidates,corporate_tree_nodes(nodes),_own_identity_map(nodes,factset),
        nodes.set_index('id_bb_company').long_comp_name.to_dict(),sponsor_entity_ids)
