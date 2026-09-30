from dash_reports.sub_servers.argus_wiki_server.config_catalog import APP
from dash_reports.sub_servers.argus_wiki_server.flask_proxy import (
    setup_argus_wiki_proxy,
    setup_argus_wiki_routes,
)


__all__ = ["APP", "setup_argus_wiki_proxy", "setup_argus_wiki_routes"]
