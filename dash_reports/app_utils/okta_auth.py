import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from dash_okta_oauth2 import OktaOAuth
from dash_okta_oauth2.auth import Auth
from dash_okta_oauth2.okta_oauth import OktaOAuth2ConsumerBlueprint
from flask import Response, current_app, redirect, render_template_string, request, session, url_for
from flask_dance.consumer import oauth_before_login
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


CALLBACK_PATH = "/login/okta/authorized"
HANDOFF_PATH = "/login/okta/handoff"
NONCE_KEY = "_okta_handoff_nonce"
STATE_TTL_SECONDS = 300
HANDOFF_TTL_SECONDS = 60


def _valid_url_host(parsed):
    try:
        parsed.port
    except ValueError:
        return False
    return (
        not parsed.username
        and not parsed.password
        and bool(parsed.hostname)
        and bool(re.fullmatch(r"[a-z0-9.-]+", parsed.hostname.lower()))
    )


def normalize_callback_url(value):
    callback_url = str(value or "").strip().rstrip("/")
    if not callback_url:
        return ""
    parsed = urlsplit(callback_url)
    if parsed.path != CALLBACK_PATH or parsed.query or parsed.fragment:
        raise ValueError(f"okta_callback_url must end with {CALLBACK_PATH}")
    if parsed.scheme not in ("http", "https") or not _valid_url_host(parsed):
        raise ValueError("okta_callback_url must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("okta_callback_url must use HTTPS outside localhost")
    return callback_url


def parse_host_patterns(value, callback_url):
    patterns = [item.strip().lower() for item in str(value or "").split(",") if item.strip()]
    callback_host = urlsplit(callback_url).netloc.lower()
    if callback_host not in patterns:
        patterns.append(callback_host)
    return tuple(patterns)


def _origin(url):
    parsed = urlsplit(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _request_origin():
    return _origin(request.host_url)


def _return_path():
    value = request.args.get("next", "/")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return "/"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _fernet():
    secret = str(current_app.secret_key).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


class CanonicalOktaOAuth2ConsumerBlueprint(OktaOAuth2ConsumerBlueprint):
    def __init__(self, app, callback_url, host_patterns, **kwargs):
        self.callback_url = callback_url
        self.host_patterns = host_patterns
        if urlsplit(callback_url).scheme == "http":
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        super().__init__(app=app, **kwargs)
        self.add_url_rule("/okta/handoff", endpoint="handoff", view_func=self.handoff, methods=["POST"])

    def _allowed_origin(self, origin):
        parsed = urlsplit(origin)
        host = parsed.netloc.lower()
        if parsed.scheme not in ("http", "https") or not host or not _valid_url_host(parsed):
            return False
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            return False
        return any(fnmatch.fnmatchcase(host, pattern) for pattern in self.host_patterns)

    def _state_serializer(self):
        return URLSafeTimedSerializer(current_app.secret_key, salt="argus-okta-state")

    def login(self):
        origin = _request_origin()
        if not self._allowed_origin(origin):
            return Response("OAuth return host is not allowed", status=400)
        nonce = secrets.token_urlsafe(32)
        session[NONCE_KEY] = nonce
        state = self._state_serializer().dumps({
            "origin": origin,
            "next": _return_path(),
            "nonce": nonce,
        })
        self.session.redirect_uri = self.callback_url
        url, _ = self.session.authorization_url(
            self.authorization_url,
            state=state,
            **self.authorization_url_params,
        )
        oauth_before_login.send(self, url=url)
        return redirect(url)

    def _load_state(self, state):
        try:
            payload = self._state_serializer().loads(state, max_age=STATE_TTL_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        if not self._allowed_origin(str(payload.get("origin", ""))):
            return None
        if not payload.get("nonce"):
            return None
        return payload

    def _fetch_token(self, state):
        self.session._state = state
        self.session.redirect_uri = self.callback_url
        return self.session.fetch_token(
            self.token_url,
            authorization_response=request.url,
            client_secret=self.client_secret,
            **self.token_url_params,
        )

    def authorized(self):
        state = request.args.get("state", "")
        payload = self._load_state(state)
        if not payload:
            return Response("Invalid or expired OAuth state", status=400)
        if request.args.get("error"):
            logging.warning("Okta authorization failed: %s", request.args.get("error"))
            return Response("Okta authorization failed", status=403)
        token = self._fetch_token(state)
        handoff = _fernet().encrypt(json.dumps({
            **payload,
            "token": token,
        }).encode()).decode()
        action = f"{payload['origin']}{HANDOFF_PATH}"
        body = render_template_string(
            """<!doctype html><html><body onload="document.forms[0].submit()">"""
            """<form method="post" action="{{ action }}"><input type="hidden" name="code" value="{{ code }}">"""
            """<button type="submit">Continue</button></form></body></html>""",
            action=action,
            code=handoff,
        )
        response = Response(body, content_type="text/html; charset=utf-8")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'unsafe-inline'; form-action {payload['origin']}"
        )
        return response

    def handoff(self):
        try:
            payload = json.loads(_fernet().decrypt(
                request.form.get("code", "").encode(),
                ttl=HANDOFF_TTL_SECONDS,
            ))
        except (InvalidToken, json.JSONDecodeError):
            return Response("Invalid or expired OAuth handoff", status=400)
        nonce = session.pop(NONCE_KEY, None)
        if (
            not nonce
            or not secrets.compare_digest(nonce, str(payload.get("nonce", "")))
            or payload.get("origin") != _request_origin()
            or not self._allowed_origin(payload.get("origin", ""))
            or not isinstance(payload.get("token"), dict)
            or not payload["token"].get("access_token")
        ):
            return Response("OAuth handoff verification failed", status=403)
        self.token = payload["token"]
        response = redirect(payload.get("next") or "/")
        response.headers["Cache-Control"] = "no-store"
        return response


class CanonicalOktaOAuth(OktaOAuth):
    def __init__(self, app, callback_url, host_patterns, additional_scopes=None):
        Auth.__init__(self, app)
        self.base_url = app.server.config["OKTA_BASE_URL"]
        self.logger = logging.getLogger(__name__)
        self.okta = CanonicalOktaOAuth2ConsumerBlueprint(
            app=app.server,
            callback_url=callback_url,
            host_patterns=host_patterns,
            name="okta",
            import_name=__name__,
            client_id=app.server.config["OKTA_OAUTH_CLIENT_ID"],
            client_secret=app.server.config["OKTA_OAUTH_CLIENT_SECRET"],
            base_url=self.base_url,
            token_url=f"{self.base_url}/token",
            authorization_url=f"{self.base_url}/authorize",
            scope=["openid", "email", "profile"] + (additional_scopes or []),
        )
        app.server.register_blueprint(self.okta, url_prefix="/login")

    def login_request(self):
        if self.okta.access_denied:
            self.okta.access_denied = False
            return Response(status=403)
        return redirect(url_for("okta.login", next=request.full_path.rstrip("?")))
