import dash_core_components as dcc
import dash_html_components as html

from common.utils.dtutil import today, dateadd
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import get_datepicker_control, make_loading
from dash_reports.vol_tracker_common.base_config import LOOKBACK_OPTIONS
from dash_reports.vol_tracker_common.styles import (
    ACCENT_COLOR, FILTER_BAR_STYLE, SUBMIT_BUTTON_STYLE, STY, make_filter,
    MATRIX_TAB_STYLE, MATRIX_TAB_SELECTED,
)
from dash_reports.vol_market_dashboard.config import (
    DASHBOARD_CONFIG, PAGE_PREFIX as PREFIX,
)


ASSET_TAB_STYLE = {
    'fontFamily': 'Helvetica, Arial, sans-serif',
    'fontWeight': '700',
    'fontSize': '13px',
    'padding': '12px 24px',
    'backgroundColor': '#f4f6f8',
    'border': '1px solid #dce1e6',
    'borderBottom': 'none',
    'borderRadius': '8px 8px 0 0',
    'color': '#7b8fa3',
    'marginRight': '4px',
}

ASSET_TAB_SELECTED = {
    'fontFamily': 'Helvetica, Arial, sans-serif',
    'fontWeight': '700',
    'fontSize': '13px',
    'padding': '12px 24px',
    'backgroundColor': 'white',
    'color': '#2c3e50',
    'border': '2px solid #006D77',
    'borderBottom': '2px solid white',
    'borderRadius': '8px 8px 0 0',
    'marginRight': '4px',
}

COMPARISON_PANEL_STYLE = {
    'backgroundColor': '#fafbfc',
    'borderRadius': '8px',
    'border': '1px solid #e2e6ea',
    'padding': '16px',
    'marginBottom': '16px',
}

TOP_N_OPTIONS = [
    {'label': 'Top 2', 'value': 2},
    {'label': 'Top 5', 'value': 5},
    {'label': 'Top 10', 'value': 10},
]

CLEAR_BUTTON_STYLE = {
    'backgroundColor': '#f85149',
    'color': 'white',
    'border': 'none',
    'padding': '6px 16px',
    'borderRadius': '4px',
    'cursor': 'pointer',
    'fontWeight': '600',
    'fontSize': '11px',
}

CHIPS_STYLE = {
    'display': 'flex',
    'flexWrap': 'wrap',
    'gap': '8px',
    'marginBottom': '12px',
}

WRAPPING_TABS_STYLE = {
    'borderBottom': f'2px solid {ACCENT_COLOR}',
}

ENTITY_TABS_WRAPPER_CSS = '''
.entity-tabs-wrapper .tab-container {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 2px !important;
}
'''


def get_dashboard_headers(app=None):
    if app is not None:
        app.title = DASHBOARD_CONFIG.page_title
    return html.Div([get_page_title(DASHBOARD_CONFIG.page_title), html.Br()])


def create_dashboard_layout():
    dt = dateadd(today(), '-1b')

    vol_unit_options = [
        {'label': 'bpd (bp/day)', 'value': 'bpd'},
        {'label': 'bp/yr (annualized)', 'value': 'bpyr'},
    ]

    primary_filters = [
        make_filter('As Of', get_datepicker_control(
            dt=dt, id=f'{PREFIX}_asof_date', disabled=False, style={'marginRight': '4px'})),
        make_filter('Lookback', dcc.Dropdown(
            id=f'{PREFIX}_lookback',
            options=[{'label': lb, 'value': lb} for lb in LOOKBACK_OPTIONS],
            value='1y', style={'width': '80px'}, clearable=False,
        )),
        make_filter('Vol Display', dcc.Dropdown(
            id=f'{PREFIX}_vol_unit',
            options=vol_unit_options,
            value='bpd', style={'width': '140px'}, clearable=False,
        )),
        make_filter('Top Signals', dcc.Dropdown(
            id=f'{PREFIX}_top_n_dropdown',
            options=TOP_N_OPTIONS,
            value=5, style={'width': '100px'}, clearable=False,
        )),
    ]

    primary_row = html.Div(primary_filters, style=STY['filter_row'])

    static_layout = html.Div([
        html.Br(),
        html.Div([primary_row], className='fund_filter', style=FILTER_BAR_STYLE),
        html.Div(
            html.Button('Submit', id=f'{PREFIX}_submit', className='submit-button',
                        style=SUBMIT_BUTTON_STYLE),
            className='fund_filter'),
        html.Br(),
    ])

    stores = html.Div([
        dcc.Store(id=f'{PREFIX}_selections', data=[]),
        dcc.Store(id=f'{PREFIX}_cached_data', data={}),
        dcc.Store(id=f'{PREFIX}_zscore_store', data=[]),
    ])

    dynamic_layout = make_loading(html.Div(children=[], className='page', id=f'{PREFIX}_output'))

    return html.Div([static_layout, stores]), dynamic_layout


SCALE_OPTIONS = [
    {'label': 'Raw Values', 'value': 'raw'},
    {'label': 'Normalized (100)', 'value': 'norm100'},
    {'label': '% Change', 'value': 'pct_chg'},
]


def build_comparison_panel(selections: list, chart_div, scale_mode: str = 'raw', table_div=None) -> html.Div:
    scale_dropdown = dcc.Dropdown(
        id=f'{PREFIX}_scale_mode',
        options=SCALE_OPTIONS,
        value=scale_mode,
        style={'width': '150px'},
        clearable=False,
    )

    if not selections:
        return html.Div([
            html.Div([
                html.Span('Comparison Chart', style={
                    'fontWeight': '700', 'fontSize': '14px', 'color': '#2c3e50'}),
                html.Div([
                    html.Span('Scale: ', style={'fontSize': '11px', 'color': '#666', 'marginRight': '4px'}),
                    scale_dropdown,
                ], style={'display': 'flex', 'alignItems': 'center'}),
            ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center', 'marginBottom': '12px'}),
            html.Div(
                'Click any signal cell below to add series to comparison (click again to remove)',
                style={
                    'padding': '30px',
                    'textAlign': 'center',
                    'color': '#8b949e',
                    'fontSize': '13px',
                    'backgroundColor': '#f0f3f6',
                    'borderRadius': '6px',
                }
            ),
        ], id=f'{PREFIX}_comparison_panel', style=COMPARISON_PANEL_STYLE)

    from dash_reports.vol_market_dashboard.config import ASSET_BY_KEY

    chips = []
    for sel in selections:
        asset = ASSET_BY_KEY.get(sel.get('asset', ''))
        color = asset.color if asset else '#8b949e'
        chips.append(html.Div([
            html.Span(f"{sel['entity']} {sel['inst']}", style={'fontWeight': '600'}),
            html.Span(f" ({sel['asset'].upper()})", style={'fontSize': '9px', 'opacity': '0.7'}),
        ], style={
            'display': 'inline-flex',
            'alignItems': 'center',
            'padding': '4px 10px',
            'backgroundColor': color,
            'color': 'white',
            'borderRadius': '12px',
            'fontSize': '11px',
        }))

    return html.Div([
        html.Div([
            html.Div([
                html.Span(f'Comparison ({len(selections)} series)', style={
                    'fontWeight': '700', 'fontSize': '14px', 'color': '#2c3e50'}),
            ]),
            html.Div([
                html.Span('Scale: ', style={'fontSize': '11px', 'color': '#666', 'marginRight': '4px'}),
                scale_dropdown,
                html.Button('Clear All', id=f'{PREFIX}_clear_btn', n_clicks=0,
                            style={**CLEAR_BUTTON_STYLE, 'marginLeft': '12px'}),
            ], style={'display': 'flex', 'alignItems': 'center'}),
        ], style={
            'display': 'flex', 'justifyContent': 'space-between',
            'alignItems': 'center', 'marginBottom': '12px',
        }),
        html.Div(chips, style=CHIPS_STYLE),
        chart_div,
        html.Div(table_div, style={'marginTop': '12px'}) if table_div else None,
    ], id=f'{PREFIX}_comparison_panel', style=COMPARISON_PANEL_STYLE)


def build_asset_tabs(cdx_content, rate_content, fx_content, active_asset='cdx') -> html.Div:
    tabs = dcc.Tabs([
        dcc.Tab(
            label='CDX Vol Market',
            value='cdx',
            style=ASSET_TAB_STYLE,
            selected_style=ASSET_TAB_SELECTED,
            children=[html.Div(cdx_content, style={'padding': '16px 0'})],
        ),
        dcc.Tab(
            label='Rate Vol Market',
            value='rate',
            style=ASSET_TAB_STYLE,
            selected_style=ASSET_TAB_SELECTED,
            children=[html.Div(rate_content, style={'padding': '16px 0'})],
        ),
        dcc.Tab(
            label='FX Vol Market',
            value='fx',
            style=ASSET_TAB_STYLE,
            selected_style=ASSET_TAB_SELECTED,
            children=[html.Div(fx_content, style={'padding': '16px 0'})],
        ),
    ], id=f'{PREFIX}_asset_tabs', value=active_asset,
       style={'borderBottom': f'2px solid {ACCENT_COLOR}'})

    return tabs


OUTPUT_DIV = f'{PREFIX}_output'
SUBMIT_ID = f'{PREFIX}_submit'
DATE_ID = f'{PREFIX}_asof_date'
LOOKBACK_ID = f'{PREFIX}_lookback'
VOL_UNIT_ID = f'{PREFIX}_vol_unit'
SELECTIONS_STORE = f'{PREFIX}_selections'
CACHED_DATA_STORE = f'{PREFIX}_cached_data'
CLEAR_BTN_ID = f'{PREFIX}_clear_btn'
ASSET_TABS_ID = f'{PREFIX}_asset_tabs'
SCALE_MODE_ID = f'{PREFIX}_scale_mode'
COMPARISON_PANEL_ID = f'{PREFIX}_comparison_panel'
TOP_N_ID = f'{PREFIX}_top_n_dropdown'
ZSCORE_STORE_ID = f'{PREFIX}_zscore_store'
