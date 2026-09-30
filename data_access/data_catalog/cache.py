import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from common.utils.jobutil import JobExecutor
from common.utils.sqlutil import readdfsql, sql_escape_literal
from data_access.coordination import DeltaLeaseStore
from data_access.spark.azuredb import AzureDB


log = logging.getLogger(__name__)

TABLE_PREFIX = "data_catalog_"
MANIFEST_TABLE = f"{TABLE_PREFIX}manifest"
SNAPSHOT_TABLES = (
    "assets",
    "columns",
    "table_lineage",
    "column_lineage",
    "usage",
    "access",
    "quality_profile",
    "quality_drift",
)
STALE_AFTER = timedelta(hours=24)
REFRESH_LEASE_SECONDS = 4 * 60 * 60
REFRESH_RETRY_SECONDS = 15 * 60
REFRESH_RESOURCE = "dash_reports:data_catalog_refresh"

_executor = JobExecutor("thread", max_workers=1)
_state_lock = threading.Lock()
_db_lock = threading.RLock()
_lease_store = None


def _table(name):
    if name not in SNAPSHOT_TABLES:
        raise ValueError(f"Unknown catalog snapshot table: {name}")
    return f"{TABLE_PREFIX}{name}"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coordination():
    global _lease_store
    if _lease_store is None:
        with _state_lock:
            if _lease_store is None:
                _lease_store = DeltaLeaseStore.local()
    return _lease_store


def _json_columns(frame):
    result = frame.dropna(axis=1, how="all").copy()
    if "column_count" in result:
        result["column_count"] = pd.to_numeric(result["column_count"], errors="coerce").fillna(0).astype("int64")
    for column in result.select_dtypes(include=["datetimetz"]).columns:
        result[column] = result[column].dt.tz_convert("UTC").dt.tz_localize(None)
    for column in result.select_dtypes(include=["object"]).columns:
        values = result[column].dropna()
        if values.map(lambda value: isinstance(value, (dict, list, tuple))).any():
            result[column] = result[column].map(
                lambda value: json.dumps(value, separators=(",", ":"), default=str)
                if isinstance(value, (dict, list, tuple))
                else value
            )
        elif len({type(value) for value in values}) > 1:
            result[column] = result[column].map(
                lambda value: None if pd.isna(value) else str(value)
            )
    return result


def get_manifest():
    with _db_lock:
        frame = readdfsql(
            f"SELECT * FROM {MANIFEST_TABLE} LIMIT 1",
            app="local",
            msg="Data Catalog snapshot manifest",
            retrycount=1,
        )
    return {} if frame is None or frame.empty else frame.iloc[0].to_dict()


def get_status():
    manifest = get_manifest()
    lease = _coordination().status(REFRESH_RESOURCE) or {}
    refreshing = (
        lease.get("status") == "leased"
        and float(lease.get("lease_until") or 0) > time.time()
    )
    error = lease.get("detail") if lease.get("status") == "failed" else ""
    generated_at = pd.to_datetime(manifest.get("generated_at"), errors="coerce")
    age_hours = None
    if pd.notna(generated_at):
        age_hours = max(0.0, (_utcnow() - generated_at.to_pydatetime()).total_seconds() / 3600)
    return {
        **manifest,
        "refreshing": refreshing,
        "last_error": error,
        "last_attempt": float(lease.get("updated_at") or 0),
        "age_hours": age_hours,
        "available": bool(manifest.get("snapshot_id")),
    }


def read_snapshot(name, where="", snapshot_id=None):
    snapshot_id = snapshot_id or get_manifest().get("snapshot_id")
    if not snapshot_id:
        return pd.DataFrame()
    predicate = f"snapshot_id = '{sql_escape_literal(snapshot_id)}'"
    if where:
        predicate += f" AND ({where})"
    with _db_lock:
        return readdfsql(
            f"SELECT * EXCLUDE (snapshot_id, snapshot_created_at) FROM {_table(name)} WHERE {predicate}",
            app="local",
            msg=f"Data Catalog cached {name}",
            retrycount=1,
        )


def _activate_snapshot(snapshot_id, generated_at, frames, metadata):
    manifest = pd.DataFrame(
        [
            {
                "snapshot_id": snapshot_id,
                "generated_at": generated_at,
                "asset_count": len(frames.get("assets", [])),
                "source_status": json.dumps(metadata.get("source_status", {}), separators=(",", ":")),
                "build_seconds": metadata.get("build_seconds", 0.0),
            }
        ]
    )
    AzureDB(local_dir="local").write_table(manifest, MANIFEST_TABLE, overwrite=True)


def _prune_snapshots():
    versions = readdfsql(
        f"""
        SELECT snapshot_id, MAX(snapshot_created_at) AS created_at
        FROM {_table("assets")}
        GROUP BY snapshot_id
        ORDER BY created_at DESC
        LIMIT 2
        """,
        app="local",
        msg="Data Catalog snapshot cleanup",
        retrycount=1,
    )
    keep = versions["snapshot_id"].astype(str).tolist() if versions is not None else []
    if not keep:
        return
    predicate = "snapshot_id NOT IN ({})".format(
        ", ".join(f"'{sql_escape_literal(snapshot_id)}'" for snapshot_id in keep)
    )
    db = AzureDB(local_dir="local")
    for name in SNAPSHOT_TABLES:
        if _table(name) in db.tables:
            db.delete(_table(name), predicate)


def write_snapshot(frames, metadata=None, lease_guard=None):
    with _db_lock:
        metadata = metadata or {}
        snapshot_id = uuid.uuid4().hex
        generated_at = _utcnow()
        db = AzureDB(local_dir="local")
        for name in SNAPSHOT_TABLES:
            frame = frames.get(name)
            if frame is None or frame.empty:
                continue
            output = _json_columns(frame)
            output.insert(0, "snapshot_created_at", generated_at)
            output.insert(0, "snapshot_id", snapshot_id)
            db.write_table(output, _table(name), overwrite=False)
        if lease_guard is not None and not lease_guard():
            raise RuntimeError("Data Catalog refresh lease lost before activation")
        _activate_snapshot(snapshot_id, generated_at, frames, metadata)
        _prune_snapshots()
    return snapshot_id


def _refresh_job(builder, reason, lease):
    started = datetime.now()
    store = _coordination()
    try:
        frames, metadata = builder()
        metadata["build_seconds"] = (datetime.now() - started).total_seconds()
        active_lease = lease

        def renew():
            nonlocal active_lease
            active_lease = store.renew(active_lease, REFRESH_LEASE_SECONDS)
            return active_lease is not None

        snapshot_id = write_snapshot(frames, metadata, lease_guard=renew)
        if not store.complete(active_lease, snapshot_id):
            raise RuntimeError("Data Catalog refresh lease lost after activation")
        log.info("Data Catalog snapshot %s activated from %s", snapshot_id, reason)
    except Exception as exc:
        store.fail(lease, str(exc))
        log.exception("Data Catalog snapshot refresh failed from %s", reason)


def request_refresh(builder, reason="user"):
    lease = _coordination().acquire(REFRESH_RESOURCE, REFRESH_LEASE_SECONDS)
    if lease is None:
        return False

    def run():
        _refresh_job(builder, reason, lease)

    _executor.submitjob_async(run, job_name=f"data_catalog_{reason}")
    return True


def ensure_snapshot(builder):
    status = get_status()
    cooling_down = (
        bool(status["last_error"])
        and time.time() - status["last_attempt"] < REFRESH_RETRY_SECONDS
    )
    if not status["refreshing"] and not cooling_down and (
        not status["available"]
        or status["age_hours"] is None
        or status["age_hours"] >= STALE_AFTER.total_seconds() / 3600
    ):
        status["refreshing"] = request_refresh(builder, reason="stale")
    return status
