"""OAuth Authorization Server proxy that bridges MCP clients to Okta.

MCP clients (Claude Web, ChatGPT, Cursor) speak OAuth + Dynamic Client
Registration to *this* server. This server speaks Authorization Code + PKCE to
the Apollo Okta authorization server using the existing confidential dash_reports
Okta app. Okta only ever sees one fixed redirect URI per environment:

  prod : https://argus.apollo.com/mcp/oauth/callback
  dev  : http://localhost:8050/mcp/oauth/callback

so DCR "just works" for clients without anyone pasting a client id.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Response, current_app, jsonify, redirect, request
from itsdangerous import BadSignature, URLSafeSerializer

from dash_reports.sub_servers.mcp_server import APP, DASH_REPORTS_APP, first_config
from dash_reports.sub_servers.mcp_server import auth

CALLBACK_PATH = "/mcp/oauth/callback"
AUTHORIZE_PATH = "/mcp/authorize"
TOKEN_PATH = "/mcp/token"
REGISTER_PATH = "/mcp/register"
OKTA_SCOPE = "openid profile email offline_access"

_TXN_TTL = 600
_CODE_TTL = 300
_CLIENT_PREFIX = "mcp-"
_CLIENT_SALT = "argus-mcp-oauth-client"
_TXN_CONTEXT = "argus-mcp-oauth-transaction"
_CODE_CONTEXT = "argus-mcp-oauth-code"


def register_oauth_proxy(server):
    # The trailing `<path:_prefix>` variants serve discovery when the client
    # appends the gateway path segment (e.g. ".../argus") to the well-known URL,
    # as RFC 8414/9728 clients do for a path-based issuer. We accept any suffix
    # so the app stays agnostic to the actual prefix value (it comes from the
    # forwarded headers via _public_base(), never hardcoded here).
    @server.route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    @server.route("/.well-known/oauth-protected-resource/<path:_prefix>", methods=["GET"])
    def mcp_oauth_protected_resource_metadata(_prefix=None):
        base = _public_base(_prefix)
        return jsonify({
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile", "email"],
        })

    # `openid-configuration` is the OIDC fallback some clients probe before the
    # OAuth-native endpoint; alias it to the same metadata so it never falls
    # through to the Okta-protected catch-all (which 302s to /login/okta).
    @server.route("/.well-known/oauth-authorization-server", methods=["GET"])
    @server.route("/.well-known/oauth-authorization-server/mcp", methods=["GET"])
    @server.route("/.well-known/oauth-authorization-server/<path:_prefix>", methods=["GET"])
    @server.route("/.well-known/openid-configuration", methods=["GET"])
    @server.route("/.well-known/openid-configuration/<path:_prefix>", methods=["GET"])
    def mcp_oauth_authorization_server_metadata(_prefix=None):
        base = _public_base(_prefix)
        return jsonify({
            "issuer": base,
            "authorization_endpoint": f"{base}{AUTHORIZE_PATH}",
            "token_endpoint": f"{base}{TOKEN_PATH}",
            "registration_endpoint": f"{base}{REGISTER_PATH}",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
        })

    @server.route(REGISTER_PATH, methods=["POST"])
    def mcp_oauth_register():
        body = request.get_json(silent=True) or {}
        redirect_uris = body.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris or not all(
            isinstance(uri, str) and uri for uri in redirect_uris
        ):
            return jsonify({"error": "invalid_redirect_uri"}), 400
        client_id = _encode_client(redirect_uris)
        logging.info("MCP DCR registered client=%s redirect_uris=%s", client_id, redirect_uris)
        return jsonify({
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }), 201

    @server.route(AUTHORIZE_PATH, methods=["GET"])
    def mcp_oauth_authorize():
        client_id = request.args.get("client_id", "")
        redirect_uri = request.args.get("redirect_uri", "")
        code_challenge = request.args.get("code_challenge", "")
        client = _decode_client(client_id)
        if not client or redirect_uri not in client["redirect_uris"]:
            return Response("Unknown client or redirect_uri", status=400)
        if not code_challenge:
            return Response("PKCE code_challenge required", status=400)

        okta_verifier, okta_challenge = _pkce_pair()
        txn = _seal({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": request.args.get("state", ""),
            "code_challenge": code_challenge,
            "okta_verifier": okta_verifier,
        }, _TXN_CONTEXT)
        okta_params = urlencode({
            "client_id": _okta_client()[0],
            "response_type": "code",
            "scope": OKTA_SCOPE,
            "redirect_uri": _callback_url(),
            "state": txn,
            "code_challenge": okta_challenge,
            "code_challenge_method": "S256",
        })
        return redirect(f"{_okta_authorize_url()}?{okta_params}")

    @server.route(CALLBACK_PATH, methods=["GET"])
    def mcp_oauth_callback():
        txn = _unseal(request.args.get("state", ""), _TXN_CONTEXT, _TXN_TTL)
        if not txn:
            return Response("Unknown or expired authorization state", status=400)
        okta_code = request.args.get("code", "")
        if not okta_code:
            return Response(f"Okta returned no code: {request.args.get('error', '')}", status=400)

        code = _seal({
            "client_id": txn["client_id"],
            "redirect_uri": txn["redirect_uri"],
            "code_challenge": txn["code_challenge"],
            "okta_code": okta_code,
            "okta_verifier": txn["okta_verifier"],
        }, _CODE_CONTEXT)
        client_params = urlencode({"code": code, "state": txn["state"]})
        return redirect(f"{txn['redirect_uri']}?{client_params}")

    @server.route(TOKEN_PATH, methods=["POST"])
    def mcp_oauth_token():
        grant_type = request.form.get("grant_type", "")
        if grant_type == "refresh_token":
            tokens = _okta_refresh(request.form.get("refresh_token", ""))
            if not tokens:
                return jsonify({"error": "invalid_grant"}), 400
            return jsonify(tokens)
        if grant_type != "authorization_code":
            return jsonify({"error": "unsupported_grant_type"}), 400

        record = _unseal(request.form.get("code", ""), _CODE_CONTEXT, _CODE_TTL)
        if not record:
            return jsonify({"error": "invalid_grant"}), 400
        if (
            not hmac.compare_digest(request.form.get("client_id", ""), record["client_id"])
            or not hmac.compare_digest(request.form.get("redirect_uri", ""), record["redirect_uri"])
        ):
            return jsonify({"error": "invalid_grant"}), 400
        if not _verify_pkce(request.form.get("code_verifier", ""), record["code_challenge"]):
            return jsonify({"error": "invalid_grant", "error_description": "PKCE mismatch"}), 400
        tokens = _okta_exchange_code(record["okta_code"], record["okta_verifier"])
        if not tokens:
            return jsonify({"error": "invalid_grant"}), 400
        return jsonify(tokens)


def _public_base(fallback_prefix: str = "") -> str:
    """Client-facing base, matching the host the client actually connected through.

    Honors ``X-Forwarded-*`` so the advertised metadata lines up whether the
    client reached us directly or via a fronting gateway. ``fallback_prefix`` is
    the gateway prefix recovered from the discovery URL path, used only when the
    route did not set ``X-Forwarded-Prefix`` (see ``public_base_url``).
    """
    from dash_reports.sub_servers.mcp_server import public_base_url

    return public_base_url(fallback_prefix)


def _canonical_base() -> str:
    """Fixed base for the Okta ``redirect_uri`` only.

    Okta has exactly one registered callback per environment, so this must NOT
    vary per request. Derived from the configured connector URL; falls back to
    the per-request base when nothing is configured (e.g. local dev / tests).
    """
    from dash_reports.sub_servers.mcp_server.flask_proxy import _configured_connector_url

    connector = _configured_connector_url()
    if connector:
        return connector[:-len("/mcp")] if connector.endswith("/mcp") else connector
    return _public_base()


def _callback_url() -> str:
    return f"{_canonical_base()}{CALLBACK_PATH}"


def _okta_authorize_url() -> str:
    return f"{auth._okta_base_url()}/authorize"


def _okta_token_url() -> str:
    return f"{auth._okta_base_url()}/token"


def _okta_exchange_code(code: str, verifier: str) -> dict:
    return _okta_token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _callback_url(),
        "code_verifier": verifier,
    })


def _okta_refresh(refresh_token: str) -> dict:
    if not refresh_token:
        return {}
    return _okta_token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": OKTA_SCOPE,
    })


def _okta_token_request(payload: dict) -> dict:
    client_id, client_secret = _okta_client()
    if not client_id or not client_secret:
        logging.error("MCP OAuth proxy missing okta_client_id/okta_client_secret")
        return {}
    response = requests.post(_okta_token_url(), data=payload, auth=(client_id, client_secret), timeout=15)
    if response.status_code != 200:
        logging.error("MCP OAuth proxy Okta token call failed: status=%s body=%s", response.status_code, response.text[:300])
        return {}
    return response.json()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    return verifier, _pkce_challenge(verifier)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    return hmac.compare_digest(_pkce_challenge(verifier), challenge)


def _secret() -> bytes:
    if not current_app.secret_key:
        raise RuntimeError("flask_secret_key is required for MCP OAuth")
    return str(current_app.secret_key).encode()


def _encode_client(redirect_uris: list[str]) -> str:
    token = URLSafeSerializer(_secret(), salt=_CLIENT_SALT).dumps({"redirect_uris": redirect_uris})
    return f"{_CLIENT_PREFIX}{token}"


def _decode_client(client_id: str) -> dict | None:
    if not client_id.startswith(_CLIENT_PREFIX):
        return None
    try:
        client = URLSafeSerializer(_secret(), salt=_CLIENT_SALT).loads(client_id[len(_CLIENT_PREFIX):])
    except BadSignature:
        return None
    redirect_uris = client.get("redirect_uris") if isinstance(client, dict) else None
    if not isinstance(redirect_uris, list):
        return None
    return client


def _fernet(context: str) -> Fernet:
    digest = hashlib.sha256(_secret() + b":" + context.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _seal(payload: dict, context: str) -> str:
    token = _fernet(context).encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return token.rstrip("=")


def _unseal(token: str, context: str, ttl: int) -> dict | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(_fernet(context).decrypt(padded.encode(), ttl=ttl))
    except (InvalidToken, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _okta_client() -> tuple[str, str]:
    client_id = first_config((APP, "okta_client_id"), (DASH_REPORTS_APP, "okta_client_id"))
    client_secret = first_config((APP, "okta_client_secret"), (DASH_REPORTS_APP, "okta_client_secret"))
    return client_id, client_secret
