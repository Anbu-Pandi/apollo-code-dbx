"""Server-side paging store for very large AG Grids (infinite row model).

Instead of shipping a huge df to the browser, we materialize it ONCE to a temp
Delta table in the AzureDB 'local' share, then serve ~100-row blocks on scroll —
sorting/filtering pushed down to DuckDB-over-Delta. No in-memory memoization; the
browser holds one block at a time. Temp tables are named grid_cache_<gid> and
garbage-collected automatically by an in-process sweeper (jobutil thread) — the
module owns its own upkeep, no external scheduled job needed.

Column names are sanitized to safe identifiers for the Delta table; the caller
carries the safe->display map (in a dcc.Store) so blocks come back with the
original headers.
"""

import atexit
import logging
import math
import os
import re
import threading
import time as _time
import uuid

import numpy as np
import pandas as pd

from common.utils.jobutil import JobExecutor
from common.utils.sqlutil import readdfsql

log = logging.getLogger(__name__)

GRID_APP = "local"
PREFIX = "grid_cache_"
ROWIDX = "_rowidx"
OWNER = "_owner"
TOKEN = "_token"

# Self-cleaning: the module owns its own upkeep — a long-running sweeper job on a
# jobutil thread executor GCs stale temp tables. The dash server is always on, so
# no external scheduled job is needed. Starts once, on first use.
SWEEP_INTERVAL_SEC = 3600     # check hourly
SWEEP_MAX_AGE_HOURS = 2       # drop temp tables older than this
_sweeper_started = False
_sweeper_lock = threading.Lock()
_sweeper_executor = None
_sweeper_stop = threading.Event()   # set at exit so the loop ends -> process can exit


def _ensure_sweeper():
    global _sweeper_started, _sweeper_executor
    if _sweeper_started:
        return
    with _sweeper_lock:
        if _sweeper_started:
            return
        _sweeper_executor = JobExecutor("thread", max_workers=1)
        _sweeper_executor.submitjob_async(_sweep_loop, params=[{}], job_name="grid_cache_sweeper")
        atexit.register(_sweeper_stop.set)   # don't block interpreter exit
        _sweeper_started = True
        log.info("grid_cache: sweeper started via jobutil (every %ds, drop > %dh)",
                 SWEEP_INTERVAL_SEC, SWEEP_MAX_AGE_HOURS)


def _sweep_loop(**_):
    # Event.wait returns False on timeout (do a sweep), True when stop is set
    # (exit promptly so pool-thread join doesn't hang the process).
    while not _sweeper_stop.wait(SWEEP_INTERVAL_SEC):
        try:
            cleanup(older_than_hours=SWEEP_MAX_AGE_HOURS)
        except Exception as exc:
            log.warning("grid_cache sweeper: %s", exc)

_NUM_OPS = {"equals": "=", "notEqual": "!=", "greaterThan": ">",
            "greaterThanOrEqual": ">=", "lessThan": "<", "lessThanOrEqual": "<="}


def _safe_ident(name) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", str(name)).strip("_")
    if not s or s[0].isdigit():
        s = "c_" + s
    return s.lower()


def _azuredb():
    from data_access.spark.azuredb import AzureDB
    return AzureDB(local_dir="local")


def _current_owner():
    from flask import has_request_context, session

    if not has_request_context():
        return "system"
    user_info = session.get("_okta_userinfo") or {}
    return (
        user_info.get("user")
        or session.get("user_id")
        or session.get("email")
        or "anonymous"
    )


def write_grid_cache(df, gid):
    """Materialize df to grid_cache_<gid>. Returns (table_name, safe->display map, token)."""
    tbl = PREFIX + _safe_ident(gid)
    used, ren, safe_to_display = set(), {}, {}
    for c in df.columns:
        base = _safe_ident(c)
        s, i = base, 1
        while s in used or s == ROWIDX:
            s, i = f"{base}_{i}", i + 1
        used.add(s); ren[c] = s; safe_to_display[s] = str(c)
    out = df.copy()
    out.columns = [ren[c] for c in df.columns]
    owner = _current_owner()
    token = uuid.uuid4().hex
    out.insert(0, TOKEN, token)
    out.insert(0, OWNER, owner)
    out.insert(0, ROWIDX, range(len(out)))
    _azuredb().write_table(out, tbl, overwrite=True)
    log.info("grid_cache: wrote %d rows to %s", len(out), tbl)
    _ensure_sweeper()   # self-cleaning; no external scheduler needed
    return tbl, safe_to_display, token


def _where(filter_model, cols) -> str:
    if not filter_model:
        return ""
    clauses = []
    for col, f in filter_model.items():
        c = _safe_ident(col)
        if c not in cols or not isinstance(f, dict):
            continue
        op, val = f.get("type"), f.get("filter")
        if f.get("filterType") == "number" and op in _NUM_OPS and val is not None:
            try:
                clauses.append(f'"{c}" {_NUM_OPS[op]} {float(val)}')
            except (TypeError, ValueError):
                pass
        else:
            v = str(val if val is not None else "").replace("'", "''")
            if op == "equals":
                clauses.append(f"\"{c}\" = '{v}'")
            elif op == "notContains":
                clauses.append(f"\"{c}\" NOT ILIKE '%{v}%'")
            elif op == "startsWith":
                clauses.append(f"\"{c}\" ILIKE '{v}%'")
            elif op == "endsWith":
                clauses.append(f"\"{c}\" ILIKE '%{v}'")
            elif v:  # contains / default
                clauses.append(f"\"{c}\" ILIKE '%{v}%'")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _order_by(sort_model, cols) -> str:
    parts = []
    for srt in (sort_model or []):
        c = _safe_ident(srt.get("colId", ""))
        if c in cols:
            parts.append(f'"{c}" {"DESC" if srt.get("sort") == "desc" else "ASC"}')
    return (" ORDER BY " + ", ".join(parts)) if parts else f' ORDER BY "{ROWIDX}"'


def _json_safe(records):
    for r in records:
        for k, v in r.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
            elif v is pd.NaT:
                r[k] = None
            elif isinstance(v, np.generic):
                r[k] = v.item()
            elif isinstance(v, pd.Timestamp):
                r[k] = v.isoformat(sep=" ")[:19]
    return records


def fetch_block(tbl, safe_to_display, token, start, end, sort_model=None, filter_model=None):
    """Return (rowData block with display column names, total filtered row count)."""
    if not re.fullmatch(rf"{PREFIX}[a-z0-9_]+", str(tbl)):
        raise ValueError("Invalid grid cache table")
    meta = readdfsql(
        f'SELECT "{OWNER}", "{TOKEN}" FROM {tbl} LIMIT 1',
        app=GRID_APP,
        retrycount=1,
    )
    if (
        meta is None
        or meta.empty
        or str(meta.iloc[0][OWNER]) != _current_owner()
        or str(meta.iloc[0][TOKEN]) != str(token)
    ):
        raise PermissionError("Grid cache ownership mismatch")
    cols = set(safe_to_display.keys())
    where, order = _where(filter_model, cols), _order_by(sort_model, cols)
    limit = max(0, int(end) - int(start))
    sql = f"SELECT * FROM {tbl}{where}{order} LIMIT {limit} OFFSET {int(start)}"
    df = readdfsql(sql, app=GRID_APP, retrycount=1)
    if df is None:
        return [], 0
    df = df.drop(columns=[ROWIDX, OWNER, TOKEN], errors="ignore")
    df = df.rename(columns=safe_to_display)
    cnt = readdfsql(f"SELECT COUNT(*) AS c FROM {tbl}{where}", app=GRID_APP, retrycount=1)
    total = int(cnt.iloc[0]["c"]) if cnt is not None and len(cnt) else len(df)
    try:
        os.utime(os.path.join(_azuredb().get_local_dir(), tbl), None)
    except OSError as exc:
        log.warning("grid_cache: failed to refresh access time for %s: %s", tbl, exc)
    return _json_safe(df.to_dict("records")), total


def cleanup(older_than_hours=2) -> int:
    """Drop grid_cache_* temp tables older than N hours. Returns count dropped."""
    import os
    import time as _time
    azdb = _azuredb()
    root = azdb.get_local_dir()
    dropped = 0
    for t in list(getattr(azdb, "tables", [])):
        if not t.startswith(PREFIX):
            continue
        try:
            age_h = (_time.time() - os.path.getmtime(os.path.join(root, t))) / 3600.0
            if age_h >= older_than_hours:
                azdb.delete(t, predicate="true")
                dropped += 1
                log.info("grid_cache: dropped stale temp table %s (%.1fh old)", t, age_h)
        except Exception as exc:
            log.warning("grid_cache: cleanup skip %s: %s", t, exc)
    return dropped


def main():
    """Optional MANUAL cleanup — routine cleanup is automatic via the in-process
    sweeper (see _ensure_sweeper), so no scheduled job is needed. This is just for
    ad-hoc/ops use:
        python -m dash_reports.report_common.utils.grid_cache --env prod --hours 2
    """
    import argparse
    from common.utils.logutil import setuplogger
    setuplogger()
    from apo_config.config import config
    ap = argparse.ArgumentParser(description="drop stale grid_cache_* temp tables")
    ap.add_argument("--env", default="prod")
    ap.add_argument("--hours", type=float, default=2.0)
    args = ap.parse_args()
    config.init(args.env)
    n = cleanup(older_than_hours=args.hours)
    log.info("grid_cache cleanup: dropped %d temp table(s) older than %sh", n, args.hours)


if __name__ == "__main__":
    main()
