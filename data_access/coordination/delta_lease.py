from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError


@dataclass(frozen=True)
class DeltaLease:
    resource: str
    owner_id: str
    fencing_token: int
    lease_until: float


class DeltaLeaseStore:
    def __init__(
        self,
        path: str,
        storage_options: dict[str, str] | None = None,
        owner_id: str | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = path
        self.storage_options = storage_options or {}
        self.owner_id = owner_id or f"{socket.gethostname().lower()}:{uuid.uuid4()}"
        self._clock = clock
        self._lock = threading.Lock()
        self._ensure_table()

    @classmethod
    def local(cls, table: str = "dash_reports_coordination") -> DeltaLeaseStore:
        from data_access.spark.azuredb import AzureDB

        database = AzureDB(local_dir="local")
        return cls(database.read_path(table), database.storage_options)

    def _ensure_table(self) -> None:
        schema = pa.schema([
            ("resource", pa.string()),
            ("owner_id", pa.string()),
            ("fencing_token", pa.int64()),
            ("lease_until", pa.float64()),
            ("completed_key", pa.string()),
            ("status", pa.string()),
            ("detail", pa.string()),
            ("updated_at", pa.float64()),
        ])
        seed = pa.Table.from_pylist([{
            "resource": "__schema__",
            "owner_id": "",
            "fencing_token": 0,
            "lease_until": 0.0,
            "completed_key": "",
            "status": "ready",
            "detail": "",
            "updated_at": self._clock(),
        }], schema=schema)
        write_deltalake(
            self.path,
            seed,
            mode="ignore",
            engine="rust",
            storage_options=self.storage_options,
        )

    def _table(self) -> DeltaTable:
        return DeltaTable(self.path, storage_options=self.storage_options)

    def _record(self, resource: str) -> dict | None:
        table = self._table().to_pyarrow_table(filters=[("resource", "=", resource)])
        return None if table.num_rows == 0 else table.to_pylist()[0]

    def acquire(
        self,
        resource: str,
        lease_seconds: float,
        completed_key: str = "",
    ) -> DeltaLease | None:
        if not resource or lease_seconds <= 0:
            raise ValueError("resource and positive lease_seconds are required")
        with self._lock:
            for _ in range(5):
                now = self._clock()
                source = pa.Table.from_pylist([{
                    "resource": resource,
                    "owner_id": self.owner_id,
                    "fencing_token": 1,
                    "lease_until": now + lease_seconds,
                    "completed_key": completed_key,
                    "status": "leased",
                    "detail": "",
                    "updated_at": now,
                    "now": now,
                }])
                try:
                    (
                        self._table()
                        .merge(
                            source,
                            "target.resource = source.resource",
                            source_alias="source",
                            target_alias="target",
                        )
                        .when_matched_update(
                            updates={
                                "owner_id": "source.owner_id",
                                "fencing_token": (
                                    "CASE WHEN target.owner_id = source.owner_id "
                                    "AND target.status = 'leased' "
                                    "AND target.lease_until > source.now "
                                    "THEN target.fencing_token ELSE target.fencing_token + 1 END"
                                ),
                                "lease_until": "source.lease_until",
                                "status": "'leased'",
                                "detail": "source.detail",
                                "updated_at": "source.updated_at",
                            },
                            predicate=(
                                "(target.owner_id = source.owner_id "
                                "OR target.lease_until <= source.now) "
                                "AND NOT (source.completed_key <> '' "
                                "AND target.status = 'completed' "
                                "AND target.completed_key = source.completed_key)"
                            ),
                        )
                        .when_not_matched_insert(updates={
                            "resource": "source.resource",
                            "owner_id": "source.owner_id",
                            "fencing_token": "source.fencing_token",
                            "lease_until": "source.lease_until",
                            "completed_key": "source.completed_key",
                            "status": "source.status",
                            "detail": "source.detail",
                            "updated_at": "source.updated_at",
                        })
                        .execute()
                    )
                except CommitFailedError:
                    time.sleep(0.02)
                    continue
                record = self._record(resource)
                if (
                    record
                    and record["owner_id"] == self.owner_id
                    and record["status"] == "leased"
                    and float(record["lease_until"]) > now
                ):
                    return DeltaLease(
                        resource=resource,
                        owner_id=self.owner_id,
                        fencing_token=int(record["fencing_token"]),
                        lease_until=float(record["lease_until"]),
                    )
                return None
            return None

    def renew(self, lease: DeltaLease, lease_seconds: float) -> DeltaLease | None:
        if lease.owner_id != self.owner_id or lease_seconds <= 0:
            return None
        with self._lock:
            now = self._clock()
            self._table().update(
                new_values={
                    "lease_until": now + lease_seconds,
                    "updated_at": now,
                },
                predicate=self._owner_predicate(lease, now),
            )
            record = self._record(lease.resource)
            if not self._owns(record, lease):
                return None
            return DeltaLease(
                resource=lease.resource,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
                lease_until=float(record["lease_until"]),
            )

    def release(self, lease: DeltaLease) -> bool:
        return self._finish(lease, "released")

    def complete(self, lease: DeltaLease, completed_key: str) -> bool:
        if not completed_key:
            raise ValueError("completed_key is required")
        return self._finish(lease, "completed", completed_key=completed_key)

    def fail(self, lease: DeltaLease, detail: str) -> bool:
        return self._finish(lease, "failed", detail=detail)

    def status(self, resource: str) -> dict | None:
        return self._record(resource)

    def _finish(
        self,
        lease: DeltaLease,
        status: str,
        completed_key: str | None = None,
        detail: str = "",
    ) -> bool:
        if lease.owner_id != self.owner_id:
            return False
        now = self._clock()
        values = {
            "lease_until": 0.0,
            "status": status,
            "detail": detail,
            "updated_at": now,
        }
        if completed_key is not None:
            values["completed_key"] = completed_key
        with self._lock:
            for _ in range(5):
                try:
                    result = self._table().update(
                        new_values=values,
                        predicate=self._owner_predicate(lease, now),
                    )
                    return bool(result.get("num_updated_rows"))
                except CommitFailedError:
                    time.sleep(0.02)
                    continue
        return False

    @staticmethod
    def _owns(record: dict | None, lease: DeltaLease) -> bool:
        return bool(
            record
            and record["owner_id"] == lease.owner_id
            and int(record["fencing_token"]) == lease.fencing_token
        )

    @staticmethod
    def _owner_predicate(lease: DeltaLease, now: float | None = None) -> str:
        owner = lease.owner_id.replace("'", "''")
        resource = lease.resource.replace("'", "''")
        predicate = (
            f"resource = '{resource}' AND owner_id = '{owner}' "
            f"AND fencing_token = {lease.fencing_token}"
        )
        return f"{predicate} AND lease_until > {now}" if now is not None else predicate
