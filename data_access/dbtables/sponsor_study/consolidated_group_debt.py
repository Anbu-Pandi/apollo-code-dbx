"""Dated group-debt attribution without changing portfolio-company identity."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
import json
from typing import Literal, Mapping

import pandas as pd

from data_access.dbtables.sponsor_study.issuer_tree_adapter import CorporateTreeNode


GROUP_DEBT_COLUMNS = [
    "debt_attribution_scope", "debt_group_bbg_company_id", "debt_group_name",
    "debt_borrower_bbg_company_id", "debt_borrower_name", "debt_relationship_path",
]


@dataclass(frozen=True)
class CompanyDebtSeed:
    company_id: str
    bloomberg_company_id: int


@dataclass(frozen=True)
class ConsolidatedDebtPath:
    company_id: str
    seed_id: int
    group_id: int
    borrower_id: int
    company_to_group: tuple[int, ...]
    group_to_borrower: tuple[int, ...]
    ownership_asof_date: date
    factset_group_id: str | None = None
    factset_group_match_method: Literal["exact_group_lei", "shared_direct_parent"] | None = None


@dataclass(frozen=True)
class FactSetGroupNode:
    entity_id: str
    parent_id: str | None
    ultimate_id: str | None


def corroborated_group_debt_paths(
    paths: tuple[ConsolidatedDebtPath, ...],
    factset_nodes: tuple[FactSetGroupNode, ...],
    bloomberg_to_factset: Mapping[int, str],
    company_to_factset: Mapping[str, str],
    *, sponsor_entity_ids: frozenset[str] = frozenset(),
) -> tuple[ConsolidatedDebtPath, ...]:
    """Require exact FactSet ancestor support on both sides of a dated group path.

    Identity maps must contain only unambiguous own-entity identifier matches.
    Current FactSet structure corroborates, but cannot establish historical dates;
    the caller's dated Bloomberg path remains required for that purpose.
    """
    by_id = {node.entity_id: node for node in factset_nodes}
    if len(by_id) != len(factset_nodes):
        raise ValueError("Group corroboration requires unique FactSet entities")

    def ancestors(entity_id: str | None) -> set[str]:
        found: set[str] = set()
        while entity_id is not None and entity_id not in sponsor_entity_ids:
            if entity_id in found:
                return set()
            found.add(entity_id)
            node = by_id.get(entity_id)
            if node is None:
                break
            if node.parent_id in sponsor_entity_ids:
                break
            if node.ultimate_id is not None and node.ultimate_id not in sponsor_entity_ids:
                found.add(node.ultimate_id)
            if node.parent_id in (None, entity_id, node.ultimate_id):
                if node.parent_id is not None:
                    found.add(node.parent_id)
                break
            entity_id = node.parent_id
        return found

    result = []
    for path in paths:
        seed = company_to_factset.get(path.company_id)
        group = bloomberg_to_factset.get(path.group_id)
        borrower = bloomberg_to_factset.get(path.borrower_id)
        if (seed is None or borrower is None or bloomberg_to_factset.get(path.seed_id) != seed
                or seed in sponsor_entity_ids or borrower in sponsor_entity_ids):
            continue
        if group is not None:
            if group in ancestors(seed) and group in ancestors(borrower):
                result.append(replace(path, factset_group_id=group, factset_group_match_method="exact_group_lei"))
            continue
        # Two exact leaf identities and the same direct parent in both providers
        # corroborate a borrowing group even when its Bloomberg LEI is missing.
        # Ultimate-parent overlap or longer paths cannot authorize this fallback.
        own_node, debt_node = by_id.get(seed), by_id.get(borrower)
        if (len(path.company_to_group) == len(path.group_to_borrower) == 2
                and own_node is not None and debt_node is not None
                and own_node.parent_id is not None and own_node.parent_id == debt_node.parent_id
                and own_node.parent_id not in sponsor_entity_ids | {seed, borrower}):
            result.append(replace(path, factset_group_id=own_node.parent_id,
                factset_group_match_method="shared_direct_parent"))
    return tuple(result)


def consolidated_debt_paths(
    seeds: tuple[CompanyDebtSeed, ...],
    nodes: tuple[CorporateTreeNode, ...],
    borrower_ids: frozenset[int],
    sponsor_ids: frozenset[int],
    *,
    asof_date: date,
) -> tuple[ConsolidatedDebtPath, ...]:
    """Find each borrower's nearest shared group, excluding own-subtree debt.

    Every traversed edge must explicitly establish subsidiary ownership at the
    cutoff. Sponsor entities are boundaries, never consolidated borrowing groups.
    Unknown, inactive, future-dated and cyclic paths cannot establish attribution.
    """
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("Consolidated debt requires unique corporate-tree nodes")
    allowed = {"subsidiary", "indirect subsidiary"}

    def lineage(node_id: int) -> tuple[int, ...]:
        path: list[int] = []
        seen: set[int] = set()
        while node_id not in sponsor_ids:
            if node_id in seen:
                return ()
            node = by_id.get(node_id)
            if (node is None or not node.company_status
                    or node.company_status.upper() == "INAC"
                    or node.knowledge_date is None
                    or node.knowledge_date.date() > asof_date):
                break
            path.append(node_id)
            seen.add(node_id)
            if (node.parent_id is None or node.parent_id == node_id
                    or (node.relationship or "").casefold() not in allowed):
                break
            node_id = node.parent_id
        return tuple(path)

    borrowers_by_group: dict[int, list[tuple[int, tuple[int, ...]]]] = defaultdict(list)
    for borrower in sorted(borrower_ids):
        path = lineage(borrower)
        for index, ancestor in enumerate(path):
            borrowers_by_group[ancestor].append((borrower, tuple(reversed(path[:index + 1]))))

    links: list[ConsolidatedDebtPath] = []
    for seed in sorted(set(seeds), key=lambda item: (item.company_id, item.bloomberg_company_id)):
        path = lineage(seed.bloomberg_company_id)
        used = {borrower for borrower, _ in borrowers_by_group.get(seed.bloomberg_company_id, ())}
        for index, ancestor in enumerate(path[1:], start=1):
            for borrower, borrower_path in borrowers_by_group.get(ancestor, ()):
                if borrower in used:
                    continue
                used.add(borrower)
                links.append(ConsolidatedDebtPath(
                    seed.company_id, seed.bloomberg_company_id, ancestor, borrower,
                    path[:index + 1], borrower_path, asof_date,
                ))
    return tuple(links)


def attribute_consolidated_debt_rows(
    debt_features: pd.DataFrame,
    paths: tuple[ConsolidatedDebtPath, ...],
    company_names: Mapping[int, str],
) -> pd.DataFrame:
    """Attach reviewed/corroborated ownership paths to whole borrower source rows.

    The caller must establish group membership before calling this boundary adapter.
    Paths from the candidate search alone are not sufficient for publication.
    Amounts remain the full source amount, without ownership-percentage allocation.
    """
    if not paths or debt_features.empty:
        return pd.DataFrame()
    bindings = pd.DataFrame([{
        "portco_id": path.company_id,
        "debt_attribution_scope": "consolidated_group",
        "debt_group_bbg_company_id": str(path.group_id),
        "debt_group_name": company_names[path.group_id],
        "debt_borrower_bbg_company_id": str(path.borrower_id),
        "debt_borrower_name": company_names[path.borrower_id],
        "debt_relationship_path": json.dumps({
            "company_to_group": path.company_to_group,
            "group_to_borrower": path.group_to_borrower,
            "ownership_asof_date": path.ownership_asof_date.isoformat(),
            "factset_group_id": path.factset_group_id,
            "factset_group_match_method": path.factset_group_match_method,
        }, separators=(",", ":")),
        "_borrower_id": path.borrower_id,
        "_ownership_date": path.ownership_asof_date,
    } for path in paths])
    features = debt_features.rename(columns=str.lower).copy()
    features["_borrower_id"] = pd.to_numeric(features["id_bb_company"], errors="raise").astype("Int64")
    features["_ownership_date"] = pd.to_datetime(features["asof_date"], errors="raise").dt.date
    keys = ["_borrower_id", "_ownership_date"]
    result = bindings.merge(features, on=keys, how="inner").drop(columns=keys)
    result["debt_bridge_source_system"] = "consolidated_group"
    result["debt_bridge_source"] = "dated_bloomberg_ownership"
    result["contributing_bridge_sources"] = "consolidated_group->dated_bloomberg_ownership"
    return result
