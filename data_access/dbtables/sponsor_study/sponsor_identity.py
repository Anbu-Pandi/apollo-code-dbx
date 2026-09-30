"""Exact sponsor families; generic-word similarity never establishes GP identity."""

from functools import lru_cache
import re

import pandas as pd

from data_access.dbtables.sponsor_study.benchmark_v31_sponsor_xref import (
    V31_TO_PROD_SPONSOR_NAME, SEED_SPONSOR_LEGAL_ALIASES,
)

LEGAL_SUFFIXES = frozenset({'llc', 'llp', 'lp', 'ltd', 'limited', 'inc', 'corp',
                           'corporation', 'co', 'plc', 'sa', 'ag', 'gmbh', 'ab', 'bv', 'nv', 'sas', 'sarl', 'aps'})


def _name_key(name: str) -> str:
    words = re.findall(r'[a-z0-9]+', name.lower())
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    if words and words[0] == 'the':
        words.pop(0)
    return '_'.join(words)


_ALIASES = {
    _name_key(name): _name_key(production)
    for short, production in V31_TO_PROD_SPONSOR_NAME.items()
    for name in (short, production, *SEED_SPONSOR_LEGAL_ALIASES.get(short, ()))
}
# Explicit operating-entity names observed for the existing seed families.
_ALIASES.update({
    _name_key('Bain Capital Private Equity'): _name_key('Bain Capital'),
})


@lru_cache(maxsize=20000)
def sponsor_family_key(name: str) -> str:
    key = _name_key(name)
    return _ALIASES.get(key, key)


def same_sponsor_family(left: str, right: str) -> bool:
    left_key, right_key = sponsor_family_key(left), sponsor_family_key(right)
    return bool(left_key and right_key and left_key == right_key)


def sponsor_identity_conflicts(holdings: pd.DataFrame) -> pd.DataFrame:
    columns = ['external_sponsor_id', 'sponsor_name']
    if holdings.empty:
        return pd.DataFrame(columns=columns)
    names = holdings[columns].drop_duplicates().copy()
    names['sponsor_family_key'] = names.sponsor_name.fillna('').map(sponsor_family_key)
    counts = names.groupby('external_sponsor_id').sponsor_family_key.nunique()
    return names[names.external_sponsor_id.isin(counts[counts > 1].index)]


def sponsor_lineage_errors(dataset: dict[str, pd.DataFrame]) -> set[str]:
    """Check canonical sponsor labels/IDs against the unmodified vendor relationships."""
    holdings = dataset.get('fact_external_holding', pd.DataFrame())
    if holdings.empty:
        return set()
    errors: set[str] = set()
    for source_system, table, holding_key, company_key in (
        ('sponsor_website', 'source_external_sponsor_website_holding', 'website_holding_id', 'structured_portco_id'),
        ('pitchbook', 'source_pitchbook_holding', 'pitchbook_holding_id', 'pitchbook_company_id'),
    ):
        source_rows = dataset.get(table, pd.DataFrame())
        if not source_rows.empty:
            errors.update(_source_holding_errors(holdings, source_rows, source_system, holding_key, company_key))
    scope = dataset.get('source_external_sponsor_scope', pd.DataFrame())
    if not scope.empty:
        families = scope.set_index('external_sponsor_id').sponsor_name.map(sponsor_family_key)
        expected = holdings.external_sponsor_id.map(families)
        actual = holdings.sponsor_name.map(sponsor_family_key)
        errors.update(holdings.loc[expected.notna() & actual.ne(expected), 'external_holding_id'])
    raw = dataset.get('source_factset_pevc_portco_investment', pd.DataFrame())
    if raw.empty:
        return errors
    keys = ['factset_gp_entity_id', 'factset_fund_entity_id', 'factset_portco_entity_id']
    source = raw.copy()
    source['_source_family'] = source.sponsor_name_factset.map(sponsor_family_key)
    source = source[keys + ['_source_family']].drop_duplicates()
    if source.duplicated(keys).any():
        raise ValueError('FactSet relationships have conflicting sponsor names for one GP/fund/company')
    current = holdings[holdings.holding_source_system.eq('factset_pevc')]
    matched = current.merge(source, on=keys, how='left', validate='many_to_one')
    wrong = matched._source_family.isna() | matched.sponsor_name.map(sponsor_family_key).ne(matched._source_family)
    errors.update(matched.loc[wrong, 'external_holding_id'])
    return errors


def _source_holding_errors(holdings: pd.DataFrame, source: pd.DataFrame,
                           system: str, holding_key: str, company_key: str) -> set[str]:
    required = {'holding_source_system', 'source_holding_id', 'source_company_id', 'sponsor_name'}
    if not required.issubset(holdings):
        return set(holdings.external_holding_id)
    current = holdings[holdings.holding_source_system.eq(system)]
    if current.empty:
        return set()
    if not {holding_key, company_key, 'sponsor_name'}.issubset(source):
        return set(current.external_holding_id)
    evidence = source[[holding_key, company_key, 'sponsor_name']].copy()
    evidence['_source_family'] = evidence.sponsor_name.map(sponsor_family_key)
    evidence = evidence.drop(columns='sponsor_name').rename(columns={
        holding_key: 'source_holding_id', company_key: '_source_company_id'}).drop_duplicates()
    if evidence.source_holding_id.duplicated().any():
        raise ValueError(f'{system} source holding has conflicting sponsor/company evidence')
    matched = current.merge(evidence, on='source_holding_id', how='left', validate='many_to_one')
    wrong = (matched._source_family.isna()
        | matched.sponsor_name.map(sponsor_family_key).ne(matched._source_family)
        | matched.source_company_id.ne(matched._source_company_id))
    return set(matched.loc[wrong.fillna(True), 'external_holding_id'])
