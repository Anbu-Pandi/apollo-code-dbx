"""Local, read-only stage tracing; disabled in normal refreshes."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import json
import logging
import re
from pathlib import Path
from time import perf_counter
from typing import Iterator

import pandas as pd


class DiagnosticStop(Exception):
    """The requested inspection boundary was reached successfully."""


def validate_diagnostic_sql(sql: str) -> None:
    """Fail before the SQL reader executes a write or deprecated WSO reference."""
    code = re.sub(r"'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)
    if (not re.match(r"\s*(SELECT|WITH|EXPLAIN|DESCRIBE|SHOW)\b", code, re.I)
            or re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|MERGE|TRUNCATE|COPY|GRANT|REVOKE|CALL)\b", code, re.I)):
        raise ValueError("Sponsor diagnostic permits read-only SQL")
    if re.search(r"\b(?:FROM|JOIN)\s+[\w.`]*wso[\w.`]*", code, re.I):
        raise ValueError("Deprecated WSO sources are forbidden in sponsor diagnostics")


@dataclass(eq=False)
class SponsorTrace(logging.Handler):
    directory: Path
    through: str
    started: float = field(default_factory=perf_counter)
    queries: int = 0
    pending_queries: dict[int, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        logging.Handler.__init__(self)
        self.directory.mkdir(parents=True, exist_ok=False)

    def record(self, event: dict) -> None:
        with (self.directory / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"elapsed_seconds": perf_counter() - self.started,
                **event}, default=str) + "\n")

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.module == "sqlutil" and message.startswith("Executing sql"):
            validate_diagnostic_sql(message.split(":", 1)[1])
            self.queries += 1
            self.pending_queries[record.thread] = perf_counter()
            self.record({"kind": "query_start", "thread": record.thread, "sql": message})
        elif record.module == "sqlutil" and message.startswith("Completed sql"):
            started = self.pending_queries.pop(record.thread, None)
            self.record({"kind": "query_complete", "thread": record.thread,
                "seconds": None if started is None else perf_counter() - started})
        elif "cache" in message.lower() or message.startswith("DCS "):
            self.record({"kind": "relationship_log", "message": message})

    def boundary(self, stage: str, event: str,
                 frames: dict[str, pd.DataFrame | int | None]) -> None:
        self.record({"kind": "stage", "stage": stage, "event": event,
            "queries_started": self.queries,
            "rows": {name: len(value) if isinstance(value, pd.DataFrame) else value
                     for name, value in frames.items()}})
        for name, frame in frames.items():
            if isinstance(frame, pd.DataFrame) and "decision_status" in frame and not frame.empty:
                columns = [column for column in ("decision_status", "decision_method") if column in frame]
                self.record({"kind": "decision_summary", "stage": stage, "frame": name,
                    "counts": frame.groupby(columns, dropna=False).size().reset_index(name="rows").to_dict("records")})
        # Freeze the exact decision inputs for local serving replay; these snapshots
        # are evidence only and are never imported as new approvals.
        for name, frame in frames.items():
            decision_input = name in {'decisions', 'xref_external_company_name_decision'}
            if not isinstance(frame, pd.DataFrame) or (frame.empty and not decision_input):
                continue
            if ('decision' in name or 'review' in name) and not decision_input:
                continue
            if stage in {"identity", "filing_relationships"} or (
                    stage != "sources" and event == "complete"):
                path = self.directory / f"{stage}-{name}.parquet"
                frame.to_parquet(path, index=False)
                if decision_input:
                    self.record({'kind': 'decision_checkpoint', 'stage': stage, 'frame': name,
                        'path': path.name, 'rows': len(frame), 'sha256': sha256(path.read_bytes()).hexdigest(),
                        'purpose': 'local_serving_replay_only'})
        if event == "complete" and stage == self.through:
            raise DiagnosticStop(stage)


_TRACE: ContextVar[SponsorTrace | None] = ContextVar("sponsor_diagnostic_trace", default=None)


def diagnostic_active() -> bool:
    return _TRACE.get() is not None


def diagnostic_boundary(stage: str, event: str, **frames: pd.DataFrame | int | None) -> None:
    trace = _TRACE.get()
    if trace is not None:
        trace.boundary(stage, event, frames)


@contextmanager
def trace_call(stage: str) -> Iterator[None]:
    trace = _TRACE.get()
    started = perf_counter()
    if trace is not None:
        trace.record({"kind": "call_start", "stage": stage})
    try:
        yield
    finally:
        if trace is not None:
            trace.record({"kind": "call_end", "stage": stage,
                          "seconds": perf_counter() - started})


@contextmanager
def sponsor_trace(trace: SponsorTrace) -> Iterator[None]:
    token = _TRACE.set(trace)
    logger = logging.getLogger()
    logger.addHandler(trace)
    try:
        yield
    finally:
        logger.removeHandler(trace)
        _TRACE.reset(token)
        trace.close()
