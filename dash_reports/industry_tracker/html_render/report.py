import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from dash_reports.industry_tracker.constants import (
    TAB_KEYS, TAB_LABELS, DEFAULT_TAB,
)
from dash_reports.industry_tracker.data.universe import Industry
from dash_reports.industry_tracker.html_render import (
    public_comps as pc, lt_historical as lt, wwr, wwo, macro, credit_comps, earnings_calls,
)
from dash_reports.industry_tracker.html_render.formatters import esc
from dash_reports.report_common import theme

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')


_STYLE_OPEN_RE = re.compile(r'^\s*<style\b[^>]*>', re.IGNORECASE)
_STYLE_CLOSE_RE = re.compile(r'</style>\s*$', re.IGNORECASE)
_SCRIPT_OPEN_RE = re.compile(r'^\s*<script\b[^>]*>', re.IGNORECASE)
_SCRIPT_CLOSE_RE = re.compile(r'</script>\s*$', re.IGNORECASE)


def _read_static(filename: str) -> str:
    path = os.path.join(_STATIC_DIR, filename)
    if not os.path.exists(path):
        logging.warning('Static file missing: %s', path)
        return ''
    with open(path, 'r', encoding='utf-8-sig') as f:
        body = f.read()
    body = _STYLE_OPEN_RE.sub('', body)
    body = _STYLE_CLOSE_RE.sub('', body)
    body = _SCRIPT_OPEN_RE.sub('', body)
    body = _SCRIPT_CLOSE_RE.sub('', body)
    return body


def _header(industry: Industry, asof: Optional[datetime]) -> str:
    asof_str = asof.strftime('%m/%d/%Y') if asof else 'never'
    title = industry.display_name.replace(' Industry', ' Tracker')
    return f"""<header>
    <div>
        <h1>{esc(title)}</h1>
    </div>
    <div class="date">Data as of {asof_str}</div>
</header>"""


def _tab_bar(default_tab: str = DEFAULT_TAB) -> str:
    btns = []
    for key in TAB_KEYS:
        cls = ' active' if key == default_tab else ''
        btns.append(f'<button class="tab-btn{cls}" data-tab="{esc(key)}" role="tab" type="button">{esc(TAB_LABELS[key])}</button>')
    return f'<nav class="tab-nav" role="tablist">{"".join(btns)}</nav>'


def render_report(
    industry: Industry,
    public_comps_df: Optional[pd.DataFrame],
    lt_historical_df: Optional[pd.DataFrame],
    wwr_items: Optional[list],
    wwo_items: Optional[list],
    asof: Optional[datetime],
    credit_loans_df: Optional[pd.DataFrame] = None,
    credit_bonds_df: Optional[pd.DataFrame] = None,
    earnings_calls_df: Optional[pd.DataFrame] = None,
    earnings_commentary: Optional[dict] = None,
    earnings_eps_df: Optional[pd.DataFrame] = None,
) -> str:
    main_css = _read_static('styles_main.css')
    altd_css = _read_static('styles_altd.css')
    credit_css = _read_static('styles_credit.css')
    scripts_js = _read_static('scripts.js')

    seg_count = pc.get_segment_count(industry)
    credit_subsectors = industry.credit_sub_group_names()

    body_sections = [
        wwr.render(
            wwr_items or [],
            earnings_calls.render_module(
                industry,
                earnings_calls_df if earnings_calls_df is not None else pd.DataFrame(),
                earnings_commentary,
                eps_df=earnings_eps_df,
            ),
        ),
        wwo.render(wwo_items or []),
        macro.render(),
        pc.render(industry, public_comps_df if public_comps_df is not None else pd.DataFrame()),
        credit_comps.render(credit_loans_df, credit_bonds_df, credit_subsectors, None, industry.key),
        lt.render(industry, lt_historical_df if lt_historical_df is not None else pd.DataFrame()),
    ]

    asof_title = asof.strftime('%m/%d/%Y') if asof else 'never'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(industry.display_name)} - Public Comps | {esc(asof_title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>{main_css}</style>
<style>{altd_css}</style>
<style>{credit_css}</style>
{theme.head_tags()}
</head>
<body>
{_header(industry, asof)}
<div class="container">
{_tab_bar(DEFAULT_TAB)}
{''.join(body_sections)}
</div>
<script>{scripts_js}</script>
<script>document.addEventListener('DOMContentLoaded', function() {{
    showTab('{DEFAULT_TAB}');
    bootstrapPage({seg_count});
}});</script>
</body>
</html>"""
