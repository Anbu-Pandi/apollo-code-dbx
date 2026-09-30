APP = "mcp_server"
DASH_REPORTS_APP = "dash_reports"


def first_config(*app_keys, default=""):
    from apo_config.config import config

    try:
        for app, key in app_keys:
            value = config.getconfig(app, key)
            if value:
                return value
    except Exception:
        return default
    return default


def public_base_url(fallback_prefix: str = "") -> str:
    """Public origin (``scheme://host[/prefix]``) the client actually reached us on.

    Honors the ``X-Forwarded-*`` headers set by an upstream reverse proxy / Front
    Door so the OAuth metadata we advertise matches the hostname the client
    connected through — whether that is the origin host directly
    (``argus.apollo.com``) or a fronting gateway (``api.apollo.com/argus``).

    ``fallback_prefix`` is used only when ``X-Forwarded-Prefix`` is absent. The
    root-level ``/.well-known/...`` discovery routes are reached on a gateway
    route that doesn't set that header, but the client encodes the gateway
    prefix in the URL path itself (RFC 8414 appends the issuer path), so the
    caller can recover it from there. We take only its leading segment so a
    trailing ``/mcp`` resource path is not mistaken for the prefix.
    """
    from flask import request

    def _first(value: str) -> str:
        return value.split(",")[0].strip()

    proto = _first(request.headers.get("X-Forwarded-Proto", "")) or request.scheme
    host = _first(request.headers.get("X-Forwarded-Host", "")) or request.host
    prefix = _first(request.headers.get("X-Forwarded-Prefix", "")).rstrip("/")
    if not prefix and fallback_prefix:
        prefix = "/" + fallback_prefix.strip("/").split("/")[0]
    return f"{proto}://{host}{prefix}"
