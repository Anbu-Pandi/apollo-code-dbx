import logging
import time

import requests
from flask import Response, request, stream_with_context

from apo_config.config import required_config
from common.service_discovery import resolve_service_urls
from dash_reports.report_common import theme
from dash_reports.sub_servers.mcp_server import APP, DASH_REPORTS_APP, first_config
from dash_reports.sub_servers.mcp_server import auth


URL_PATH = "/mcp"
_CONNECT_TIMEOUT_SEC = 15.0
_READ_TIMEOUT_SEC = 600.0
_MAX_UPSTREAM_ATTEMPTS = 4
_RETRY_BACKOFF_SEC = 1.5
_RETRY_AFTER_UNAVAILABLE = "3"
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _close_response(resp) -> None:
    if resp is None:
        return
    try:
        resp.close()
    except Exception:
        pass


def _unavailable_response(backend_url: str, exc: BaseException | None) -> Response:
    if exc is not None:
        logging.error("MCP sub-server is not reachable at %s: %s", backend_url, exc)
    msg = f"MCP sub-server is not reachable at {backend_url}"
    return Response(
        msg,
        status=503,
        headers={"Retry-After": _RETRY_AFTER_UNAVAILABLE},
    )


def get_mcp_base_urls() -> list:
    # MCP is stateless (started with stateless_http=True) and runs co-located on
    # every gateway node. resolve_service_urls returns candidates local-first
    # (127.0.0.1) with the peer node(s) as fallback -- "whichever is available"
    # -- driven by the mcp_server.hosts config list. (Contrast pred_tracker: a
    # stateful singleton still pinned via internal_service_host.)
    return resolve_service_urls(APP, path=required_config(APP, "server.path"))


def get_mcp_base_url() -> str:
    return get_mcp_base_urls()[0]


def _can_retry(method, headers):
    return method.upper() in {"GET", "HEAD"} or any(
        key.lower() == "idempotency-key" and value
        for key, value in headers.items()
    )


def _request_with_retries(method, target_url, headers, body, timeout):
    """Issue one upstream request with bounded retries. Returns (response, None)
    on any HTTP response (including a persistent 5xx), or (None, exc) if the
    connection kept failing -- signalling the caller to try the next backend."""
    response = None
    last_exc: BaseException | None = None
    attempts = _MAX_UPSTREAM_ATTEMPTS if _can_retry(method, headers) else 1
    for attempt in range(attempts):
        _close_response(response)
        response = None
        try:
            response = requests.request(
                method, target_url, headers=headers, data=body,
                stream=True, timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                return None, last_exc
            time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        if response.status_code in (502, 503, 504) and attempt + 1 < attempts:
            _close_response(response)
            response = None
            time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        return response, None
    return response, last_exc


def setup_mcp_server_proxy(server):
    auth_enabled = auth.is_auth_enabled()

    if auth_enabled:
        from dash_reports.sub_servers.mcp_server import oauth_proxy

        oauth_proxy.register_oauth_proxy(server)

    @server.route(f"{URL_PATH}/help", methods=["GET"])
    def mcp_server_help():
        return Response(
            _help_html(get_mcp_connector_url(), auth_enabled),
            status=200,
            content_type="text/html",
        )

    @server.route(URL_PATH, defaults={"path": ""}, methods=["GET", "POST", "DELETE", "OPTIONS"])
    @server.route(f"{URL_PATH}/<path:path>", methods=["GET", "POST", "DELETE", "OPTIONS"])
    def mcp_server_proxy(path):
        if request.method == "OPTIONS":
            return Response(status=204)
        if auth_enabled:
            principal, err = auth.authenticate_request()
            if err is not None:
                return err
            extra_headers = auth.trusted_principal_headers(principal)
        else:
            extra_headers = {}

        headers = {
            key: value
            for key, value in request.headers
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in auth.STRIP_FROM_FORWARDED_REQUEST
        }
        headers.update(extra_headers)

        body = request.get_data()
        timeout = (_CONNECT_TIMEOUT_SEC, _READ_TIMEOUT_SEC)
        query = f"?{request.query_string.decode('utf-8')}" if request.query_string else ""

        # Try each co-located backend local-first; on a connection failure that
        # exhausts retries for one, fall through to the next ("whichever is
        # available"). A real HTTP response (even 5xx) is returned as-is.
        candidates = get_mcp_base_urls()
        if not _can_retry(request.method, headers):
            candidates = candidates[:1]
        response = None
        last_exc: BaseException | None = None
        for backend_url in candidates:
            base = backend_url if not path else f"{backend_url}/{path}"
            response, last_exc = _request_with_retries(
                request.method, f"{base}{query}", headers, body, timeout
            )
            if response is not None:
                break
        if response is None:
            return _unavailable_response(candidates[-1] if candidates else "?", last_exc)

        response_headers = [
            (name, value)
            for name, value in response.raw.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
        ]

        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(generate()),
            status=response.status_code,
            headers=response_headers,
            direct_passthrough=True,
        )

    @server.after_request
    def _mcp_cors(resp):
        path = request.path or ""
        if path.startswith(URL_PATH) or path.startswith("/.well-known/oauth"):
            resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin") or "*"
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, mcp-protocol-version, mcp-session-id"
            resp.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate, mcp-session-id"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    logging.info(
        "MCP server proxy registered: %s -> %s (auth_enabled=%s)",
        URL_PATH, get_mcp_base_url(), auth_enabled,
    )


def get_mcp_connector_url() -> str:
    configured_url = _configured_connector_url()
    if configured_url:
        return configured_url
    return f"{request.url_root.rstrip('/')}{URL_PATH}"


def _configured_connector_url() -> str:
    return first_config(
        (APP, "connector_url"),
        (APP, "public_url"),
        (APP, "external_url"),
        (DASH_REPORTS_APP, "mcp_connector_url"),
        (DASH_REPORTS_APP, "mcp_public_url"),
    ).rstrip("/")


def _help_html(mcp_url: str, auth_enabled: bool) -> str:
    if auth_enabled:
        auth_status_label = "Okta / OAuth"
        auth_box = (
            '<div class="highlight-box">'
            "<strong>🔐 Auth: Okta / OAuth.</strong> The first connect opens a browser to "
            "log in with your Apollo Okta account. After that the client caches a bearer "
            "token and every call flows automatically — same feel as a browser session."
            "</div>"
        )
        footer_caveat = (
            "Machine API keys are for service integrations only. Human users should connect "
            "with Okta / OAuth so Argus Tools receives the verified principal."
        )
    else:
        auth_status_label = "open (disabled)"
        auth_box = (
            '<div class="warning-box">'
            "<strong>Auth is currently disabled.</strong> Just register and go &mdash; we "
            "will switch on Okta / OAuth later. Anyone on the network can call this connector "
            "right now, so do not use it for sensitive workflows until auth is turned back on."
            "</div>"
        )
        footer_caveat = (
            "The connector is currently open. When Okta / OAuth is turned on, every client "
            "will go through your Apollo SSO automatically."
        )

    cursor_json = (
        '{\n'
        '  "mcpServers": {\n'
        '    "argus-tools": {\n'
        '      "type": "http",\n'
        f'      "url": "{mcp_url}"\n'
        '    }\n'
        '  }\n'
        '}'
    )
    openai_json = (
        '{\n'
        '  "type": "mcp",\n'
        '  "server_label": "argus-tools",\n'
        f'  "server_url": "{mcp_url}"\n'
        '}'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Argus Tools MCP - Connector Guide</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            font-size: 18px;
            line-height: 1.6;
            color: #1a1a1a;
            background: #ffffff;
            padding: 22px;
        }}

        .container {{
            max-width: 1350px;
            margin: 0 auto;
            background: white;
            border-top: 1px solid #ddd;
            border-bottom: 1px solid #ddd;
            overflow: visible;
        }}

        .header {{
            border-bottom: 4px solid #006D77;
            color: #006D77;
            padding: 22px 45px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            background: white;
            z-index: 1000;
        }}

        .header-left {{ display: flex; align-items: center; gap: 22px; }}
        .header-left img {{ height: 60px; }}
        .header-title {{ display: flex; flex-direction: column; }}
        .header h1 {{ font-size: 27px; margin: 0; font-weight: 600; }}
        .header p {{ font-size: 16px; color: #666; margin: 0; }}

        .header-right {{ text-align: right; }}
        .support-label {{ font-size: 15px; color: #666; font-weight: 600; margin-bottom: 5px; }}
        .support-team {{ font-size: 16px; color: #006D77; font-weight: 600; }}
        .support-email {{ font-size: 15px; color: #006D77; text-decoration: none; }}
        .support-email:hover {{ text-decoration: underline; }}
        .auth-pill {{
            display: inline-block;
            margin-top: 6px;
            padding: 3px 10px;
            border-radius: 999px;
            background: #E0F2F1;
            color: #006D77;
            font-size: 13px;
            font-weight: 600;
        }}

        .content {{ padding: 38px 45px; }}
        .section {{ margin-bottom: 42px; }}
        .section h2 {{
            color: #006D77;
            font-size: 24px;
            margin-bottom: 21px;
            padding-bottom: 9px;
            border-bottom: 3px solid #006D77;
            font-weight: 600;
        }}
        .section h3 {{
            color: #006D77;
            font-size: 20px;
            margin-top: 27px;
            margin-bottom: 15px;
            font-weight: 600;
        }}

        .highlight-box {{
            border: 2px solid #006D77;
            border-left: 5px solid #006D77;
            padding: 21px;
            margin: 21px 0;
            border-radius: 5px;
            font-size: 16px;
        }}

        .step-box {{
            border: 2px solid #E29578;
            border-left: 5px solid #E29578;
            padding: 21px;
            margin: 21px 0;
            border-radius: 5px;
            font-size: 16px;
        }}
        .step-box h4 {{
            color: #E29578;
            margin-bottom: 12px;
            font-size: 18px;
            font-weight: 600;
        }}

        .command-list {{
            border: 2px solid #e0e0e0;
            padding: 21px;
            border-radius: 6px;
            margin: 18px 0;
        }}
        .command-item {{
            padding: 15px;
            margin: 9px 0;
            border: 2px solid #e0e0e0;
            border-radius: 5px;
            border-left: 5px solid #006D77;
            font-size: 16px;
        }}
        .command-item strong {{ color: #006D77; font-size: 18px; }}

        .example-query {{
            border: 2px solid #83C5BE;
            border-left: 5px solid #83C5BE;
            padding: 15px;
            margin: 12px 0;
            border-radius: 5px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 15px;
        }}

        .tip-box {{
            border: 2px solid #006D77;
            border-left: 5px solid #006D77;
            padding: 18px;
            margin: 18px 0;
            border-radius: 5px;
            font-size: 16px;
        }}
        .tip-box::before {{
            content: "💡 TIP: ";
            font-weight: 600;
            color: #006D77;
            font-size: 18px;
        }}

        .warning-box {{
            border: 2px solid #E29578;
            border-left: 5px solid #E29578;
            padding: 18px;
            margin: 18px 0;
            border-radius: 5px;
            font-size: 16px;
        }}
        .warning-box::before {{
            content: "⚠️ NOTE: ";
            font-weight: 600;
            color: #E29578;
            font-size: 18px;
        }}

        pre {{
            background: #0f1419;
            color: #d4e3e8;
            padding: 16px 18px;
            border-radius: 6px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 14px;
            overflow-x: auto;
            margin: 12px 0;
        }}
        code {{
            background: #f0f4f5;
            color: #006D77;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 15px;
        }}
        pre code {{ background: transparent; color: inherit; padding: 0; font-size: 14px; }}

        ul, ol {{ margin-left: 27px; margin-top: 12px; }}
        li {{ margin: 9px 0; font-size: 18px; }}

        .footer {{
            padding: 24px;
            text-align: center;
            color: #666;
            border-top: 2px solid #ddd;
            font-size: 15px;
        }}
        strong {{ font-weight: 600; }}
        a {{ color: #006D77; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}

        @media print {{
            body {{ background: white; padding: 0; }}
            .container {{ box-shadow: none; border-radius: 0; }}
            .section {{ page-break-inside: avoid; }}
        }}
    </style>
    {theme.head_tags()}
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-left">
                <img src="/assets/images/marketanalytics-color.png" alt="Argus Tools MCP">
                <div class="header-title">
                    <h1>Argus Tools MCP</h1>
                    <p>FactSet warehouse, FactSet Vector API, Databricks SQL, and the Argus Wiki — for any MCP client</p>
                    <span class="auth-pill">Auth: {auth_status_label}</span>
                </div>
            </div>
            <div class="header-right">
                <div class="support-label">SUPPORT:</div>
                <div class="support-team">Apollo Thematic Investing Team</div>
                <a href="mailto:ati_team@apollo.com" class="support-email">ati_team@apollo.com</a>
            </div>
        </div>

        <div class="content">
            <div class="section">
                <h2>📑 Quick Links</h2>
                <ul>
                    <li><a href="#getting-started">🚀 Getting Started — Pick Your AI Client</a></li>
                    <li><a href="#claude-code">🐉 Claude Code (CLI)</a></li>
                    <li><a href="#claude-web">🌐 Claude Web (claude.ai)</a></li>
                    <li><a href="#chatgpt-codex">🤖 ChatGPT Codex (CLI)</a></li>
                    <li><a href="#chatgpt-web">💬 ChatGPT Web (chatgpt.com)</a></li>
                    <li><a href="#cursor">🎯 Cursor</a></li>
                    <li><a href="#generic">🔌 Any Other Agentic / MCP Client</a></li>
                    <li><a href="#tools-skills">🛠️ Tools and Skills You Get</a></li>
                    <li><a href="#examples">💬 Example Prompts</a></li>
                    <li><a href="#tips">💡 Tips and Best Practices</a></li>
                    <li><a href="#troubleshooting">❓ Troubleshooting</a></li>
                </ul>
            </div>

            <div class="section" id="getting-started">
                <h2>🚀 Getting Started</h2>
                <p>Argus Tools is a Model Context Protocol (MCP) connector. Any MCP-aware client
                   — Claude, ChatGPT, Cursor, custom agents — can register it and immediately use
                   the FactSet and Databricks tools, plus the skill resources that teach the LLM
                   how to use them.</p>

                <div class="highlight-box">
                    <strong>Connector URL</strong><br>
                    <code>{mcp_url}</code>
                </div>

                {auth_box}
            </div>

            <div class="section" id="claude-code">
                <h2>🐉 Claude Code (CLI)</h2>
                <div class="step-box">
                    <h4>Step 1: Register the connector</h4>
                    <pre>claude mcp add --scope user --transport http argus-tools {mcp_url}</pre>
                </div>
                <div class="step-box">
                    <h4>Step 2: Verify it</h4>
                    <pre>claude mcp get argus-tools</pre>
                    <p>You should see <strong>Status: ✓ Connected</strong>.</p>
                </div>
                <div class="step-box">
                    <h4>Step 3: Use it</h4>
                    <pre>claude
&gt; Use argus-tools and call mcp_capabilities.</pre>
                </div>
                <div class="step-box">
                    <h4>Unregister</h4>
                    <pre>claude mcp remove argus-tools -s user</pre>
                </div>
            </div>

            <div class="section" id="claude-web">
                <h2>🌐 Claude Web (claude.ai)</h2>
                <div class="step-box">
                    <h4>Step 1: Open Connectors</h4>
                    <ol>
                        <li>Go to <strong>Settings</strong></li>
                        <li>Click <strong>Connectors</strong> (or <strong>Integrations</strong>)</li>
                        <li>Click <strong>Add custom connector</strong></li>
                    </ol>
                </div>
                <div class="step-box">
                    <h4>Step 2: Configure</h4>
                    <ul>
                        <li><strong>Name:</strong> <code>argus-tools</code></li>
                        <li><strong>Transport:</strong> <code>HTTP</code></li>
                        <li><strong>URL:</strong> <code>{mcp_url}</code></li>
                    </ul>
                </div>
                <div class="step-box">
                    <h4>Step 3: Try it</h4>
                    <p>In any chat, ask:</p>
                    <div class="example-query">"Use argus-tools and call mcp_health_check."</div>
                </div>
                <p>Remove it from the same Connectors panel.</p>
            </div>

            <div class="section" id="chatgpt-codex">
                <h2>🤖 ChatGPT Codex (CLI)</h2>
                <div class="step-box">
                    <h4>Option A — Edit the config file</h4>
                    <p>Append to <code>~/.codex/config.toml</code>:</p>
                    <pre>[mcp_servers.argus-tools]
type = "http"
url  = "{mcp_url}"</pre>
                </div>
                <div class="step-box">
                    <h4>Option B — Use the codex CLI</h4>
                    <pre>codex mcp add argus-tools --transport http --url {mcp_url}</pre>
                </div>
                <div class="step-box">
                    <h4>Use it</h4>
                    <pre>codex
&gt; Using argus-tools, call mcp_capabilities.</pre>
                </div>
            </div>

            <div class="section" id="chatgpt-web">
                <h2>💬 ChatGPT Web (chatgpt.com)</h2>
                <div class="step-box">
                    <h4>Step 1: Open Connectors</h4>
                    <ol>
                        <li>Go to <strong>Settings</strong></li>
                        <li>Click <strong>Connectors</strong> (or <strong>Apps &amp; Integrations</strong>)</li>
                        <li>Click <strong>Add custom MCP connector</strong></li>
                    </ol>
                </div>
                <div class="step-box">
                    <h4>Step 2: Configure</h4>
                    <ul>
                        <li><strong>Name:</strong> <code>argus-tools</code></li>
                        <li><strong>URL:</strong> <code>{mcp_url}</code></li>
                        <li>Complete OAuth if prompted</li>
                    </ul>
                </div>
                <div class="step-box">
                    <h4>Step 3: Enable for the chat and ask</h4>
                    <div class="example-query">"Using argus-tools, call mcp_capabilities."</div>
                </div>
            </div>

            <div class="section" id="cursor">
                <h2>🎯 Cursor</h2>
                <div class="step-box">
                    <h4>Option A — UI</h4>
                    <ol>
                        <li>Open <strong>Settings</strong> &rarr; <strong>Tools &amp; Integrations</strong></li>
                        <li>Open the <strong>MCP</strong> section</li>
                        <li>Click <strong>Add custom server</strong></li>
                        <li>Name <code>argus-tools</code>, URL <code>{mcp_url}</code></li>
                    </ol>
                </div>
                <div class="step-box">
                    <h4>Option B — Edit <code>~/.cursor/mcp.json</code></h4>
                    <pre>{cursor_json}</pre>
                    <p>Reload Cursor; the connector appears under <strong>Tools</strong>.</p>
                </div>
            </div>

            <div class="section" id="generic">
                <h2>🔌 Any Other Agentic / MCP-Aware Client</h2>
                <p>The connector speaks the standard Model Context Protocol over streamable HTTP.
                   For any client, the four fields you need are:</p>
                <div class="command-list">
                    <div class="command-item"><strong>Name</strong><br><code>argus-tools</code></div>
                    <div class="command-item"><strong>Transport</strong><br><code>http</code> (streamable)</div>
                    <div class="command-item"><strong>URL</strong><br><code>{mcp_url}</code></div>
                    <div class="command-item"><strong>Auth</strong><br>{auth_status_label}</div>
                </div>
                <h3>OpenAI Responses API</h3>
                <p>Pass an MCP tool entry directly:</p>
                <pre>{openai_json}</pre>
                <p>Programmatic clients like LangChain and LlamaIndex accept the URL directly.</p>
            </div>

            <div class="section" id="tools-skills">
                <h2>🛠️ Tools and Skills You Get</h2>

                <h3>Tools</h3>
                <div class="command-list">
                    <div class="command-item">
                        <strong>factset_fundamentals_query(sql)</strong><br>
                        Read-only SQL door to the FactSet warehouse. Pair with <code>skill://factset_shared</code>.
                    </div>
                    <div class="command-item">
                        <strong>factset_broker_research_search(search_text, ids, contributor_id, …)</strong><br>
                        Search permissioned FactSet Research Connect broker documents. Pair with <code>skill://factset_broker_research</code>.
                    </div>
                    <div class="command-item">
                        <strong>factset_broker_research_count(ids, contributor_id, search_text)</strong><br>
                        Count matching broker research documents before widening a search.
                    </div>
                    <div class="command-item">
                        <strong>factset_broker_research_metadata(metadata_type)</strong><br>
                        List contributors, categories, security types, asset types, time zones, or analysts.
                    </div>
                    <div class="command-item">
                        <strong>factset_vector_search(query, sources, start_date, end_date, …)</strong><br>
                        Semantic search of filings, news, and transcripts. Pair with <code>skill://factset_vector_search</code>.
                    </div>
                    <div class="command-item">
                        <strong>factset_vector_get_chunks(vector_ids)</strong><br>
                        Fetch full chunk text for vector hits.
                    </div>
                    <div class="command-item">
                        <strong>factset_vector_metadata(metadata_type)</strong><br>
                        List sources, document types, schemas, or themes.
                    </div>
                    <div class="command-item">
                        <strong>databricks_query(sql, app)</strong><br>
                        Read-only SQL for any Databricks-backed dataset.
                    </div>
                    <div class="command-item">
                        <strong>databricks_tables(app), databricks_describe_table(table, app)</strong><br>
                        Schema discovery for any Databricks app.
                    </div>
                    <div class="command-item">
                        <strong>wiki_search(query, view, top_k)</strong><br>
                        Full-text search the Argus Wiki vault (default / macro / thematic views).
                        Pair with <code>skill://wiki</code>.
                    </div>
                    <div class="command-item">
                        <strong>wiki_get_page(rel, view)</strong><br>
                        Read a wiki page by vault-relative path. Returns title, frontmatter, body, and cited sources.
                    </div>
                    <div class="command-item">
                        <strong>wiki_get_today(view)</strong><br>
                        Today's LLM-synthesized daily research brief for the chosen view.
                    </div>
                    <div class="command-item">
                        <strong>wiki_get_entity(folder, name, view)</strong><br>
                        Canonical entity page (tickers / brokers / sectors / themes) with rolling thesis.
                    </div>
                    <div class="command-item">
                        <strong>wiki_list_entities(folder, view), wiki_get_sources(name, since, view)</strong><br>
                        Entity enumeration and source-grounding helpers.
                    </div>
                    <div class="command-item">
                        <strong>mcp_health_check, mcp_capabilities</strong><br>
                        Server meta. Use for a quick smoke test.
                    </div>
                </div>

                <h3>Skill resources</h3>
                <p>The LLM auto-discovers these from your question via descriptions and signals:</p>
                <div class="command-list">
                    <div class="command-item">
                        <strong>skill://factset_shared</strong><br>
                        FactSet identifiers (<code>ticker_region</code>, <code>fsym_id</code>,
                        <code>factset_entity_id</code>), schemas, PIT rules, SQL recipes.
                        References cover fundamentals, estimates, prices, DCS, BDC, hierarchy, GeoRev.
                    </div>
                    <div class="command-item">
                        <strong>skill://factset_broker_research</strong><br>
                        Broker research routing, JPM contributor discovery, document search filters,
                        counts, metadata endpoints, and document download guidance.
                    </div>
                    <div class="command-item">
                        <strong>skill://factset_vector_search</strong><br>
                        Corpora (<code>EDG</code>, <code>SA</code>, <code>FCST</code>), date windows,
                        themes, sentiment, scoring bands, query shaping recipes.
                    </div>
                    <div class="command-item">
                        <strong>skill://risk_intern_skill</strong><br>
                        Apollo risk and P&amp;L tables (<code>v_risk_enhanced</code>, <code>v_pnl_enhanced</code>),
                        entity classification, aggregation rules, fund / strategy filters.
                    </div>
                    <div class="command-item">
                        <strong>skill://wiki</strong><br>
                        Argus Wiki source-grounded research lookup — broker views, ratings,
                        daily briefs, macro regime, and thematic research across default / macro / thematic
                        views. References cover API patterns, entity resolution, view selection, and source grounding.
                    </div>
                </div>
                <p>Each skill exposes its <code>references/*</code> markdown files as sub-resources
                   for deeper guidance.</p>
            </div>

            <div class="section" id="examples">
                <h2>💬 Example Prompts</h2>
                <p>Same prompts work in every client once registered:</p>
                <div class="example-query">"Using argus-tools, call mcp_health_check."</div>
                <div class="example-query">"Using argus-tools, list resources, then read skill://factset_vector_search."</div>
                <div class="example-query">"Using argus-tools, read skill://factset_broker_research, then list FactSet broker contributors matching JPM."</div>
                <div class="example-query">"Using argus-tools, find recent FactSet transcripts where management discussed AI cost savings."</div>
                <div class="example-query">"Using argus-tools, what is the gross exposure for fund CSTRAT as of today?"</div>
                <div class="example-query">"Using argus-tools, get the latest FactSet consensus EPS estimates for AAPL-US for the next four quarters."</div>
            </div>

            <div class="section" id="tips">
                <h2>💡 Tips and Best Practices</h2>
                <div class="tip-box">
                    <strong>Let the skills do the routing.</strong> If your question contains words like
                    <em>beta</em>, <em>exposure</em>, <em>10-K</em>, <em>transcript</em>, or
                    <em>consensus</em>, the LLM will pick the matching <code>skill://</code> automatically.
                </div>
                <div class="tip-box">
                    <strong>Be specific.</strong> Use <code>ticker_region</code> like <code>AAPL-US</code>,
                    fund codes like <code>CSTRAT</code>, and date ranges where possible.
                </div>
                <div class="tip-box">
                    <strong>Vector search needs sources and dates.</strong> The wrapper defaults to last 90
                    days and all three sources (<code>EDG</code>, <code>SA</code>, <code>FCST</code>) so the
                    first call works, but narrower windows give better hits.
                </div>
                <div class="warning-box">
                    Tools are read-only and result-limited. For larger pulls, narrow the SQL or vector query
                    rather than asking for everything.
                </div>
            </div>

            <div class="section" id="troubleshooting">
                <h2>❓ Troubleshooting</h2>
                <div class="command-list">
                    <div class="command-item">
                        <strong>Client says "Failed to connect"</strong><br>
                        • Verify the URL above is reachable from your machine.<br>
                        • Make sure the transport is set to <code>http</code> (streamable), not stdio.<br>
                        • Run <code>mcp_health_check</code> from a registered client.
                    </div>
                    <div class="command-item">
                        <strong>Client says "Needs authentication"</strong><br>
                        • Auth is currently {auth_status_label}.<br>
                        • If Okta / OAuth, complete the browser sign-in the client opens.<br>
                        • If you cannot complete OAuth, ping the support team.
                    </div>
                    <div class="command-item">
                        <strong>FactSet API returns 400 on vector search</strong><br>
                        • The wrapper now defaults dates and sources, so this should be rare.<br>
                        • Read <code>skill://factset_vector_search</code> for valid <code>sources</code> and
                          <code>document_types</code>.
                    </div>
                    <div class="command-item">
                        <strong>SQL error from Databricks or FactSet</strong><br>
                        • Read the matching <code>skill://</code> first — it teaches schemas and PIT rules.<br>
                        • Use <code>databricks_describe_table</code> or
                          <code>databricks_tables</code> to inspect schemas live.
                    </div>
                </div>
            </div>
        </div>

        <div class="footer">
            <p><strong>Argus Tools MCP</strong> — single connector for FactSet and Databricks across
               Claude, ChatGPT, Cursor, and any MCP-aware agent.</p>
            <p>{footer_caveat}</p>
        </div>
    </div>
</body>
</html>"""
