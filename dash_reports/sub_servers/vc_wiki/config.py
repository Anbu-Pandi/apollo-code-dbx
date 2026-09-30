from __future__ import annotations

from dash_reports.sub_servers.vc_wiki.config_catalog import APP, CATALOG


def _cfg(key: str) -> str:
    from apo_config.config import config
    default = (CATALOG.get(key) or {}).get(config.getenv(), "")
    value = config.getconfig(APP, key)
    return value if value not in (None, "") else default


def host() -> str:
    return _cfg("server.host")


def client_host() -> str:
    from common.service_discovery import resolve_service_hosts
    from apo_config.config import config
    return resolve_service_hosts(APP)[0] if config.getconfig(APP, "hosts") else _cfg("server.client_host")


def port() -> int:
    return int(_cfg("server.port"))


def scheme() -> str:
    return _cfg("server.scheme")


def url_path() -> str:
    return _cfg("server.url_path")


def proxy_disabled() -> bool:
    return str(_cfg("proxy_disabled")).strip().lower() in {"1", "true", "yes"}


def base_url() -> str:
    return f"{scheme()}://{client_host()}:{port()}{url_path()}"
