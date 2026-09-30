from __future__ import annotations

import base64
import html
import json
import logging
import mimetypes
import os
import posixpath
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import bleach
import markdown
import yaml
from bs4 import BeautifulSoup

from argus_wiki.bootstrap import config, dimension_registry
from argus_wiki.common import markdown_links, raw_paths
from argus_wiki.entities import canonicalize, entity_identifiers
from argus_wiki.render import theme as theme_mod
from argus_wiki.search import reader_contract
from argus_wiki.maintain.conflict_files import is_sharepoint_conflict_path
from argus_wiki.render.entity_collection_pages import (
    entity_filename_slug,
    relationship_entity_key,
    relationship_source_entities,
)
from dash_reports.sub_servers.argus_wiki_server import clicks
from dash_reports.sub_servers.argus_wiki_server.constants import SCAN_INTERVAL_SEC, URL_PATH
from dash_reports.report_common import theme as dr_theme

WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")
MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TOC_RE = re.compile(r"<h([2-4])\s+id=\"([^\"]+)\">(.*?)</h\1>", re.DOTALL)
WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]\|#]+)")
MD_LINK_TARGET_RE = re.compile(r"\]\(([^)]+)\)")
URL_RE = re.compile(r"<?(https?://[^\s<>]+)>?", re.IGNORECASE)
EMAIL_ALLOWED_TAGS = sorted(set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "br", "div", "span", "table", "thead", "tbody", "tr", "td", "th",
    "ul", "ol", "li", "strong", "em", "b", "i", "h1", "h2", "h3", "h4",
    "blockquote", "hr", "img",
})
EMAIL_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
EMAIL_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
PUBLIC_PAGE_DIR = "public"
FOLDER_INTRO_FILES = ("index.md", "readme.md", "_about.md")
HTML_PREVIEW_SUFFIXES = {".html", ".htm"}

def _site_name() -> str:
    return dimension_registry.site().get("name", "Argus Wiki")


def _known_dirs() -> set[str]:
    return reader_contract.page_directories()


def _default_view_name() -> str:
    return reader_contract.default_view_name()


def _default_fallback_page_dirs() -> set[str]:
    return reader_contract.default_fallback_page_dirs()


def _asset_suffixes() -> set[str]:
    return reader_contract.asset_suffixes()


def _raw_suffixes() -> set[str]:
    return reader_contract.raw_suffixes()


def _lazy_chart_snapshots_enabled() -> bool:
    server = dimension_registry.load_registry().get("server") or {}
    if not isinstance(server, dict):
        return False
    return bool(server.get("lazy_chart_snapshots"))


def _strip_leading_vault_wiki_prefix(rel_posix: str) -> str:
    return reader_contract.strip_leading_view_prefix(rel_posix)


_SQUASH_RE = re.compile(r"[^a-z0-9]+")


def squash(text: str) -> str:
    """Lowercase + strip every non-alphanumeric char so that 'USD/JPY', 'USD-JPY',
    'usdjpy', 'usd jpy' all collapse to the same canonical 'usdjpy'."""
    return _SQUASH_RE.sub("", text.lower())


@dataclass
class Note:
    rel: str
    route: str
    title: str
    folder: str
    mtime: float
    text: str
    frontmatter: dict
    tags: tuple[str, ...]
    text_lower: str = ""
    text_squashed: str = ""
    # Outbound wikilink/markdown-link target keys, so the index can build the
    # inbound backlink map without holding page bodies in memory.
    outbound: tuple[str, ...] = ()
    # False for metadata-only notes loaded from the Delta entity index (their
    # `text` is just a snippet); the serve path reads the full body on demand.
    body_loaded: bool = True


@dataclass
class ViewState:
    name: str
    wiki_root: Path
    is_default: bool
    ready_marker: str | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    notes_by_rel: dict[str, Note] = field(default_factory=dict)
    index_result: dict[str, Any] | None = None
    last_scan_at: float = 0.0
    # Freshness token of the entity Delta index this view was loaded from; the
    # reader reloads (not re-scans) when the watcher bumps it.
    entity_index_token: str = ""
    entity_freshness_checked_at: float = 0.0
    entity_freshness_check_running: bool = False
    bg_scan_running: bool = False
    bg_scan_scheduled: bool = False
    render_cache: dict[str, tuple[float, str, list[tuple[int, str, str]]]] = field(default_factory=dict)
    # Per-rel single-flight locks: concurrent requests for the SAME page coalesce
    # into one render instead of each running the CPU-bound markdown step and
    # starving the GIL (a cold today.md stampede went 0.08s -> 540s; client
    # timeouts don't cancel the server render, so abandoned renders pile up too).
    render_locks: dict[str, threading.Lock] = field(default_factory=dict)
    render_locks_guard: threading.Lock = field(default_factory=threading.Lock)
    # Page-level single-flight (keyed by rel). render_locks above only guards the
    # markdown step inside render_cached; the read + backlinks + layout work in
    # render_page runs unguarded, so N concurrent hits to today.md (many clients +
    # the watcher) each ran the 50-95s backlinks/layout in parallel, starving the
    # GIL. This coalesces the WHOLE page render per rel.
    page_render_locks: dict[str, threading.Lock] = field(default_factory=dict)
    # Rels with an in-flight background stale-while-revalidate render (dedup so a
    # burst of stale hits spawns ONE refresh, not N). Guarded by render_locks_guard.
    page_bg_refreshing: set[str] = field(default_factory=set)
    page_cache: dict[str, tuple[float, int, str]] = field(default_factory=dict)
    search_records: list[dict] | None = None
    search_records_sig: int = 0
    search_result_cache: dict[tuple[int, str, int, str, int], list[dict]] = field(default_factory=dict)
    # folder -> (computed_at, cache_sig, sorted [(rel, path)]) for entity-folder
    # listings. Prefer ES notes_by_rel; SMB glob only on cold index.
    folder_entries_cache: dict[str, tuple[float, Any, list[tuple[str, Path]]]] = field(default_factory=dict)
    folder_reconcile_running: set[str] = field(default_factory=set)
    # (folder, page, page_size) -> (computed_at, (manifest_sig, dir_mtime), (notes, page, has_next))
    # for SOURCE folders, whose listing recurses the date-bucketed tree over SMB
    # (rglob) and reads per-page summaries — cached so repeat views don't re-walk.
    source_folder_page_cache: dict[tuple[str, int, int], tuple[float, tuple[float, float], tuple[list["Note"], int, bool]]] = field(default_factory=dict)


@dataclass
class SearchCoreResult:
    entity_sections: list[tuple[str, list[tuple[Note, str, float]]]]
    matches: list[tuple[Note, str, float]]
    warming: bool = False
    partial: bool = False
    fast_entity: bool = False
    source_rels: set[str] = field(default_factory=set)
    sidebar_notes: list[Note] = field(default_factory=list)
    top_matches: list[tuple[Note, str, float]] = field(default_factory=list)


VIEWS: dict[str, ViewState] = {}
VIEW_ORDER: list[str] = []
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_TTL_SEC = 15.0
_last_discovery_at: float = 0.0

# How long an entity-folder listing is reused. Also keyed by index sig + dir
# mtime so a newly-added entity page shows up immediately when the folder
# changes, and ES index swaps never serve a stale listing.
_FOLDER_ENTRIES_TTL_SEC = 15.0

# How long a SOURCE-folder listing page (date-bucket rglob + per-page summary reads
# over SMB) is reused. Also keyed by manifest signature so new sources appear on
# the next ingest; the TTL caps staleness while the manifest is idle.
_SOURCE_FOLDER_TTL_SEC = 20.0


def _local_share_path(path: Path) -> Path:
    raw = str(path)
    if os.name != "nt" or not raw.startswith("\\\\"):
        return path
    parts = raw.lstrip("\\").split("\\")
    if len(parts) < 2:
        return path
    host, share, *rest = parts
    computer = os.environ.get("COMPUTERNAME", "")
    if host.split(".", 1)[0].casefold() != computer.split(".", 1)[0].casefold():
        return path
    if len(share) != 1 or not share.isalpha():
        return path
    return Path(f"{share.upper()}:\\", *rest)


def _view_state_from_spec(spec: dimension_registry.ViewSpec,
                          vault_root: Path) -> ViewState:
    return ViewState(
        name=spec.name,
        wiki_root=vault_root / spec.wiki_dir,
        is_default=spec.is_default,
        ready_marker=spec.ready_marker,
    )


def discover_views(vault_root: Path | None = None, force: bool = False) -> None:
    """Discover and register views from the registry + vault filesystem.

    Idempotent and TTL-gated: existing ViewState (caches, locks) is preserved;
    only newly-appearing views are added, and views whose directories vanish
    are dropped. Safe to call from concurrent request handlers.
    """
    global _last_discovery_at
    with _DISCOVERY_LOCK:
        now = time.monotonic()
        if not force and VIEWS and (now - _last_discovery_at) < _DISCOVERY_TTL_SEC:
            return
        root = _local_share_path(vault_root or config.VAULT_ROOT)
        reg = reader_contract.registry()
        seen: set[str] = set()
        for spec in reg.discover_view_dirs():
            view_dir = root / spec.wiki_dir
            if not view_dir.is_dir():
                continue
            seen.add(spec.name)
            if spec.name not in VIEWS:
                state = _view_state_from_spec(spec, root)
                VIEWS[spec.name] = state
                VIEW_ORDER.append(spec.name)
                if not state.is_default:
                    logging.info("argus_wiki discovered new view: %s -> %s", spec.name, view_dir)
        for stale in [n for n in VIEWS if n not in seen]:
            VIEWS.pop(stale, None)
            if stale in VIEW_ORDER:
                VIEW_ORDER.remove(stale)
            logging.info("argus_wiki dropped vanished view: %s", stale)
        _last_discovery_at = now


def ensure_views() -> None:
    discover_views()


def get_view(view: str) -> ViewState:
    ensure_views()
    try:
        return VIEWS[view]
    except KeyError:
        discover_views(force=True)
        try:
            return VIEWS[view]
        except KeyError:
            raise KeyError(f"unknown view: {view}") from None


def view_status(view: ViewState, include_search: bool = True) -> dict[str, Any]:
    if not view.ready_marker:
        status = {"ready": True}
    else:
        marker = view.wiki_root / view.ready_marker
        if not marker.exists():
            status = {"ready": False, "reason": "first synth has not completed yet"}
        else:
            try:
                status = {"ready": True, **json.loads(marker.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError):
                status = {"ready": True}
    if include_search:
        audit, audit_code = ingest_audit_status()
        status["search"] = audit
        status["search_ready"] = audit_code == 200
    return status


def is_ready(view: ViewState) -> bool:
    return bool(view_status(view, include_search=False).get("ready"))


def _path_probe(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "repr": repr(str(path))}
    for name, fn in (
        ("exists", path.exists),
        ("is_dir", path.is_dir),
        ("is_file", path.is_file),
    ):
        try:
            out[name] = fn()
        except OSError as exc:
            out[name] = f"{type(exc).__name__}: {exc}"
    try:
        out["children"] = [p.name for p in list(path.iterdir())[:12]]
    except OSError as exc:
        out["children"] = f"{type(exc).__name__}: {exc}"
    return out


def view_diagnostics(view: ViewState) -> dict[str, Any]:
    home_rel = reader_contract.ensure_md_rel(reader_contract.home_page_rel())
    return {
        "view": view.name,
        "vault_root": _path_probe(config.VAULT_ROOT),
        "wiki_root": _path_probe(view.wiki_root),
        "home_page": _path_probe(view.wiki_root / home_rel),
    }


# Use the libyaml C loader when available — frontmatter is parsed once per file
# during the index scan (tens of thousands of files), and the pure-Python loader
# is ~10-30x slower and GIL-bound, which dominates cold-scan CPU time.
try:
    _YAML_LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - libyaml not built
    _YAML_LOADER = yaml.SafeLoader


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.load(match.group(1), Loader=_YAML_LOADER) or {}
        return data if isinstance(data, dict) else {}, text[match.end():]
    except yaml.YAMLError:
        return {}, text[match.end():]


def title_for(path: Path, body: str) -> str:
    match = HEADING_RE.search(body)
    if match:
        raw = re.sub(r"\s+", " ", match.group(1)).strip()
        # 1) Piped wikilink: [[target|label]] → label
        piped = re.search(r"\[\[([^]|]+)\|([^\]]+)\]\]", raw)
        if piped:
            return piped.group(2).strip()
        # 2) Plain wikilink: [[target]] → last path segment
        plain_wl = re.search(r"\[\[([^\]]+)\]\]", raw)
        if plain_wl:
            target = plain_wl.group(1).strip()
            # Use last path segment as display name
            label = target.rsplit("/", 1)[-1].replace("_", " ").strip()
            return label or target
        # 3) Markdown link: [label](url) → label
        md_link = re.search(r"\[([^\]]+)\]\([^)]+\)", raw)
        if md_link:
            return md_link.group(1).strip()
        # 4) Strip any leftover brackets
        cleaned = re.sub(r"\[+|\]+|\(.*?\)", "", raw).strip()
        return cleaned or raw
    return path.stem.replace("_", " ")


def route_for(view: str, rel: str) -> str:
    return reader_contract.route_for("page", view=view, rel=reader_contract.stem_no_md(rel),
                                     prefix=URL_PATH)


def _visible_rel(rel: str) -> str:
    clean = rel.replace("\\", "/").strip("/")
    if clean == PUBLIC_PAGE_DIR:
        return ""
    prefix = f"{PUBLIC_PAGE_DIR}/"
    if clean.startswith(prefix):
        return clean[len(prefix):]
    return clean


def _folder_index_values(folder: str) -> set[str] | None:
    spec = dimension_registry.dimension_for_page_dir(_visible_rel(folder))
    if not spec or not spec.raw.get("index_values_from_extraction"):
        return None
    extraction = spec.raw.get("extraction")
    if not isinstance(extraction, dict):
        return None
    return {str(value).casefold() for value in extraction.get("values") or []}


def _reader_route_for(view: str, rel: str) -> str:
    visible = _visible_rel(rel)
    return route_for(view, visible or rel)


def _folder_route(view: str, folder: str) -> str:
    return reader_contract.route_for("folder", view=view, dir=folder, prefix=URL_PATH)


def _search_route(view: str) -> str:
    return reader_contract.route_for("search", view=view, prefix=URL_PATH)


def _trending_route(view: str) -> str:
    return reader_contract.route_for("trending", view=view, prefix=URL_PATH)


def _asset_route(view: str, rel: str) -> str:
    return reader_contract.route_for("asset", view=view, rel=rel, prefix=URL_PATH)


def _raw_route(kind: str = "open") -> str:
    template_kind = "raw_open" if kind else "raw"
    return reader_contract.route_for(template_kind, prefix=URL_PATH)


def _content_route(view: str, rel: str) -> str:
    clean = quote(rel.replace("\\", "/").lstrip("/"), safe="/")
    return f"{URL_PATH}/{quote(view, safe='')}/content/{clean}"


def _content_file_route(view: str, rel: str) -> str:
    clean = quote(rel.replace("\\", "/").lstrip("/"), safe="/")
    return f"{URL_PATH}/{quote(view, safe='')}/content-file/{clean}"


def _note_identity_title(note: Note) -> str:
    try:
        dim_spec = dimension_registry.dimension_for_page_dir(note.folder)
        dim = dimension_registry.dimension(dim_spec.name) if dim_spec else {}
    except (AttributeError, KeyError):
        dim_spec = None
        dim = {}
    if not isinstance(dim, dict) or dim.get("type") != "entity":
        return note.title
    keys = [
        str(dim.get("entity_key") or "").replace(" ", "_"),
        str(dim.get("singular") or "").replace(" ", "_"),
        note.folder[:-1] if note.folder.endswith("s") else note.folder,
        note.folder,
    ]
    if note.folder == "senders":
        keys.extend(["sender", "broker", "name"])
    elif note.folder == "tickers":
        keys.extend(["ticker", "primary_ticker"])
    for key in [k for k in keys if k]:
        value = note.frontmatter.get(key)
        if isinstance(value, list):
            value = next((str(x).strip() for x in value if str(x).strip()), "")
        elif value not in (None, ""):
            value = str(value).strip()
        if value:
            if note.folder == "senders":
                return dimension_registry.label_for_sender(str(value), default=str(value))
            return str(value)
    if note.folder == "senders":
        return dimension_registry.label_for_sender(
            Path(note.rel).stem, default=Path(note.rel).stem.replace("_", " ")
        )
    return Path(note.rel).stem.replace("_", " ")


def _note_list_title(note: Note) -> str:
    title = _note_identity_title(note) or note.title
    stem = Path(note.rel).stem
    if title and title != stem and not title.startswith("src_"):
        return title
    # List/results hot path (search cards, folder listings): bulk=True -> cache
    # only, never a per-result ES/SMB read. Only reached when the note's own title
    # is a bare stem; a result-heavy search page would otherwise do one network
    # read per stem-titled hit (the search's ~18s). Uncached -> readable stem.
    source_title = _source_display_title(note.rel, bulk=True)
    return source_title or title or stem.replace("_", " ")


def _daily_snapshot_backlink_suffix(note: Note) -> str:
    meta = _daily_snapshot_meta(note)
    if not meta:
        return ""
    return f"{meta['kind']} {meta['day_label']} {meta['time_label']}"


def _daily_snapshot_meta(note: Note) -> dict[str, str | int] | None:
    rel = note.rel.replace("\\", "/")
    match = re.match(r"^daily/(\d{4}-\d{2}-\d{2})/(today(?:-full)?|today_full|feed)_(\d{6})(\d*)\.md$", rel, re.I)
    if not match:
        return None
    day, kind, hhmmss, rest = match.groups()
    kind = kind.replace("_", "-").lower()
    try:
        dt = datetime.strptime(f"{day} {hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}", "%Y-%m-%d %H:%M:%S")
        day_label = dt.strftime("%d-%b-%Y")
        sort_ts = int(dt.strftime("%Y%m%d%H%M%S"))
    except ValueError:
        day_label = day
        sort_ts = 0
    time_label = f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"
    if rest:
        time_label = f"{time_label}.{rest[:3]}"
    return {
        "kind": kind,
        "day_label": day_label,
        "time_label": time_label,
        "sort_ts": sort_ts,
        "compact_label": f"{kind} {day_label} {time_label}",
    }


def _backlink_title(note: Note) -> str:
    title = _note_list_title(note)
    suffix = _daily_snapshot_backlink_suffix(note)
    if suffix and suffix.lower() not in title.lower():
        return f"{title} · {suffix}"
    return title


def _backlink_label(note: Note, *, compact_daily: bool = False) -> str:
    if compact_daily:
        meta = _daily_snapshot_meta(note)
        if meta:
            return str(meta["compact_label"])
    return _backlink_title(note)


def _today_backlink_page(note: Note) -> bool:
    rel = note.rel.replace("\\", "/").lower()
    return rel in {"today.md", "today-full.md"} or re.match(r"^daily/\d{4}-\d{2}-\d{2}/today(?:-full)?(?:_\d+)?\.md$", rel) is not None


def _backlink_sort_key(note: Note, label: str, *, today_page: bool = False) -> tuple:
    meta = _daily_snapshot_meta(note)
    if today_page and meta:
        return (0, -int(meta["sort_ts"]), note.rel)
    if meta:
        return (1, -int(meta["sort_ts"]), note.rel)
    return (2, note.folder, label.lower(), note.rel)


def _singleton_redirect_rel(page_rel: str, frontmatter: dict, wiki_root: Path | None = None) -> str | None:
    """When identity maps the page's entity to exactly one entity in another
    dimension, return that target's `<page_dir>/<slug>` rel (no .md). Driven
    by `redirect_to_singleton_of:` declarations in dimensions.yaml."""
    redirects = dimension_registry.singleton_redirects()
    if not redirects:
        return None
    clean = page_rel.replace("\\", "/")
    folder = clean.split("/", 1)[0] if "/" in clean else ""
    root = wiki_root or config.WIKI_ROOT
    for spec in redirects:
        if spec.get("from_page_dir") != folder:
            continue
        if str(frontmatter.get("type") or "") == str(spec.get("type_fm_skip") or "folder_index"):
            return None
        entity_key = str(spec.get("identity_field") or "").replace(" ", "_")
        if not entity_key:
            return None
        label = frontmatter.get(entity_key)
        if label is None or not str(label).strip():
            return None
        identifiers = frontmatter.get("identifiers")
        if isinstance(identifiers, list) and any(
            isinstance(item, dict) and str(item.get("type") or "") == "private_code"
            for item in identifiers
        ):
            return None
        from_dim = str(spec.get("from_dim") or "")
        to_dim = str(spec.get("to_dim") or "")
        to_page_dir = str(spec.get("to_page_dir") or "")
        target_dim = dimension_registry.dimension(to_dim)
        slug_max = int(target_dim.get("slug_max_len") or 80)

        syms = _primary_redirect_entities(frontmatter, to_dim)
        rel = _single_existing_redirect_target(syms, to_page_dir, root, slug_max)
        if rel:
            return rel

        if identifiers:
            if to_page_dir:
                identifier_targets: list[str] = []
                for item in identifiers if isinstance(identifiers, list) else []:
                    if not isinstance(item, dict):
                        continue
                    identifier = str(item.get("identifier") or "").strip()
                    if identifier:
                        identifier_targets.append(identifier)
                rel = _single_existing_redirect_target(identifier_targets, to_page_dir, root, slug_max)
                if rel:
                    return rel
            return None
        syms = relationship_source_entities(to_dim, from_dim).get(relationship_entity_key(from_dim, str(label)))
        rel = _single_existing_redirect_target(syms or [], to_page_dir, root, slug_max)
        if rel:
            return rel
    return None


def _primary_redirect_entities(frontmatter: dict, to_dim: str) -> list[str]:
    target_dim = dimension_registry.dimension(to_dim)
    singular = str(target_dim.get("singular") or to_dim.rstrip("s") or to_dim).replace(" ", "_")
    fields = (f"primary_{to_dim}", f"primary_{singular}")
    out: list[str] = []
    for field in fields:
        raw = frontmatter.get(field)
        if isinstance(raw, str) and raw.strip():
            out.append(raw.strip())
        elif isinstance(raw, list):
            out.extend(str(item).strip() for item in raw if str(item).strip())
    return out


def _single_existing_redirect_target(entities: list[str], page_dir: str, root: Path, slug_max: int) -> str | None:
    clean = [str(entity).strip() for entity in entities if str(entity).strip()]
    if len(set(clean)) != 1 or not page_dir:
        return None
    stem = entity_filename_slug(clean[0], max_len=slug_max)
    try:
        if (root / page_dir / f"{stem}.md").is_file():
            return f"{page_dir}/{stem}"
    except OSError:
            return None
    return None


def _identifier_redirect_rel(page_rel: str, label: str, wiki_root: Path | None = None) -> str | None:
    clean = page_rel.replace("\\", "/")
    folder = clean.split("/", 1)[0] if "/" in clean else ""
    dim_spec = dimension_registry.dimension_for_page_dir(folder)
    if not dim_spec:
        return None
    dim_name = dim_spec.name
    identifier = entity_identifiers.resolve(label, dim_name)
    if not identifier:
        return None
    target_dir = dimension_registry.page_dir_for(identifier.target_dimension) or identifier.target_dimension
    target_dim = dimension_registry.dimension(identifier.target_dimension)
    target_stem = entity_filename_slug(
        canonicalize.canonical_for(identifier.target_dimension, identifier.target),
        max_len=int(target_dim.get("slug_max_len") or 80),
    )
    target_rel = f"{target_dir}/{target_stem}"
    root = wiki_root or config.WIKI_ROOT
    if target_rel.lower() == clean.removesuffix(".md").lower():
        return None
    return target_rel if (root / target_dir / f"{target_stem}.md").is_file() else None


def _canonical_entity_redirect_rel(page_rel: str, frontmatter: dict, wiki_root: Path | None = None) -> str | None:
    clean = page_rel.replace("\\", "/")
    folder = clean.split("/", 1)[0] if "/" in clean else ""
    dim_spec = dimension_registry.dimension_for_page_dir(folder)
    if not dim_spec:
        return None
    dim_name = dim_spec.name
    dim = dimension_registry.dimension(dim_name)
    if str(dim.get("type") or "") != "entity":
        return None
    entity_key = str(dim.get("singular") or dim_name.rstrip("s") or "").replace(" ", "_")
    label = str(frontmatter.get(entity_key) or "").strip()
    identifier_rel = _identifier_redirect_rel(
        page_rel,
        label or Path(clean).stem.replace("_", " "),
        wiki_root,
    )
    if identifier_rel:
        return identifier_rel
    if not label:
        return None
    canonical = (canonicalize.canonical_for(dim_name, label, allow_manifest_read=False)
                 if dim_name == "authors" else canonicalize.canonical_for(dim_name, label))
    if dim_name == "companies" and canonical == label:
        canonical = _canonical_company_from_top_ticker(label, frontmatter, wiki_root) or canonical
    if not canonical or canonical == label:
        return None
    stem = entity_filename_slug(canonical, max_len=int(dim.get("slug_max_len") or 80))
    target_rel = f"{folder}/{stem}"
    if target_rel.lower() == clean.removesuffix(".md").lower():
        return None
    root = wiki_root or config.WIKI_ROOT
    try:
        if not (root / folder / f"{stem}.md").is_file():
            return None
    except OSError:
        return None
    return target_rel


def _canonical_company_from_top_ticker(label: str, frontmatter: dict, wiki_root: Path | None = None) -> str | None:
    if frontmatter.get("primary_tickers") or frontmatter.get("identifiers"):
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9&.-]*", label.strip()):
        return None
    top_tickers = frontmatter.get("top_tickers")
    if isinstance(top_tickers, str):
        tickers = [top_tickers]
    elif isinstance(top_tickers, list):
        tickers = [str(ticker).strip() for ticker in top_tickers if str(ticker).strip()]
    else:
        tickers = []
    if len(set(tickers)) != 1:
        return None
    ticker_dir = dimension_registry.page_dir_for("tickers") or "tickers"
    root = wiki_root or config.WIKI_ROOT
    ticker_stem = entity_filename_slug(tickers[0], max_len=60)
    try:
        ticker_text = (root / ticker_dir / f"{ticker_stem}.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ticker_fm, _body = split_frontmatter(ticker_text)
    target = canonicalize.canonical_for("companies", str(ticker_fm.get("company") or "").strip())
    if not target:
        return None
    if relationship_entity_key("companies", target) == relationship_entity_key("companies", label):
        return None
    return target


def _serves_as_own_entity(frontmatter: dict) -> bool:
    """True when a page carries its own synthesized standalone content, so it
    must be served as a first-class entity rather than collapsed into another
    dimension. A connected ticker (e.g. the built private code ``.OAI``) that
    now has a full synth writeup is a real entity, not just a pointer to its
    company."""
    if not isinstance(frontmatter, dict):
        return False
    return bool(frontmatter.get("synthesized_at") or frontmatter.get("model"))


def _cross_dim_redirect_suppressed(src_rel: str, target_rel: str | None, frontmatter: dict) -> bool:
    """Suppress a redirect ONLY when it points to a different dimension (folder)
    and the source page is its own synthesized entity. Same-dimension canonical
    / alias dedup (e.g. a company-name variant -> canonical company, a ticker
    alias -> canonical ticker) is untouched — this generalizes to every
    connected ticker, not any one page."""
    if not target_rel:
        return False
    src_folder = str(src_rel).replace("\\", "/").strip("/").split("/", 1)[0]
    tgt_folder = str(target_rel).replace("\\", "/").strip("/").split("/", 1)[0]
    if not src_folder or src_folder == tgt_folder:
        return False
    return _serves_as_own_entity(frontmatter)


def _manifest_sig() -> float:
    try:
        from argus_wiki.render import entity_source_sections
        return entity_source_sections.manifest_index_signature()
    except Exception:
        return 0.0


def _hydrate_note_for_serve(view: ViewState, note: Note) -> Note:
    """Apply manifest-backed source-list hydration for the page being served.

    Done per-viewed-page (cheap) rather than per-file during the index scan, so
    a cold scan stays I/O-bound and parallelizable. Returns the note unchanged
    when there's nothing to hydrate (sources, source-index pages, non-entity).
    """
    if reader_contract.is_source_rel(note.rel):
        return note
    from argus_wiki.render import entity_source_sections
    if entity_source_sections.is_source_index_rel(note.rel, note.frontmatter):
        return note
    t0 = time.time()
    fm, body = entity_source_sections.hydrate_page_body(note.rel, note.frontmatter, note.text)
    elapsed = time.time() - t0
    if elapsed > 1.0:
        logging.info("argus_wiki hydrate slow view=%s rel=%s elapsed=%.2fs", view.name, note.rel, elapsed)
    if body == note.text and fm is note.frontmatter:
        return note
    return replace(note, frontmatter=fm, text=body)


def _defer_note_sources(view: ViewState, note: Note) -> tuple[Note, bool]:
    """Strip the page's embedded source list so it paints without the ES query;
    the block is fetched async (see render_source_section / the deferred JS).
    Returns ``(note, deferred)``. Non-source-list pages are returned unchanged."""
    if reader_contract.is_source_rel(note.rel):
        return note, False
    from argus_wiki.render import entity_source_sections
    if entity_source_sections.is_source_index_rel(note.rel, note.frontmatter):
        return note, False
    try:
        stripped, had = entity_source_sections.strip_source_section(
            note.rel, note.frontmatter, note.text)
    except Exception:
        logging.debug("argus_wiki defer sources: strip failed rel=%s", note.rel, exc_info=True)
        return note, False
    if not had:
        return note, False
    return replace(note, text=stripped), True


def _deferred_sources_block(rel: str) -> str:
    """Placeholder the deferred JS fills with the live source list."""
    return (
        f'<section class="wiki-sources-deferred" data-deferred-sources="1" '
        f'data-rel="{html.escape(rel)}">'
        f'<p aria-busy="true" style="color:var(--text-muted);font-size:13px">Loading sources…</p>'
        f"</section>"
    )


def _deferred_structured_block(rel: str) -> str:
    """Placeholder the deferred JS fills with the entity's structured KPI tiles.
    Deferred like the source list: reading+parsing the dataset files is tens of
    seconds on prod, so the page paints first and the tiles fetch async."""
    return (
        f'<section class="wiki-structured-deferred" data-deferred-structured="1" '
        f'data-rel="{html.escape(rel)}">'
        f'<p aria-busy="true" style="color:var(--text-muted);font-size:13px">Loading data…</p>'
        f"</section>"
    )


_SUGGEST_MAX_SCAN = 80000


def search_suggest(view_name: str, q: str, limit: int = 8) -> dict:
    """Typeahead result, structured for a clean UI:
      { "categories": [ {kind, items:[{label,route}]}, ... up to 2 ],
        "results":    [ {label, kind, route, sub}, ... general relevance-sorted ] }

    ``categories`` = the up-to-2 entity dimensions whose names best match the query
    (e.g. tickers, companies), up to 3 names each, rendered inline. ``results`` =
    the general list -- lexical source/content hits (ES, BM25, relevance order).
    Category selection is generic: whichever dimensions the vault defines that match
    best win; no hardcoded per-kind logic. `kind` is just the page's folder."""
    empty = {"categories": [], "results": []}
    ql = (q or "").strip()
    if len(ql) < 2:
        return empty
    try:
        view = get_view(view_name)
    except KeyError:
        return empty
    if not is_ready(view):
        return empty

    def _kind_of(rel: str) -> str:
        return (rel.split("/", 1)[0] if "/" in rel else "").lower()

    # Entity name matches grouped by kind, each ranked (tier, label length, alpha).
    qs = squash(ql)  # 'henry kim' / 'henry_kim' / 'HenryKim' all -> 'henrykim'
    by_kind: dict[str, list] = {}
    if qs and view.notes_by_rel:
        for i, (rel, note) in enumerate(view.notes_by_rel.items()):
            if i > _SUGGEST_MAX_SCAN:
                break
            if reader_contract.is_source_rel(rel):
                continue
            title = (note.title or "").strip()
            stem = Path(rel).stem
            label = title or _display_name(stem)
            # Match on squashed forms so '_', spaces, punctuation and case are all
            # ignored: 'henry kim' matches the 'henry_kim' author; 'usd/jpy' -> 'usdjpy'.
            tsq, ssq = squash(title), squash(stem)
            tokens = [squash(t) for t in re.split(r"[\s_\-/.]+", f"{title} {stem}") if t]
            if tsq.startswith(qs) or ssq.startswith(qs):
                tier = 0
            elif any(tok.startswith(qs) for tok in tokens):
                tier = 1
            elif qs in tsq or qs in ssq:
                tier = 2
            else:
                continue
            by_kind.setdefault(_kind_of(rel), []).append((tier, len(label), label.lower(), label, note.route))

    for items in by_kind.values():
        items.sort()
    # The two kinds whose BEST match is strongest become the inline category rows.
    ranked_kinds = sorted(by_kind.items(), key=lambda kv: kv[1][0][:3])
    categories: list[dict] = []
    used: set[str] = set()
    for kind, items in ranked_kinds[:2]:
        picks = [{"label": it[3], "route": it[4]} for it in items[:3]]
        for it in items[:3]:
            used.add(it[4])
        if picks:
            categories.append({"kind": kind, "items": picks})

    # General list: lexical source/content hits (ES, relevance order), minus anything
    # already shown as a category chip.
    results: list[dict] = []
    seen = set(used)
    try:
        from argus_wiki.search import wiki_api
        hits, _available = wiki_api._fts_source_hits(ql, view_name, limit + 4, lexical_only=True)
        for hit in hits:
            rel = str(hit.get("rel") or "")
            if not rel:
                continue
            route = str(hit.get("route") or "") or route_for(view_name, rel)
            if not route or route in seen:
                continue
            seen.add(route)
            label = str(hit.get("title") or _display_name(Path(rel).stem))
            snippet = re.sub(r"<[^>]+>", "", str(hit.get("snippet") or "")).strip()
            day = str(hit.get("day") or hit.get("date") or (hit.get("frontmatter") or {}).get("day") or "")
            results.append({"label": label, "kind": _kind_of(rel), "route": route,
                            "sub": snippet[:140], "date": _short_source_date(day)})
            if len(results) >= limit:
                break
    except Exception:
        logging.debug("argus_wiki suggest source search failed", exc_info=True)
    return {"categories": categories, "results": results}


def render_structured_section(view_name: str, rel: str) -> tuple[str, int]:
    """Rendered HTML for a page's structured KPI tiles — the deferred fragment.
    202 while the view is still building; empty 200 when there are no tiles.
    The build reads+parses the entity's dataset files (tens of seconds on prod on
    the real data), which is exactly why it is fetched off the page render."""
    view = get_view(view_name)
    if not is_ready(view):
        return "", 202
    if not structured_enabled():
        return "", 200
    clean = unquote(rel or "").split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean or ".." in clean.split("/"):
        return "", 200
    page_rel = clean if clean.lower().endswith(".md") else f"{clean}.md"
    try:
        from argus_wiki.render import structured_sections as _structured
        html_out = _structured.render_structured_html(page_rel)
    except Exception:
        logging.debug("argus_wiki structured section failed rel=%s", clean, exc_info=True)
        return "", 200
    return (html_out or ""), 200


def render_source_section(view_name: str, rel: str) -> tuple[str, int]:
    """Rendered HTML for a page's live source list — the deferred fragment. 202
    while the view is still building; empty 200 when the page has no sources."""
    view = get_view(view_name)
    if not is_ready(view):
        return "", 202
    clean = unquote(rel or "").split("#", 1)[0].replace("\\", "/").strip("/")
    if ".." in clean.split("/"):
        return "", 200
    note = view.notes_by_rel.get(clean) or view.notes_by_rel.get(f"{clean}.md")
    if note is None:
        try:
            note = read_note_summary(view, vault_path(view, rel))
        except OSError:
            return "", 200
    from argus_wiki.render import entity_source_sections
    md = entity_source_sections.source_section_markdown(note.rel, note.frontmatter)
    if not md:
        return "", 200
    indexes = _indexes_for_page_render(view, note)
    fragment, _toc = render_markdown(view, md, note.rel, indexes.get("lookup", {}))
    return fragment, 200


def read_note(view: ViewState, path: Path, rel_override: str | None = None) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    mtime = path.stat().st_mtime
    frontmatter, body = split_frontmatter(text)
    rel = rel_override or path.relative_to(view.wiki_root).as_posix()
    # NOTE: source-list hydration is intentionally NOT done here. read_note runs
    # for every file during the (parallel) index scan; hydrating per page is a
    # CPU re-render that serializes on the GIL and dominates cold-scan time.
    # Hydration is a serve-time concern, applied once for the page being viewed
    # in render_page via _hydrate_note_for_serve().
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    raw_tags = frontmatter.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = tuple(str(tag) for tag in raw_tags if tag)
    title = title_for(path, body)
    fm_blob = json.dumps(frontmatter, ensure_ascii=False, default=str)
    blob = f"{title}\n{rel}\n{fm_blob}\n{body[:8000]}"
    text_lower = blob.lower()
    text_squashed = squash(blob)
    alt = _canonical_entity_redirect_rel(rel, frontmatter, view.wiki_root) or _singleton_redirect_rel(rel, frontmatter, view.wiki_root)
    if _cross_dim_redirect_suppressed(rel, alt, frontmatter):
        alt = None  # a synthesized entity keeps its own route; links point at it, not its company
    route = _reader_route_for(view.name, alt) if alt else _reader_route_for(view.name, rel)
    outbound = tuple(_backlink_keys_for_text(body))
    return Note(rel, route, title, folder, mtime, body, frontmatter, tags,
                text_lower, text_squashed, outbound, True)


def read_note_summary(view: ViewState, path: Path, rel_override: str | None = None) -> Note:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(32768)
    mtime = path.stat().st_mtime
    frontmatter, body = split_frontmatter(text)
    rel = rel_override or path.relative_to(view.wiki_root).as_posix()
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    title = title_for(path, body)
    route = _reader_route_for(view.name, rel)
    blob = f"{title}\n{rel}"
    return Note(rel, route, title, folder, mtime, "", frontmatter, (),
                blob.lower(), squash(blob), (), False)


def read_note_stub(view: ViewState, path: Path, rel_override: str | None = None) -> Note:
    rel = rel_override or path.relative_to(view.wiki_root).as_posix()
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    title = path.stem.replace("_", " ")
    route = _reader_route_for(view.name, rel)
    blob = f"{title}\n{rel}"
    return Note(rel, route, title, folder, 0.0, "", {}, (),
                blob.lower(), squash(blob), (), False)


def vault_path(view: ViewState, rel: str) -> Path:
    clean = unquote(rel).replace("\\", "/").strip("/") or "index"
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    parts = [part for part in clean.split("/") if part]
    sources = set(reader_contract.source_page_dirs())
    if not view.is_default and parts and parts[0] in sources and ".." not in parts:
        return view.wiki_root / clean
    candidate = view.wiki_root / clean
    path = candidate.resolve()
    root = view.wiki_root.resolve()
    if root != path and root not in path.parents:
        default_root = reader_contract.default_view_wiki_root().resolve()
        if (
            not view.is_default
            and parts
            and ".." not in parts
            and parts[0] in _default_fallback_page_dirs()
            and (default_root == path or default_root in path.parents)
        ):
            return candidate
        raise ValueError("page outside vault")
    return candidate


def _source_fallback_path(view: ViewState, rel: str) -> tuple[Path | None, str | None]:
    """Resolve a missing-on-disk URL under `sources/` to an existing file.

    Two recovery paths handled here:
      (a) Non-default view referencing a source page that only exists in the
          default view's `wiki/sources/`. (Original behaviour.)
      (b) Legacy flat URL `sources/src_xxx[.md]` whose file has moved to the
          bucketed layout `sources/<YYYY-MM-DD>/src_xxx.md` (or _undated/).
          A one-level glob locates it, so external bookmarks and cached
          citation URLs keep working forever.

    Returns (absolute path, new-rel-under-wiki) on a hit, or (None, None).
    """
    clean = unquote(rel).replace("\\", "/").strip("/")
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    sources = reader_contract.source_page_dirs()
    parts = [p for p in clean.split("/") if p]
    if not parts:
        return None, None
    head = parts[0]
    if head not in sources:
        return None, None

    def _glob_by_stem(root: Path, sources_dir_name: str, stem: str) -> tuple[Path, str] | None:
        sources_root = root / sources_dir_name
        if not sources_root.is_dir():
            return None
        hits = list(sources_root.glob(f"*/{stem}.md"))
        if not hits:
            return None
        hit = hits[0]
        try:
            new_rel = hit.relative_to(root).as_posix()
        except ValueError:
            return None
        return hit, new_rel

    # (a) Default-view source lookup for non-default views (exact path first).
    if not view.is_default:
        default_root = reader_contract.default_view_wiki_root()
        path = (default_root / clean).resolve()
        sources_root = (default_root / head).resolve()
        if sources_root == path or sources_root in path.parents:
            if path.exists():
                return path, clean
            # (b) Stale flat URL → bucketed file in the default view.
            if len(parts) == 2:
                stem = parts[1][:-3]
                got = _glob_by_stem(default_root, head, stem)
                if got:
                    return got
        return None, None

    # (b) Stale flat URL in the default view itself.
    if len(parts) == 2:
        stem = parts[1][:-3]
        got = _glob_by_stem(view.wiki_root, head, stem)
        if got:
            return got
    return None, None


def _source_index_fallback_path(view: ViewState, rel: str) -> tuple[Path | None, str | None]:
    """Generate a missing ``<entity>.sources`` overflow page from the manifest.

    The entity page's "All N sources" overflow link is driven by the live
    manifest count, so its target must resolve even before the write path
    (synth / refresh_touched) has materialized the file. Build it on demand,
    persist it (so Obsidian and later requests see it too), and serve it.
    """
    from argus_wiki.render import entity_source_sections

    clean = unquote(rel).replace("\\", "/").strip("/")
    if not entity_source_sections.is_source_index_rel(clean):
        return None, None
    text = entity_source_sections.render_source_index_text(clean, wiki_root=view.wiki_root)
    if not text:
        return None, None
    rel_md = clean if clean.lower().endswith(".md") else f"{clean}.md"
    path = view.wiki_root / rel_md
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write only when changed so the page's mtime (and the render cache keyed
        # on it) stays stable across re-renders of an unchanged source index.
        if not path.is_file() or path.read_text(encoding="utf-8", errors="replace") != text:
            path.write_text(text, encoding="utf-8")
    except OSError:
        return None, None
    return path, rel_md


def _default_fallback_path(view: ViewState, rel: str) -> tuple[Path | None, str | None]:
    """When a non-default view has no markdown on disk, fall back to the default view's wiki tree."""
    if view.is_default:
        return None, None
    clean = unquote(rel).replace("\\", "/").strip("/")
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    parts = [part for part in clean.split("/") if part]
    if not parts or parts[0] not in _default_fallback_page_dirs():
        return None, None
    try:
        if (view.wiki_root / clean).exists():
            return None, None
    except OSError:
        pass
    default_root = reader_contract.default_view_wiki_root()
    path = (default_root / clean).resolve()
    root = default_root.resolve()
    if root != path and root not in path.parents:
        return None, None
    return (path, clean) if path.exists() else (None, None)


def _route_internal_md(view_name: str, rel_posix: str) -> str:
    rel_posix = rel_posix.replace("\\", "/")
    if not rel_posix.lower().endswith(".md"):
        rel_posix = f"{rel_posix}.md"
    return route_for(view_name, rel_posix)


def _recover_source_relative_page_rel(rel_posix: str) -> str:
    rel = rel_posix.replace("\\", "/")
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    parts = [p for p in rel.split("/") if p]
    if len(parts) >= 3 and parts[0] in set(reader_contract.source_page_dirs()):
        page_dirs = _known_dirs()
        if parts[1] in page_dirs:
            return "/".join(parts[1:])
    return rel_posix


def _resolve_wikilink_note(
    view_name: str,
    target: str,
    lookup: dict[str, Note],
    context_note: Note | None = None,
) -> tuple[Note | None, str]:
    note = resolve_note(target, lookup, context_note)
    if note:
        return note, view_name
    clean = target.split("#", 1)[0].replace("\\", "/").strip("/")
    default = _default_view_name()
    if view_name != default and "/" not in clean:
        try:
            view = get_view(view_name)
        except KeyError:
            return None, view_name
        if view.index_result is None:
            return None, view_name
    note, link_view = _load_explicit_link_note(view_name, target, context_note)
    if note:
        return note, link_view
    if view_name == default:
        return None, view_name
    if not clean:
        return None, view_name
    first = clean.split("/", 1)[0]
    if first not in _default_fallback_page_dirs():
        return None, view_name
    try:
        default_view = get_view(default)
    except KeyError:
        return None, view_name
    if default_view.index_result is None:
        kick_background_scan(default_view)
        return None, view_name
    def_lookup = build_indexes(default_view).get("lookup", {})
    note_d = resolve_note(target, def_lookup, context_note)
    if note_d:
        return note_d, default
    return None, view_name


_SOURCE_TITLE_CACHE: dict[str, tuple[float, str | None]] = {}
_SOURCE_TITLE_TTL_SEC = 120.0
_SOURCE_TITLE_CACHE_MAX = 2048


def _source_row_display_title(row: dict | None, fallback: str = "") -> str:
    if not row:
        return fallback
    from argus_wiki.common import source_display

    title = source_display.short_title(source_display.derived_title(row), 72)
    sender = source_display.short_sender(source_display.sender_of(row))
    if sender and sender != "Unknown" and title:
        return f"{sender} — {title}"
    if title:
        return title
    if sender and sender != "Unknown":
        return sender
    return fallback


def _source_display_title(rel: str, *, bulk: bool = False) -> str | None:
    """Human title for a source page that is not in the entity note index.

    ``bulk=True`` is the render hot path (convert_wikilinks): it relies solely on
    caches populated by the batch ``_prefetch_source_titles`` and NEVER does a
    per-link ES ``source_row`` or SMB frontmatter read. A link-heavy page (a busy
    ticker) would otherwise do hundreds of blocking network reads -- 60-80s
    renders that pin the process and hang every other request (today included)."""
    clean = (rel or "").replace("\\", "/").strip("/")
    if not clean:
        return None
    folder = clean.split("/", 1)[0]
    if folder not in set(reader_contract.source_page_dirs()):
        stem = Path(clean).stem
        if not (stem.startswith("src_") or stem.startswith("src::")):
            return None
    now = time.time()
    from argus_wiki.index import reader_cache as rc

    # Render hot path (bulk): memory ONLY. `rc.kv_get` falls back to
    # `store().get_value`, which REOPENS the Delta table over SMB on every
    # `_MEM_KV` miss (a fresh `DeltaTable(...)` + duckdb connect + `_delta_log`
    # read per call). On a cold process a link-heavy page does one such blocking
    # SMB read PER LINK -- 2208 links on a folder index = ~275s. The memory-only
    # getter keeps the documented contract (caches populated by batch prefetch /
    # startup hydrate / watcher); a true miss returns the leaf label below.
    cached_title = rc.kv_get_mem(rc.NS_SOURCE_TITLE, clean)
    if cached_title is not None:
        title = cached_title or None
        _SOURCE_TITLE_CACHE[clean] = (now, title)
        return title
    cached = _SOURCE_TITLE_CACHE.get(clean)
    if cached is not None and now - cached[0] < _SOURCE_TITLE_TTL_SEC:
        return cached[1]
    if bulk:
        # Not in cache and we're mid-render: do NOT fall through to a per-link ES
        # lookup or SMB read (see docstring). Caller uses the leaf label; the
        # batch prefetch / watcher fills real titles into the cache out of band.
        return None
    # Source pages are intentionally absent from the in-memory entity-note index.
    # Resolve their display metadata from local ES before touching the SMB page;
    # link-heavy pages otherwise do one network-share read per linked source.
    title: str | None = None
    try:
        from argus_wiki.index import elastic_search_index

        if elastic_search_index.enabled():
            title = _source_row_display_title(
                elastic_search_index.store().source_row(Path(clean).stem)
            ) or None
    except Exception:
        logging.debug("argus_wiki source title ES lookup failed rel=%s", clean, exc_info=True)

    # ES can be unavailable or mid-reindex; keep a direct-page fallback.
    page_rel = clean if clean.lower().endswith(".md") else f"{clean}.md"
    fm = _source_page_frontmatter(page_rel) if not title else {}
    if fm:
        title = _source_row_display_title(
            {
                "title": fm.get("title"),
                "sender": fm.get("sender") or fm.get("broker"),
                "day": fm.get("day"),
                "raw_path": fm.get("raw_path"),
            }
        ) or None
    if len(_SOURCE_TITLE_CACHE) >= _SOURCE_TITLE_CACHE_MAX:
        _SOURCE_TITLE_CACHE.clear()
    _SOURCE_TITLE_CACHE[clean] = (now, title)
    rc.kv_put_async(rc.NS_SOURCE_TITLE, clean, title or "")
    return title


def _prefetch_source_titles(text: str) -> None:
    """Resolve all source-link labels in one ES request for this markdown page."""
    source_dirs = set(reader_contract.source_page_dirs())
    rels: dict[str, str] = {}
    for match in WIKILINK_RE.finditer(text):
        raw = match.group(1).strip()
        target = raw.split("|", 1)[0].strip().split("#", 1)[0]
        clean = target.replace("\\", "/").strip("/")
        first_dir = clean.split("/", 1)[0] if "/" in clean else ""
        rel = clean if first_dir in source_dirs else _source_link_target(target)
        if rel:
            normalized = rel.replace("\\", "/").strip("/")
            rels[Path(normalized).stem] = normalized
    if not rels:
        return

    now = time.time()
    missing = {
        source_id: rel
        for source_id, rel in rels.items()
        if rel not in _SOURCE_TITLE_CACHE
        or now - _SOURCE_TITLE_CACHE[rel][0] >= _SOURCE_TITLE_TTL_SEC
    }
    if not missing:
        return
    rows: dict[str, dict] = {}
    try:
        from argus_wiki.index import elastic_search_index

        if elastic_search_index.enabled():
            rows = elastic_search_index.store().source_rows(missing)
    except Exception:
        logging.debug("argus_wiki source title ES prefetch failed", exc_info=True)
    from argus_wiki.index import reader_cache as rc

    if len(_SOURCE_TITLE_CACHE) + len(missing) >= _SOURCE_TITLE_CACHE_MAX:
        _SOURCE_TITLE_CACHE.clear()
    for source_id, rel in missing.items():
        title = _source_row_display_title(rows.get(source_id)) or None
        _SOURCE_TITLE_CACHE[rel] = (now, title)
        rc.kv_put_async(rc.NS_SOURCE_TITLE, rel, title or "")


def _trending_href(view_name: str, rel: str, label: str | None = None) -> tuple[str, str]:
    clean = rel.replace("\\", "/").strip("/")
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    folder = clean.split("/", 1)[0] if "/" in clean else ""
    stem = Path(rel).stem if rel else rel
    text = (label or "").strip() or stem
    default = _default_view_name()
    if (
        view_name != default
        and folder in _default_fallback_page_dirs()
    ):
        try:
            vw = get_view(view_name)
            dw = get_view(default).wiki_root
            if not (vw.wiki_root / clean).exists() and (dw / clean).exists():
                return route_for(default, rel), text
        except (KeyError, OSError):
            pass
    return route_for(view_name, rel), text


# Stat (metadata) reads scale with concurrency on the network share. Content
# reads do NOT — beyond a modest concurrency the share throttles and then hangs
# (blocked reads can't be cancelled, draining the pool), so keep reads low.
_SCAN_STAT_WORKERS = max(4, int(os.environ.get("ARGUS_WIKI_SCAN_STAT_WORKERS", "32")))
_SCAN_READ_WORKERS = max(2, int(os.environ.get("ARGUS_WIKI_SCAN_WORKERS", "8")))


# --- Delta-backed entity-page metadata index ----------------------------------
# The vault lives on a network share; a cold scan reads every .md over SMB and
# takes minutes. The built index's METADATA (title, folder, mtime, outbound link
# keys, a short snippet — never the full body) is persisted to a Delta table
# (EntityStore) so a restart loads it instantly and with tiny RAM, and only an
# incremental background scan touches the share. Bodies are read on demand at
# render time. This replaces the old multi-hundred-MB pickle of full notes.


def _note_from_entity_row(view: ViewState, row: dict) -> Note:
    """Build a metadata-only Note (no body) from a Delta entity-index row."""
    rel = str(row.get("rel") or "")
    try:
        fm = json.loads(row.get("fm_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        fm = {}
    try:
        outbound = tuple(json.loads(row.get("outbound") or "[]"))
    except (json.JSONDecodeError, TypeError):
        outbound = ()
    raw_tags = fm.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = tuple(str(t) for t in raw_tags if t)
    snippet = str(row.get("snippet") or "")
    title = str(row.get("title") or Path(rel).stem.replace("_", " "))
    folder = str(row.get("folder") or (rel.split("/", 1)[0] if "/" in rel else ""))
    # Use the route precomputed at scan time (it bakes in canonical/singleton
    # redirects). NEVER recompute it here — the redirect helpers do a filesystem
    # check per note, which on a 19k-note load means minutes of SMB I/O. Falling
    # back to the plain route (for an older index without the column) is fine:
    # render_page still applies the redirect as a 302 at serve time.
    route = str(row.get("route") or "") or _reader_route_for(view.name, rel)
    blob = f"{title}\n{rel}\n{snippet}"
    return Note(rel, route, title, folder, float(row.get("mtime") or 0.0), snippet,
                fm, tags, blob.lower(), squash(blob), outbound, False)


def _es_doc_to_row(doc: dict) -> dict:
    """Map an ES entity doc ``_source`` to the entity-index *row* shape, so the
    same tested Note builder (:func:`_note_from_entity_row`) works unchanged."""
    return {
        "rel": doc.get("rel") or "",
        "folder": doc.get("folder") or "",
        "title": doc.get("title") or "",
        "mtime": doc.get("mtime") or 0.0,
        "route": doc.get("route") or "",   # ES has no route; the builder derives a plain one
        "snippet": str(doc.get("summary") or "")[:600],
        "identity": doc.get("identity") or "",
        "outbound": json.dumps(list(doc.get("outbound") or [])),
        "fm_json": doc.get("fm_json") or "{}",
    }


def _local_entity_freshness_token(view: ViewState) -> str:
    source_dirs = set(reader_contract.source_page_dirs())
    count = 0
    latest = 0.0
    try:
        for path in view.wiki_root.rglob("*.md"):
            rel = path.relative_to(view.wiki_root).as_posix()
            if rel.split("/", 1)[0] in source_dirs:
                continue
            count += 1
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        return ""
    return f"local:{count}:{latest:.6f}"


def _load_index_snapshot(view: ViewState) -> dict[str, Any] | None:
    """Build the full in-memory index snapshot for a view from ELASTICSEARCH
    (entity docs) — NO ``view`` mutation and NO SMB walk, so it's safe off-lock.
    Returns None when ES has no entity docs for the view (reindex needed).
    ES-only: no Delta / vault-scan fallback — a failure surfaces immediately."""
    from argus_wiki.index import elastic_search_index

    if not elastic_search_index.enabled():
        source_dirs = set(reader_contract.source_page_dirs())
        notes_by_rel: dict[str, Note] = {}
        try:
            paths = view.wiki_root.rglob("*.md")
            for path in paths:
                try:
                    rel = path.relative_to(view.wiki_root).as_posix()
                    if rel.split("/", 1)[0] in source_dirs:
                        continue
                    if is_sharepoint_conflict_path(rel):
                        continue
                    note = read_note(view, path, rel_override=rel)
                    notes_by_rel[rel] = note
                except (OSError, ValueError):
                    continue
        except OSError:
            return None
        if not notes_by_rel:
            return None
        built = _build_index_maps(view, notes_by_rel)
        built["token"] = _local_entity_freshness_token(view)
        built["last_scan_at"] = max((note.mtime for note in notes_by_rel.values()), default=0.0)
        return built

    docs = elastic_search_index.iter_all_entity_docs(view.name)
    if not docs:
        return None
    notes_by_rel = {
        rel: _note_from_entity_row(view, _es_doc_to_row(d))
        for d in docs if (rel := str(d.get("rel") or ""))
    }
    if not notes_by_rel:
        return None
    last_scan_at = max((float(d.get("mtime") or 0.0) for d in docs), default=0.0)
    generation = max((float(d.get("indexed_at") or 0.0) for d in docs), default=0.0)
    built = _build_index_maps(view, notes_by_rel)
    built["token"] = f"{len(docs)}:{last_scan_at:.6f}:{generation:.6f}"
    built["last_scan_at"] = last_scan_at
    return built


def _apply_index_snapshot(view: ViewState, built: dict[str, Any]) -> None:
    """Swap a Delta snapshot (from :func:`_load_index_snapshot`) onto ``view``,
    including the freshness token. Hold ``view.lock`` around this."""
    view.last_scan_at = built["last_scan_at"]
    # Record the index's freshness token: the reader reloads (not re-scans)
    # only when the watcher bumps it.
    view.entity_index_token = built["token"]
    _apply_index_maps(view, built)


def _load_index_cache(view: ViewState) -> bool:
    """Seed notes_by_rel (metadata-only) + index_result from the Delta entity
    index. Returns True on a hit; any error or empty table falls through to a
    normal scan. Sets last_scan_at so a fresh index defers the refresh scan.

    Caller holds ``view.lock`` (startup path); the recurring background reload
    uses :func:`_load_index_snapshot` + :func:`_apply_index_snapshot` so its
    heavy build stays off-lock."""
    try:
        built = _load_index_snapshot(view)
        if not built:
            return False
        _apply_index_snapshot(view, built)
        return True
    except Exception:
        logging.exception("argus_wiki entity index load failed view=%s", view.name)
        return False


def _build_index_maps(view: ViewState, notes_by_rel: dict[str, "Note"]) -> dict[str, Any]:
    """Pure builder: lookup / backlinks / search records + index_result from a
    ``notes_by_rel`` map, with NO mutation of ``view``. Because it touches no
    shared state it can run off-lock (the ~10s heavy part for tens of thousands
    of notes); :func:`_apply_index_maps` then swaps the result in under the lock."""
    notes = sorted(notes_by_rel.values(), key=lambda note: (note.folder, _note_list_title(note).lower()))
    lookup: dict[str, Note] = {}
    for note in notes:
        stem = Path(note.rel).with_suffix("").as_posix()
        lookup[stem.lower()] = note
        visible_stem = _visible_rel(stem)
        if visible_stem:
            lookup[visible_stem.lower()] = note
        lookup[Path(note.rel).stem.lower()] = note
        lookup[note.title.lower()] = note

    backlinks: dict[str, list[Note]] = {}
    for note in notes:
        # Use the stored outbound link keys (set from the body at read time and
        # persisted in the Delta entity index) so backlinks work for metadata-
        # only notes that no longer carry the page body in memory.
        keys = note.outbound if note.outbound or not note.body_loaded else _backlink_keys_for_text(note.text)
        for key in keys:
            backlinks.setdefault(key, []).append(note)

    search_records = None
    from argus_wiki.index import elastic_search_index

    if len(notes) > 250 and not elastic_search_index.enabled():
        search_records = _search_records_from_notes(notes)
        try:
            from argus_wiki.search import wiki_api
            wiki_api.prepare_search_records(search_records)
        except Exception:
            logging.exception("argus_wiki search record warm failed view=%s", view.name)
    return {
        "notes_by_rel": notes_by_rel,
        "search_records": search_records,
        "index_result": {"notes": notes, "lookup": lookup, "backlinks": backlinks},
    }


def _apply_index_maps(view: ViewState, built: dict[str, Any]) -> dict[str, Any]:
    """Swap prebuilt index structures onto ``view`` (reference swaps only, fast).
    Hold ``view.lock`` around this so a reader sees an all-old or all-new index,
    never a half-swapped one."""
    view.notes_by_rel = built["notes_by_rel"]
    view.search_records = built["search_records"]
    view.search_records_sig += 1
    view.search_result_cache.clear()
    view.folder_entries_cache.clear()
    view.index_result = built["index_result"]
    return view.index_result


def _finalize_index(view: ViewState) -> dict[str, Any]:
    """Build lookup/backlinks/search records + index_result from
    ``view.notes_by_rel`` and apply in place (caller holds ``view.lock``)."""
    return _apply_index_maps(view, _build_index_maps(view, view.notes_by_rel))


def scan_and_update(view: ViewState) -> dict[str, Any]:
    # ES-only: (re)load the view's index from Elasticsearch — NEVER walk the SMB
    # share. Every background / hourly-refresh / search-cold caller funnels
    # through here, so keeping it ES-native kills all remaining vault scans. An
    # empty result means ES has no entity docs for the view yet (reindex in
    # progress); the reader reloads automatically as the ES doc count rises.
    built = None
    try:
        built = _load_index_snapshot(view)
    except Exception:
        logging.exception("argus_wiki entity index reload failed view=%s", view.name)
    with view.lock:
        if built is not None:
            _apply_index_snapshot(view, built)
        elif view.index_result is None:
            view.index_result = {"notes": [], "lookup": {}, "backlinks": {}}
    return view.index_result or {"notes": [], "lookup": {}, "backlinks": {}}



def build_indexes(view: ViewState, fresh: bool = False) -> dict[str, Any]:
    # ES load can take tens of seconds for a big view. Never hold view.lock
    # across that I/O — otherwise cold page renders stall behind prewarm
    # (kick_background_scan / today-warm need the lock briefly and used to wait
    # the full ES load). Coalesce with bg_scan_running so prewarm + a cold
    # request don't load the same 30k+ entity docs twice (GIL/memory thrash).
    if view.index_result is None:
        owned = False
        with view.lock:
            if view.index_result is not None:
                return view.index_result
            if view.bg_scan_running:
                pass
            else:
                view.bg_scan_running = True
                owned = True
        if not owned:
            deadline = time.time() + 180.0
            while view.index_result is None and view.bg_scan_running and time.time() < deadline:
                time.sleep(0.05)
            return view.index_result or {"notes": [], "lookup": {}, "backlinks": {}}
        built = None
        try:
            built = _load_index_snapshot(view)
        except Exception:
            logging.exception("argus_wiki entity index load failed view=%s", view.name)
        with view.lock:
            if view.index_result is None:
                if built is not None:
                    _apply_index_snapshot(view, built)
                else:
                    logging.warning(
                        "argus_wiki [%s]: no entity docs in ES — reindex needed; "
                        "serving an empty index (not scanning the share)", view.name)
                    view.index_result = {"notes": [], "lookup": {}, "backlinks": {}}
            result = view.index_result
            view.bg_scan_running = False
        if built is not None and result and result.get("notes"):
            logging.info("argus_wiki [%s]: loaded %d notes from ES entity index",
                         view.name, len(view.notes_by_rel))
        return result or {"notes": [], "lookup": {}, "backlinks": {}}
    # Reload (a cheap ES count check + re-query) only when the watcher has bumped
    # the index — never a vault scan.
    _maybe_reload_entity_index(view)
    return view.index_result or {"notes": [], "lookup": {}, "backlinks": {}}


def _maybe_reload_entity_index(view: ViewState) -> None:
    now = time.time()
    if (
        view.bg_scan_running
        or view.entity_freshness_check_running
        or now - view.entity_freshness_checked_at < 15.0
    ):
        return
    with view.lock:
        if view.bg_scan_running or view.entity_freshness_check_running:
            return
        view.entity_freshness_check_running = True
        view.entity_freshness_checked_at = now

    def run() -> None:
        try:
            from argus_wiki.index import elastic_search_index

            if elastic_search_index.enabled():
                token = elastic_search_index.entity_freshness_token(view.name)
            else:
                token = _local_entity_freshness_token(view)
            if token and token != view.entity_index_token:
                kick_background_reload(view)
        except Exception:
            logging.exception("argus_wiki entity freshness check failed view=%s", view.name)
        finally:
            with view.lock:
                view.entity_freshness_check_running = False

    threading.Thread(target=run, name=f"argus-wiki-freshness-{view.name}", daemon=True).start()


def kick_background_reload(view: ViewState) -> None:
    """Re-query the Delta entity index in the background (no vault scan)."""
    if view.bg_scan_running:
        return
    with view.lock:
        if view.bg_scan_running:
            return
        view.bg_scan_running = True
    logging.info("argus_wiki [%s]: entity index changed — reloading from Delta", view.name)

    def run() -> None:
        started = time.time()
        try:
            # Build the whole snapshot OFF-lock (the ~10s heavy part: Delta query +
            # rebuilding lookup/backlinks/search records for tens of thousands of
            # notes), then hold view.lock only for the atomic reference swap. This
            # keeps page renders — which take view.lock — from stalling behind a
            # reload; readers use the current index until the instant it swaps.
            built = _load_index_snapshot(view)
            if built is None:
                logging.info("argus_wiki [%s]: entity index reload skipped (empty/missing)", view.name)
                return
            with view.lock:
                _apply_index_snapshot(view, built)
            logging.info("argus_wiki [%s]: entity index reloaded — %d notes in %.2fs",
                         view.name, len(built["notes_by_rel"]), time.time() - started)
        except Exception:
            logging.exception("argus_wiki entity index reload crashed view=%s", view.name)
        finally:
            view.bg_scan_running = False

    threading.Thread(target=run, name=f"argus-wiki-reload-{view.name}", daemon=True).start()


def kick_background_scan(view: ViewState) -> None:
    if view.bg_scan_running:
        return
    with view.lock:
        if view.bg_scan_running:
            return
        view.bg_scan_scheduled = False
        view.bg_scan_running = True
    logging.info("argus_wiki [%s]: background refresh scan kicked", view.name)

    def run() -> None:
        started = time.time()
        try:
            result = scan_and_update(view)
            logging.info("argus_wiki background scan complete view=%s notes=%d elapsed=%.2fs",
                         view.name, len(result.get("notes") or []), time.time() - started)
        except OSError as exc:
            logging.warning("argus_wiki background scan failed view=%s: %s", view.name, exc)
        except Exception:
            logging.exception("argus_wiki background scan crashed view=%s", view.name)
        finally:
            view.bg_scan_running = False

    threading.Thread(target=run, name=f"argus-wiki-scan-{view.name}", daemon=True).start()


def schedule_background_scan(view: ViewState, delay_sec: float = 2.0) -> None:
    if view.index_result is not None or view.bg_scan_running or view.bg_scan_scheduled:
        return
    with view.lock:
        if view.index_result is not None or view.bg_scan_running or view.bg_scan_scheduled:
            return
        view.bg_scan_scheduled = True
    logging.info("argus_wiki scheduled background scan view=%s delay=%.1fs", view.name, delay_sec)

    def run() -> None:
        with view.lock:
            view.bg_scan_scheduled = False
        if view.index_result is None:
            kick_background_scan(view)

    timer = threading.Timer(delay_sec, run)
    timer.daemon = True
    timer.start()


def _frontmatter_list(fm: dict, *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        val = fm.get(key)
        if isinstance(val, list):
            out.extend(str(x).strip() for x in val if str(x).strip())
        elif val not in (None, ""):
            out.append(str(val).strip())
    return out


def _context_candidate_folders(target: str, context_note: Note | None) -> list[str]:
    clean = target.split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean or "/" in clean:
        return []
    label = Path(clean).stem.lower()
    folders: list[str] = []

    def add(folder: str) -> None:
        if folder and folder not in folders:
            folders.append(folder)

    if context_note and context_note.folder:
        add(context_note.folder)
    fm = context_note.frontmatter if context_note else {}
    field_map = [
        ("tickers", ("primary_ticker", "primary_tickers", "ticker", "tickers", "top_tickers")),
        ("companies", ("company", "companies", "top_companies")),
        ("senders", ("sender", "broker", "senders", "top_senders")),
        ("sectors", ("sector", "sectors", "top_sectors")),
        ("themes", ("theme", "themes", "top_themes")),
        ("authors", ("author", "authors")),
    ]
    for folder, keys in field_map:
        values = [v.lower() for v in _frontmatter_list(fm, *keys)]
        if label in values or any(entity_filename_slug(v).lower() == label for v in values):
            add(folder)
    return folders


def resolve_note(target: str, lookup: dict[str, Note], context_note: Note | None = None) -> Note | None:
    clean = target.split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean:
        return None
    if clean.lower().endswith(".md"):
        clean = clean[:-3]
    if "/" not in clean:
        for folder in _context_candidate_folders(clean, context_note):
            note = lookup.get(f"{folder}/{clean}".lower())
            if note:
                return note
    direct = lookup.get(clean.lower())
    if direct:
        return direct
    for folder in _known_dirs():
        note = lookup.get(f"{folder}/{clean}".lower())
        if note:
            return note
    return None


_DIR_STEMS_CACHE: dict[tuple, tuple[float, set[str]]] = {}
_DIR_STEMS_TTL_SEC = 30.0


def _view_dir_stems(view: ViewState, folder: str) -> set[str]:
    """Lower-cased ``.md`` stems in a view folder for link-existence checks.

    When the index is warm, entity-folder membership is already known in memory
    (``notes_by_rel``), so derive it from there — instant, and it never goes
    stale mid-render. An SMB directory listing of a big folder (companies has
    thousands of files) costs seconds and was previously re-run per unresolved
    link, turning link-heavy pages into multi-minute renders. Only the cold path
    (no index yet) or source folders (not in the index) fall back to listing.
    """
    if (
        view.index_result is not None
        and folder not in set(reader_contract.source_page_dirs())
    ):
        key = ("idx", view.name, folder, view.search_records_sig)
        cached = _DIR_STEMS_CACHE.get(key)
        if cached is not None:
            return cached[1]
        stems = {
            Path(rel).stem.lower()
            for rel in view.notes_by_rel
            if (rel.split("/", 1)[0] if "/" in rel else "") == folder
        }
        _DIR_STEMS_CACHE[key] = (0.0, stems)
        return stems
    key = (view.name, folder)
    from argus_wiki.index import reader_cache as rc

    cached_stems = rc.get_dir_stems(view.name, folder, disk=False)
    if cached_stems is not None:
        _DIR_STEMS_CACHE[key] = (time.time(), cached_stems)
        return cached_stems
    now = time.time()
    cached = _DIR_STEMS_CACHE.get(key)
    if cached is not None and now - cached[0] < _DIR_STEMS_TTL_SEC:
        return cached[1]
    directory = view.wiki_root / folder if folder else view.wiki_root
    try:
        stems = {p.stem.lower() for p in directory.iterdir() if p.suffix == ".md"}
    except OSError:
        stems = set()
    _DIR_STEMS_CACHE[key] = (now, stems)
    rc.put_dir_stems(view.name, folder, stems, persist=False)
    return stems


def _candidate_page_exists(view: ViewState, candidate: str) -> bool:
    rel = candidate.replace("\\", "/").strip("/")
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    folder, _, stem = rel.rpartition("/")
    if not stem:
        return False
    return stem.lower() in _view_dir_stems(view, folder)


def _load_explicit_link_note(view_name: str, target: str, context_note: Note | None = None) -> tuple[Note | None, str]:
    clean = target.split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean:
        return None, view_name
    if clean.lower().endswith(".md"):
        clean = clean[:-3]
    candidates = [clean]
    if "/" not in clean:
        # Bare single-token links are never source pages (real source links are
        # explicit bucketed paths, fast-pathed in convert_wikilinks). Excluding
        # source dirs here is critical: a `sources/<token>` candidate makes
        # _source_fallback_path glob the tens-of-thousands-of-files source tree,
        # which on a link-heavy page turns into minutes of SMB I/O per render.
        source_dirs = set(reader_contract.source_page_dirs())
        preferred = ["tickers", "companies", "senders", "authors", "sectors", "themes", "daily"]
        context_folders = [f for f in _context_candidate_folders(clean, context_note) if f not in source_dirs]
        preferred = [*context_folders, *(folder for folder in preferred if folder not in context_folders)]
        folders = [f for f in preferred if f in _known_dirs() and f not in source_dirs]
        folders.extend(sorted(_known_dirs() - set(folders) - source_dirs))
        candidates = [clean, *(f"{folder}/{clean}" for folder in folders)]
    try:
        view = get_view(view_name)
    except KeyError:
        return None, view_name
    is_default_view = view_name == _default_view_name()
    for candidate in candidates:
        page_rel_override = None
        # Cheap cached directory-listing check instead of a per-candidate SMB
        # stat() — this is the cold-resolution hot path on the network vault.
        if _candidate_page_exists(view, candidate):
            path = vault_path(view, candidate)
            link_view = view_name
        else:
            fallback_path, fallback_rel = _source_fallback_path(view, candidate)
            link_view = view_name
            # In the default view, _default_fallback_path resolves against the
            # same root just checked, so it is redundant; only named views need
            # the cross-view fallback to default.
            if fallback_path is None and not is_default_view:
                fallback_path, fallback_rel = _default_fallback_path(view, candidate)
                link_view = _default_view_name()
            if fallback_path is None:
                continue
            path = fallback_path
            page_rel_override = fallback_rel
        try:
            page_rel = page_rel_override or path.relative_to(get_view(link_view).wiki_root).as_posix()
        except (KeyError, ValueError):
            page_rel = candidate if candidate.lower().endswith(".md") else f"{candidate}.md"
        return ensure_note_fresh(get_view(link_view), path, page_rel), link_view
    author_note = _resolve_author_last_name_note(view, clean, context_note)
    if author_note:
        return author_note, view_name
    return None, view_name


def _norm_context_value(value: str) -> str:
    return entity_filename_slug(value).lower()


def _resolve_author_last_name_note(view: ViewState, clean: str, context_note: Note | None) -> Note | None:
    if not clean.lower().startswith("authors/"):
        return None
    stem = Path(clean).stem
    if not stem or "_" in stem or " " in stem:
        return None
    sender_values = {
        _norm_context_value(value)
        for value in _frontmatter_list(context_note.frontmatter if context_note else {}, "sender", "broker", "senders", "top_senders")
    }
    if not sender_values:
        return None
    root = view.wiki_root / "authors"
    try:
        paths = list(root.glob(f"*_{stem}.md"))
    except OSError:
        return None
    scored: list[tuple[int, Note]] = []
    for path in paths:
        try:
            rel = path.relative_to(view.wiki_root).as_posix()
        except ValueError:
            continue
        if is_sharepoint_conflict_path(rel):
            continue
        note = ensure_note_fresh(view, path, rel)
        note_senders = {
            _norm_context_value(value)
            for value in _frontmatter_list(note.frontmatter, "sender", "broker", "senders", "top_senders")
        }
        if not sender_values & note_senders:
            continue
        try:
            source_count = int(note.frontmatter.get("source_count") or 0)
        except (TypeError, ValueError):
            source_count = 0
        scored.append((source_count, note))
    if len(scored) != 1:
        return None
    return scored[0][1]


def html_link(href: str, label: str, css: str = "internal-link", title: str | None = None) -> str:
    title_attr = f' title="{html.escape(title)}"' if title else ""
    return f'<a class="{css}" href="{html.escape(href)}"{title_attr}>{html.escape(label)}</a>'


def _plain_preview_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("---", ">", "|")):
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\[\[([^]|]+)\|([^\]]+)\]\]", r"\2", line)
        line = re.sub(r"\[\[([^\]]+)\]\]", lambda m: m.group(1).rsplit("/", 1)[-1], line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"[*_`~]+", "", line).strip()
        if line:
            lines.append(line)
        if sum(len(x) for x in lines) >= 360:
            break
    preview = " ".join(lines)
    return preview[:360].rstrip() + ("..." if len(preview) > 360 else "")


def _load_preview_note(view_name: str, rel: str) -> Note | None:
    note, link_view = _load_explicit_link_note(view_name, rel)
    if not note:
        return None
    try:
        link_root = get_view(link_view).wiki_root
    except KeyError:
        link_root = None
    ticker_rel = _canonical_entity_redirect_rel(note.rel, note.frontmatter, link_root) or _singleton_redirect_rel(note.rel, note.frontmatter, link_root)
    if _cross_dim_redirect_suppressed(note.rel, ticker_rel, note.frontmatter):
        ticker_rel = None  # preview the entity itself, not its company
    if ticker_rel:
        redirect_note, _redirect_view = _load_explicit_link_note(link_view, ticker_rel)
        if redirect_note:
            return redirect_note
    return note


def preview_api(view_name: str, rel: str) -> tuple[dict[str, Any], int]:
    view = get_view(view_name)
    if not is_ready(view):
        return {"error": "view is building"}, 503
    clean = unquote(rel or "").split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean or ".." in clean.split("/"):
        return {"error": "invalid rel"}, 400
    from argus_wiki.index import reader_cache as rc

    cached = rc.kv_get_mem(rc.NS_PREVIEW, clean, view_name)
    if cached:
        try:
            data = json.loads(cached)
            if isinstance(data, dict) and data.get("rel"):
                return data, 200
        except json.JSONDecodeError:
            pass
    note = _load_preview_note(view_name, clean)
    if not note:
        return {"error": "not found"}, 404
    meta = str(note.frontmatter.get("type") or note.folder or "").replace("_", " ").title()
    body = {
        "title": note.title,
        "meta": meta,
        "rel": _visible_rel(note.rel) or note.rel,
        "preview": _plain_preview_text(note.text),
        "url": note.route,
    }
    rc.kv_put_async(rc.NS_PREVIEW, clean, json.dumps(body), view_name)
    return body, 200


def _source_link_target(target: str) -> str | None:
    return reader_contract.resolve_source_wikilink_target(target)


def _entity_identifier_target(target: str) -> str | None:
    return reader_contract.entity_identifier_to_rel(target)


def convert_wikilinks(
    view_name: str,
    text: str,
    lookup: dict[str, Note],
    context_note: Note | None = None,
) -> str:
    started = time.perf_counter()
    text = markdown_links.sanitize_nested_wikilinks(text)
    sanitized_at = time.perf_counter()
    _prefetch_source_titles(text)
    prefetched_at = time.perf_counter()
    identifier_rel_map = reader_contract.entity_identifier_rel_map()
    identifier_page_dirs = {page_dir for page_dir, _key in identifier_rel_map if page_dir}
    identifiers_at = time.perf_counter()

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        target, label = raw, raw
        if "|" in raw:
            target, label = [x.strip() for x in raw.split("|", 1)]
        anchor = ""
        if "#" in target:
            anchor_part = target.split("#", 1)[1].strip()
            if anchor_part:
                anchor = "#" + re.sub(r"[^\w\-]+", "-", anchor_part.lower()).strip("-")
        reversed_source_rel = _source_link_target(label)
        # Reversed Obsidian form: [[Readable title|sources/.../src_xxx]]. Bare
        # [[sources/...]] has label == target and must not take this branch.
        if reversed_source_rel and label != target:
            return html_link(route_for(view_name, reversed_source_rel) + anchor, target, title=target)
        clean_identifier_target = target.split("#", 1)[0].replace("\\", "/").strip("/")
        explicit_path = "/" in clean_identifier_target
        # Only a BARE identifier ([[ANTH]]) resolves via the identifier map. An
        # explicit [[tickers/ANTH]] names its own dimension and must route there
        # (resolve_note + the known-dir branch below handle it; canonicalization, if
        # any, happens at serve time). Previously an explicit path whose head was a
        # page_dir ALSO went through the map, so a private-code redirect (ANTH ->
        # companies/Anthropic, target_dimension=companies) hijacked the ticker link
        # back to the company page -- the ".ANTH" Identity link then pointed at the
        # page you were already on ("clicking does nothing").
        if not explicit_path:
            identifier_key = entity_identifiers.normalize_identifier(clean_identifier_target)
            entity_rel = identifier_rel_map.get(("", identifier_key))
            if entity_rel:
                return html_link(route_for(view_name, entity_rel) + anchor, label, title=label)
        note = resolve_note(target, lookup, context_note)
        if note:
            if label == target and "/" in target:
                label = note.title or target.rsplit("/", 1)[-1]
            return html_link(note.route + anchor, label, title=note.title)
        clean_target = target.split("#", 1)[0].replace("\\", "/").strip("/")
        first_dir = clean_target.split("/", 1)[0] if "/" in clean_target else ""
        source_dirs = set(reader_contract.source_page_dirs())
        source_parts = [part for part in clean_target.split("/") if part]
        if (
            "/" in clean_target
            and first_dir in _known_dirs()
            and first_dir != "authors"
            # Any path under a source dir (bucketed OR flat `sources/src_xxx`)
            # routes directly. Flat links used to fall through to a whole-tree
            # glob of every date bucket *per link, per render* — minutes on big
            # pages. The bucketed file is still recovered (once) at serve time if
            # someone actually opens a flat link.
            and (first_dir not in source_dirs or len(source_parts) >= 2)
        ):
            link_target = clean_target[:-3] if clean_target.lower().endswith(".md") else clean_target
            if label == target:
                leaf = _visible_rel(link_target).rsplit("/", 1)[-1] or link_target.rsplit("/", 1)[-1]
                if first_dir in source_dirs:
                    label = _source_display_title(link_target, bulk=True) or leaf
                else:
                    label = leaf
            # Pages for shared dims (and any dim not materialized in this view)
            # live in the default view; route there so named-view links don't
            # 404 against a page that only exists under default.
            default_view = _default_view_name()
            target_view = (
                default_view
                if view_name != default_view and first_dir in _default_fallback_page_dirs()
                else view_name
            )
            return html_link(route_for(target_view, _visible_rel(link_target) or link_target) + anchor, label, title=label)
        note, link_view = _resolve_wikilink_note(view_name, target, lookup, context_note)
        if note:
            if label == target and "/" in target:
                label = note.title or target.rsplit("/", 1)[-1]
            href = note.route if link_view == view_name else route_for(link_view, _visible_rel(note.rel) or note.rel)
            return html_link(href + anchor, label, title=note.title)
        source_rel = _source_link_target(target)
        if source_rel:
            display = label
            rawish = (
                display == target
                or display.startswith("src::")
                or display.startswith("src_")
                or Path(display).stem.startswith("src_")
            )
            if rawish:
                src_note = resolve_note(source_rel, lookup, context_note)
                if src_note and src_note.title and not str(src_note.title).startswith("src_"):
                    display = src_note.title
                else:
                    # Render hot path: bulk=True -> cache-only, never a per-link
                    # ES/SMB read. This is the branch link-heavy ticker pages take
                    # for their [[src::...]] citations; without it a single ticker
                    # render is 60-80s and hangs the server (see _source_display_title).
                    display = _source_display_title(source_rel, bulk=True) or display
            return html_link(route_for(view_name, source_rel) + anchor, display, title=display)
        return f'<span class="missing-link" title="No note yet">{html.escape(label)}</span>'

    rendered = WIKILINK_RE.sub(replace, text)
    finished = time.perf_counter()
    if finished - started >= 0.5:
        logging.info(
            "argus_wiki wikilinks slow total=%.2fs sanitize=%.2fs source_titles=%.2fs identifiers=%.2fs replace=%.2fs links=%d",
            finished - started,
            sanitized_at - started,
            prefetched_at - sanitized_at,
            identifiers_at - prefetched_at,
            finished - identifiers_at,
            len(WIKILINK_RE.findall(text)),
        )
    return rendered


def _raw_path_key(path: str) -> str | None:
    return raw_paths.raw_path_key_resolved(path, config.RAW_ROOT)


_MANIFEST_RAW_CACHE: dict[str, dict] | None = None
_MANIFEST_RAW_CACHE_AT = 0.0


def _manifest_by_raw_key(force: bool = False) -> dict[str, dict]:
    # Built directly from the ingested manifest (the source of truth), memoized
    # in-process for 30s. There is deliberately no reader_cache/Delta layer here:
    # the manifest maps every raw_path and is cheap to re-read, whereas caching it
    # as a single Delta value produced a multi-hundred-MB blob that (a) was read
    # off the SMB share on the raw-open hot path and (b) took down every raw link
    # when a fragment corrupted. Keep this file-backed.
    global _MANIFEST_RAW_CACHE, _MANIFEST_RAW_CACHE_AT
    now = time.time()
    if not force and _MANIFEST_RAW_CACHE is not None and now - _MANIFEST_RAW_CACHE_AT < 30:
        return _MANIFEST_RAW_CACHE
    out: dict[str, dict] = {}
    manifest = config.INGESTED_MANIFEST
    if manifest.exists():
        try:
            lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logging.warning("argus_wiki manifest raw-key lookup skipped: %s", exc)
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("raw_path") or "").strip()
            if not key:
                continue
            out[key] = row
            bare = key.split("/", 1)[-1] if "/" in key else key
            if bare not in out:
                out[bare] = row
    _MANIFEST_RAW_CACHE = out
    _MANIFEST_RAW_CACHE_AT = now
    return out


def _source_page_frontmatter(current_rel: str) -> dict:
    clean = current_rel.replace("\\", "/")
    if not clean.startswith("sources/"):
        return {}
    for root in (config.WIKI_ROOT, reader_contract.default_view_wiki_root()):
        path = root / clean
        if not path.is_file():
            continue
        m = FRONTMATTER_RE.match(path.read_text(encoding="utf-8", errors="replace"))
        if not m:
            return {}
        try:
            return yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return {}
    return {}


# v2 HuggingNews feed layout + semantic search live in feed_view.py.
from dash_reports.sub_servers.argus_wiki_server import feed_view

_FEED_CSS = feed_view.FEED_CSS + feed_view.SOURCE_CITE_CSS
_FEED_JS = feed_view.FEED_JS + feed_view.SOURCE_CITE_JS
_feed_source_data_script = feed_view.feed_source_data_script
feed_semantic_search = feed_view.feed_semantic_search


def _raw_open_route(key: str, ingest_source: str | None = None) -> str:
    query = f"path={quote(key, safe='/')}"
    if ingest_source:
        query += f"&ingest_source={quote(str(ingest_source), safe='')}"
    return f"{_raw_route('open')}?{query}"


def _raw_file_route(key: str, ingest_source: str | None = None) -> str:
    query = f"path={quote(key, safe='/')}"
    if ingest_source:
        query += f"&ingest_source={quote(str(ingest_source), safe='')}"
    return f"{_raw_route('')}?{query}"


def raw_route_from_file_url(href: str) -> str:
    parsed = urlparse(href)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return f"{_raw_route('open')}?path={quote(_raw_path_key(path) or path)}"


def raw_route_from_wiki_href(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    path = (query.get("path") or [""])[0]
    if not path:
        return href
    return _raw_open_route(_raw_path_key(path) or path, (query.get('ingest_source') or [None])[0])


def raw_route_from_relative_href(href: str, current_rel: str) -> str:
    fm = _source_page_frontmatter(current_rel)
    raw_key = str(fm.get("raw_path") or "").strip()
    if raw_key:
        return _raw_open_route(raw_key, fm.get("ingest_source"))
    target, fragment = href, ""
    if "#" in target:
        target, fragment = target.split("#", 1)
        fragment = f"#{fragment}"
    cleaned = unquote(target).replace("\\", "/")
    portable = raw_paths.portable_raw_key_from_text(cleaned)
    if portable:
        key = portable
    else:
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(current_rel), cleaned))
        parts = [part for part in rel.split("/") if part and part != "."]
        while parts and parts[0] == "..":
            parts.pop(0)
        key = "/".join(parts)
    if not key:
        return href
    row = _manifest_by_raw_key().get(key)
    ingest_source = (row or {}).get("ingest_source")
    if row and row.get("raw_path"):
        key = str(row["raw_path"])
    return _raw_open_route(_raw_path_key(key) or key, ingest_source) + fragment


def _raw_prefix() -> str:
    return _raw_route("").rstrip("/")


def rewrite_href(view: str, href: str, current_rel: str, lookup: dict[str, Note] | None = None) -> str:
    href = href.strip()
    lower = href.lower()
    parsed = urlparse(href)
    public_host = urlparse((dimension_registry.load_registry().get('routes') or {}).get('public_base_url', '')).netloc
    if (not parsed.netloc or parsed.netloc == public_host) and parsed.path in {f'{URL_PATH}/raw/open', '/wiki/raw/open'}:
        return raw_route_from_wiki_href(href)
    if lower.startswith(("http://", "https://", "mailto:", "#")):
        return href
    if lower.startswith("file:///"):
        return raw_route_from_file_url(href)
    raw_prefix = _raw_prefix().lower()
    if lower.startswith(f"{raw_prefix}/"):
        return raw_route_from_wiki_href(href)
    target, fragment = href, ""
    if "#" in target:
        target, fragment = target.split("#", 1)
        fragment = f"#{fragment}"
    cleaned = unquote(target).replace("\\", "/")
    if cleaned.lower().endswith(".md"):
        vault_rel = posixpath.normpath(posixpath.join(posixpath.dirname(current_rel), cleaned))
        while vault_rel.startswith("../"):
            vault_rel = vault_rel[3:]
        stripped = _strip_leading_vault_wiki_prefix(vault_rel)
        if stripped != vault_rel:
            vault_rel = stripped
            sources = set(reader_contract.source_page_dirs())
            head = vault_rel.split("/", 1)[0] if "/" in vault_rel else vault_rel
            if head in sources:
                return f"{route_for(view, vault_rel)}{fragment}"
            return f"{_route_internal_md(view, vault_rel)}{fragment}"
        parts = cleaned.split("/", 1)
        known = _known_dirs()
        rel = cleaned if parts[0] in known else posixpath.normpath(posixpath.join(posixpath.dirname(current_rel), cleaned))
        rel = _recover_source_relative_page_rel(rel)
        if lookup:
            note = resolve_note(rel, lookup)
            if note:
                return f"{note.route}{fragment}"
        return f"{_route_internal_md(view, rel)}{fragment}"
    if Path(cleaned).suffix.lower() in _asset_suffixes():
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(current_rel), cleaned))
        while rel.startswith("../"):
            rel = rel[3:]
        rel = _strip_leading_vault_wiki_prefix(rel)
        return f"{_asset_route(view, _visible_rel(rel).lstrip('/'))}{fragment}"
    if Path(cleaned).suffix.lower() in _raw_suffixes():
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(current_rel), cleaned))
        while rel.startswith("../"):
            rel = rel[3:]
        rel = _strip_leading_vault_wiki_prefix(rel)
        if wiki_content_path_or_none(view, rel) is not None:
            return f"{_content_route(view, _visible_rel(rel).lstrip('/'))}{fragment}"
        return raw_route_from_relative_href(href, current_rel) + fragment
    clean_rel = current_rel.replace("\\", "/")
    if clean_rel.startswith("sources/") and not cleaned.lower().endswith(".md"):
        if Path(cleaned).suffix.lower() not in _asset_suffixes():
            fm = _source_page_frontmatter(current_rel)
            if str(fm.get("raw_path") or "").strip():
                return raw_route_from_relative_href(href, current_rel) + fragment
    return href


def convert_markdown_links(view: str, text: str, current_rel: str, lookup: dict[str, Note] | None = None) -> str:
    return MD_LINK_RE.sub(lambda match: f"]({rewrite_href(view, match.group(1), current_rel, lookup)})", text)


CALLOUT_BLOCK_RE = re.compile(
    r"(?m)^>[ \t]*\[!(?P<kind>[A-Za-z]+)\](?P<fold>[-+]?)[ \t]*(?P<title>[^\r\n]*)\r?\n"
    r"(?P<body>(?:>[^\n]*(?:\n|$))*)"
)


def convert_callouts(text: str) -> str:
    def repl(match: re.Match) -> str:
        kind = match.group("kind").lower()
        fold = match.group("fold")
        title = match.group("title").strip() or kind.title()
        body = "\n".join(re.sub(r"^>\s?", "", ln) for ln in match.group("body").splitlines())
        open_attr = " open" if fold == "+" else ""
        css = f"callout callout-{kind}"
        return (
            f'\n<details class="{css}" markdown="1"{open_attr}>\n'
            f'<summary>{html.escape(title)}</summary>\n\n{body}\n\n</details>\n'
        )
    return CALLOUT_BLOCK_RE.sub(repl, text)


HIGHLIGHT_RE = re.compile(r"==([^=\n][^=\n]*?)==")



def convert_highlights(text: str) -> str:
    return HIGHLIGHT_RE.sub(r"**\1**", text)


_TAB_PAIRS: tuple[tuple[str, str], ...] = (("Charts", "Tables"), ("Key Charts", "Key Tables"))


def _tabify_sections(html: str) -> str:
    """Web-reader only: render an adjacent ``## A`` / ``## B`` heading pair
    (Charts/Tables, Key Charts/Key Tables) as CSS tabs. The vault markdown stays
    plain ``## `` sections (Obsidian shows both), so this just restyles the
    rendered HTML — no JS, fail-open (leaves the HTML as-is if either heading is
    missing)."""
    for idx, (a, b) in enumerate(_TAB_PAIRS):
        ra = re.compile(rf"<h2[^>]*>\s*{re.escape(a)}\s*</h2>(.*?)(?=<h2|\Z)", re.DOTALL)
        rb = re.compile(rf"<h2[^>]*>\s*{re.escape(b)}\s*</h2>(.*?)(?=<h2|\Z)", re.DOTALL)
        ma, mb = ra.search(html), rb.search(html)
        if not (ma and mb) or ma.start() >= mb.start():
            continue
        uid = f"ct{idx}"
        tabs = (
            f'<div class="ctabs">'
            f'<input type="radio" name="{uid}" id="{uid}a" class="ctab-r ctab-ra" checked>'
            f'<label for="{uid}a" class="ctab-l ctab-la">{a}</label>'
            f'<input type="radio" name="{uid}" id="{uid}b" class="ctab-r ctab-rb">'
            f'<label for="{uid}b" class="ctab-l ctab-lb">{b}</label>'
            f'<div class="ctab-p ctab-pa">{ma.group(1)}</div>'
            f'<div class="ctab-p ctab-pb">{mb.group(1)}</div>'
            f"</div>"
        )
        # A precedes B and each block ends at the next <h2 (B's), so ma.end()
        # == mb.start(): drop both original blocks, drop tabs in at A's start.
        html = html[:ma.start()] + tabs + html[mb.end():]
    return html


def render_markdown(view: ViewState, text: str, current_rel: str, lookup: dict[str, Note]) -> tuple[str, list[tuple[int, str, str]]]:
    started = time.time()
    timings: list[tuple[str, float]] = []

    def mark(label: str) -> None:
        timings.append((label, time.time() - started))

    current_note = lookup.get(Path(current_rel).with_suffix("").as_posix().lower())
    if current_note is None and reader_contract.is_source_rel(current_rel):
        # Source pages aren't in the in-memory index, so links in a source body
        # (e.g. an ambiguous author last-name) would lose the source's own
        # frontmatter context. Read it on-demand to keep that resolution intact.
        try:
            current_note = read_note_summary(
                view, vault_path(view, current_rel),
                rel_override=current_rel.replace("\\", "/"),
            )
        except (OSError, ValueError):
            current_note = None
    body = convert_wikilinks(view.name, text, lookup, current_note)
    mark("wikilinks")
    body = convert_markdown_links(view.name, body, current_rel, lookup)
    mark("mdlinks")
    body = convert_callouts(body)
    mark("callouts")
    body = convert_highlights(body)
    mark("highlights")
    rendered = markdown.markdown(body, extensions=["extra", "sane_lists", "toc"])
    rendered = _tabify_sections(rendered)
    mark("markdown")
    toc = [(int(level), anchor, re.sub(r"<[^>]+>", "", title).strip()) for level, anchor, title in TOC_RE.findall(rendered)]
    elapsed = time.time() - started
    if elapsed > 1.0:
        logging.info("argus_wiki markdown render slow rel=%s elapsed=%.2fs timings=%s",
                     current_rel, elapsed, timings)
    return rendered, toc


def render_cached(view: ViewState, note: Note, lookup: dict[str, Note]) -> tuple[str, list[tuple[int, str, str]]]:
    cached = view.render_cache.get(note.rel)
    if cached and cached[0] == note.mtime:
        return cached[1], cached[2]
    # Single-flight: only ONE render of a given rel runs at a time; concurrent
    # (or client-abandoned-but-still-running) requests wait and reuse its result
    # rather than each launching a CPU-bound render_markdown and starving the GIL.
    # Different rels still render in parallel (per-rel lock, not the view lock).
    with view.render_locks_guard:
        rlock = view.render_locks.setdefault(note.rel, threading.Lock())
    with rlock:
        cached = view.render_cache.get(note.rel)
        if cached and cached[0] == note.mtime:
            return cached[1], cached[2]
        rendered, toc = render_markdown(view, note.text, note.rel, lookup)
        view.render_cache[note.rel] = (note.mtime, rendered, toc)
        return rendered, toc


def _single_note_index(note: Note) -> dict[str, Any]:
    stem = Path(note.rel).with_suffix("").as_posix()
    lookup = {
        stem.lower(): note,
        Path(note.rel).stem.lower(): note,
        note.title.lower(): note,
    }
    visible_stem = _visible_rel(stem)
    if visible_stem:
        lookup[visible_stem.lower()] = note
    return {"notes": [note], "lookup": lookup, "backlinks": {}}


def _backlink_keys_for_text(text: str) -> set[str]:
    keys: set[str] = set()
    for match in WIKILINK_TARGET_RE.finditer(text):
        target = match.group(1).strip().split("#", 1)[0].split("/")[-1].lower()
        if target:
            keys.add(target)
    for match in MD_LINK_TARGET_RE.finditer(text):
        href = match.group(1).strip()
        if href.startswith(("http", "mailto:", "#", "file:")):
            continue
        stem = Path(unquote(href).split("#", 1)[0]).stem.lower()
        if stem:
            keys.add(stem)
    return keys


def _indexes_for_page_render(view: ViewState, note: Note) -> dict[str, Any]:
    ready = bool(view.index_result and view.index_result.get("notes"))
    if ready:
        indexes = build_indexes(view)
        return indexes if indexes.get("notes") else _single_note_index(note)

    return _single_note_index(note)


def ensure_note_fresh(view: ViewState, path: Path, page_rel: str) -> Note:
    try:
        disk_mtime = path.stat().st_mtime
    except OSError as exc:
        logging.warning("argus_wiki ensure_note_fresh stat failed path=%r: %s", path, exc)
        return view.notes_by_rel.get(page_rel) or read_note(view, path, rel_override=page_rel)
    cached = view.notes_by_rel.get(page_rel)
    # A metadata-only note (loaded from the Delta entity index) has no body, so
    # always read the full file to render it — even if the mtime matches.
    if cached is not None and cached.mtime == disk_mtime and cached.body_loaded:
        return cached
    note = read_note(view, path, rel_override=page_rel)
    view.notes_by_rel[page_rel] = note
    view.render_cache.pop(page_rel, None)
    view.page_cache.pop(page_rel, None)
    return note


def folder_notes_direct(view: ViewState, folder: str) -> list[Note]:
    clean = folder.replace("\\", "/").strip("/")
    if not clean or ".." in clean.split("/"):
        return []
    root = view.wiki_root / clean
    try:
        if not root.exists() or not root.is_dir():
            return []
    except OSError:
        return []
    try:
        paths = list(root.glob("*.md"))
        if not paths and clean in set(reader_contract.source_page_dirs()):
            paths = list(root.rglob("*.md"))
    except OSError:
        return []
    notes: list[Note] = []
    for path in paths:
        try:
            rel = path.relative_to(view.wiki_root).as_posix()
            if is_sharepoint_conflict_path(rel):
                continue
            note = read_note_stub(view, path, rel)
            notes.append(note)
        except (OSError, ValueError):
            continue
    return notes


def _is_listable_entity_rel(rel: str) -> bool:
    """Shared canonical entity-page filter (reader_contract) so the web reader's
    folder listing + sidebar counts show the SAME entity set as list_entities
    (MCP/genie) and the watcher's index self-heal."""
    return reader_contract.is_listable_entity_rel(rel)


def _entity_folder_entries(view: ViewState, root: Path, clean: str) -> list[tuple[str, Path]]:
    """Sorted listable (rel, path) entries for an entity/daily folder.

    Prefer the warm ES entity index (``notes_by_rel``) for speed, then merge any
    disk-only ``.md`` names so a brand-new entity page is never invisible while
    ES projection catches up. Cache keyed by (manifest, index sig, dir mtime).
    """
    try:
        dir_mtime = root.stat().st_mtime
    except OSError:
        dir_mtime = 0.0
    sig = _manifest_sig()
    index_sig = view.search_records_sig if view.index_result is not None else -1
    cache_sig = (sig, index_sig, dir_mtime)
    now = time.time()
    cached = view.folder_entries_cache.get(clean)
    if cached and cached[1] == cache_sig and (now - cached[0]) < _FOLDER_ENTRIES_TTL_SEC:
        return cached[2]

    entries: list[tuple[str, Path]] = []
    if view.index_result is not None and view.notes_by_rel:
        prefix = f"{clean}/"
        for rel in view.notes_by_rel:
            if not rel.startswith(prefix):
                continue
            if "/" in rel[len(prefix):]:
                continue
            if not _is_listable_entity_rel(rel):
                continue
            entries.append((rel, view.wiki_root / rel))
    else:
        for path in root.glob("*.md"):
            try:
                rel = path.relative_to(view.wiki_root).as_posix()
            except ValueError:
                continue
            if not _is_listable_entity_rel(rel):
                continue
            entries.append((rel, path))
    entries.sort(key=lambda rp: rp[0].rsplit("/", 1)[-1].lower())
    view.folder_entries_cache[clean] = (now, cache_sig, entries)
    if view.index_result is not None and view.notes_by_rel and clean not in view.folder_reconcile_running:
        with view.lock:
            if clean in view.folder_reconcile_running:
                return entries
            view.folder_reconcile_running.add(clean)

        def reconcile() -> None:
            try:
                merged: list[tuple[str, Path]] = []
                for name in os.listdir(root):
                    lower = name.lower()
                    if not lower.endswith(".md"):
                        continue
                    rel = f"{clean}/{name}".replace("\\", "/")
                    if _is_listable_entity_rel(rel):
                        merged.append((rel, root / name))
                merged.sort(key=lambda item: item[0].rsplit("/", 1)[-1].lower())
                with view.lock:
                    current = view.folder_entries_cache.get(clean)
                    if current and current[1] == cache_sig:
                        view.folder_entries_cache[clean] = (time.time(), cache_sig, merged)
            except OSError:
                pass
            finally:
                with view.lock:
                    view.folder_reconcile_running.discard(clean)

        threading.Thread(target=reconcile, name=f"argus-wiki-folder-{view.name}-{clean}", daemon=True).start()
    return entries


def folder_notes_direct_page(view: ViewState, folder: str, page: str | int | None, page_size: int) -> tuple[list[Note], int, bool]:
    clean = folder.replace("\\", "/").strip("/")
    if not clean or ".." in clean.split("/"):
        return [], 1, False
    root = view.wiki_root / clean
    try:
        if not root.exists() or not root.is_dir():
            return [], 1, False
    except OSError:
        return [], 1, False
    current = _page_number(page)
    start = (current - 1) * page_size
    stop = start + page_size + 1
    source_dirs = set(reader_contract.source_page_dirs())
    top = clean.split("/", 1)[0]
    # Top-level source dir → recurse into date buckets; a bucket (or any entity
    # folder) → list its direct children.
    is_source = top in source_dirs
    src_key = (clean, current, page_size)
    src_sig: tuple[float, float] = (0.0, 0.0)
    if is_source:
        # Invalidate on manifest change (bucketed sources arrive via ingest) OR the
        # folder's own mtime (a file added/removed directly), so a new source shows
        # up immediately while an unchanged folder skips the SMB rglob.
        try:
            dir_mtime = root.stat().st_mtime
        except OSError:
            dir_mtime = 0.0
        src_sig = (_manifest_sig(), dir_mtime)
        c = view.source_folder_page_cache.get(src_key)
        if c and c[1] == src_sig and (time.time() - c[0]) < _SOURCE_FOLDER_TTL_SEC:
            return c[2]
    visible: list[tuple[str, Path]] = []
    try:
        if is_source:
            # Sources: bucketed and huge — stream the glob and stop early once
            # we have this page's slice (order = filesystem order).
            paths = root.rglob("*.md") if clean in source_dirs else root.glob("*.md")
            seen = 0
            for path in paths:
                try:
                    rel = path.relative_to(view.wiki_root).as_posix()
                except ValueError:
                    continue
                if is_sharepoint_conflict_path(rel):
                    continue
                if seen >= stop:
                    break
                if seen >= start:
                    visible.append((rel, path))
                seen += 1
        else:
            # Entity/daily folders: materialize + sort for STABLE pagination and
            # apply the shared listable filter so the page shows exactly the real
            # entities (no overview/.sources/conflict pages) — parity with
            # list_entities and Obsidian. Names only (no reads); cheap even for
            # the 4.7k-ticker folder. Cached per (folder, manifest sig) so the SMB
            # directory glob + sort isn't redone on every page view / pagination.
            entries = _entity_folder_entries(view, root, clean)
            allowed = _folder_index_values(clean)
            if allowed is not None:
                entries = [
                    entry for entry in entries
                    if Path(entry[0]).stem.casefold() in allowed
                ]
            visible = entries[start:stop]
    except OSError:
        return [], current, False

    notes: list[Note] = []
    if not is_source:
        # Prefer warm index notes (real titles) over filename stubs. Never
        # re-read entity pages from SMB just to draw a folder card.
        for rel, path in visible:
            note = view.notes_by_rel.get(rel)
            notes.append(note if note is not None else read_note_stub(view, path, rel))
    elif visible:
        # Source folders are never in the index, so read this page's summaries
        # for real titles. Only page_size+1 files, modest concurrency so we don't
        # exceed the share's read budget alongside a running scan.
        def _read(item: tuple[str, Path]) -> Note | None:
            rel, path = item
            try:
                return read_note_summary(view, path, rel)
            except OSError:
                try:
                    return read_note_stub(view, path, rel)
                except OSError:
                    return None

        workers = max(2, min(4, _SCAN_READ_WORKERS))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wiki-folder") as ex:
            notes = [note for note in ex.map(_read, visible) if note is not None]
    has_next = len(visible) > page_size
    result = (notes[:page_size], current, has_next)
    if is_source:
        view.source_folder_page_cache[src_key] = (time.time(), src_sig, result)
    return result


def format_note_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def page_mtime_line(note: Note) -> str:
    return f'<div class="note-mtime muted small">Modified {html.escape(format_note_mtime(note.mtime))}</div>'


def format_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.1f}M"


def sidebar_groups(notes: list[Note]) -> dict[str, list[Note]]:
    groups: dict[str, list[Note]] = {}
    for note in notes:
        groups.setdefault(note.folder or "root", []).append(note)
    return groups


def sidebar_counts(view_name: str) -> tuple[dict[str, Any], int]:
    view = get_view(view_name)
    indexes = view.index_result
    counts = sidebar_counts_direct(view)
    if indexes is None:
        kick_background_scan(view)
        from argus_wiki.index import reader_cache as rc

        rc.kick_refresh()
        return {"status": "warming", "counts": counts}, 202
    if not counts:
        from argus_wiki.index import reader_cache as rc

        rc.kick_refresh()
        return {"status": "warming", "counts": counts}, 202
    return {"status": "ready", "counts": counts}, 200


def _entity_dim_folder_counts(view: ViewState) -> dict[str, int]:
    """Listable-entity count per structured-nav folder.

    Prefer the warm in-memory index (``view.notes_by_rel``): one pass, no SMB.
    Globbing each entity folder over the share (companies/tickers hold thousands
    of files) cost seconds AND stole the GIL from live renders when the refresher
    ran -- mirrors the same index-first rule as ``_entity_folder_entries``. Only
    the cold path (index not built yet) falls back to a disk glob.
    """
    counts: dict[str, int] = {}
    seen: set[str] = set()
    targets: list[str] = []
    source_dirs = set(reader_contract.source_page_dirs())
    for item in reader_contract.structured_nav():
        if item.kind != dimension_registry._VIEW_KIND_FOLDER or item.target in seen:
            continue
        seen.add(item.target)
        if item.target == PUBLIC_PAGE_DIR or item.target in source_dirs:
            continue
        targets.append(item.target)

    if view.index_result is not None and view.notes_by_rel:
        target_set = set(targets)
        for rel in view.notes_by_rel:
            head = rel.split("/", 1)[0] if "/" in rel else ""
            if head not in target_set:
                continue
            if "/" in rel[len(head) + 1:]:  # top-level entity page only (matches glob('*.md'))
                continue
            if not _is_listable_entity_rel(rel):
                continue
            counts[head] = counts.get(head, 0) + 1
        return counts

    # Cold path: index not built yet -> disk glob fallback (one-time).
    for target in targets:
        root = view.wiki_root / target
        try:
            if root.exists() and root.is_dir():
                counts[target] = sum(
                    1 for p in root.glob("*.md")
                    if _is_listable_entity_rel(p.relative_to(view.wiki_root).as_posix())
                )
        except OSError:
            continue
    return counts


def _source_dir_counts() -> dict[str, int]:
    """Sidebar counts for source folders, from the ES source index.

    Source pages are excluded from the in-memory note index and are bucketed
    under ``<source_dir>/<date>/``, so neither note-grouping nor a top-level
    ``glob`` can count them. Elasticsearch is the source of truth.
    Best-effort: returns {} if ES has no source docs yet.
    """
    try:
        from argus_wiki.index import elastic_search_index

        store = elastic_search_index.store()
        if not store.exists():
            return {}
        total = store.count(kind="source")
    except Exception:
        return {}
    if total <= 0:
        return {}
    return {d: total for d in reader_contract.source_page_dirs()}


def sidebar_counts_direct(view: ViewState) -> dict[str, int]:
    """Folder badge counts — read-only from reader_cache (never SMB on request path)."""
    from argus_wiki.index import reader_cache as rc

    cached = rc.get_sidebar_counts(view.name, disk=False)
    return dict(cached) if cached else {}


def _compute_sidebar_counts(view: ViewState, *, include_public: bool = True) -> dict[str, int]:
    counts = _entity_dim_folder_counts(view)
    if include_public:
        counts.update(_public_sidebar_counts(view))
    counts.update(_source_dir_counts())
    return counts


def _public_sidebar_counts(view: ViewState) -> dict[str, int]:
    location = _folder_location(view, PUBLIC_PAGE_DIR)
    if location is None:
        return {}
    root, storage_clean = location
    counts: dict[str, int] = {}
    for path in _direct_child_dirs(root):
        rel = f"{storage_clean.rstrip('/')}/{path.name}".strip("/")
        counts[_visible_rel(rel)] = _public_nav_count(path)
    return counts


def _structured_nav_entries(view: str, groups: dict[str, list[Note]]) -> list[str]:
    nav_items: list[str] = []
    has_trending = False
    for item in reader_contract.structured_nav():
        if item.kind == dimension_registry._VIEW_KIND_PAGE:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FOLDER:
            if item.target == PUBLIC_PAGE_DIR:
                nav_items.extend(_public_nav_entries(view))
                continue
            count = len(groups.get(item.target, []))
            label = format_count(count) if count else ""
            badge = f' <span class="count" data-sidebar-count="{html.escape(item.target)}">{label}</span>'
            nav_items.append(html_link(_folder_route(view, item.target), item.label, "nav-link") + badge)
        elif item.kind == dimension_registry._VIEW_KIND_OVERVIEW:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FEED:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_TRENDING:
            nav_items.append(html_link(_trending_route(view), item.label, "nav-link"))
            has_trending = True
    if not has_trending:
        nav_items.append(html_link(_trending_route(view), "Trending", "nav-link"))
    return nav_items


def _structured_nav_entries_light(view: str, groups: dict[str, list[Note]]) -> list[str]:
    """Nav without public SMB rglob — for background cache refresh only."""
    nav_items: list[str] = []
    has_trending = False
    for item in reader_contract.structured_nav():
        if item.kind == dimension_registry._VIEW_KIND_PAGE:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FOLDER:
            if item.target == PUBLIC_PAGE_DIR:
                nav_items.append(html_link(_folder_route(view, item.target), item.label, "nav-link"))
                continue
            count = len(groups.get(item.target, []))
            label = format_count(count) if count else ""
            badge = f' <span class="count" data-sidebar-count="{html.escape(item.target)}">{label}</span>'
            nav_items.append(html_link(_folder_route(view, item.target), item.label, "nav-link") + badge)
        elif item.kind == dimension_registry._VIEW_KIND_OVERVIEW:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FEED:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_TRENDING:
            nav_items.append(html_link(_trending_route(view), item.label, "nav-link"))
            has_trending = True
    if not has_trending:
        nav_items.append(html_link(_trending_route(view), "Trending", "nav-link"))
    return nav_items


def _public_nav_entries(view_name: str) -> list[str]:
    try:
        view = get_view(view_name)
    except KeyError:
        return []
    location = _folder_location(view, PUBLIC_PAGE_DIR)
    if location is None:
        return []
    root, storage_clean = location
    entries: list[str] = []
    for path in _direct_child_dirs(root):
        rel = f"{storage_clean.rstrip('/')}/{path.name}".strip("/")
        count = _public_nav_count(path)
        label = format_count(count) if count else ""
        badge = f' <span class="count" data-sidebar-count="{html.escape(_visible_rel(rel))}">{label}</span>'
        entries.append(html_link(_folder_route(view_name, _visible_rel(rel)), _display_name(path.name), "nav-link") + badge)
    intro_names = set(FOLDER_INTRO_FILES)
    for note in _folder_notes_from_root(view, storage_clean, root):
        if Path(note.rel).name.lower() in intro_names:
            continue
        entries.append(html_link(note.route, _note_list_title(note), "nav-link"))
    return entries


def _public_nav_count(path: Path) -> int:
    try:
        return sum(
            1 for md in path.rglob("*.md")
            if md.name.lower() not in set(FOLDER_INTRO_FILES) and not is_sharepoint_conflict_path(md.name)
        )
    except OSError:
        return 0


def _sidebar_tree_enabled() -> bool:
    return bool(dimension_registry.display().get("sidebar_tree"))


def _sidebar_cache_key(current_rel: str) -> str:
    current_folder = Path(current_rel).parts[0] if current_rel else ""
    return f"{current_folder}:{int(_sidebar_tree_enabled())}"


def _sidebar_cache_get(view: ViewState, current_rel: str, *, disk: bool = False) -> str | None:
    from argus_wiki.index import reader_cache as rc

    key = _sidebar_cache_key(current_rel)
    hit = rc.get_sidebar_html(view.name, key, disk=disk)
    if hit is not None:
        return hit
    base = _sidebar_cache_key("")
    if key != base:
        return rc.get_sidebar_html(view.name, base, disk=disk)
    return None


def _sidebar_cache_put(view: ViewState, current_rel: str, html: str, *, persist: bool = False) -> None:
    from argus_wiki.index import reader_cache as rc

    rc.put_sidebar_html(view.name, _sidebar_cache_key(current_rel), html, persist=persist)


def sidebar_shell(view: str, current_rel: str = "") -> str:
    """Fast nav chrome only — no tree, trending, or SMB rglob. Full sidebar loads async."""
    nav_items: list[str] = []
    has_trending = False
    for item in reader_contract.structured_nav():
        if item.kind == dimension_registry._VIEW_KIND_PAGE:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FOLDER:
            if item.target == PUBLIC_PAGE_DIR:
                nav_items.append(html_link(_folder_route(view, item.target), item.label, "nav-link"))
                continue
            badge = f' <span class="count" data-sidebar-count="{html.escape(item.target)}"></span>'
            nav_items.append(html_link(_folder_route(view, item.target), item.label, "nav-link") + badge)
        elif item.kind == dimension_registry._VIEW_KIND_OVERVIEW:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_FEED:
            nav_items.append(html_link(route_for(view, item.target), item.label, "nav-link"))
        elif item.kind == dimension_registry._VIEW_KIND_TRENDING:
            nav_items.append(html_link(_trending_route(view), item.label, "nav-link"))
            has_trending = True
    if not has_trending:
        nav_items.append(html_link(_trending_route(view), "Trending", "nav-link"))
    return (
        f'<aside class="sidebar" id="wiki-sidebar" data-deferred-sidebar="1" '
        f'data-current-rel="{html.escape(current_rel)}">'
        f'<nav class="nav-primary">{"".join(nav_items)}</nav>'
        f'<div class="tree sidebar-loading" aria-busy="true" '
        f'style="padding:12px 14px;font-size:12px;color:var(--text-muted)">Loading navigation…</div>'
        f"</aside>"
    )


def sidebar_lazy(view: str, current_rel: str = "") -> str:
    """Use cached full sidebar when hot; otherwise shell + async fill (first-click path)."""
    try:
        vstate = get_view(view)
    except KeyError:
        return sidebar_shell(view, current_rel)
    cached = _sidebar_cache_get(vstate, current_rel, disk=False)
    if cached is not None:
        if current_rel:
            cached = cached.replace(
                f'data-rel="{html.escape(current_rel)}"',
                f'data-rel="{html.escape(current_rel)}" class="tree-link active"',
                1,
            )
        return cached
    from argus_wiki.index import reader_cache as rc

    rc.kick_refresh()
    return sidebar_shell(view, current_rel)


def render_sidebar(view_name: str, current_rel: str = "") -> tuple[str, int]:
    view = get_view(view_name)
    cached = _sidebar_cache_get(view, current_rel)
    if cached is not None:
        if current_rel:
            cached = cached.replace(
                f'data-rel="{html.escape(current_rel)}"',
                f'data-rel="{html.escape(current_rel)}" class="tree-link active"',
                1,
            )
        return cached, 200
    if not is_ready(view):
        from argus_wiki.index import reader_cache as rc

        rc.kick_refresh()
        return sidebar_shell(view_name, current_rel), 202
    notes: list[Note] = []
    indexes = view.index_result
    if indexes and indexes.get("notes") is not None:
        notes = indexes.get("notes") or []
    elif view.notes_by_rel:
        notes = list(view.notes_by_rel.values())
    return sidebar(view_name, notes, current_rel), 200


def sidebar(view: str, notes: list[Note], current_rel: str, *, persist_cache: bool = False,
            lightweight: bool = False) -> str:
    vstate = get_view(view)
    cached = _sidebar_cache_get(vstate, current_rel, disk=persist_cache)
    if cached is not None:
        if current_rel:
            cached = cached.replace(
                f'data-rel="{html.escape(current_rel)}"',
                f'data-rel="{html.escape(current_rel)}" class="tree-link active"',
                1,
            )
        return cached

    if not notes and vstate.notes_by_rel:
        notes = list(vstate.notes_by_rel.values())
    groups = sidebar_groups(notes)
    if lightweight:
        nav_items = _structured_nav_entries_light(view, groups)
        trend = ""
    else:
        nav_items = _structured_nav_entries(view, groups)
        trend = trending_widget(view)

    folder_blocks = []
    if _sidebar_tree_enabled():
        for folder in sorted(_known_dirs()):
            folder_notes = groups.get(folder, [])
            if not folder_notes:
                continue
            items = []
            cap = 40
            for note in folder_notes[:cap]:
                list_title = _note_list_title(note)
                title_attr = note.title if note.title == list_title else f"{list_title} - {note.title}"
                items.append(
                    f'<a class="tree-link" data-rel="{html.escape(note.rel)}" href="{note.route}" title="{html.escape(title_attr)}">{html.escape(list_title)}</a>'
                )
            more = ""
            if len(folder_notes) > cap:
                more = f'<a class="tree-more" href="{_folder_route(view, folder)}">+{len(folder_notes) - cap} more</a>'
            open_attr = " open" if folder == (Path(current_rel).parts[0] if current_rel else "") else ""
            folder_blocks.append(
                f'<details class="tree-folder"{open_attr}>'
                f'<summary><span class="folder-name">{html.escape(dimension_registry.folder_label(folder))}</span><span class="count">{len(folder_notes)}</span></summary>'
                f'<div class="tree-children">{"".join(items)}{more}</div>'
                f"</details>"
            )

    result = (
        f'<aside class="sidebar" id="wiki-sidebar">'
        f'<nav class="nav-primary">{"".join(nav_items)}</nav>'
        f'{trend}'
        f'<div class="tree">{"".join(folder_blocks)}</div>'
        f"</aside>"
    )
    _sidebar_cache_put(vstate, current_rel, result, persist=persist_cache)
    if current_rel:
        result = result.replace(
            f'data-rel="{html.escape(current_rel)}"',
            f'data-rel="{html.escape(current_rel)}" class="tree-link active"',
            1,
        )
    return result


def refresh_reader_cache() -> None:
    """Hourly/background: rebuild durable web-read cache for ready views.

    Per-view work fans out through JobExecutor (thread pool); Delta write is one
    batched upsert on the caller thread.
    """
    from common.utils.jobutil import JobExecutor
    from argus_wiki.index import reader_cache as rc

    ensure_views()
    # Warm the entity-identifier resolution map OFF the request path. It is memoized
    # on entity_identifiers.state_signature(), which the watcher's provider
    # reconciliation busts; rebuilt lazily it iterates ~25k provider identifiers and
    # costs 6-12s on the first render after a bust (the convert_wikilinks
    # `identifiers` bucket). Building it here -- on the periodic refresher and on
    # kick_refresh -- keeps it warm so a user render never pays the cold build.
    try:
        reader_contract.entity_identifier_rel_map()
    except Exception:
        logging.warning("argus_wiki reader_cache refresh: identifier map warm failed", exc_info=True)
    view_names = [
        v.name for v in list(VIEWS.values())
        if v.index_result is not None or v.notes_by_rel
    ]
    if not view_names:
        logging.info("argus_wiki reader_cache refresh skipped (no ready views)")
        return
    workers = max(1, min(3, len(view_names)))
    jobber = JobExecutor("thread", max_workers=workers)
    try:
        results = jobber.submitjob(
            _refresh_view_cache_rows,
            [{"view_name": name} for name in view_names],
            initconfig=False,
            job_name="reader_cache_view",
        )
    finally:
        jobber.cleanup()
    rows: list[dict] = []
    for name, row in zip(view_names, results or []):
        err = (row or {}).get("error")
        if err:
            logging.error("argus_wiki reader_cache view=%s failed: %s", name, err)
            continue
        chunk = (row or {}).get("result") or []
        if isinstance(chunk, list):
            rows.extend(chunk)
    if rows:
        rc.flush_rows(rows)
    logging.info("argus_wiki reader_cache refreshed views=%d rows=%d", len(view_names), len(rows))
    # Compact the reader_cache table only — this node is its sole writer (and only
    # when persistence is enabled; rc.maybe_optimize() no-ops on a replica). The
    # SOURCE index is written by the watcher, so the watcher owns its compaction —
    # a reader must never vacuum a table another process writes.
    try:
        rc.maybe_optimize()
    except Exception:
        logging.exception("argus_wiki reader_cache maybe_optimize failed")


def _refresh_view_cache_rows(view_name: str) -> list[dict]:
    """Build sidebar counts/html/stems rows for one view (JobExecutor worker)."""
    from argus_wiki.index import reader_cache as rc

    view = get_view(view_name)
    if view.index_result is None and not view.notes_by_rel:
        return []
    rows: list[dict] = []
    counts = _compute_sidebar_counts(view, include_public=False)
    rc.put_sidebar_counts(view.name, counts, persist=False)
    rows.append(rc._row(rc.NS_SIDEBAR_COUNTS, view.name, "", json.dumps(counts, separators=(",", ":"))))
    notes = list(view.notes_by_rel.values()) if view.notes_by_rel else []
    if view.index_result and view.index_result.get("notes"):
        notes = view.index_result.get("notes") or notes
    html = sidebar(view.name, notes, "", persist_cache=False, lightweight=True)
    key = _sidebar_cache_key("")
    rc.put_sidebar_html(view.name, key, html, persist=False)
    rows.append(rc._row(rc.NS_SIDEBAR_HTML, view.name, key, html))
    stems_by_folder: dict[str, set[str]] = {}
    for note in notes:
        folder = note.folder or ""
        stems_by_folder.setdefault(folder, set()).add(Path(note.rel).stem.lower())
    for folder, stems in stems_by_folder.items():
        rc.put_dir_stems(view.name, folder, stems, persist=False)
        rows.append(rc._row(rc.NS_DIR_STEMS, view.name, folder, json.dumps(sorted(stems))))
    return rows


def backlinks(view: ViewState, current: Note, backlinks_index: dict[str, list[Note]] | None = None) -> str:
    stem = Path(current.rel).with_suffix("").as_posix()
    leaf = Path(stem).name.lower()
    index = backlinks_index if backlinks_index is not None else build_indexes(view).get("backlinks", {})
    matches = []
    seen = set()
    for key in {leaf, stem.lower()}:
        for note in index.get(key, []):
            if note.rel != current.rel and note.rel not in seen:
                seen.add(note.rel)
                matches.append(note)
    if not matches:
        return '<div class="empty">No notes mention this page yet.</div>'
    t0 = time.perf_counter()
    today_page = _today_backlink_page(current)
    limit = 15 if today_page else 60
    # Sort with a CHEAP key (regex-only daily meta + rel/folder attrs) so we do
    # NOT build a display label for every match. today.md accumulates one backlink
    # per archived daily snapshot -- thousands over time -- and building the
    # identity-title label for all of them was the whole render cost (the 'right'
    # bucket = 17s single-request). Label ONLY the slice actually shown.
    def _cheap_sort_key(note: Note) -> tuple:
        meta = _daily_snapshot_meta(note)
        if today_page and meta:
            return (0, -int(meta["sort_ts"]), note.rel)
        if meta:
            return (1, -int(meta["sort_ts"]), note.rel)
        return (2, note.folder, Path(note.rel).stem.lower(), note.rel)
    matches.sort(key=_cheap_sort_key)
    shown = matches[:limit]
    sorted_at = time.perf_counter()
    labels = {note.rel: _backlink_label(note, compact_daily=today_page) for note in shown}
    label_counts: dict[str, int] = {}
    for label in labels.values():
        label_counts[label] = label_counts.get(label, 0) + 1
    for note in shown:
        label = labels[note.rel]
        if label_counts[label] > 1:
            labels[note.rel] = f"{label} · {Path(note.rel).stem}"
    items = "".join(
        f'<li><a href="{note.route}" title="{html.escape(note.rel)}">{html.escape(labels[note.rel])}</a>'
        f'<span class="bl-folder">{html.escape(note.folder)}</span></li>'
        for note in shown
    )
    extra = "" if len(matches) <= limit else f'<div class="muted small">+{len(matches) - limit} more</div>'
    total = time.perf_counter() - t0
    if total >= 0.5:
        logging.info(
            "argus_wiki backlinks slow rel=%s matches=%d shown=%d sort=%.2fs labels=%.2fs total=%.2fs",
            current.rel, len(matches), len(shown), sorted_at - t0, time.perf_counter() - sorted_at, total,
        )
    return f'<ul class="backlinks">{items}</ul>{extra}'


def toc_html(toc: list[tuple[int, str, str]]) -> str:
    if not toc:
        return '<div class="toc-empty">No sections.</div>'
    items = "".join(
        f'<a class="toc-link toc-l{level}" href="#{anchor}" data-toc-anchor="{anchor}">{html.escape(title)}</a>'
        for level, anchor, title in toc
    )
    return f'<nav class="toc-list" aria-label="Table of contents">{items}</nav>'


def tag_pills(tags: tuple[str, ...]) -> str:
    if not tags:
        return ""
    pills = "".join(f'<span class="tag">#{html.escape(tag)}</span>' for tag in tags)
    return f'<div class="tags-row">{pills}</div>'


def breadcrumb(view: str, rel: str) -> str:
    clean = rel.replace("\\", "/").strip("/")
    parts = [part for part in clean.split("/") if part]
    crumbs = [f'<a href="{route_for(view, reader_contract.overview_page_rel())}">{html.escape(_site_name())}</a>']
    if not parts:
        return '<nav class="breadcrumbs">' + ' <span class="crumb-sep">/</span> '.join(crumbs) + "</nav>"
    cumulative: list[str] = []
    last_idx = len(parts) - 1
    for idx, part in enumerate(parts):
        cumulative.append(part)
        if part == PUBLIC_PAGE_DIR and idx == 0:
            continue
        label = Path(part).stem if idx == last_idx and not rel.endswith("/") else part
        if idx == last_idx:
            label = _source_display_title(clean) or _display_name(label)
        else:
            label = _display_name(label)
        visible = _visible_rel("/".join(cumulative))
        if idx == last_idx:
            crumbs.append(f'<span class="crumb-current">{html.escape(label)}</span>')
        else:
            crumbs.append(f'<a href="{_folder_route(view, visible)}">{html.escape(label)}</a>')
    return '<nav class="breadcrumbs">' + ' <span class="crumb-sep">/</span> '.join(crumbs) + "</nav>"


def view_switcher(active_view: str, current_rel: str | None) -> str:
    ensure_views()
    if len(VIEW_ORDER) <= 1:
        return ""
    tabs = []
    home_rel = reader_contract.home_page_rel()
    for name in VIEW_ORDER:
        if current_rel and current_rel.endswith(".md"):
            visible = _visible_rel(current_rel[:-3])
            href = route_for(name, visible or current_rel[:-3])
        elif current_rel and current_rel.endswith("/"):
            visible = _visible_rel(current_rel.strip("/"))
            href = route_for(name, visible or current_rel.strip("/"))
        else:
            href = route_for(name, home_rel)
        cls = "view-tab active" if name == active_view else "view-tab"
        tabs.append(f'<a class="{cls}" href="{html.escape(href)}">{html.escape(name)}</a>')
    return f'<nav class="view-switcher">{"".join(tabs)}</nav>'


def _structured_switch(view: str, current_rel: str | None) -> str:
    """Server-wide 'structured' data on/off toggle for the top bar."""
    from urllib.parse import quote
    on = structured_enabled()
    nxt = ""
    try:
        from flask import request, has_request_context
        if has_request_context():
            nxt = request.full_path or ""
    except Exception:
        nxt = ""
    if not nxt:
        nxt = route_for(view, current_rel or reader_contract.home_page_rel())
    href = f"{URL_PATH}/_structured?next={quote(nxt, safe='')}"
    return (
        f'<a class="struct-switch {"on" if on else ""}" href="{href}" role="switch" '
        f'aria-checked="{"true" if on else "false"}" '
        f'title="Structured data tiles — server-wide, resets on restart">'
        f'<span class="struct-switch-label">structured</span>'
        f'<span class="struct-track"><span class="struct-knob"></span></span></a>'
    )


def topbar(view: str, current_rel: str | None) -> str:
    home_href = route_for(view, reader_contract.home_page_rel())
    feed_href = route_for(view, reader_contract.feed_page_rel())
    return f"""
    <header class="topbar">
      <button class="wiki-drawer-toggle left" id="nav-toggle" type="button" aria-label="Open navigation" aria-controls="wiki-sidebar" aria-expanded="false">&#9776;</button>
      <a class="brand" href="{home_href}">
        <span class="brand-mark">*</span>
        <span class="brand-text">{html.escape(_site_name())}</span>
      </a>
      {view_switcher(view, current_rel)}
      <form class="topbar-search" action="{_search_route(view)}" method="get">
        <input id="search-input" name="q" placeholder="Search…" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="search-suggest">
        <kbd class="search-shortcut">⌘K</kbd>
        <ul id="search-suggest" class="search-suggest" role="listbox" hidden></ul>
      </form>
      <div class="topbar-actions">
        {_structured_switch(view, current_rel)}
        <a class="icon-btn" href="{feed_href}" title="Activity feed">📋</a>
        <button class="wiki-drawer-toggle right" id="toc-toggle" type="button" aria-label="Open contents" aria-controls="wiki-rightbar" aria-expanded="false">&#9776;</button>
      </div>
    </header>
    """


# Collapsible nav/TOC rails. The two top-bar ☰ buttons are always present. On WIDE
# screens a toggle docks/undocks the rail column (body.nav-collapsed / toc-collapsed,
# main reflows to full width); on NARROW screens it opens/closes an off-canvas drawer
# (body.nav-open / toc-open, backdrop + Esc close). matchMedia picks the mode per side
# (left ≤720, right ≤980) to match the CSS breakpoints. The right toggle self-hides
# when the right rail is empty (e.g. search pages). Kept as a plain (non-f) raw string
# so its JS braces are literal; layout() interpolates it once via {_DRAWER_JS}.
_DRAWER_JS = r"""  <script>
  (function(){
    var body=document.body;
    var navT=document.getElementById('nav-toggle');
    var tocT=document.getElementById('toc-toggle');
    var back=document.getElementById('wiki-drawer-backdrop');
    var rb=document.getElementById('wiki-rightbar');
    if(tocT && (!rb || rb.children.length===0)){ tocT.style.display='none'; }
    function isMobile(side){
      return window.matchMedia(side==='nav'?'(max-width: 720px)':'(max-width: 980px)').matches;
    }
    function railShown(side){
      if(isMobile(side)) return body.classList.contains(side==='nav'?'nav-open':'toc-open');
      return !body.classList.contains(side==='nav'?'nav-collapsed':'toc-collapsed');
    }
    function syncAria(){
      if(navT)navT.setAttribute('aria-expanded', String(railShown('nav')));
      if(tocT)tocT.setAttribute('aria-expanded', String(railShown('toc')));
    }
    function closeAll(){ body.classList.remove('nav-open','toc-open'); syncAria(); }
    function toggle(side){
      if(isMobile(side)){
        var openCls=side==='nav'?'nav-open':'toc-open';
        var willOpen=!body.classList.contains(openCls);
        body.classList.remove('nav-open','toc-open');   // one drawer at a time
        body.classList.toggle(openCls, willOpen);
      } else {
        body.classList.toggle(side==='nav'?'nav-collapsed':'toc-collapsed');
      }
      syncAria();
    }
    if(navT)navT.addEventListener('click',function(e){e.preventDefault();toggle('nav');});
    if(tocT)tocT.addEventListener('click',function(e){e.preventDefault();toggle('toc');});
    if(back)back.addEventListener('click',closeAll);
    document.addEventListener('keydown',function(e){if(e.key==='Escape')closeAll();});
    window.addEventListener('resize',syncAria);
    syncAria();
  })();
  </script>
"""


# Self-contained image lightbox (zoom / pan / rotate / flip), injected into every
# page. Dependency-free and fail-safe: if the JS errors, images still render as
# plain <img>. Kept as a plain string (NOT an f-string) so its JS braces are
# literal; layout() interpolates it once via {_IMG_VIEWER}. Its own <script> tag
# isolates any error from the page's main script.
_IMG_VIEWER = r"""  <style>
  .aiv-overlay{position:fixed;inset:0;z-index:9998;background:rgba(10,12,16,.93);display:none;align-items:center;justify-content:center;touch-action:none;user-select:none}
  .aiv-overlay.open{display:flex}
  .aiv-stage{position:absolute;inset:0;overflow:hidden;cursor:grab}
  .aiv-stage.drag{cursor:grabbing}
  .aiv-img{position:absolute;left:50%;top:50%;transform-origin:center center;will-change:transform;max-width:none;max-height:none;-webkit-user-drag:none}
  .aiv-tools{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);display:flex;gap:4px;background:rgba(28,32,40,.96);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:6px;box-shadow:0 8px 30px rgba(0,0,0,.5);z-index:10000}
  .aiv-btn{all:unset;box-sizing:border-box;cursor:pointer;color:#e6e8ec;font:600 14px/1 system-ui,-apple-system,sans-serif;padding:8px 11px;border-radius:8px;min-width:18px;text-align:center}
  .aiv-btn:hover{background:rgba(255,255,255,.14)}
  .aiv-x{position:fixed;top:14px;right:16px;z-index:10000;font-size:16px}
  .aiv-hint{position:fixed;top:16px;left:18px;color:#9aa0aa;font:500 12px/1.3 system-ui,sans-serif;z-index:10000;pointer-events:none}
  main img{cursor:zoom-in}
  /* CSS-only content tabs (Charts/Tables, Key Charts/Key Tables). No JS; the
     vault markdown stays plain ## sections so Obsidian is unaffected. */
  .ctabs{margin:14px 0}
  .ctab-r{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
  .ctab-l{display:inline-block;padding:8px 18px;cursor:pointer;font-weight:600;color:var(--text-muted,#8a8f98);border-bottom:2px solid transparent;margin-right:4px}
  .ctab-l:hover{color:var(--text,#111)}
  .ctab-ra:checked~.ctab-la,.ctab-rb:checked~.ctab-lb{color:var(--text,#111);border-bottom-color:currentColor}
  .ctab-p{display:none;padding-top:10px}
  .ctab-ra:checked~.ctab-pa,.ctab-rb:checked~.ctab-pb{display:block}
  </style>
  <div class="aiv-overlay" id="aiv" aria-hidden="true">
    <div class="aiv-stage" id="aiv-stage"><img class="aiv-img" id="aiv-img" alt=""></div>
    <div class="aiv-hint">scroll = zoom &middot; drag = pan &middot; double-click = reset</div>
    <button class="aiv-btn aiv-x" id="aiv-x" title="Close (Esc)">&#10005;</button>
    <div class="aiv-tools">
      <button class="aiv-btn" data-act="zoomout" title="Zoom out (-)">&minus;</button>
      <button class="aiv-btn" data-act="zoomin" title="Zoom in (+)">+</button>
      <button class="aiv-btn" data-act="rotl" title="Rotate left">&#8634;</button>
      <button class="aiv-btn" data-act="rotr" title="Rotate right">&#8635;</button>
      <button class="aiv-btn" data-act="fliph" title="Flip">&#8646;</button>
      <button class="aiv-btn" data-act="reset" title="Reset (0)">Reset</button>
      <a class="aiv-btn" id="aiv-open" target="_blank" rel="noopener" title="Open original">&#8599;</a>
    </div>
  </div>
  <script>
  (function(){
    var ov=document.getElementById('aiv'),stage=document.getElementById('aiv-stage'),
        img=document.getElementById('aiv-img'),openLink=document.getElementById('aiv-open');
    if(!ov||!img)return;
    var scale=1,rot=0,flip=1,tx=0,ty=0,drag=false,sx=0,sy=0;
    function apply(){img.style.transform='translate(calc(-50% + '+tx+'px),calc(-50% + '+ty+'px)) scale('+(scale*flip)+','+scale+') rotate('+rot+'deg)';}
    function reset(){scale=1;rot=0;flip=1;tx=0;ty=0;apply();}
    function open(src,alt){img.src=src;img.alt=alt||'';if(openLink)openLink.href=src;reset();ov.classList.add('open');ov.setAttribute('aria-hidden','false');document.body.style.overflow='hidden';}
    function close(){ov.classList.remove('open');ov.setAttribute('aria-hidden','true');document.body.style.overflow='';img.src='';}
    function zoom(f){scale=Math.min(14,Math.max(0.15,scale*f));apply();}
    document.addEventListener('click',function(e){
      var t=e.target;
      if(!t||t.tagName!=='IMG')return;
      if(!t.closest('main')||t.closest('.aiv-overlay')||t.closest('a'))return;
      if((t.naturalWidth||t.width||0)<40)return;
      e.preventDefault();open(t.currentSrc||t.src,t.alt);
    });
    ov.addEventListener('click',function(e){
      var b=e.target.closest&&e.target.closest('[data-act]');
      if(b){var a=b.getAttribute('data-act');
        if(a==='zoomin')zoom(1.25);else if(a==='zoomout')zoom(0.8);
        else if(a==='rotl'){rot-=90;apply();}else if(a==='rotr'){rot+=90;apply();}
        else if(a==='fliph'){flip*=-1;apply();}else if(a==='reset')reset();
        e.stopPropagation();return;}
      if(e.target===ov||e.target===stage)close();
    });
    document.getElementById('aiv-x').addEventListener('click',close);
    stage.addEventListener('wheel',function(e){e.preventDefault();zoom(e.deltaY<0?1.12:0.89);},{passive:false});
    stage.addEventListener('pointerdown',function(e){drag=true;sx=e.clientX-tx;sy=e.clientY-ty;stage.classList.add('drag');try{stage.setPointerCapture(e.pointerId);}catch(_){}});
    stage.addEventListener('pointermove',function(e){if(!drag)return;tx=e.clientX-sx;ty=e.clientY-sy;apply();});
    stage.addEventListener('pointerup',function(){drag=false;stage.classList.remove('drag');});
    stage.addEventListener('dblclick',function(){if(scale>1||rot||flip<0)reset();else zoom(2.4);});
    document.addEventListener('keydown',function(e){if(!ov.classList.contains('open'))return;
      if(e.key==='Escape')close();else if(e.key==='+'||e.key==='=')zoom(1.25);
      else if(e.key==='-')zoom(0.8);else if(e.key==='0')reset();});
  })();
  </script>
"""


# ── HuggingNews-style "feed" layout ────────────────────────────────────────
# A page whose frontmatter carries `layout: feed` renders the SAME Obsidian
# markdown (the `### N / 10 · Tags` + `_meta:_` + `> [!summary]` + per-note
# bullet contract that today.md / today_v2.md emit) as a dense, ranked,
# day-grouped news feed instead of the default story-cards. The content is not
# touched — this is a client-side re-layout keyed off the body class below, so
# the vault file stays valid Obsidian markdown. Colors reuse the active theme
# variables (we adopt HuggingNews *structure/density*, not its brand palette).



def layout(view: str, title: str, content: str, left: str, right: str = "", page_rel: str | None = None,
           body_layout: str | None = None) -> str:
    crumbs = breadcrumb(view, page_rel) if page_rel else ""
    page_prefix = reader_contract.route_for("page", view=view, rel="", prefix=URL_PATH).rstrip("/")
    page_prefix = page_prefix + "/" if not page_prefix.endswith("/") else page_prefix
    css_href = f"{URL_PATH}/_theme.css"
    # Body class drives source-page-specific typography (the muted first-paragraph + verdict
    # blockquote treatment that used to apply globally via `article > p:first-of-type`).
    body_classes: list[str] = []
    if page_rel and reader_contract.is_source_rel(page_rel):
        body_classes.append("source-page")
    if body_layout:
        body_classes.append(f"layout-{re.sub(r'[^a-z0-9_-]', '', str(body_layout).lower())}")
    body_class_attr = f' class="{" ".join(body_classes)}"' if body_classes else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - {html.escape(_site_name())} - {html.escape(view)}</title>
  <link rel="stylesheet" href="{css_href}">
  <meta name="color-scheme" content="light dark">
  <style>
    html {{ scroll-behavior: smooth; }}
    .view-switcher {{ display: inline-flex; gap: 4px; align-items: center; }}
    .view-tab {{ padding: 4px 12px; border-radius: 999px; font-size: 12.5px; font-weight: 500; color: var(--text-muted); border: 1px solid transparent; transition: all 0.15s ease; }}
    .view-tab:hover {{ background: var(--bg-elev, var(--summary-bg)); color: var(--text); text-decoration: none; }}
    .view-tab.active {{ background: var(--bg-elev, var(--summary-bg)); color: var(--text); border-color: var(--border-strong, var(--border)); font-weight: 600; }}
    .search-shortcut {{ font-family: var(--sans); font-size: 10px; color: var(--text-muted, var(--grey)); background: var(--surface, var(--white)); border: 1px solid var(--border-light); padding: 2px 6px; border-radius: 4px; pointer-events: none; position: absolute; right: 12px; top: 50%; transform: translateY(-50%); }}
    .topbar-search {{ position: relative; }}
    .search-suggest {{ position: absolute; top: 100%; left: 0; right: 0; margin-top: 5px; background: var(--surface, #fff); border: 1px solid var(--border-light); border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.14); list-style: none; padding: 4px; z-index: 3000; max-height: 360px; overflow-y: auto; }}
    .search-suggest[hidden] {{ display: none; }}
    .search-suggest li {{ margin: 0; }}
    .search-suggest a {{ display: flex; flex-direction: column; gap: 2px; padding: 7px 10px; border-radius: 7px; text-decoration: none; color: var(--text); }}
    .search-suggest a:hover, .search-suggest a.sg-active {{ background: var(--bg-elev, var(--summary-bg)); }}
    .search-suggest .sg-main {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
    .search-suggest .sg-kind {{ flex: 0 0 auto; font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; padding: 1px 7px; border-radius: 999px; border: 1px solid transparent; }}
    .search-suggest .sg-date {{ flex: 0 0 auto; font-size: 11.5px; font-weight: 700; color: var(--text-muted); font-variant-numeric: tabular-nums; }}
    .search-suggest .sg-label {{ font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .search-suggest .sg-sub {{ font-size: 11.5px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-left: 2px; }}
    .search-suggest .sg-cats {{ display: flex; flex-direction: column; gap: 3px; padding: 4px 4px 6px; margin-bottom: 4px; border-bottom: 1px solid var(--border-light); }}
    .search-suggest .sg-cat {{ display: flex; align-items: center; gap: 8px; padding: 2px 6px; }}
    .search-suggest .sg-cat-label {{ flex: 0 0 72px; font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; text-align: center; padding: 2px 0; border-radius: 999px; border: 1px solid transparent; }}
    .search-suggest .sg-cat-items {{ display: flex; flex-wrap: wrap; gap: 5px; min-width: 0; }}
    .search-suggest .sg-chip {{ font-size: 12.5px; font-weight: 600; padding: 2px 9px; border-radius: 7px; text-decoration: none; color: var(--text); background: var(--bg-subtle, rgba(0,0,0,.04)); border: 1px solid var(--border-light); }}
    .search-suggest .sg-chip:hover, .search-suggest .sg-chip.sg-active {{ background: var(--bg-elev, var(--summary-bg)); border-color: var(--apollo-green, #007B63); }}
    .search-suggest .sg-results {{ list-style: none; padding: 0; margin: 0; }}
    .topbar-search input:focus ~ .search-shortcut {{ opacity: 0; }}
    .search-summary {{ margin-top: 18px; display: grid; gap: 18px; }}
    .search-folder {{ padding: 18px 20px; border: 1px solid var(--border-light); border-radius: var(--radius, 8px); background: var(--surface, var(--white)); }}
    .search-folder h2 {{ margin: 0 0 14px; display: flex; align-items: center; gap: 10px; font-size: 16px; }}
    .search-folder h2 span {{ color: var(--text-muted); font-size: 12px; font-weight: 600; }}
    .search-entity-top {{ border-color: var(--apollo-green, #007B63); box-shadow: 0 2px 10px rgba(0,123,99,0.06); }}
    .search-top {{ border-color: var(--apollo-green, #007B63); box-shadow: 0 2px 10px rgba(0,123,99,0.08); }}
    .search-pagination {{ display: flex; align-items: center; gap: 12px; margin-top: 14px; color: var(--text-muted); font-size: 12px; }}
    .search-pagination a {{ padding: 5px 10px; border: 1px solid var(--border-light); border-radius: 999px; background: var(--surface, var(--white)); text-decoration: none; }}
    /* Faceted-search filter bar: date presets + config-driven facet chips with counts. */
    .search-filters {{ display: grid; gap: 8px; margin: 14px 0 4px; }}
    .fbar-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 2px; }}
    .fbar-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }}
    .fbar-clear {{ display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; color: var(--text-muted); background: var(--surface, var(--white)); border: 1px solid var(--border-light); border-radius: 999px; padding: 4px 12px; text-decoration: none; transition: border-color .12s ease, color .12s ease; }}
    .fbar-clear:hover {{ border-color: var(--apollo-green, #007B63); color: var(--apollo-green, #007B63); text-decoration: none; }}
    .fbar-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
    .fbar-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); min-width: 62px; }}
    .fchip {{ padding: 4px 11px; border: 1px solid var(--border-light); border-radius: 999px; font-size: 12.5px; color: var(--text); background: var(--surface, var(--white)); text-decoration: none; white-space: nowrap; transition: border-color .12s ease, background .12s ease; }}
    .fchip:hover {{ border-color: var(--apollo-green, #007B63); }}
    .fchip.active {{ background: var(--apollo-green, #007B63); color: #fff; border-color: var(--apollo-green, #007B63); }}
    .fchip .fcount {{ opacity: 0.7; font-size: 11px; margin-left: 2px; }}
    .fchip.active .fcount {{ opacity: 0.9; }}
    .search-audit {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin: 10px 0 16px; padding: 10px 13px; border: 1px solid var(--border-light); border-radius: 10px; background: var(--surface, var(--white)); font-size: 12.5px; }}
    .search-audit.healthy {{ border-left: 4px solid var(--apollo-green, #007B63); }}
    .search-audit.processing {{ border-left: 4px solid #d99000; }}
    .search-audit.degraded, .search-audit.unknown {{ border-left: 4px solid #bd2c2c; }}
    .search-audit strong {{ display: block; margin-bottom: 2px; }}
    .search-audit details {{ max-width: 48%; color: var(--text-muted); }}
    .search-audit ul {{ margin: 6px 0 0; padding-left: 18px; }}
    .result-badge {{ display: inline-flex; margin-right: 6px; padding: 1px 6px; border-radius: 999px; background: var(--bg-subtle, #eef2f0); color: var(--apollo-green, #007B63); font-size: 10px; font-weight: 700; text-transform: uppercase; }}
    .result-date {{ margin-right: 6px; color: var(--text-muted); font-weight: 700; font-variant-numeric: tabular-nums; }}
    /* Responsive card grid: ~3-4 across on desktop, auto-collapses to 1 on mobile. */
    .results.compact {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; align-items: start; }}
    .results.compact .result {{ display: flex; flex-direction: column; gap: 4px; padding: 12px 14px; border: 1px solid var(--border-light); border-radius: 10px; background: var(--surface, var(--white)); text-decoration: none; color: inherit; height: 100%; transition: border-color .12s ease, box-shadow .12s ease, transform .12s ease; }}
    .results.compact .result:hover {{ border-color: var(--apollo-green, #007B63); box-shadow: 0 3px 12px rgba(0,0,0,0.07); transform: translateY(-1px); }}
    .result-title {{ font-size: 14px; font-weight: 600; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .result-meta {{ font-size: 11.5px; color: var(--text-muted); }}
    .result-meta .result-mtime {{ opacity: 0.85; }}
    /* the snippet is the last meta line — clamp so cards stay a tidy, even height */
    .results.compact .result .result-meta:last-child {{ display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
    .result-more {{ color: var(--text-muted); font-size: 12px; padding-top: 4px; }}
    .trending-card {{ margin: 14px 0 6px; padding: 14px 16px; border: 1px solid var(--border-light); border-radius: var(--radius, 8px); background: var(--surface, var(--white)); }}
    .trending-card h3 {{ margin: 0 0 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); display: flex; align-items: baseline; justify-content: space-between; }}
    .trending-window {{ font-weight: 500; text-transform: none; letter-spacing: 0; opacity: 0.7; }}
    .trending-list {{ margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; counter-reset: trend; }}
    .trending-list li {{ display: flex; align-items: baseline; gap: 8px; counter-increment: trend; font-size: 13px; }}
    .trending-list li::before {{ content: counter(trend); color: var(--text-muted); font-variant-numeric: tabular-nums; min-width: 14px; text-align: right; }}
    .trending-link {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .trending-count {{ color: var(--text-muted); font-variant-numeric: tabular-nums; font-size: 11.5px; }}
    .note-mtime {{ margin: 6px 0 14px; }}
    .result-mtime {{ color: var(--text-muted); font-variant-numeric: tabular-nums; }}
    /* reading progress bar */
    .reading-progress {{ position: fixed; top: 0; left: 0; height: 2px; background: var(--apollo-green, #007B63); z-index: 100; width: 0; transition: width 0.1s linear; pointer-events: none; }}
    /* TOC active highlighting */
    .toc-link.active {{ color: var(--apollo-green, #007B63) !important; border-left-color: var(--apollo-green, #007B63) !important; font-weight: 600; background: var(--row-hover, #F0F7F5); }}
    .toc-empty {{ font-size: 11px; color: var(--text-muted); font-style: italic; padding: 4px 0; }}
    /* folder card grid */
    .folder-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }}
    .folder-card {{ display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; border: 1px solid var(--border-light); border-radius: var(--radius, 8px); background: var(--surface, var(--white)); text-decoration: none; color: var(--text); transition: all 0.15s ease; }}
    .folder-card:hover {{ border-color: var(--apollo-green, #007B63); box-shadow: 0 2px 8px rgba(0,123,99,0.08); transform: translateY(-1px); text-decoration: none; }}
    .folder-card-title {{ font-weight: 600; font-size: 13px; color: var(--text); }}
    .folder-card:hover .folder-card-title {{ color: var(--apollo-green, #007B63); }}
    .folder-card-subtitle {{ font-size: 11.5px; color: var(--text-muted); }}
    .folder-card-meta {{ font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }}
    .folder-section-title {{ margin: 22px 0 10px; font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }}
    .folder-pagination {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 18px 0; color: var(--text-muted); font-size: 12px; }}
    .folder-pagination a {{ padding: 6px 12px; border: 1px solid var(--border-light); border-radius: 999px; background: var(--surface, var(--white)); text-decoration: none; }}
    .folder-pagination .current {{ color: var(--text); font-weight: 600; }}
    .raw-actions {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 14px 0 20px; }}
    .raw-path-label {{ color: var(--text-muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }}
    .raw-actions code {{ font-size: 12px; color: var(--text-muted); overflow-wrap: anywhere; }}
    .raw-btn {{ display: inline-flex; align-items: center; padding: 8px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-elev); color: var(--text) !important; font-size: 13px; font-weight: 600; text-decoration: none; }}
    .raw-btn.primary {{ background: var(--accent); color: #fff !important; border-color: var(--accent); }}
    .raw-frame {{ width: 100%; height: calc(100vh - 260px); min-height: 520px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }}
    .raw-image {{ max-width: 100%; border: 1px solid var(--border); border-radius: 12px; background: #fff; }}
    .raw-pre {{ white-space: pre-wrap; overflow: auto; max-height: calc(100vh - 280px); padding: 18px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-elev); font-size: 12px; line-height: 1.45; }}
    .raw-empty {{ padding: 22px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-elev); color: var(--text-muted); }}
    .wiki-preview {{ position: fixed; z-index: 200; max-width: 360px; min-width: 240px; padding: 12px 14px; border: 1px solid var(--border-strong, var(--border-light)); border-radius: 12px; background: var(--surface, var(--white)); color: var(--text); box-shadow: 0 12px 30px rgba(0,0,0,0.18); font-size: 12.5px; line-height: 1.45; pointer-events: none; opacity: 0; transform: translateY(4px); transition: opacity 0.12s ease, transform 0.12s ease; }}
    .wiki-preview.visible {{ opacity: 1; transform: translateY(0); }}
    .wiki-preview-title {{ font-weight: 700; font-size: 13.5px; margin-bottom: 3px; }}
    .wiki-preview-meta {{ color: var(--text-muted); font-size: 11px; margin-bottom: 7px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .wiki-preview-body {{ color: var(--text); }}
    /* server-wide structured-data toggle */
    .struct-switch {{ display: inline-flex; align-items: center; gap: 7px; text-decoration: none; color: var(--text-muted); font-size: 12px; font-weight: 600; letter-spacing: .02em; }}
    .struct-switch:hover {{ color: var(--text); text-decoration: none; }}
    .struct-switch .struct-track {{ position: relative; width: 34px; height: 18px; border-radius: 999px; background: var(--border-strong, #c5c7cd); transition: background .18s ease; flex: none; }}
    .struct-switch .struct-knob {{ position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.35); transition: left .18s ease; }}
    .struct-switch.on .struct-track {{ background: var(--apollo-green, #007B63); }}
    .struct-switch.on .struct-knob {{ left: 18px; }}
    /* fade-in animation */
    main {{ animation: fadeIn 0.2s ease-out; }}
    @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}
{_FEED_CSS}
  </style>
{dr_theme.head_tags()}
</head>
<body{body_class_attr}>
  <div class="reading-progress" id="reading-progress"></div>
  {topbar(view, page_rel)}
  <div class="wiki-drawer-backdrop" id="wiki-drawer-backdrop"></div>
  <div class="app">
    {left}
    <main>{crumbs}{content}</main>
    <aside class="rightbar" id="wiki-rightbar">{right}</aside>
  </div>
{_IMG_VIEWER}
{_DRAWER_JS}
  <script>
  (function() {{
    var VIEW = {json.dumps(view)};
    var PREFIX = {json.dumps(page_prefix)};
    var TRACK_URL = {json.dumps(reader_contract.route_for("track", prefix=URL_PATH))};

    /* ── Click tracking ── */
    function send(to, fromRel, q, title) {{
      try {{
        var url = TRACK_URL + "?view=" + encodeURIComponent(VIEW)
          + "&to=" + encodeURIComponent(to)
          + "&from=" + encodeURIComponent(fromRel)
          + "&q=" + encodeURIComponent(q)
          + "&title=" + encodeURIComponent(title || "");
        if (navigator.sendBeacon) navigator.sendBeacon(url);
        else fetch(url, {{ keepalive: true }});
      }} catch (e) {{}}
    }}
    document.addEventListener("click", function(ev) {{
      var a = ev.target && ev.target.closest && ev.target.closest("a");
      if (!a) return;
      var href = a.getAttribute("href") || "";
      if (href.indexOf(PREFIX) !== 0) return;
      var to = decodeURIComponent(href.substring(PREFIX.length).split("#")[0].split("?")[0]);
      var fromRel = location.pathname.indexOf(PREFIX) === 0
        ? decodeURIComponent(location.pathname.substring(PREFIX.length))
        : "";
      var q = "";
      try {{ q = new URL(location.href).searchParams.get("q") || ""; }} catch (e) {{}}
      var title = (a.getAttribute("title") || a.textContent || "").replace(/\\s+/g, " ").trim();
      send(to, fromRel, q, title);
    }}, true);

    /* ── Cmd/Ctrl+K focus search ── */
    document.addEventListener("keydown", function(e) {{
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {{
        e.preventDefault();
        var input = document.getElementById("search-input");
        if (input) {{ input.focus(); input.select(); }}
      }}
    }});

    /* ── Reading progress bar ── */
    var progressBar = document.getElementById("reading-progress");
    if (progressBar) {{
      var ticking = false;
      window.addEventListener("scroll", function() {{
        if (!ticking) {{
          requestAnimationFrame(function() {{
            var h = document.documentElement.scrollHeight - window.innerHeight;
            progressBar.style.width = h > 0 ? (window.scrollY / h * 100) + "%" : "0";
            ticking = false;
          }});
          ticking = true;
        }}
      }}, {{ passive: true }});
    }}

    (function wikiPreview() {{
      var box = null;
      var timer = null;
      var activeLink = null;
      var cache = {{}};

      function relFromLink(a) {{
        var href = a && a.getAttribute("href") || "";
        if (href.indexOf(PREFIX) !== 0) return "";
        return decodeURIComponent(href.substring(PREFIX.length).split("#")[0].split("?")[0]);
      }}

      function ensureBox() {{
        if (box) return box;
        box = document.createElement("div");
        box.className = "wiki-preview";
        box.innerHTML = '<div class="wiki-preview-title"></div><div class="wiki-preview-meta"></div><div class="wiki-preview-body"></div>';
        document.body.appendChild(box);
        return box;
      }}

      function place(a) {{
        var b = ensureBox();
        var r = a.getBoundingClientRect();
        var top = Math.min(window.innerHeight - b.offsetHeight - 12, r.bottom + 8);
        var left = Math.min(window.innerWidth - b.offsetWidth - 12, Math.max(12, r.left));
        b.style.top = Math.max(12, top) + "px";
        b.style.left = left + "px";
      }}

      function fill(data) {{
        var b = ensureBox();
        b.querySelector(".wiki-preview-title").textContent = data.title || "Preview";
        b.querySelector(".wiki-preview-meta").textContent = data.meta || data.rel || "";
        b.querySelector(".wiki-preview-body").textContent = data.preview || "No summary available yet.";
        b.classList.add("visible");
        if (activeLink) place(activeLink);
      }}

      function hide() {{
        activeLink = null;
        if (timer) window.clearTimeout(timer);
        if (box) box.classList.remove("visible");
      }}

      function show(a) {{
        var rel = relFromLink(a);
        if (!rel) return;
        activeLink = a;
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(function() {{
          if (cache[rel]) {{ fill(cache[rel]); return; }}
          var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW) + "/api/preview?rel=" + encodeURIComponent(rel);
          fetch(url, {{ cache: "force-cache" }})
            .then(function(resp) {{ if (!resp.ok) throw new Error("preview"); return resp.json(); }})
            .then(function(data) {{ cache[rel] = data; if (activeLink === a) fill(data); }})
            .catch(function() {{}});
        }}, 220);
      }}

      document.addEventListener("mouseover", function(ev) {{
        var a = ev.target && ev.target.closest && ev.target.closest("a.internal-link");
        if (a) show(a);
      }});
      document.addEventListener("mouseout", function(ev) {{
        var a = ev.target && ev.target.closest && ev.target.closest("a.internal-link");
        if (a) hide();
      }});
      document.addEventListener("focusin", function(ev) {{
        var a = ev.target && ev.target.closest && ev.target.closest("a.internal-link");
        if (a) show(a);
      }});
      document.addEventListener("focusout", hide);
    }})();

    /* ── Sidebar counts: never block page render; fill when index is ready ── */
    function fillSidebarCounts(counts) {{
      document.querySelectorAll("[data-sidebar-count]").forEach(function(el) {{
        var key = el.getAttribute("data-sidebar-count");
        var n = counts && counts[key];
        if (!n) {{ el.textContent = ""; return; }}
        if (n < 1000) el.textContent = String(n);
        else if (n < 1000000) el.textContent = (n / 1000).toFixed(1).replace(/\\.0$/, "") + "k";
        else el.textContent = (n / 1000000).toFixed(1).replace(/\\.0$/, "") + "M";
      }});
    }}
    function pollSidebarCounts(tries) {{
      if (!document.querySelectorAll("[data-sidebar-count]").length) return;
      var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW) + "/api/sidebar-counts";
      fetch(url, {{ cache: "no-store" }})
        .then(function(resp) {{ return resp.json().then(function(data) {{ return {{ status: resp.status, data: data }}; }}); }})
        .then(function(res) {{
          if (res.data && res.data.counts) fillSidebarCounts(res.data.counts);
          if (res.status === 200) return;
          if (tries > 0) setTimeout(function() {{ pollSidebarCounts(tries - 1); }}, 2500);
        }})
        .catch(function() {{}});
    }}
    pollSidebarCounts(8);

    /* ── Deferred sidebar: page content first; tree/trending fill async ── */
    (function deferredSidebar() {{
      var el = document.getElementById("wiki-sidebar");
      if (!el || el.getAttribute("data-deferred-sidebar") !== "1") return;
      var rel = el.getAttribute("data-current-rel") || "";
      var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW) + "/api/sidebar"
        + (rel ? ("?rel=" + encodeURIComponent(rel)) : "");
      function load(tries) {{
        fetch(url, {{ cache: "no-store" }})
          .then(function(resp) {{ return resp.text().then(function(html) {{ return {{ status: resp.status, html: html }}; }}); }})
          .then(function(res) {{
            if (!res.html) return;
            var wrap = document.createElement("div");
            wrap.innerHTML = res.html.trim();
            var next = wrap.firstElementChild;
            if (next && el.parentNode) {{
              el.parentNode.replaceChild(next, el);
              pollSidebarCounts(4);
            }}
            if (res.status === 202 && tries > 0) {{
              setTimeout(function() {{ load(tries - 1); }}, 2500);
            }}
          }})
          .catch(function() {{
            if (tries > 0) setTimeout(function() {{ load(tries - 1); }}, 2500);
          }});
      }}
      load(8);
    }})();

    /* ── Deferred source list: page paints first; sources fetch async ── */
    (function deferredSources() {{
      var el = document.querySelector("[data-deferred-sources]");
      if (!el) return;
      var rel = el.getAttribute("data-rel") || "";
      var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW)
        + "/api/source-section?rel=" + encodeURIComponent(rel);
      function load(tries) {{
        fetch(url, {{ cache: "no-store" }})
          .then(function(resp) {{ return resp.text().then(function(html) {{ return {{ status: resp.status, html: html }}; }}); }})
          .then(function(res) {{
            if (res.status === 202 && tries > 0) {{ setTimeout(function() {{ load(tries - 1); }}, 2000); return; }}
            if (res.status >= 400 || res.status === 202) throw new Error('Sources unavailable');
            el.innerHTML = (res.html && res.html.trim()) ? res.html : "";
            el.removeAttribute("data-deferred-sources");
          }})
          .catch(function() {{
            if (tries > 0) setTimeout(function() {{ load(tries - 1); }}, 2000);
            else {{
              el.innerHTML = '<p role="alert">Sources could not be loaded. <button type="button">Retry sources</button></p>';
              el.querySelector('button').onclick = function() {{
                el.innerHTML = '<p role="status">Loading sources…</p>';
                load(6);
              }};
            }}
          }});
      }}
      load(6);
    }})();

    /* ── Deferred structured KPI tiles: page paints first; tiles fetch async ── */
    (function deferredStructured() {{
      var el = document.querySelector("[data-deferred-structured]");
      if (!el) return;
      var rel = el.getAttribute("data-rel") || "";
      var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW)
        + "/api/structured-section?rel=" + encodeURIComponent(rel);
      function load(tries) {{
        fetch(url, {{ cache: "no-store" }})
          .then(function(resp) {{ return resp.text().then(function(html) {{ return {{ status: resp.status, html: html }}; }}); }})
          .then(function(res) {{
            if (res.status === 202 && tries > 0) {{ setTimeout(function() {{ load(tries - 1); }}, 2000); return; }}
            el.innerHTML = (res.html && res.html.trim()) ? res.html : "";
            // innerHTML does NOT execute <script> tags. Re-create them and run
            // SEQUENTIALLY, waiting for external (src) scripts to load before the
            // next -- otherwise the inline chart-render fires before Plotly's
            // <script src> has loaded (charts don't appear). Also restores the
            // data-table pagination that ships inline in the fragment.
            (function runScripts(nodes, i) {{
              if (i >= nodes.length) {{ return; }}
              var old = nodes[i], s = document.createElement("script");
              for (var a = 0; a < old.attributes.length; a++) {{ s.setAttribute(old.attributes[a].name, old.attributes[a].value); }}
              s.textContent = old.textContent;
              if (old.src) {{
                s.onload = s.onerror = function() {{ runScripts(nodes, i + 1); }};
                old.parentNode.replaceChild(s, old);
              }} else {{
                old.parentNode.replaceChild(s, old);
                runScripts(nodes, i + 1);
              }}
            }})(Array.prototype.slice.call(el.querySelectorAll("script")), 0);
            el.removeAttribute("data-deferred-structured");
          }})
          .catch(function() {{
            if (tries > 0) setTimeout(function() {{ load(tries - 1); }}, 2000);
            else el.innerHTML = "";
          }});
      }}
      load(6);
    }})();

    /* ── Search typeahead: top-5 browse-jump suggestions as you type ── */
    (function searchSuggest() {{
      var input = document.getElementById("search-input");
      var box = document.getElementById("search-suggest");
      if (!input || !box) return;
      var timer = null, active = -1, curData = {{}};
      function esc(s) {{ var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }}
      // Generic, config-agnostic colour per kind: hash the dimension/folder name to
      // a stable hue -- every dimension the vault defines gets its own colour, no
      // hardcoded per-kind map.
      function hueOf(s) {{ var h = 0; s = s || "page"; for (var i = 0; i < s.length; i++) {{ h = (h * 31 + s.charCodeAt(i)) >>> 0; }} return h % 360; }}
      function chipStyle(kind) {{ var h = hueOf(kind); return 'color:hsl(' + h + ',70%,45%);background:hsla(' + h + ',70%,50%,.14);border-color:hsla(' + h + ',60%,50%,.32)'; }}
      function hide() {{ box.hidden = true; box.innerHTML = ""; active = -1; curData = {{}}; input.setAttribute("aria-expanded", "false"); }}
      function draw() {{
        var cats = (curData.categories || []), res = (curData.results || []);
        if (!cats.length && !res.length) {{ hide(); return; }}
        var html = "";
        if (cats.length) {{
          html += '<div class="sg-cats">' + cats.map(function(c) {{
            var chips = (c.items || []).map(function(it) {{
              return '<a class="sg-chip" href="' + esc(it.route) + '">' + esc(it.label) + '</a>';
            }}).join("");
            return '<div class="sg-cat"><span class="sg-cat-label" style="' + chipStyle(c.kind) + '">'
              + esc(c.kind) + '</span><span class="sg-cat-items">' + chips + '</span></div>';
          }}).join("") + '</div>';
        }}
        if (res.length) {{
          html += '<ul class="sg-results">' + res.map(function(it) {{
            var kchip = '<span class="sg-kind" style="' + chipStyle(it.kind || "page") + '">' + esc(it.kind || "page") + '</span>';
            var dtag = it.date ? '<span class="sg-date">' + esc(it.date) + '</span>' : '';
            var sub = it.sub ? '<span class="sg-sub">' + esc(it.sub) + '</span>' : '';
            return '<li role="option"><a href="' + esc(it.route) + '"><span class="sg-main">'
              + kchip + dtag + '<span class="sg-label">' + esc(it.label) + '</span></span>' + sub + '</a></li>';
          }}).join("") + '</ul>';
        }}
        box.innerHTML = html;
        box.hidden = false; active = -1; input.setAttribute("aria-expanded", "true");
      }}
      function highlight() {{
        var links = box.querySelectorAll("a");
        for (var i = 0; i < links.length; i++) links[i].classList.toggle("sg-active", i === active);
      }}
      input.addEventListener("input", function() {{
        var q = input.value.trim();
        if (timer) clearTimeout(timer);
        if (q.length < 2) {{ hide(); return; }}
        timer = setTimeout(function() {{
          var url = {json.dumps(URL_PATH)} + "/" + encodeURIComponent(VIEW)
            + "/api/suggest?q=" + encodeURIComponent(q);
          fetch(url, {{ cache: "no-store" }})
            .then(function(r) {{ return r.json(); }})
            .then(function(d) {{ curData = d || {{}}; draw(); }})
            .catch(function() {{ hide(); }});
        }}, 150);
      }});
      input.addEventListener("keydown", function(e) {{
        if (box.hidden) return;
        var links = box.querySelectorAll("a");
        if (e.key === "ArrowDown") {{ e.preventDefault(); active = Math.min(active + 1, links.length - 1); highlight(); }}
        else if (e.key === "ArrowUp") {{ e.preventDefault(); active = Math.max(active - 1, 0); highlight(); }}
        else if (e.key === "Enter") {{ if (active >= 0 && links[active]) {{ e.preventDefault(); window.location.href = links[active].getAttribute("href"); }} }}
        else if (e.key === "Escape") {{ hide(); }}
      }});
      document.addEventListener("click", function(e) {{ if (e.target !== input && !box.contains(e.target)) hide(); }});
      input.addEventListener("blur", function() {{ setTimeout(hide, 150); }});
    }})();

    /* ── TOC scroll spy ── */
    var tocLinks = document.querySelectorAll("[data-toc-anchor]");
    if (tocLinks.length) {{
      var anchors = [];
      tocLinks.forEach(function(link) {{
        var id = link.getAttribute("data-toc-anchor");
        var el = document.getElementById(id);
        if (el) anchors.push({{ el: el, link: link }});
      }});
      var spyTicking = false;
      function updateSpy() {{
        var scrollY = window.scrollY + 80;
        var active = null;
        for (var i = anchors.length - 1; i >= 0; i--) {{
          if (anchors[i].el.offsetTop <= scrollY) {{ active = anchors[i]; break; }}
        }}
        tocLinks.forEach(function(l) {{ l.classList.remove("active"); }});
        if (active) active.link.classList.add("active");
      }}
      window.addEventListener("scroll", function() {{
        if (!spyTicking) {{
          requestAnimationFrame(function() {{ updateSpy(); spyTicking = false; }});
          spyTicking = true;
        }}
      }}, {{ passive: true }});
      updateSpy();
    }}

    /* ── Section cards: wrap each H2 + content into a card div ── */
    (function sectionCards() {{
      /* feed pages get the HuggingNews re-layout instead (see _FEED_JS) */
      if (document.body.classList.contains("layout-feed")) return;
      var article = document.querySelector("article");
      if (!article) return;

      /* Empty-<p> cleanup — the markdown library sometimes leaves blank
         paragraphs between H3 sections, which inflates vertical rhythm. */
      var emptyPs = article.querySelectorAll("p");
      for (var ep = 0; ep < emptyPs.length; ep++) {{
        var p = emptyPs[ep];
        if (!p.textContent.trim() && p.children.length === 0) p.remove();
      }}

      /* H2-based sections (briefs, entity pages) */
      var h2s = article.querySelectorAll("h2");
      if (h2s.length) {{
        for (var i = h2s.length - 1; i >= 0; i--) {{
          var h2 = h2s[i];
          /* skip if inside a <details> or already inside a card */
          if (h2.closest && (h2.closest("details") || h2.closest(".story-card"))) continue;
          var card = document.createElement("div");
          card.className = "story-card";
          var sibling = h2.nextElementSibling;
          h2.parentNode.insertBefore(card, h2);
          card.appendChild(h2);
          while (sibling && sibling.tagName !== "H2" && sibling.tagName !== "H1" && sibling.tagName !== "HR"
                 && !(sibling.classList && sibling.classList.contains("story-card"))) {{
            var next = sibling.nextElementSibling;
            card.appendChild(sibling);
            sibling = next;
          }}
        }}
      }}
      /* H3-based story cards (today / daily-cards pages) — each H3 runs until
         the next HR or H2/H1; we wrap the H3 + siblings into a card and remove
         the trailing HR separator so the cards float cleanly. */
      var h3s = article.querySelectorAll("h3");
      if (h3s.length > 1) {{
        for (var j = h3s.length - 1; j >= 0; j--) {{
          var h3 = h3s[j];
          /* skip if inside a <details>, a story-card, or a blockquote */
          if (h3.closest && (h3.closest(".story-card") || h3.closest("details") || h3.closest("blockquote"))) continue;
          var card3 = document.createElement("div");
          card3.className = "story-card story-card-h3";
          var sib = h3.nextElementSibling;
          h3.parentNode.insertBefore(card3, h3);
          card3.appendChild(h3);
          while (sib && sib.tagName !== "H3" && sib.tagName !== "H2" && sib.tagName !== "H1"
                 && !(sib.classList && sib.classList.contains("story-card"))) {{
            var nxt = sib.nextElementSibling;
            if (sib.tagName === "HR") {{ sib.remove(); break; }}
            card3.appendChild(sib);
            sib = nxt;
          }}
        }}
      }}

      /* Eyebrow detection — tag the `_meta:_ ...` paragraph (the slot marker
         emitted by the today.md and today-full.md schemas) with `card-eyebrow`
         so the muted strap-line styling applies only to that line, not to
         arbitrary first-of-type paragraphs. The markdown library renders the
         leading `_meta:_` token as `<em>meta:</em>`, so we sniff for either
         a leading <em> with that text or a raw textContent prefix. */
      var cards = article.querySelectorAll(".story-card-h3");
      for (var c = 0; c < cards.length; c++) {{
        var cardEl = cards[c];
        var paras = cardEl.querySelectorAll(":scope > p");
        for (var pi = 0; pi < paras.length; pi++) {{
          var para = paras[pi];
          var firstEm = para.querySelector(":scope > em:first-child");
          var emText = firstEm ? firstEm.textContent.trim().toLowerCase() : "";
          var textHead = (para.textContent || "").trim().slice(0, 8).toLowerCase();
          if (emText === "meta:" || textHead.indexOf("meta:") === 0) {{
            para.classList.add("card-eyebrow");
            /* hide the literal `meta:` token — it's a slot marker, not content */
            if (firstEm && firstEm.textContent.trim().toLowerCase() === "meta:") {{
              var sib2 = firstEm.nextSibling;
              firstEm.remove();
              /* trim a leading space/colon left over after removing the marker */
              if (sib2 && sib2.nodeType === 3) {{
                sib2.nodeValue = sib2.nodeValue.replace(/^\\s*[:·\\u00b7]?\\s*/, "");
              }}
            }}
            break;  /* only the first matching paragraph gets the class */
          }}
        }}
      }}

      /* Day-digest hoist — the article's first top-level `[!abstract]` callout
         becomes the ICYMI hero. We only hoist if it's the article's first
         meaningful element (heading or callout). */
      var firstAbstract = article.querySelector(":scope > details.callout-abstract");
      if (firstAbstract && !firstAbstract.closest(".day-digest")) {{
        var wrap = document.createElement("div");
        wrap.className = "day-digest";
        firstAbstract.parentNode.insertBefore(wrap, firstAbstract);
        wrap.appendChild(firstAbstract);
        /* default to open — the digest is meant to read at-a-glance */
        firstAbstract.setAttribute("open", "");
      }}

      /* In-card thesis callouts — `> [!summary]` / `> [!tldr]` inside a story
         card are the thesis itself, not a foldable side note. Convert each to
         a visible <div class="card-thesis"> so the prose is shown by default
         AND fully text-selectable (HTML <summary> elements have
         `user-select: none` by browser default, which broke copy / select on
         the thesis title; <div> doesn't). The body content keeps its
         markdown-rendered HTML so links and bold still work. */
      var inCardCallouts = article.querySelectorAll(
        ".story-card-h3 > details.callout-summary, .story-card-h3 > details.callout-tldr"
      );
      for (var k = 0; k < inCardCallouts.length; k++) {{
        var det = inCardCallouts[k];
        var div = document.createElement("div");
        var kind = "summary";
        if (det.classList.contains("callout-tldr")) kind = "tldr";
        div.className = "card-thesis card-thesis-" + kind;
        var childNodes = Array.prototype.slice.call(det.childNodes);
        for (var n = 0; n < childNodes.length; n++) {{
          var node = childNodes[n];
          if (node.nodeType === 1 && node.tagName === "SUMMARY") continue;
          div.appendChild(node);
        }}
        det.parentNode.replaceChild(div, det);
      }}
    }})();
  }})();
  </script>
{_FEED_JS}
</body>
</html>"""


def _meta_link_dirs() -> dict[str, str]:
    """Map frontmatter field keys to their page directories for clickable meta links."""
    out: dict[str, str] = {}
    try:
        dims = dimension_registry.dimensions()
    except Exception:
        return out
    for dim_name, dim in dims.items():
        if not isinstance(dim, dict):
            continue
        page_dir = dim.get("page_dir")
        if not page_dir:
            continue
        ext = dim.get("extraction")
        if isinstance(ext, dict):
            fm_field = str(ext.get("frontmatter_field") or "")
            if fm_field:
                out[fm_field] = str(page_dir)
        # Also map well-known keys
        if dim_name == "senders":
            out["sender"] = str(page_dir)
            out["broker"] = str(page_dir)
        elif dim_name == "tickers":
            out["primary_ticker"] = str(page_dir)
    return out


def source_actions(note: Note) -> str:
    if not reader_contract.is_source_rel(note.rel):
        return ''
    fm = note.frontmatter
    raw = fm.get('raw_path')
    url = fm.get('url') or fm.get('source_url')
    publisher = fm.get('publisher') or fm.get('sender') or 'Publisher'
    if raw and not url and str(raw).lower().endswith(('.md', '.markdown')):
        try:
            with raw_path(raw, ingest_source=fm.get('ingest_source')).open(encoding='utf-8') as fh:
                match = FRONTMATTER_RE.match(fh.read(16384))
            header = match[1] if match else ''
            try:
                metadata = yaml.safe_load(header)
            except yaml.YAMLError:
                # Legacy raw documents did not quote headlines containing colons.
                metadata = dict(re.findall(r'^(url|source):[ \t]*(.*)$', header, re.MULTILINE))
            metadata = metadata if isinstance(metadata, dict) else {}
            url = metadata.get('url')
            publisher = metadata.get('source') or publisher
        except (OSError, ValueError, yaml.YAMLError):
            logging.warning('Source metadata unavailable: %s', note.rel)
    links = []
    if url and urlparse(str(url)).scheme in {'http', 'https'} and urlparse(str(url)).netloc:
        links.append(f'<a href="{html.escape(str(url), quote=True)}" target="_blank" rel="noopener noreferrer">Read original · {html.escape(str(publisher))} ↗</a>')
    if raw:
        href = _raw_open_route(str(raw), fm.get('ingest_source'))
        links.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">Open saved source ↗</a>')
    return '<nav class="source-actions" aria-label="Source documents">' + ' · '.join(links) + '</nav>' if links else ''


def meta_strip(note: Note) -> str:
    fm = dict(note.frontmatter)
    bits: list[str] = []
    # Map meta field keys to dimension page directories for clickable links
    _linkable = _meta_link_dirs()
    for field_spec in reader_contract.meta_fields():
        value = fm.get(field_spec.key)
        if value in (None, ""):
            for fb in field_spec.fallback_keys:
                if fb in fm and fm[fb] not in (None, ""):
                    value = fm[fb]
                    break
        if value in (None, ""):
            continue
        value_str = str(value)
        page_dir = _linkable.get(field_spec.key)
        if page_dir and value_str.strip():
            slug = entity_filename_slug(value_str)
            href = route_for(_default_view_name(), f"{page_dir}/{slug}")
            bits.append(
                f'<span>{html.escape(field_spec.label)} · '
                f'<a class="internal-link" href="{html.escape(href)}">{html.escape(value_str)}</a></span>'
            )
        else:
            bits.append(f'<span>{html.escape(field_spec.label)} · {html.escape(value_str)}</span>')
    return f'<div class="note-meta">{"".join(bits)}</div>' if bits else ""


def wip_html(view: str) -> str:
    css = theme_mod.read_theme()
    default_home = route_for(_default_view_name(), reader_contract.home_page_rel())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(view)} view - building</title>
  <style>{css}</style>
  <style>
    .view-switcher {{ display: inline-flex; gap: 4px; align-items: center; }}
    .view-tab {{ padding: 4px 12px; border-radius: 999px; font-size: 12.5px; color: var(--text-muted); border: 1px solid transparent; }}
    .view-tab.active {{ background: var(--bg-elev); color: var(--text); border-color: var(--border-strong); font-weight: 600; }}
    .wip-shell {{ min-height: 100vh; display: grid; place-items: center; padding: 40px 20px; background: var(--bg); }}
    .wip-card {{ max-width: 560px; width: 100%; padding: 36px 32px; background: var(--bg-elev); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); text-align: center; }}
    .wip-mark {{ font-size: 36px; color: var(--accent); margin-bottom: 14px; }}
    .wip-title {{ font-size: 22px; font-weight: 700; margin: 0 0 8px; }}
    .wip-sub {{ font-size: 14px; color: var(--text-muted); line-height: 1.55; margin: 0 0 20px; }}
    .wip-tabs {{ margin: 22px 0 6px; display: flex; justify-content: center; }}
    .wip-back {{ display: inline-block; margin-top: 14px; padding: 8px 18px; border-radius: 999px; background: var(--accent); color: #fff !important; font-size: 13px; font-weight: 600; }}
  </style>
{dr_theme.head_tags()}
</head>
<body>
  <main class="wip-shell">
    <div class="wip-card">
      <div class="wip-mark">*</div>
      <h1 class="wip-title">{html.escape(view)} view is being built</h1>
      <p class="wip-sub">This view runs its own synthesis pass against the shared raw vault. We do not show placeholder content until the view-specific pass is ready.</p>
      <p class="wip-sub">Refresh after the build completes; the watcher will keep it fresh after that.</p>
      <div class="wip-tabs">{view_switcher(view, None)}</div>
      <a class="wip-back" href="{default_home}">Go to the default view</a>
    </div>
  </main>
</body>
</html>"""


def wip_json(view: str) -> dict:
    return {"view": view, "status": "building", "detail": "view-specific synth has not completed yet"}


# Server-wide toggle for the structured-data KPI tiles. In-memory only, shared
# across all requests/threads in this process, and flipped via the
# {URL_PATH}/_structured route.
_STRUCTURED_ENABLED = True


def structured_enabled() -> bool:
    return _STRUCTURED_ENABLED


def set_structured_enabled(on: bool) -> None:
    global _STRUCTURED_ENABLED
    _STRUCTURED_ENABLED = bool(on)


def _inject_after_h1(rendered: str, block: str) -> str:
    """Insert an HTML block right after the article's first </h1> (else prepend)."""
    idx = rendered.find("</h1>")
    if idx == -1:
        return block + rendered
    cut = idx + len("</h1>")
    return rendered[:cut] + block + rendered[cut:]


def _kick_page_revalidate(view: ViewState, view_name: str, rel: str) -> None:
    """Stale-while-revalidate: refresh a stale page_cache entry in the background
    so the user gets the last good HTML instantly. Single-flighted per rel."""
    with view.render_locks_guard:
        if rel in view.page_bg_refreshing:
            return
        view.page_bg_refreshing.add(rel)

    def _run() -> None:
        try:
            render_page(view_name, rel, _revalidate=True)
        except Exception:
            logging.warning("argus_wiki page revalidate failed view=%s rel=%s", view_name, rel, exc_info=True)
        finally:
            with view.render_locks_guard:
                view.page_bg_refreshing.discard(rel)

    threading.Thread(target=_run, name="argus-wiki-revalidate", daemon=True).start()


def render_page(view_name: str, rel: str, page: str | int | None = "1", *, _revalidate: bool = False) -> tuple[str, int]:
    started = time.time()
    timings: list[tuple[str, float]] = []

    def mark(label: str) -> None:
        timings.append((label, time.time() - started))

    view = get_view(view_name)
    if not is_ready(view):
        return wip_html(view_name), 503
    clean_rel = unquote(rel).replace("\\", "/").strip("/")
    if clean_rel and not Path(clean_rel).suffix:
        folder_path = _folder_path(view, clean_rel)
        try:
            if folder_path is not None and folder_path.is_dir():
                return render_folder(view_name, clean_rel, page)
        except OSError as exc:
            logging.warning("argus_wiki folder path check failed view=%s rel=%s path=%r error=%s diagnostics=%s",
                            view_name, rel, str(folder_path), exc, view_diagnostics(view))
            raise
    path = vault_path(view, rel)
    page_rel_override = None
    # Source-index (`<entity>.sources[.YYYY]`) pages are paginated by year. Build
    # one on demand when it's missing OR still in the old un-paginated format
    # (no per-year nav); once written it serves from disk (page-cached) and synth
    # keeps its data fresh. Detecting the old format upgrades legacy files lazily
    # without re-rendering an already-paginated page on every view.
    from argus_wiki.render import entity_source_sections as _ess
    if _ess.is_source_index_rel(clean_rel):
        needs_gen = True
        try:
            if path.is_file():
                needs_gen = "Browse by year" not in path.read_text(
                    encoding="utf-8", errors="replace")[:4000]
        except OSError:
            needs_gen = True
        if needs_gen:
            gen_path, gen_rel = _source_index_fallback_path(view, rel)
            if gen_path is not None:
                path, page_rel_override = gen_path, gen_rel
    try:
        exists = page_rel_override is not None or path.exists()
    except OSError as exc:
        logging.warning("argus_wiki page path check failed view=%s rel=%s path=%r error=%s diagnostics=%s",
                        view_name, rel, str(path), exc, view_diagnostics(view))
        raise
    if not exists:
        identifier_rel = _identifier_redirect_rel(
            clean_rel,
            Path(clean_rel).stem.replace("_", " "),
            view.wiki_root,
        )
        if identifier_rel:
            return route_for(view_name, identifier_rel), 302
        fallback_path, fallback_rel = _source_index_fallback_path(view, rel)
        fb_kind = "source_index"
        if fallback_path is None:
            fallback_path, fallback_rel = _source_fallback_path(view, rel)
            fb_kind = "source"
        if fallback_path is None:
            fallback_path, fallback_rel = _default_fallback_path(view, rel)
            fb_kind = "default"
        if fallback_path is None:
            fallback_path, fallback_rel = _public_markdown_fallback_path(view, rel)
            fb_kind = "public"
        if fallback_path is None:
            raise FileNotFoundError(rel)
        logging.info("argus_wiki %s fallback view=%s rel=%s -> %s", fb_kind, view_name, rel, fallback_path)
        path = fallback_path
        page_rel_override = fallback_rel
    page_rel = page_rel_override or path.relative_to(view.wiki_root).as_posix()
    note = ensure_note_fresh(view, path, page_rel)
    mark("read")
    ticker_rel = _canonical_entity_redirect_rel(note.rel, note.frontmatter, view.wiki_root) or _singleton_redirect_rel(note.rel, note.frontmatter, view.wiki_root)
    if _cross_dim_redirect_suppressed(note.rel, ticker_rel, note.frontmatter):
        ticker_rel = None  # a synthesized entity page is served, not bounced to another dimension
    if ticker_rel:
        return route_for(view_name, ticker_rel), 302
    mark("redirect")
    # Cache lookup BEFORE the expensive render. The manifest signature bumps on
    # EVERY ingest (sub-minute), so keying a page's cached HTML on it busts the
    # cache constantly. That is only correct for pages that render a manifest-backed
    # source list SYNCHRONOUSLY -- source-index pages (is_source_index_rel), which
    # `_defer_note_sources` deliberately does NOT defer. Entity/today pages DEFER
    # their source list (fetched async via render_source_section), so their cached
    # HTML is manifest-independent; keying them on `search_records_sig` (bumped only
    # when the index is rebuilt in _apply_index_maps -- also exactly when backlinks
    # change) keeps them correct AND lets them serve cached across the ingest stream
    # instead of re-rendering every few minutes. Source pages stay mtime-only.
    # `_defer_note_sources`/hydrate preserve note.mtime, so the mtime used here
    # matches the mtime stored on a miss.
    if reader_contract.is_source_rel(note.rel):
        sig = 0.0
    else:
        try:
            from argus_wiki.render import entity_source_sections as _ess
            embeds_manifest = _ess.is_source_index_rel(note.rel, note.frontmatter)
        except Exception:
            embeds_manifest = True  # unknown -> conservative: keep the manifest sig
        sig = _manifest_sig() if embeds_manifest else 0.0
    struct_on = structured_enabled()
    _structured = None
    struct_mtime = 0.0
    if struct_on:
        try:
            from argus_wiki.render import structured_sections as _structured  # type: ignore
            struct_mtime = _structured.structured_mtime(note.rel)
        except Exception:
            _structured = None
            struct_mtime = 0.0
    cache_key = f"{note.rel}|{view.search_records_sig}|{sig}|{struct_mtime}|{int(struct_on)}"
    cached_page = view.page_cache.get(cache_key)
    if cached_page and cached_page[0] == note.mtime and not _revalidate:
        elapsed = time.time() - started
        if elapsed > 1.0:
            logging.info("argus_wiki page cache hit slow view=%s rel=%s elapsed=%.2fs timings=%s",
                         view_name, note.rel, elapsed, timings)
        return cached_page[2], cached_page[1]
    # Stale-while-revalidate: entity/today pages are rewritten by the watcher every
    # few minutes, so their cached entry goes stale (mtime mismatch) constantly.
    # Rather than make the user wait on a cold re-render, serve the last good HTML
    # INSTANTLY (lock-free) and refresh in the background. Only when we already have
    # a prior render, for regular non-source, non-source-index, first-page requests;
    # everything else renders synchronously below.
    if (
        cached_page is not None
        and not _revalidate
        and str(page or "1") == "1"
        and not reader_contract.is_source_rel(note.rel)
    ):
        try:
            from argus_wiki.render import entity_source_sections as _ess2
            _is_srcidx = _ess2.is_source_index_rel(note.rel, note.frontmatter)
        except Exception:
            _is_srcidx = False
        if not _is_srcidx:
            _kick_page_revalidate(view, view_name, rel)
            return cached_page[2], cached_page[1]
    # Page-level single-flight (per rel): coalesce concurrent renders of the same
    # page so read+render+backlinks+layout run ONCE, not N times in parallel and
    # starving the GIL. today.md is hit by many clients + the watcher at once; the
    # inner render_cached lock only guards markdown, so backlinks/layout stampeded.
    lock_rel = note.rel
    with view.render_locks_guard:
        plock = view.page_render_locks.setdefault(lock_rel, threading.Lock())
    with plock:
        cached_page = view.page_cache.get(cache_key)
        if cached_page and cached_page[0] == note.mtime and not _revalidate:
            return cached_page[2], cached_page[1]
        note, deferred_sources = _defer_note_sources(view, note)
        mark("note")
        indexes = _indexes_for_page_render(view, note)
        mark("indexes")
        rendered, toc = render_cached(view, note, indexes.get("lookup", {}))
        mark("render")
        if _structured is not None and struct_mtime:
            # Defer the KPI tiles like the source list: the page paints now; the
            # tiles (read+parse of the entity's dataset files, tens of seconds on
            # prod) load async via /api/structured-section. Placeholder goes where
            # the tiles used to be injected (after the H1).
            rendered = _inject_after_h1(rendered, _deferred_structured_block(note.rel))
        mark("structured")
        if deferred_sources:
            rendered = rendered + _deferred_sources_block(note.rel)
        feed_data = ""
        _fm = note.frontmatter or {}
        # The feed layout needs the source map; non-feed daily briefs (today.md)
        # need it too so the cite-rewrite pass can show short senders + "Xh ago" chips.
        if str(_fm.get("layout") or "") == "feed" or str(_fm.get("type") or "") == "daily_document_brief":
            # Foreground render: cache-only (no per-source SMB, ~76s on today).
            # The background SWR revalidate does the real reads and fills the cache
            # so the citation chips are present on the cached HTML it stores.
            feed_data = _feed_source_data_script(note.text, cache_only=not _revalidate)
        mark("feed_data")
        content = f'{meta_strip(note)}{source_actions(note)}{page_mtime_line(note)}{tag_pills(note.tags)}{feed_data}<article>{rendered}</article>'
        bl_html = backlinks(view, note, indexes.get("backlinks", {}))
        mark("backlinks")
        right = '<h4>On this page</h4>' + toc_html(toc) + '<h4>Backlinks</h4>' + bl_html
        page_html = layout(view_name, note.title, content, sidebar_lazy(view_name, note.rel), right, page_rel=note.rel,
                           body_layout=(note.frontmatter or {}).get("layout"))
        mark("layout")
        if len(view.page_cache) > 512:
            view.page_cache.clear()
        view.page_cache[cache_key] = (note.mtime, 200, page_html)
    elapsed = time.time() - started
    if elapsed > 1.0:
        logging.info("argus_wiki page render slow view=%s rel=%s elapsed=%.2fs timings=%s",
                     view_name, note.rel, elapsed, timings)
    return page_html, 200


def _folder_card(note: Note) -> str:
    title = _note_list_title(note)
    subtitle = ""
    if note.title and note.title != title:
        subtitle = f'<span class="folder-card-subtitle">{html.escape(note.title)}</span>'
    meta = (
        f'<span class="folder-card-meta">{html.escape(format_note_mtime(note.mtime))}</span>'
        if note.mtime > 0
        else ""
    )
    return (
        f'<a class="folder-card" href="{note.route}">'
        f'<span class="folder-card-title">{html.escape(title)}</span>'
        f'{subtitle}'
        f'{meta}</a>'
    )


def _display_name(name: str) -> str:
    text = re.sub(r"[_-]+", " ", name).strip()
    return text.title() if text else name


def _folder_label(clean: str) -> str:
    if clean == PUBLIC_PAGE_DIR:
        return "Contributor Pages"
    if "/" not in clean:
        return dimension_registry.folder_label(clean)
    return _display_name(clean.rsplit("/", 1)[-1])


def _folder_location(view: ViewState, clean: str) -> tuple[Path, str] | None:
    parts = [part for part in clean.replace("\\", "/").strip("/").split("/") if part]
    if not parts or ".." in parts:
        return None
    rel = "/".join(parts)
    rels = [rel]
    if parts[0] != PUBLIC_PAGE_DIR:
        rels.append(f"{PUBLIC_PAGE_DIR}/{rel}")
    for candidate_rel in rels:
        roots = _storage_roots_for_rel(view, candidate_rel)
        for candidate_root in roots:
            try:
                root = candidate_root.resolve()
                path = (candidate_root / candidate_rel).resolve()
            except OSError:
                continue
            if (root == path or root in path.parents) and path.is_dir():
                return path, candidate_rel
    return None


def _folder_path(view: ViewState, clean: str) -> Path | None:
    location = _folder_location(view, clean)
    return location[0] if location else None


def _storage_roots_for_rel(view: ViewState, rel: str) -> list[Path]:
    clean = rel.replace("\\", "/").strip("/")
    if view.is_default:
        return [view.wiki_root]
    default_root = reader_contract.default_view_wiki_root()
    if clean == PUBLIC_PAGE_DIR or clean.startswith(f"{PUBLIC_PAGE_DIR}/"):
        return [default_root, view.wiki_root]
    return [view.wiki_root, default_root]


def _public_markdown_fallback_path(view: ViewState, rel: str) -> tuple[Path | None, str | None]:
    parts = [part for part in rel.replace("\\", "/").strip("/").split("/") if part]
    if not parts or ".." in parts or parts[0] == PUBLIC_PAGE_DIR:
        return None, None
    clean = "/".join(parts)
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    storage_rel = f"{PUBLIC_PAGE_DIR}/{clean}"
    for candidate_root in _storage_roots_for_rel(view, storage_rel):
        try:
            root = candidate_root.resolve()
            path = (candidate_root / storage_rel).resolve()
        except OSError:
            continue
        if (root == path or root in path.parents) and path.is_file():
            return path, storage_rel
    return None, None


def _public_storage_rel_for_content(view: ViewState, rel: str) -> str | None:
    parts = [part for part in rel.replace("\\", "/").strip("/").split("/") if part]
    if not parts or ".." in parts or parts[0] == PUBLIC_PAGE_DIR:
        return None
    clean = "/".join(parts)
    storage_rel = f"{PUBLIC_PAGE_DIR}/{clean}"
    for candidate_root in _storage_roots_for_rel(view, storage_rel):
        try:
            root = candidate_root.resolve()
            path = (candidate_root / storage_rel).resolve()
        except OSError:
            continue
        if (root == path or root in path.parents) and path.is_file():
            return storage_rel
    return None


def _format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{size} bytes"


def _child_folder_card(view_name: str, clean: str, path: Path) -> str:
    rel = f"{clean.rstrip('/')}/{path.name}".strip("/")
    try:
        child_count = sum(1 for p in path.iterdir() if not p.name.startswith("."))
    except OSError:
        child_count = 0
    meta = f"{child_count} item{'s' if child_count != 1 else ''}"
    return (
        f'<a class="folder-card" href="{route_for(view_name, _visible_rel(rel))}">'
        f'<span class="folder-card-title">{html.escape(_display_name(path.name))}</span>'
        f'<span class="folder-card-subtitle">Folder</span>'
        f'<span class="folder-card-meta">{html.escape(meta)}</span></a>'
    )


def _file_card(view_name: str, clean: str, path: Path) -> str:
    rel = f"{clean.rstrip('/')}/{path.name}".strip("/")
    try:
        meta = _format_file_size(path.stat().st_size)
    except OSError:
        meta = "File"
    suffix = path.suffix.upper().lstrip(".") or "File"
    return (
        f'<a class="folder-card" href="{_content_route(view_name, _visible_rel(rel))}">'
        f'<span class="folder-card-title">{html.escape(path.stem.replace("_", " "))}</span>'
        f'<span class="folder-card-subtitle">{html.escape(suffix)} file</span>'
        f'<span class="folder-card-meta">{html.escape(meta)}</span></a>'
    )


def _folder_intro_note(view: ViewState, clean: str, root: Path) -> Note | None:
    for name in FOLDER_INTRO_FILES:
        path = root / name
        if not path.is_file():
            continue
        rel = f"{clean.rstrip('/')}/{path.name}".strip("/")
        try:
            return ensure_note_fresh(view, path, rel)
        except OSError:
            return None
    return None


def _direct_child_dirs(root: Path) -> list[Path]:
    try:
        return sorted(
            [path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")],
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []


def _direct_child_files(root: Path) -> list[Path]:
    try:
        return sorted(
            [
                path for path in root.iterdir()
                if path.is_file()
                and not path.name.startswith(".")
                and not path.name.lower().endswith(".md")
                and not is_sharepoint_conflict_path(path.name)
            ],
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []


def _folder_notes_from_root(view: ViewState, clean: str, root: Path) -> list[Note]:
    try:
        paths = sorted(
            [path for path in root.iterdir() if path.is_file() and path.name.lower().endswith(".md")],
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []
    notes: list[Note] = []
    for path in paths:
        rel = f"{clean.rstrip('/')}/{path.name}".strip("/")
        if is_sharepoint_conflict_path(rel):
            continue
        try:
            notes.append(ensure_note_fresh(view, path, rel))
        except (OSError, ValueError):
            continue
    return notes


def _folder_intro_html(view: ViewState, note: Note | None, indexes: dict[str, Any] | None) -> str:
    if note is None:
        return ""
    lookup = (indexes or _single_note_index(note)).get("lookup", {})
    rendered, _toc = render_cached(view, note, lookup)
    return f'<article>{rendered}</article>'


def _note_in_folder(note: Note, folder: str) -> bool:
    clean = folder.replace("\\", "/").strip("/")
    if "/" not in clean:
        return note.folder == clean
    return Path(note.rel).parent.as_posix() == clean


def _page_number(raw: str | int | None) -> int:
    try:
        return max(int(str(raw or "1")), 1)
    except ValueError:
        return 1


def _paginate(items: list, page: str | int | None, page_size: int) -> tuple[list, int, int, int]:
    total = len(items)
    total_pages = max((total + page_size - 1) // page_size, 1)
    current = min(_page_number(page), total_pages)
    start = (current - 1) * page_size
    return items[start:start + page_size], current, total_pages, total


def _folder_pagination_html(
    view_name: str,
    storage_clean: str,
    current: int,
    total_pages: int | None,
    total: int | None,
    has_next: bool = False,
) -> str:
    if (total_pages is None or total_pages <= 1) and not has_next and current <= 1:
        return ""
    visible = _visible_rel(storage_clean) or storage_clean
    base = route_for(view_name, visible)
    prev_link = (
        f'<a href="{html.escape(base)}?page={current - 1}">Previous</a>'
        if current > 1 else ""
    )
    next_page = current + 1 if total_pages is None else min(current + 1, total_pages)
    next_link = (
        f'<a href="{html.escape(base)}?page={next_page}">Next</a>'
        if has_next or (total_pages is not None and current < total_pages) else ""
    )
    label = f"Page {current}" if total_pages is None else f"Page {current} of {total_pages}"
    total_label = f'<span>{total:,} items</span>' if total is not None else ""
    return (
        '<nav class="folder-pagination" aria-label="Folder pagination">'
        f'{prev_link}'
        f'<span class="current">{label}</span>'
        f'{total_label}'
        f'{next_link}'
        '</nav>'
    )


def render_folder(view_name: str, folder: str, page: str | int | None = "1") -> tuple[str, int]:
    started = time.time()
    timings: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        timings.append((name, time.time() - started))

    view = get_view(view_name)
    if not is_ready(view):
        return wip_html(view_name), 503
    clean = unquote(folder).strip("/")
    location = _folder_location(view, clean)
    if location is None:
        raise FileNotFoundError(folder)
    root, storage_clean = location
    indexes = view.index_result
    public_folder = storage_clean == PUBLIC_PAGE_DIR or storage_clean.startswith(f"{PUBLIC_PAGE_DIR}/")
    # A source folder is the source dir itself OR any date-bucket beneath it
    # (e.g. ``sources/2026-06-04``); none of these are in the note index.
    source_dirs = set(reader_contract.source_page_dirs())
    source_root = bool(storage_clean) and storage_clean.split("/", 1)[0] in source_dirs
    # Entity folders: ES-first listing (with disk reconcile) so browse stays
    # fast AND shows pages the watcher has not projected yet. Kick a cheap ES
    # freshness check so folder browse never sits on a stale in-memory index.
    direct_page = source_root or not public_folder
    if indexes is not None and not source_root:
        _maybe_reload_entity_index(view)
    mark("freshness")
    notes = _folder_notes_from_root(view, storage_clean, root) if not direct_page else []
    intro_note = _folder_intro_note(view, storage_clean, root) if public_folder else None
    intro_names = set(FOLDER_INTRO_FILES)
    if direct_page:
        visible_notes, current_page, has_next = folder_notes_direct_page(view, storage_clean, page, FOLDER_PAGE_SIZE)
        total_pages = None
        total_notes = None
    else:
        allowed = _folder_index_values(storage_clean)
        folder_notes = sorted(
            [
                note for note in notes
                if _note_in_folder(note, storage_clean)
                and Path(note.rel).name.lower() not in intro_names
                and (allowed is None or Path(note.rel).stem.casefold() in allowed)
            ],
            key=lambda note: _note_list_title(note).lower(),
        )
        visible_notes, current_page, total_pages, total_notes = _paginate(folder_notes, page, FOLDER_PAGE_SIZE)
        has_next = False
    mark("notes")
    child_dirs = _direct_child_dirs(root) if public_folder else []
    child_files = _direct_child_files(root) if public_folder else []
    folder_cards = "".join(_child_folder_card(view_name, storage_clean, path) for path in child_dirs)
    page_cards = "".join(_folder_card(note) for note in visible_notes)
    file_cards = "".join(_file_card(view_name, storage_clean, path) for path in child_files)
    pagination = _folder_pagination_html(view_name, storage_clean, current_page, total_pages, total_notes, has_next)
    sections = []
    if folder_cards:
        sections.append(f'<h2 class="folder-section-title">Folders</h2><div class="folder-grid">{folder_cards}</div>')
    if page_cards:
        sections.append(f'<h2 class="folder-section-title">Pages</h2>{pagination}<div class="folder-grid">{page_cards}</div>{pagination}')
    if file_cards:
        sections.append(f'<h2 class="folder-section-title">Files</h2><div class="folder-grid">{file_cards}</div>')
    cards = "".join(sections)
    label = _folder_label(_visible_rel(storage_clean) or storage_clean)
    empty_msg = '<p class="muted">No notes here yet.</p>'
    counts = []
    if child_dirs:
        counts.append(f"{len(child_dirs)} folder{'s' if len(child_dirs) != 1 else ''}")
    note_label = "page" if public_folder else "note"
    if total_notes is None:
        shown_notes = (current_page - 1) * FOLDER_PAGE_SIZE + len(visible_notes)
        counts.append(f"{shown_notes}{'+' if has_next else ''} {note_label}{'s' if shown_notes != 1 or has_next else ''}")
    else:
        counts.append(f"{total_notes} {note_label}{'s' if total_notes != 1 else ''}")
    if child_files:
        counts.append(f"{len(child_files)} file{'s' if len(child_files) != 1 else ''}")
    intro = _folder_intro_html(view, intro_note, indexes)
    structured_overview = ""
    if structured_enabled():
        try:
            from argus_wiki.render import structured_sections as _structured
            structured_overview = _structured.render_structured_overview_html(storage_clean, view_name)
        except Exception as exc:
            logging.warning("argus_wiki structured folder render failed folder=%s: %s", storage_clean, exc)
    mark("structured")
    content = (
        f'<article><h1>{html.escape(label)}</h1>'
        f'<p class="muted">{" · ".join(counts)}</p>{structured_overview}</article>'
        f'{intro}'
        f'{cards or empty_msg}'
    )
    result = layout(view_name, label, content, sidebar_lazy(view_name, storage_clean), page_rel=storage_clean + "/")
    mark("layout")
    if time.time() - started > 1.0:
        logging.info("argus_wiki folder render slow view=%s folder=%s timings=%s", view_name, storage_clean, timings)
    return result, 200


SEARCH_TOP_K = 80
SEARCH_ENTITY_TOP_N = 5
SEARCH_ENTITY_SECTION_LIMIT = 5
SEARCH_PAGE_SIZE = 25
TOP_MATCH_LIMIT = 5
HIGH_MATCH_QUALITY = 0.72
SEARCH_TERM_STOPWORDS = {"a", "an", "and", "for", "of", "the", "to", "with"}
FOLDER_PAGE_SIZE = 120


def _search_terms(query: str) -> list[str]:
    return [
        part.lower()
        for part in re.findall(r"[A-Za-z0-9]+", query or "")
        if part and part.lower() not in SEARCH_TERM_STOPWORDS
    ]


def _bounded_edit_distance(a: str, b: str, max_distance: int) -> int:
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    prevprev: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        row_min = cur[0]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if prevprev is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prevprev[j - 2] + 1)
            row_min = min(row_min, cur[j])
        if row_min > max_distance:
            return max_distance + 1
        prevprev, prev = prev, cur
    return prev[-1]


def _search_label_parts(*values: str) -> list[str]:
    parts: list[str] = []
    for value in values:
        text = str(value or "")
        for part in [text, *re.split(r"[\s_./\\-]+", text)]:
            clean = squash(part)
            if len(clean) >= 4 and clean not in parts:
                parts.append(clean)
    return parts


def _search_typo_score(query: str, *values: str) -> float:
    q = squash(query)
    if len(q) < 4:
        return 0.0
    max_distance = 1 if len(q) <= 6 else 2
    best = 0.0
    for candidate in _search_label_parts(*values):
        if candidate == q:
            continue
        distance = _bounded_edit_distance(q, candidate, max_distance)
        if distance <= max_distance:
            best = max(best, 620 - distance * 140 - abs(len(candidate) - len(q)) * 25)
    return best


def _typo_candidate_stems(term: str) -> list[str]:
    clean = re.sub(r"[^A-Za-z0-9]+", "", str(term or ""))
    if len(clean) < 4 or len(clean) > 12:
        return []
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    lower = clean.lower()
    variants = {lower}
    for i in range(len(lower)):
        variants.add(lower[:i] + lower[i + 1:])
        for ch in alphabet:
            variants.add(lower[:i] + ch + lower[i + 1:])
    for i in range(len(lower) + 1):
        for ch in alphabet:
            variants.add(lower[:i] + ch + lower[i:])
    for i in range(len(lower) - 1):
        variants.add(lower[:i] + lower[i + 1] + lower[i] + lower[i + 2:])
    stems: list[str] = []
    for variant in variants:
        for candidate in (variant.capitalize(), variant.upper(), variant):
            stem = entity_filename_slug(candidate)
            if stem and stem not in stems:
                stems.append(stem)
    return stems[:1200]


def _search_page_number(value: int | str | None) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _search_page_href(view: str, q: str, page: int) -> str:
    href = f"{_search_route(view)}?q={quote(q)}"
    if page > 1:
        href += f"&page={page}"
    return href


def _short_token_entity_search(query: str) -> bool:
    terms = _search_terms(query)
    return len(terms) == 1 and len(terms[0]) <= 3


def _source_like_search_query(query: str) -> bool:
    q = str(query or "").strip().lower()
    terms = _search_terms(q)
    return bool(
        len(q) >= 24
        and (
            "__" in q
            or "src::" in q
            or "src_" in q
            or any(re.fullmatch(r"20\d{6}", term) for term in terms)
            or any(re.fullmatch(r"20\d{2}-\d{2}-\d{2}", term) for term in terms)
        )
    )


def _source_phrase_search_query(query: str) -> bool:
    q = str(query or "").strip()
    terms = _search_terms(q)
    return len(q) >= 18 and len(terms) >= 3


def _source_search_bucket_roots(view: ViewState, query: str) -> list[Path]:
    q = str(query or "")
    buckets: list[str] = []
    for raw in re.findall(r"20\d{6}", q):
        bucket = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if bucket not in buckets:
            buckets.append(bucket)
    for raw in re.findall(r"20\d{2}-\d{2}-\d{2}", q):
        if raw not in buckets:
            buckets.append(raw)
    roots: list[Path] = []
    for source_dir in reader_contract.source_page_dirs():
        base = view.wiki_root / source_dir
        for bucket in buckets:
            root = base / bucket
            try:
                if root.is_dir():
                    roots.append(root)
            except OSError:
                continue
    return roots


def _source_phrase_search_paths(view: ViewState, query: str, limit: int) -> list[Path]:
    if not _source_phrase_search_query(query):
        return []
    terms = [term for term in _search_terms(query) if len(term) >= 3]
    if not terms:
        return []
    roots = []
    for source_dir in reader_contract.source_page_dirs():
        root = view.wiki_root / source_dir
        try:
            if root.is_dir():
                roots.append(root)
        except OSError:
            continue
    if not roots:
        return []
    manifest_paths = _source_manifest_search_paths(view, query, limit)
    if manifest_paths:
        return manifest_paths
    cmd = [
        "rg",
        "--files-with-matches",
        "--ignore-case",
        "--glob",
        "*.md",
        "--max-filesize",
        "5M",
        "--no-messages",
        "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True)),
    ] + [str(root) for root in roots]
    rg_output = ""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
        )
        rg_output = proc.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.info("argus_wiki cold source phrase rg skipped query=%r: %s", query, exc)
    paths: list[Path] = []
    seen: set[str] = set()
    for line in rg_output.splitlines():
        try:
            path = Path(line.strip())
            rel = path.relative_to(view.wiki_root).as_posix()
        except (OSError, ValueError):
            continue
        if rel in seen or is_sharepoint_conflict_path(rel):
            continue
        seen.add(rel)
        paths.append(path)
        if len(paths) >= limit:
            break
    if not paths:
        paths = _source_manifest_search_paths(view, query, limit)
    if not paths:
        paths = _source_phrase_scan_paths(view, query, roots, limit)
    return paths


def _source_manifest_search_paths(view: ViewState, query: str, limit: int) -> list[Path]:
    manifest = config.INGESTED_MANIFEST
    try:
        exists = manifest.is_file()
    except OSError:
        return []
    if not exists:
        return []
    terms = [term for term in _search_terms(query) if len(term) >= 3]
    q = str(query or "").strip().lower()
    q_sq = squash(q)
    rows: list[tuple[int, str, str]] = []
    try:
        fh = manifest.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line_no, line in enumerate(fh):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            page_rel = str(row.get("page_path") or "").replace("\\", "/").strip()
            if not page_rel or not reader_contract.is_source_rel(page_rel):
                continue
            if row.get("is_disclaimer_only"):
                continue
            hay = "\n".join(str(row.get(key) or "") for key in ("title", "raw_path", "source_id", "page_path")).lower()
            score = 0
            if q and q in hay:
                score += 5000
            if q_sq and q_sq in squash(hay):
                score += 3500
            matched = sum(1 for term in terms if term in hay)
            if terms and matched >= max(2, min(len(terms), int(len(terms) * 0.65))):
                score += matched * 80
            score += 750
            if str(row.get("kind") or row.get("doc_kind") or "").lower() == "pdf":
                score += 250
            if score <= 0:
                continue
            rows.append((score, f"{line_no:09d}", page_rel))
    rows.sort(key=lambda item: (-item[0], item[1]))
    paths: list[Path] = []
    for _score, _line_no, page_rel in rows:
        path = view.wiki_root / page_rel
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def _source_phrase_scan_paths(view: ViewState, query: str, roots: list[Path], limit: int) -> list[Path]:
    terms = [term for term in _search_terms(query) if len(term) >= 3]
    q_sq = squash(query)
    out: list[Path] = []
    scanned = 0
    for root in roots:
        try:
            candidates = list(root.rglob("*.md"))
        except OSError:
            continue
        if scanned + len(candidates) > 500:
            return out
        for path in candidates:
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:20000]
                rel = path.relative_to(view.wiki_root).as_posix()
            except (OSError, ValueError):
                continue
            hay = f"{rel}\n{text}".lower()
            if q_sq and q_sq in squash(hay):
                out.append(path)
            elif terms and all(term in hay for term in terms):
                out.append(path)
            if len(out) >= limit:
                return out
    return out


def _note_from_es_hit(hit: dict) -> Note | None:
    """A metadata-only Note from an ES search hit — no SMB read. ``body_loaded``
    is False so the serve path reads the full body on demand when opened; ``text``
    holds the ES highlight snippet for the result card."""
    rel = str(hit.get("rel") or "").replace("\\", "/").strip("/")
    if not rel or is_sharepoint_conflict_path(rel):
        return None
    snippet = str(hit.get("snippet") or "")
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    frontmatter = {
        "kind": hit.get("content_kind") or "",
        "day": hit.get("day") or hit.get("date") or "",
        "sender": hit.get("sender") or "",
        "doc_kind": hit.get("doc_kind") or "",
        "ingested_at": hit.get("ingested_at") or "",
        "raw_path": hit.get("raw_path") or "",
        "content_sha256": hit.get("content_sha256") or "",
        "facets": hit.get("facets") or {},
    }
    return Note(
        rel=rel, route=str(hit.get("route") or ""),
        title=str(hit.get("title") or Path(rel).stem),
        folder=folder, mtime=0.0, text=snippet, frontmatter=frontmatter, tags=(),
        text_lower=snippet.lower(), text_squashed=squash(snippet),
        outbound=(), body_loaded=False,
    )


def cold_source_search_notes(view: ViewState, query: str, limit: int = 40) -> list[Note]:
    """Source hits from Elasticsearch (BM25 + recency, hybrid when vectors on) —
    NOT a walk of the SMB share. Order is ES rank; snippets are ES highlights."""
    if not (_source_like_search_query(query) or _source_phrase_search_query(query)):
        return []
    from argus_wiki.search import wiki_api

    hits, _available = wiki_api._fts_source_hits(query, view.name, limit)
    notes: list[Note] = []
    seen: set[str] = set()
    for hit in hits:
        note = _note_from_es_hit(hit)
        if note is None or note.rel in seen:
            continue
        seen.add(note.rel)
        notes.append(note)
    return notes[:limit]


def _entity_search_folders() -> list[str]:
    entity_dirs = set(dimension_registry.entity_page_dirs()) | {"tickers"}
    ordered = [folder for folder in reader_contract.folder_priority() if folder in entity_dirs]
    for folder in sorted(entity_dirs):
        if folder not in ordered:
            ordered.append(folder)
    return ordered or ["tickers", "senders", "companies", "authors", "sectors", "themes"]


def _top_match_quality(note: Note, query: str) -> float:
    q = query.strip().lower()
    if not q:
        return 0.0
    q_sq = squash(q)
    title = (note.title or "").lower()
    title_sq = squash(note.title or "")
    stem = Path(note.rel).stem.lower()
    stem_sq = squash(stem)
    terms = _search_terms(q)
    best = 0.0
    if title == q or stem == q:
        best = 1.0
    elif q_sq and (title_sq == q_sq or stem_sq == q_sq):
        best = 0.95
    elif title.startswith(q) or stem.startswith(q):
        best = 0.85
    elif q_sq and (title_sq.startswith(q_sq) or stem_sq.startswith(q_sq)):
        best = 0.82
    elif q_sq and len(q_sq) > 3 and (q_sq in title_sq or q_sq in stem_sq):
        ratio = len(q_sq) / max(max(len(title_sq), len(stem_sq)), 1)
        best = 0.80 if ratio >= 0.6 else 0.40
    if terms and all(term in title or term in stem for term in terms):
        best = max(best, 0.70)
    elif terms and any(term in title or term in stem for term in terms):
        best = max(best, 0.35)
    typo = _search_typo_score(q, note.title, Path(note.rel).stem)
    if typo >= 480:
        best = max(best, 0.78)
    elif typo > 0:
        best = max(best, 0.55)
    return best


def _pick_top_matches(
    candidates: list[tuple[Note, str, float]],
    query: str,
    limit: int = TOP_MATCH_LIMIT,
) -> list[tuple[Note, str, float]]:
    ranked: list[tuple[Note, str, float, float]] = []
    seen: set[str] = set()
    for note, snippet, raw_score in candidates:
        if note.rel in seen:
            continue
        seen.add(note.rel)
        quality = _top_match_quality(note, query)
        if quality < HIGH_MATCH_QUALITY:
            continue
        ranked.append((note, snippet, float(raw_score or 0.0), quality))
    picked: list[tuple[Note, str, float]] = []
    folders_used: set[str] = set()
    pool = list(ranked)
    while pool and len(picked) < limit:
        pool.sort(
            key=lambda row: (
                -round(row[3], 2),
                1 if (row[0].folder or "") in folders_used else 0,
                -row[2],
                row[0].rel,
            )
        )
        note, snippet, raw_score, _quality = pool.pop(0)
        picked.append((note, snippet, raw_score))
        folders_used.add(note.folder or "")
    return picked


def _strip_and_resort_sections(
    sections: list[tuple[str, list[tuple[Note, str, float]]]],
    skip_rels: set[str],
) -> list[tuple[str, list[tuple[Note, str, float]]]]:
    priority = reader_contract.folder_priority()
    priority_index = {folder: idx for idx, folder in enumerate(priority)}
    out: list[tuple[str, list[tuple[Note, str, float]]]] = []
    for folder, rows in sections:
        kept = [row for row in rows if row[0].rel not in skip_rels]
        if kept:
            out.append((folder, kept))
    out.sort(
        key=lambda item: (
            -item[1][0][2] if item[1] else 0,
            priority_index.get(item[0], len(priority)),
            item[0],
        )
    )
    return out


def _apply_top_match_layout(
    query: str,
    entity_sections: list[tuple[str, list[tuple[Note, str, float]]]],
    matches: list[tuple[Note, str, float]],
) -> tuple[
    list[tuple[Note, str, float]],
    list[tuple[str, list[tuple[Note, str, float]]]],
    list[tuple[Note, str, float]],
]:
    entity_rows = _entity_hits_from_sections(entity_sections)
    top = _pick_top_matches([*entity_rows, *matches], query)
    top_rels = {note.rel for note, _snippet, _score in top}
    sections = _strip_and_resort_sections(entity_sections, top_rels)
    stripped_matches = _dedupe_matches(matches, top_rels)
    return top, sections, stripped_matches


def _search_result(
    query: str,
    entity_sections: list[tuple[str, list[tuple[Note, str, float]]]],
    matches: list[tuple[Note, str, float]],
    **kwargs,
) -> SearchCoreResult:
    top, sections, stripped = _apply_top_match_layout(query, entity_sections, matches)
    return SearchCoreResult(
        entity_sections=sections,
        matches=stripped,
        top_matches=top,
        **kwargs,
    )


def _folder_priority_score(folder: str) -> float:
    """Config-driven folder ranking for entity pages — from the registry's
    ``folder_priority()`` (display.folder_priority / page_directories), never a
    hardcoded entity list. Earlier in priority => higher score; unknown => baseline.
    Specificity (which folders exist, their order) comes from dimensions.yaml."""
    if not folder:
        return 20.0
    try:
        prio = reader_contract.folder_priority()
    except Exception:
        return 20.0
    try:
        idx = prio.index(folder)
    except ValueError:
        return 20.0
    # Map priority position into the historical ~50-70 entity band, top-first.
    return 50.0 + float(len(prio) - idx)


def _entity_search_score(note: Note, query: str, backlinks_map: dict[str, list[Note]] | None = None) -> float:
    if reader_contract.is_source_rel(note.rel):
        return 0.0
    q = query.strip().lower()
    if not q:
        return 0.0
    q_sq = squash(q)
    title = note.title.lower()
    title_sq = squash(note.title)
    stem = Path(note.rel).stem.lower()
    stem_sq = squash(stem)
    rel = note.rel.lower()
    terms = _search_terms(q)
    hay = f"{title}\n{rel}\n{json.dumps(note.frontmatter, default=str).lower()}"
    score = 0.0
    if title == q or stem == q:
        score += 1000
    elif title.startswith(q) or stem.startswith(q):
        score += 760
    elif not _short_token_entity_search(q) and (q in title or q in stem):
        score += 560
    if q_sq:
        short = len(q_sq) <= 3
        if title_sq == q_sq or stem_sq == q_sq:
            score += 900
        elif title_sq.startswith(q_sq) or stem_sq.startswith(q_sq):
            score += 700
        elif not short and (q_sq in title_sq or q_sq in stem_sq):
            score += 520
    if terms and all(term in hay for term in terms):
        score += 260
    elif any(term in hay for term in terms):
        score += 90
    for term in terms:
        term_sq = squash(term)
        if term in title or term in stem:
            score += 180
        elif len(term_sq) > 3 and term_sq and (term_sq in title_sq or term_sq in stem_sq):
            score += 150
    score += _search_typo_score(q, note.title, Path(note.rel).stem)
    score += _folder_priority_score(note.folder)
    if backlinks_map:
        key = Path(note.rel).stem.lower()
        score += min(len(backlinks_map.get(key, [])), 25) * 3
    return score


def _best_entity_matches(
    notes: list[Note],
    query: str,
    backlinks_map: dict[str, list[Note]] | None = None,
    limit: int = SEARCH_ENTITY_TOP_N,
) -> list[tuple[Note, str, float]]:
    rows = []
    for note in notes:
        score = _entity_search_score(note, query, backlinks_map)
        if score <= 0:
            continue
        rows.append((note, "", score))
    rows.sort(key=lambda row: (-row[2], row[0].folder, row[0].title.lower()))
    return rows[:limit]


def _best_entity_match_sections(
    notes: list[Note],
    query: str,
    backlinks_map: dict[str, list[Note]] | None = None,
    per_section_limit: int = SEARCH_ENTITY_SECTION_LIMIT,
    view_name: str | None = None,
) -> list[tuple[str, list[tuple[Note, str, float]]]]:
    # Candidate entity notes come from Elasticsearch (BM25) — NOT an O(N) scan of
    # every in-memory note. Scoring tens of thousands of notes per query was the
    # multi-second search stall; we now only score the small ES candidate set.
    from argus_wiki.search import wiki_api

    by_rel = {note.rel: note for note in notes}
    candidates: list[Note] = []
    if view_name:
        hits, ok = wiki_api._fts_entity_hits(query, view_name, max(per_section_limit * 6, 60))
        for hit in hits:
            note = by_rel.get(str(hit.get("rel") or "").replace("\\", "/").strip("/"))
            if note is not None:
                candidates.append(note)
        if not ok and not candidates and len(notes) <= 1000:
            candidates = notes
    elif len(notes) <= 1000:
        candidates = notes
    by_folder: dict[str, list[tuple[Note, str, float]]] = {}
    for note in candidates:
        score = _entity_search_score(note, query, backlinks_map)
        if score <= 0:
            continue
        by_folder.setdefault(note.folder or "root", []).append((note, "", score))
    priority = reader_contract.folder_priority()
    priority_index = {folder: idx for idx, folder in enumerate(priority)}
    sections: list[tuple[str, list[tuple[Note, str, float]]]] = []
    for folder, rows in by_folder.items():
        rows.sort(key=lambda row: (-row[2], _note_list_title(row[0]).lower(), row[0].rel))
        sections.append((folder, rows[:per_section_limit]))
    sections.sort(
        key=lambda item: (
            -item[1][0][2] if item[1] else 0,
            priority_index.get(item[0], len(priority)),
            item[0],
        )
    )
    return sections


def _search_top_matches_html(rows: list[tuple[Note, str, float]]) -> str:
    if not rows:
        return ""
    items = "".join(search_card(note, snippet) for note, snippet, _score in rows)
    return (
        f'<section class="search-folder search-top"><h2>Top matches'
        f'<span>{len(rows)}</span></h2>'
        f'<div class="results compact">{items}</div></section>'
    )


def _search_entity_sections_html(
    sections: list[tuple[str, list[tuple[Note, str, float]]]],
) -> str:
    blocks = []
    for folder, rows in sections:
        if not rows:
            continue
        items = "".join(search_card(note, snippet) for note, snippet, _score in rows)
        blocks.append(
            f'<section class="search-folder search-entity-top"><h2>'
            f'{html.escape(dimension_registry.folder_label(folder))}<span>{len(rows)}</span></h2>'
            f'<div class="results compact">{items}</div></section>'
        )
    return "".join(blocks)


def _dedupe_matches(
    rows: list[tuple[Note, str, float]],
    skip_rels: set[str] | None = None,
) -> list[tuple[Note, str, float]]:
    out = []
    seen = set(skip_rels or set())
    for note, snippet, score in rows:
        if note.rel in seen:
            continue
        seen.add(note.rel)
        out.append((note, snippet, score))
    return out


def _source_first_matches(
    rows: list[tuple[Note, str, float]],
    source_rels: set[str],
) -> list[tuple[Note, str, float]]:
    if not source_rels:
        return rows
    return sorted(rows, key=lambda row: (row[0].rel not in source_rels, -row[2], row[0].rel))


def _pagination_html(
    view: str,
    q: str,
    page: int,
    total: int,
    page_size: int = SEARCH_PAGE_SIZE,
    filters: dict | None = None,
) -> str:
    if total <= page_size:
        return ""
    page_count = max(1, (total + page_size - 1) // page_size)
    pieces: list[str] = []
    filters = filters or {}
    if page > 1:
        pieces.append(
            f'<a href="{html.escape(_search_href(view, q, since=str(filters.get("since_preset") or ""), facets=filters.get("facets"), page=page - 1))}">Previous</a>'
        )
    pieces.append(f'<span>Page {page} of {page_count}</span>')
    if page < page_count:
        pieces.append(
            f'<a href="{html.escape(_search_href(view, q, since=str(filters.get("since_preset") or ""), facets=filters.get("facets"), page=page + 1))}">Next</a>'
        )
    return '<nav class="search-pagination">' + "".join(pieces) + "</nav>"


def _sync_search_records(view: ViewState, view_name: str) -> None:
    if view.search_records is not None:
        return
    notes = (view.index_result or {}).get("notes") or []
    if not notes:
        return
    records = _search_records_from_notes(notes)
    view.search_records = records
    try:
        from argus_wiki.search import wiki_api

        wiki_api.set_search_records(view_name, records)
        wiki_api.prepare_search_records(records)
    except Exception:
        logging.exception("argus_wiki search record sync failed view=%s", view_name)


def _note_from_hit(view: ViewState, hit: dict) -> Note | None:
    """Build a lightweight Note straight from an ES search hit -- no file read.

    Source pages aren't in the in-memory index, so search used to read each one's
    file (an SMB round-trip PER result -- the search hot spot in py-spy). The ES
    hit already carries title/rel/date, which is everything a result card needs.
    """
    rel = str(hit.get("rel") or "").replace("\\", "/").strip("/")
    if not rel:
        return None
    mtime = 0.0
    ds = str(hit.get("date") or "")
    if len(ds) == 8 and ds.isdigit():
        try:
            mtime = datetime.strptime(ds, "%Y%m%d").timestamp()
        except Exception:
            mtime = 0.0
    snippet = str(hit.get("snippet") or "")
    return Note(
        rel=rel,
        route=route_for(view.name, rel),
        title=str(hit.get("title") or Path(rel).stem),
        folder=rel.split("/", 1)[0] if "/" in rel else "",
        mtime=mtime,
        text=snippet,
        frontmatter={},
        tags=(),
        text_lower=snippet.lower(),
        body_loaded=False,
    )


def _note_for_search_hit(view: ViewState, rel: str, notes: list[Note] | None = None,
                         hit: dict | None = None) -> Note | None:
    clean = str(rel or "").replace("\\", "/").strip("/")
    if not clean:
        return None
    note = view.notes_by_rel.get(clean)
    if note:
        return note
    if notes:
        for candidate in notes:
            if candidate.rel == clean:
                return candidate
    note = _lookup_note_by_stem(view.notes_by_rel, clean)
    if note:
        return note
    # Not in the in-memory index (source pages): build from the ES hit rather
    # than reading the file per match. File read only if we truly have no hit.
    if hit is not None:
        return _note_from_hit(view, hit)
    path = view.wiki_root / clean
    if not path.exists():
        return None
    try:
        return ensure_note_fresh(view, path, clean)
    except OSError:
        return None


def _wiki_search_matches(
    view_name: str,
    notes: list[Note],
    query: str,
    top_k: int = SEARCH_TOP_K,
    *,
    cache_records: bool = True,
) -> list[tuple[Note, str, float]]:
    from argus_wiki.search import wiki_api

    view = get_view(view_name)
    if cache_records:
        _sync_search_records(view, view_name)
    # The active date/facet filters MUST be part of the key: without them a
    # filtered search (e.g. since=today) returns cached unfiltered hits.
    cache_key = (
        view.search_records_sig,
        query.strip().lower(),
        int(top_k),
        wiki_api.active_search_filters_key(),
        wiki_api.search_generation(),
    )
    hits = view.search_result_cache.get(cache_key) if cache_records else None
    if hits is None:
        hits = wiki_api.search(query=query, view=view_name, top_k=top_k)
        if cache_records:
            if len(view.search_result_cache) > 512:
                view.search_result_cache.clear()
            view.search_result_cache[cache_key] = hits
    out = []
    for rank, hit in enumerate(hits):
        rel = str(hit.get("rel") or "")
        note = _note_for_search_hit(view, rel, notes, hit=hit)
        if not note:
            continue
        out.append((note, str(hit.get("snippet") or ""), float(top_k - rank)))
    return out


def _search_records_from_notes(notes: list[Note]) -> list[dict]:
    return [
        {
            "title": note.title,
            "rel": note.rel,
            "route": note.route,
            "text": note.text_lower,
            "frontmatter": note.frontmatter,
        }
        for note in notes
    ]


def cold_search_notes(view: ViewState) -> list[Note]:
    folders: list[str] = []
    for item in reader_contract.structured_nav():
        if item.kind == dimension_registry._VIEW_KIND_FOLDER and item.target not in folders:
            folders.append(item.target)
    for folder in reader_contract.default_fallback_page_dirs():
        if folder not in folders:
            folders.append(folder)
    source_dirs = set(reader_contract.source_page_dirs())
    notes: list[Note] = []
    for folder in folders:
        if folder in source_dirs:
            continue
        root = view.wiki_root / folder
        try:
            paths = list(root.glob("*.md")) if root.exists() and root.is_dir() else []
        except OSError:
            paths = []
        for path in paths:
            try:
                rel = path.relative_to(view.wiki_root).as_posix()
                mtime = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            title = path.stem.replace("_", " ")
            route = route_for(view.name, rel)
            blob = f"{title}\n{rel}"
            notes.append(Note(rel, route, title, folder, mtime, "", {}, (), blob.lower(), squash(blob)))
    return notes


def cold_entity_search_notes(view: ViewState, query: str, limit: int = 100) -> list[Note]:
    q = query.strip()
    if not q:
        return []
    q_sq = squash(q)
    raw_terms = [part.lower() for part in re.findall(r"[A-Za-z0-9]+", q) if part]
    terms = _search_terms(q)
    short_query = _short_token_entity_search(q)
    folders = _entity_search_folders()
    source_dirs = set(reader_contract.source_page_dirs())
    seen: set[str] = set()
    notes: list[Note] = []

    def add_path(path: Path, folder: str) -> None:
        if len(notes) >= limit:
            return
        try:
            rel = path.relative_to(view.wiki_root).as_posix()
        except ValueError:
            return
        rel_key = rel.lower()
        if rel_key in seen or is_sharepoint_conflict_path(rel):
            return
        try:
            note = ensure_note_fresh(view, path, rel)
        except OSError:
            return
        seen.add(rel_key)
        notes.append(note)

    def add_virtual(folder: str, stem: str) -> None:
        if len(notes) >= limit:
            return
        clean = stem.strip().strip("/")
        if not clean:
            return
        rel = f"{folder}/{clean}.md"
        rel_key = rel.lower()
        if rel_key in seen:
            return
        seen.add(rel_key)
        title = clean.replace("_", " ")
        route = route_for(view.name, rel)
        blob = f"{title}\n{rel}"
        notes.append(Note(rel, route, title, folder, time.time(), "", {}, (), blob.lower(), squash(blob)))

    for folder in folders:
        if folder in source_dirs:
            continue
        root = view.wiki_root / folder
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        if folder == "tickers":
            stems = [q.upper(), *(term.upper() for term in terms if len(term) <= 8)]
        elif folder == "companies" and short_query:
            stems = [entity_filename_slug(term) for term in terms if len(term) > 3][:1]
        elif folder == "companies" and len(terms) > 1:
            stems = [entity_filename_slug(q)]
            if terms and len(terms[0]) > 3:
                stems.append(entity_filename_slug(terms[0]))
        else:
            titled = " ".join(part.capitalize() for part in raw_terms)
            stems = [entity_filename_slug(titled), entity_filename_slug(q), q, q.upper()]
            stems.extend(entity_filename_slug(term) for term in terms)
            stems.extend(term.upper() for term in terms if len(term) <= 6)
        seen_stems: set[str] = set()
        for stem in [s for s in stems if s]:
            stem_key = stem.lower()
            if stem_key in seen_stems:
                continue
            seen_stems.add(stem_key)
            if folder == "companies" and (short_query or len(terms) > 1):
                add_virtual(folder, stem)
                continue
            path = root / f"{stem}.md"
            try:
                if path.is_file():
                    add_path(path, folder)
            except OSError:
                continue
        if (
            len(terms) == 1
            and q_sq
            and len(q_sq) >= 4
            and any(note.folder == folder and squash(Path(note.rel).stem) == q_sq for note in notes)
        ):
            return notes
        if (
            folder == "authors"
            and len(terms) > 1
            and any(note.folder == folder and all(term in note.text_lower for term in terms) for note in notes)
        ):
            return notes
        if folder == "companies" and len(q_sq) >= 4:
            before = len(notes)
            for stem in _typo_candidate_stems(terms[0] if terms else q):
                path = root / f"{stem}.md"
                try:
                    if path.is_file():
                        add_path(path, folder)
                except OSError:
                    continue
                if len(notes) > before:
                    return notes
            continue
        if folder == "authors" and len(terms) == 1 and len(q_sq) >= 4:
            patterns = {
                f"*{entity_filename_slug(q)}*.md",
                f"*{q.replace(' ', '_')}*.md",
                f"*{q}*.md",
            }
            for pattern in patterns:
                try:
                    paths = list(root.glob(pattern))
                except OSError:
                    paths = []
                for path in paths[:limit]:
                    add_path(path, folder)
                if notes:
                    return notes
            continue
        if short_query and folder not in {"tickers", "senders"}:
            continue
        if not short_query:
            continue
        try:
            paths = list(root.glob("*.md")) if folder != "companies" else []
        except OSError:
            paths = []
        for path in paths:
            stem_sq = squash(path.stem)
            if q_sq and (
                (len(q_sq) > 3 and q_sq in stem_sq)
                or stem_sq.startswith(q_sq)
                or stem_sq.endswith(q_sq)
                or _search_typo_score(q, path.stem) > 0
            ):
                add_path(path, folder)
            elif not short_query and terms and all(term in path.stem.lower() for term in terms):
                add_path(path, folder)
            if len(notes) >= limit:
                return notes
    return notes


def search_warming_html(view_name: str, q: str) -> str:
    body = (
        f'<article><h1>Search is warming</h1>'
        f'<p class="muted">The wiki search index is building in the background. '
        f'Retry <strong>{html.escape(q)}</strong> in a few seconds.</p></article>'
    )
    return layout(view_name, "Search is warming", body, sidebar_lazy(view_name, ""))


def _search_display_note(note: Note) -> tuple[str, str]:
    try:
        dim_spec = dimension_registry.dimension_for_page_dir(note.folder)
        dim = dimension_registry.dimension(dim_spec.name) if dim_spec else {}
    except (KeyError, AttributeError):
        dim = {}
    display = dim.get("search_display") if isinstance(dim, dict) else None
    if not isinstance(display, dict):
        return _note_list_title(note), note.rel
    label_field = str(display.get("label_field") or "").strip()
    target_dim = str(display.get("target_dimension") or "").strip()
    label = str(note.frontmatter.get(label_field) or "").strip() if label_field else ""
    if not label:
        return _note_list_title(note), note.rel
    target_dir = dimension_registry.page_dir_for(target_dim) if target_dim else ""
    if not target_dir:
        return label, note.rel
    target_rel = f"{target_dir}/{entity_filename_slug(label)}.md"
    return label, target_rel


def _entity_hits_from_sections(
    sections: list[tuple[str, list[tuple[Note, str, float]]]],
) -> list[tuple[Note, str, float]]:
    return [row for _folder, rows in sections for row in rows]


def _api_flat_rows(core: SearchCoreResult) -> list[tuple[Note, str, float]]:
    top = list(core.top_matches)
    top_rels = {note.rel for note, _snippet, _score in top}
    entity_hits = [
        row for row in _entity_hits_from_sections(core.entity_sections)
        if row[0].rel not in top_rels
    ]
    entity_rels = {note.rel for note, _snippet, _score in entity_hits} | top_rels
    matches = _dedupe_matches(core.matches, entity_rels)
    if core.source_rels:
        source_matches = [row for row in matches if row[0].rel in core.source_rels]
        other_matches = [row for row in matches if row[0].rel not in core.source_rels]
        if source_matches and _search_source_first():
            # Raw research is first-class (full-text recall): rank source matches
            # ahead of generic entity pages. Config-gated (search_source_first).
            return [*top, *source_matches, *entity_hits, *other_matches]
        remainder = source_matches + other_matches
    else:
        remainder = matches
    return [*top, *entity_hits, *remainder]


def _search_source_first() -> bool:
    try:
        from dash_reports.sub_servers.argus_wiki_server import config as wiki_cfg

        return wiki_cfg.search_source_first()
    except Exception:
        return True


def _search_core(view_name: str, query: str) -> SearchCoreResult:
    view = get_view(view_name)
    q = query.strip().lower()
    from argus_wiki.index import elastic_search_index

    if elastic_search_index.enabled():
        return _elastic_search_core(view, q)
    if not q:
        from argus_wiki.search import wiki_api

        hits, _available = wiki_api._fts_source_hits(q, view_name, 250)
        matches = [
            (note, str(hit.get("snippet") or ""), float(250 - rank))
            for rank, hit in enumerate(hits)
            if (note := _note_from_es_hit(hit)) is not None
        ]
        source_rels = {note.rel for note, _snippet, _score in matches}
        return _search_result(q, [], matches, source_rels=source_rels)

    if _short_token_entity_search(q):
        notes = cold_entity_search_notes(view, q)
        entity_sections = _best_entity_match_sections(notes, q, {}, view_name=view_name)
        entity_rels = {note.rel for _folder, rows in entity_sections for note, _snippet, _score in rows}
        matches = _dedupe_matches(
            _wiki_search_matches(view_name, notes, q, cache_records=False),
            entity_rels,
        )
        return _search_result(q, entity_sections, matches, fast_entity=True)

    if view.index_result is None:
        source_notes = cold_source_search_notes(view, q)
        source_rels = {note.rel for note in source_notes}
        entity_notes = [] if source_notes else cold_entity_search_notes(view, q)
        notes = [*source_notes, *entity_notes]
        source_matches = [
            (note, "", float(SEARCH_TOP_K + len(source_notes) - idx))
            for idx, note in enumerate(source_notes)
        ]
        entity_sections = _best_entity_match_sections(notes, q, {}, view_name=view_name)
        entity_rels = {note.rel for _folder, rows in entity_sections for note, _snippet, _score in rows}
        matches = _wiki_search_matches(view_name, notes, q, cache_records=False)
        matches = _dedupe_matches(matches, entity_rels)
        matches = [*source_matches, *_dedupe_matches(matches, source_rels)]
        matches = _source_first_matches(matches, source_rels)
        partial = bool(matches or entity_sections)
        if partial:
            schedule_background_scan(view)
        else:
            kick_background_scan(view)
        return _search_result(
            q,
            entity_sections,
            matches,
            warming=not partial,
            partial=partial,
            source_rels=source_rels,
        )

    notes = build_indexes(view).get("notes", [])
    search_notes = cold_entity_search_notes(view, q) if _short_token_entity_search(q) else notes
    matches = _wiki_search_matches(
        view_name,
        search_notes,
        q,
        cache_records=not _short_token_entity_search(q),
    )
    backlinks = view.index_result.get("backlinks", {}) if view.index_result else {}
    entity_sections = _best_entity_match_sections(search_notes, q, backlinks, view_name=view_name)
    entity_rels = {note.rel for _folder, rows in entity_sections for note, _snippet, _score in rows}
    matches = _dedupe_matches(matches, entity_rels)
    # Sources are not in the in-memory index, so the index-based matches above
    # never include them. Scan source bodies from disk (gated to source-like
    # queries inside the helper) and merge, mirroring the cold path.
    source_notes = cold_source_search_notes(view, q)
    found_source_rels = {note.rel for note in source_notes}
    if source_notes:
        source_matches = [
            (note, "", float(SEARCH_TOP_K + len(source_notes) - idx))
            for idx, note in enumerate(source_notes)
        ]
        matches = [*source_matches, *_dedupe_matches(matches, found_source_rels)]
    source_rels = found_source_rels | {
        note.rel for note, _snippet, _score in matches if reader_contract.is_source_rel(note.rel)
    }
    if _source_like_search_query(q):
        matches = _source_first_matches(matches, source_rels)
    return _search_result(
        q,
        entity_sections,
        matches,
        source_rels=source_rels,
        sidebar_notes=notes,
    )


def _elastic_search_core(view: ViewState, query: str) -> SearchCoreResult:
    from argus_wiki.index import elastic_search_index
    from argus_wiki.search import wiki_api

    store = elastic_search_index.store()
    filters = wiki_api._active_search_filters()
    source_hits, source_ok = store.search_with_status(
        query, limit=SEARCH_TOP_K if query else 250, kind="source", wait_for_vectors=False, **filters,
    )
    entity_hits, entity_ok = store.search_with_status(
        query, limit=SEARCH_TOP_K, kind="entity", view=view.name, lexical_only=True, **filters,
    ) if query else ([], True)
    if not source_ok or not entity_ok:
        raise OSError("Elasticsearch search is unavailable")
    source_rows = []
    entity_rows = []
    for hits, rows in ((source_hits, source_rows), (entity_hits, entity_rows)):
        for rank, hit in enumerate(hits):
            hit = {**hit, "route": route_for(view.name, str(hit.get("rel") or ""))}
            note = _note_from_es_hit(hit)
            if note is None:
                continue
            if rows is entity_rows:
                note.frontmatter.update(json.loads(hit.get("fm_json") or "{}"))
            rows.append((note, str(hit.get("snippet") or ""), float(len(hits) - rank)))
    sections = _best_entity_match_sections([note for note, _, _ in entity_rows], query)
    section_rels = {note.rel for _, rows in sections for note, _, _ in rows}
    matches = [*source_rows, *_dedupe_matches(entity_rows, section_rels)]
    return _search_result(query, sections, matches, source_rels={note.rel for note, _, _ in source_rows})


def _search_summary_blocks(
    core: SearchCoreResult,
    *,
    page_rows: list[tuple[Note, str, float]] | None = None,
    page_total: int | None = None,
    pagination: str = "",
    remainder_first: bool = False,
) -> str:
    top_html = _search_top_matches_html(core.top_matches)
    entity_html = _search_entity_sections_html(core.entity_sections)
    rows = page_rows if page_rows is not None else core.matches[:SEARCH_PAGE_SIZE]
    total = len(core.matches) if page_total is None else page_total
    result_block = ""
    if rows:
        result_items = "".join(search_card(note, snippet) for note, snippet, _ in rows)
        result_block = (
            f'<section class="search-folder"><h2>All results<span>{total}</span></h2>'
            f'<div class="results compact">{result_items}</div>{pagination}</section>'
        )
    parts = [top_html]
    if remainder_first and result_block:
        parts.extend([result_block, entity_html])
    else:
        parts.extend([entity_html, result_block])
    blocks = [part for part in parts if part]
    return "".join(blocks) if blocks else '<p class="muted">No matches.</p>'


_SINCE_PRESETS: tuple[tuple[str, str], ...] = (
    ("", "Any time"), ("today", "Today"), ("yesterday", "Yesterday"),
    ("7d", "Past week"), ("30d", "Past month"),
    ("90d", "Past quarter"), ("365d", "Past year"),
)


def _since_cutoff(preset: str) -> str | None:
    from datetime import date, timedelta
    preset = preset or ""
    if preset == "today":
        return date.today().strftime("%Y-%m-%d")
    if preset == "yesterday":
        return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    days = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}.get(preset)
    if not days:
        return None
    return (date.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _until_cutoff(preset: str) -> str | None:
    from datetime import date

    if not preset:
        return None
    return _since_cutoff("yesterday") if preset == "yesterday" else date.today().isoformat()


def parse_search_filters(args) -> dict:
    """Parse date + facet filters from request args. Facet keys are config-driven
    (the dimension registry), so this stays vault-agnostic. ``args`` is a Flask
    MultiDict (or any mapping exposing ``.get`` / ``.getlist``)."""
    from argus_wiki.index import elastic_search_index

    getlist = getattr(args, "getlist", None)
    since_preset = str(args.get("since") or "").strip()
    facets: dict[str, list[str]] = {}
    for key, _label in elastic_search_index.facet_dimensions():
        if getlist:
            vals = [v for v in getlist(f"f_{key}") if v]
        else:
            one = args.get(f"f_{key}")
            vals = [one] if one else []
        if vals:
            facets[key] = vals
    return {
        "since_preset": since_preset,
        "since": _since_cutoff(since_preset),
        "until": _until_cutoff(since_preset),
        "facets": facets,
    }


def _search_href(
    view_name: str,
    q: str,
    *,
    since: str = "",
    facets: dict | None = None,
    page: int = 1,
) -> str:
    from urllib.parse import quote

    parts = [f"q={quote(q)}"]
    if since:
        parts.append(f"since={quote(since)}")
    for dim, vals in (facets or {}).items():
        for v in vals:
            parts.append(f"f_{dim}={quote(str(v))}")
    if page > 1:
        parts.append(f"page={page}")
    return f"{URL_PATH}/{view_name}/search?{'&'.join(parts)}"


def _search_filter_bar(view_name: str, q: str, filters: dict, facet_counts: dict) -> str:
    """A config-driven filter bar: a date-preset row plus one chip row per
    dimension (from the registry) with live ES counts. A selected chip toggles
    off on click. Nothing here names an entity type — it's all dimensions.yaml."""
    from argus_wiki.index import elastic_search_index

    since = str(filters.get("since_preset") or "")
    active = filters.get("facets") or {}
    has_active = bool(since or any(active.values()))
    rows: list[str] = []

    if has_active:
        rows.append(
            f'<div class="fbar-head"><span class="fbar-title">Filters</span>'
            f'<a class="fbar-clear" href="{html.escape(_search_href(view_name, q))}">'
            f'✕ Clear all filters</a></div>'
        )

    date_chips = "".join(
        f'<a class="fchip{" active" if val == since else ""}" '
        f'aria-current="{"true" if val == since else "false"}" '
        f'href="{html.escape(_search_href(view_name, q, since=val, facets=active))}">{label}</a>'
        for val, label in _SINCE_PRESETS
    )
    rows.append(f'<div class="fbar-row"><span class="fbar-label">Date</span>{date_chips}</div>')

    for key, label in elastic_search_index.facet_dimensions():
        vals = facet_counts.get(key) or []
        if not vals:
            continue
        sel = set(active.get(key) or [])
        chips = []
        for value, count in vals[:8]:
            is_sel = value in sel
            nf = {k: list(v) for k, v in active.items()}
            cur = [c for c in nf.get(key, []) if c != value]
            if not is_sel:
                cur.append(value)
            if cur:
                nf[key] = cur
            else:
                nf.pop(key, None)
            chips.append(
                f'<a class="fchip{" active" if is_sel else ""}" '
                f'aria-pressed="{"true" if is_sel else "false"}" '
                f'href="{html.escape(_search_href(view_name, q, since=since, facets=nf))}">'
                f'{html.escape(value)} <span class="fcount">{count}</span></a>'
            )
        rows.append(
            f'<div class="fbar-row"><span class="fbar-label">{html.escape(label)}</span>{"".join(chips)}</div>'
        )
    return f'<div class="search-filters" aria-label="Search filters">{"".join(rows)}</div>'


def ingest_audit_status() -> tuple[dict, int]:
    from argus_wiki.index import search_audit

    snapshot = search_audit.read()
    checked = float(snapshot.get("checked_at") or 0)
    snapshot["fresh"] = bool(checked and time.time() - checked <= 900)
    status = str(snapshot.get("status") or "unknown")
    code = 200 if status in {"healthy", "processing"} and snapshot["fresh"] else 503
    return snapshot, code


def _search_audit_panel() -> str:
    snapshot, _status_code = ingest_audit_status()
    status = str(snapshot.get("status") or "unknown")
    display_status = status if snapshot.get("fresh") else "unknown"
    checked = float(snapshot.get("checked_at") or 0)
    checked_label = time.strftime("%H:%M", time.localtime(checked)) if checked else "not yet"
    landed = int(snapshot.get("landed_pdfs") or 0)
    indexed = int(snapshot.get("indexed") or 0)
    processing = int(snapshot.get("processing") or 0)
    projection = int(snapshot.get("projection_pending") or 0)
    ingest_dead = int(snapshot.get("ingest_dead") or 0)
    projection_dead = int(
        snapshot.get("projection_dead_today", snapshot.get("projection_dead", 0)) or 0
    )
    oldest = max(0, int(snapshot.get("oldest_lag_seconds") or 0))
    if not snapshot.get("fresh"):
        headline = "Search status is stale"
        detail = f"Last audit checked {checked_label}."
    elif display_status == "healthy":
        headline = f"All {landed} PDFs searchable · checked {checked_label}"
        detail = "Vault and search index agree."
    elif display_status == "processing":
        headline = f"{indexed} searchable · {processing} processing · oldest {max(1, oldest // 60)}m"
        queued = f" · {projection} queued for indexing" if projection else ""
        detail = f"{landed} PDFs landed today{queued} · checked {checked_label}"
    else:
        headline = "Search is incomplete"
        detail = (
            f"{indexed} of {landed} PDFs searchable · ingest errors {ingest_dead} "
            f"· index errors {projection_dead} · checked {checked_label}"
        )
    pending = snapshot.get("pending") or []
    pending_html = "".join(
        f"<li>{html.escape(str(item.get('name') or 'Unknown source'))} · "
        f"{html.escape(str(item.get('status') or 'queued'))}</li>"
        for item in pending
    )
    details = (
        f'<details><summary>Pipeline details</summary><ul>{pending_html}</ul></details>'
        if pending_html else ""
    )
    return (
        f'<section class="search-audit {html.escape(display_status)}" role="status" aria-live="polite">'
        f'<div><strong>{html.escape(headline)}</strong><span>{html.escape(detail)}</span></div>'
        f"{details}</section>"
    )


def render_search(view_name: str, q: str, page: int | str | None = 1,
                  filters: dict | None = None) -> tuple[str, int]:
    started = time.time()
    view = get_view(view_name)
    if not is_ready(view):
        return wip_html(view_name), 503
    query = q.strip().lower()
    page_no = _search_page_number(page)
    filters = filters or {}

    from argus_wiki.search import wiki_api

    # Scope the date/facet filters over the whole search + facet-count pass.
    with wiki_api.search_filters(
        since=filters.get("since"),
        until=filters.get("until"),
        facets=filters.get("facets"),
    ):
        try:
            fcounts = wiki_api.facet_counts(query, view_name) if query else {}
        except Exception:
            fcounts = {}
    fbar = _search_filter_bar(view_name, q, filters, fcounts)
    audit = _search_audit_panel()
    has_filters = bool(filters.get("since") or filters.get("until") or filters.get("facets"))
    if not query and not has_filters:
        body = (
            f'<article><h1>Search</h1><p class="muted">Enter a query or choose filters to browse sources.</p></article>'
            f'{fbar}{audit}<div class="search-summary"><p class="muted">Choose a date or content type.</p></div>'
        )
        return layout(view_name, "Search", body, sidebar_lazy(view_name, "")), 200
    with wiki_api.search_filters(
        since=filters.get("since"),
        until=filters.get("until"),
        facets=filters.get("facets"),
    ):
        core = _search_core(view_name, query)
    logging.info(
        "argus_wiki search view=%s query=%r matches=%d elapsed=%.2fs",
        view_name,
        q,
        len(core.matches),
        time.time() - started,
    )

    if core.warming and not core.partial:
        return search_warming_html(view_name, q), 202

    if core.fast_entity:
        summary = _search_summary_blocks(core)
        body = (
            f'<article><h1>Search</h1><p class="muted">Fast entity search for <strong>{html.escape(q)}</strong>. '
            f'Keep typing for broader content results.</p></article>'
            f'{fbar}{audit}<div class="search-summary">{summary}</div>'
        )
        return layout(view_name, f"Search: {q}", body, sidebar_lazy(view_name, "")), 200

    if core.partial and view.index_result is None:
        status_note = (
            f'<article><h1>Search</h1><p class="muted">Showing entity-page matches for '
            f'<strong>{html.escape(q)}</strong> while the full source index warms.</p></article>'
        )
        summary = _search_summary_blocks(
            core,
            remainder_first=bool(core.source_rels and core.matches),
        )
        body = status_note + f'{fbar}{audit}<div class="search-summary">{summary}</div>'
        return layout(view_name, f"Search: {q}", body, sidebar_lazy(view_name, "")), 200

    matches = core.matches
    capped = matches[:250]
    start = (page_no - 1) * SEARCH_PAGE_SIZE
    page_rows = capped[start:start + SEARCH_PAGE_SIZE]
    summary = _search_summary_blocks(
        core,
        page_rows=page_rows,
        page_total=len(matches),
        pagination=_pagination_html(view_name, q, page_no, len(capped), filters=filters),
    )
    match_label = (
        f'{len(matches)} matches for <strong>{html.escape(q)}</strong>'
        if q else f"{len(matches)} sources match the selected filters"
    )
    body = (
        f'<article><h1>Search</h1><p class="muted">{match_label}</p></article>'
        f'{fbar}{audit}<div class="search-summary">{summary}</div>'
    )
    return layout(view_name, f"Search: {q}", body, sidebar_lazy(view_name, "")), 200


def _short_source_date(raw: str) -> str:
    """Leading date tag for a source result: ``mm/dd`` for the current year,
    ``mm/dd/yyyy`` for older years. Accepts a leading ``YYYY-MM-DD`` (optionally
    with a trailing time) or ``YYYYMMDD``; '' when no leading date is present."""
    digits = "".join(c for c in (raw or "") if c.isdigit())[:8]
    if len(digits) != 8:
        return ""
    yyyy, mm, dd = digits[:4], digits[4:6], digits[6:8]
    year = int(yyyy)
    # Ignore epoch/mtime-zero fallbacks (1969/1970) and other implausible dates so
    # undated sources show no tag rather than a misleading "12/31/1969".
    if year < 2000 or year > datetime.now().year + 1 or not ("01" <= mm <= "12"):
        return ""
    return f"{mm}/{dd}" if year == datetime.now().year else f"{mm}/{dd}/{yyyy}"


def search_card(note: Note, snippet: str) -> str:
    display_title, display_rel = _search_display_note(note)
    frontmatter = note.frontmatter or {}
    is_source = reader_contract.is_source_rel(note.rel)
    raw_path = str(frontmatter.get("raw_path") or "")
    content_kind = str(frontmatter.get("kind") or Path(raw_path).suffix.lstrip(".") or "").lower()
    if content_kind in {"eml", "msg"}:
        content_kind = "email"
    sender = str(frontmatter.get("sender") or frontmatter.get("broker") or "")
    day = str(
        frontmatter.get("day") or frontmatter.get("date") or
        frontmatter.get("as_of") or frontmatter.get("report_date") or ""
    )
    digits = "".join(char for char in day if char.isdigit())[:8]
    primary_date = (
        f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 8 else day[:10]
    ) or format_note_mtime(note.mtime)
    badge = f'<span class="result-badge">{html.escape(content_kind or "source")}</span>' if is_source else ""
    date_tag = _short_source_date(primary_date) if is_source else ""
    date_prefix = f'<span class="result-date">{html.escape(date_tag)}</span>' if date_tag else ""
    source_meta = ""
    if is_source:
        source_meta = " · ".join(
            value for value in (sender, primary_date, "Searchable") if value
        )
    return (
        f'<a class="result" href="{note.route}">'
        f'<strong class="result-title">{date_prefix}{badge}{html.escape(display_title)}</strong>'
        f'<div class="result-meta">{html.escape(display_rel)}'
        f' <span class="result-mtime">{html.escape(source_meta or primary_date)}</span></div>'
        f'<div class="result-meta" style="color:var(--text-muted);margin-top:6px">{html.escape(snippet)}</div>'
        f'</a>'
    )


def _lookup_note_by_stem(notes_by_rel: dict[str, Note], rel: str) -> Note | None:
    return notes_by_rel.get(rel) or notes_by_rel.get(f"{rel}.md")


def trending_widget(view_name: str, n: int = 8) -> str:
    rows = clicks.trending(view_name, n)
    if not rows:
        return ""
    notes_by_rel: dict[str, Note] = {}
    try:
        view = get_view(view_name)
        notes_by_rel = view.notes_by_rel
    except KeyError:
        pass
    items = []
    for rel, count in rows:
        note = _lookup_note_by_stem(notes_by_rel, rel)
        if note:
            href, label = note.route, _note_list_title(note)
        else:
            href, label = _trending_href(view_name, rel, clicks.title_for(rel))
        items.append(
            f'<li><a class="trending-link" href="{html.escape(href)}">{html.escape(label)}</a>'
            f'<span class="trending-count">{count}</span></li>'
        )
    return (
        f'<section class="trending-card">'
        f'<h3>Trending now <span class="trending-window">({clicks.window_days()}d)</span></h3>'
        f'<ol class="trending-list">{"".join(items)}</ol>'
        f'</section>'
    )


def render_trending(view_name: str) -> tuple[str, int]:
    view = get_view(view_name)
    if not is_ready(view):
        return wip_html(view_name), 503
    rows = clicks.trending(view_name, 50)
    notes_by_rel = view.notes_by_rel
    if not rows:
        body = '<article><h1>Trending</h1><p class="muted">No clicks captured yet. Browse a few pages and they will show up here.</p></article>'
    else:
        cards = []
        for rel, count in rows:
            note = _lookup_note_by_stem(notes_by_rel, rel)
            if note:
                href, title = note.route, _note_list_title(note)
            else:
                href, title = _trending_href(view_name, rel, clicks.title_for(rel))
            folder = note.folder if note else (rel.split("/", 1)[0] if "/" in rel else "")
            cards.append(
                f'<a class="result" href="{html.escape(href)}">'
                f'<strong>{html.escape(title)}</strong>'
                f'<div class="result-meta">{html.escape(folder)}</div>'
                f'<div class="result-meta" style="color:var(--text-muted);margin-top:6px">{count} click{"s" if count != 1 else ""} in the last {clicks.window_days()} days</div>'
                f'</a>'
            )
        body = (
            f'<article><h1>Trending</h1><p class="muted">Top pages by clicks in the last {clicks.window_days()} days.</p></article>'
            f'<div class="results" style="margin-top:14px">{"".join(cards)}</div>'
        )
    indexes = view.index_result
    if indexes is None:
        kick_background_scan(view)
        notes = []
    else:
        notes = build_indexes(view).get("notes", [])
    return layout(view_name, "Trending", body, sidebar_lazy(view_name, "")), 200


def search_api(
    view_name: str,
    query: str,
    filters: dict | None = None,
) -> tuple[list[dict] | dict, int]:
    started = time.time()
    view = get_view(view_name)
    if not is_ready(view):
        return wip_json(view_name), 503
    q = query.strip().lower()
    filters = filters or {}
    if not q and not any((filters.get("since"), filters.get("until"), filters.get("facets"))):
        return [], 200

    from argus_wiki.search import wiki_api

    with wiki_api.search_filters(
        since=filters.get("since"),
        until=filters.get("until"),
        facets=filters.get("facets"),
    ):
        core = _search_core(view_name, q)
    if core.warming and not core.partial:
        return {
            "status": "warming",
            "detail": "full search index is building",
            "query": query,
            "results": [],
        }, 202

    if core.partial and view.index_result is None:
        ordered = _api_flat_rows(core)
        logging.info(
            "argus_wiki api_search_partial view=%s query=%r matches=%d elapsed=%.2fs",
            view_name,
            query,
            len(ordered),
            time.time() - started,
        )
        return {
            "status": "partial",
            "detail": "full search index is building",
            "query": query,
            "results": [
                {
                    "title": _note_list_title(note),
                    "rel": _visible_rel(note.rel) or note.rel,
                    "url": note.route,
                    "score": round(score, 1),
                }
                for note, _snippet, score in ordered[:50]
            ],
        }, 200

    ordered = _api_flat_rows(core)
    tag = "api_search_fast" if core.fast_entity else "api_search"
    logging.info(
        "argus_wiki %s view=%s query=%r matches=%d elapsed=%.2fs",
        tag,
        view_name,
        query,
        len(ordered),
        time.time() - started,
    )
    results = [
        {
            "title": _note_list_title(note),
            "rel": _visible_rel(note.rel) or note.rel,
            "url": note.route,
            "score": round(score, 1),
        }
        for note, _snippet, score in ordered[:100]
    ]
    audit, audit_code = ingest_audit_status()
    if audit_code != 200:
        return {
            "status": "degraded",
            "detail": "Search results may be incomplete while Elasticsearch catches up.",
            "query": query,
            "audit": audit,
            "results": results,
        }, 206
    return results, 200


def asset_path(view_name: str, rel: str) -> Path:
    view = get_view(view_name)
    clean = unquote(rel).replace("\\", "/").lstrip("/")
    rels = [clean]
    public_rel = _public_storage_rel_for_content(view, clean)
    if public_rel:
        rels.append(public_rel)
    candidates: list[tuple[Path, Path]] = []
    for candidate_rel in rels:
        for candidate_root in _storage_roots_for_rel(view, candidate_rel):
            try:
                root = candidate_root.resolve()
                candidates.append(((candidate_root / candidate_rel).resolve(), root))
            except OSError:
                continue
    for path, root in candidates:
        if not path.exists():
            _ensure_snapshot_asset(view, clean, path)
        if path.exists() and (root == path or root in path.parents):
            return path
    raise FileNotFoundError(rel)


# Content-existence probes hit the SMB share (Path.resolve() + is_file()). The
# markdown-link href rewriter (rewrite_href) calls this once PER LINK, so a
# link-heavy page (feed = 2208 raw-file links) did 2208 blocking SMB stats =
# ~117s render, GIL-starving every other request. Per the serve-time
# architecture (never enumerate/stat SMB per link -- resolve from an in-memory
# index/cache), memoize the resolved path so a given (view, rel) is statted at
# most once per TTL: duplicate links and repeat/cached renders are free, and a
# prewarm pass seeds it out of the request path. Bounded + TTL'd; existence of a
# content file effectively never flips within the window.
_CONTENT_PATH_CACHE: dict[tuple[str, str], tuple[float, "Path | None"]] = {}
_CONTENT_PATH_TTL_SEC = 600.0
_CONTENT_PATH_CACHE_MAX = 50000


def _resolve_wiki_content_path(view_name: str, clean: str) -> Path | None:
    view = get_view(view_name)
    rels = [clean]
    public_rel = _public_storage_rel_for_content(view, clean)
    if public_rel:
        rels.append(public_rel)
    candidates: list[tuple[Path, Path]] = []
    for candidate_rel in rels:
        for candidate_root in _storage_roots_for_rel(view, candidate_rel):
            try:
                root = candidate_root.resolve()
                candidates.append(((candidate_root / candidate_rel).resolve(), root))
            except OSError:
                continue
    for path, root in candidates:
        try:
            if path.is_file() and (root == path or root in path.parents):
                return path
        except OSError:
            continue
    return None


def wiki_content_path_or_none(view_name: str, rel: str) -> Path | None:
    clean = unquote(rel).replace("\\", "/").lstrip("/")
    parts = [part for part in clean.split("/") if part]
    if not parts or ".." in parts or clean.lower().endswith(".md"):
        return None
    now = time.time()
    ck = (view_name, clean)
    hit = _CONTENT_PATH_CACHE.get(ck)
    if hit is not None and now - hit[0] < _CONTENT_PATH_TTL_SEC:
        return hit[1]
    result = _resolve_wiki_content_path(view_name, clean)
    if len(_CONTENT_PATH_CACHE) >= _CONTENT_PATH_CACHE_MAX:
        _CONTENT_PATH_CACHE.clear()
    _CONTENT_PATH_CACHE[ck] = (now, result)
    return result


def wiki_content_path(view_name: str, rel: str) -> Path:
    path = wiki_content_path_or_none(view_name, rel)
    if path is None:
        raise FileNotFoundError(rel)
    return path


def wiki_content_as_attachment(rel: str) -> bool:
    return Path(rel).suffix.lower() in HTML_PREVIEW_SUFFIXES


def wiki_content_preview_body(path: Path, suffix: str, mime: str, file_href: str) -> str:
    if suffix in HTML_PREVIEW_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 2_000_000:
            text = text[:2_000_000] + "\n\n<!-- truncated at 2MB for browser preview -->"
        return f'<iframe class="raw-frame" sandbox srcdoc="{html.escape(text, quote=True)}"></iframe>'
    return raw_preview_body(path, suffix, mime, file_href)


def wiki_content_preview(view_name: str, rel: str) -> str:
    view = get_view(view_name)
    if not is_ready(view):
        return wip_html(view_name)
    path = wiki_content_path(view_name, rel)
    clean = unquote(rel).replace("\\", "/").lstrip("/")
    suffix = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    file_href = _content_file_route(view_name, clean)
    download_attr = " download" if wiki_content_as_attachment(clean) else ""
    actions = (
        f'<div class="raw-actions">'
        f'<a class="raw-btn primary" href="{html.escape(file_href)}"{download_attr} target="_blank" rel="noopener">Open File</a>'
        f'<span class="raw-path-label">Wiki path</span><code>{html.escape(clean)}</code>'
        f'</div>'
    )
    preview = wiki_content_preview_body(path, suffix, mime, file_href)
    content = (
        f'<article><h1>{html.escape(path.name)}</h1>'
        f'<p class="muted">{html.escape(mime)} - {size:,} bytes</p>'
        f'{actions}{preview}</article>'
    )
    return layout(view_name, path.name, content, sidebar_lazy(view_name, clean), page_rel=clean)


def _ensure_snapshot_asset(view: ViewState, rel: str, target: Path) -> None:
    if not _lazy_chart_snapshots_enabled():
        return
    match = re.fullmatch(r"_charts/([^/]+)/p(\d{2})\.png", rel.replace("\\", "/"))
    if not match:
        return
    source_dirs = reader_contract.source_page_dirs()
    if not source_dirs:
        return
    source_dir = source_dirs[0]
    source_page = view.wiki_root / source_dir / f"{match.group(1)}.md"
    if not source_page.exists() and not view.is_default:
        source_page = reader_contract.default_view_wiki_root() / source_dir / f"{match.group(1)}.md"
    if not source_page.exists():
        return
    try:
        fm, _ = split_frontmatter(source_page.read_text(encoding="utf-8", errors="replace"))
        raw = fm.get("raw_path")
        if not raw:
            return
        raw_pdf = raw_paths.resolve_raw_path(raw, config.RAW_ROOT)
        if raw_pdf.suffix.lower() != ".pdf" or not raw_pdf.exists():
            return
        from argus_wiki.charts import vision_charts

        png = vision_charts._render_pdf_page_to_png(raw_pdf, int(match.group(2)) - 1, dpi=130)
        if not png:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
    except OSError:
        return


def _raw_candidate(path: str, ingest_source: str | None = None) -> Path:
    path = unquote(path or "").strip()
    row = _manifest_by_raw_key().get(path)
    if row and not ingest_source:
        ingest_source = row.get("ingest_source")
    keys = [path]
    if row:
        canon = str(row.get("raw_path") or "").strip()
        if canon and canon not in keys:
            keys.insert(0, canon)
    for key in keys:
        candidate = raw_paths.resolve_raw_path(key, config.RAW_ROOT, ingest_source=ingest_source)
        if candidate.exists():
            return candidate
    return raw_paths.resolve_raw_path(path, config.RAW_ROOT, ingest_source=ingest_source)


def raw_path(path: str, ingest_source: str | None = None) -> Path:
    file_path = _raw_candidate(path, ingest_source=ingest_source)
    if not file_path.exists() or not raw_paths.is_under_ingest_root(file_path, config.RAW_ROOT):
        raise FileNotFoundError(path)
    return file_path


def file_uri(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except ValueError:
        return "file:///" + quote(str(path).replace("\\", "/"))


def raw_preview(path_query: str, ingest_source: str | None = None) -> str:
    path = raw_path(path_query, ingest_source=ingest_source)
    title = path.name
    suffix = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    row = _manifest_by_raw_key().get(unquote(path_query or "").strip())
    ingest_source = ingest_source or (row or {}).get("ingest_source")
    try:
        raw_key = raw_paths.canonical_raw_key(path, config.RAW_ROOT, ingest_source=ingest_source)
    except ValueError:
        raw_key = _raw_path_key(str(path)) or str(path)
    web_href = _raw_open_route(raw_key, ingest_source)
    file_href = _raw_file_route(raw_key, ingest_source)
    desktop_href = file_uri(path)
    size = path.stat().st_size
    show_href = file_href if suffix == ".pdf" else web_href
    actions = (
        f'<div class="raw-actions">'
        f'<a class="raw-btn primary" href="{html.escape(show_href)}" target="_blank" rel="noopener">Show Original</a>'
        f'<a class="raw-btn" href="{html.escape(desktop_href)}">Open Original</a>'
        f'<span class="raw-path-label">Raw path</span><code>{html.escape(str(path))}</code>'
        f'</div>'
    )
    preview = raw_preview_body(path, suffix, mime, file_href)
    css = theme_mod.read_theme()
    default_home = route_for(_default_view_name(), reader_contract.home_page_rel())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Raw Source</title>
  <style>{css}</style>
  <style>
    body {{ padding: 0; background: var(--bg); color: var(--text); }}
    .raw-shell {{ max-width: 960px; margin: 0 auto; padding: 24px; }}
    .raw-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 18px; }}
    .raw-title {{ margin: 0 0 8px; font-size: 24px; }}
    .raw-meta {{ color: var(--text-muted); font-size: 13px; }}
    .raw-actions {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 14px 0 20px; }}
    .raw-path-label {{ color: var(--text-muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }}
    .raw-actions code {{ font-size: 12px; color: var(--text-muted); overflow-wrap: anywhere; }}
    .raw-btn {{ display: inline-flex; align-items: center; padding: 8px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-elev); color: var(--text) !important; font-size: 13px; font-weight: 600; }}
    .raw-btn.primary {{ background: var(--accent); color: #fff !important; border-color: var(--accent); }}
    .raw-frame {{ width: 100%; height: calc(100vh - 190px); min-height: 560px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }}
    .raw-image {{ max-width: 100%; border: 1px solid var(--border); border-radius: 12px; background: #fff; }}
    .raw-pre {{ white-space: pre-wrap; overflow: auto; max-height: calc(100vh - 210px); padding: 18px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-elev); font-size: 12px; line-height: 1.45; }}
    .email-preview {{ border: 1px solid var(--border); border-radius: 10px; background: #fff; color: #202124; overflow: hidden; box-shadow: 0 1px 2px rgba(60,64,67,.18); }}
    .email-meta {{ padding: 22px 28px 18px; border-bottom: 1px solid #e8eaed; }}
    .email-subject {{ margin: 0 0 20px; color: #202124; font-size: 22px; font-weight: 400; line-height: 1.35; }}
    .email-sender-row {{ display: flex; gap: 12px; align-items: center; min-width: 0; }}
    .email-avatar {{ width: 40px; height: 40px; flex: 0 0 40px; border-radius: 50%; display: grid; place-items: center; background: #1a73e8; color: #fff; font-size: 17px; font-weight: 600; }}
    .email-sender-info {{ flex: 1; min-width: 0; font-size: 13px; }}
    .email-sender-top {{ display: flex; justify-content: space-between; gap: 16px; align-items: baseline; }}
    .email-sender {{ overflow-wrap: anywhere; font-size: 14px; font-weight: 600; }}
    .email-date, .email-to {{ color: #5f6368; }}
    .email-date {{ flex: 0 0 auto; text-align: right; }}
    .email-to {{ margin-top: 2px; overflow-wrap: anywhere; }}
    .email-body {{ padding: 28px; font: 14px/1.55 Arial, sans-serif; max-height: calc(100vh - 350px); overflow: auto; overflow-wrap: anywhere; }}
    .email-body p {{ margin: 0 0 14px; }}
    .email-body ul, .email-body ol {{ padding-left: 22px; }}
    .email-body table {{ border-collapse: collapse; max-width: 100%; margin: 12px 0; }}
    .email-body td, .email-body th {{ padding: 4px 6px; vertical-align: top; }}
    .email-body img {{ display: block; max-width: 100%; height: auto; }}
    .email-body a {{ color: #1a73e8; text-decoration: none; overflow-wrap: anywhere; }}
    .email-body a:hover {{ text-decoration: underline; }}
    .email-plain {{ margin: 0; white-space: pre-wrap; font: inherit; }}
    @media (max-width: 640px) {{
      .raw-shell {{ padding: 12px; }}
      .email-meta, .email-body {{ padding: 18px; }}
      .email-sender-top {{ display: block; }}
      .email-date {{ margin-top: 3px; text-align: left; }}
    }}
    .raw-empty {{ padding: 22px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-elev); color: var(--text-muted); }}
  </style>
{dr_theme.head_tags()}
</head>
<body>
  <main class="raw-shell">
    <section class="raw-head">
      <div>
        <h1 class="raw-title">{html.escape(title)}</h1>
        <div class="raw-meta">{html.escape(mime)} - {size:,} bytes</div>
      </div>
      <a class="raw-btn" href="{default_home}">Back to wiki</a>
    </section>
    {actions}
    {preview}
  </main>
</body>
</html>"""


def raw_preview_body(path: Path, suffix: str, mime: str, file_href: str) -> str:
    if suffix == ".msg":
        return msg_preview(path)
    if suffix in {".eml", ".mht", ".mhtml"}:
        return mime_email_preview(path)
    if mime == "application/pdf" or suffix == ".pdf":
        return f'<iframe class="raw-frame" src="{html.escape(file_href)}"></iframe>'
    if mime.startswith("image/"):
        return f'<img class="raw-image" src="{html.escape(file_href)}" alt="{html.escape(path.name)}">'
    if mime.startswith(("video/", "audio/")):
        tag = "video" if mime.startswith("video/") else "audio"
        controls = "controls"
        return f'<{tag} class="raw-frame" {controls} src="{html.escape(file_href)}"></{tag}>'
    if suffix in {".html", ".htm"}:
        return f'<iframe class="raw-frame" sandbox src="{html.escape(file_href)}"></iframe>'
    if suffix in {".txt", ".md", ".log", ".csv", ".json", ".yaml", ".yml", ".xml", ".py", ".sql", ".ini", ".cfg"} or mime.startswith("text/"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 2_000_000:
            text = text[:2_000_000] + "\n\n[truncated at 2MB for browser preview]"
        return f'<pre class="raw-pre">{html.escape(text)}</pre>'
    return (
        '<div class="raw-empty">'
        '<strong>No safe inline browser preview for this file type.</strong><br>'
        'Use Show Original to let the browser handle it, or Open Original for the native application.'
        '</div>'
    )


def mime_email_preview(path: Path) -> str:
    from email import policy
    from email.parser import BytesParser

    try:
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    except OSError:
        return (
            '<div class="raw-empty">'
            '<strong>Email body could not be extracted for preview.</strong><br>'
            'Use Open Original to view it in Outlook.'
            '</div>'
        )
    html_body = ""
    text_body = ""
    inline_images = {}

    def part_content(part) -> str:
        try:
            content = part.get_content()
            if isinstance(content, str):
                return content
        except (LookupError, UnicodeDecodeError):
            pass
        payload = part.get_payload(decode=True) or b""
        charset = (part.get_content_charset() or "utf-8").lower()
        if charset == "unicode":
            charset = "utf-16"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type in EMAIL_IMAGE_TYPES:
            add_inline_image(
                inline_images,
                part.get("Content-ID") or part.get("Content-Location"),
                content_type,
                part.get_payload(decode=True),
            )
            continue
        if content_type not in {"text/html", "text/plain"}:
            continue
        if part.get_content_disposition() == "attachment":
            continue
        content = part_content(part)
        if content_type == "text/html" and not html_body:
            html_body = str(content or "")
        elif content_type == "text/plain" and not text_body:
            text_body = str(content or "")
    subject = msg.get("subject") or path.stem
    sender = msg.get("from") or ""
    date = msg.get("date") or ""
    recipient = msg.get("to") or ""
    if html_body:
        return email_preview_html(
            subject, sender, date, recipient, clean_email_html(html_body, inline_images)
        )
    if text_body:
        return email_preview_html(
            subject, sender, date, recipient,
            f'<pre class="email-plain">{html.escape(text_body)}</pre>',
        )
    return (
        '<div class="raw-empty">'
        '<strong>Email body could not be extracted for preview.</strong><br>'
        'Use Open Original to view it in Outlook.'
        '</div>'
    )


def msg_preview(path: Path) -> str:
    meta = read_msg_full(path)
    if not meta:
        return (
            '<div class="raw-empty">'
            '<strong>Email body could not be extracted for preview.</strong><br>'
            'Use Open Original to view it in Outlook.'
            '</div>'
        )
    body = email_body_html(meta)
    return email_preview_html(
        meta.get("subject") or path.stem,
        meta.get("sender") or "",
        meta.get("date") or "",
        meta.get("to") or "",
        body,
    )


def read_msg_full(path: Path) -> dict:
    import extract_msg

    msg = extract_msg.openMsg(str(path))
    try:
        text_body = decode_msg_value(msg.body)
        html_body = decode_msg_value(msg.htmlBody)
        inline_images = {}
        for attachment in msg.attachments:
            name = decode_msg_value(
                getattr(attachment, "longFilename", None)
                or getattr(attachment, "shortFilename", None)
            )
            content_type = decode_msg_value(getattr(attachment, "mimetype", None))
            content_type = content_type or mimetypes.guess_type(name)[0] or ""
            add_inline_image(
                inline_images,
                getattr(attachment, "cid", None),
                content_type,
                getattr(attachment, "data", None),
            )
        return {
            "subject": msg.subject or path.stem,
            "sender": msg.sender or "",
            "to": decode_msg_value(getattr(msg, "to", None)),
            "date": str(msg.date) if msg.date else "",
            "body": text_body,
            "html_body": html_body,
            "inline_images": inline_images,
        }
    finally:
        msg.close()


def decode_msg_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    text = str(value)
    return "" if text.strip().lower() == "none" else text


def email_body_html(meta: dict) -> str:
    html_body = (meta.get("html_body") or "").strip()
    if html_body:
        return clean_email_html(html_body, meta.get("inline_images"))
    text_body = (meta.get("body") or "").strip()
    if not text_body:
        return "<p>(empty email body)</p>"
    return linkify_plain_email(text_body)


def email_preview_html(subject: str, sender: str, date: str, recipient: str, body: str) -> str:
    sender = str(sender or "")
    avatar = next((char.upper() for char in sender if char.isalnum()), "?")
    to_html = f'<div class="email-to">to {html.escape(str(recipient))}</div>' if recipient else ""
    return (
        '<section class="email-preview">'
        '<div class="email-meta">'
        f'<h2 class="email-subject">{html.escape(str(subject))}</h2>'
        '<div class="email-sender-row">'
        f'<div class="email-avatar">{html.escape(avatar)}</div>'
        '<div class="email-sender-info">'
        '<div class="email-sender-top">'
        f'<div class="email-sender">{html.escape(sender or "Unknown sender")}</div>'
        f'<div class="email-date">{html.escape(str(date))}</div>'
        '</div>'
        f'{to_html}'
        '</div></div></div>'
        f'<div class="email-body">{body}</div>'
        '</section>'
    )


def normalize_content_id(value) -> str:
    return unquote(decode_msg_value(value)).strip().strip("<>").removeprefix("cid:").lower()


def add_inline_image(images: dict[str, str], content_id, content_type: str, payload) -> None:
    key = normalize_content_id(content_id)
    content_type = str(content_type or "").lower()
    if not key or content_type not in EMAIL_IMAGE_TYPES or not isinstance(payload, bytes):
        return
    encoded = base64.b64encode(payload).decode("ascii")
    images[key] = f"data:{content_type};base64,{encoded}"


def clean_email_html(raw_html: str, inline_images: dict[str, str] | None = None) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()
    inline_images = inline_images or {}
    for img in soup.find_all("img"):
        source = normalize_content_id(img.get("src") or "")
        source = inline_images.get(source, "")
        if not source:
            img.decompose()
            continue
        img["src"] = source
    for tag in soup.find_all(True):
        if tag.name == "a":
            href = normalize_email_url(tag.get("href") or "")
            if href:
                text = tag.get_text(" ", strip=True)
                if not text or looks_like_raw_url(text):
                    tag.string = display_url(href)
                tag["href"] = href
                tag["target"] = "_blank"
                tag["rel"] = "noopener noreferrer"
            else:
                tag.unwrap()
    body = soup.body or soup
    cleaned = bleach.clean(
        str(body),
        tags=EMAIL_ALLOWED_TAGS,
        attributes=EMAIL_ALLOWED_ATTRS,
        protocols=["http", "https", "mailto", "data"],
        strip=True,
    )
    return cleaned.strip() or "<p>(empty email body)</p>"


def linkify_plain_email(text: str) -> str:
    text = re.sub(r"^\?{2,}", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    parts = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        linked = linkify_plain_block(block)
        parts.append(f"<p>{linked.replace(chr(10), '<br>')}</p>")
    return "".join(parts) or "<p>(empty email body)</p>"


def linkify_plain_block(block: str) -> str:
    out = []
    pos = 0
    for match in URL_RE.finditer(block):
        out.append(html.escape(block[pos:match.start()]))
        out.append(email_anchor(match.group(1)))
        pos = match.end()
    out.append(html.escape(block[pos:]))
    return "".join(out)


def email_anchor(raw_url: str) -> str:
    href = normalize_email_url(html.unescape(raw_url.strip("<>")))
    label = display_url(href)
    return f'<a href="{html.escape(href)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'


def normalize_email_url(url: str) -> str:
    url = html.unescape(url or "").strip().strip("<>")
    parsed = urlparse(url)
    if "urldefense" in parsed.netloc.lower():
        match = re.search(r"__([^;]+)__", parsed.path)
        if match:
            candidate = unquote(match.group(1))
            if candidate.startswith(("http://", "https://")):
                return candidate
    if "safelinks.protection.outlook.com" in parsed.netloc.lower():
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        if query.get("url"):
            return unquote(query["url"])
    return url


def display_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url[:80]
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    label = host + (path if path and path != "/" else "")
    return label if len(label) <= 90 else label[:87] + "..."


def looks_like_raw_url(text: str) -> bool:
    return len(text) > 80 or bool(URL_RE.search(text)) or "urldefense" in text.lower()
