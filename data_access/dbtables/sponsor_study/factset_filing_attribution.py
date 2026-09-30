"""Dated ownership gates for related-company FactSet capital-structure filings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import json
import logging
import re
from typing import Literal, Mapping

import pandas as pd

from data_access.dbtables.sponsor_study.consolidated_group_debt import GROUP_DEBT_COLUMNS
from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.group_debt_sources import _dated_nodes, _factset_nodes, _own_identity_map
from data_access.dbtables.sponsor_study.issuer_tree_adapter import CorporateTreeNode, corporate_tree_nodes
from data_access.dbtables.sponsor_study.refresh_runtime import active_runtime, checkpointed
from data_access.dbtables.sponsor_study.relationship_cache import active_relationship_cache


DCS_ATTRIBUTION_COLUMNS = [
    "dcs_entity_relationship", "dcs_attribution_status", "dcs_attribution_reason",
    "dcs_relationship_asof_date", "dcs_relationship_path", "dcs_selected_filing_flag",
]
ADMITTED_DCS_STATUSES = frozenset({"own_entity", "dated_parent_filer", "dated_subsidiary_filer"})


@dataclass(frozen=True)
class FilingClaim:
    company_id: str
    company_entity_id: str
    filer_entity_id: str
    report_date: date

    def __post_init__(self) -> None:
        FactSetDebtSeed(self.company_id, self.company_entity_id)
        FactSetDebtSeed(self.company_id, self.filer_entity_id)
        if not isinstance(self.report_date, date):
            raise ValueError("A filing claim requires its reporting date")


@dataclass(frozen=True)
class FilingOwnership:
    relationship: Literal["dated_parent_filer", "dated_subsidiary_filer"]
    path: tuple[int, ...]


class _FactSetIdentifierSnapshot:
    """Reuse the refresh-scoped FactSet identifier mapping during date expansion.

    The mapping query is independent of the historical Bloomberg observation
    date.  Only newly discovered LEIs need an incremental lookup while walking
    dated parent relationships.
    """

    def __init__(self, entity_ids: tuple[str, ...], *, catalog: str, app: str) -> None:
        self._entity_ids = entity_ids
        self._catalog = catalog
        self._app = app
        self._loaded_leis: set[str] = set()
        self._frame = pd.DataFrame()

    def load_entities(self) -> pd.DataFrame:
        if self._entity_ids and self._frame.empty:
            self._frame = _factset_nodes(
                self._entity_ids, (), catalog=self._catalog, app=self._app
            )
        return self._frame.copy()

    def load_leis(self, leis: tuple[str, ...]) -> pd.DataFrame:
        requested = tuple(sorted({str(value).strip().upper() for value in leis if value}))
        missing = tuple(
            value for value in requested
            if value not in self._loaded_leis
        )
        if missing:
            additional = _factset_nodes((), missing, catalog=self._catalog, app=self._app)
            self._frame = pd.concat([self._frame, additional], ignore_index=True).drop_duplicates()
            self._loaded_leis.update(missing)
        return self._frame.copy()


class _DatedIssuerMasterCache:
    """Cache only identifier-complete slices of each dated issuer snapshot."""

    def __init__(self, *, app: str) -> None:
        self._app = app
        self._frames: dict[date, pd.DataFrame] = {}
        self._loaded_ids: dict[date, set[int]] = {}
        self._loaded_leis: dict[date, set[str]] = {}

    def load(self, observed: date, company_ids: tuple[int, ...], leis: tuple[str, ...]) -> pd.DataFrame:
        requested_ids = {int(value) for value in company_ids}
        requested_leis = {str(value).strip().upper() for value in leis if value}
        frame = self._frames.get(observed, pd.DataFrame())
        cached_ids = (
            set(pd.to_numeric(frame.id_bb_company, errors="raise").astype(int))
            if not frame.empty else set()
        )
        missing_ids = requested_ids - self._loaded_ids.get(observed, set()) - cached_ids
        missing_leis = requested_leis - self._loaded_leis.get(observed, set())
        logging.info(
            "DCS issuer-master cache date=%s id_hits=%s id_misses=%s lei_hits=%s lei_misses=%s",
            observed, len(requested_ids) - len(missing_ids), len(missing_ids),
            len(requested_leis) - len(missing_leis), len(missing_leis),
        )
        if missing_ids or missing_leis:
            source = f"dbo.f_get_bond_issuer_master(DATE '{observed.isoformat()}')"
            fetched = _dated_nodes(
                tuple(sorted(missing_ids)), tuple(sorted(missing_leis)),
                source=source, app=self._app,
            )
            frame = pd.concat([frame, fetched], ignore_index=True).drop_duplicates()
            self._frames[observed] = frame
            self._loaded_ids.setdefault(observed, set()).update(missing_ids)
            self._loaded_leis.setdefault(observed, set()).update(missing_leis)
        if frame.empty:
            return frame.copy()
        normalized_leis = frame.legal_entity_identifier.astype("string").str.strip().str.upper()
        return frame[
            frame.id_bb_company.astype(int).isin(requested_ids)
            | normalized_leis.isin(requested_leis)
        ].copy()


def dated_filer_ownership(
    claim: FilingClaim, nodes: tuple[CorporateTreeNode, ...], *,
    company_id: int | None, filer_id: int | None, boundary_ids: frozenset[int],
) -> FilingOwnership | None:
    """Require an explicit ancestor path at the statement's reporting date."""
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("Filing ownership requires unique corporate-tree nodes")

    def lineage(identity: int | None) -> tuple[int, ...]:
        path: list[int] = []
        while identity is not None and identity not in boundary_ids:
            if identity in path:
                return ()
            node = by_id.get(identity)
            if (node is None or not node.company_status or node.company_status.upper() == "INAC"
                    or node.knowledge_date is None or node.knowledge_date.date() > claim.report_date):
                break
            path.append(identity)
            if (node.parent_id == identity
                    or (node.relationship or "").casefold() not in {"subsidiary", "indirect subsidiary"}):
                break
            identity = node.parent_id
        return tuple(path)

    company_path, filer_path = lineage(company_id), lineage(filer_id)
    if filer_id in company_path[1:]:
        return FilingOwnership("dated_parent_filer", company_path[:company_path.index(filer_id) + 1])
    if company_id in filer_path[1:]:
        return FilingOwnership("dated_subsidiary_filer", filer_path[:filer_path.index(company_id) + 1])
    return None


@checkpointed
def annotate_filing_attribution(
    detail: pd.DataFrame, company_entities: Mapping[str, str], *, app: str,
    factset_catalog: str, asof_cutoff: str | pd.Timestamp,
    sponsor_entity_ids: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Keep all source evidence, marking unsupported relationships for review.

    A current entity tree proposes candidates but cannot establish a historical
    financing perimeter. Own-company filings need no ownership-tree expansion.
    """
    if detail.empty:
        return detail.copy()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", factset_catalog):
        raise ValueError("Invalid FactSet catalog identifier")
    cutoff_stamp = pd.Timestamp(asof_cutoff)
    if pd.isna(cutoff_stamp):
        raise ValueError("Filing attribution requires a valid source cutoff")
    cutoff = cutoff_stamp.date()
    out = detail.copy()
    reports = pd.to_datetime(out["report_date"], errors="coerce")
    own_entity = out["portco_id"].map(company_entities)
    own = out["factset_entity_id"].eq(own_entity) & reports.notna() & reports.le(cutoff_stamp)
    out["dcs_attribution_status"] = "unverified_related_filer"
    out["dcs_attribution_reason"] = "missing_exact_dated_identifiers"
    out["dcs_relationship_asof_date"] = pd.NaT
    out["dcs_relationship_path"] = pd.NA
    for column in GROUP_DEBT_COLUMNS:
        out[column] = pd.NA
    out.loc[own, "dcs_attribution_status"] = "own_entity"
    out.loc[own, "dcs_attribution_reason"] = "same_factset_entity_id"
    claim_rows = out.assign(_company_entity=own_entity, _report_date=reports.dt.date).loc[
        ~own & own_entity.notna() & reports.notna()]
    claims = tuple(set(FilingClaim(str(owner), str(company), str(filer), report)
        for owner, filer, company, report in claim_rows[
            ["portco_id", "factset_entity_id", "_company_entity", "_report_date"]].itertuples(index=False, name=None)
        if report <= cutoff and filer not in sponsor_entity_ids))
    if not claims:
        return out
    entity_ids = tuple(sorted({entity for claim in claims for entity in (claim.company_entity_id, claim.filer_entity_id)}))
    identifiers = _FactSetIdentifierSnapshot(entity_ids, catalog=factset_catalog, app=app)
    fs = identifiers.load_entities()
    valid_lei = fs.lei.astype("string").str.fullmatch(r"[A-Z0-9]{20}", na=False)
    fs = fs[valid_lei]
    if fs.empty:
        return out
    fs = identifiers.load_leis(tuple(sorted(fs.lei.unique())))
    leis_by_entity = fs.groupby("factset_entity_id").lei.agg(lambda values: tuple(values.dropna().unique())).to_dict()
    proofs: dict[tuple[FilingClaim, date], FilingOwnership] = {}
    filer_names: dict[FilingClaim, str] = {}
    filer_ids: dict[FilingClaim, int] = {}
    runtime = active_runtime()
    from data_access.dbtables.sponsor_study.diagnostic_trace import diagnostic_active
    use_batched_local = runtime is None and (active_relationship_cache() is not None or diagnostic_active())
    logging.info("DCS filing attribution route=%s",
        "spark_refresh_runtime" if runtime is not None else "local_batched_history" if use_batched_local else "local_pandas")
    if runtime is not None or use_batched_local:
        from data_access.dbtables.sponsor_study.relationship_helper import resolve_filing_claims, resolve_local_filing_claims
        resolutions = (resolve_filing_claims(runtime, claims, identifiers, cutoff, sponsor_entity_ids)
            if runtime is not None else resolve_local_filing_claims(claims, identifiers, cutoff, sponsor_entity_ids, app=app))
        from data_access.dbtables.sponsor_study.diagnostic_trace import diagnostic_boundary
        diagnostic_boundary("filing_relationships", "complete", resolutions=resolutions)
        by_pair = {(row.company_entity_id, row.filer_entity_id, pd.Timestamp(row.evidence_date).date()): row
                   for row in resolutions.itertuples(index=False)}
        for claim in claims:
            mask = (out.portco_id.eq(claim.company_id) & out.factset_entity_id.eq(claim.filer_entity_id)
                    & reports.dt.date.eq(claim.report_date))
            for observed in sorted({claim.report_date, cutoff}):
                row = by_pair[claim.company_entity_id, claim.filer_entity_id, observed]
                if row.reason == "no_ownership":
                    out.loc[mask, "dcs_attribution_reason"] = (
                        "no_ownership_at_cutoff" if observed == cutoff else "no_ownership_at_report_date")
                if pd.notna(row.relationship):
                    proofs[claim, observed] = FilingOwnership(row.relationship, tuple(json.loads(row.path)))
                    if observed == cutoff:
                        filer_ids[claim] = int(row.filer_id)
                        filer_names[claim] = row.filer_name
    issuer_master = _DatedIssuerMasterCache(app=app)
    for evidence_date in ([] if runtime is not None or use_batched_local else sorted({claim.report_date for claim in claims} | {cutoff})):
        dated_claims = tuple(claim for claim in claims if (claim.report_date == evidence_date or evidence_date == cutoff)
            and len(leis_by_entity.get(claim.company_entity_id, ())) == 1
            and len(leis_by_entity.get(claim.filer_entity_id, ())) == 1)
        if not dated_claims:
            continue
        leis = tuple(sorted({leis_by_entity[entity][0] for claim in dated_claims
            for entity in (claim.company_entity_id, claim.filer_entity_id)}))
        nodes = issuer_master.load(evidence_date, (), leis)
        if nodes.empty:
            continue
        frames, seen = [nodes], set()
        while not nodes.empty:
            seen.update(nodes.id_bb_company.astype(int))
            parents = set(nodes.id_bb_parent_co.dropna().astype(int)) - seen
            if not parents:
                break
            seen.update(parents)
            nodes = issuer_master.load(evidence_date, tuple(sorted(parents)), ())
            frames.append(nodes)
        nodes = pd.concat(frames, ignore_index=True).drop_duplicates()
        if nodes.id_bb_company.duplicated().any():
            raise ValueError("Filing ownership source contains conflicting company rows")
        node_leis = tuple(sorted(nodes.legal_entity_identifier.dropna().astype(str).str.strip().str.upper().unique()))
        dated_fs = identifiers.load_leis(node_leis)
        from data_access.dbtables.sponsor_study.diagnostic_trace import diagnostic_boundary
        diagnostic_boundary(f"filing_tree_{evidence_date.isoformat()}", "complete",
            nodes=nodes, dated_identifiers=dated_fs)
        mapping = _own_identity_map(nodes, dated_fs)
        reverse = {entity: bbg_id for bbg_id, entity in mapping.items()}
        tree = corporate_tree_nodes(nodes)
        boundaries = frozenset(bbg_id for bbg_id, entity in mapping.items() if entity in sponsor_entity_ids)
        for claim in dated_claims:
            mask = (out.portco_id.eq(claim.company_id) & out.factset_entity_id.eq(claim.filer_entity_id)
                    & reports.dt.date.eq(claim.report_date))
            ownership = dated_filer_ownership(replace(claim, report_date=evidence_date), tree,
                company_id=reverse.get(claim.company_entity_id),
                filer_id=reverse.get(claim.filer_entity_id), boundary_ids=boundaries)
            if ownership is None:
                if claim.company_entity_id in reverse and claim.filer_entity_id in reverse:
                    out.loc[mask, "dcs_attribution_reason"] = (
                        "no_ownership_at_cutoff" if evidence_date == cutoff else "no_ownership_at_report_date")
                continue
            proofs[claim, evidence_date] = ownership
            if evidence_date == cutoff:
                filer_ids[claim] = reverse[claim.filer_entity_id]
                filer_names[claim] = str(nodes.set_index("id_bb_company").loc[filer_ids[claim], "long_comp_name"])
    for claim in claims:
        historic, current = proofs.get((claim, claim.report_date)), proofs.get((claim, cutoff))
        if historic is None or current is None or historic.relationship != current.relationship:
            continue
        mask = (out.portco_id.eq(claim.company_id) & out.factset_entity_id.eq(claim.filer_entity_id)
                & reports.dt.date.eq(claim.report_date))
        path = json.dumps({"report_date": claim.report_date.isoformat(), "report_path": historic.path,
            "cutoff_date": cutoff.isoformat(), "cutoff_path": current.path}, separators=(",", ":"))
        out.loc[mask, "dcs_attribution_status"] = historic.relationship
        out.loc[mask, "dcs_attribution_reason"] = "exact_lei_and_dated_bloomberg_ownership"
        out.loc[mask, "dcs_relationship_asof_date"] = pd.Timestamp(cutoff)
        out.loc[mask, "dcs_relationship_path"] = path
        if historic.relationship == "dated_parent_filer":
            out.loc[mask, "debt_attribution_scope"] = "consolidated_group"
            out.loc[mask, "debt_group_bbg_company_id"] = str(filer_ids[claim])
            out.loc[mask, "debt_group_name"] = filer_names[claim]
            out.loc[mask, "debt_relationship_path"] = path
    logging.info("DCS filing attribution: %s", out.groupby("dcs_attribution_status").size().to_dict())
    return out


def admitted_dcs_attribution_rows(detail: pd.DataFrame) -> pd.DataFrame:
    """Only the selected, supported filing may populate debt or security-price rows."""
    if detail.empty or "dcs_attribution_status" not in detail:
        return detail.copy()
    return detail[detail.dcs_attribution_status.isin(ADMITTED_DCS_STATUSES)
        & detail.dcs_selected_filing_flag.astype("boolean").fillna(False)].copy()


def filing_security_metadata(detail: pd.DataFrame) -> pd.DataFrame:
    """Carry filing-group labels through an exact security-price lookup."""
    rows = admitted_dcs_attribution_rows(detail)
    columns = ["portco_id", "sec_id", *GROUP_DEBT_COLUMNS]
    if rows.empty or "portco_id" not in rows:
        return pd.DataFrame(columns=columns)
    frames = []
    for identifier in ("factset_instrument_id", "isin", "bbg_id"):
        if identifier not in rows:
            continue
        frame = rows.reindex(columns=["portco_id", identifier, *GROUP_DEBT_COLUMNS]).rename(columns={identifier: "sec_id"})
        frame["sec_id"] = frame.sec_id.astype("string").str.strip()
        frames.append(frame[frame.sec_id.notna() & frame.sec_id.ne("")])
    return pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame(columns=columns)


def security_price_owners(xref: pd.DataFrame, detail: pd.DataFrame | None) -> pd.DataFrame:
    """A shared group security can be attributed to several distinct companies."""
    columns = ["portco_id", "sec_id", *GROUP_DEBT_COLUMNS]
    frames = []
    if detail is not None and not detail.empty:
        dcs = detail.copy()
        own_ids = xref[["factset_entity_id", "portco_id"]].dropna().drop_duplicates()
        if "portco_id" not in dcs:
            dcs = dcs.merge(own_ids, on="factset_entity_id", how="inner")
        frames.append(filing_security_metadata(dcs))
    for identifier in ("first_lien_debt_identifier", "second_lien_unsecured_debt_identifier", "loanx_id"):
        if identifier in xref:
            frames.append(xref[["portco_id", identifier]].rename(columns={identifier: "sec_id"}).reindex(columns=columns))
    if not frames:
        return pd.DataFrame(columns=columns)
    owners = pd.concat(frames, ignore_index=True)
    owners["sec_id"] = owners.sec_id.astype("string").str.strip()
    return owners[owners.portco_id.notna() & owners.sec_id.notna() & owners.sec_id.ne("")].drop_duplicates(
        ["portco_id", "sec_id"], keep="first")
