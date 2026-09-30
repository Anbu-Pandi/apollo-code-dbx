"""Read dated, exact-identifier evidence for consolidated group debt."""

from __future__ import annotations

from datetime import date
import logging
import re

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.consolidated_group_debt import (
    CompanyDebtSeed, FactSetGroupNode, attribute_consolidated_debt_rows,
    consolidated_debt_paths, corroborated_group_debt_paths,
)
from data_access.dbtables.sponsor_study.factset_dcs_debt_bridge import FactSetDebtSeed
from data_access.dbtables.sponsor_study.issuer_tree_adapter import corporate_tree_nodes


_NODE_COLUMNS = """id_bb_company, legal_entity_identifier, long_comp_name,
    id_bb_parent_co, id_bb_ultimate_parent_co, company_to_parent_relationship,
    company_status, acquired_by_parent, knowledge_date"""


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join("'" + value.replace("'", "''") + "'" for value in values) or "NULL"


@checkpointed
def _factset_nodes(
    entity_ids: tuple[str, ...], leis: tuple[str, ...], *, catalog: str, app: str,
) -> pd.DataFrame:
    return readdfsql(f"""SELECT DISTINCT c.factset_entity_id,
        UPPER(TRIM(i.entity_id_value)) AS lei,
        s.factset_parent_entity_id, s.factset_ultimate_parent_entity_id
        FROM {catalog}.ent_v1.ent_entity_coverage c
        LEFT JOIN {catalog}.ent_v1.ent_entity_identifiers i
          ON i.factset_entity_id=c.factset_entity_id AND i.entity_id_type='LEI'
        LEFT JOIN {catalog}.ent_v1.ent_entity_structure s
          ON s.factset_entity_id=c.factset_entity_id
        WHERE c.factset_entity_id IN ({_quoted(entity_ids)})
           OR UPPER(TRIM(i.entity_id_value)) IN ({_quoted(leis)})""", app=app)


def _dated_nodes(
    company_ids: tuple[int, ...], leis: tuple[str, ...], *, source: str, app: str,
) -> pd.DataFrame:
    ids = ",".join(str(value) for value in company_ids) or "NULL"
    return readdfsql(f"""SELECT {_NODE_COLUMNS} FROM {source}
        WHERE CAST(id_bb_company AS BIGINT) IN ({ids})
           OR UPPER(TRIM(legal_entity_identifier)) IN ({_quoted(leis)})""", app=app)


def _own_identity_map(nodes: pd.DataFrame, factset: pd.DataFrame) -> dict[int, str]:
    """Accept only one Bloomberg entity and one FactSet entity per legal identifier."""
    bb = nodes[["id_bb_company", "legal_entity_identifier"]].dropna().drop_duplicates()
    bb["lei"] = bb.legal_entity_identifier.astype(str).str.strip().str.upper()
    fs = factset[["factset_entity_id", "lei"]].dropna().drop_duplicates()
    bb = bb[bb.lei.str.fullmatch(r"[A-Z0-9]{20}")]
    fs = fs[fs.lei.str.fullmatch(r"[A-Z0-9]{20}")]
    matched = bb.merge(fs, on="lei")
    unique = (
        matched.groupby("lei").id_bb_company.transform("nunique").eq(1)
        & matched.groupby("lei").factset_entity_id.transform("nunique").eq(1)
        & matched.groupby("id_bb_company").factset_entity_id.transform("nunique").eq(1)
        & matched.groupby("factset_entity_id").id_bb_company.transform("nunique").eq(1)
    )
    return dict(zip(matched.loc[unique, "id_bb_company"].astype(int),
                    matched.loc[unique, "factset_entity_id"]))


@checkpointed
def fetch_consolidated_group_debt(
    seeds: tuple[FactSetDebtSeed, ...], *, sponsor_entity_ids: frozenset[str],
    app: str = "databricks", factset_catalog: str = "dbrx_factset_prod",
    table_name: str = "dbo.debt_features", asof_cutoff: str | pd.Timestamp,
) -> pd.DataFrame:
    """Find group borrowers without changing the upstream market universe.

    Ownership is checked at the selected debt observation date. Current FactSet
    structure provides independent corroboration, never the historical date.
    Source rows and full group amounts are retained with explicit group labels.
    sponsor_entity_ids includes both GP and fund entities: neither can be a group.
    """
    if not seeds:
        return pd.DataFrame()
    for identifier in (factset_catalog, table_name):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", identifier):
            raise ValueError("Invalid group-debt source identifier")
    cutoff = pd.Timestamp(asof_cutoff)
    if pd.isna(cutoff):
        raise ValueError("Group debt requires a valid source cutoff")
    borrowers = readdfsql(f"""WITH latest_date AS (
        SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name}
        WHERE CAST(asof_date AS DATE) <= DATE '{cutoff.date().isoformat()}'
    ) SELECT DISTINCT CAST(id_bb_company AS BIGINT) AS id_bb_company,
        CAST(asof_date AS DATE) AS asof_date FROM {table_name}
        WHERE CAST(asof_date AS DATE) = (SELECT asof_date FROM latest_date)
          AND id_bb_company IS NOT NULL AND instrument_id_uniq IS NOT NULL
          AND LOWER(instrument_type) IN ('loan','bond','convertible bond','asset-backed','municipal bonds')""", app=app)
    if borrowers.empty:
        return pd.DataFrame()
    observation_dates = pd.to_datetime(borrowers.asof_date, errors="raise").dt.date.unique()
    if len(observation_dates) != 1 or observation_dates[0] > cutoff.date():
        raise ValueError("Group-debt source must contain one eligible observation date")
    observation_date: date = observation_dates[0]
    # Follow the configured warehouse catalog while using the standard dated issuer master.
    prefix = table_name.rsplit(".", 2)[0] + "." if table_name.count(".") == 2 else ""
    source = f"{prefix}dbo.f_get_bond_issuer_master(DATE '{observation_date.isoformat()}')"
    # GP/fund IDs are boundary checks, not additional roots to scan and expand.
    entity_ids = tuple(sorted({seed.factset_entity_id for seed in seeds}))
    factset = _factset_nodes(entity_ids, (), catalog=factset_catalog, app=app)
    own_leis = tuple(sorted(factset.lei.dropna().unique()))
    if not own_leis:
        return pd.DataFrame()
    borrower_ids = frozenset(borrowers.id_bb_company.astype(int))
    nodes = _dated_nodes(tuple(sorted(borrower_ids)), own_leis, source=source, app=app)
    if nodes.empty:
        return pd.DataFrame()
    seen: set[int] = set()
    frames = [nodes]
    while not nodes.empty:
        seen.update(nodes.id_bb_company.astype(int))
        parents = set(nodes.id_bb_parent_co.dropna().astype(int)) - seen
        if not parents:
            break
        # Missing parent rows must terminate too; cycles are rejected by the pure resolver.
        seen.update(parents)
        nodes = _dated_nodes(tuple(sorted(parents)), (), source=source, app=app)
        frames.append(nodes)
    nodes = pd.concat(frames, ignore_index=True).drop_duplicates()
    tree = corporate_tree_nodes(nodes)
    # Duplicate source observations cannot silently provide competing legal identities.
    if nodes.id_bb_company.duplicated().any():
        raise ValueError("Group-debt issuer master contains conflicting company rows")
    leis = tuple(sorted(nodes.legal_entity_identifier.dropna().astype(str).str.strip().str.upper().unique()))
    factset = _factset_nodes(entity_ids, leis, catalog=factset_catalog, app=app)
    mapping = _own_identity_map(nodes, factset)
    reverse = {entity: bbg_id for bbg_id, entity in mapping.items()}
    company_seeds = tuple(CompanyDebtSeed(seed.portco_id, reverse[seed.factset_entity_id])
        for seed in seeds if seed.factset_entity_id in reverse)
    sponsor_ids = frozenset(bbg_id for bbg_id, entity in mapping.items() if entity in sponsor_entity_ids)
    paths = consolidated_debt_paths(company_seeds, tree, borrower_ids, sponsor_ids, asof_date=observation_date)
    if not paths:
        return pd.DataFrame()
    structure = factset[["factset_entity_id", "factset_parent_entity_id", "factset_ultimate_parent_entity_id"]].drop_duplicates()
    fs_nodes = tuple(FactSetGroupNode(str(entity), None if pd.isna(parent) else str(parent),
        None if pd.isna(ultimate) else str(ultimate)) for entity, parent, ultimate
        in structure.itertuples(index=False, name=None))
    admitted = []
    for seed_id in sorted({path.seed_id for path in paths}):
        same_seed = tuple(path for path in paths if path.seed_id == seed_id)
        admitted.extend(corroborated_group_debt_paths(same_seed, fs_nodes, mapping,
            {path.company_id: mapping[seed_id] for path in same_seed}, sponsor_entity_ids=sponsor_entity_ids))
    logging.info("Consolidated group debt: %s candidate paths; %s corroborated paths at %s",
        len(paths), len(admitted), observation_date)
    if not admitted:
        return pd.DataFrame()
    selected_ids = ",".join(str(value) for value in sorted({path.borrower_id for path in admitted}))
    features = readdfsql(f"""SELECT * FROM {table_name}
        WHERE CAST(asof_date AS DATE) = DATE '{observation_date.isoformat()}'
          AND CAST(id_bb_company AS BIGINT) IN ({selected_ids})
          AND instrument_id_uniq IS NOT NULL
          AND LOWER(instrument_type) IN ('loan','bond','convertible bond','asset-backed','municipal bonds')""", app=app)
    names = nodes.set_index("id_bb_company").long_comp_name.to_dict()
    return attribute_consolidated_debt_rows(features, tuple(admitted), names)
