import json
from typing import Any, Dict, List, Tuple

import pandas as pd

from dash_reports.industry_tracker.data.universe import Industry
from dash_reports.industry_tracker.html_render.formatters import esc, fmt_x, cls_pos_neg, slug

LT_STYLE = """
<style>
.lt-toggle-bar{display:flex;gap:8px;margin:12px 0;padding:8px;
    background:var(--summary-bg);border-radius:4px;align-items:center}
.lt-toggle-bar label{font-weight:600;color:var(--grey);font-size:11px;text-transform:uppercase;
    letter-spacing:0.4px;margin-right:6px}
.lt-toggle-btn{padding:6px 14px;font-family:inherit;font-size:11px;font-weight:600;
    background:white;color:var(--apollo-green);border:1px solid var(--apollo-green);
    border-radius:3px;cursor:pointer;transition:all 0.15s ease;text-transform:uppercase;
    letter-spacing:0.3px}
.lt-toggle-btn:hover{background:var(--apollo-green);color:white}
.lt-toggle-btn.active{background:var(--apollo-green);color:white;
    box-shadow:0 1px 3px rgba(0,123,99,0.3)}
.lt-grid{display:grid;grid-template-columns:repeat(auto-fit, minmax(360px, 1fr));
    gap:14px;margin-top:10px}
.lt-sector{margin-top:24px}
.lt-sector-title{font-family:'Segoe UI',sans-serif;font-weight:700;font-size:12px;
    color:var(--apollo-green);text-transform:uppercase;letter-spacing:0.35px;
    padding:6px 0 6px 10px;border-left:3px solid var(--apollo-green)}
.lt-card{background:white;border:1px solid var(--ecru);border-radius:4px;
    padding:12px 14px 10px;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.lt-card-title{font-weight:700;font-size:13px;color:var(--apollo-green);
    border-bottom:1px solid var(--ecru);padding-bottom:6px;margin-bottom:8px;
    text-transform:uppercase;letter-spacing:0.3px}
.lt-stats{display:grid;grid-template-columns:repeat(3, 1fr);gap:8px;margin-bottom:8px;
    font-size:10px;color:var(--grey)}
.lt-stat{text-align:center;padding:4px 6px;background:var(--summary-bg);border-radius:3px}
.lt-stat-label{display:block;font-size:9px;text-transform:uppercase;letter-spacing:0.4px}
.lt-stat-val{display:block;font-weight:700;color:var(--black);font-size:13px;margin-top:2px}
.lt-stat-val.pos{color:var(--green)}
.lt-stat-val.neg{color:var(--red)}
.lt-chart-wrap{position:relative;height:180px;margin-top:6px}
.lt-period-row{display:flex;gap:6px;margin-top:6px;font-size:9.5px}
.lt-period-cell{flex:1;text-align:center;padding:3px;background:var(--summary-bg);border-radius:2px}
.lt-period-label{display:block;color:var(--grey);text-transform:uppercase;
    letter-spacing:0.3px;font-size:8.5px}
.lt-period-val{display:block;font-weight:700;color:var(--black)}
.lt-summary-table{margin-top:24px;width:100%}
.lt-summary-table th{cursor:pointer;user-select:none}
.lt-summary-table th[data-sort="asc"]::after{content:" \\25B2"}
.lt-summary-table th[data-sort="desc"]::after{content:" \\25BC"}
</style>
"""


def _split_group(sub_group: str) -> Tuple[str, str]:
    parts = str(sub_group or '').split(' / ', 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return 'Consumer', sub_group


def _groups_by_sector(sub_groups: List[str]) -> List[Tuple[str, List[str]]]:
    out, seen = [], {}
    for sg in sub_groups:
        sector, _ = _split_group(sg)
        if sector not in seen:
            seen[sector] = []
            out.append((sector, seen[sector]))
        seen[sector].append(sg)
    return out


def _build_lt_data(industry: Industry, df: pd.DataFrame) -> Dict[str, Any]:
    sub_groups = industry.sub_group_names()
    if df is None or df.empty:
        return {'dates': [], 'categories': sub_groups, 'ntm': {}, 'ltm': {},
                'avg_ntm': {}, 'avg_ltm': {}}

    dfs = df.sort_values('qtr_end').copy()
    if 'qtr_end' in dfs.columns:
        dfs['qtr_end_str'] = pd.to_datetime(dfs['qtr_end']).dt.strftime('%Y-%m-%d')
    else:
        dfs['qtr_end_str'] = ''

    dates = sorted(dfs['qtr_end_str'].unique().tolist())

    ntm: Dict[str, List] = {}
    ltm: Dict[str, List] = {}
    avg_ntm: Dict[str, float] = {}
    avg_ltm: Dict[str, float] = {}

    for sg in sub_groups:
        seg = dfs[dfs['sub_group'] == sg].set_index('qtr_end_str')
        ntm_vals = []
        ltm_vals = []
        for d in dates:
            if d in seg.index:
                row = seg.loc[d]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                ntm_v = row.get('ev_ebitda_ntm')
                ltm_v = row.get('ev_ebitda_ltm')
                ntm_vals.append(None if pd.isna(ntm_v) else float(ntm_v))
                ltm_vals.append(None if pd.isna(ltm_v) else float(ltm_v))
            else:
                ntm_vals.append(None)
                ltm_vals.append(None)
        ntm[sg] = ntm_vals
        ltm[sg] = ltm_vals

        avg_ntm[sg] = float(seg['ev_ebitda_ntm_10y'].iloc[-1]) if 'ev_ebitda_ntm_10y' in seg and not seg.empty and not pd.isna(seg['ev_ebitda_ntm_10y'].iloc[-1]) else None
        avg_ltm[sg] = float(seg['ev_ebitda_ltm_10y'].iloc[-1]) if 'ev_ebitda_ltm_10y' in seg and not seg.empty and not pd.isna(seg['ev_ebitda_ltm_10y'].iloc[-1]) else None

    return {
        'dates': dates,
        'categories': sub_groups,
        'ntm': ntm,
        'ltm': ltm,
        'avg_ntm': avg_ntm,
        'avg_ltm': avg_ltm,
    }


def _last_n(values: List, n: int) -> List:
    out = [v for v in values if v is not None]
    return out[-n:]


def _avg(values: List) -> float:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _card(idx: int, sub_group: str, lt: Dict[str, Any]) -> str:
    _, subsector = _split_group(sub_group)
    ntm_series = lt['ntm'].get(sub_group, [])
    ltm_series = lt['ltm'].get(sub_group, [])
    cur_ntm = next((v for v in reversed(ntm_series) if v is not None), None)
    cur_ltm = next((v for v in reversed(ltm_series) if v is not None), None)
    avg_ntm = lt['avg_ntm'].get(sub_group)
    avg_ltm = lt['avg_ltm'].get(sub_group)

    def fmt_data(v):
        return '' if v is None else f'{v:.4f}'

    p1y_ntm = _avg(_last_n(ntm_series, 4))
    p3y_ntm = _avg(_last_n(ntm_series, 12))
    p5y_ntm = _avg(_last_n(ntm_series, 20))
    p1y_ltm = _avg(_last_n(ltm_series, 4))
    p3y_ltm = _avg(_last_n(ltm_series, 12))
    p5y_ltm = _avg(_last_n(ltm_series, 20))

    return f"""<div class="lt-card">
    <div class="lt-card-title">{esc(subsector)}</div>
    <div class="lt-stats">
        <div class="lt-stat">
            <span class="lt-stat-label">Current</span>
            <span class="lt-stat-val lt-stat-cur"
                  data-cur-ntm="{fmt_data(cur_ntm)}" data-cur-ltm="{fmt_data(cur_ltm)}">--</span>
        </div>
        <div class="lt-stat">
            <span class="lt-stat-label">10-Yr Avg</span>
            <span class="lt-stat-val lt-stat-avg"
                  data-avg-ntm="{fmt_data(avg_ntm)}" data-avg-ltm="{fmt_data(avg_ltm)}">--</span>
        </div>
        <div class="lt-stat">
            <span class="lt-stat-label">vs Avg</span>
            <span class="lt-stat-val lt-stat-cur" id="lt-vs-avg-{idx}">--</span>
        </div>
    </div>
    <div class="lt-period-row">
        <div class="lt-period-cell">
            <span class="lt-period-label">1Y</span>
            <span class="lt-period-val" data-val-ntm="{fmt_data(p1y_ntm)}" data-val-ltm="{fmt_data(p1y_ltm)}">--</span>
        </div>
        <div class="lt-period-cell">
            <span class="lt-period-label">3Y</span>
            <span class="lt-period-val" data-val-ntm="{fmt_data(p3y_ntm)}" data-val-ltm="{fmt_data(p3y_ltm)}">--</span>
        </div>
        <div class="lt-period-cell">
            <span class="lt-period-label">5Y</span>
            <span class="lt-period-val" data-val-ntm="{fmt_data(p5y_ntm)}" data-val-ltm="{fmt_data(p5y_ltm)}">--</span>
        </div>
    </div>
    <div class="lt-chart-wrap"><canvas id="lt-chart-{idx}"></canvas></div>
</div>"""


def _summary_table(industry: Industry, lt: Dict[str, Any]) -> str:
    def fd(v):
        return '' if v is None else f'{v:.4f}'

    tables = []
    for sector, sub_groups in _groups_by_sector(industry.sub_group_names()):
        rows = []
        for sg in sub_groups:
            _, subsector = _split_group(sg)
            ntm_series = lt['ntm'].get(sg, [])
            ltm_series = lt['ltm'].get(sg, [])
            cur_ntm = next((v for v in reversed(ntm_series) if v is not None), None)
            cur_ltm = next((v for v in reversed(ltm_series) if v is not None), None)
            avg_ntm = lt['avg_ntm'].get(sg)
            avg_ltm = lt['avg_ltm'].get(sg)
            pd_ntm = (cur_ntm - avg_ntm) if (cur_ntm is not None and avg_ntm is not None) else None
            pd_ltm = (cur_ltm - avg_ltm) if (cur_ltm is not None and avg_ltm is not None) else None

            rows.append(f"""<tr>
            <td class="company-name">{esc(subsector)}</td>
            <td class="num"><span class="lt-sum-val" data-val-ntm="{fd(cur_ntm)}" data-val-ltm="{fd(cur_ltm)}">--</span></td>
            <td class="num"><span class="lt-sum-val" data-val-ntm="{fd(avg_ntm)}" data-val-ltm="{fd(avg_ltm)}">--</span></td>
            <td class="num"><span class="lt-sum-pd" data-pd-ntm="{fd(pd_ntm)}" data-pd-ltm="{fd(pd_ltm)}">--</span></td>
        </tr>""")

        tables.append(f"""<div class="lt-sector">
    <div class="lt-sector-title">{esc(sector)}</div>
    <div class="table-wrap">
        <table class="lt-summary-table">
            <thead>
                <tr>
                    <th>Subsector</th>
                    <th class="num">Current</th>
                    <th class="num">10-Yr Avg</th>
                    <th class="num">vs. Avg</th>
                </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
</div>""")

    return f"""<div style="margin-top: 28px;">
    <div class="summary-title">Sector Multiple Snapshot <span class="hint">(click headers to sort)</span></div>
{''.join(tables)}
</div>"""


def render(industry: Industry, df: pd.DataFrame) -> str:
    lt = _build_lt_data(industry, df)
    card_by_group = {
        sg: _card(i, sg, lt)
        for i, sg in enumerate(industry.sub_group_names())
    }
    sector_blocks = []
    for sector, sub_groups in _groups_by_sector(industry.sub_group_names()):
        sector_blocks.append(f"""<div class="lt-sector">
    <div class="lt-sector-title">{esc(sector)}</div>
    <div class="lt-grid">{''.join(card_by_group[sg] for sg in sub_groups)}</div>
</div>""")
    summary = _summary_table(industry, lt)

    body = f"""<div class="lt-toggle-bar">
        <label>Multiple:</label>
        <button class="lt-toggle-btn active" data-mult="ntm" type="button">NTM EV/EBITDA</button>
        <button class="lt-toggle-btn" data-mult="ltm" type="button">LTM EV/EBITDA</button>
    </div>
    {''.join(sector_blocks)}
    {summary}
    <div class="source">Source: FactSet Snowflake. Quarterly aggregates (median across constituents); 10-year averages computed across all available quarterly observations within the trailing 10-year window.</div>"""

    lt_data_json = json.dumps(lt)
    return f"""<section class="tab-section" id="tab-lt-historical" hidden>
    <div class="subtitle-bar">Quarterly EV/EBITDA history with 10-year average comparison</div>
    {LT_STYLE}
    {body}
    <script>window.LT_DATA = {lt_data_json};</script>
</section>"""
