"""Click capture and trending stats for Argus Wiki.

Append-only JSONL log under <vault>/state/clicks.jsonl. Aggregator is
mtime-cached so reads are O(1) for ranking and trending lookups.

Display titles for source pages are stored on the click (or backfilled once
into ``click_titles.json``), so trending never opens Delta on page render.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argus_wiki.bootstrap import config, dimension_registry
from argus_wiki.search import reader_contract
from data_access.storage import atomic_file

_REFRESH_SEC_DEFAULT = 20.0
_TRENDING_WINDOW_DAYS_DEFAULT = 7


def _log_path() -> Path:
    return config.VAULT_ROOT / "state" / f"clicks.{socket.gethostname().lower()}.jsonl"


def _titles_path() -> Path:
    return config.VAULT_ROOT / "state" / f"click_titles.{socket.gethostname().lower()}.json"


def _log_paths() -> list[Path]:
    state = config.VAULT_ROOT / "state"
    legacy = state / "clicks.jsonl"
    paths = list(state.glob("clicks.*.jsonl"))
    return ([legacy] if legacy.exists() else []) + paths


def _title_paths() -> list[Path]:
    state = config.VAULT_ROOT / "state"
    legacy = state / "click_titles.json"
    paths = list(state.glob("click_titles.*.json"))
    return ([legacy] if legacy.exists() else []) + paths


def _analytics_block() -> dict:
    raw = dimension_registry.load_registry().get("analytics")
    if isinstance(raw, dict):
        clicks_block = raw.get("clicks")
        return clicks_block if isinstance(clicks_block, dict) else {}
    return {}


def _refresh_sec() -> float:
    val = _analytics_block().get("refresh_sec")
    try:
        return float(val) if val is not None else _REFRESH_SEC_DEFAULT
    except (TypeError, ValueError):
        return _REFRESH_SEC_DEFAULT


def _window_days_internal() -> int:
    val = _analytics_block().get("trending_window_days")
    try:
        return int(val) if val is not None else _TRENDING_WINDOW_DAYS_DEFAULT
    except (TypeError, ValueError):
        return _TRENDING_WINDOW_DAYS_DEFAULT


@dataclass
class _Stats:
    total: Counter = field(default_factory=Counter)
    last7d: Counter = field(default_factory=Counter)
    by_view_last7d: dict[str, Counter] = field(default_factory=dict)
    by_query: dict[str, Counter] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    mtime: float = 0.0
    loaded_at: float = 0.0


_STATS = _Stats()
_LOAD_LOCK = threading.Lock()
_APPEND_LOCK = threading.Lock()


def reset_for_tests() -> None:
    global _STATS
    _STATS = _Stats()


def _ensure_dir() -> None:
    _log_path().parent.mkdir(parents=True, exist_ok=True)


def _norm(rel: str) -> str:
    rel = (rel or "").strip().replace("\\", "/").strip("/")
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    return rel


def _is_source_rel(rel: str) -> bool:
    clean = _norm(rel)
    if not clean:
        return False
    folder = clean.split("/", 1)[0]
    if folder in set(reader_contract.source_page_dirs()):
        return True
    stem = Path(clean).stem
    return stem.startswith("src_") or stem.startswith("src::")


def _is_raw_title(title: str, rel: str) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    clean = _norm(rel)
    stem = Path(clean).stem
    if text in {clean, stem, f"{clean}.md"}:
        return True
    if text.startswith("src_") or text.startswith("src::"):
        return True
    leaf = Path(text.replace("\\", "/")).stem
    return leaf.startswith("src_") or leaf.startswith("src::")


def _display_title_from_row(row: dict | None) -> str:
    if not row:
        return ""
    from argus_wiki.common import source_display

    title = source_display.short_title(source_display.derived_title(row), 72)
    sender = source_display.short_sender(source_display.sender_of(row))
    if sender and sender != "Unknown" and title:
        return f"{sender} — {title}"
    if title:
        return title
    if sender and sender != "Unknown":
        return sender
    return ""


def _resolve_source_title(rel: str) -> str:
    clean = _norm(rel)
    if not clean:
        return ""
    try:
        # Reuse the reader's source-title helper (reads the source page's own
        # frontmatter now that ES serves search). Lazy import: reader imports
        # clicks, so a module-level import would be circular.
        from dash_reports.sub_servers.argus_wiki_server import reader

        return reader._source_display_title(clean) or ""
    except Exception:
        logging.debug("clicks source title resolve failed for %s", clean, exc_info=True)
    return ""


def _load_title_sidecar() -> dict[str, str]:
    titles = {}
    for path in _title_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            titles.update(
                {_norm(k): str(v).strip() for k, v in data.items() if str(v).strip()}
            )
    return titles


def _save_title_sidecar(titles: dict[str, str]) -> None:
    path = _titles_path()
    atomic_file.write_text(
        json.dumps(titles, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        str(path),
    )


def _remember_title(rel: str, title: str, *, persist: bool = False) -> None:
    clean = _norm(rel)
    text = (title or "").strip()
    if not clean or _is_raw_title(text, clean):
        return
    _STATS.titles[clean] = text
    if not persist:
        return
    sidecar = _load_title_sidecar()
    if sidecar.get(clean) == text:
        return
    sidecar[clean] = text
    try:
        _save_title_sidecar(sidecar)
    except OSError as exc:
        logging.warning("clicks title sidecar write failed: %s", exc)


def log_click(view: str, to: str, from_rel: str = "", query: str = "", title: str = "") -> None:
    to = _norm(to)
    if not to:
        return
    from_rel = _norm(from_rel)
    text = (title or "").strip()
    if _is_raw_title(text, to):
        text = ""
    _ensure_dir()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "view": view,
        "to": to,
        "from": from_rel,
        "q": query,
    }
    if text:
        payload["title"] = text
        _remember_title(to, text, persist=_is_source_rel(to))
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _APPEND_LOCK:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line)


def _backfill_source_titles(titles: dict[str, str], needed: set[str], *, limit: int = 64) -> dict[str, str]:
    missing = [rel for rel in sorted(needed) if _is_source_rel(rel) and _is_raw_title(titles.get(rel, ""), rel)]
    if not missing:
        return titles
    changed = False
    for rel in missing[:limit]:
        resolved = _resolve_source_title(rel)
        if resolved:
            titles[rel] = resolved
            changed = True
    if changed:
        try:
            _save_title_sidecar(titles)
        except OSError as exc:
            logging.warning("clicks title sidecar write failed: %s", exc)
    return titles


def _reload(now: float, mtime: float) -> None:
    total: Counter = Counter()
    last7d: Counter = Counter()
    by_view_last7d: dict[str, Counter] = {}
    by_query: dict[str, Counter] = {}
    titles = _load_title_sidecar()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_window_days_internal())
    for path in _log_paths():
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    to = _norm(ev.get("to") or "")
                    if not to:
                        continue
                    total[to] += 1
                    event_title = str(ev.get("title") or "").strip()
                    if event_title and not _is_raw_title(event_title, to):
                        titles[to] = event_title
                    ts = None
                    ts_str = ev.get("ts") or ""
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                        except ValueError:
                            ts = None
                    if ts and ts >= cutoff:
                        last7d[to] += 1
                        view = ev.get("view") or ""
                        if view:
                            by_view_last7d.setdefault(view, Counter())[to] += 1
                    q = (ev.get("q") or "").strip().lower()
                    if q:
                        by_query.setdefault(q, Counter())[to] += 1
        except OSError as exc:
            logging.warning("clicks log read failed for %s: %s", path, exc)
    titles = _backfill_source_titles(titles, set(last7d))
    _STATS.total = total
    _STATS.last7d = last7d
    _STATS.by_view_last7d = by_view_last7d
    _STATS.by_query = by_query
    _STATS.titles = titles
    _STATS.mtime = mtime
    _STATS.loaded_at = now


def _maybe_load() -> _Stats:
    now = time.time()
    if now - _STATS.loaded_at < _refresh_sec():
        return _STATS
    paths = _log_paths()
    if not paths:
        _STATS.loaded_at = now
        return _STATS
    try:
        mtime = hash(tuple((str(path), path.stat().st_mtime_ns) for path in paths))
    except OSError:
        return _STATS
    if mtime == _STATS.mtime:
        _STATS.loaded_at = now
        return _STATS
    with _LOAD_LOCK:
        if mtime != _STATS.mtime:
            _reload(now, mtime)
    return _STATS


def clicks_for(rel: str) -> tuple[int, int]:
    stats = _maybe_load()
    key = _norm(rel)
    return stats.total.get(key, 0), stats.last7d.get(key, 0)


def title_for(rel: str) -> str:
    stats = _maybe_load()
    return stats.titles.get(_norm(rel), "")


def trending(view: str | None = None, n: int = 8) -> list[tuple[str, int]]:
    stats = _maybe_load()
    counter = stats.by_view_last7d.get(view, Counter()) if view else stats.last7d
    return counter.most_common(n)


def query_picks(query: str, n: int = 5) -> list[tuple[str, int]]:
    stats = _maybe_load()
    counter = stats.by_query.get(query.strip().lower(), Counter())
    return counter.most_common(n)


def window_days() -> int:
    return _window_days_internal()
