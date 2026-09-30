"""GenAI Reliability Radar — the issues view of Genie Admin.

Actionable signal: what's broken (system) vs what the agent did wrong (agent),
what needs attention now, and the trend. Light/minimal, styled entirely by
assets/genie_admin.css via the shared genie_ui kit. Gated to app_Dash_Admin.
"""

import dash_core_components as dcc
import dash_html_components as html

from dash_reports.genai_issues.data.genai_issues_report import get_genai_issue_summary
from dash_reports.genai_issues.pages.charts import chart_by_category, chart_class_split, chart_issue_trend
from dash_reports.report_common.pages.genie_ui import (
    GREEN, MUTED, SEVERITY_COLOR, cls_chip, kpi_card, note, panel, s, sev_chip, shell,
)

_TITLE = "Reliability Radar"


def get_genai_issues_headers(app=None):
    if app is not None:
        app.title = "GenAI Reliability Radar"
    return html.Div()


def create_genai_issues_layout():
    d = get_genai_issue_summary()
    k = d["kpis"]
    table_df = d["table"]

    body = [
        _kpi_row(k),
        _attention_strip(table_df),
        html.Div([
            panel("Issue Trend", "occurrences/day by severity",
                  dcc.Graph(figure=chart_issue_trend(d["trend"]), config={"displayModeBar": False}),
                  flex="2 1 520px"),
            panel("System vs Agent", "where the fault lies", accent="blue",
                  content=dcc.Graph(figure=chart_class_split(table_df), config={"displayModeBar": False}),
                  flex="1 1 300px"),
        ], className="genie-row"),
        panel("By Category", "what kind of problem", accent="orange",
              content=dcc.Graph(figure=chart_by_category(d["by_category"]), config={"displayModeBar": False}),
              margin_top="16px"),
        panel("Signal Issues", "most severe & recent first — with suggested action",
              content=_issue_table(table_df), margin_top="16px"),
        html.Div("Per-host Delta on the analytics share · signal-only (user-error noise filtered) · "
                 "recurring issues folded by signature", className="genie-footer"),
    ]
    return shell("radar", "Argus Genie · Observability", _TITLE,
                 "Actionable signal from every Genie run — systemic faults and agent misbehaviour, "
                 "noise filtered out.", body)


def _kpi_row(k):
    cards = [
        ("Open Signal", k.get("open", 0), "green", "actionable & unresolved"),
        ("Critical / High", k.get("critical_high", 0), "red", "need attention"),
        ("System", k.get("system", 0), "blue", "platform faults"),
        ("Agent", k.get("agent", 0), "green", "agent misbehaviour"),
        ("Occurrences", k.get("total_occurrences", 0), "grey", "total events"),
        ("Resolved", k.get("resolved", 0), "green", "closed out"),
    ]
    return html.Div([kpi_card(l, f"{v:,}", a, sub) for l, v, a, sub in cards], className="genie-kpis")


def _attention_strip(df):
    if df is None or df.empty:
        return html.Div()
    d = df.copy()
    if "severity" in d.columns:
        d = d[d["severity"].isin(["critical", "high"])]
    if "status" in d.columns:
        d = d[d["status"].isin(["new", "acknowledged", "investigating"])]
    if d.empty:
        return html.Div()
    cards = [_attention_card(r) for _, r in d.head(4).iterrows()]
    return html.Div([
        html.Div("Needs attention now", className="genie-eyebrow", style={"margin": "22px 0 10px"}),
        html.Div(cards, className="genie-attn"),
    ])


def _conv_ident(r):
    """The id to open an issue's conversation by. Prefer conversation_id: that is
    the exact key the transcript is logged under, so the Explorer opens it
    directly (no audit bridge, works retroactively). Fall back to run_id (maps
    via run-audit) then the per-message session_id. Single source of truth for
    both the link href and its label."""
    return s(r, "conversation_id") or s(r, "run_id") or s(r, "session_id")


def _conv_href(r):
    """Deep-link to this issue's conversation, or the full conversation list when
    no id is on the row."""
    ident = _conv_ident(r)
    return f"/genai-conversations?id={ident}" if ident else "/genai-conversations/"


def _attention_card(r):
    sev = s(r, "severity", "unknown")
    cat = s(r, "category").replace("_", " ").title()
    summary = s(r, "error_summary")[:150]
    suggestion = s(r, "suggestion")[:180] or "Investigate root cause."
    cls = "genie-attn-card" + (" crit" if sev == "critical" else "")
    return html.Div([
        html.Div([sev_chip(sev), cls_chip(s(r, "issue_class", "unknown")),
                  html.Span(cat, className="genie-muted", style={"fontSize": "12px", "marginLeft": "4px"})],
                 style={"display": "flex", "alignItems": "center", "gap": "6px", "marginBottom": "8px"}),
        html.Div(summary, style={"fontSize": "12.5px", "fontWeight": 600, "lineHeight": "1.35",
                                 "marginBottom": "8px"}),
        html.Div([html.Span("→ ", style={"color": GREEN, "fontWeight": 700}),
                  html.Span(suggestion, className="genie-muted", style={"fontSize": "12px"})],
                 style={"borderTop": "1px solid #F0EDE9", "paddingTop": "8px"}),
        dcc.Link("View conversation ↗", href=_conv_href(r), className="genie-conv-link"),
    ], className=cls)


def _issue_table(df):
    if df is None or df.empty:
        return note("No signal issues in the selected window. The system is behaving. ✅")
    heads = ["When", "Class", "Severity", "Category", "Where", "Count", "Problem", "Suggested action", "Run"]
    head = html.Tr([html.Th(h) for h in heads])
    rows = []
    for _, r in df.head(60).iterrows():
        when = s(r, "last_seen_at")[:16].replace("T", " ")
        where = " · ".join([x for x in [s(r, "domain"), s(r, "tool_name")] if x]) or "—"
        occ = s(r, "occurrence_count").split(".")[0] or "1"
        rows.append(html.Tr([
            html.Td(when, className="genie-muted", style={"whiteSpace": "nowrap"}),
            html.Td(cls_chip(s(r, "issue_class", "unknown"))),
            html.Td(sev_chip(s(r, "severity", "unknown"))),
            html.Td(s(r, "category").replace("_", " ").title()),
            html.Td(where, className="genie-muted"),
            html.Td(occ, style={"fontWeight": 700}),
            html.Td(s(r, "error_summary")[:130]),
            html.Td(s(r, "suggestion")[:150] or "—", className="genie-suggest"),
            html.Td(_conv_cell(r), style={"whiteSpace": "nowrap"}),
        ]))
    return html.Div(html.Table([html.Thead(head), html.Tbody(rows)], className="genie-table"),
                    style={"overflowX": "auto"})


def _conv_cell(r):
    ident = _conv_ident(r)
    label = ("↗ " + ident[-10:]) if ident else "↗ view"
    return dcc.Link(label, href=_conv_href(r), className="genie-mono genie-conv-link",
                    style={"fontSize": "11px"})
