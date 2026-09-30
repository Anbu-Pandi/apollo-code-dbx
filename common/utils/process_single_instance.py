"""PID-file helpers for single-instance long-running jobs (watchers, daemons)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


def argus_wiki_watch_signature(
    env: str,
    view: str,
    also_views: Sequence[str],
    runas: str | None,
) -> dict[str, Any]:
    normalized = {
        "env": env.strip(),
        "view": (view or "default").strip(),
        "also_views": tuple(sorted({v.strip() for v in also_views if v and v.strip()})),
        "runas": runas.strip() if runas else None,
    }
    return normalized


def watch_instance_file_stem(sig: Mapping[str, Any]) -> str:
    payload = json.dumps(sig, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def cmdline_looks_like_argus_wiki_watch(cmdline: Sequence[str] | None) -> bool:
    if not cmdline:
        return False
    flat = " ".join(cmdline).lower()
    if "argus_wiki.orchestrate.watch" in flat or "argus_wiki\\orchestrate\\watch" in flat:
        return True
    # setuptools console_script name
    return "argus-wiki-watch" in flat


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/PID", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_pid_record(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_pid_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _process_cmdline(pid: int) -> list[str] | None:
    try:
        import psutil
    except ImportError:
        logging.warning("psutil unavailable; cannot verify PID %s command line", pid)
        return None
    try:
        proc = psutil.Process(pid)
        return list(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _cleanup_pid_path(path: Path, my_pid: int) -> None:
    try:
        rec = _read_pid_record(path)
        if rec and int(rec.get("pid", -1)) == my_pid:
            path.unlink(missing_ok=True)
    except OSError as exc:
        logging.warning("could not remove pid file %s: %s", path, exc)


def ensure_single_argus_wiki_watch(
    sig: Mapping[str, Any],
    *,
    log_label: str,
    no_kill_stale: bool,
    register_atexit: bool = True,
) -> None:
    """Resolve duplicate watcher processes for the same signature.

    Default: if another live watcher holds this instance file, terminate it
    after its command line matches ``argus_wiki.orchestrate.watch``. Always writes a fresh
    pid record for this process.

    With ``no_kill_stale``: refuse to start if a verified live watcher is still
    bound to this instance slot (no broad process scan).
    """
    import atexit

    from common.utils.logutil import get_log_folder

    stem = watch_instance_file_stem(sig)
    base = Path(get_log_folder()) / log_label
    path = base / f"{stem}.pid"
    my_pid = os.getpid()

    prev = _read_pid_record(path)
    if prev and int(prev.get("pid", -1)) != my_pid:
        try:
            import psutil  # noqa: F401
        except ImportError:
            logging.error(
                "psutil is required for argus_wiki.orchestrate.watch single-instance when %s already exists",
                path,
            )
            sys.exit(1)

    if prev:
        old_pid = int(prev["pid"])
        if old_pid != my_pid:
            cmdline = _process_cmdline(old_pid)
            if cmdline is None:
                logging.info(
                    "argus wiki watch pid file %s referred to missing/inaccessible PID %s; replacing",
                    path,
                    old_pid,
                )
            elif not cmdline_looks_like_argus_wiki_watch(cmdline):
                logging.error(
                    "argus wiki watch pid file %s points to PID %s which is not argus_wiki.orchestrate.watch "
                    "(command line guard). Remove %s or fix the stale file.",
                    path,
                    old_pid,
                    path,
                )
                sys.exit(1)
            elif no_kill_stale:
                logging.error(
                    "another argus wiki watch process (PID %s) holds %s; exiting (--no-kill-stale)",
                    old_pid,
                    path,
                )
                sys.exit(1)
            else:
                logging.warning(
                    "stopping previous argus wiki watch PID=%s for instance %s",
                    old_pid,
                    stem,
                )
                _taskkill(old_pid)

    record = {"pid": my_pid, "signature": dict(sig)}
    _write_pid_record(path, record)
    logging.info("argus wiki watch instance file %s (pid=%s)", path, my_pid)

    if register_atexit:
        atexit.register(_cleanup_pid_path, path, my_pid)
