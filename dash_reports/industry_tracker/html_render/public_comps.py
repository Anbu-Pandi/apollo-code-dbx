from typing import List, Tuple

import pandas as pd

from dash_reports.industry_tracker.data.universe import Industry
from dash_reports.industry_tracker.html_render.formatters import (
    esc, fmt_price, fmt_pct, fmt_pct_no_sign, fmt_x, fmt_money_billions, cls_pos_neg, slug,
)

PUBLIC_COMPS_STYLE = """
<style>
.summary-title{font-family:'Segoe UI',sans-serif;font-weight:600;font-size:13px;color:var(--saxe-blue);
    padding:6px 0;letter-spacing:0.3px;text-transform:uppercase}
.summary-title .hint{font-weight:300;font-size:11px;color:var(--grey);text-transform:none;
    letter-spacing:0;margin-left:8px}
.sector-summary{margin-top:18px}
.sector-summary-title{font-family:'Segoe UI',sans-serif;font-weight:700;font-size:12px;
    color:var(--apollo-green);text-transform:uppercase;letter-spacing:0.35px;
    padding:6px 0 6px 10px;border-left:3px solid var(--apollo-green)}
.summary-table{table-layout:fixed;min-width:1100px}
.summary-table col.col-subsector{width:190px}
.summary-table col.col-metric{width:90px}
.summary-table col.col-mult{width:78px}
.summary-table th,.summary-table td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.summary-table thead th{white-space:normal;line-height:1.2}
.summary-table thead tr.header-group-row th.group-header{
    text-align:center;background:var(--header-bg);color:var(--grey);
    border-bottom:1px solid var(--ecru);border-left:1px solid var(--ecru);
    border-right:1px solid var(--ecru);font-weight:700}
.summary-table thead th.sub{background:rgba(85,88,90,0.08);font-size:9px;font-weight:600;
    text-transform:none;letter-spacing:0;padding:3px 6px}
.summary-table tr.sector-link{cursor:pointer}
.summary-table tr.sector-link:hover{background:var(--row-hover)}
.summary-table tr.sector-link:hover td.subsector-name{text-decoration:underline}
.summary-table td.subsector-name{text-align:left;font-weight:700;color:var(--black);font-size:11px}

.segment{margin-top:32px;scroll-margin-top:60px}
.segment-title{font-family:'Segoe UI',sans-serif;font-weight:700;font-size:14px;
    color:var(--apollo-green);padding:8px 0 8px 12px;border-left:3px solid var(--apollo-green);
    margin-bottom:6px;letter-spacing:0.3px;text-transform:uppercase;display:flex;align-items:baseline;
    gap:12px;flex-wrap:wrap}
.segment-title .sector-label{font-weight:500;font-size:11px;color:var(--grey)}
.detail-table{table-layout:fixed;min-width:1320px}
.detail-table col.col-company{width:200px}
.detail-table col.col-price{width:80px}
.detail-table col.col-metric{width:80px}
.detail-table col.col-mult{width:74px}
.detail-table th,.detail-table td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.detail-table thead th{white-space:normal;line-height:1.2}
.detail-table thead tr.header-group-row th.group-header{
    text-align:center;background:var(--header-bg);color:var(--grey);
    border-bottom:1px solid var(--ecru);border-left:1px solid var(--ecru);
    border-right:1px solid var(--ecru);font-weight:700}
.detail-table thead th.sub{background:rgba(85,88,90,0.08);font-size:9px;font-weight:600;
    text-transform:none;letter-spacing:0;padding:3px 6px}
.detail-table td.company-name{text-align:left;font-weight:600;color:var(--black);
    font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis}
.detail-table tr.data-row:hover{background:var(--row-hover)}
.source{margin-top:24px;padding:10px 14px;background:var(--summary-bg);
    border-left:3px solid var(--ecru);font-size:10.5px;color:var(--grey);
    font-style:italic;line-height:1.5}
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


def _summary_row(sub_group: str, df_seg: pd.DataFrame) -> str:
    _, subsector = _split_group(sub_group)
    pct_52w = df_seg['pct_52w_high'].median(skipna=True) if 'pct_52w_high' in df_seg else None
    rev_cagr = df_seg['ntm_rev_cagr'].median(skipna=True) if 'ntm_rev_cagr' in df_seg else None
    ebitda_cagr = df_seg['ntm_ebitda_cagr'].median(skipna=True) if 'ntm_ebitda_cagr' in df_seg else None
    ebitda_mgn = df_seg['ntm_ebitda_margin'].median(skipna=True) if 'ntm_ebitda_margin' in df_seg else None
    net_lev = df_seg['net_leverage'].median(skipna=True) if 'net_leverage' in df_seg else None
    ev_ebitda_ltm = df_seg['ev_ebitda_ltm'].median(skipna=True) if 'ev_ebitda_ltm' in df_seg else None
    ev_ebitda_ntm = df_seg['ev_ebitda_ntm'].median(skipna=True) if 'ev_ebitda_ntm' in df_seg else None
    ev_ebitda_next = df_seg['ev_ebitda_next'].median(skipna=True) if 'ev_ebitda_next' in df_seg else None
    avg_10y_ev_ebitda_ntm = df_seg['avg_10y_ev_ebitda_ntm'].median(skipna=True) if 'avg_10y_ev_ebitda_ntm' in df_seg else None
    ec_cur = df_seg['ev_ebitda_capex_cur'].median(skipna=True) if 'ev_ebitda_capex_cur' in df_seg else None
    ec_next = df_seg['ev_ebitda_capex_next'].median(skipna=True) if 'ev_ebitda_capex_next' in df_seg else None

    return f"""<tr class="sector-link" onclick="scrollToSector('section-{slug(sub_group)}')">
        <td class="subsector-name">{esc(subsector)}</td>
        <td class="num grad-52w">{fmt_pct_no_sign(pct_52w)}</td>
        <td class="num {cls_pos_neg(rev_cagr)}">{fmt_pct(rev_cagr, signed=True)}</td>
        <td class="num {cls_pos_neg(ebitda_cagr)}">{fmt_pct(ebitda_cagr, signed=True)}</td>
        <td class="num">{fmt_pct_no_sign(ebitda_mgn)}</td>
        <td class="num">{fmt_x(net_lev)}</td>
        <td class="num grad-tev">{fmt_x(ev_ebitda_ltm)}</td>
        <td class="num grad-tev">{fmt_x(ev_ebitda_ntm)}</td>
        <td class="num grad-tev">{fmt_x(ev_ebitda_next)}</td>
        <td class="num grad-tev">{fmt_x(avg_10y_ev_ebitda_ntm)}</td>
        <td class="num grad-ec">{fmt_x(ec_cur)}</td>
        <td class="num grad-ec">{fmt_x(ec_next)}</td>
    </tr>"""


def _render_sector_summary(industry: Industry, df: pd.DataFrame) -> str:
    tables = []
    for sector, sub_groups in _groups_by_sector(industry.sub_group_names()):
        rows_html = []
        for sg in sub_groups:
            df_seg = df[df['sub_group'] == sg] if 'sub_group' in df else pd.DataFrame()
            rows_html.append(_summary_row(sg, df_seg))
        tables.append(f"""<div class="sector-summary">
    <div class="sector-summary-title">{esc(sector)}</div>
    <div class="table-wrap">
        <table class="summary-table">
            <colgroup>
                <col class="col-subsector">
                <col class="col-metric"><col class="col-metric"><col class="col-metric">
                <col class="col-metric"><col class="col-metric">
                <col class="col-mult"><col class="col-mult"><col class="col-mult"><col class="col-mult">
                <col class="col-mult"><col class="col-mult">
            </colgroup>
            <thead>
                <tr class="header-group-row">
                    <th rowspan="2">Subsector</th>
                    <th rowspan="2" class="num">% 52Wk Hi</th>
                    <th rowspan="2" class="num">NTM Rev CAGR</th>
                    <th rowspan="2" class="num">NTM EBITDA CAGR</th>
                    <th rowspan="2" class="num">EBITDA Margin</th>
                    <th rowspan="2" class="num">Net Lev</th>
                    <th colspan="4" class="num group-header">TEV / EBITDA</th>
                    <th colspan="2" class="num group-header">TEV / E&minus;C</th>
                </tr>
                <tr>
                    <th class="num sub">'25A</th>
                    <th class="num sub">'26E</th>
                    <th class="num sub">NTM</th>
                    <th class="num sub">10-Yr Avg NTM</th>
                    <th class="num sub">'25A</th>
                    <th class="num sub">'26E</th>
                </tr>
            </thead>
            <tbody>
{''.join(rows_html)}
            </tbody>
        </table>
    </div>
</div>""")

    return f"""<div style="margin-top: 8px;">
    <div class="summary-title">Subsector Averages <span class="hint">(click any row to jump to detail)</span></div>
{''.join(tables)}
</div>"""


def _detail_data_row(row: pd.Series, seg_idx: int) -> str:
    return f"""<tr class="data-row">
            <td class="company-name">{esc(row.get('name'))}</td>
            <td class="num">{fmt_price(row.get('share_price'), row.get('currency'))}</td>
            <td class="num {cls_pos_neg(row.get('chg_1m'))}">{fmt_pct(row.get('chg_1m'), signed=True)}</td>
            <td class="num grad-hi-seg-{seg_idx}">{fmt_pct_no_sign(row.get('pct_52w_high'))}</td>
            <td class="num grad-lo-seg-{seg_idx}">{fmt_pct_no_sign(row.get('pct_52w_low'))}</td>
            <td class="num">{fmt_money_billions(row.get('mkt_cap_usd_m'))}</td>
            <td class="num">{fmt_money_billions(row.get('tev_usd_m'))}</td>
            <td class="num {cls_pos_neg(row.get('ntm_rev_cagr'))}">{fmt_pct(row.get('ntm_rev_cagr'), signed=True)}</td>
            <td class="num {cls_pos_neg(row.get('ntm_ebitda_cagr'))}">{fmt_pct(row.get('ntm_ebitda_cagr'), signed=True)}</td>
            <td class="num">{fmt_pct_no_sign(row.get('ntm_ebitda_margin'))}</td>
            <td class="num">{fmt_x(row.get('net_leverage'))}</td>
            <td class="num grad-tev-seg-{seg_idx}">{fmt_x(row.get('ev_ebitda_ltm'))}</td>
            <td class="num grad-tev-seg-{seg_idx}">{fmt_x(row.get('ev_ebitda_ntm'))}</td>
            <td class="num grad-tev-seg-{seg_idx}">{fmt_x(row.get('ev_ebitda_next'))}</td>
            <td class="num grad-tev-seg-{seg_idx}">{fmt_x(row.get('avg_10y_ev_ebitda_ntm'))}</td>
            <td class="num grad-ec-seg-{seg_idx}">{fmt_x(row.get('ev_ebitda_capex_cur'))}</td>
            <td class="num grad-ec-seg-{seg_idx}">{fmt_x(row.get('ev_ebitda_capex_next'))}</td>
        </tr>"""


def _detail_table(sub_group: str, df_seg: pd.DataFrame, seg_idx: int) -> str:
    sector, subsector = _split_group(sub_group)
    rows_html = [_detail_data_row(row, seg_idx) for _, row in df_seg.iterrows()]
    return f"""<div class="segment" id="section-{slug(sub_group)}">
    <div class="segment-title">{esc(subsector)} <span class="sector-label">{esc(sector)}</span></div>
    <div class="table-wrap">
        <table class="detail-table" data-seg="{seg_idx}">
            <colgroup>
                <col class="col-company"><col class="col-price">
                <col class="col-metric"><col class="col-metric"><col class="col-metric">
                <col class="col-metric"><col class="col-metric"><col class="col-metric">
                <col class="col-metric"><col class="col-metric"><col class="col-metric">
                <col class="col-mult"><col class="col-mult"><col class="col-mult"><col class="col-mult">
                <col class="col-mult"><col class="col-mult">
            </colgroup>
            <thead>
                <tr class="header-group-row">
                    <th rowspan="2" data-sort-col="0">Company</th>
                    <th rowspan="2" class="num" data-sort-col="1">Price</th>
                    <th rowspan="2" class="num" data-sort-col="2">1 Mo Chg</th>
                    <th colspan="2" class="num group-header">% of 52-Week</th>
                    <th rowspan="2" class="num" data-sort-col="5">Mkt Cap</th>
                    <th rowspan="2" class="num" data-sort-col="6">TEV</th>
                    <th rowspan="2" class="num" data-sort-col="7">NTM Rev CAGR</th>
                    <th rowspan="2" class="num" data-sort-col="8">NTM EBITDA CAGR</th>
                    <th rowspan="2" class="num" data-sort-col="9">EBITDA Margin</th>
                    <th rowspan="2" class="num" data-sort-col="10">Net Lev</th>
                    <th colspan="4" class="num group-header">TEV / EBITDA</th>
                    <th colspan="2" class="num group-header">TEV / E&minus;C</th>
                </tr>
                <tr>
                    <th class="num sub" data-sort-col="3">High</th>
                    <th class="num sub" data-sort-col="4">Low</th>
                    <th class="num sub" data-sort-col="11">'25A</th>
                    <th class="num sub" data-sort-col="12">'26E</th>
                    <th class="num sub" data-sort-col="13">NTM</th>
                    <th class="num sub" data-sort-col="14">10-Yr Avg NTM</th>
                    <th class="num sub" data-sort-col="15">'25A</th>
                    <th class="num sub" data-sort-col="16">'26E</th>
                </tr>
            </thead>
            <tbody>
{''.join(rows_html)}
            </tbody>
        </table>
    </div>
</div>"""


def render(industry: Industry, df: pd.DataFrame) -> str:
    if df is None or df.empty:
        body = '<div class="placeholder"><h2>No Data</h2><p>Run the nightly refresh task to populate the public comps grid.</p></div>'
    else:
        summary = _render_sector_summary(industry, df)
        details = []
        for i, sg in enumerate(industry.sub_group_names()):
            df_seg = df[df['sub_group'] == sg] if 'sub_group' in df else df.iloc[0:0]
            details.append(_detail_table(sg, df_seg, i))
        body = f"""{summary}
    <div style="margin-top: 20px;">
        <div class="summary-title">Company Detail by Segment</div>
    </div>
{''.join(details)}
    <div class="source">Source: FactSet Snowflake. Market data and consensus estimates.</div>"""

    return f"""<section class="tab-section" id="tab-public-comps" hidden>
    <div class="subtitle-bar">US$ in billions, except for share price</div>
    {PUBLIC_COMPS_STYLE}
    {body}
</section>"""


def get_segment_count(industry: Industry) -> int:
    return len(industry.sub_group_names())
