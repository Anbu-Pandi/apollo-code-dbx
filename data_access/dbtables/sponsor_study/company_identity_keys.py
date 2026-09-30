"""Stable source-company keys before the independent decision ledger is applied."""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyKeyObservation:
    sponsor_id: str
    name_key: str
    portco_id: str
    factset_entity_id: str | None


def canonical_company_redirects(observations: tuple[CompanyKeyObservation, ...]) -> dict[str, str]:
    """Names cannot merge distinct FactSet IDs or resolve contradictory sponsor evidence."""
    entity_owners: dict[str, set[str]] = defaultdict(set)
    owner_entities: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        if row.factset_entity_id:
            entity_owners[row.factset_entity_id].add(row.portco_id)
            owner_entities[row.portco_id].add(row.factset_entity_id)
    strong: dict[str, str] = {}
    for owners in entity_owners.values():
        if any(len(owner_entities[owner]) > 1 for owner in owners):
            continue
        canonical = min(owners)
        strong.update({owner: canonical for owner in owners if owner != canonical})
    cohorts: dict[tuple[str, str], set[str]] = defaultdict(set)
    entities: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        owner = strong.get(row.portco_id, row.portco_id)
        if row.sponsor_id and row.name_key:
            cohorts[row.sponsor_id, row.name_key].add(owner)
        if row.factset_entity_id:
            entities[owner].add(row.factset_entity_id)
    proposed: dict[str, set[str]] = defaultdict(set)
    for owners in cohorts.values():
        factset_ids = {entity for owner in owners for entity in entities[owner]}
        if len(factset_ids) > 1:
            continue
        factset_owners = {owner for owner in owners if entities[owner]}
        canonical = min(factset_owners or owners)
        for owner in owners - {canonical}:
            proposed[owner].add(canonical)
    redirects = {**strong, **{owner: next(iter(targets)) for owner, targets in proposed.items() if len(targets) == 1}}
    result = {}
    for owner in redirects:
        current, seen = owner, set()
        while current in redirects:
            if current in seen:
                raise ValueError("Company identity redirects contain a cycle")
            seen.add(current)
            current = redirects[current]
        result[owner] = current
    return result
