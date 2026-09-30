import json
import logging
import os
import re
from typing import Optional

import pandas as pd

from data_access.storage import atomic_file
from dash_reports.industry_tracker.data import data_loader
from dash_reports.industry_tracker.data.universe import get_industry
from dash_reports.industry_tracker.html_render import (
    credit_comps, earnings_calls, lt_historical, public_comps,
)


DEFAULT_TEMPLATE_PATH = 'HTML/Consumer_Tracker.html'
DEFAULT_OUTPUT_PATH = 'HTML/Consumer_Tracker_Argus.html'


def _configured_path(key: str, default: str) -> str:
    try:
        from apo_config.config import config
        return config.getconfig('industry_tracker', key) or default
    except Exception:
        return default


def _html_root() -> Optional[str]:
    try:
        from dash_reports.sub_servers.html_sub_server.server import get_html_folder
        return get_html_folder()
    except Exception:
        logging.exception('failed to resolve HTML sub-server root')
        return None


def _path(root: str, rel_path: str) -> str:
    return os.path.join(root, *str(rel_path).replace('\\', '/').split('/'))


def _replace_section(html: str, tab_id: str, replacement: str) -> str:
    start_re = re.compile(rf'<section\b[^>]*\bid=["\']{re.escape(tab_id)}["\'][^>]*>', re.IGNORECASE)
    match = start_re.search(html)
    if not match:
        raise ValueError(f'section not found: {tab_id}')
    end = html.find('</section>', match.end())
    if end < 0:
        raise ValueError(f'section end not found: {tab_id}')
    end += len('</section>')
    return html[:match.start()] + replacement + html[end:]


def _replace_legacy_earnings_module(html: str, replacement: str) -> str:
    start = html.find('<div class="ern-module">')
    if start < 0:
        return html
    script = re.search(r'\s*<script>\s*\n\s*function\s+toggleErnIssuer\s*\(', html[start:], re.IGNORECASE)
    if script is None:
        return html
    return html[:start] + replacement + '\n    </section>\n</div>\n' + html[start + script.start():]


def _patch_script(html: str, lt_data: dict, segment_count: int) -> str:
    html = html.replace(
        "document.querySelectorAll('#summary-table td.' + cellClass)",
        "document.querySelectorAll('.summary-table td.' + cellClass)",
    )
    html = re.sub(r'const segCount\s*=\s*\d+\s*;', f'const segCount = {segment_count};', html, count=1)
    lt_json = json.dumps(lt_data, separators=(',', ':'), default=str)
    html = re.sub(
        r'const LT_DATA\s*=\s*\{.*?\};\s*let ltCurrentMult',
        f'const LT_DATA = {lt_json};\nlet ltCurrentMult',
        html,
        count=1,
        flags=re.DOTALL,
    )
    return html


def generate_consumer_argus_html() -> Optional[str]:
    root = _html_root()
    if not root:
        logging.warning('HTML sub-server root is not configured')
        return None

    template_rel = _configured_path('argus_template_html', DEFAULT_TEMPLATE_PATH)
    output_rel = _configured_path('argus_output_html', DEFAULT_OUTPUT_PATH)
    template_path = _path(root, template_rel)
    output_path = _path(root, output_rel)

    industry = get_industry('consumer')
    if industry is None:
        logging.warning('consumer industry config is not loaded')
        return None
    if not os.path.exists(template_path):
        logging.warning('Argus template HTML not found: %s', template_path)
        return None

    bundle = data_loader.load_industry_bundle(industry.key)
    public_df = bundle.get('public_comps') if bundle.get('public_comps') is not None else pd.DataFrame()
    lt_df = bundle.get('lt_historical') if bundle.get('lt_historical') is not None else pd.DataFrame()
    loans_df = bundle.get('credit_loans')
    bonds_df = bundle.get('credit_bonds')
    calls_df = bundle.get('earnings_calls') if bundle.get('earnings_calls') is not None else pd.DataFrame()
    eps_df = bundle.get('earnings_eps')
    commentary = bundle.get('earnings_commentary') or {}

    public_html = public_comps.render(industry, public_df)
    credit_html = credit_comps.render(loans_df, bonds_df, industry.credit_sub_group_names(), None, industry.key)
    lt_html = lt_historical.render(industry, lt_df)
    lt_html = re.sub(r'\s*<script>window\.LT_DATA\s*=.*?</script>\s*', '\n', lt_html, flags=re.DOTALL)
    earnings_html = earnings_calls.render_module(industry, calls_df, commentary, eps_df=eps_df)

    with open(template_path, 'r', encoding='utf-8-sig') as f:
        html = f.read()
    html = _replace_legacy_earnings_module(html, earnings_html)
    html = _replace_section(html, 'tab-public-comps', public_html)
    html = _replace_section(html, 'tab-credit-comps', credit_html)
    html = _replace_section(html, 'tab-lt-historical', lt_html)
    html = _patch_script(html, lt_historical._build_lt_data(industry, lt_df), public_comps.get_segment_count(industry))

    atomic_file.write_text(html, output_path)
    logging.info('wrote Argus consumer tracker HTML: %s (%d chars)', output_path, len(html))
    return output_path
