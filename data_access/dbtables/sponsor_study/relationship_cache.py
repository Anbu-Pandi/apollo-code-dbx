"""Cross-refresh memoization of dated filing evidence, separate from run checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from io import StringIO
from functools import lru_cache
import json
import logging
import re
from pathlib import Path
from typing import Iterator

import pandas as pd

from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables.sponsor_study.refresh_runtime import fingerprint


# Bump when dated ownership, identity ambiguity, or boundary rules change.
RELATIONSHIP_RULE_VERSION = "dated_filing_ownership_v1"


@lru_cache(maxsize=1)
def _rule_sources() -> str:
    root = Path(__file__).parent
    return fingerprint(tuple((name, (root / name).read_text(encoding="utf-8")) for name in (
        "relationship_cache.py", "relationship_helper.py", "factset_filing_attribution.py",
        "group_debt_sources.py", "issuer_tree_adapter.py")))


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _frame_key(frame: pd.DataFrame) -> str:
    """Row ordering and pandas indexes are not evidence."""
    rows = frame.reindex(sorted(frame.columns), axis=1).to_json(
        orient="records", date_format="iso", default_handler=str)
    return fingerprint(sorted(json.dumps(row, sort_keys=True) for row in json.loads(rows)))


def evidence_key(frame: pd.DataFrame, *, factset: pd.DataFrame,
                 boundaries: frozenset[str], seed_identifiers: pd.DataFrame) -> str:
    from data_access.dbtables.sponsor_study.relationship_helper import NODE_COLUMNS

    observed = pd.Timestamp(frame.evidence_date.iloc[0]).date()
    requests = sorted(tuple(pair) for pair in json.loads(frame.requests.iloc[0]))
    return fingerprint((RELATIONSHIP_RULE_VERSION, _rule_sources(), observed, requests, boundaries,
        _frame_key(frame.reindex(columns=NODE_COLUMNS).dropna(subset=["id_bb_company"]).drop_duplicates()),
        _frame_key(factset), _frame_key(seed_identifiers)))


def evidence_key_group(frame: pd.DataFrame, *, factset: pd.DataFrame,
                       boundaries: frozenset[str], seed_identifiers: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([(pd.Timestamp(frame.evidence_date.iloc[0]).date(), evidence_key(
        frame, factset=factset, boundaries=boundaries, seed_identifiers=seed_identifiers))],
        columns=["evidence_date", "cache_key"])


@dataclass(frozen=True)
class ResolutionBatch:
    key: str
    observed: date
    rows: pd.DataFrame

    def __post_init__(self) -> None:
        from data_access.dbtables.sponsor_study.relationship_helper import RESOLUTION_SCHEMA

        if not re.fullmatch(r"[a-f0-9]{64}", self.key):
            raise ValueError("Invalid relationship evidence key")
        required = [field.split()[0] for field in RESOLUTION_SCHEMA.split(", ")]
        if list(self.rows.columns) != required or self.rows.empty:
            raise ValueError("A relationship cache batch requires complete resolution rows")
        if (not pd.to_datetime(self.rows.evidence_date).dt.date.eq(self.observed).all()
                or self.rows.duplicated(required[:3]).any()):
            raise ValueError("Relationship cache dates and pair keys must be unique and consistent")


@dataclass(frozen=True)
class RelationshipCache:
    table: str
    app: str
    run_id: str
    location: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){2}", self.table):
            raise ValueError("Relationship cache requires a qualified table")
        if not self.location.startswith("abfss://"):
            raise ValueError("Relationship cache requires configured Delta storage")

    def load(self, keys: tuple[str, ...]) -> dict[str, pd.DataFrame]:
        if not keys:
            return {}
        if any(not re.fullmatch(r"[a-f0-9]{64}", key) for key in keys):
            raise ValueError("Invalid relationship evidence key")
        execsql(f"CREATE TABLE IF NOT EXISTS {self.table} (cache_key STRING, evidence_date DATE, "
            "rule_version STRING, payload STRING, payload_hash STRING, row_count BIGINT, "
            f"created_run_id STRING, created_at TIMESTAMP) USING DELTA LOCATION {_literal(self.location)}", app=self.app)
        result = {}
        for offset in range(0, len(keys), 128):
            selected = ",".join(_literal(key) for key in keys[offset:offset + 128])
            rows = readdfsql(f"SELECT * FROM {self.table} WHERE cache_key IN ({selected})", app=self.app)
            for row in rows.itertuples(index=False):
                if row.cache_key in result or fingerprint(row.payload) != row.payload_hash:
                    raise ValueError("Duplicate or corrupt relationship cache batch")
                if row.rule_version != RELATIONSHIP_RULE_VERSION:
                    raise ValueError("Relationship cache rule version mismatch")
                frame = pd.read_json(StringIO(row.payload), orient="table")
                batch = ResolutionBatch(row.cache_key, pd.Timestamp(row.evidence_date).date(), frame)
                if len(batch.rows) != row.row_count:
                    raise ValueError("Incomplete relationship cache batch")
                result[row.cache_key] = batch.rows
        logging.info("Relationship cache run=%s requested=%s reused=%s missing=%s",
                     self.run_id, len(keys), len(result), len(keys) - len(result))
        return result

    def save(self, batches: tuple[ResolutionBatch, ...]) -> None:
        # Each dated batch is one atomic MERGE. A failure cannot expose a partial date.
        for batch in batches:
            payload = batch.rows.to_json(orient="table", date_format="iso", index=False)
            execsql(f"MERGE INTO {self.table} target USING (SELECT {_literal(batch.key)} cache_key, "
                f"DATE '{batch.observed.isoformat()}' evidence_date, {_literal(RELATIONSHIP_RULE_VERSION)} rule_version, "
                f"decode(unhex('{payload.encode('utf-8').hex()}'), 'UTF-8') payload, {_literal(fingerprint(payload))} payload_hash, "
                f"CAST({len(batch.rows)} AS BIGINT) row_count, {_literal(self.run_id)} created_run_id, "
                "current_timestamp() created_at) source ON target.cache_key = source.cache_key "
                "WHEN NOT MATCHED THEN INSERT *", app=self.app)
        logging.info("Relationship cache run=%s computed_batches=%s computed_pairs=%s",
                     self.run_id, len(batches), sum(len(batch.rows) for batch in batches))


_ACTIVE: ContextVar[RelationshipCache | None] = ContextVar("sponsor_relationship_cache", default=None)


def active_relationship_cache() -> RelationshipCache | None:
    return _ACTIVE.get()


@contextmanager
def relationship_cache(cache: RelationshipCache) -> Iterator[None]:
    token = _ACTIVE.set(cache)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
