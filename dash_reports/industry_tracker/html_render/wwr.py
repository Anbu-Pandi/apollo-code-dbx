from typing import Any, Dict, List

from dash_reports.industry_tracker.html_render.formatters import esc

WWR_STYLE = """
<style>
.wwr-grid{display:grid;grid-template-columns:repeat(auto-fit, minmax(420px, 1fr));
    gap:14px;margin-top:12px}
.wwr-card{background:white;border:1px solid var(--ecru);border-radius:4px;
    padding:0;box-shadow:0 1px 3px rgba(0,0,0,0.04);overflow:hidden}
.wwr-card-head{padding:10px 14px;background:var(--summary-bg);border-bottom:1px solid var(--ecru);
    cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.wwr-card-head:hover{background:rgba(85,88,90,0.08)}
.wwr-card-title{font-weight:700;font-size:12.5px;color:var(--apollo-green);
    text-transform:uppercase;letter-spacing:0.3px;flex:1}
.wwr-card-source{font-size:10px;color:var(--grey);font-style:italic;margin-right:10px}
.wwr-toggle-icon{font-weight:700;font-size:14px;color:var(--apollo-green);
    width:18px;text-align:center}
.wwr-card.expanded{box-shadow:0 2px 6px rgba(0,123,99,0.15)}
.wwr-card-body{padding:12px 14px;font-size:11.5px;line-height:1.6;color:var(--black)}
.wwr-card-body ul{margin:6px 0 6px 18px;padding:0}
.wwr-card-body li{margin-bottom:4px}
.wwr-card-body p{margin:6px 0}
.wwr-empty{padding:30px;text-align:center;color:var(--grey);font-style:italic}
</style>
"""


def _card(idx: int, item: Dict[str, Any]) -> str:
    title = esc(item.get('title') or item.get('headline') or 'Untitled')
    source = esc(item.get('source') or '')
    body = item.get('body') or item.get('summary') or ''
    return f"""<div class="wwr-card" data-card-idx="{idx}">
    <div class="wwr-card-head" onclick="toggleWwrCard({idx})">
        <span class="wwr-card-title">{title}</span>
        <span class="wwr-card-source">{source}</span>
        <span class="wwr-toggle-icon">+</span>
    </div>
    <div class="wwr-card-body" hidden>{body}</div>
</div>"""


def render(items: List[Dict[str, Any]], appended_html: str = '') -> str:
    cards = [_card(i, it) for i, it in enumerate(items or [])]
    body = (
        f'<div class="wwr-grid">{"".join(cards)}</div>' if cards
        else '<div class="wwr-empty">No items available. Update the WWR JSON for this industry.</div>'
    )
    return f"""<section class="tab-section" id="tab-wwr" hidden>
    <div class="subtitle-bar">Curated reading list updated nightly</div>
    {WWR_STYLE}
    {body}
    {appended_html}
</section>"""
