from dash_reports.sub_servers.argus_wiki_server import config as _config

URL_PATH = _config.url_path()
SCAN_INTERVAL_SEC = _config.scan_interval_sec()
PREWARM_PAGES = _config.prewarm_pages()


__all__ = ["URL_PATH", "SCAN_INTERVAL_SEC", "PREWARM_PAGES"]
