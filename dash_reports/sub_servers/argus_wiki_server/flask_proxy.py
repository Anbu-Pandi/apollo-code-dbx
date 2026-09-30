"""Reverse proxy that forwards dash_reports `/wiki/*` to the standalone
Argus Wiki Flask backend.

Backend URL and proxy-disable flag come from `apo_config` via
`argus_wiki_server.config`. Falls back to in-process route registration when
`argus_wiki_server.proxy_disabled = true` is set - useful for one-shot local
runs or environments that don't want a second process.
"""
from __future__ import annotations

import logging

from flask import Response

from dash_reports.sub_servers.argus_wiki_server import config as wiki_cfg
from dash_reports.sub_servers.argus_wiki_server.routes import setup_argus_wiki_routes
from dash_reports.sub_servers.argus_wiki_server.proxy_transport import backend_down_html, forward


def get_argus_wiki_base_url() -> str:
    return wiki_cfg.base_url()


def setup_argus_wiki_proxy(server) -> None:
    if wiki_cfg.proxy_disabled():
        logging.info("argus_wiki proxy disabled by config; registering in-process routes")
        setup_argus_wiki_routes(server)
        return

    url_path = wiki_cfg.url_path()
    logging.info("argus_wiki proxy registered: %s -> %s", url_path, wiki_cfg.base_url())

    @server.route(url_path, defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
    @server.route(f"{url_path}/", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
    @server.route(f"{url_path}/<path:path>", methods=["GET", "POST", "OPTIONS"])
    def argus_wiki_proxy(path: str):
        return _forward(path)


def _forward(path: str) -> Response:
    return forward(path, base_url=wiki_cfg.base_url(), url_path=wiki_cfg.url_path())


def _backend_down_html() -> str:
    return backend_down_html(wiki_cfg.url_path())


__all__ = [
    "setup_argus_wiki_proxy",
    "setup_argus_wiki_routes",
    "get_argus_wiki_base_url",
]
