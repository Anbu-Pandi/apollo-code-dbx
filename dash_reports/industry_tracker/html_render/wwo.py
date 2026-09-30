from typing import Any, Dict, List

from dash_reports.industry_tracker.html_render.formatters import esc

WWO_STYLE = """
<style>
.wwo-list{margin-top:12px}
.wwo-item{background:white;border:1px solid var(--ecru);border-radius:4px;
    padding:12px 16px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,0.03)}
.wwo-item-head{display:flex;justify-content:space-between;align-items:center;
    border-bottom:1px solid var(--ecru);padding-bottom:6px;margin-bottom:8px}
.wwo-item-title{font-weight:700;font-size:13px;color:var(--apollo-green);
    text-transform:uppercase;letter-spacing:0.3px}
.wwo-status{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;
    padding:3px 10px;border-radius:10px}
.wwo-status.active{background:#E0F4EB;color:var(--apollo-green)}
.wwo-status.diligence{background:#FFF1D6;color:#A06700}
.wwo-status.monitor{background:#E6E1DC;color:var(--grey)}
.wwo-status.completed{background:#D6E4FF;color:#1E3A8A}
.wwo-meta{display:flex;gap:14px;font-size:10.5px;color:var(--grey);margin-bottom:6px}
.wwo-meta span{padding-right:6px}
.wwo-body{font-size:11.5px;line-height:1.6;color:var(--black)}
.wwo-empty{padding:30px;text-align:center;color:var(--grey);font-style:italic}
</style>
"""


def _status_class(status: str) -> str:
    s = (status or '').lower()
    if s in ('active', 'live'):
        return 'active'
    if 'dilig' in s:
        return 'diligence'
    if 'monitor' in s or 'watch' in s:
        return 'monitor'
    if 'complet' in s or 'closed' in s:
        return 'completed'
    return 'monitor'


def _item(item: Dict[str, Any]) -> str:
    title = esc(item.get('title') or item.get('name') or 'Untitled')
    status = item.get('status') or 'Monitor'
    sector = esc(item.get('sector') or '')
    owner = esc(item.get('owner') or '')
    body = item.get('body') or item.get('notes') or item.get('summary') or ''
    meta = []
    if sector:
        meta.append(f'<span><b>Sector:</b> {sector}</span>')
    if owner:
        meta.append(f'<span><b>Owner:</b> {owner}</span>')
    meta_html = f'<div class="wwo-meta">{"".join(meta)}</div>' if meta else ''

    return f"""<div class="wwo-item">
    <div class="wwo-item-head">
        <span class="wwo-item-title">{title}</span>
        <span class="wwo-status {_status_class(status)}">{esc(status)}</span>
    </div>
    {meta_html}
    <div class="wwo-body">{body}</div>
</div>"""


def render(items: List[Dict[str, Any]]) -> str:
    rendered = [_item(it) for it in (items or [])]
    body = (
        f'<div class="wwo-list">{"".join(rendered)}</div>' if rendered
        else '<div class="wwo-empty">No active workstreams. Update the WWO JSON for this industry.</div>'
    )
    return f"""<section class="tab-section" id="tab-wwo" hidden>
    <div class="subtitle-bar">Active and monitoring opportunities</div>
    {WWO_STYLE}
    {body}
</section>"""
