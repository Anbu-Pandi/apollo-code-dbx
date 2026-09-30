"""Shared UI kit for the Genie Admin pages (Reliability Radar + Conversation
Explorer). Look lives in assets/genie_admin.css (central, class-based, reuses the
apollo_home.css palette). This module just emits the markup with those classes so
every Genie page is uniform. Hex constants remain only for Plotly (charts can't
use CSS).
"""

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd

# palette for PLOTLY only (CSS handles the DOM)
GREEN = "#007B63"
GREEN_DARK = "#005A48"
BLUE = "#395878"
ORANGE = "#EC9A5F"
SURFACE = "#FFFFFF"
TEXT = "#1a1a1a"
MUTED = "#5a5d5f"
BORDER = "#E5E0DB"
SANS = '"Segoe UI", Calibri, Helvetica, Arial, sans-serif'
ACCENT = GREEN
INK = SURFACE           # back-compat alias
PANEL = SURFACE
PANEL2 = "#F7F9F8"
SECTION_BG = "#EDF5F3"

SEVERITY_COLOR = {"critical": "#C0392B", "high": "#E67E22", "medium": "#C99A2E",
                  "low": BLUE, "unknown": "#8A8D8F"}
CLASS_COLOR = {"system": BLUE, "agent": GREEN, "user": "#8A8D8F", "unknown": "#8A8D8F"}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "unknown"]
SERIES = [GREEN, BLUE, ORANGE, "#7BA7BF", "#C99A2E", "#9DB89F", "#C0392B"]
FONT = dict(family=SANS, color=TEXT, size=12)
_GRID = "rgba(90,93,95,0.12)"


def s(row, key, default=""):
    """NA/None-safe string cell from a pandas row."""
    v = row.get(key, default)
    try:
        if v is None or pd.isna(v):
            return default
    except (TypeError, ValueError):
        pass
    return str(v)


# ── class-based components ──────────────────────────────────────────────

def eyebrow(text):
    return html.Div(text, className="genie-eyebrow")


def sev_chip(sev):
    sev = (sev or "unknown").lower()
    cls = sev if sev in SEVERITY_COLOR else "unknown"
    return html.Span(sev.upper(), className=f"genie-chip sev-{cls}")


def cls_chip(issue_class):
    c = (issue_class or "unknown").lower()
    cls = c if c in ("system", "agent", "user") else "user"
    return html.Span(c.upper(), className=f"genie-chip cls-{cls}")


def badge(txt, tone="agent"):
    """Small neutral badge; tone maps to a class colour."""
    tone = tone if tone in ("system", "agent", "user") else "agent"
    return html.Span(str(txt).upper(), className=f"genie-chip cls-{tone}")


def panel(title, subtitle, content, accent="green", flex=None, margin_top=None):
    cls = "genie-panel" + ({"blue": " accent-blue", "orange": " accent-orange"}.get(accent, ""))
    style = {}
    if flex:
        style["flex"] = flex
    if margin_top:
        style["marginTop"] = margin_top
    return html.Div([
        html.Div(title, className="genie-panel-title"),
        html.Div(subtitle, className="genie-panel-sub"),
        content,
    ], className=cls, style=style)


def kpi_card(label, value, accent="green", sub=""):
    cls = "genie-kpi" + ({"blue": " a-blue", "orange": " a-orange", "red": " a-red",
                          "grey": " a-grey"}.get(accent, ""))
    return html.Div([
        html.Div(f"{value}", className="genie-kpi-value"),
        html.Div(label, className="genie-kpi-label"),
        html.Div(sub, className="genie-kpi-sub"),
    ], className=cls)


_NAV = [
    ("Reliability Radar", "/genai-issues-report/", "radar"),
    ("Conversations", "/genai-conversations/", "conversations"),
]


def subnav(active):
    links = [dcc.Link(label, href=href, className="genie-tab" + (" active" if key == active else ""))
             for label, href, key in _NAV]
    return html.Div(links, className="genie-subnav")


def shell(active, eyebrow_text, title, subtitle, body):
    hero = html.Div([
        eyebrow(eyebrow_text),
        html.Div(title, className="genie-title"),
        html.Div(subtitle, className="genie-sub"),
    ], className="genie-hero")
    return html.Div([hero, subnav(active), html.Div(body)], className="genie-page")


def note(text):
    return html.Div(text, className="genie-note")


def style_fig(fig, height=300, empty_msg=None):
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=FONT,
                      height=height, margin=dict(l=10, r=20, t=10, b=10),
                      xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(color=MUTED)),
                      yaxis=dict(showgrid=True, gridcolor=_GRID, zeroline=False, tickfont=dict(color=MUTED)))
    if empty_msg:
        fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False),
                          annotations=[dict(text=empty_msg, x=0.5, y=0.5, xref="paper", yref="paper",
                                            showarrow=False, font=dict(color=MUTED, size=13))])
    return fig
