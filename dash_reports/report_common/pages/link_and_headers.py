from datetime import date, timedelta

import dash_core_components as dcc
import dash_html_components as html
from .ui_config import get_save_config_submit


LEGACY_RELEASE_DATE = date(2024, 1, 1)
NEW_RELEASE_DATE = date(2026, 8, 1)
NEW_BADGE_DAYS = 92


def get_base_header_menu(app):
    return html.Div([html.Div(get_header_logo(app), style=dict(flex='1')),
                     dcc.Link('Email subscriptions', href='/email-subscriptions', style=dict(margin='0 24px', whiteSpace='nowrap')),
                     html.Div(get_save_config_submit())],
                    style=dict(display='flex', alignItems='center', flexWrap='wrap'))


def get_header_logo(app):
    # "Argus@Apollo" wordmark (mirrors the corporate Today@Apollo masthead).
    # Two variants swap on the theme toggle's data-theme attribute.
    logo = html.A(
        [
            html.Img(src=app.get_asset_url("images/argus_at_apollo_official.png"),
                     className="header-mark header-mark-light", alt="Argus@Apollo"),
            html.Img(src=app.get_asset_url("images/argus_at_apollo_official_dark.png"),
                     className="header-mark header-mark-dark", alt="Argus@Apollo"),
        ],
        href='/',
    )
    return html.Div(logo, className="row")


def get_page_title(pagetitle):
    return html.H6(pagetitle, className='subtitleheading')


_HOME_SECTIONS = [
    {
        "title": "Reports",
        "blurb": "Daily portfolio, risk, and liquidity reports.",
        "tone": "",
        "links": [
            ("Automated Reports", "/reports/", "Browse generated HTML, PDF, and image reports.", LEGACY_RELEASE_DATE),
            ("Beta Attribution Report", "/risk-report/overview", "Active beta decomposition vs benchmark.", LEGACY_RELEASE_DATE),
            ("Liquidity Report", "/liquidity-report/", "Position-level liquidity tiers and stress.", LEGACY_RELEASE_DATE),
            ("Portfolio Stats", "/pf-stats/", "Snapshot stats for funds and strategies.", LEGACY_RELEASE_DATE),
            ("Risk Query", "/risk-query/", "Ad-hoc risk pulls across portfolios.", LEGACY_RELEASE_DATE),
            ("TRACE Report", "/trace/flow-summary", "TRACE flow and dealer summary.", LEGACY_RELEASE_DATE),
            ("Trace Trade Summary", "/trade-summary-trace/", "Aggregate TRACE trade signatures.", LEGACY_RELEASE_DATE),
            ("Data Center", "/data-center", "Risk and pricing data exploration.", LEGACY_RELEASE_DATE),
            ("Data Viewer", "/data-viewer/", "Generic table viewer across datasets.", LEGACY_RELEASE_DATE),
        ],
    },
    {
        "title": "Credit & Index",
        "blurb": "Credit indices, tranches, and issuer analytics.",
        "tone": "",
        "links": [
            ("Credit Index Movers", "/index", "Top movers across CDX series.", LEGACY_RELEASE_DATE),
            ("CDX Live Quotes", "/cdx-live-quotes/", "Live Tradeweb dealer quotes, SDR trades and flow imbalance.", date(2026, 9, 17)),
            ("Credit Index Movers + Issuer", "/issuer-debt-index/movers", "Movers drilled into issuer bonds.", LEGACY_RELEASE_DATE),
            ("CDS-Cash Basis", "/cds-cash-basis/", "US & EU HY/IG cash-CDS basis — index, sector, and names.", LEGACY_RELEASE_DATE),
            ("ETF vs APO Funds", "/etf-vs-apo-funds/overlap-by-issuer", "Issuer overlap between ETFs and APO.", LEGACY_RELEASE_DATE),
            ("Issuer Info", "/issuer-info/", "Issuer-level credit, equity, news.", LEGACY_RELEASE_DATE),
        ],
    },
    {
        "title": "Cross-Asset & Macro",
        "blurb": "Vol surfaces, macro series, and data tools.",
        "tone": "",
        "links": [
            ("Vol Market Dashboard", "/vol-dashboard/", "Cross-asset vol regime monitor (rates, FX, CDX).", LEGACY_RELEASE_DATE),
            ("CDX Options Analytics", "/cdx-options/", "Index option chains, greeks, surfaces.", LEGACY_RELEASE_DATE),
            ("Cross-Asset Volatility & Correlation", "/cross-asset-correlation/", "ETF, CDX and rate volatility decomposition, implied and realized correlations.", date(2026, 9, 17)),
            ("CDX Tranche Pricer", "/cdx-tranche-pricer/", "Compute tranche pricing and risk.", LEGACY_RELEASE_DATE),
            ("Macro Data", "/macro-data/", "Rates, FX, commodities time series.", LEGACY_RELEASE_DATE),
            ("Benchmark Relative Performance", "/benchmark-perf/", "Performance vs custom benchmarks.", LEGACY_RELEASE_DATE),
        ],
    },
    {
        "title": "Private Markets",
        "blurb": "Venture, growth, and private-company intelligence.",
        "tone": "",
        "links": [
            ("VC Dashboard", "/vc-dashboard/", "Venture funding, sector deals heatmap, and semantic news.", date(2026, 9, 17)),
        ],
    },
    {
        "title": "AI & Knowledge",
        "blurb": "Argus knowledge, AI assistants, and connector setup.",
        "tone": "tone-accent",
        "links": [
            ("Argus Wiki", "/wiki/", "LLM-curated broker research vault.", NEW_RELEASE_DATE),
            ("APO Risk Intern", "/ai-chat/", "Conversational analyst over risk and P&L data.", LEGACY_RELEASE_DATE),
            ("Argus MCP Help", "/mcp/help", "Connect Argus tools to Cursor, Claude, ChatGPT, and other MCP clients.", NEW_RELEASE_DATE),
        ],
    },
    {
        "title": "Operations",
        "blurb": "Internal usage and event tracking.",
        "tone": "",
        "links": [
            ("Dash Usage Report", "/dash-usage-report/", "Who is using which report.", LEGACY_RELEASE_DATE),
            ("Data Catalog", "/data-catalog", "Discover governed production data assets across platforms.", NEW_RELEASE_DATE),
            ("Genie Admin", "/genai-issues-report/", "Argus Genie observability — reliability signal, conversation search & stats (admin).", NEW_RELEASE_DATE),
            ("Jobs Admin", "/jobs/server-dashboard/", "Job server / Airflow dashboard — schedules, runs, health (admin).", LEGACY_RELEASE_DATE),
            ("Prediction Markets Tracker", "/pred-tracker/", "Polymarket and Kalshi event monitor.", LEGACY_RELEASE_DATE),
            ("Analyst Questionnaire", "/analyst-questionnaire/", "Capture analyst views into structured inputs.", LEGACY_RELEASE_DATE),
        ],
    },
    {
        "title": "Compliance",
        "blurb": "Pre-trade compliance and required submissions.",
        "tone": "tone-warn",
        "links": [
            ("Compliance", "/compliance/", "Restricted lists and compliance checks.", LEGACY_RELEASE_DATE),
            ("Go Private Form", "/go-private-form/", "Submit go-private deal request.", LEGACY_RELEASE_DATE),
        ],
    },
]


def _is_new(release_date=None, today=None):
    release_date = release_date or LEGACY_RELEASE_DATE
    return (today or date.today()) < release_date + timedelta(days=NEW_BADGE_DAYS)


def _link_row(label, href, blurb, release_date=None):
    badge = "New" if _is_new(release_date) else None
    title_children = [html.Span(label, className='link-title')]
    if badge:
        title_children.append(html.Span(badge, className='badge'))
    children = [
        html.Div(title_children, style={'display': 'flex', 'alignItems': 'center'}),
        html.Span(blurb, className='link-blurb'),
    ]
    if str(href).startswith(("/wiki/", "/mcp/", "/reports/", "/jobs/")):
        return html.A(children=children, href=href, className='link-row')
    return dcc.Link(children=children, href=href, className='link-row')


def _section(spec):
    # Links sorted alphabetically by label within each section.
    rows = [_link_row(*entry) for entry in sorted(spec['links'], key=lambda e: e[0].lower())]
    return html.Div(
        children=[
            html.Div(spec['title'], className='section-title'),
            html.Div(spec['blurb'], className='section-sub'),
            html.Div(rows, className='link-list'),
        ],
        className=f"section {spec.get('tone', '')}".strip(),
    )


def get_index_page_links():
    hero = html.Div(
        children=[
            html.Div(
                children=[
                    # No title here — the "Argus@Apollo" wordmark in the global
                    # masthead is the only header. This is just a one-line lede.
                    html.P(
                        'Apollo research, risk, and AI tooling in one place — daily '
                        'reports for the workflow and the Argus Wiki for source-grounded broker research.',
                        className='hero-sub',
                    ),
                ],
                className='hero-inner',
            ),
        ],
        className='hero',
    )
    grid = html.Div(
        children=[_section(spec) for spec in _HOME_SECTIONS],
        className='grid',
    )
    footer = html.Div(
        children=[
            html.Span('Apollo Thematic Investing Team'),
            html.A('Support', href='mailto:ati_team@apollo.com'),
            html.A('Help', href='/assets/help/user_guide.html'),
        ],
        className='footer',
    )
    return html.Div([hero, grid, footer], className='apollo-home')
