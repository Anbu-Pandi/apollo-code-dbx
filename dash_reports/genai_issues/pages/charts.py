"""Figures for the Reliability Radar — light, Apollo-palette, via the shared kit."""

import plotly.graph_objects as go

from dash_reports.report_common.pages.genie_ui import (
    CLASS_COLOR, GREEN, MUTED, SERIES, SEVERITY_COLOR, SEVERITY_ORDER, style_fig,
)


def chart_issue_trend(trend_df):
    fig = go.Figure()
    if trend_df is None or trend_df.empty or "day" not in trend_df.columns:
        return style_fig(fig, 300, empty_msg="No signal issues in range — clean skies ✅")
    for sev in [s for s in SEVERITY_ORDER if s in trend_df.columns]:
        fig.add_trace(go.Bar(x=trend_df["day"], y=trend_df[sev], name=sev.title(),
                             marker=dict(color=SEVERITY_COLOR.get(sev, "#8A8D8F"))))
    fig.update_layout(barmode="stack", bargap=0.35,
                      legend=dict(orientation="h", y=1.15, x=0, font=dict(size=11)))
    return style_fig(fig, 300)


def chart_class_split(df):
    fig = go.Figure()
    if df is None or df.empty or "issue_class" not in df.columns:
        return style_fig(fig, 300, empty_msg="—")
    counts = (df.groupby(df["issue_class"].fillna("unknown"))["occurrence_count"].sum()
              if "occurrence_count" in df.columns else df["issue_class"].fillna("unknown").value_counts())
    labels = [c for c in ["system", "agent", "user", "unknown"] if c in counts.index]
    vals = [int(counts[c]) for c in labels]
    if not vals or sum(vals) == 0:
        return style_fig(fig, 300, empty_msg="—")
    fig.add_trace(go.Pie(labels=[l.title() for l in labels], values=vals, hole=0.62, sort=False,
                         marker=dict(colors=[CLASS_COLOR.get(l, "#8A8D8F") for l in labels],
                                     line=dict(color="#FFFFFF", width=2)),
                         textinfo="label+percent", textfont=dict(size=12)))
    fig.update_layout(showlegend=False,
                      annotations=[dict(text=f"{sum(vals)}<br>occurrences", x=0.5, y=0.5,
                                        showarrow=False, font=dict(color=MUTED, size=15))])
    return style_fig(fig, 300)


def chart_by_category(cat_df):
    fig = go.Figure()
    if cat_df is None or cat_df.empty:
        return style_fig(fig, 300, empty_msg="—")
    cat_df = cat_df.sort_values("occurrences")
    n = len(cat_df)
    colors = [SERIES[i % len(SERIES)] for i in range(n)]
    fig.add_trace(go.Bar(x=cat_df["occurrences"], y=[c.replace("_", " ").title() for c in cat_df["category"]],
                         orientation="h", marker=dict(color=colors), text=cat_df["occurrences"],
                         textposition="outside", textfont=dict(color=MUTED, size=11)))
    fig.update_layout(bargap=0.4)
    return style_fig(fig, max(240, 46 * n + 60))
