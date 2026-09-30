"""Durable manual-presence evidence, separate from identity-link approvals."""
from dataclasses import dataclass
from datetime import date
import re

import pandas as pd

from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables.sponsor_study.canonical import stable_id
from data_access.dbtables.sponsor_study.identity_review_store import literal, qualified

EVENT_TABLE = "fundamental_review_event"
QUEUE_TABLE = "fundamental_review_queue"
EXCEPTION_STATUSES = frozenset({"Aggregate access not allowed", "Private model excluded",
    "Core only / no aggregate model", "No safe name match", "Ambiguous name", "Persisted aggregate gap"})
EXPORT_COLUMNS = ["case_id", "sponsor_name", "fund_name", "company_name", "recovery_status",
                  "checkpoint_date", "workflow_status", "evidence_url"]
TYPES = {"source_row": "BIGINT", "manual_reviewed": "BOOLEAN", "identity_link_approved": "BOOLEAN",
         "review_date": "DATE", "checkpoint_date": "DATE", "recorded_at": "TIMESTAMP"}


@dataclass(frozen=True)
class ReviewAttribution:
    reviewer_name: str
    reviewed_by: str
    review_date: date
    recorded_by: str

    def __post_init__(self) -> None:
        if not self.reviewer_name.strip() or type(self.review_date) is not date:
            raise ValueError("Reviewer name and a calendar review date are required")
        if any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
               for email in (self.reviewed_by, self.recorded_by)):
            raise ValueError("Reviewer and recording requester must be email addresses")


@dataclass(frozen=True)
class FundamentalException:
    sponsor: str
    fund: str
    company: str
    source_row: int
    recovery_status: str
    evidence_url: str
    analyst_issuer_ids: str
    discovered_issuer_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.sponsor, self.fund, self.company)) or self.source_row < 1:
            raise ValueError("A fundamental exception requires a sponsor, fund, company, and worksheet row")
        if self.recovery_status not in EXCEPTION_STATUSES:
            raise ValueError("Only unresolved fundamental rows belong in this review ledger")


def review_events(exceptions: tuple[FundamentalException, ...], attribution: ReviewAttribution, *,
                  source_file: str, source_sha256: str, source_sheet: str,
                  checkpoint_date: date) -> pd.DataFrame:
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha256) or not source_file or not source_sheet:
        raise ValueError("Original worksheet provenance is required")
    if attribution.review_date > checkpoint_date:
        raise ValueError("Review date cannot be later than the checkpoint")
    rows = []
    for item in exceptions:
        case_id = stable_id("fundamental_case", source_sha256, source_sheet, str(item.source_row))
        row = {"case_id": case_id, "sponsor_name": item.sponsor, "fund_name": item.fund,
            "company_name": item.company, "source_system": "findox", "source_file": source_file,
            "source_sha256": source_sha256, "source_sheet": source_sheet, "source_row": item.source_row,
            "review_scope": "manual_findox_presence", "manual_reviewed": True,
            "reviewer_name": attribution.reviewer_name, "reviewed_by": attribution.reviewed_by,
            "review_date": attribution.review_date.isoformat(), "recorded_by": attribution.recorded_by,
            "attribution_basis": "Reviewer attribution supplied by recording requester",
            "recovery_status": item.recovery_status, "checkpoint_date": checkpoint_date.isoformat(),
            "workflow_status": "open", "identity_link_approved": False,
            "evidence_url": item.evidence_url, "analyst_issuer_ids": item.analyst_issuer_ids,
            "discovered_issuer_id": item.discovered_issuer_id,
            "review_note": "Analyst marked fundamentals found in FinDox. This records that manual finding; "
                           "it does not authorize an identity link, merge, or private-data access."}
        row["event_id"] = stable_id("fundamental_review", *(str(row[key]) for key in sorted(row)))
        rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty or result.case_id.duplicated().any():
        raise ValueError("Submit a nonempty batch with one record per worksheet row")
    return result


def append_reviews(events: pd.DataFrame, *, catalog: str, schema: str, app: str,
                   prefix: str = "pe_sponsor_") -> pd.DataFrame:
    table = qualified(catalog, schema, EVENT_TABLE, prefix)
    queue = qualified(catalog, schema, QUEUE_TABLE, prefix)
    columns = list(events.columns)
    ddl = ", ".join(f"{name} {TYPES.get(name, 'STRING')}" for name in [*columns, "recorded_at", "recorded_principal"])
    execsql(f"CREATE TABLE IF NOT EXISTS {table} ({ddl}) USING DELTA", app=app)
    selects = ["SELECT " + ", ".join(
        f"CAST({literal(str(row[name]).lower() if isinstance(row[name], bool) else str(row[name]))} "
        f"AS {TYPES.get(name, 'STRING')}) AS {name}" for name in columns)
        for _, row in events.iterrows()]
    execsql(f"""MERGE INTO {table} target USING (
        SELECT batch.*, CURRENT_TIMESTAMP() AS recorded_at, CURRENT_USER() AS recorded_principal
        FROM ({' UNION ALL '.join(selects)}) batch
    ) incoming ON target.event_id=incoming.event_id
    WHEN NOT MATCHED THEN INSERT *""", app=app)
    execsql(f"""CREATE OR REPLACE VIEW {queue} AS SELECT * FROM {table}
        QUALIFY ROW_NUMBER() OVER(PARTITION BY case_id ORDER BY recorded_at DESC,event_id DESC)=1""", app=app)
    ids = ",".join(literal(value) for value in events.event_id)
    stored = readdfsql(f"SELECT * FROM {table} WHERE event_id IN ({ids})", app=app)
    expected = events.sort_values("event_id").reset_index(drop=True).astype("string")
    actual = stored[columns].sort_values("event_id").reset_index(drop=True).astype("string")
    for name in ("review_date", "checkpoint_date"):
        actual[name] = pd.to_datetime(actual[name]).dt.strftime("%Y-%m-%d").astype("string")
    pd.testing.assert_frame_equal(expected, actual)
    if stored.recorded_at.isna().any():
        raise ValueError("Recorded reviews are missing their actual database entry timestamp")
    return stored


def read_review_queue(*, catalog: str, schema: str, app: str,
                      prefix: str = "pe_sponsor_") -> pd.DataFrame:
    table = qualified(catalog, schema, QUEUE_TABLE, prefix)
    exists = readdfsql(f"SELECT table_name FROM {catalog}.information_schema.tables "
        f"WHERE table_schema={literal(schema)} AND table_name={literal(table.rsplit('.',1)[1])}", app=app)
    if exists.empty:
        return pd.DataFrame(columns=EXPORT_COLUMNS)
    return readdfsql(f"SELECT * FROM {table} ORDER BY sponsor_name,company_name,fund_name", app=app)
