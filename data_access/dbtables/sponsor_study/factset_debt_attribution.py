"""Validate proposed FactSet debt links against dated security and borrower evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import logging
from typing import Literal

import pandas as pd

from data_access.dbtables.sponsor_study.consolidated_group_debt import GROUP_DEBT_COLUMNS
from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.factset_filing_attribution import FilingClaim, dated_filer_ownership
from data_access.dbtables.sponsor_study.group_debt_sources import _dated_nodes, _factset_nodes, _own_identity_map
from data_access.dbtables.sponsor_study.issuer_tree_adapter import CorporateTreeNode, corporate_tree_nodes


DEBT_ATTRIBUTION_COLUMNS = [
    "asof_date", "debt_attribution_status", "debt_security_factset_id",
    "debt_security_entity_id", "debt_credit_parent_entity_id", "debt_security_is_physical",
    "debt_security_start_date", "debt_security_end_date", "debt_credit_start_date", "debt_credit_end_date",
    *GROUP_DEBT_COLUMNS,
]
SUPPORTED_DEBT_STATUSES = frozenset({
    "dated_security_issuer", "dated_credit_parent", "dated_own_borrower",
    "dated_owned_borrower", "dated_parent_borrower", "exact_wso_borrower",
    "corroborated_findox_wso_borrower",
    "corroborated_findox_direct_group",
    "corroborated_findox_bloomberg_group",
    "corroborated_audited_direct_group",
})


@dataclass(frozen=True)
class SecurityDebtClaim:
    company_id: str
    company_entity_id: str
    matched_entity_id: str
    security_entity_id: str | None
    credit_parent_entity_id: str | None
    physical_security: bool
    asof_date: date
    company_bloomberg_id: int | None
    borrower_bloomberg_id: int | None

    def __post_init__(self) -> None:
        FactSetDebtSeed(self.company_id, self.company_entity_id)
        FactSetDebtSeed(self.company_id, self.matched_entity_id)
        if not isinstance(self.asof_date, date):
            raise ValueError("Debt attribution requires an observation date")


@dataclass(frozen=True)
class DebtAttribution:
    status: Literal["dated_security_issuer", "dated_credit_parent", "dated_own_borrower",
                    "dated_owned_borrower", "dated_parent_borrower", "unsupported"]
    group_id: int | None = None
    path: tuple[int, ...] = ()


def classify_debt_attribution(
    claim: SecurityDebtClaim, nodes: tuple[CorporateTreeNode, ...], boundary_ids: frozenset[int],
) -> DebtAttribution:
    """A current corporate-tree match alone never authorizes debt attribution."""
    if claim.company_bloomberg_id in boundary_ids or claim.borrower_bloomberg_id in boundary_ids:
        return DebtAttribution("unsupported")
    if claim.physical_security and claim.matched_entity_id == claim.company_entity_id:
        if claim.security_entity_id == claim.company_entity_id:
            return DebtAttribution("dated_security_issuer")
        if claim.credit_parent_entity_id == claim.company_entity_id:
            return DebtAttribution("dated_credit_parent", claim.company_bloomberg_id)
    if claim.company_bloomberg_id is None or claim.borrower_bloomberg_id is None:
        return DebtAttribution("unsupported")
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("Debt attribution requires unique dated company observations")
    for identity in (claim.company_bloomberg_id, claim.borrower_bloomberg_id):
        node = by_id.get(identity)
        if (node is None or not node.company_status or node.company_status.upper() == "INAC"
                or node.knowledge_date is None or node.knowledge_date.date() > claim.asof_date):
            return DebtAttribution("unsupported")
    if claim.company_bloomberg_id == claim.borrower_bloomberg_id:
        return DebtAttribution("dated_own_borrower", path=(claim.company_bloomberg_id,))
    ownership = dated_filer_ownership(
        FilingClaim(claim.company_id, claim.company_entity_id, claim.matched_entity_id, claim.asof_date),
        nodes, company_id=claim.company_bloomberg_id, filer_id=claim.borrower_bloomberg_id,
        boundary_ids=boundary_ids,
    )
    if ownership is None:
        return DebtAttribution("unsupported")
    if ownership.relationship == "dated_parent_filer":
        return DebtAttribution("dated_parent_borrower", claim.borrower_bloomberg_id, ownership.path)
    return DebtAttribution("dated_owned_borrower", claim.company_bloomberg_id, ownership.path)


def admitted_factset_debt_rows(features: pd.DataFrame) -> pd.DataFrame:
    """Filter reviewed tree candidates while preserving other exact-source readers."""
    features = features.rename(columns=lambda column: str(column).lower())
    if features.empty or "debt_attribution_status" not in features:
        return features.copy()
    status = features.debt_attribution_status
    return features[status.isna() | status.isin(SUPPORTED_DEBT_STATUSES)].copy()


def annotate_factset_debt_attribution(
    candidates: pd.DataFrame, *, app: str, factset_catalog: str,
    table_name: str, sponsor_entity_ids: frozenset[str],
) -> pd.DataFrame:
    """Retain all proposed links and their dated evidence for the source audit."""
    if candidates.empty:
        return candidates.copy()
    out = candidates.copy().reset_index(drop=True)
    dates = pd.to_datetime(out.asof_date, errors="raise").dt.date
    if dates.isna().any() or dates.nunique() != 1:
        raise ValueError("FactSet debt attribution requires one source observation date")
    observed = dates.iloc[0]
    entities = tuple(sorted(set(out.portco_factset_entity_id.dropna().astype(str))))
    fs = _factset_nodes(entities, (), catalog=factset_catalog, app=app)
    leis = tuple(sorted(fs.lei.dropna().astype(str).unique()))
    prefix = table_name.rsplit(".", 2)[0] + "." if table_name.count(".") == 2 else ""
    source = f"{prefix}dbo.f_get_bond_issuer_master(DATE '{observed.isoformat()}')"
    borrower_ids = tuple(sorted(pd.to_numeric(out.id_bb_company.dropna(), errors="raise").astype(int).unique()))
    nodes = _dated_nodes(borrower_ids, leis, source=source, app=app)
    frames, seen = [nodes], set()
    while not nodes.empty:
        seen.update(nodes.id_bb_company.astype(int))
        parents = set(nodes.id_bb_parent_co.dropna().astype(int)) - seen
        if not parents:
            break
        seen.update(parents)
        nodes = _dated_nodes(tuple(sorted(parents)), (), source=source, app=app)
        frames.append(nodes)
    nodes = pd.concat(frames, ignore_index=True).drop_duplicates()
    if nodes.id_bb_company.duplicated().any():
        raise ValueError("Debt attribution source contains conflicting company rows")
    node_leis = tuple(sorted(nodes.legal_entity_identifier.dropna().astype(str).str.strip().str.upper().unique()))
    fs = _factset_nodes(entities, node_leis, catalog=factset_catalog, app=app)
    mapping = _own_identity_map(nodes, fs)
    reverse = {entity: bbg for bbg, entity in mapping.items()}
    boundaries = frozenset(bbg for bbg, entity in mapping.items() if entity in sponsor_entity_ids)
    tree = corporate_tree_nodes(nodes)
    names = nodes.set_index("id_bb_company").long_comp_name.to_dict()
    out["debt_attribution_status"] = "unsupported"
    proofs: dict[SecurityDebtClaim, DebtAttribution] = {}
    for column in GROUP_DEBT_COLUMNS:
        out[column] = pd.NA
    for row in out.itertuples():
        borrower = None if pd.isna(row.id_bb_company) else int(row.id_bb_company)
        claim = SecurityDebtClaim(
            str(row.portco_id), str(row.portco_factset_entity_id), str(row.debt_factset_entity_id),
            None if pd.isna(row.debt_security_entity_id) else str(row.debt_security_entity_id),
            None if pd.isna(row.debt_credit_parent_entity_id) else str(row.debt_credit_parent_entity_id),
            bool(row.debt_security_is_physical), observed, reverse.get(row.portco_factset_entity_id), borrower,
        )
        if claim not in proofs:
            proofs[claim] = (DebtAttribution("unsupported") if claim.company_entity_id in sponsor_entity_ids
                             else classify_debt_attribution(claim, tree, boundaries))
        proof = proofs[claim]
        out.at[row.Index, "debt_attribution_status"] = proof.status
        out.at[row.Index, "debt_relationship_path"] = json.dumps({
            "asof_date": observed.isoformat(), "factset_company": claim.company_entity_id,
            "security": row.debt_security_factset_id, "security_issuer": claim.security_entity_id,
            "credit_parent": claim.credit_parent_entity_id,
            "company_bloomberg_id": claim.company_bloomberg_id, "borrower_bloomberg_id": borrower,
            "ownership_path": proof.path, "status": proof.status,
        }, separators=(",", ":"))
        if proof.status == "unsupported":
            out.at[row.Index, "debt_bridge_relation"] = "unverified_current_tree"
            out.at[row.Index, "debt_bridge_confidence"] = "unverified"
        elif proof.status != "dated_security_issuer":
            # A financing relationship cannot become a company identity assertion.
            out.at[row.Index, "debt_bridge_relation"] = proof.status
        if proof.status in {"dated_credit_parent", "dated_owned_borrower", "dated_parent_borrower"}:
            out.at[row.Index, "debt_attribution_scope"] = "consolidated_group"
            out.at[row.Index, "debt_group_bbg_company_id"] = str(proof.group_id) if proof.group_id is not None else pd.NA
            out.at[row.Index, "debt_group_name"] = names.get(proof.group_id, claim.company_entity_id)
            out.at[row.Index, "debt_borrower_bbg_company_id"] = str(borrower) if borrower is not None else pd.NA
            out.at[row.Index, "debt_borrower_name"] = names.get(borrower, row.issuer_name)
    logging.info("FactSet debt attribution candidate rows: %s", out.debt_attribution_status.value_counts().to_dict())
    return out
