"""apo_config-backed accessors for the wiki sub-server.

Reads each key from `apo_config` with the catalog default as fallback. Values
are resolved at first access then cached for the life of the process - so a
config change requires a restart, matching mcp_server semantics.
"""
from __future__ import annotations

from functools import lru_cache

from apo_config.config import required_config
from common.service_discovery import resolve_service_hosts
from dash_reports.sub_servers.argus_wiki_server.config_catalog import (
    APP,
    CATALOG,
    DEFAULT_PREWARM_ENABLED,
    DEFAULT_PREWARM_PAGES,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_URL_PATH,
)


def _read(key: str, default):
    try:
        from apo_config.config import config
        value = config.getconfig(APP, key)
    except Exception:
        value = None
    return value if value not in (None, "") else default


@lru_cache(maxsize=1)
def host() -> str:
    return required_config(APP, "server.host")


@lru_cache(maxsize=1)
def client_host() -> str:
    # Local-first when argus_wiki_server.hosts lists this node, else falls back
    # to internal_service_host. The reader is read-only and multi-instance-ready
    # (replica pattern: node02 runs with ARGUS_WIKI_READER_CACHE_PERSIST=0 so only
    # the primary writes the shared reader_cache Delta -- see reader_cache_persist).
    # Node02's reader still queries the node01 Elasticsearch (prod
    # wiki_search_elastic_url -> pue2mal01:9200), so this is local page-render +
    # cache with a cross-node ES query; full wiki HA would need ES on both nodes
    # (a 2-node cluster -- wiki_search_elastic_url accepts a comma seed list).
    return resolve_service_hosts(APP)[0]


@lru_cache(maxsize=1)
def port() -> int:
    return int(required_config(APP, "server.port"))


@lru_cache(maxsize=1)
def scheme() -> str:
    return required_config(APP, "server.scheme")


@lru_cache(maxsize=1)
def url_path() -> str:
    # Per-process override so a second reader instance (e.g. the VC wiki on the
    # vc_news vault) can mount under its own prefix without touching app_config.
    import os
    return os.environ.get("ARGUS_WIKI_URL_PATH", DEFAULT_URL_PATH)


@lru_cache(maxsize=1)
def proxy_disabled() -> bool:
    return str(_read("proxy_disabled", "false")).strip().lower() in {"1", "true", "yes"}


@lru_cache(maxsize=1)
def search_source_first() -> bool:
    """Rank source (raw research) matches ahead of generic entity pages in search
    results. Raw full-text makes sources first-class; set false for legacy order."""
    return str(_read("search_source_first", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=1)
def scan_interval_sec() -> float:
    return float(_read("scan_interval_sec", DEFAULT_SCAN_INTERVAL_SEC))


@lru_cache(maxsize=1)
def prewarm_enabled() -> bool:
    default = "true" if DEFAULT_PREWARM_ENABLED else "false"
    return str(_read("prewarm_enabled", default)).strip().lower() in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=1)
def reader_cache_persist() -> bool:
    """Whether this node writes the shared reader_cache Delta (primary) or keeps
    it in-memory only (replica). Exactly one node may be primary -- two writers
    corrupt the shared Delta. (This is about the reader_cache Delta only; the
    Elasticsearch search index is read-only/shared and unaffected.)

    Resolution order: an explicit env (ARGUS_WIKI_READER_CACHE_PERSIST) or config
    (reader_cache_persist) override wins; otherwise it's decided by MACHINE
    identity -- the primary is the first host in argus_wiki_server.hosts (else the
    pinned internal_service_host). Machine-based so both nodes run identical
    config/bat even though they share one service account -- no per-node env var."""
    import os
    import re
    import socket

    truthy = {"1", "true", "yes", "y", "on"}
    env = os.environ.get("ARGUS_WIKI_READER_CACHE_PERSIST")
    if env not in (None, ""):
        return env.strip().lower() in truthy
    cfg = _read("reader_cache_persist", "")
    if str(cfg) != "":
        return str(cfg).strip().lower() in truthy

    from common.service_discovery import resolve_internal_service_host
    raw_hosts = _read("hosts", "")
    hosts = [h.strip() for h in re.split(r"[,/]", str(raw_hosts)) if h.strip()]
    primary = (hosts[0] if hosts else resolve_internal_service_host()).lower()
    me = socket.gethostname().lower()
    return me == primary or me.split(".")[0] == primary


@lru_cache(maxsize=1)
def prewarm_pages() -> tuple[str, ...]:
    raw = _read("prewarm_pages", "")
    if not raw:
        return DEFAULT_PREWARM_PAGES
    return tuple(p.strip() for p in str(raw).split(",") if p.strip())


def base_url() -> str:
    return f"{scheme()}://{client_host()}:{port()}{url_path()}"


__all__ = [
    "APP",
    "CATALOG",
    "host",
    "client_host",
    "port",
    "scheme",
    "url_path",
    "proxy_disabled",
    "scan_interval_sec",
    "prewarm_enabled",
    "prewarm_pages",
    "base_url",
]
