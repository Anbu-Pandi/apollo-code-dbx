import logging
import os
import socket
import threading
import time
from importlib.metadata import PackageNotFoundError, version
import dash
from dash import dcc, html, Input, Output, callback
from flask import Flask, session
from dash_okta_oauth2 import OktaOAuth
from dash_reports.app_utils.okta_auth import (
    CanonicalOktaOAuth,
    normalize_callback_url,
    parse_host_patterns,
)

# Cache the Okta /userinfo result in the signed Flask session cookie. Without
# this the page callback did a blocking HTTP round-trip to Okta on EVERY page
# navigation (100-500ms added to every load). Groups change rarely, so a short
# TTL is safe; the cookie is per-user and signed (SECRET_KEY is set).
_OKTA_USERINFO_TTL_SEC = 900
_READINESS_PACKAGES = (
    "dash",
    "dash-ag-grid",
    "dash-extensions",
    "dash-okta-oauth2",
    "cryptography",
    "pyOpenSSL",
)


class FleetDash(dash.Dash):
    def __setattr__(self, name, value):
        if name == "title" and getattr(self, "_title_locked", False):
            return
        super().__setattr__(name, value)

    def lock_title(self):
        self._title_locked = True


def _package_versions():
    packages = {}
    for package in _READINESS_PACKAGES:
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "missing"
    return packages


def _okta_user_and_groups(auth):
    now = time.time()
    cached = session.get("_okta_userinfo")
    if cached and cached.get("email") and cached.get("subject") and (now - float(cached.get("ts", 0))) < _OKTA_USERINFO_TTL_SEC:
        return cached.get("user", ""), cached.get("groups", "")
    data = auth.okta.session.get(auth.okta.base_url + "/userinfo").json()
    user = (data.get("email", "") or "").split("@")[0]
    groups = data.get("groups", "")
    session["_okta_userinfo"] = {"user": user, "groups": groups, "ts": now,
                               "email": data.get("email", ""), "subject": data.get("sub", "")}
    return user, groups

from dash_reports.report_common.pages.link_and_headers import get_base_header_menu
from dash_reports.report_common.pages import user_info
from dash_reports.app_utils.teams_bot_integration import (
    create_teams_bot_blueprint, 
    initialize_teams_bot, 
    unwrap_api_endpoints_from_okta
)
from dash_reports.app_utils.page_router import PageRouter
from dash_reports.pred_tracker_viewer.pred_tracker_proxy import setup_pred_tracker_proxy
from dash_reports.industry_tracker.flask_proxy import setup_industry_tracker_proxy
from dash_reports.sub_servers.html_sub_server.flask_proxy import setup_html_sub_server_proxy
from dash_reports.sub_servers.mcp_server.flask_proxy import setup_mcp_server_proxy
from dash_reports.sub_servers.vc_wiki.proxy import setup_vc_wiki_proxy
from dash_reports.airflow_proxy import setup_airflow_proxy
from dash_reports.cdx_options.ws_events import register_cdx_websocket
from apo_config.config import config
from dash_reports import APP

class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        return not any(path in message for path in ['/health', '/healthz'])

def create_flask_server(use_okta, is_debug_mode):
    server = Flask(__name__)
    serving_node = socket.gethostname()
    build_id = os.environ.get("ARGUS_BUILD_ID", "unversioned")
    package_versions = _package_versions()

    # When a TLS-terminating proxy/LB fronts us (behind_reverse_proxy), the app
    # sees plain HTTP on loopback. Trust the proxy's X-Forwarded-* so flask-dance
    # builds https:// Okta redirect_uris (otherwise callbacks go to http:// and
    # Okta rejects with MismatchingStateError) and request.host/scheme are right.
    # Only one hop is trusted, and only in this mode — the app binds loopback
    # here, so the proxy is the sole possible client. No effect on direct-TLS or
    # local runs where these headers are absent.
    from dash_reports.app_utils.runtime_flags import is_behind_proxy
    if is_behind_proxy():
        from werkzeug.middleware.proxy_fix import ProxyFix
        server.wsgi_app = ProxyFix(server.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        logging.info("ProxyFix enabled: trusting X-Forwarded-* from the TLS-terminating proxy (1 hop)")

    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addFilter(HealthCheckFilter())
    
    @server.route('/health')
    def health_check():
        from flask import jsonify
        return jsonify({
            'status': 'healthy',
            'service': 'dash_reports',
            'mode': 'debug' if is_debug_mode else 'production'
        }), 200
    
    @server.route('/healthz')
    def health_check_k8s():
        return 'OK', 200

    @server.route('/ready')
    def readiness_check():
        from flask import jsonify

        teams_ready = True
        if server.config.get("ARGUS_TEAMS_REQUIRED"):
            from dash_reports.app_utils import teams_bot_integration

            teams_ready = (
                teams_bot_integration.bot is not None
                and teams_bot_integration.adapter is not None
            )
        ready = bool(server.config.get("ARGUS_READY")) and teams_ready
        return jsonify({
            "status": "ready" if ready else "not_ready",
            "service": "dash_reports",
            "node": serving_node,
            "build_id": build_id,
            "callbacks": server.config.get("ARGUS_CALLBACK_COUNT", 0),
            "teams_ready": teams_ready,
            "packages": package_versions,
        }), 200 if ready else 503

    @server.after_request
    def _tag_serving_node(resp):
        resp.headers['X-Served-By'] = serving_node
        resp.headers['X-Argus-Build'] = build_id
        return resp

    if use_okta:
        server.config.update({
            'OKTA_OAUTH_CLIENT_ID': config.getconfig(APP, 'okta_client_id'),
            'OKTA_OAUTH_CLIENT_SECRET': config.getconfig(APP, 'okta_client_secret'),
            'OKTA_BASE_URL': config.getconfig(APP, 'okta_base_url')
        })
    
    # The session cookie is signed with this key and now carries the cached Okta
    # user/groups (used for access control), so it MUST be a strong secret — a
    # guessable key lets a client forge a cookie with elevated groups. It must
    # also be the SAME across all instances so cookies validate fleet-wide behind
    # the load balancer. Read it from config; only fall back to a dev key when
    # running locally (is_debug_mode), and warn loudly otherwise.
    secret_key = None
    try:
        secret_key = config.getconfig(APP, 'flask_secret_key')
    except Exception:
        secret_key = None
    if not secret_key:
        if is_debug_mode:
            secret_key = 'python-dash-local-dev'
        elif use_okta:
            raise RuntimeError("flask_secret_key is required in production")
        else:
            secret_key = 'python-dash'
    server.config.update(SECRET_KEY=secret_key)
    logging.info("✅ Health check endpoints registered: /health and /healthz")
    return server

def register_teams_bot(server, is_debug_mode, get_port_func):
    api_bp = create_teams_bot_blueprint()
    server.register_blueprint(api_bp)
    threading.Thread(
        target=initialize_teams_bot,
        args=(is_debug_mode, get_port_func),
        name='TeamsBotInit',
        daemon=True,
    ).start()


def setup_argus_wiki_safe(server):
    try:
        from dash_reports.sub_servers.argus_wiki_server import setup_argus_wiki_proxy
        setup_argus_wiki_proxy(server)
    except Exception as exc:
        logging.warning("Argus Wiki disabled: %s: %s", type(exc).__name__, exc)
        from flask import Response
        reason = f"{type(exc).__name__}: {exc}"

        @server.route('/wiki')
        @server.route('/wiki/', defaults={'_rest': ''})
        @server.route('/wiki/<path:_rest>')
        def argus_wiki_unavailable(_rest=''):
            try:
                from dash_reports.sub_servers.argus_wiki_server.flask_proxy import _backend_down_html
                html = _backend_down_html()
            except Exception:
                html = (
                    "<h2>Argus Wiki unavailable</h2>"
                    f"<p>Import failed: <code>{reason}</code></p>"
                    "<p>Start the sub-server: "
                    "<code>python -m dash_reports.sub_servers.argus_wiki_server --env dev</code></p>"
                )
            return Response(html, status=503, content_type='text/html; charset=utf-8')

def create_dash_app(server, is_debug_mode):
    import os
    assets_folder = os.path.join(os.path.dirname(__file__), '..', 'assets')
    assets_folder = os.path.abspath(assets_folder)
    
    logging.info(f'Dash assets folder: {assets_folder}')
    logging.info(f'Assets folder exists: {os.path.exists(assets_folder)}')
    
    app = FleetDash(
        __name__,
        update_title=None,
        server=server,
        assets_folder=assets_folder,
        meta_tags=[{"name": "viewport", "content": "width=device-width"}]
    )
    app.title = 'Market Analytics Reports'
    app.lock_title()
    app.config.suppress_callback_exceptions = not is_debug_mode

    # Global dark/light theme toggle (bottom-right on every page). Injected into
    # <head> after {%css%} so the dark rules override the asset stylesheets and
    # data-theme is set before first paint (no flash). Shared with the Flask
    # sub-servers via report_common.theme so the whole surface toggles together.
    from dash_reports.report_common import theme
    app.index_string = theme.dash_index_string()
    
    app_kids = [
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="page-title-sink"),
        html.Div(id='common-page-header', children=[get_base_header_menu(app)]),
        html.Div(id='specific-page-header', children=[]),
        html.Div(id="page-content-static"),
        html.Div(id="page-content-dynamic"),
        html.Div(id="page-content-dynamic-1"),
    ]
    app_kids += user_info.get_logged_user_controls()
    app.layout = html.Div(app_kids)
    
    app.css.append_css({'external_url': '/assets/styles.css'})
    
    return app

def setup_okta_auth(app, use_okta):
    if not use_okta:
        return None
    
    additional_scopes = []
    callback_url = normalize_callback_url(config.getconfig(APP, "okta_callback_url"))
    if callback_url:
        if app.server.secret_key in ("python-dash", "python-dash-local-dev"):
            raise RuntimeError("flask_secret_key is required for canonical Okta callbacks")
        host_patterns = parse_host_patterns(
            config.getconfig(APP, "okta_return_host_patterns"),
            callback_url,
        )
        auth = CanonicalOktaOAuth(app, callback_url, host_patterns, additional_scopes)
        logging.info(
            "Okta canonical callback enabled: callback=%s return_hosts=%s",
            callback_url,
            ",".join(host_patterns),
        )
    else:
        auth = OktaOAuth(app, additional_scopes)
    unwrap_api_endpoints_from_okta(app.server)
    return auth

def register_callbacks(app, page_router, auth, use_okta):
    app.clientside_callback(
        """
        function(pathname) {
            const key = (pathname || '/').split('/').filter(Boolean)[0] || '';
            const names = {
                'issuer-info': 'Issuer Information',
                'data-catalog': 'Argus Data Discovery',
                'vol-dashboard': 'Vol Market Dashboard',
                'ai-chat': 'AI Chat',
                'cdx-options': 'CDX Options Trading'
            };
            const title = names[key] || (key
                ? key.split('-').map(x => x.charAt(0).toUpperCase() + x.slice(1)).join(' ')
                : 'Market Analytics Reports');
            document.title = title;
            return title;
        }
        """,
        Output("page-title-sink", "data"),
        Input("url", "pathname"),
    )

    @callback(
        Output("specific-page-header", "children"),
        Input("url", "pathname")
    )
    def on_url_request_change_header(pathname):
        return page_router.get_header_for_path(pathname)
    
    @callback(
        Output("page-content-static", "children"),
        Output("page-content-dynamic", "children"),
        Output(user_info.LOGGED_USER_ID, 'data'),
        Output(user_info.LOGGED_USER_GROUPS_ID, 'data'),
        [Input("url", "pathname"), Input("url", "search")]
    )
    def on_url_request_change_page(pathname, search):
        user = 'okta_switched_off'
        groups = 'okta_switched_off'

        if use_okta and auth:
            user, groups = _okta_user_and_groups(auth)

        static, dynamic = page_router.get_content_for_path(pathname, search, user, groups)
        return static, dynamic, user, groups

def create_application(use_okta, is_debug_mode, get_port_func, *, run_teams_bot=True):
    """Build the app.

    Everything here is horizontally scalable — the web/report/MCP surface is a
    stateless gateway to the sub-servers, and the CDX websocket/Flight bridge just
    RELAYS cdx-options-server (each instance polls the shared sub-server and
    broadcasts to its own ws clients), so N identical instances behind the load
    balancer is correct. The ONE exception is the Teams bot: it keeps in-process
    conversation memory (MemoryStorage/ConversationState) plus a BYOT tool
    registration, so N copies fragment mid-conversation context and duplicate the
    registration. Teams is low-volume and doesn't need to scale, so run it on a
    single instance (``run_teams_bot=False`` on the scale-out 'web' instances).
    Default True keeps the single-process all-in-one behavior unchanged.
    """
    server = create_flask_server(use_okta, is_debug_mode)
    server.config["ARGUS_TEAMS_REQUIRED"] = run_teams_bot
    auth = None
    jobs_identity_provider = (lambda: _okta_user_and_groups(auth)) if use_okta else None
    if run_teams_bot:
        register_teams_bot(server, is_debug_mode, get_port_func)
    else:
        logging.info("web role: Teams bot NOT started (runs on the single Teams instance)")

    sock = setup_pred_tracker_proxy(server)
    logging.info("Pred Tracker proxy registered: /pred-tracker-api/")

    setup_industry_tracker_proxy(server)
    setup_html_sub_server_proxy(server)
    setup_mcp_server_proxy(server)
    setup_vc_wiki_proxy(server)
    setup_airflow_proxy(server, jobs_identity_provider)
    setup_argus_wiki_safe(server)

    # CDX ws/bridge relays the shared cdx-options-server, so it's safe on every
    # instance (a ws client is served by whichever instance the LB routed it to).
    register_cdx_websocket(server, sock=sock)
    logging.info("CDX Options WebSocket registered: /cdx-options/ws")
    logging.info("CDX Options Flight bridge active (polling cdx-options-server via gRPC)")

    app = create_dash_app(server, is_debug_mode)
    auth = setup_okta_auth(app, use_okta)
    if use_okta:
        from dash_reports.subscriptions.runtime import okta_principal
        server.extensions["email_subscription_identity"] = lambda: okta_principal(auth)

    page_router = PageRouter(app)
    register_callbacks(app, page_router, auth, use_okta)
    server.config["ARGUS_CALLBACK_COUNT"] = len(app.callback_map)
    server.config["ARGUS_READY"] = True

    return app
