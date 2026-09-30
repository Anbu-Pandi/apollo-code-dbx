"""Stats tab — "how things are going" for GenAI conversations, by the categories
we log (channel, route, user, feedback) + volume, tokens and cost. Light theme
via the shared kit."""

import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objects as go

from dash_reports.genai_conversations.data import conversation_reader as store
from dash_reports.report_common.pages.genie_ui import (
    BLUE, GREEN, MUTED, SERIES, kpi_card, note, panel, style_fig,
)


def build_stats(since_days: int = 30):
    st = store.conversation_stats(since_days=since_days)
    kpi = st["kpi"].iloc[0].to_dict() if not st["kpi"].empty else {}
    cost = _sum(st["cost_daily"], "cost")
    tokens = _sum(st["cost_daily"], "tokens")
    audit_ok = st.get("audit_available", False)

    kpis = html.Div([
        kpi_card("Conversations", _fmt(kpi.get("sessions", 0)), "green", f"last {since_days}d"),
        kpi_card("Turns", _fmt(kpi.get("turns", 0)), "blue", "questions asked"),
        kpi_card("Channels", _fmt(kpi.get("channels", 0)), "green", "surfaces in use"),
        kpi_card("Tokens", _fmt(tokens), "orange", "in+out"),
        kpi_card("Cost", f"${cost:,.2f}", "green", "estimated"),
        kpi_card("Feedback", _fmt(kpi.get("feedback", 0)), "blue", "thumbs given"),
    ], className="genie-kpis")

    def _audit(title, subtitle, fig, flex, accent="green"):
        content = (dcc.Graph(figure=fig, config={"displayModeBar": False}) if audit_ok
                   else note("Run-audit table not provisioned yet — token/cost/route metrics "
                             "appear once genai_run_audit exists."))
        return panel(title, subtitle, content, accent=accent, flex=flex)

    row1 = html.Div([
        panel("Daily Volume", "turns & sessions per day",
              dcc.Graph(figure=_daily(st["daily"]), config={"displayModeBar": False}), flex="2 1 520px"),
        panel("Feedback", "user sentiment mix", accent="blue",
              content=dcc.Graph(figure=_donut(st["feedback"], "sentiment", "n"), config={"displayModeBar": False}),
              flex="1 1 280px"),
    ], className="genie-row")

    row2 = html.Div([
        panel("By Channel", "where conversations happen",
              dcc.Graph(figure=_hbar(st["by_source"], "source_system", "turns"), config={"displayModeBar": False}),
              flex="1 1 340px"),
        _audit("By Route", "which genie handled it", _hbar(st["by_route"], "route", "runs"), "1 1 340px", "blue"),
    ], className="genie-row")

    row3 = html.Div([
        _audit("Cost / Tokens Trend", "spend per day", _cost_trend(st["cost_daily"]), "2 1 520px", "orange"),
        _audit("Top Users", "by run count", _hbar(st["top_users"], "user_name", "runs"), "1 1 280px"),
    ], className="genie-row")

    return html.Div([kpis, row1, row2, row3])


def _sum(df, col):
    try:
        return float(df[col].fillna(0).sum()) if df is not None and not df.empty and col in df else 0.0
    except Exception:
        return 0.0


def _fmt(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "0"


def _daily(df):
    fig = go.Figure()
    if df is None or df.empty or "day" not in df:
        return style_fig(fig, 300, empty_msg="No data in range")
    fig.add_trace(go.Bar(x=df["day"], y=df["turns"], name="Turns", marker_color=BLUE))
    if "sessions" in df:
        fig.add_trace(go.Scatter(x=df["day"], y=df["sessions"], name="Sessions", mode="lines+markers",
                                 line=dict(color=GREEN, width=2)))
    fig.update_layout(legend=dict(orientation="h", y=1.15, font=dict(size=11)))
    return style_fig(fig, 300)


def _cost_trend(df):
    fig = go.Figure()
    if df is None or df.empty or "day" not in df:
        return style_fig(fig, 300, empty_msg="No data in range")
    fig.add_trace(go.Scatter(x=df["day"], y=df.get("cost"), name="Cost $", mode="lines",
                             fill="tozeroy", line=dict(color=GREEN, width=2)))
    return style_fig(fig, 300)


def _hbar(df, label_col, val_col):
    fig = go.Figure()
    if df is None or df.empty or label_col not in df or val_col not in df:
        return style_fig(fig, 300, empty_msg="No data in range")
    d = df.sort_values(val_col).tail(12)
    n = len(d)
    colors = [SERIES[i % len(SERIES)] for i in range(n)]
    fig.add_trace(go.Bar(x=d[val_col], y=[str(x) for x in d[label_col]], orientation="h",
                         marker_color=colors, text=d[val_col], textposition="outside",
                         textfont=dict(color=MUTED, size=11)))
    return style_fig(fig, max(240, 34 * n + 60))


def _donut(df, label_col, val_col):
    fig = go.Figure()
    if df is None or df.empty or label_col not in df:
        return style_fig(fig, 300, empty_msg="No data in range")
    fig.add_trace(go.Pie(labels=[str(x) for x in df[label_col]], values=df[val_col], hole=0.6,
                         marker=dict(colors=SERIES, line=dict(color="#FFFFFF", width=2)),
                         textfont=dict(size=11)))
    fig.update_layout(showlegend=True, legend=dict(font=dict(size=10, color=MUTED)),
                      paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10, r=10, t=10, b=10), height=300)
    return fig
