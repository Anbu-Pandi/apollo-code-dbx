"""Back-compat shim. Use `python -m dash_reports.sub_servers.argus_wiki_server` instead."""
from dash_reports.sub_servers.argus_wiki_server.server import main


if __name__ == "__main__":
    main()
