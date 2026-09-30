"""Typed adapter over issuers_info corporate-tree helpers for sponsor-study gates."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from importlib import import_module
from typing import Callable, Mapping

import pandas as pd

AUTO_ADMIT_RELATIONSHIPS = frozenset({"Subsidiary", "Indirect Subsidiary"})
_AUTO_ADMIT_RELATIONSHIP_KEYS = frozenset(
    relationship.casefold() for relationship in AUTO_ADMIT_RELATIONSHIPS
)
INACTIVE_STATUS = "INAC"
NUMERIC_BBG_ID_COLUMNS = frozenset({
    "id_bb_company",
    "obligor_bbid",
    "id_bb_parent_co",
    "id_bb_ultimate_parent_co",
    "parent_obligor_id",
    "ultimate_obligor_id",
    "parent_id_bb_company",
    "ult_parent_id_bb_company",
    "seed_bbg_company_id",
    "capital_stack_bbg_company_id",
    "seed_obligor_bbid",
})
_EMPTY_TEXT = {"", "nan", "none", "na", "n/a", "<na>"}


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_bbg_company_id(value: object) -> str | None:
    """Normalize numeric Bloomberg company ids through BIGINT string casting."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.lower() in _EMPTY_TEXT:
        return None
    try:
        return str(int(Decimal(text)))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def normalize_bbg_id_series(series: pd.Series) -> pd.Series:
    return series.map(normalize_bbg_company_id)


def bbg_company_id_sql(column: str) -> str:
    base_column = column.rsplit(".", 1)[-1].strip("`[]\"")
    if base_column in NUMERIC_BBG_ID_COLUMNS:
        return f"CAST(CAST({column} AS BIGINT) AS STRING)"
    return f"CAST({column} AS STRING)"


@dataclass(frozen=True)
class CorporateTreeNode:
    node_id: int
    parent_id: int | None
    ultimate_parent_id: int | None
    relationship: str | None
    company_status: str | None
    acquired_by_parent: bool | None
    knowledge_date: pd.Timestamp | None


@dataclass(frozen=True)
class TreeAdmission:
    node_id: int
    seed_id: int
    path: tuple[int, ...]
    relationship: str | None
    company_status: str | None
    admitted: bool
    eligibility: str
    rejection_reason: str | None = None


def _capital_structure_module():
    return import_module("dash_reports.issuers_info.data.capital_structure")


def load_corporate_sup_tree(
    bbg_ids: list[int] | tuple[int, ...],
    *,
    app: str = "databricks",
) -> pd.DataFrame:
    del app  # issuers_info's working ES implementation has no connection argument.
    ids = [int(normalize_bbg_company_id(value) or 0) for value in bbg_ids]
    ids = [value for value in ids if value]
    if not ids:
        return pd.DataFrame()
    return _capital_structure_module().get_bbg_corp_sup_tree(ids)


def corporate_tree_nodes(tree_df: pd.DataFrame) -> tuple[CorporateTreeNode, ...]:
    """Parse the legacy issuer-info frame into strict nodes at the boundary."""
    if tree_df.empty:
        return ()
    id_column = "id_bb_company"
    parent_column = (
        "parent_id_bb_company"
        if "parent_id_bb_company" in tree_df
        else "id_bb_parent_co"
    )
    ultimate_column = (
        "ult_parent_id_bb_company"
        if "ult_parent_id_bb_company" in tree_df
        else "id_bb_ultimate_parent_co"
    )
    nodes: dict[int, CorporateTreeNode] = {}
    for _, row in tree_df.iterrows():
        node_value = normalize_bbg_company_id(row.get(id_column))
        if node_value is None:
            continue
        node_id = int(node_value)
        parent_value = normalize_bbg_company_id(row.get(parent_column))
        ultimate_value = normalize_bbg_company_id(row.get(ultimate_column))
        acquired = row.get("acquired_by_parent")
        acquired_flag = None if pd.isna(acquired) else bool(acquired)
        knowledge = pd.to_datetime(row.get("knowledge_date"), errors="coerce")
        node = CorporateTreeNode(
            node_id=node_id,
            parent_id=int(parent_value) if parent_value else None,
            ultimate_parent_id=int(ultimate_value) if ultimate_value else None,
            relationship=_optional_text(row.get("company_to_parent_relationship")),
            company_status=_optional_text(row.get("company_status")),
            acquired_by_parent=acquired_flag,
            knowledge_date=None if pd.isna(knowledge) else knowledge,
        )
        existing = nodes.get(node_id)
        if existing is not None and existing != node:
            raise ValueError(f"Conflicting Bloomberg corporate-tree rows for {node_id}")
        nodes[node_id] = node
    return tuple(nodes.values())


def _nontrivial_cycle_ids(nodes: tuple[CorporateTreeNode, ...]) -> set[int]:
    """Return nodes in directed cycles, ignoring legitimate self-parent roots."""
    children: dict[int, list[int]] = {}
    for node in nodes:
        children.setdefault(node.node_id, [])
        if node.parent_id is None or node.parent_id == node.node_id:
            continue
        children.setdefault(node.parent_id, []).append(node.node_id)
    index: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    cyclic: set[int] = set()
    counter = 0

    def strongconnect(node_id: int) -> None:
        nonlocal counter
        index[node_id] = lowlink[node_id] = counter
        counter += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for nxt in children.get(node_id, []):
            if nxt not in index:
                strongconnect(nxt)
                lowlink[node_id] = min(lowlink[node_id], lowlink[nxt])
            elif nxt in on_stack:
                lowlink[node_id] = min(lowlink[node_id], index[nxt])
        if lowlink[node_id] != index[node_id]:
            return
        component = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node_id:
                break
        if len(component) > 1:
            cyclic.update(component)

    for node_id in children:
        if node_id not in index:
            strongconnect(node_id)
    return cyclic


def admitted_corporate_paths(
    seed_bbg_id: int,
    tree_df: pd.DataFrame | None = None,
    *,
    app: str = "databricks",
    tree_loader: Callable[..., pd.DataFrame] | None = None,
) -> dict[int, list[int]]:
    """Map admitted nodes to their seed-to-node corporate path."""
    seed = int(seed_bbg_id)
    loader = tree_loader if tree_loader is not None else load_corporate_sup_tree
    if tree_df is None:
        tree_df = loader([seed], app=app)
    if tree_df.empty:
        return {}
    nodes = corporate_tree_nodes(tree_df)
    if not nodes:
        return {}
    cyclic = _nontrivial_cycle_ids(nodes)
    normalized = pd.DataFrame([
        {
            "id_bb_company": node.node_id,
            "parent_id_bb_company": node.parent_id,
            "ult_parent_id_bb_company": node.ultimate_parent_id,
        }
        for node in nodes
    ])
    materialized_ids = set(normalized["id_bb_company"])
    synthetic_roots = [
        {
            "id_bb_company": ultimate_parent_id,
            "parent_id_bb_company": ultimate_parent_id,
            "ult_parent_id_bb_company": ultimate_parent_id,
        }
        for ultimate_parent_id in {
            node.ultimate_parent_id for node in nodes
            if node.ultimate_parent_id is not None
        } - materialized_ids
    ]
    if synthetic_roots:
        normalized = pd.concat(
            [normalized, pd.DataFrame(synthetic_roots)],
            ignore_index=True,
        )
    levels = _capital_structure_module().construct_bbg_tree_levels(normalized)
    paths: dict[int, list[int]] = {}
    for node_id, (_, path) in levels.items():
        node = int(node_id)
        ordered = [int(value) for value in path]
        if seed not in ordered:
            continue
        seed_path = ordered[ordered.index(seed):]
        if cyclic.intersection(seed_path):
            continue
        paths[node] = seed_path
    return paths


def path_contains_node(
    node_id: int,
    seed_id: int,
    paths: Mapping[int, list[int]],
) -> bool:
    path = paths.get(int(node_id))
    return path is not None and int(seed_id) in path


def path_contains_obligor(
    obligor_bbg_id: object,
    seed_bbg_id: int,
    paths: Mapping[int, list[int]],
) -> bool:
    obligor = normalize_bbg_company_id(obligor_bbg_id)
    if obligor is None:
        return False
    seed = int(seed_bbg_id)
    if int(obligor) == seed:
        return True
    return path_contains_node(int(obligor), seed, paths)


def evaluate_node_admission(
    *,
    node_id: int,
    seed_id: int,
    paths: Mapping[int, list[int]],
    relationship: object = None,
    company_status: object = None,
    is_seed: bool = False,
) -> TreeAdmission:
    path = paths.get(int(node_id), [int(node_id)])
    rel = _optional_text(relationship)
    status = _optional_text(company_status)
    if not path_contains_node(int(node_id), int(seed_id), paths):
        return TreeAdmission(
            node_id=int(node_id),
            seed_id=int(seed_id),
            path=tuple(path),
            relationship=rel,
            company_status=status,
            admitted=False,
            eligibility="rejected",
            rejection_reason="seed_not_on_corporate_path",
        )
    if status is None or status.upper() == INACTIVE_STATUS:
        return TreeAdmission(
            node_id=int(node_id),
            seed_id=int(seed_id),
            path=tuple(path),
            relationship=rel,
            company_status=status,
            admitted=False,
            eligibility="needs_review",
            rejection_reason=(
                "unknown_company_status"
                if status is None
                else "inactive_company_status"
            ),
        )
    if is_seed or int(node_id) == int(seed_id):
        return TreeAdmission(
            node_id=int(node_id),
            seed_id=int(seed_id),
            path=tuple(path),
            relationship=rel,
            company_status=status,
            admitted=True,
            eligibility="admitted",
        )
    if rel is not None and rel.casefold() in _AUTO_ADMIT_RELATIONSHIP_KEYS:
        return TreeAdmission(
            node_id=int(node_id),
            seed_id=int(seed_id),
            path=tuple(path),
            relationship=rel,
            company_status=status,
            admitted=True,
            eligibility="admitted",
        )
    return TreeAdmission(
        node_id=int(node_id),
        seed_id=int(seed_id),
        path=tuple(path),
        relationship=rel,
        company_status=status,
        admitted=False,
        eligibility="needs_review",
        rejection_reason="relationship_not_auto_admitted",
    )


def evaluate_path_admission(
    *,
    node_id: int,
    seed_id: int,
    paths: Mapping[int, list[int]],
    nodes: Mapping[int, CorporateTreeNode],
    relationship: object = None,
    company_status: object = None,
    is_seed: bool = False,
) -> TreeAdmission:
    """Admit a node only when every seed-to-node ancestor is also admitted."""
    del is_seed
    target = int(node_id)
    seed = int(seed_id)
    path = paths.get(target, [target])
    for step in path:
        ancestor = nodes.get(int(step))
        if int(step) == target:
            step_relationship = relationship
            step_status = company_status
        elif ancestor is None and int(step) == seed:
            continue
        elif ancestor is None:
            step_relationship = None
            step_status = None
        else:
            step_relationship = ancestor.relationship
            step_status = ancestor.company_status
        step_admission = evaluate_node_admission(
            node_id=int(step),
            seed_id=seed,
            paths=paths,
            relationship=step_relationship,
            company_status=step_status,
            is_seed=int(step) == seed,
        )
        if step_admission.admitted:
            continue
        if int(step) == target:
            return step_admission
        return TreeAdmission(
            node_id=target,
            seed_id=seed,
            path=tuple(path),
            relationship=_optional_text(relationship),
            company_status=_optional_text(company_status),
            admitted=False,
            eligibility=step_admission.eligibility,
            rejection_reason=f"ancestor_{step_admission.rejection_reason}",
        )
    return evaluate_node_admission(
        node_id=target,
        seed_id=seed,
        paths=paths,
        relationship=relationship,
        company_status=company_status,
        is_seed=target == seed,
    )


def build_seed_path_index(
    stack: pd.DataFrame,
    *,
    tree_cache: dict[int, pd.DataFrame] | None = None,
    app: str = "databricks",
) -> dict[str, dict[int, list[int]]]:
    if stack.empty:
        return {}
    cache = tree_cache if tree_cache is not None else {}
    index: dict[str, dict[int, list[int]]] = {}
    seed_column = "seed_bbg_company_id" if "seed_bbg_company_id" in stack else "id_bb_company"
    for external_id, group in stack.groupby("external_company_id", sort=False):
        seed_value = normalize_bbg_company_id(group.iloc[0].get(seed_column))
        if seed_value is None:
            continue
        seed = int(seed_value)
        if seed not in cache:
            cache[seed] = load_corporate_sup_tree([seed])
        index[str(external_id)] = admitted_corporate_paths(seed, cache[seed])
    return index


def annotate_capital_stack_admission(
    stack: pd.DataFrame,
    *,
    tree_cache: dict[int, pd.DataFrame] | None = None,
    app: str = "databricks",
    max_descendants: int = 100,
) -> pd.DataFrame:
    """Add path and obligor admission evidence without mutating audit identifiers."""
    if stack.empty:
        return stack
    out = stack.copy()
    cache = tree_cache if tree_cache is not None else {}
    path_index = build_seed_path_index(out, tree_cache=cache, app=app)
    node_index = {
        int(seed): {
            item.node_id: item for item in corporate_tree_nodes(tree_df)
        }
        for seed, tree_df in cache.items()
    }
    audit_columns = {
        "path_depth": "Int64",
        "admitted_corporate_path": "string",
        "resolved_obligor_company_id": "string",
        "resolved_obligor_company_name": "string",
        "obligor_inside_stack": "boolean",
        "stack_eligibility": "string",
        "stack_rejection_reason": "string",
        "capital_stack_descendant_count": "Int64",
        "capital_stack_expansion_truncated": "boolean",
    }
    for column, dtype in audit_columns.items():
        if column not in out:
            out[column] = pd.Series(pd.NA, index=out.index, dtype=dtype)
        else:
            out[column] = out[column].astype(dtype)
    if "capital_stack_rank" in out:
        out["capital_stack_rank"] = pd.to_numeric(
            out["capital_stack_rank"], errors="coerce"
        ).astype("Int64")
    for index, row in out.iterrows():
        external_id = str(row.get("external_company_id"))
        seed_value = normalize_bbg_company_id(row.get("seed_bbg_company_id"))
        node_value = normalize_bbg_company_id(row.get("id_bb_company"))
        if seed_value is None or node_value is None:
            out.at[index, "stack_eligibility"] = "rejected"
            out.at[index, "stack_rejection_reason"] = "missing_bbg_company_id"
            continue
        seed = int(seed_value)
        node = int(node_value)
        paths = path_index.get(external_id, {})
        rank = pd.to_numeric(row.get("capital_stack_rank"), errors="coerce")
        is_seed = bool(rank == 0) if pd.notna(rank) else node == seed
        admission = evaluate_path_admission(
            node_id=node,
            seed_id=seed,
            paths=paths,
            nodes=node_index.get(seed, {}),
            relationship=row.get("company_to_parent_relationship"),
            company_status=row.get("company_status"),
            is_seed=is_seed,
        )
        path = paths.get(node) or list(admission.path)
        out.at[index, "path_depth"] = (
            max(len(path) - 1, 0) if path else pd.NA
        )
        out.at[index, "admitted_corporate_path"] = (
            ">".join(str(item) for item in path) if path else pd.NA
        )
        obligor = normalize_bbg_company_id(row.get("obligor_bbid"))
        out.at[index, "resolved_obligor_company_id"] = obligor
        out.at[index, "resolved_obligor_company_name"] = _optional_text(
            row.get("id_bb_global_obligor_name")
        )
        inside_stack = path_contains_obligor(obligor, seed, paths) if obligor else pd.NA
        out.at[index, "obligor_inside_stack"] = inside_stack
        out.at[index, "stack_eligibility"] = admission.eligibility
        out.at[index, "stack_rejection_reason"] = admission.rejection_reason
        if not admission.admitted:
            continue
        if obligor and not bool(inside_stack):
            out.at[index, "stack_rejection_reason"] = "obligor_outside_admitted_stack"
    for _, indices in out.groupby("external_company_id", sort=False).groups.items():
        group = out.loc[indices]
        admitted = group["stack_eligibility"].eq("admitted")
        descendants = admitted & pd.to_numeric(
            group["path_depth"], errors="coerce"
        ).fillna(0).gt(0)
        descendant_count = int(descendants.sum())
        out.loc[indices, "capital_stack_descendant_count"] = descendant_count
        truncated = descendant_count > max_descendants
        out.loc[indices, "capital_stack_expansion_truncated"] = truncated
        if truncated:
            rejected_indices = group.index[descendants]
            out.loc[rejected_indices, "stack_eligibility"] = "rejected"
            out.loc[rejected_indices, "stack_rejection_reason"] = (
                "capital_stack_descendant_limit_exceeded"
            )
    admitted_depth = out["stack_eligibility"].eq("admitted") & out["path_depth"].notna()
    out.loc[admitted_depth, "capital_stack_rank"] = out.loc[
        admitted_depth, "path_depth"
    ]
    logging.info(
        "Capital-stack admission annotated for %s rows across %s seeds",
        len(out),
        len(path_index),
    )
    return out
