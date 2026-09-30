import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from dash_reports.industry_tracker.html_render.formatters import (
    esc, fmt_pct, fmt_pct_no_sign, fmt_x, fmt_int, slug,
)


def _display(subsector: str, label_map: Optional[Dict[str, str]]) -> str:
    if label_map and subsector in label_map:
        return label_map[subsector]
    return subsector.title()


def _split_group(label: str) -> Tuple[str, str]:
    parts = str(label or '').split(' / ', 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return 'Consumer', label


def _groups_by_sector(subsectors: List[str], label_map: Optional[Dict[str, str]]) -> List[Tuple[str, List[str]]]:
    out, seen = [], {}
    for sub in subsectors:
        sector, _ = _split_group(_display(sub, label_map))
        if sector not in seen:
            seen[sector] = []
            out.append((sector, seen[sector]))
        seen[sector].append(sub)
    return out


CREDIT_COMPS_STYLE = """
<style>
#tab-credit-comps .summary-title{font-family:'Segoe UI',sans-serif;font-weight:600;font-size:13px;
    color:var(--saxe-blue);padding:6px 0;letter-spacing:0.3px;text-transform:uppercase}
#tab-credit-comps .summary-title .hint{font-weight:300;font-size:11px;color:var(--grey);
    text-transform:none;letter-spacing:0;margin-left:8px}
.credit-sector-summary{margin-top:18px}
.credit-sector-summary-title{font-family:'Segoe UI',sans-serif;font-weight:700;font-size:12px;
    color:var(--apollo-green);text-transform:uppercase;letter-spacing:0.35px;
    padding:6px 0 6px 10px;border-left:3px solid var(--apollo-green)}
.credit-summary-table{table-layout:fixed;min-width:1100px}
.credit-summary-table col.col-subsector{width:220px}
.credit-summary-table col.col-num{width:90px}
.credit-summary-table th,.credit-summary-table td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.credit-summary-table thead th{white-space:normal;line-height:1.2;position:sticky;top:0;z-index:10;
    background:var(--header-bg);color:var(--grey);font-weight:700;text-transform:uppercase;
    letter-spacing:0.4px;font-size:10px;padding:8px 10px;text-align:center;border-bottom:2px solid var(--ecru)}
.credit-summary-table thead th.group-header{border-left:1px solid var(--ecru);border-right:1px solid var(--ecru)}
.credit-summary-table thead th.sub{background:rgba(85,88,90,0.08);font-size:9px;font-weight:600;
    text-transform:none;letter-spacing:0;padding:3px 6px}
.credit-summary-table td{padding:6px 10px;font-size:11px;border-bottom:1px solid var(--border-light, #E5E0DB);
    text-align:center}
.credit-summary-table td.subsector-name{text-align:left;font-weight:700;color:var(--black);font-size:11px}
.credit-summary-table tr.sector-link{cursor:pointer}
.credit-summary-table tr.sector-link:hover{background:var(--row-hover)}
.credit-summary-table tr.sector-link:hover td.subsector-name{text-decoration:underline}

.credit-segment{margin-top:32px;scroll-margin-top:60px}
.credit-segment-title{font-family:'Segoe UI',sans-serif;font-weight:700;font-size:14px;
    color:var(--apollo-green);padding:8px 0 8px 12px;border-left:3px solid var(--apollo-green);
    margin-bottom:6px;letter-spacing:0.3px;text-transform:uppercase;display:flex;align-items:baseline;
    gap:14px;flex-wrap:wrap}
.credit-segment-title .sector-label{font-weight:500;font-size:11px;color:var(--grey)}
.credit-segment-title .seg-meta{font-weight:400;font-size:11px;color:var(--grey);
    text-transform:none;letter-spacing:0}

.credit-detail-table{table-layout:fixed;min-width:1280px}
.credit-detail-table col.col-security{width:240px}
.credit-detail-table col.col-rank{width:80px}
.credit-detail-table col.col-type{width:62px}
.credit-detail-table col.col-size{width:80px}
.credit-detail-table col.col-px{width:62px}
.credit-detail-table col.col-coup{width:65px}
.credit-detail-table col.col-spread{width:75px}
.credit-detail-table col.col-mat{width:78px}
.credit-detail-table col.col-rating{width:90px}
.credit-detail-table col.col-yield{width:65px}
.credit-detail-table th,.credit-detail-table td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.credit-detail-table thead th{white-space:normal;line-height:1.2;background:var(--header-bg);color:var(--grey);
    font-weight:700;text-transform:uppercase;letter-spacing:0.4px;font-size:10px;padding:8px 10px;
    text-align:center;border-bottom:2px solid var(--ecru);position:sticky;top:0;z-index:5}
.credit-detail-table thead th.group-header{border-left:1px solid var(--ecru);border-right:1px solid var(--ecru)}
.credit-detail-table thead th.sub{background:rgba(85,88,90,0.08);font-size:9px;font-weight:600;
    text-transform:none;letter-spacing:0;padding:3px 6px}
.credit-detail-table td{padding:5px 10px;font-size:11px;text-align:center;
    border-bottom:1px dashed var(--border-light, #E5E0DB)}
.credit-detail-table td.security-name{text-align:left;padding-left:24px;font-family:'Consolas','Courier New',monospace;
    font-size:10.5px;color:var(--grey);white-space:nowrap}

.credit-detail-table tr.issuer-header-row td{background:white;border-top:1px solid var(--ecru);
    border-bottom:none;padding:8px 10px 4px 10px;text-align:left}
.credit-detail-table tr.issuer-header-row.first-issuer td{border-top:none}
.issuer-header-content{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.issuer-name{font-weight:700;font-size:12px;color:var(--black);letter-spacing:0.1px}
.issuer-meta{font-size:10.5px;color:var(--grey);font-weight:400}
.issuer-rating{background:white;border:1px solid var(--ecru);padding:1px 6px;border-radius:3px;
    font-weight:600;color:var(--saxe-blue);font-size:10px}

.rank-pill,.type-pill{display:inline-block;padding:1px 7px;border-radius:3px;font-size:10px;
    font-weight:600;letter-spacing:0.2px;border:1px solid var(--border-light, #E5E0DB);
    background:white;color:var(--grey)}
.rank-1l{background:#E8F1ED;color:var(--apollo-green);border-color:var(--apollo-green)}
.rank-2l{background:#EFF3E5;color:#6B7A3F;border-color:#A8B574}
.rank-unsec{background:#F1E8DC;color:#6B4F2E;border-color:#8B6F4E}
.rank-sub{background:#FBE7E5;color:var(--matchstick);border-color:var(--matchstick)}
.rank-sec{background:#E8F1ED;color:var(--apollo-green);border-color:var(--apollo-green)}
.type-loan{background:rgba(57,88,120,0.08);color:var(--saxe-blue);border-color:var(--saxe-blue)}
.type-bond{background:rgba(115,162,211,0.12);color:#2A5C8A;border-color:var(--sky-blue)}

.px-distressed{color:var(--matchstick);font-weight:600}
.px-stressed{color:var(--orange);font-weight:600}

.twin-tag{display:inline-block;margin-left:6px;padding:0 5px;font-size:9px;font-weight:600;
    color:var(--grey);background:var(--light-grey);border:1px solid var(--ecru);
    border-radius:2px;letter-spacing:0.2px;font-family:'Segoe UI',sans-serif;
    vertical-align:middle}

.credit-source-note{margin-top:24px;padding:10px 14px;background:var(--summary-bg);
    border-left:3px solid var(--ecru);font-size:10.5px;color:var(--grey);
    font-style:italic;line-height:1.5}
</style>
"""


def _to_num(value):
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_points(value):
    v = _to_num(value)
    if v is None:
        return None
    return v * 100.0 if abs(v) < 1 else v


def _loan_spread_bp(value):
    v = _to_num(value)
    if v is None:
        return None
    if abs(v) < 1:
        return v * 10000.0
    if abs(v) < 100:
        return v * 100.0
    return v


def _bp(value) -> str:
    v = _to_num(value)
    if v is None:
        return '--'
    return f'{v:.0f} bp'


def _money_mm(value) -> str:
    v = _to_num(value)
    if v is None:
        return '--'
    if v >= 1000:
        return f'${v / 1000.0:,.2f}B'
    return f'${v:,.0f}mm'


def _price_class(price) -> str:
    v = _to_num(price)
    if v is None:
        return ''
    if v < 80:
        return 'px-distressed'
    if v < 90:
        return 'px-stressed'
    return ''


def _short_maturity(mat) -> str:
    if mat is None or pd.isna(mat):
        return '--'
    try:
        ts = pd.to_datetime(mat)
        return ts.strftime("%b '%y")
    except Exception:
        return str(mat)


_TRANCHE_RE = re.compile(r'\bterm\s+loan\s+([A-Z](?:[-]?\d+)?)\b', re.IGNORECASE)
_TLA_RE = re.compile(r'\bTL([A-Z]\d?)\b')


def _loan_tranche(facility_name) -> str:
    if not facility_name or (isinstance(facility_name, float) and pd.isna(facility_name)):
        return 'TL'
    name = str(facility_name)
    m = _TRANCHE_RE.search(name)
    if m:
        return m.group(1).upper()
    m = _TLA_RE.search(name)
    if m:
        return m.group(1).upper()
    lo = name.lower()
    if 'revolv' in lo or 'rcf' in lo or lo.startswith('rc'):
        return 'RC'
    if 'delayed' in lo and 'draw' in lo:
        return 'DD'
    if 'incremental' in lo:
        return 'Incr'
    if 'bridge' in lo:
        return 'Bridge'
    return 'TL'


def _loan_name(row) -> str:
    tranche = _loan_tranche(row.get('facility_name'))
    spd_bp = _loan_spread_bp(row.get('offer_spread_bp'))
    spd_str = f"S+{int(round(spd_bp))}bp" if spd_bp is not None and spd_bp > 0 else ''
    mty = _short_maturity(row.get('maturity_date'))
    parts = [f"Loan {tranche}"]
    if spd_str:
        parts.append(spd_str)
    if mty != '--':
        parts.append(mty)
    return ' '.join(parts)


_BENCH_LABELS = {
    'US0003M': 'L', 'US0001M': 'L', 'US0006M': 'L',
    'SOFRRATE': 'SOFR', 'SOFR': 'SOFR', 'SOFR3M': 'SOFR', 'SOFRINDX': 'SOFR',
    'EUR003M': 'E', 'EUR006M': 'E', 'EURIBOR': 'E',
    'BP0003M': 'SONIA', 'SONIO/N': 'SONIA', 'SONIA': 'SONIA',
}


def _bench_label(reset_idx) -> str:
    if not reset_idx or (isinstance(reset_idx, float) and pd.isna(reset_idx)):
        return 'FLT'
    raw = str(reset_idx).strip().upper()
    if raw in _BENCH_LABELS:
        return _BENCH_LABELS[raw]
    if 'SOFR' in raw:
        return 'SOFR'
    if raw.startswith('US') and raw.endswith('M'):
        return 'L'
    if raw.startswith('EUR'):
        return 'E'
    if 'SONIA' in raw or raw.startswith('BP'):
        return 'SONIA'
    return raw[:6]


def _bond_name(row) -> str:
    floater_flag = row.get('floater')
    cpn_typ_raw = row.get('coupon_type')
    cpn_typ = str(cpn_typ_raw).strip().upper() if cpn_typ_raw and not (isinstance(cpn_typ_raw, float) and pd.isna(cpn_typ_raw)) else ''
    is_floating = bool(floater_flag) or any(t in cpn_typ for t in ('FLOAT', 'FRN', 'VARIABLE', 'FLT'))
    mty = _short_maturity(row.get('maturity'))

    if is_floating:
        spread_pct = _to_num(row.get('basic_spread'))
        bench = _bench_label(row.get('reset_idx'))
        if spread_pct is not None and spread_pct > 0:
            cpn_str = f"{bench}+{int(round(spread_pct * 100))}bp"
        else:
            cpn_str = bench
    else:
        cpn = _to_num(row.get('coupon_pct'))
        if cpn is None:
            cpn = _to_num(row.get('__coupon_pct'))
        cpn_str = f"{cpn:.2f}%" if cpn is not None else ''

    parts = ['Bond']
    if cpn_str:
        parts.append(cpn_str)
    if mty != '--':
        parts.append(mty)
    return ' '.join(parts)


def _loan_rank(row) -> Tuple[str, str]:
    if (row.get('sec_lien_mkt_wt') or 0) > 0:
        return ('2nd Lien', 'rank-2l')
    return ('1st Lien', 'rank-1l')


def _bond_rank(row) -> Tuple[str, str]:
    junior = bool(row.get('junior'))
    senior = bool(row.get('senior'))
    if junior and not senior:
        return ('Sub', 'rank-sub')
    sub = (row.get('bsm_subgroup') or '').strip().lower()
    if 'secured' in sub or '1st lien' in sub or '1st-lien' in sub:
        return ('Sr Sec', 'rank-sec')
    return ('Sr Unsec', 'rank-unsec')


_NUM_COLS = ['__bid', '__ask', '__price', '__coupon_pct', '__spread_bp', '__ytw', '__size_mm']


def _coerce_num(df: pd.DataFrame) -> pd.DataFrame:
    for c in _NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def _normalize_loans(df: pd.DataFrame, subsectors: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df['type'] = 'Loan'
    end_price = pd.to_numeric(df.get('end_price'), errors='coerce') if 'end_price' in df.columns else pd.Series([None] * len(df))
    markit_bid = pd.to_numeric(df.get('markit_bid'), errors='coerce') if 'markit_bid' in df.columns else pd.Series([None] * len(df))
    markit_eval = pd.to_numeric(df.get('markit_evaluated'), errors='coerce') if 'markit_evaluated' in df.columns else pd.Series([None] * len(df))
    df['__bid'] = markit_bid.fillna(end_price)
    df['__ask'] = markit_eval.fillna(markit_bid).fillna(end_price)
    df['__price'] = df['__ask']
    df['__coupon_pct'] = df['coupon'].map(_pct_points) if 'coupon' in df.columns else None
    df['__spread_bp'] = df['offer_spread_bp'].map(_loan_spread_bp) if 'offer_spread_bp' in df.columns else None
    df['__ytw'] = df['fw_3y_yld'].map(_pct_points) if 'fw_3y_yld' in df.columns else None
    co = pd.to_numeric(df.get('current_outstanding'), errors='coerce') if 'current_outstanding' in df.columns else None
    df['__size_mm'] = co / 1_000_000.0 if co is not None else None
    df['__maturity'] = df.get('maturity_date')
    df['__rating'] = df.get('rating')
    df['__security_name'] = df.get('facility_name')
    df['__issuer'] = df.get('issuer_name')
    df['__issuer_ticker'] = df.get('issuer_ticker')
    df['__currency'] = df.get('currency')
    df['__subsector'] = df.get('sub_group')
    df = df[df['__subsector'].isin(subsectors)] if subsectors else df
    return _coerce_num(df)


def _normalize_bonds(df: pd.DataFrame, subsectors: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df['type'] = 'Bond'
    df['__bid'] = df.get('bid_price')
    df['__ask'] = df.get('ask_price')
    df['__price'] = df.get('mid_price')
    df['__coupon_pct'] = df.get('coupon_pct')
    df['__spread_bp'] = df.get('mid_z_spread')
    df['__ytw'] = df.get('mid_ytw')
    if 'amt_outstanding' in df.columns:
        amt = pd.to_numeric(df['amt_outstanding'], errors='coerce')
        df['__size_mm'] = amt / 1_000_000.0
    else:
        df['__size_mm'] = None
    df['__maturity'] = df.get('maturity')
    df['__rating'] = None
    cur = df['currency'] if 'currency' in df.columns else pd.Series([''] * len(df))
    isin = df['isin'] if 'isin' in df.columns else pd.Series([''] * len(df))
    df['__security_name'] = [f"{i} {c or ''}".strip() for i, c in zip(isin, cur)]
    df['__issuer'] = df.get('issuer_name')
    df['__issuer_ticker'] = df.get('issuer_ticker')
    df['__currency'] = df.get('currency')
    df['__subsector'] = df.get('sub_group')
    df = df[df['__subsector'].isin(subsectors)] if subsectors else df
    return _coerce_num(df)


def _summary_row(subsector: str, label: str, df_seg: pd.DataFrame) -> str:
    _, subsector_label = _split_group(label)
    if df_seg.empty:
        empty_cells = ''.join('<td class="num">--</td>' for _ in range(7))
        return f'<tr class="sector-link"><td class="subsector-name">{esc(subsector_label)}</td>{empty_cells}</tr>'

    issuers = df_seg['__issuer'].dropna().nunique()
    tranches = len(df_seg)
    loan_n = int((df_seg['type'] == 'Loan').sum())
    bond_n = int((df_seg['type'] == 'Bond').sum())

    med_bid = df_seg['__bid'].median(skipna=True)
    med_ytw = df_seg['__ytw'].median(skipna=True)
    med_sprd = df_seg['__spread_bp'].median(skipna=True)

    med_ytw_v = _to_num(med_ytw)
    return f"""<tr class="sector-link" onclick="scrollToCreditSection('credit-section-{slug(subsector)}')">
    <td class="subsector-name">{esc(subsector_label)}</td>
    <td class="num">{fmt_int(issuers)}</td>
    <td class="num">{fmt_int(tranches)}</td>
    <td class="num">{fmt_int(loan_n)}</td>
    <td class="num">{fmt_int(bond_n)}</td>
    <td class="num">{_fmt_2dp(med_bid)}</td>
    <td class="num">{fmt_pct_no_sign(med_ytw_v, decimals=2) if med_ytw_v is not None else '--'}</td>
    <td class="num">{_bp(med_sprd)}</td>
</tr>"""


def _summary_table(combined: pd.DataFrame, subsectors: List[str], label_map: Optional[Dict[str, str]]) -> str:
    tables = []
    for sector, sector_subs in _groups_by_sector(subsectors, label_map):
        rows = [_summary_row(sub, _display(sub, label_map), combined[combined['__subsector'] == sub]) for sub in sector_subs]
        tables.append(f"""<div class="credit-sector-summary">
    <div class="credit-sector-summary-title">{esc(sector)}</div>
    <div class="table-wrap">
        <table class="credit-summary-table">
            <colgroup>
                <col class="col-subsector">
                <col class="col-num"><col class="col-num">
                <col class="col-num"><col class="col-num">
                <col class="col-num"><col class="col-num"><col class="col-num">
            </colgroup>
            <thead>
                <tr>
                    <th rowspan="2">Subsector</th>
                    <th colspan="2" class="num group-header">Universe</th>
                    <th colspan="2" class="num group-header">Type Mix</th>
                    <th rowspan="2" class="num">Med Bid</th>
                    <th rowspan="2" class="num">Med YTW</th>
                    <th rowspan="2" class="num">Med Sprd</th>
                </tr>
                <tr>
                    <th class="num sub">Issuers</th>
                    <th class="num sub">Tranches</th>
                    <th class="num sub">Loans</th>
                    <th class="num sub">Bonds</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    </div>
</div>""")
    return f"""<div class="summary-title">Subsector Averages <span class="hint">(median values &middot; click any row to jump to detail)</span></div>
{''.join(tables)}"""


def _fmt_2dp(value) -> str:
    v = _to_num(value)
    return '--' if v is None else f'{v:.2f}'


def _max_date_str(s: pd.Series) -> str:
    if s is None or len(s) == 0:
        return '--'
    ts = pd.to_datetime(s, errors='coerce').dropna()
    if ts.empty:
        return '--'
    return ts.max().strftime('%Y-%m-%d')


def _loan_source_dates(df: Optional[pd.DataFrame]) -> Tuple[str, str]:
    if df is None or df.empty:
        return '--', '--'
    jpm_dt = _max_date_str(df['asof_date']) if 'asof_date' in df.columns else '--'
    mkt_dt = _max_date_str(df['markit_asof_date']) if 'markit_asof_date' in df.columns else '--'
    return jpm_dt, mkt_dt


def _tranche_row(row, is_loan: bool) -> str:
    if is_loan:
        rank_label, rank_cls = _loan_rank(row)
        type_label, type_cls = 'Loan', 'type-loan'
        sec_name = _loan_name(row)
    else:
        rank_label, rank_cls = _bond_rank(row)
        type_label, type_cls = 'Bond', 'type-bond'
        sec_name = _bond_name(row)

    twin = _to_num(row.get('twin_count'))
    twin_badge = ' <span class="twin-tag">144A+RS</span>' if (not is_loan and twin is not None and twin > 1) else ''

    bid = _to_num(row.get('__bid'))
    bid_cls = _price_class(bid)
    coup = _to_num(row.get('__coupon_pct'))
    coup_str = fmt_pct(coup, decimals=3) if coup is not None else '--'
    ytw = _to_num(row.get('__ytw'))
    ytw_str = fmt_pct_no_sign(ytw, decimals=2) if ytw is not None else '--'
    rating = row.get('__rating')

    return f"""<tr class="tranche-row" data-issuer="{esc(row.get('__issuer'))}">
    <td class="security-name">{esc(sec_name)}{twin_badge}</td>
    <td><span class="rank-pill {rank_cls}">{esc(rank_label)}</span></td>
    <td><span class="type-pill {type_cls}">{esc(type_label)}</span></td>
    <td class="num">{_money_mm(row.get('__size_mm'))}</td>
    <td class="num {bid_cls}">{_fmt_2dp(bid)}</td>
    <td class="num">{_fmt_2dp(row.get('__ask'))}</td>
    <td class="num">{coup_str}</td>
    <td class="num">{_bp(row.get('__spread_bp'))}</td>
    <td class="num">{_short_maturity(row.get('__maturity'))}</td>
    <td class="num">{esc(rating) if rating and not (isinstance(rating, float) and pd.isna(rating)) else '--'}</td>
    <td class="num">{ytw_str}</td>
</tr>"""


def _issuer_block(issuer: str, issuer_df: pd.DataFrame, first: bool) -> str:
    rating_vals = issuer_df['__rating'].dropna().unique().tolist()
    rating_str = rating_vals[0] if rating_vals else ''
    sizes = issuer_df['__size_mm'].dropna()
    total_size_mm = sizes.sum() if not sizes.empty else None
    n_tr = len(issuer_df)
    parts = []
    if rating_str:
        parts.append(f'<span class="issuer-rating">{esc(rating_str)}</span>')
    if total_size_mm is not None and total_size_mm > 0:
        parts.append(f'Total: {_money_mm(total_size_mm)}')
    parts.append(f'{n_tr} tranche{"s" if n_tr != 1 else ""}')
    meta = ' &middot; '.join(parts)

    first_cls = ' first-issuer' if first else ''
    rows = [
        f'<tr class="issuer-header-row{first_cls}" data-issuer-grp="{esc(issuer)}"><td colspan="11"><div class="issuer-header-content"><span class="issuer-name">{esc(issuer)}</span><span class="issuer-meta">{meta}</span></div></td></tr>',
    ]
    for _, row in issuer_df.iterrows():
        is_loan = row.get('type') == 'Loan'
        rows.append(_tranche_row(row, is_loan))
    return ''.join(rows)


def _detail_table(subsector: str, label: str, df_seg: pd.DataFrame) -> str:
    sector_label, subsector_label = _split_group(label)
    if df_seg.empty:
        return f"""<div class="credit-segment" id="credit-section-{slug(subsector)}">
    <div class="credit-segment-title">{esc(subsector_label)} <span class="sector-label">{esc(sector_label)}</span> <span class="seg-meta">no data</span></div>
</div>"""

    issuers_n = df_seg['__issuer'].dropna().nunique()
    loan_n = int((df_seg['type'] == 'Loan').sum())
    bond_n = int((df_seg['type'] == 'Bond').sum())
    tr_n = len(df_seg)

    df_sorted = df_seg.sort_values(['__issuer', 'type', '__size_mm'], ascending=[True, True, False], na_position='last')
    blocks = []
    first = True
    for issuer, sub in df_sorted.groupby('__issuer', sort=False):
        if not issuer:
            continue
        blocks.append(_issuer_block(issuer, sub, first))
        first = False

    return f"""<div class="credit-segment" id="credit-section-{slug(subsector)}">
    <div class="credit-segment-title">{esc(subsector_label)}
        <span class="sector-label">{esc(sector_label)}</span>
        <span class="seg-meta">{issuers_n} issuers &middot; {tr_n} tranches ({loan_n} loan / {bond_n} bond)</span>
    </div>
    <div class="table-wrap">
        <table class="credit-detail-table">
            <colgroup>
                <col class="col-security"><col class="col-rank"><col class="col-type">
                <col class="col-size">
                <col class="col-px"><col class="col-px">
                <col class="col-coup"><col class="col-spread">
                <col class="col-mat"><col class="col-rating">
                <col class="col-yield">
            </colgroup>
            <thead>
                <tr>
                    <th>Security</th><th>Rank</th><th>Type</th><th class="num">Size</th>
                    <th class="num">Bid</th><th class="num">Ask</th>
                    <th class="num">Coup</th><th class="num">Sprd</th>
                    <th class="num">Maturity</th><th class="num">Rating</th>
                    <th class="num">YTW</th>
                </tr>
            </thead>
            <tbody>
                {''.join(blocks)}
            </tbody>
        </table>
    </div>
</div>"""


def render(loans_df: Optional[pd.DataFrame] = None,
           bonds_df: Optional[pd.DataFrame] = None,
           subsectors: Optional[List[str]] = None,
           label_map: Optional[Dict[str, str]] = None,
           tracker_key: Optional[str] = None) -> str:
    subsectors = subsectors or []
    has_data = (loans_df is not None and not loans_df.empty) or (bonds_df is not None and not bonds_df.empty)
    if not has_data or not subsectors:
        body = """<div class="placeholder">
    <h2>Credit Comps</h2>
    <p>Run the nightly refresh to populate JPM Loan Index + Markit bond pricing.</p>
</div>"""
    else:
        loans_n = _normalize_loans(loans_df, subsectors) if loans_df is not None else pd.DataFrame()
        bonds_n = _normalize_bonds(bonds_df, subsectors) if bonds_df is not None else pd.DataFrame()
        cols = ['__subsector', '__issuer', '__issuer_ticker', '__security_name', 'type', '__rating',
                '__size_mm', '__bid', '__ask', '__price', '__coupon_pct', '__spread_bp',
                '__ytw', '__maturity', '__currency', 'sec_lien_mkt_wt', 'senior', 'junior', 'bsm_subgroup',
                'facility_name', 'offer_spread_bp', 'maturity_date',
                'coupon_type', 'coupon_pct', 'maturity', 'isin', 'twin_count',
                'floater', 'reset_idx', 'basic_spread', 'flt_bench_multiplier']
        for c in cols:
            if c not in loans_n.columns:
                loans_n[c] = None
            if c not in bonds_n.columns:
                bonds_n[c] = None
        combined = pd.concat([loans_n[cols], bonds_n[cols]], ignore_index=True) if (not loans_n.empty or not bonds_n.empty) else pd.DataFrame()

        summary = _summary_table(combined, subsectors, label_map)
        details = []
        for sub in subsectors:
            details.append(_detail_table(sub, _display(sub, label_map), combined[combined['__subsector'] == sub]))
        config_ref = f'data/universes/{tracker_key}.json' if tracker_key else 'data/universes/<tracker>.json'
        jpm_dt, mkt_dt = _loan_source_dates(loans_df)
        body = f"""{summary}
{''.join(details)}
<div class="credit-source-note">Sources: Markit loan pricing for live bid/evaluated marks (latest mark per loanx) &middot; JPM Leveraged Loan Index for loan descriptives (issuer / facility / spread / size) &middot; Markit pricing data (bonds).
Universe is driven by the tracker config at <code>{esc(config_ref)}</code>.
Loans pulled directly via JPM ticker; bonds resolved via Bloomberg ticker &rarr; ISIN in <code>bond_security_master</code>.
Loan data asof &mdash; JPM descriptives: <code>{esc(jpm_dt)}</code> &middot; Markit prices: <code>{esc(mkt_dt)}</code>.</div>"""

    return f"""<section class="tab-section" id="tab-credit-comps" hidden>
    <div class="subtitle-bar">JPM Leveraged Loan Index &middot; Markit Bond Pricing</div>
    {CREDIT_COMPS_STYLE}
    {body}
    <script>
    if (typeof window.scrollToCreditSection !== 'function') {{
        window.scrollToCreditSection = function(id) {{
            var el = document.getElementById(id);
            if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
        }};
    }}
    </script>
</section>"""
