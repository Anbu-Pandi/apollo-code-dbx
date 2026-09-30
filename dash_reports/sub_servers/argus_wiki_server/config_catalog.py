"""apo_config catalog for the standalone Argus Wiki sub-server.

All deployment knobs live here. `argus_wiki_server.config` reads these keys at
module-load with the defaults as fallbacks, so the catalog is both the seed
spec for `seed_app_config.py` and the authoritative defaults.

Prewarm targets are derived from the dimension registry at runtime via
`prewarm_relpaths()`. The static `DEFAULT_PREWARM_PAGES` tuple is kept only as
the apo_config seed value for operators who want to lock the warmup list to a
specific set of pages.
"""
from __future__ import annotations

from typing import Iterable

APP = "argus_wiki_server"

DEFAULT_URL_PATH = "/wiki"
DEFAULT_SCAN_INTERVAL_SEC = 60.0
DEFAULT_PREWARM_ENABLED = False
DEFAULT_PREWARM_PAGES: tuple[str, ...] = ()

CATALOG = {
    "proxy_disabled": {"dev": "false", "prod": "false",
                       "note": "If true, dash_reports falls back to in-process route registration."},
    "scan_interval_sec": {"dev": str(DEFAULT_SCAN_INTERVAL_SEC), "prod": str(DEFAULT_SCAN_INTERVAL_SEC),
                          "note": "Seconds between vault rescan passes per view."},
    "prewarm_enabled": {"dev": "false", "prod": "false",
                        "note": "If true, background-warm indexes/hot pages after route registration. "
                                "Keep false for large vaults so startup serves immediately."},
    "prewarm_pages": {"dev": "", "prod": "",
                      "note": "Optional comma-separated rel list to prewarm. "
                              "When empty, the server derives the list from "
                              "dimensions.yaml (pages with role=home/daily/feed/overview "
                              "+ every <page_dir>/index.md)."},
    "search_source_first": {"dev": "true", "prod": "true",
                            "note": "Rank source (raw research) matches ahead of generic entity "
                                    "pages in search results. Raw full-text makes sources "
                                    "first-class; set false for the legacy entity-first order."},
}


def _index_md_targets() -> list[str]:
    from argus_wiki.bootstrap import dimension_registry

    return [f"{d}/index.md" for d in dimension_registry.page_directories()]


def _role_targets() -> Iterable[str]:
    from argus_wiki.bootstrap import dimension_registry

    roles = (
        dimension_registry.ROLE_HOME,
        dimension_registry.ROLE_DAILY,
        dimension_registry.ROLE_FEED,
        dimension_registry.ROLE_OVERVIEW,
    )
    seen: set[str] = set()
    for role in roles:
        for spec in dimension_registry.pages_with_role(role):
            page_file = spec.page_file
            if not page_file:
                continue
            rel = page_file if page_file.lower().endswith(".md") else f"{page_file}.md"
            if rel in seen:
                continue
            seen.add(rel)
            yield rel
    fallback = (
        dimension_registry.home_page_rel(),
        dimension_registry.feed_page_rel(),
        dimension_registry.overview_page_rel(),
    )
    for stem in fallback:
        rel = stem if stem.lower().endswith(".md") else f"{stem}.md"
        if rel not in seen:
            seen.add(rel)
            yield rel


def prewarm_relpaths() -> tuple[str, ...]:
    """Compose the prewarm target list - apo_config override wins; otherwise
    derive from registry."""
    from dash_reports.sub_servers.argus_wiki_server import config as runtime_config

    configured = runtime_config.prewarm_pages()
    if configured:
        return tuple(configured)
    derived: list[str] = list(_role_targets()) + _index_md_targets()
    seen: set[str] = set()
    out: list[str] = []
    for rel in derived:
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return tuple(out)
