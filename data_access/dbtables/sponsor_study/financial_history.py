"""Financial candidate decisions and dated capital-structure evidence, without inferred M&A dates."""
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from data_access.dbtables.sponsor_study.financial_waterfall import FinancialCandidate


FINANCIAL_CANDIDATE_COLUMNS = [
    'candidate_id', 'statement_id', 'external_portco_id', 'metric', 'provider', 'reporting_entity_id',
    'aggregate_company_id', 'source_row', 'period_id', 'period', 'asof', 'source_loaded_at',
    'source_version', 'source_record_id', 'reporting_scope_id', 'scope_status',
    'scope_valid_from', 'scope_valid_to', 'scope_recorded_at', 'scope_evidence_id', 'value', 'selected',
    'source_table', 'currency', 'source_value', 'fx_rate', 'source_unit',
    'selection_reason', 'candidate_json',
]


def candidate_ledger(records: list[dict]) -> pd.DataFrame:
    """Serialization boundary for the resolver's complete candidate decisions."""
    return pd.DataFrame([{**record, 'external_portco_id': record['portco_id'],
        'candidate_json': json.dumps(record, sort_keys=True, allow_nan=False)} for record in records],
        columns=FINANCIAL_CANDIDATE_COLUMNS)


@dataclass(frozen=True)
class EntityLineageObservation:
    external_portco_id: str
    source_system: str
    reporting_entity_id: str | None
    related_entity_id: str | None
    effective_asof_date: date
    source_knowledge_date: str | None
    relationship: str | None
    evidence_status: str | None
    relationship_path: str | None
    source_record_id: str | None
    evidence_json: str

    @property
    def observation_id(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()[:32]


ENTITY_LINEAGE_COLUMNS = ['lineage_observation_id', *EntityLineageObservation.__dataclass_fields__,
                          'source_version', 'source_version_kind', 'validity_basis']


def build_entity_lineage(capital_stack: pd.DataFrame, dcs: pd.DataFrame,
                         group_debt: pd.DataFrame, *, cutoff: str) -> pd.DataFrame:
    """Retain observed paths at their evidence dates; a snapshot is not an effective interval.

    Existing publication stamps retain when we ingested each observation. Missing vendor
    knowledge dates stay unknown. Repeated refreshes never overwrite an earlier run's paths.
    """
    def text(value) -> str | None:
        return str(value) if pd.notna(value) else None

    observations: list[EntityLineageObservation] = []
    for source, frame in [('bloomberg_capital_stack', capital_stack), ('factset_dcs', dcs),
                           ('consolidated_group_debt', group_debt)]:
        for row in frame.to_dict('records'):
            if source == 'bloomberg_capital_stack':
                owner, entity, related = row['external_company_id'], row.get('seed_bbg_company_id'), row.get('capital_stack_bbg_company_id')
                observed, known = cutoff, row.get('knowledge_date')
                relation, status, path = row.get('capital_stack_relationship'), row.get('stack_eligibility'), row.get('admitted_corporate_path')
                record_id = related
            elif source == 'factset_dcs':
                owner, entity, related = row['portco_id'], row.get('factset_entity_id'), row.get('factset_instrument_id')
                observed, known = row.get('dcs_relationship_asof_date', row.get('report_date')), row.get('publication_date')
                relation, status, path = row.get('dcs_entity_relationship'), row.get('dcs_attribution_status'), row.get('dcs_relationship_path')
                record_id = row.get('dcs_detail_id')
            else:
                owner, entity, related = row['portco_id'], row.get('debt_group_bbg_company_id'), row.get('debt_borrower_bbg_company_id')
                observed, known = row.get('asof_date'), None
                relation, status, path = row.get('debt_attribution_scope'), 'corroborated', row.get('debt_relationship_path')
                record_id = row.get('instrument_id_uniq')
            if pd.isna(observed):
                observed = row.get('report_date')
            if pd.isna(observed):
                continue
            # Keep source evidence intact; IDs from different vendors remain namespaced by source.
            evidence = json.dumps({key: text(row.get(key)) for key in (
                'report_date', 'publication_date', 'instrument_id_uniq', 'factset_instrument_id',
                'debt_relationship_path', 'dcs_relationship_path', 'admitted_corporate_path',
                'debt_attribution_scope', 'dcs_attribution_status', 'stack_eligibility')},
                sort_keys=True, allow_nan=False)
            observations.append(EntityLineageObservation(str(owner), source, text(entity), text(related),
                pd.Timestamp(observed).date(), text(known), text(relation), text(status), text(path),
                text(record_id), evidence))
    records = [{**asdict(item), 'lineage_observation_id': item.observation_id,
                'source_version': sha256(item.evidence_json.encode()).hexdigest(),
                'source_version_kind': 'observed_content_hash', 'validity_basis': 'observation_only'}
               for item in observations]
    return pd.DataFrame(records, columns=ENTITY_LINEAGE_COLUMNS).drop_duplicates('lineage_observation_id')


def comparison_status(left: 'FinancialCandidate', right: 'FinancialCandidate') -> str:
    """Only an explicit, evidenced reporting perimeter establishes comparability."""
    if left.scope_status != 'confirmed' or right.scope_status != 'confirmed':
        return 'unverified_reporting_scope'
    if left.reporting_scope_id != right.reporting_scope_id:
        return 'reporting_scope_changed'
    if (left.provider, left.metric, left.reporting_entity_id, left.aggregate_company_id) != (
            right.provider, right.metric, right.reporting_entity_id, right.aggregate_company_id):
        return 'reporting_basis_changed'
    return 'comparable'
