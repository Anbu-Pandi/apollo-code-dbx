"""Per-host local read-replica of the ``vc_*`` serving tables.

Databricks external Delta = source of truth. This mirrors the argus_wiki
``ingest_dedup`` / ``reader_cache`` idiom: a small **local Delta table per serving
table** under the host's ``azuredb_local_dir``, rebuilt (atomic overwrite) at ingest
time and read by the dashboard for fast, Databricks-free renders. Each of the two
nodes builds its OWN copy on local disk — no shared-write contention, no lease.

Reads are full-table (the VC serving set is tiny); the dashboard filters in pandas
(``queries.py``). Write-path CRUD (source toggles) bypasses this cache and hits
Databricks directly so it always sees source-of-truth state.

Correctness never depends on the cache: ``read`` returns ``None`` when the replica is
absent/mid-rebuild, and ``read_table`` falls back to ``readdfsql(app='databricks')``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

CACHE_SUBDIR = "vc_cache"

# Serving tables the dashboard reads. (vc_meta_refresh_run is control-plane, not served.)
SERVING_TABLES = [
    "vc_deals", "vc_companies", "vc_investors",
    "vc_sources", "vc_universe", "vc_subsectors",
]

_lock = threading.RLock()


def _local_root() -> str:
    """Resolve the per-host local dir: env override → azuredb_local_dir → LOCALAPPDATA."""
    override = os.environ.get("VC_CACHE_DIR")
    if override:
        return override
    try:
        from data_access.spark.azuredb import AzureDB
        d = AzureDB.get_local_dir()
        if d:
            return str(d)
    except Exception:
        pass
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return os.path.join(base, "vc_cache_delta")


def _table_path(env: str, table: str) -> str:
    return str(Path(_local_root()) / CACHE_SUBDIR / str(env) / table)


def _is_delta(path: str) -> bool:
    try:
        from common.utils.dfutil import is_delta
        return bool(is_delta(path))
    except Exception:
        return False


def enabled() -> bool:
    """Cache is opt-out via VC_CACHE_DISABLED=1 (e.g. to force live Databricks reads)."""
    return str(os.environ.get("VC_CACHE_DISABLED", "")).lower() not in ("1", "true", "yes")


def read(table: str, env: str):
    """Full-table read from the local replica, or ``None`` if absent/unavailable."""
    if not enabled():
        return None
    path = _table_path(env, table)
    if not _is_delta(path):
        return None
    commits = list((Path(path) / "_delta_log").glob("*.json"))
    if not commits or time.time() - max(p.stat().st_mtime for p in commits) > 900:
        return None
    try:
        from deltalake import DeltaTable
        return DeltaTable(path).to_pyarrow_table().to_pandas()
    except Exception as exc:  # mid-rebuild / corrupt fragment → degrade to a miss
        logging.debug("vc local_cache read miss %s: %s", table, exc)
        return None


def build(env: str, tables: list | None = None) -> dict:
    """Materialize serving tables from Databricks into the local replica (overwrite).

    Delta overwrite commits atomically; existing readers retain their snapshot.
    Best-effort per table; a failed table is skipped (dashboard falls back to Databricks).
    """
    import pyarrow as pa
    from deltalake import write_deltalake
    from data_access.dbtables.vc.tables import read_table_databricks

    tables = tables or SERVING_TABLES
    built: dict[str, int] = {}
    for t in tables:
        try:
            df = read_table_databricks(t, env=env)
        except Exception as exc:
            logging.warning("vc local_cache build: read %s failed: %s", t, exc)
            continue
        if df is None:
            continue
        path = _table_path(env, t)
        try:
            tbl = pa.Table.from_pandas(df, preserve_index=False)
            # All-None columns infer a pyarrow `null` type that write_deltalake
            # rejects — cast them to string (all-null string is valid Delta).
            for i, field in enumerate(tbl.schema):
                if pa.types.is_null(field.type):
                    tbl = tbl.set_column(i, field.name, tbl.column(i).cast(pa.string()))
            with _lock:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                write_deltalake(path, tbl, mode="overwrite", schema_mode="overwrite")
            built[t] = int(len(df))
        except Exception as exc:
            logging.warning("vc local_cache build: write %s failed: %s", t, exc)
    logging.info("vc local_cache built env=%s: %s", env, built)
    return built


def refresh(env: str) -> dict:
    """Rebuild the local replica after an ingest run (best-effort; never raises)."""
    try:
        return build(env)
    except Exception:
        logging.exception("vc local_cache refresh failed (non-fatal)")
        return {}
