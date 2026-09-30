"""Reverse-proxy dash_reports ``/vcnews/*`` to the standalone VC wiki backend.

Uses the shared ``argus_wiki_server.proxy_transport``: the dash app forwards HTTP to a sibling
VC-wiki process (the argus_wiki reader scoped to the vc_news vault). Backend address +
disable flag come from ``vc_wiki_server`` app_config.
"""
from __future__ import annotations

import logging

from flask import Response

from dash_reports.sub_servers.vc_wiki import config as vc_cfg

def setup_vc_wiki_proxy(server) -> None:
    if vc_cfg.proxy_disabled():
        logging.info("vc_wiki proxy disabled by config; /vcnews not mounted")
        return
    url_path = vc_cfg.url_path()
    logging.info("vc_wiki proxy registered: %s -> %s", url_path, vc_cfg.base_url())

    @server.route(url_path, defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
    @server.route(f"{url_path}/", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
    @server.route(f"{url_path}/<path:path>", methods=["GET", "POST", "OPTIONS"])
    def vc_wiki_proxy(path: str):
        return _forward(path)


def _forward(path: str) -> Response:
    # The standalone VC process imports this package before applying its vault
    # configuration. Importing the shared reader package earlier binds /wiki.
    from dash_reports.sub_servers.argus_wiki_server.proxy_transport import forward
    return forward(path, base_url=vc_cfg.base_url(), url_path=vc_cfg.url_path())


__all__ = ["setup_vc_wiki_proxy"]
