from __future__ import annotations

APP = "vc_wiki_server"

CATALOG = {
    "server.host": {"dev": "127.0.0.1", "prod": "0.0.0.0"},
    "server.client_host": {"dev": "127.0.0.1", "prod": "pue2mal01"},
    "hosts": {"dev": "127.0.0.1", "prod": "pue2mal01/pue2mal02"},
    "server.port": {"dev": "8071", "prod": "8071"},
    "server.scheme": {"dev": "http", "prod": "http"},
    "server.url_path": {"dev": "/vcnews", "prod": "/vcnews"},
    "proxy_disabled": {"dev": "false", "prod": "false"},
    "vault_root": {"dev": "C:/Users/agite/dev/vc_wiki_vault", "prod": "//pue2mal01/e/wiki-vc"},
    "research_root": {"dev": "C:/Users/agite/dev/vc_wiki_raw", "prod": "//pue2mal01/e/wiki-vc-raw"},
    "es_url": {"dev": "http://localhost:9201", "prod": "http://pue2mal01:9200"},
    "es_index": {"dev": "vc_news", "prod": "vc_news"},
}
