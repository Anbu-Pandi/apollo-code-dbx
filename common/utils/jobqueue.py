from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY = "retry"
    COMPLETED = "completed"
    DEAD = "dead"


@dataclass(frozen=True)
class QueueJob:
    id: str
    key: str
    ref: str
    fingerprint: str
    version: str
    status: JobStatus
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    lease_owner: str = ""
    lease_until: float = 0
    last_error: str = ""
    available_at: float = 0

    @property
    def source_id(self) -> str:
        return self.key

    @property
    def source_path(self) -> str:
        return self.ref

    @property
    def source_hash(self) -> str:
        return self.fingerprint


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def idempotency_key(key: str, fingerprint: str, version: str) -> str:
    return f"{key}:{version}:{fingerprint}"


class JobQueue:
    def __init__(
        self,
        path: str | Path,
        *,
        table: str = "jobs",
    ) -> None:
        self.path = Path(path)
        self.table = str(table or "jobs")
        if not self.table.replace("_", "").isalnum():
            raise ValueError(f"invalid queue table name: {self.table!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        last: Exception | None = None
        for attempt in range(12):
            try:
                connection = sqlite3.connect(str(self.path), timeout=60.0, isolation_level=None)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout=60000")
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    pass
                return connection
            except sqlite3.OperationalError as exc:
                last = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(min(8.0, 0.4 * (attempt + 1)))
        raise last or sqlite3.OperationalError("database is locked")

    def _initialize(self) -> None:
        t = self.table
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '{{}}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS {t}_claim
                ON {t}(status, available_at, lease_until, created_at);
                """
            )
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({t})")
            }
            for name in ("claimed_at", "completed_at", "failed_at"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE {t} ADD COLUMN {name} REAL NOT NULL DEFAULT 0"
                    )

    @staticmethod
    def _job(row: sqlite3.Row | None) -> QueueJob | None:
        if row is None:
            return None
        keys = row.keys()
        return QueueJob(
            id=row["id"],
            key=row["source_id"],
            ref=row["source_path"],
            fingerprint=row["source_hash"],
            version=row["version"],
            status=JobStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            payload=json.loads(row["payload"]),
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            last_error=row["last_error"],
            available_at=float(row["available_at"]) if "available_at" in keys else 0.0,
        )

    def enqueue(
        self,
        key: str | None = None,
        ref: str | Path = "",
        fingerprint: str = "",
        version: str = "1",
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
        available_at: float | None = None,
        *,
        source_id: str | None = None,
        source_path: str | Path | None = None,
        source_hash: str | None = None,
    ) -> QueueJob:
        job_key = str(key if key is not None else source_id or "")
        if not job_key:
            raise ValueError("enqueue requires key= (or source_id=)")
        job_ref = source_path if source_path is not None else ref
        job_fp = source_hash if source_hash is not None else fingerprint
        jobs = self.enqueue_many([(
            job_key, job_ref, job_fp, version, payload, max_attempts, available_at,
        )])
        return jobs[0]

    def enqueue_many(self, items: list[tuple]) -> list[QueueJob]:
        if not items:
            return []
        out: list[QueueJob] = []
        chunk = 100
        for start in range(0, len(items), chunk):
            out.extend(self._enqueue_chunk(items[start:start + chunk]))
        return out

    def _enqueue_chunk(self, items: list[tuple]) -> list[QueueJob]:
        now = _now()
        t = self.table
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            keys: list[str] = []
            for item in items:
                if len(item) == 6:
                    job_key, job_ref, job_fp, version, payload, max_attempts = item
                    available_at = None
                else:
                    job_key, job_ref, job_fp, version, payload, max_attempts, available_at = item
                key = idempotency_key(str(job_key), str(job_fp), str(version))
                keys.append(key)
                body = json.dumps(payload or {}, sort_keys=True)
                avail = float(available_at) if available_at is not None else 0.0
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {t}
                    (id, idempotency_key, source_id, source_path, source_hash, version,
                     status, max_attempts, payload, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex, key, str(job_key), str(job_ref), str(job_fp),
                        str(version), JobStatus.QUEUED.value, max(1, max_attempts),
                        body, avail, now, now,
                    ),
                )
                connection.execute(
                    f"""
                    UPDATE {t}
                    SET source_path = ?, payload = ?, available_at = CASE
                        WHEN ? > 0 THEN ?
                        ELSE available_at
                    END, updated_at = ?
                    WHERE idempotency_key = ? AND status IN (?, ?)
                    """,
                    (
                        str(job_ref), body, avail, avail, now, key,
                        JobStatus.QUEUED.value, JobStatus.RETRY.value,
                    ),
                )
            rows = {
                row["idempotency_key"]: row
                for row in connection.execute(
                    f"SELECT * FROM {t} WHERE idempotency_key IN ({','.join('?' * len(keys))})",
                    keys,
                ).fetchall()
            }
            connection.commit()
        jobs: list[QueueJob] = []
        for key in keys:
            job = self._job(rows.get(key))
            if job is None:
                raise RuntimeError("queue enqueue failed")
            jobs.append(job)
        return jobs

    def bump_coalesced(
        self,
        *,
        key: str | None = None,
        fingerprint: str = "pending",
        version: str = "1",
        ref: str = "",
        merge_payload: dict[str, Any] | None = None,
        available_at: float | None = None,
        max_attempts: int = 3,
        source_id: str | None = None,
        source_hash: str | None = None,
        source_path: str | None = None,
    ) -> QueueJob:
        job_key = str(key if key is not None else source_id or "")
        if not job_key:
            raise ValueError("bump_coalesced requires key= (or source_id=)")
        job_fp = source_hash if source_hash is not None else fingerprint
        path = str(source_path if source_path is not None else (ref or job_key))
        now = _now()
        t = self.table
        merge = dict(merge_payload or {})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT * FROM {t}
                WHERE source_id = ? AND status IN (?, ?)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (job_key, JobStatus.QUEUED.value, JobStatus.RETRY.value),
            ).fetchone()
            if row is not None:
                current = json.loads(row["payload"] or "{}")
                body = json.dumps(merge_payloads(current, merge), sort_keys=True)
                if available_at is None:
                    connection.execute(
                        f"""
                        UPDATE {t}
                        SET payload = ?, source_path = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (body, path, now, row["id"]),
                    )
                else:
                    connection.execute(
                        f"""
                        UPDATE {t}
                        SET payload = ?, source_path = ?, available_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (body, path, float(available_at), now, row["id"]),
                    )
                job_id = row["id"]
            else:
                body = json.dumps(merge, sort_keys=True)
                avail = 0.0 if available_at is None else float(available_at)
                job_id = uuid.uuid4().hex
                ikey = f"{idempotency_key(job_key, str(job_fp), version)}:{job_id[:8]}"
                connection.execute(
                    f"""
                    INSERT INTO {t}
                    (id, idempotency_key, source_id, source_path, source_hash, version,
                     status, max_attempts, payload, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id, ikey, job_key, path, str(job_fp), version,
                        JobStatus.QUEUED.value, max(1, max_attempts), body, avail, now, now,
                    ),
                )
            out = connection.execute(
                f"SELECT * FROM {t} WHERE id = ?", (job_id,),
            ).fetchone()
            connection.commit()
        job = self._job(out)
        if job is None:
            raise RuntimeError("coalesce bump failed")
        return job

    def get(self, job_id: str) -> QueueJob | None:
        with self._connect() as connection:
            return self._job(connection.execute(
                f"SELECT * FROM {self.table} WHERE id = ?", (job_id,),
            ).fetchone())

    def claim(self, worker_id: str, lease_seconds: int = 300) -> QueueJob | None:
        now = _now()
        lease_until = now + max(1, lease_seconds)
        t = self.table
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                UPDATE {t}
                SET status = ?, lease_owner = '', lease_until = 0,
                    last_error = 'lease expired after final attempt',
                    failed_at = ?, updated_at = ?
                WHERE status = ? AND lease_until <= ? AND attempts >= max_attempts
                """,
                (JobStatus.DEAD.value, now, now, JobStatus.LEASED.value, now),
            )
            row = connection.execute(
                f"""
                SELECT * FROM {t}
                WHERE (
                    status IN (?, ?) AND available_at <= ?
                ) OR (
                    status = ? AND lease_until <= ?
                )
                ORDER BY created_at, id
                LIMIT 1
                """,
                (
                    JobStatus.QUEUED.value, JobStatus.RETRY.value, now,
                    JobStatus.LEASED.value, now,
                ),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                f"""
                UPDATE {t}
                SET status = ?, attempts = attempts + 1, lease_owner = ?,
                    lease_until = ?, claimed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (JobStatus.LEASED.value, worker_id, lease_until, now, now, row["id"]),
            )
            claimed = connection.execute(
                f"SELECT * FROM {t} WHERE id = ?", (row["id"],),
            ).fetchone()
            connection.commit()
        return self._job(claimed)

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int = 300) -> bool:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table}
                SET lease_until = ?, updated_at = ?
                WHERE id = ? AND status = ? AND lease_owner = ? AND lease_until > ?
                """,
                (
                    now + max(1, lease_seconds), now, job_id,
                    JobStatus.LEASED.value, worker_id, now,
                ),
            )
        return cursor.rowcount == 1

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any] | None = None) -> bool:
        return self._finish(job_id, worker_id, JobStatus.COMPLETED, result=result or {})

    def fail(
        self, job_id: str, worker_id: str, error: str, retry_delay: float = 0,
    ) -> JobStatus:
        now = _now()
        t = self.table
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT attempts, max_attempts FROM {t} "
                f"WHERE id = ? AND status = ? AND lease_owner = ?",
                (job_id, JobStatus.LEASED.value, worker_id),
            ).fetchone()
            if row is None:
                connection.commit()
                raise ValueError("job lease is not owned by worker")
            status = JobStatus.DEAD if row["attempts"] >= row["max_attempts"] else JobStatus.RETRY
            connection.execute(
                f"""
                UPDATE {t}
                SET status = ?, lease_owner = '', lease_until = 0,
                    available_at = ?, last_error = ?, failed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value, now + max(0, retry_delay), error[:2000],
                    now, now, job_id,
                ),
            )
            connection.commit()
        return status

    def _finish(
        self, job_id: str, worker_id: str, status: JobStatus,
        result: dict[str, Any],
    ) -> bool:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table}
                SET status = ?, result = ?, lease_owner = '', lease_until = 0,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND status = ? AND lease_owner = ?
                """,
                (
                    status.value, json.dumps(result, sort_keys=True), now, now, job_id,
                    JobStatus.LEASED.value, worker_id,
                ),
            )
        return cursor.rowcount == 1

    def claimable(self) -> int:
        # Mirror claim()'s WHERE clause so deferred jobs (available_at in the
        # future) are not reported as ready — otherwise a drain worker busy-polls
        # while a coalesced job waits out its flush window.
        now = _now()
        t = self.table
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS n FROM {t}
                WHERE (status IN (?, ?) AND available_at <= ?)
                   OR (status = ? AND lease_until <= ?)
                """,
                (
                    JobStatus.QUEUED.value, JobStatus.RETRY.value, now,
                    JobStatus.LEASED.value, now,
                ),
            ).fetchone()
        return int(row["n"] or 0)

    def requeue_dead(self) -> int:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE {self.table}
                SET status = ?, attempts = 0, lease_owner = '', lease_until = 0,
                    available_at = 0, last_error = '', result = '{{}}',
                    claimed_at = 0, completed_at = 0, failed_at = 0, updated_at = ?
                WHERE status = ?
                """,
                (JobStatus.QUEUED.value, now, JobStatus.DEAD.value),
            )
        return cursor.rowcount

    def stats(self) -> dict[str, int | float | None]:
        counts = {status.value: 0 for status in JobStatus}
        now = _now()
        t = self.table
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT status, COUNT(*) AS count FROM {t} GROUP BY status",
            ).fetchall()
            times = connection.execute(
                f"""
                SELECT MIN(CASE WHEN status IN (?, ?) THEN created_at END) AS oldest,
                       MAX(created_at) AS enqueued,
                       MAX(claimed_at) AS claimed,
                       MAX(completed_at) AS completed,
                       MAX(failed_at) AS failed,
                       SUM(CASE WHEN completed_at >= ? THEN 1 ELSE 0 END) AS completed_60s
                FROM {t}
                """,
                (JobStatus.QUEUED.value, JobStatus.RETRY.value, now - 60),
            ).fetchone()
        counts.update({row["status"]: row["count"] for row in rows})
        counts["total"] = sum(counts[status.value] for status in JobStatus)
        counts["active_depth"] = counts["queued"] + counts["retry"] + counts["leased"]
        counts["oldest_queued_age_seconds"] = max(0, now - times["oldest"]) if times["oldest"] else 0
        counts["last_enqueued_at"] = times["enqueued"] or None
        counts["last_claimed_at"] = times["claimed"] or None
        counts["last_completed_at"] = times["completed"] or None
        counts["last_failed_at"] = times["failed"] or None
        counts["completed_last_60s"] = times["completed_60s"] or 0
        return counts


def merge_payloads(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    out = dict(left or {})
    for key, value in (right or {}).items():
        cur = out.get(key)
        if isinstance(value, list):
            prev = cur if isinstance(cur, list) else []
            seen = {json.dumps(x, sort_keys=True, default=str) for x in prev}
            merged = list(prev)
            for item in value:
                token = json.dumps(item, sort_keys=True, default=str)
                if token in seen:
                    continue
                seen.add(token)
                merged.append(item)
            out[key] = merged
        elif isinstance(value, dict) and isinstance(cur, dict):
            out[key] = merge_payloads(cur, value)
        elif isinstance(value, bool) and isinstance(cur, bool):
            out[key] = cur or value
        else:
            out[key] = value
    return out
