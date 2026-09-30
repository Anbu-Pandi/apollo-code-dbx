"""Conversation Explorer — a standalone, searchable view of GenAI conversations.

Search the chat logs (semantic via Databricks Vector Search, keyword fallback),
click a hit to see the whole conversation as a workflow to analyze: every turn,
run metrics, and any captured issues tied to it. Registered in page_router and
gated to app_Dash_Admin. Interactivity lives in callbacks.py.
"""

import dash_core_components as dcc
import dash_html_components as html

from dash_reports.genai_conversations.data import conversation_reader as store
from dash_reports.genai_conversations.pages.style import (
    BORDER, GREEN, MUTED, ORANGE, SURFACE, TEXT, badge, note, s as _s, shell,
)

_TITLE = "GenAI Conversation Explorer"


def get_conversations_headers(app=None):
    if app is not None:
        app.title = _TITLE
    return html.Div()


def create_conversations_layout(initial_id: str = None):
    from dash_reports.genai_conversations.pages.stats import build_stats

    detail = conversation_view(initial_id) if initial_id else _detail_placeholder()

    tab_style = {"background": "transparent", "color": MUTED, "border": "none",
                 "borderBottom": "2px solid transparent", "padding": "9px 16px",
                 "fontWeight": 600, "fontSize": "13px"}
    tab_sel = {**tab_style, "color": TEXT, "borderBottom": f"2px solid {GREEN}"}

    search_tab = html.Div([
        _search_bar(initial_id),
        html.Div(id="conv-results", children=_results_hint()),
        html.Div(id="conv-detail", children=detail, style={"marginTop": "18px"}),
    ], style={"paddingTop": "16px"})
    stats_tab = html.Div([build_stats(30)], style={"paddingTop": "16px"})

    tabs = dcc.Tabs(id="conv-tabs", value="search", children=[
        dcc.Tab(label="Search & Replay", value="search", children=search_tab,
                style=tab_style, selected_style=tab_sel),
        dcc.Tab(label="Stats", value="stats", children=stats_tab,
                style=tab_style, selected_style=tab_sel),
    ], style={"borderBottom": f"1px solid {BORDER}", "marginTop": "8px"},
        colors={"border": BORDER, "primary": GREEN, "background": SURFACE})

    return shell("conversations", "Argus Genie · Observability", "Conversation Explorer",
                 "Search and replay every Genie conversation, and track usage by channel, "
                 "route and user.", tabs)


# ── search ──────────────────────────────────────────────────────────────

def _search_bar(initial_id):
    return html.Div([
        dcc.Input(id="conv-q", type="text", debounce=True, className="genie-input",
                  placeholder="Search conversations — e.g. 'carvana pnl', 'factset symbology error'…",
                  style={"flex": "3 1 380px"}),
        dcc.Input(id="conv-id", type="text", value=initial_id or "", debounce=True, className="genie-input",
                  placeholder="…or paste a session / run / conversation id", style={"flex": "2 1 260px"}),
        html.Button("Search", id="conv-search-btn", n_clicks=0, className="genie-btn"),
        html.Span(id="conv-mode", className="genie-muted",
                  style={"fontSize": "11px", "alignSelf": "center"}),
    ], className="genie-searchbar")


def _results_hint():
    return note("Type a query and hit Search, or open an id directly.")


def _detail_placeholder():
    return note("Select a result or open an id to replay the full conversation here.")


# ── results list (used by layout + search callback) ────────────────────────

def results_list(df, mode):
    if df is None or df.empty:
        return note("No matching conversations.")
    rows = []
    for _, r in df.iterrows():
        sid = _s(r, "session_id")
        # a link to ?id=<session> — deep-link server-renders the replay reliably
        # (more robust than a pattern-matching n_clicks callback)
        rows.append(dcc.Link([
            html.Div([badge(_s(r, "source_system") or "genie"),
                      html.Span(_s(r, "timestamp")[:16].replace("T", " "), className="genie-muted",
                                style={"fontSize": "11px", "marginLeft": "8px"}),
                      html.Span(sid[:18], className="genie-muted genie-mono",
                                style={"fontSize": "10.5px", "marginLeft": "8px"})],
                     style={"marginBottom": "5px"}),
            html.Div(_s(r, "question")[:140] or "(no question logged)", className="rq"),
            html.Div(_s(r, "text_response")[:180], className="rr"),
        ], href=f"/genai-conversations?id={sid}", className="genie-result"))
    header = html.Div(f"{len(df)} result(s) · {mode} search", className="genie-muted",
                      style={"fontSize": "11px", "letterSpacing": "0.06em", "margin": "10px 0 8px"})
    return html.Div([header, html.Div(rows)])


# ── single conversation view (used by layout + open callback) ───────────────

def conversation_view(id_value):
    sid = store.resolve_session_id(id_value)
    conv = store.get_conversation(sid)
    if conv is None or conv.empty:
        return note(f"No conversation found for id: {id_value}")
    audit = store.get_run_audit(sid)
    issues = store.get_issues_for_session(sid)
    return html.Div([
        _detail_header(sid, conv, audit, issues),
        html.Div([_turn(r) for _, r in conv.iterrows()], style={"marginTop": "12px"}),
    ], className="genie-detail")


def _detail_header(sid, conv, audit, issues):
    metrics = []
    if audit is not None and not audit.empty:
        tin = int(audit["input_tokens"].fillna(0).sum()) if "input_tokens" in audit else 0
        tout = int(audit["output_tokens"].fillna(0).sum()) if "output_tokens" in audit else 0
        cost = float(audit["estimated_cost"].fillna(0).sum()) if "estimated_cost" in audit else 0.0
        metrics = [_stat("runs", len(audit)), _stat("tokens", f"{tin+tout:,}"), _stat("cost", f"${cost:.3f}")]
    n_issues = 0 if issues is None or issues.empty else len(issues)
    return html.Div([
        html.Div([html.Span("SESSION ", className="genie-muted", style={"fontSize": "11px",
                                                                        "letterSpacing": "0.08em"}),
                  html.Span(sid, className="genie-mono", style={"fontSize": "12px"})]),
        html.Div([_stat("turns", len(conv)), *metrics,
                  html.Span(badge(f"{n_issues} linked issue(s)", "user" if not n_issues else "agent"),
                            style={"marginLeft": "6px"})],
                 style={"display": "flex", "gap": "18px", "alignItems": "center", "flexWrap": "wrap",
                        "marginTop": "8px"}),
    ], className="genie-detail-head")


def _stat(label, value):
    return html.Span([html.B(f"{value}"), html.Span(f" {label}")], className="genie-stat")


def _turn(r):
    sql = _s(r, "sql_query")
    blocks = [html.Div(_s(r, "timestamp")[:19].replace("T", " "), className="genie-muted",
                       style={"fontSize": "10.5px", "marginBottom": "6px"})]
    if _s(r, "question"):
        blocks.append(_bubble("USER", _s(r, "question"), "user"))
    if sql:
        blocks.append(html.Details([
            html.Summary("SQL", className="genie-muted", style={"fontSize": "11px", "cursor": "pointer"}),
            html.Pre(sql, className="genie-code"),
        ], style={"margin": "6px 0"}))
    if _s(r, "text_response"):
        blocks.append(_bubble("GENIE", _s(r, "text_response"), "genie"))
    if _s(r, "user_feedback"):
        blocks.append(html.Div(f"feedback: {_s(r, 'user_feedback')}",
                               style={"color": ORANGE, "fontSize": "11px", "marginTop": "4px"}))
    return html.Div(blocks, className="genie-turn")


def _bubble(who, text, kind):
    # render the turn as real markdown (tables, code, lists, links) — same
    # dcc.Markdown the rest of dash_reports uses — instead of flat text.
    return html.Div([
        html.Div(who, className="genie-bubble-who"),
        dcc.Markdown(text, className="genie-md", link_target="_blank"),
    ], className=f"genie-bubble {kind}")
