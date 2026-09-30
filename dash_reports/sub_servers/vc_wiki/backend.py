"""Standalone VC wiki backend — the argus_wiki reader scoped to the vc_news vault.

Reuses ``argus_wiki_server`` UNCHANGED: applies the VC vault env (so the reader binds
to the vc_news vault/index), overrides the route mount to ``/vcnews`` + the VC port,
then serves the same Flask app. Run:

    python -m dash_reports.sub_servers.vc_wiki --env dev
"""
from __future__ import annotations

import logging


def main() -> None:
    import os

    from common.utils.envsetup import get_base_parser, init_app_using_parser
    parser = get_base_parser("vc_wiki_server")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = init_app_using_parser(parser)
    from data_importer.vc.news import wiki_env
    r = wiki_env.apply(args.env)
    os.environ["ARGUS_WIKI_URL_PATH"] = r["url_path"]

    from dash_reports.sub_servers.vc_wiki import config as vc_cfg
    from dash_reports.sub_servers.argus_wiki_server.server import create_app
    app = create_app()
    host = args.host or vc_cfg.host()
    port = args.port or vc_cfg.port()
    logging.info("vc_wiki_server listening on %s:%d (url_path=%s, index=vc_news)",
                 host, port, vc_cfg.url_path())
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
