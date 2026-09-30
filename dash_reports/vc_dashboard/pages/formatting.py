import math

from urllib.parse import urlencode

import pandas as pd
from dash import html
from dash.dash_table.Format import Format, Scheme, Symbol
import plotly.graph_objects as go

from dash_reports.report_common.pages.plotly_theme import GREEN, SAXE


def _usd(value):
    if value is None or not math.isfinite(float(value)):
        return "—"
    for scale, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if abs(value) >= scale:
            return f"${value / scale:,.3g}{suffix}"
    return f"${value:,.0f}"


def summary_cards(summary):
    cards = []
    for label, value, previous, note in [
        ("Total deal value", _usd(summary.get("total_raised_usd", 0)), "previous_raised_usd", ""),
        ("Number of deals", f"{summary.get('deal_count', 0):,}", "previous_deal_count", ""),
    ]:
        direction = ""
        if previous:
            prior = summary.get(previous, 0)
            current = summary.get("total_raised_usd" if previous == "previous_raised_usd" else "deal_count", 0)
            if prior:
                change = (current / prior - 1) * 100
                direction = "up" if change > 0 else "down" if change < 0 else ""
                note = f"{'↑' if change > 0 else '↓' if change < 0 else '→'} {abs(change):.1f}% vs preceding period"
            else:
                note = "No prior-period data"
        cards.append(html.Div([
            html.Div(label, className="vc-metric-label"), html.Div(value, className="vc-metric-value"),
            html.Div(note, className=f"vc-metric-note {direction}"),
        ], className="vc-metric"))
    return cards


def summary_charts(summary):
    trend = summary.get("trend", [])
    sector = list(reversed(summary.get("sector_capital", [])))
    capital = go.Figure(go.Bar(
        x=[r["date"] for r in trend], y=[r["capital"] for r in trend], marker_color=GREEN,
        customdata=[[_usd(r["capital"]), r["deals"]] for r in trend],
        hovertemplate="%{x|%d %b %Y}<br>%{customdata[0]} completed deal value<br>%{customdata[1]} deals<extra></extra>"))
    sectors = go.Figure(go.Bar(
        y=[r["sector"] for r in sector], x=[r["capital"] for r in sector], orientation="h",
        marker_color=SAXE, customdata=[_usd(r["capital"]) for r in sector],
        hovertemplate="%{y}<br>%{customdata}<extra></extra>"))
    for figure, values, axis in [(capital, [r["capital"] for r in trend], "yaxis"),
                                  (sectors, [r["capital"] for r in sector], "xaxis")]:
        maximum = max(values, default=0)
        ticks = [maximum * i / 4 for i in range(5)] if maximum else [0]
        figure.update_layout(template="plotly_white+apollo", margin=dict(l=16, r=20, t=8, b=32),
                             height=265, showlegend=False, bargap=.25,
                             **{axis: dict(rangemode="tozero", tickvals=ticks,
                                           ticktext=[_usd(v) for v in ticks])})
        if not summary.get("deal_count"):
            figure.add_annotation(text="No deals in this window", x=.5, y=.5,
                                  xref="paper", yref="paper", showarrow=False)
    capital.update_xaxes(type="date", showgrid=False, tickformat="%d %b")
    sectors.update_yaxes(type="category", showgrid=False)
    return capital, sectors


def firm_deal_feed(deals, followed=True):
    """Day-grouped feed of followed-firm deals, newest day first."""
    if not deals:
        return html.P("No deals match these filters. Try a longer window.",
                      className="vc-feed-empty")
    days = {}
    for deal in deals:
        days.setdefault(str(deal.get("deal_date") or "")[:10], []).append(deal)
    sections = []
    for day in sorted(days, reverse=True):
        rows = days[day]
        total = sum(d.get("deal_size_usd") or 0 for d in rows
                    if (d.get("deal_status") or "") == "Completed")
        try:
            label = pd.Timestamp(day).strftime("%A · %d %b %Y")
        except ValueError:
            label = day or "Undated"
        sections.append(html.Section([
            html.Div([html.H4(label), html.Span(
                f"{len(rows)} deal{'s' if len(rows) != 1 else ''}"
                + (f" · {_usd(total)} completed" if total else ""), className="vc-muted")],
                className="vc-feed-day-head"),
            html.Ul([html.Li([
                html.Div([
                    entity_link("company", d.get("company") or "—",
                                className="vc-ent-link vc-feed-company"),
                    html.Span(" · ".join(filter(None, [d.get("round_type"),
                                                       d.get("deal_status"),
                                                       _usd(d.get("deal_size_usd")) if d.get("deal_size_usd") else None])),
                              className="vc-feed-meta"),
                ], className="vc-feed-head"),
                html.Div([html.Span("Followed: " if followed else "Investors: ", className="vc-feed-label")]
                         + ([entity_link("investor", f.strip(), className="vc-ent-link")
                             for f in (d.get("followed_firms") if followed else d.get("investors") or d.get("lead_investors")).split(",")]
                            if (d.get("followed_firms") if followed else d.get("investors") or d.get("lead_investors"))
                            else [html.Span("participants not disclosed by source")]),
                         className="vc-feed-firms"),
                html.P((d.get("description") or "")[:280], className="vc-feed-desc"),
                entity_link("deal", d["deal_id"], "View exact deal →") if d.get("deal_id") else None,
            ], className="vc-feed-item") for d in rows], className="vc-feed-list"),
        ], className="vc-feed-day"))
    return sections


def entity_link(kind, name, children=None, className="vc-ent-link"):
    from argus_wiki.render.entity_collection_pages import entity_filename_slug
    dimension = {"company": "companies", "investor": "investors", "deal": "deals"}[kind]
    wiki_page = "default/page/" + dimension + "/" + entity_filename_slug(str(name))
    return html.A(children if children is not None else name,
                  href="/vc-dashboard?" + urlencode({"tab": "news", "news_page": wiki_page}),
                  className=className, **{"data-vc-news": wiki_page}, title=f"Open {name} in VC wiki")


def _backer_chips(backers):
    return [entity_link("investor", b["name"],
                        children=[b["name"]] + ([html.Span("lead", className="vc-chip-lead")]
                                                if b["lead"] else []),
                        className="vc-inv-chip vc-ent-chip" + (" followed" if b["followed"] else ""))
            for b in backers or []]


def _deal_row(d, show_company=True):
    meta = " · ".join(filter(None, [
        str(d.get("deal_date") or "")[:10], d.get("round_type"), d.get("deal_status"),
        _usd(d.get("deal_size_usd")) if d.get("deal_size_usd") else None,
        f"valued {_usd(d['post_valuation_usd'])}" if d.get("post_valuation_usd") else None,
        d.get("sector"), d.get("sub_sector")]))
    head = [entity_link("company", d.get("company"), className="vc-ent-link vc-feed-company")] \
        if show_company and d.get("company") else []
    return html.Li([
        html.Div(head + [html.Span(meta, className="vc-feed-meta")], className="vc-feed-head"),
        html.Div(_backer_chips(d.get("backers"))
                 or html.Span("Participants not disclosed by source", className="vc-muted"),
                 className="vc-inv-chips"),
        html.P((d.get("description") or "")[:260], className="vc-feed-desc"),
        entity_link("deal", d["deal_id"], "View exact deal →") if d.get("deal_id") else None,
    ], className="vc-feed-item")


def activity(rows):
    if not rows:
        return html.P("No activity matches these filters. Try a broader search, lower minimum or longer window.",
                      className="vc-feed-empty")
    cards = []
    for row in rows:
        other_kind = "investor" if row["kind"] == "company" else "company"
        chips = [entity_link(other_kind, other["name"], [other["name"],
                    html.Span(f" · {other['deals']} deals · {_usd(other['value_usd'])}", className="vc-inv-chip-size")],
                    className="vc-inv-chip vc-ent-chip") for other in row["counterparties"]]
        change = f"{row['change']:+,} vs {row['previous_deals']:,} prior"
        cards.append(html.Li([
            html.Div([entity_link(row["kind"], row["name"], className="vc-ent-link vc-feed-company"),
                      html.Span(f"{row['deals']:,} deals · {change} · {_usd(row['value_usd'])} completed · latest {row['latest']}",
                                className="vc-feed-meta"), _sparkline(row["monthly"])], className="vc-feed-head"),
            html.Div([html.Span(f"Top {min(5, row['counterparty_count'])} {'companies' if other_kind == 'company' else 'investors'}: ", className="vc-feed-label")]
                     + (chips or [html.Span("Participants not disclosed", className="vc-muted")]), className="vc-inv-chips"),
            html.Small(f"Completed value disclosed for {row['disclosed']} of {row['deals']} deals · 12-month activity above",
                       className="vc-muted"),
        ], className="vc-feed-item"))
    return html.Ul(cards, className="vc-feed-list")


def _sparkline(monthly):
    """Trailing-12M deal counts as CSS bars (window-independent momentum)."""
    peak = max(monthly or [0]) or 1
    return html.Div([html.Span(style={"height": f"{max(8, round(100 * n / peak))}%"},
                               className="vc-spark-bar" + (" empty" if not n else ""),
                               title=f"{n} deal{'s' if n != 1 else ''}")
                     for n in monthly], className="vc-spark",
                    title="Deals per month · trailing 12 months")


def _deal_chips(deals):
    return [entity_link("company", d.get("company") or "—", [html.Strong(d.get("company") or "—"),
                       html.Span(f" {_usd(d['deal_size_usd'])}" if d.get("deal_size_usd") else "",
                                 className="vc-inv-chip-size")],
                      className="vc-inv-chip vc-ent-chip")
            for d in deals]


def investor_activity(firms):
    if not firms:
        return html.P("No followed-firm activity in this window. Try a longer window.",
                      className="vc-feed-empty")
    rows = []
    for f in firms:
        stats = [f"{f['deals']} deal{'s' if f['deals'] != 1 else ''}"]
        if f.get("led"):
            stats.append(f"led {f['led']}")
        if f.get("value_usd"):
            stats.append(f"{_usd(f['value_usd'])} completed value")
        if f.get("new_holdings"):
            stats.append(f"{f['new_holdings']} new portfolio adds")
        if f.get("latest"):
            stats.append(f"latest {f['latest']}")
        rows.append(html.Li([
            html.Div([
                entity_link("investor", f["firm"], className="vc-inv-firm vc-ent-link"),
                html.Span(" · ".join(stats), className="vc-feed-meta"),
                _sparkline(f.get("monthly", [])),
            ], className="vc-feed-head"),
            html.Div(_deal_chips(f.get("top_deals", []))
                     or html.Span("No sized deals disclosed in this window", className="vc-muted"),
                     className="vc-inv-chips"),
        ], className="vc-feed-item"))
    return html.Ul(rows, className="vc-feed-list")


def firm_drill(drill):
    """One firm's syndicate view: window deals + co-investors (followed vs candidates)."""
    firm = drill.get("firm")
    deals = drill.get("deals", [])
    co = drill.get("co_investors", [])
    followed = [c for c in co if c["followed"]]
    outside = [c for c in co if not c["followed"]]

    def chips(items, cls):
        return [entity_link("investor", c["name"], [c["name"], html.Span(f" ×{c['n']}", className="vc-inv-chip-size")],
                          className=f"vc-inv-chip {cls}") for c in items]

    return [
        html.H4(f"{firm} — {len(deals)} deal{'s' if len(deals) != 1 else ''} in the window",
                className="vc-drill-firm"),
        html.Div([html.Span("Co-invests with: ", className="vc-feed-label"),
                  *(chips(followed, "followed") or [html.Span("no followed firms in these syndicates",
                                                              className="vc-muted")])],
                 className="vc-inv-chips"),
        html.Div([html.Span("Outside the watchlist: ", className="vc-feed-label"),
                  *(chips(outside[:12], "candidate") or [html.Span("none", className="vc-muted")])],
                 className="vc-inv-chips"),
        html.Ul([html.Li([
            html.Div([entity_link("company", d.get("company") or "—", className="vc-ent-link vc-feed-company"),
                      html.Span(" · ".join(filter(None, [str(d.get("deal_date") or "")[:10],
                                                         d.get("round_type"), d.get("deal_status"),
                                                         _usd(d.get("deal_size_usd")) if d.get("deal_size_usd") else None])),
                                className="vc-feed-meta")], className="vc-feed-head"),
            html.P((d.get("description") or "")[:240], className="vc-feed-desc"),
        ], className="vc-feed-item") for d in deals[:15]], className="vc-feed-list"),
    ]


def company_backers(deals):
    """Company-centric: recent sizable deals, each with its investor syndicate."""
    if not deals:
        return html.P("No deals match this window and size filter.", className="vc-feed-empty")
    return html.Ul([_deal_row(d) for d in deals], className="vc-feed-list")


def follow_candidates(candidates):
    """Investors seen most often alongside the watchlist — candidates to follow."""
    if not candidates:
        return html.Span("No recurring outside co-investors in this window.", className="vc-muted")
    return html.Div([entity_link("investor", c["name"], [c["name"], html.Span(f" ×{c['n']}", className="vc-inv-chip-size")],
                               className="vc-inv-chip vc-ent-chip candidate") for c in candidates],
                    className="vc-inv-chips")


def hot_companies(rows):
    """Companies heating up: raised, repeat rounds, valuation step-up."""
    if not rows:
        return html.P("No completed raises in this window.", className="vc-feed-empty")
    return html.Ul([html.Li([
        html.Div([entity_link("company", r["company"], className="vc-ent-link vc-feed-company"),
                  html.Span(" · ".join(filter(None, [
                      f"{_usd(r['raised_usd'])} raised" if r.get("raised_usd") else None,
                      f"{r['rounds']} round{'s' if r['rounds'] != 1 else ''}",
                      r.get("latest_round"),
                      f"valued {_usd(r['post_valuation_usd'])}" if r.get("post_valuation_usd") else None,
                      f"{r['step_up']}× step-up" if r.get("step_up") else None,
                      r.get("sector"), r.get("latest_date")])),
                            className="vc-feed-meta")], className="vc-feed-head"),
    ], className="vc-feed-item") for r in rows], className="vc-feed-list")


def geo_chart(countries, metric="size"):
    rows = [r for r in countries if r.get("country") not in (None, "Unknown")][:12]
    key = "value" if metric == "size" else "deals"
    rows = sorted(rows, key=lambda r: r.get(key) or 0)
    values = [float(r.get(key) or 0) for r in rows]
    labels = [r["country"] for r in rows]
    hover = [f"{r['country']}<br>{_usd(r.get('value') or 0)} completed · {r.get('deals', 0)} deals"
             for r in rows]
    figure = go.Figure(go.Bar(y=labels, x=values, orientation="h", marker_color=GREEN,
                              customdata=hover, hovertemplate="%{customdata}<extra></extra>"))
    maximum = max(values, default=0)
    ticks = [maximum * i / 4 for i in range(5)] if maximum else [0]
    tick_text = [_usd(v) if metric == "size" else f"{v:,.0f}" for v in ticks]
    figure.update_layout(template="plotly_white+apollo", margin=dict(l=16, r=20, t=8, b=32),
                         height=360, showlegend=False, bargap=.3,
                         xaxis=dict(rangemode="tozero", tickvals=ticks, ticktext=tick_text))
    figure.update_yaxes(type="category", showgrid=False)
    if not rows:
        figure.add_annotation(text="No located deals in this window", x=.5, y=.5,
                              xref="paper", yref="paper", showarrow=False)
    return figure


def heatmap_table(heatmap):
    sectors = heatmap.get("sectors", [])
    buckets = heatmap.get("buckets", [])
    matrix = heatmap.get("matrix", [])
    flat = [v for row in matrix for v in row if v]
    vmax = max(flat, default=1)
    size = heatmap.get("metric") == "size"
    scale, suffix = next(((s, t) for s, t in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]
                          if size and vmax >= s), (1, ""))
    number_format = Format(precision=1 if size else 0, scheme=Scheme.fixed,
                           symbol=Symbol.yes if size else Symbol.no,
                           symbol_prefix="$" if size else "", symbol_suffix=suffix, group=True)
    columns = [{"name": "Sector", "id": "sector"}] + [
        {"name": b, "id": b, "type": "numeric", "format": number_format} for b in buckets]
    data, styles = [], []
    for i, sec in enumerate(sectors):
        row = {"sector": sec}
        for j, bucket in enumerate(buckets):
            val = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else 0
            row[bucket] = val / scale
            if val:
                # color-mix keeps the gradient legible in both light and dark themes
                # (the dark theme's DataTable override exempts this table by id).
                pct = 12 + round(83 * val / vmax)
                styles.append({"if": {"row_index": i, "column_id": bucket},
                               "backgroundColor": f"color-mix(in srgb, var(--apollo-green) {pct}%, var(--apollo-surface))",
                               "color": "#ffffff" if pct >= 60 else "var(--apollo-text)"})
        data.append(row)
    return columns, data, styles
