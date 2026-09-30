"""Persist dated issuer evidence and resolve filing paths on Spark workers."""

from __future__ import annotations

from datetime import date
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
from functools import partial
from typing import TYPE_CHECKING

import pandas as pd

from data_access.dbtables.sponsor_study.refresh_runtime import RefreshRuntime, fingerprint
from data_access.dbtables.sponsor_study.relationship_cache import (
    ResolutionBatch, active_relationship_cache, evidence_key_group,
)

if TYPE_CHECKING:
    from data_access.dbtables.sponsor_study.factset_filing_attribution import FilingClaim, _FactSetIdentifierSnapshot


NODE_COLUMNS = (
    "id_bb_company", "legal_entity_identifier", "long_comp_name", "id_bb_parent_co",
    "id_bb_ultimate_parent_co", "company_to_parent_relationship", "company_status",
    "acquired_by_parent", "knowledge_date", "load_date",
)
RESOLUTION_SCHEMA = ("company_entity_id string, filer_entity_id string, evidence_date date, "
    "relationship string, path string, filer_id long, filer_name string, reason string")


def pinned_issuer_source(runtime: RefreshRuntime, cutoff: str | pd.Timestamp) -> str:
    from pyspark.sql import Window, functions as F
    history = runtime.source_table(f"{runtime.catalog}.dbo.bond_issuer_master")
    window = Window.partitionBy("id_bb_company").orderBy(
        F.col("knowledge_date").desc(), F.col("load_date").desc())
    dated = history.where(F.col("knowledge_date") <= F.lit(pd.Timestamp(cutoff).date()))
    dated = dated.withColumn("_sponsor_row", F.row_number().over(window)).where("_sponsor_row = 1").drop("_sponsor_row")
    view = "sponsor_issuer_" + fingerprint((runtime.identity, cutoff))[:16]
    dated.createOrReplaceTempView(view)
    return view


def capital_stack_schema() -> str:
    from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import CAPITAL_STACK_COLUMNS
    integers = {"capital_stack_rank", "capital_stack_descendant_count", "path_depth"}
    booleans = {"legacy_seed_promoted", "capital_stack_expansion_truncated", "exact_match_flag", "obligor_inside_stack"}
    return ", ".join(f"{name} " + ("long" if name in integers else "boolean" if name in booleans
        else "double" if name == "match_score" else "string") for name in CAPITAL_STACK_COLUMNS)


def capital_stack_group(frame: pd.DataFrame, *, max_descendants: int) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.bloomberg_issuer_bridge import (
        BLOOMBERG_BRIDGE_METHOD, CAPITAL_STACK_EXPANSION_METHOD, CAPITAL_STACK_COLUMNS,
    )
    from data_access.dbtables.sponsor_study.issuer_tree_adapter import annotate_capital_stack_admission

    frame = frame.copy()
    frame["capital_stack_bbg_company_id"] = frame.id_bb_company
    frame["capital_stack_name"] = frame.long_comp_name
    frame["capital_stack_match_method"] = frame.capital_stack_rank.map(
        lambda rank: BLOOMBERG_BRIDGE_METHOD if rank == 0 else CAPITAL_STACK_EXPANSION_METHOD)
    frame["exact_match_flag"], frame["match_score"] = True, 1.0
    trees = {int(seed): group.copy() for seed, group in frame.groupby("seed_bbg_company_id")}
    result = annotate_capital_stack_admission(frame, tree_cache=trees, max_descendants=max_descendants)
    result = result.reindex(columns=CAPITAL_STACK_COLUMNS).drop_duplicates(
        ["external_company_id", "capital_stack_bbg_company_id"])
    for field in capital_stack_schema().split(", "):
        name, kind = field.split()
        if kind == "string":
            result[name] = result[name].astype("string")
        elif kind == "long":
            result[name] = pd.to_numeric(result[name], errors="raise").astype("Int64")
        elif kind == "boolean":
            result[name] = result[name].astype("boolean")
        else:
            result[name] = result[name].astype(float)
    return result


def resolve_date_group(frame: pd.DataFrame, *, factset: pd.DataFrame,
                       boundaries: frozenset[str], seed_identifiers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Worker boundary: parse requests, then reuse the existing pure ownership rule."""
    from data_access.dbtables.sponsor_study.factset_filing_attribution import FilingClaim, dated_filer_ownership
    from data_access.dbtables.sponsor_study.group_debt_sources import _own_identity_map
    from data_access.dbtables.sponsor_study.issuer_tree_adapter import corporate_tree_nodes

    observed = pd.Timestamp(frame.evidence_date.iloc[0]).date()
    pairs = json.loads(frame.requests.iloc[0])
    nodes = frame.dropna(subset=["id_bb_company"]).reindex(columns=NODE_COLUMNS).drop_duplicates()
    seed_identifiers = factset if seed_identifiers is None else seed_identifiers
    leis_by_entity = seed_identifiers.groupby("factset_entity_id").lei.agg(lambda x: tuple(x.dropna().unique())).to_dict()
    eligible = [(company, filer) for company, filer in pairs
                if len(leis_by_entity.get(company, ())) == len(leis_by_entity.get(filer, ())) == 1]
    leis = {leis_by_entity[entity][0] for pair in eligible for entity in pair}
    # Reproduce each date's original seed/ancestor scope before testing ambiguity.
    selected = set(nodes.loc[nodes.legal_entity_identifier.astype("string").str.strip().str.upper().isin(leis), "id_bb_company"].astype(int))
    parents = dict(zip(nodes.id_bb_company.astype(int), nodes.id_bb_parent_co))
    frontier = selected.copy()
    while frontier:
        frontier = {int(parents[node]) for node in frontier if node in parents and pd.notna(parents[node])} - selected
        selected.update(frontier)
    nodes = nodes[nodes.id_bb_company.isin(selected)]
    mapping = _own_identity_map(nodes, factset)
    reverse = {entity: key for key, entity in mapping.items()}
    tree = corporate_tree_nodes(nodes)
    boundary_ids = frozenset(key for key, entity in mapping.items() if entity in boundaries)
    names = dict(zip(nodes.id_bb_company.astype(int), nodes.long_comp_name))
    rows = []
    for company, filer in pairs:
        ownership = (dated_filer_ownership(FilingClaim("relationship", company, filer, observed), tree,
            company_id=reverse.get(company), filer_id=reverse.get(filer), boundary_ids=boundary_ids)
            if (company, filer) in eligible else None)
        mapped = (company, filer) in eligible and company in reverse and filer in reverse
        rows.append((company, filer, observed, ownership.relationship if ownership else None,
            json.dumps(ownership.path) if ownership else None, reverse.get(filer),
            str(names.get(reverse.get(filer), "")),
            "supported" if ownership else "no_ownership" if mapped else "missing_exact_dated_identifiers"))
    return pd.DataFrame(rows, columns=[part.split()[0] for part in RESOLUTION_SCHEMA.split(", ")])


def historical_nodes(runtime: RefreshRuntime, leis: tuple[str, ...], cutoff: date):
    """Capture relevant history once; retain old observations and dated parent changes."""
    from pyspark.sql import functions as F

    key = fingerprint((leis, cutoff))

    def capture():
        history = runtime.source_table(f"{runtime.catalog}.dbo.bond_issuer_master").select(*NODE_COLUMNS)
        history = history.where(F.col("knowledge_date") <= F.lit(cutoff))
        ids = history.where(F.upper(F.trim("legal_entity_identifier")).isin(list(leis))).select("id_bb_company").distinct()
        loaded = ids.limit(0)
        frames = []
        wave = 0
        while ids.limit(1).count():
            current_ids = ids
            current = runtime.materialize("relationship_history_wave", f"{key}:{wave}",
                lambda: history.join(current_ids, "id_bb_company", "left_semi"))
            frames.append(current)
            loaded = loaded.unionByName(ids).distinct().localCheckpoint(eager=True)
            ids = current.select(F.col("id_bb_parent_co").alias("id_bb_company")).dropna().distinct().join(
                loaded, "id_bb_company", "left_anti")
            wave += 1
        if not frames:
            return history.limit(0)
        result = frames[0]
        for frame in frames[1:]:
            result = result.unionByName(frame)
        return result.dropDuplicates()

    return runtime.materialize("entity_relationship_history", key, capture)


def resolve_filing_claims(runtime: RefreshRuntime, claims: tuple[FilingClaim, ...], identifiers: _FactSetIdentifierSnapshot,
                          cutoff: date, boundaries: frozenset[str]) -> pd.DataFrame:
    from pyspark.sql import Window, functions as F

    initial = identifiers.load_entities()
    leis = tuple(sorted(initial.lei.dropna().astype(str).unique()))
    history = historical_nodes(runtime, leis, cutoff)
    node_leis = tuple(row[0] for row in history.select(F.upper(F.trim("legal_entity_identifier"))).dropna().distinct().collect())
    factset = identifiers.load_leis(node_leis)
    pairs_by_date = {}
    for claim in claims:
        for observed in {claim.report_date, cutoff}:
            pairs_by_date.setdefault(observed, set()).add((claim.company_entity_id, claim.filer_entity_id))
    dates = sorted(pairs_by_date)
    outputs = []
    # Date batches are durable; Spark distributes issuer selection and date groups.
    for offset in range(0, len(dates), 16):
        selected = dates[offset:offset + 16]
        requests = [(day, json.dumps(sorted(pairs_by_date[day]))) for day in selected]
        key = fingerprint((requests, factset, boundaries))

        def dated_evidence():
            requested = runtime.spark.createDataFrame(requests, "evidence_date date, requests string")
            window = Window.partitionBy("evidence_date", "id_bb_company").orderBy(
                F.col("knowledge_date").desc(), F.col("load_date").desc())
            dated = history.join(requested, F.col("knowledge_date") <= F.col("evidence_date"))
            dated = dated.withColumn("rank", F.row_number().over(window)).where("rank = 1").drop("rank", "requests")
            return requested.join(dated, "evidence_date", "left")

        def resolve():
            dated = runtime.materialize("entity_relationship_dated_evidence", key, dated_evidence)
            cache = active_relationship_cache()
            cached = {}
            keys = pd.DataFrame()
            if cache is not None:
                keys = dated.groupBy("evidence_date").applyInPandas(
                    partial(evidence_key_group, factset=factset, boundaries=boundaries,
                            seed_identifiers=initial), "evidence_date date, cache_key string").toPandas()
                cached = cache.load(tuple(keys.cache_key))
                missing = keys.loc[~keys.cache_key.isin(cached), "evidence_date"].tolist()
                dated = dated.where(F.col("evidence_date").isin(missing))
            fresh = dated.groupBy("evidence_date").applyInPandas(
                partial(resolve_date_group, factset=factset, boundaries=boundaries,
                        seed_identifiers=initial), schema=RESOLUTION_SCHEMA)
            if cache is None:
                return fresh
            resolved = fresh.toPandas() if len(cached) < len(keys) else pd.DataFrame()
            batches = tuple(ResolutionBatch(row.cache_key, pd.Timestamp(row.evidence_date).date(),
                resolved[pd.to_datetime(resolved.evidence_date).dt.date.eq(pd.Timestamp(row.evidence_date).date())])
                for row in keys[~keys.cache_key.isin(cached)].itertuples(index=False))
            cache.save(batches)
            combined = pd.concat([*cached.values(), *(batch.rows for batch in batches)], ignore_index=True)
            records = combined.astype(object).where(combined.notna(), None).to_dict("records")
            for record in records:
                record["evidence_date"] = pd.Timestamp(record["evidence_date"]).date()
                if record["filer_id"] is not None:
                    record["filer_id"] = int(record["filer_id"])
            return runtime.spark.createDataFrame(records, RESOLUTION_SCHEMA)

        outputs.append(runtime.materialize("entity_relationship_resolution", key, resolve).toPandas())
    return pd.concat(outputs, ignore_index=True)


def local_historical_nodes(leis: tuple[str, ...], cutoff: date, *, app: str) -> pd.DataFrame:
    """Read ancestor history once at a pinned Delta version, not once per date."""
    from common.utils.sqlutil import readdfsql
    from data_access.dbtables.sponsor_study.group_debt_sources import _quoted

    catalog = str(readdfsql("SELECT current_catalog() AS catalog", app=app).iloc[0].catalog)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", catalog):
        raise ValueError("Invalid issuer-history catalog")
    table = f"{catalog}.dbo.bond_issuer_master"
    version = int(readdfsql(f"DESCRIBE HISTORY {table} LIMIT 1", app=app).iloc[0].version)
    source = f"{table} VERSION AS OF {version}"
    eligible = f"knowledge_date <= DATE '{cutoff.isoformat()}'"
    seed_queries = [f"SELECT DISTINCT id_bb_company FROM {source} WHERE {eligible} "
        f"AND UPPER(TRIM(legal_entity_identifier)) IN ({_quoted(leis[offset:offset + 256])})"
        for offset in range(0, len(leis), 256)]
    seen: set[int] = set()
    frames = []
    queries = len(seed_queries)
    # Independent identifier slices can overlap; parent waves remain dependent.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="sponsor_history") as pool:
        selected = list(pool.map(partial(readdfsql, app=app), seed_queries))
        pending = set(pd.concat(selected).id_bb_company.dropna().astype(int)) if selected else set()
        while pending:
            requested = tuple(sorted(pending - seen))
            seen.update(requested)  # Missing IDs and cycles must terminate too.
            pending = set()
            statements = []
            for offset in range(0, len(requested), 256):
                ids = ",".join(str(value) for value in requested[offset:offset + 256])
                statements.append(f"SELECT {','.join(NODE_COLUMNS)} FROM {source} WHERE {eligible} "
                    f"AND id_bb_company IN ({ids})")
            for frame in pool.map(partial(readdfsql, app=app), statements):
                frames.append(frame)
                pending.update(set(frame.id_bb_parent_co.dropna().astype(int)) - seen)
                queries += 1
    result = pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame(columns=NODE_COLUMNS)
    logging.info("DCS local history source=%s version=%s cutoff=%s queries=%s entities=%s rows=%s",
                 table, version, cutoff, queries, len(seen), len(result))
    return result


def local_dated_nodes(history: pd.DataFrame, observed: date) -> pd.DataFrame:
    """Match f_get_bond_issuer_master: knowledge date first, then load date."""
    dated = history[pd.to_datetime(history.knowledge_date).le(pd.Timestamp(observed))].copy()
    dated["knowledge_date"] = pd.to_datetime(dated.knowledge_date)
    dated["load_date"] = pd.to_datetime(dated.load_date)
    dated = dated.sort_values(["knowledge_date", "load_date"], ascending=False, na_position="last")
    winners = dated.drop_duplicates("id_bb_company")
    ties = dated.merge(winners[["id_bb_company", "knowledge_date", "load_date"]],
                       on=["id_bb_company", "knowledge_date", "load_date"])
    if ties.id_bb_company.duplicated().any():
        raise ValueError("Conflicting issuer history at identical knowledge/load dates")
    return winners.reset_index(drop=True)


def resolve_local_filing_claims(claims: tuple[FilingClaim, ...], identifiers: _FactSetIdentifierSnapshot,
                                cutoff: date, boundaries: frozenset[str], *, app: str) -> pd.DataFrame:
    initial = identifiers.load_entities()
    history = local_historical_nodes(tuple(sorted(initial.lei.dropna().unique())), cutoff, app=app)
    factset = identifiers.load_leis(tuple(sorted(history.legal_entity_identifier.dropna().unique())))
    dates = sorted({claim.report_date for claim in claims} | {cutoff})
    cache = active_relationship_cache()
    outputs = []
    for offset in range(0, len(dates), 16):
        prepared = []
        for observed in dates[offset:offset + 16]:
            pairs = sorted({(claim.company_entity_id, claim.filer_entity_id) for claim in claims
                            if observed in {claim.report_date, cutoff}})
            frame = local_dated_nodes(history, observed)
            if frame.empty:
                frame = pd.DataFrame([{name: None for name in NODE_COLUMNS}])
            frame["evidence_date"], frame["requests"] = observed, json.dumps(pairs)
            key = evidence_key_group(frame, factset=factset, boundaries=boundaries,
                                     seed_identifiers=initial).cache_key.iloc[0]
            prepared.append((observed, key, frame))
        cached = cache.load(tuple(key for _, key, _ in prepared)) if cache is not None else {}
        fresh = []
        for observed, key, frame in prepared:
            if key in cached:
                outputs.append(cached[key])
            else:
                rows = resolve_date_group(frame, factset=factset, boundaries=boundaries, seed_identifiers=initial)
                fresh.append(ResolutionBatch(key, observed, rows))
                outputs.append(rows)
        if cache is not None:
            cache.save(tuple(fresh))
    return pd.concat(outputs, ignore_index=True)
