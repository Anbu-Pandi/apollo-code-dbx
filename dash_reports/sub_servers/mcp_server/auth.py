"""Optional auth layer for the MCP gateway.

Disabled by default. Enable with `apo_config` flag `mcp_server.auth_enabled = true`
(or `dash_reports.mcp_auth_enabled = true`).

When enabled, requests to `/mcp` must carry one of:
  - Okta bearer token (validated against Okta /userinfo), or
  - Machine API key (`Ocp-Apim-Subscription-Key` or `X-Argus-MCP-Key`).

The verified principal is forwarded to the MCP backend via `X-Argus-*` headers,
authenticated by an internal `X-Argus-Proxy-Secret` shared with the backend.
"""
import hmac
import logging

import requests
from flask import Response, request

from dash_reports.sub_servers.mcp_server import APP, DASH_REPORTS_APP, first_config


SENSITIVE_FORWARD_HEADERS = {
    "authorization",
    "ocp-apim-subscription-key",
    "x-argus-mcp-key",
}
TRUSTED_IDENTITY_HEADERS = {
    "x-argus-user",
    "x-argus-email",
    "x-argus-oid",
    "x-argus-groups",
    "x-argus-auth-source",
    "x-argus-proxy-secret",
}
STRIP_FROM_FORWARDED_REQUEST = SENSITIVE_FORWARD_HEADERS | TRUSTED_IDENTITY_HEADERS


def is_auth_enabled() -> bool:
    flag = first_config((APP, "auth_enabled"), (DASH_REPORTS_APP, "mcp_auth_enabled"))
    return str(flag).strip().lower() in {"1", "true", "yes", "on"}


def authenticate_request():
    bearer = _get_bearer_token()
    if bearer:
        principal = _validate_okta_bearer(bearer)
        if principal:
            return principal, None
        return None, Response("Invalid bearer token", status=401)

    return _validate_machine_api_key()


def trusted_principal_headers(principal: dict) -> dict:
    headers = {
        "X-Argus-Auth-Source": principal.get("auth_source", ""),
        "X-Argus-User": principal.get("user", ""),
        "X-Argus-Email": principal.get("email", ""),
        "X-Argus-Oid": principal.get("oid", ""),
        "X-Argus-Groups": principal.get("groups", ""),
    }
    secret = _proxy_secret()
    if secret:
        headers["X-Argus-Proxy-Secret"] = secret
    return headers


def _validate_machine_api_key():
    expected_key = _expected_machine_api_key()
    if not expected_key:
        return None, _oauth_challenge_response()

    api_key = request.headers.get("Ocp-Apim-Subscription-Key") or request.headers.get("X-Argus-MCP-Key")
    if not api_key:
        return None, _oauth_challenge_response()
    if not hmac.compare_digest(api_key, expected_key):
        return None, Response("Invalid machine API key", status=403)
    return {"auth_source": "machine_api_key"}, None


def _oauth_challenge_response():
    from dash_reports.sub_servers.mcp_server import public_base_url

    metadata_url = f"{public_base_url()}/.well-known/oauth-protected-resource/mcp"
    return Response(
        "Missing bearer token",
        status=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'},
    )


def _get_bearer_token() -> str:
    auth_header = request.headers.get("Authorization") or ""
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _validate_okta_bearer(token: str):
    userinfo_url = _okta_userinfo_url()
    if not userinfo_url:
        logging.error("MCP proxy cannot validate bearer token: okta_base_url is not configured")
        return None
    try:
        response = requests.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        logging.error("MCP proxy Okta userinfo call failed: %s", exc)
        return None
    if response.status_code != 200:
        logging.warning("MCP proxy Okta userinfo rejected token: status=%s", response.status_code)
        return None

    claims = response.json()
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    user = (claims.get("preferred_username") or email).split("@")[0].strip().lower()
    groups = claims.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    return {
        "auth_source": "okta_bearer",
        "user": user,
        "email": email,
        "oid": claims.get("sub") or claims.get("uid") or "",
        "groups": ",".join(str(group) for group in groups),
    }


def _expected_machine_api_key() -> str:
    return first_config(
        (APP, "machine_api_key"),
        (APP, "api_key_machine"),
        (DASH_REPORTS_APP, "api_key_mcp_machine"),
    )


def _okta_userinfo_url() -> str:
    base_url = _okta_base_url()
    if not base_url:
        return ""
    if base_url.endswith("/v1"):
        return f"{base_url}/userinfo"
    return f"{base_url}/v1/userinfo"


def _okta_issuer_url() -> str:
    base_url = _okta_base_url()
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


def _okta_base_url() -> str:
    return first_config((DASH_REPORTS_APP, "okta_base_url")).rstrip("/")


def _proxy_secret() -> str:
    return first_config(
        (APP, "proxy_secret"),
        (APP, "internal_proxy_secret"),
        (DASH_REPORTS_APP, "mcp_proxy_secret"),
    )
