"""Standalone Argus Wiki Flask backend.

Runs the wiki on its own port so dash_reports can proxy `/wiki/*` to it
without sharing memory, workers, or restart lifecycle.

All deployment knobs (host, port, scheme, prewarm list, scan interval) come
from `apo_config` via `argus_wiki_server.config`; the catalog ships sensible
defaults.
"""
from __future__ import annotations

import inspect
import logging

from flask import Flask

from common.utils.envsetup import get_base_parser, init_app_using_parser, kill_stale_server
from dash_reports.sub_servers.argus_wiki_server import config as wiki_cfg
from dash_reports.sub_servers.argus_wiki_server.routes import setup_argus_wiki_routes


_ARGUS_SERVER_CMDLINE_MARKERS = (
    "argus_wiki_server",
    "argus-wiki-server",
    "dash_reports.sub_servers.argus_wiki_server",
)


def create_app() -> Flask:
    app = Flask("argus_wiki_server")

    @app.route("/health")
    def _health():
        return {"status": "healthy", "service": "argus_wiki_server"}, 200

    setup_argus_wiki_routes(app)
    return app


def main() -> None:
    parser = get_base_parser("argus_wiki_server")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-kill-stale", action="store_true")
    args = init_app_using_parser(parser)
    host = args.host or wiki_cfg.host()
    port = args.port or wiki_cfg.port()

    if not args.no_kill_stale:
        if "cmdline_must_contain" in inspect.signature(kill_stale_server).parameters:
            kill_stale_server(port, cmdline_must_contain=_ARGUS_SERVER_CMDLINE_MARKERS)
        else:
            logging.warning(
                "kill_stale_server does not support cmdline filtering; skipping stale-process kill on port %s",
                port,
            )

    app = create_app()
    logging.info("argus_wiki_server listening on %s:%d (url_path=%s)",
                 host, port, wiki_cfg.url_path())
    try:
        from waitress import serve
    except ImportError:
        logging.warning("waitress is not installed; falling back to Flask development server")
        app.run(host=host, port=port, debug=False, threaded=True)
        return
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
