from dash import dash_table, dcc, html
from dash_extensions import WebSocket

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import get_datepicker_control
from common.utils.dtutil import today
from dash_reports.cdx_options.pages.formatting.styles import (
    STATUS_BAR_STYLE, STATUS_BAR_HEADER_STYLE, STATUS_BAR_BODY_STYLE,
    FLEX_ROW, FLEX_ITEM, LABEL_STYLE, SECTION_BORDER,
    STRIP_STYLE, BTN_STYLE, GRID_HEADER, GRID_SUBTITLE, SECTION_HEADER,
    PAGE_CHROME_STYLE, PAGE_CONTENT_STYLE, CONTROL_SURFACE_STYLE, CONTROL_ROW_STYLE, CONTROL_GROUP_STYLE,
    CONTROL_LABEL_STYLE, INPUT_STYLE, SMALL_INPUT_STYLE, DROPDOWN_STYLE,
    SELECT_SMALL_STYLE, ACTION_BUTTON_STYLE, STATUS_ROW_STYLE, STATUS_DOT_STYLE,
    STATUS_TEXT_STYLE, STATUS_BADGE_STYLE, OVERRIDE_ROW_STYLE, TAB_BAR_STYLE,
    TAB_STYLE, TAB_SELECTED_STYLE, PANEL_CARD_STYLE, GRAPH_CARD_STYLE,
    GRAPH_GRID_STYLE, GRAPH_STYLE, GRAPH_CONFIG, LIGHT_TABLE_CELL, LIGHT_TABLE_HEADER,
    LIGHT_TABLE_WRAP, BLOTTER_FILTER_STYLE, OPTION_TABLE_CELL, OPTION_TABLE_HEADER,
    OPTION_TABLE_DATA, OPTION_TABLE_WRAP, OPTION_TABLE_FILTER,
)
from dash_reports.cdx_options.constants import (
    DEFAULT_TICKERS, TAB_INDEX,
    VOL_GRID_KEYS, VOL_GRID_LABELS, BLOTTER_KEYS, BLOTTER_LABELS,
    POSITION_KEYS, TENOR_OPTIONS,
    MAX_OPTION_STACKS, MAX_EXPIRY_CHART_SETS,
)

CDX_OPTIONS_SUBMIT_ID = 'cdx-options-submit'
CDX_OPTIONS_DATE_ID = 'cdx-options-date'
CDX_OPTIONS_PRODUCT_ID = 'cdx-options-product'
CDX_OPTIONS_ACTIVE_PRODUCT_ID = 'cdx-options-active-product'
CDX_OPTIONS_LOOKBACK_ID = 'cdx-options-lookback'
CDX_OPTIONS_MARK_POS_ID = 'cdx-options-mark-pos'
CDX_OPTIONS_STRIKE_ID = 'cdx-options-strike'
CDX_OPTIONS_EXPIRY_ID = 'cdx-options-expiry'
CDX_OPTIONS_STRIKE_EXPIRY_CONTAINER = 'cdx-options-strike-expiry-container'
CDX_OPTIONS_TABS_ID = 'cdx-options-tabs'
CDX_OPTIONS_TAB_CONTENT_ID = 'cdx-options-tab-content'

CDX_OPTIONS_PROGRESS_CONTAINER = 'cdx-options-progress-container'

CDX_OPTIONS_AUTOBOOT_INTERVAL = 'cdx-options-autoboot-interval'
CDX_OPTIONS_STATUS_TICK = 'cdx-options-status-tick'
CDX_OPTIONS_LAST_UPDATE_TEXT = 'cdx-options-last-update-text'
CDX_OPTIONS_INLINE_BADGE = 'cdx-options-inline-badge'

CDX_OPTIONS_REBUILD_GRIDS = 'cdx-options-rebuild-grids'
CDX_OPTIONS_GRID_BADGE = 'cdx-options-grid-badge'
CDX_OPTIONS_WS_STATUS = 'cdx-options-ws-status'

CDX_OPTIONS_REFRESH_FREQ = 'cdx-options-refresh-freq'
CDX_OPTIONS_MARK_TIME_ID = 'cdx-options-mark-time'
CDX_OPTIONS_THRESH_TIME_ID = 'cdx-options-thresh-time'
CDX_OPTIONS_TRADING_TZ_ID = 'cdx-options-trading-tz'
CDX_OPTIONS_TRADING_INFO_ID = 'cdx-options-trading-info'
CDX_OPTIONS_STATUS_BAR = 'cdx-options-status-bar'

CDX_OPTIONS_WS = 'cdx-options-ws'
CDX_OPTIONS_WS_CONTAINER = 'cdx-options-ws-container'
CDX_OPTIONS_WS_RECOVERY = 'cdx-options-ws-recovery'
CDX_OPTIONS_WS_PROGRESS_STORE = 'cdx-options-ws-progress-store'
CDX_OPTIONS_WS_LIGHT_STORE = 'cdx-options-ws-light-store'
CDX_OPTIONS_WS_ACTIVITY_STORE = 'cdx-options-ws-activity-store'
CDX_OPTIONS_WS_COMPUTE_STATUS_STORE = 'cdx-options-ws-compute-status-store'
CDX_OPTIONS_WS_SPOT_STORE = 'cdx-options-ws-spot-store'
CDX_OPTIONS_WS_STATUS_INFO_STORE = 'cdx-options-ws-status-info-store'
CDX_OPTIONS_WS_TASKS_STORE = 'cdx-options-ws-tasks-store'
CDX_OPTIONS_DELTA_ONE_STRIP = 'cdx-options-delta-one-strip'

CDX_OPTIONS_REF_PX_ID = 'cdx-options-ref-px'
CDX_OPTIONS_REF_BPS_ID = 'cdx-options-ref-bps'
CDX_OPTIONS_VOL_SHIFT_ID = 'cdx-options-vol-shift'
CDX_OPTIONS_VOL_LOOKBACK_ID = 'cdx-options-vol-lookback'
CDX_OPTIONS_OVERRIDE_ROW = 'cdx-options-override-row'
CDX_OPTIONS_PX_CONV = 'cdx-options-px-conv'
CDX_OPTIONS_BPS_CONV = 'cdx-options-bps-conv'
CDX_OPTIONS_OVERRIDE_STORE = 'cdx-options-override-store'
CDX_OPTIONS_TAB_STORE = 'cdx-options-tab-store'
CDX_OPTIONS_DD_PENDING = 'cdx-options-dd-pending'
CDX_OPTIONS_DD_COMMITTED = 'cdx-options-dd-committed'
CDX_OPTIONS_DD_DEBOUNCE = 'cdx-options-dd-debounce'
CDX_OPTIONS_URL = 'cdx-options-url'
CDX_OPTIONS_URL_SYNC = 'cdx-options-url-sync'
CDX_OPTIONS_TAB_CACHE = 'cdx-options-tab-cache'
CDX_OPTIONS_TAB_CACHE_TRIGGER = 'cdx-options-tab-cache-trigger'

CDX_OPTIONS_WS_TAB_DATA_STORE = 'cdx-ws-tab-data-store'
CDX_OPTIONS_WS_DEFAULTS_STORE = 'cdx-ws-defaults-store'
CDX_OPTIONS_LOADING_STORE = 'cdx-options-loading-store'
CDX_OPTIONS_LIFECYCLE = 'cdx-options-lifecycle'
CDX_OPTIONS_PRODUCT_MEMORY = 'cdx-options-product-memory'
CDX_OPTIONS_EVENT_LOG_STORE = 'cdx-options-event-log-store'
CDX_OPTIONS_SESSION_STORE = 'cdx-options-session-store'

TAB_PANEL_VOL_GRIDS = 'cdx-tab-panel-vol-grids'
TAB_PANEL_TRADE_BLOTTER = 'cdx-tab-panel-trade-blotter'
TAB_PANEL_POSITIONS = 'cdx-tab-panel-positions'
TAB_PANEL_EXPIRY_CHARTS = 'cdx-tab-panel-expiry-charts'
TAB_PANEL_OPTION_STACK = 'cdx-tab-panel-option-stack'
TAB_PANEL_RELATIVE_VOL = 'cdx-tab-panel-relative-vol'

TAB_PANELS = {
    'vol-grids': TAB_PANEL_VOL_GRIDS,
    'trade-blotter': TAB_PANEL_TRADE_BLOTTER,
    'positions': TAB_PANEL_POSITIONS,
    'expiry-charts': TAB_PANEL_EXPIRY_CHARTS,
    'option-stack': TAB_PANEL_OPTION_STACK,
    'relative-vol': TAB_PANEL_RELATIVE_VOL,
}

CDX_RV_OPT1_PRODUCT_ID = 'cdx-rv-opt1-product'
CDX_RV_OPT1_TENOR_ID = 'cdx-rv-opt1-tenor'
CDX_RV_OPT1_DELTA_ID = 'cdx-rv-opt1-delta'
CDX_RV_OPT1_SPACE_ID = 'cdx-rv-opt1-space'
CDX_RV_OPT2_PRODUCT_ID = 'cdx-rv-opt2-product'
CDX_RV_OPT2_TENOR_ID = 'cdx-rv-opt2-tenor'
CDX_RV_OPT2_DELTA_ID = 'cdx-rv-opt2-delta'
CDX_RV_OPT2_SPACE_ID = 'cdx-rv-opt2-space'
CDX_RV_SUBMIT_ID = 'cdx-rv-submit'


def _control(label, child, extra_style=None):
    return html.Div([
        html.Span(label, style=CONTROL_LABEL_STYLE),
        child,
    ], style={**CONTROL_GROUP_STYLE, **(extra_style or {})})


def _card(children, style=None):
    return html.Div(children, style={**PANEL_CARD_STYLE, **(style or {})})


def _graph(graph_id, style=None):
    return dcc.Graph(id=graph_id, responsive=True, config=GRAPH_CONFIG,
                     className='cdx-chart', style={**GRAPH_STYLE, **(style or {})})


def _graph_card(title, graph_id, graph_style=None, wrapper_id=None, wrapper_style=None):
    content = [
        html.H6(title, style=SECTION_HEADER),
        _graph(graph_id, graph_style),
    ]
    return html.Div(id=wrapper_id, style={**GRAPH_CARD_STYLE, **(wrapper_style or {})}, children=content)


def _chart_header(title, title_id=None):
    heading = html.H6(title, style=SECTION_HEADER, **({'id': title_id} if title_id else {}))
    return html.Div([
        html.Button('⠿', className='cdx-chart-drag', draggable='true', title='Drag to reorder chart',
                    **{'aria-label': 'Drag to reorder chart'}),
        heading,
        html.Button('↑', className='cdx-chart-move', **{'data-direction': '-1', 'aria-label': 'Move chart up'}),
        html.Button('↓', className='cdx-chart-move', **{'data-direction': '1', 'aria-label': 'Move chart down'}),
    ], className='cdx-chart-header')


def get_cdx_options_headers(app=None):
    if app is not None:
        app.title = 'CDX Options Trading'
    return html.Div([get_page_title('CDX Options Trading'), html.Br()])


def create_cdx_options_layout():
    dt = today()

    delta_one_strip = html.Div(id=CDX_OPTIONS_DELTA_ONE_STRIP, style=STRIP_STYLE)

    row_config = html.Div([
        _control('Tickers', dcc.Dropdown(
            id=CDX_OPTIONS_PRODUCT_ID,
            options=[{'label': t, 'value': t} for t in DEFAULT_TICKERS],
            value=DEFAULT_TICKERS, multi=True, style={**DROPDOWN_STYLE, 'width': '390px'},
        )),
        _control('Date', get_datepicker_control(dt=dt, id=CDX_OPTIONS_DATE_ID, disabled=False)),
        _control('Vol History', dcc.Dropdown(
            id=CDX_OPTIONS_LOOKBACK_ID,
            options=[{'label': x, 'value': x} for x in ['1m', '3m', '6m', '12m']],
            value='6m', clearable=False, style={**SELECT_SMALL_STYLE, 'width': '76px'},
        )),
        html.Div([
            dcc.Checklist(
                id=CDX_OPTIONS_MARK_POS_ID,
                options=[{'label': ' Mark Positions', 'value': 'mark'}],
                value=['mark'], style={'fontSize': '11px', 'fontWeight': '600'},
            ),
        ], style=CONTROL_GROUP_STYLE),
        _control('Mark Time', dcc.Input(
            id=CDX_OPTIONS_MARK_TIME_ID, type='text', placeholder='HH:MM',
            value='', debounce=True, style={**SMALL_INPUT_STYLE, 'width': '68px'},
        )),
        _control('Lookback', dcc.Dropdown(
            id=CDX_OPTIONS_THRESH_TIME_ID,
            options=[{'label': x, 'value': x} for x in ['2h', '4h', '6h', '8h', '12h', '24h']],
            value='4h', clearable=False, style={**SELECT_SMALL_STYLE, 'width': '76px'},
        )),
    ], style={**CONTROL_ROW_STYLE, 'paddingBottom': '8px', 'borderBottom': '1px solid #e4ebf3'})

    row_view = html.Div([
        _control('Product', dcc.Dropdown(
            id=CDX_OPTIONS_ACTIVE_PRODUCT_ID,
            options=[], value=None, style={**DROPDOWN_STYLE, 'width': '220px'},
            persistence=True, persistence_type='session',
        )),
        html.Div(id=CDX_OPTIONS_STRIKE_EXPIRY_CONTAINER, children=[
            _control('Expiry', dcc.Dropdown(
                id=CDX_OPTIONS_EXPIRY_ID, options=[], value=[], multi=True,
                style={**DROPDOWN_STYLE, 'width': '230px'},
                persistence=True, persistence_type='session',
            )),
            _control('Strike', dcc.Dropdown(
                id=CDX_OPTIONS_STRIKE_ID, options=[], value=[], multi=True,
                style={**DROPDOWN_STYLE, 'width': '185px'},
                persistence=True, persistence_type='session',
            )),
        ], style={**CONTROL_ROW_STYLE}),
        html.Div(style={'flex': '1'}),
        _control('Refresh', dcc.Dropdown(
            id=CDX_OPTIONS_REFRESH_FREQ,
            options=[
                {'label': '10s', 'value': 10},
                {'label': '30s', 'value': 30},
                {'label': '1 min', 'value': 60},
                {'label': '2 min', 'value': 120},
                {'label': '5 min', 'value': 300},
            ],
            value=300, clearable=False,
            style={**SELECT_SMALL_STYLE, 'width': '82px'},
        )),
        html.Button('\u2699 Rebuild Grids', id=CDX_OPTIONS_REBUILD_GRIDS, disabled=True,
                    style={**ACTION_BUTTON_STYLE, 'fontSize': '9px', 'color': '#8fa0b5'}),
    ], style={**CONTROL_ROW_STYLE, 'paddingTop': '8px'})

    _st = STATUS_TEXT_STYLE
    _sep = html.Span(' / ', style={'color': '#b8c7d9', 'fontSize': '10px'})
    row_status = html.Div([
        html.Span(id=CDX_OPTIONS_WS_STATUS, children='\u25cf',
                   style=STATUS_DOT_STYLE,
                   title='WS: connecting...'),
        html.Span(id=CDX_OPTIONS_TRADING_INFO_ID,
                   style={**STATUS_BADGE_STYLE, 'color': '#006D77'}),
        _sep,
        html.Span(id=CDX_OPTIONS_LAST_UPDATE_TEXT, style={**_st, 'color': '#65758b'}),
        html.Span(id=CDX_OPTIONS_INLINE_BADGE, style={'display': 'none'}),
        html.Div(style={'flex': '1'}),
        html.Span(id=CDX_OPTIONS_GRID_BADGE,
                   style={**STATUS_BADGE_STYLE, 'color': '#006D77'}),
    ], style=STATUS_ROW_STYLE)

    hidden_submit = html.Button(id=CDX_OPTIONS_SUBMIT_ID, disabled=True,
                                style={'display': 'none'})

    progress = html.Div(id=CDX_OPTIONS_PROGRESS_CONTAINER,
                        style={'display': 'none', 'padding': '4px 0'})

    settings = html.Details([
        html.Summary('Data settings · tickers, date & history'), row_config,
    ], className='cdx-data-settings')
    controls = html.Div([delta_one_strip, row_view, settings, row_status, progress, hidden_submit],
                        className='fund_filter', style=CONTROL_SURFACE_STYLE)

    tabs = dcc.Tabs(id=CDX_OPTIONS_TABS_ID, value='vol-grids',
                     style=TAB_BAR_STYLE,
                     persistence=True, persistence_type='session', children=[
        dcc.Tab(label='Vol Grids', value='vol-grids', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
        dcc.Tab(label='Trade Blotter', value='trade-blotter', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
        dcc.Tab(label='Positions & Greeks', value='positions', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
        dcc.Tab(label='Expiry Vol Charts', value='expiry-charts', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
        dcc.Tab(label='Option Stack', value='option-stack', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
        dcc.Tab(label='Relative Vol', value='relative-vol', style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
    ])

    _lbl = CONTROL_LABEL_STYLE
    _inp = SMALL_INPUT_STYLE
    _conv = {**STATUS_BADGE_STYLE, 'color': '#006D77', 'marginLeft': '4px'}
    _inl = {'display': 'inline-flex', 'alignItems': 'center', 'gap': '5px'}
    override_row = html.Div(id=CDX_OPTIONS_OVERRIDE_ROW, children=[
        html.Div([
            html.Span('Ref Px', style=_lbl),
            dcc.Input(id=CDX_OPTIONS_REF_PX_ID, type='number', placeholder='px',
                      debounce=True, style={**_inp, 'width': '85px'}),
            html.Span('', id=CDX_OPTIONS_PX_CONV, style=_conv),
        ], style=_inl),
        html.Div([
            html.Span('Ref Spread (bps)', style=_lbl),
            dcc.Input(id=CDX_OPTIONS_REF_BPS_ID, type='number', placeholder='bps',
                      debounce=True, style={**_inp, 'width': '85px'}),
            html.Span('', id=CDX_OPTIONS_BPS_CONV, style=_conv),
        ], style=_inl),
        html.Div([
            html.Span('Vol Shift (ann bps)', style=_lbl),
            dcc.Input(id=CDX_OPTIONS_VOL_SHIFT_ID, type='number', placeholder='0',
                      debounce=True, style={**_inp, 'width': '70px'}),
        ], style=_inl),
        html.Div([
            html.Span('Vol Chg Lookback', style=_lbl),
            dcc.Input(id=CDX_OPTIONS_VOL_LOOKBACK_ID, type='text', placeholder='5b',
                      value='5b', debounce=True, style={**_inp, 'width': '50px'}),
        ], style=_inl),
    ], style=OVERRIDE_ROW_STYLE)

    hidden_style = {'display': 'none'}
    visible_style = {'display': 'block'}

    _tbl_cell = {**LIGHT_TABLE_CELL, 'padding': '4px', 'minWidth': '62px', 'maxWidth': '100px'}
    _tbl_hdr = {**LIGHT_TABLE_HEADER, 'fontSize': '11px', 'minWidth': '62px', 'maxWidth': '100px'}
    _tbl_wrap = LIGHT_TABLE_WRAP

    vg_children = []
    for key in VOL_GRID_KEYS:
        vg_children.append(html.Div([
            html.Div([
                html.Span(VOL_GRID_LABELS.get(key, key), style=GRID_HEADER),
                html.Span('', id=f'cdx-vol-grid-{key}-title', style=GRID_SUBTITLE),
            ], style={'marginBottom': '6px', 'lineHeight': '1.3', 'display': 'flex',
                      'alignItems': 'baseline', 'gap': '6px', 'flexWrap': 'wrap'}),
            dash_table.DataTable(
                id=f'cdx-vol-grid-{key}', columns=[], data=[],
                style_data_conditional=[], style_cell=_tbl_cell,
                style_header=_tbl_hdr, style_table=_tbl_wrap,
                style_filter={'fontSize': '10px', 'padding': '3px 5px', 'backgroundColor': '#f8fafd'},
                cell_selectable=True, sort_action='native',
                filter_action='native', page_size=50,
            ),
        ], style=GRAPH_CARD_STYLE))
    vg_children.append(dcc.Store(id='vol-grid-base-styles', data={}))

    blotter_children = [
        html.Div([
            html.Span('Filter', style=CONTROL_LABEL_STYLE),
            dcc.Input(id='cdx-blotter-unified-filter', type='text',
                      placeholder='| OR, & AND, ~regex, col:val  (e.g. prod:NAIG&dir:long)',
                      debounce=True, style=BLOTTER_FILTER_STYLE),
        ], style={**CONTROL_ROW_STYLE, 'marginBottom': '8px'}),
        html.Div(id='cdx-blotter-loading-banner', style={'display': 'none'}),
    ]
    for bk in BLOTTER_KEYS:
        tid = f'cdx-blotter-{bk}'
        blotter_children.extend([
            html.Div([
                html.H6(BLOTTER_LABELS.get(bk, bk), style=SECTION_HEADER),
                dcc.Store(id=f'{tid}-full-data', data=[]),
                dcc.Store(id=f'{tid}-columns', data=[]),
                html.Div(id=f'{tid}-summary', style={'marginBottom': '6px'}),
                dash_table.DataTable(id=tid, columns=[], data=[],
                                     style_cell={**LIGHT_TABLE_CELL, 'minWidth': '72px', 'maxWidth': '140px'},
                                     style_header={**LIGHT_TABLE_HEADER, 'textAlign': 'center'},
                                     style_header_conditional=[
                                         {'if': {'column_id': ['product', 'option', 'dlr_dir']},
                                          'backgroundColor': '#21314a'},
                                         {'if': {'column_id': ['live_vol_ann', 'live_spot', 'live_delta', 'live_MVE', 'live_vega', 'live_pnl']},
                                          'backgroundColor': '#0f4c81', 'color': '#d9ecff'},
                                     ],
                                     style_cell_conditional=[
                                         {'if': {'column_id': ['execution_timestamp', 'product', 'option', 'spot', 'live_spot', 'expiration_date']},
                                          'textAlign': 'left'},
                                         {'if': {'column_id': 'product'}, 'fontWeight': '700'},
                                         {'if': {'column_id': 'option'}, 'fontWeight': '600'},
                                         {'if': {'column_id': 'dlr_dir'}, 'fontWeight': '700'},
                                     ],
                                     style_data={'backgroundColor': '#ffffff', 'color': '#172033'},
                                     style_table=LIGHT_TABLE_WRAP,
                                     sort_action='native', filter_action='native', page_size=10),
            ], style=GRAPH_CARD_STYLE),
        ])

    pos_children = []
    for key, label in POSITION_KEYS:
        pos_children.append(html.Div([
            html.H6(label, id=f'cdx-pos-{key}-title', style=SECTION_HEADER),
            _graph(f'cdx-pos-{key}', {'display': 'none'}),
        ], style=GRAPH_CARD_STYLE))

    exp_children = [
        html.Div([
            _chart_header('Volatility Skew', 'cdx-exp-vols-title'),
            _graph('cdx-exp-vols', {'display': 'none'}),
        ], id='cdx-exp-vols-wrap', className='cdx-orderable-chart', style=GRAPH_CARD_STYLE,
            **{'data-chart-key': 'skew', 'data-chart-rank': '1'}),
        html.Div(id='cdx-exp-spot-ts-wrap', style={'display': 'none'}, children=[
            _chart_header('Spot History'),
            _graph('cdx-exp-spot-ts'),
        ], className='cdx-orderable-chart', **{'data-chart-key': 'spot', 'data-chart-rank': '0'}),
    ]
    for _ei in range(MAX_EXPIRY_CHART_SETS):
        _eid = f'cdx-exp-chart-{_ei}'
        exp_children.append(html.Div(
            id=f'{_eid}-wrap', style={'display': 'none'},
            children=[
                _chart_header('', f'{_eid}-header'),
                _graph(f'{_eid}-vol-hist'),
            ],
            className='cdx-orderable-chart', **{'data-chart-key': '', 'data-chart-rank': str(_ei + 2)},
        ))

    _os_cell = OPTION_TABLE_CELL
    _os_hdr = OPTION_TABLE_HEADER
    _os_data = OPTION_TABLE_DATA
    _os_tbl = OPTION_TABLE_WRAP
    _os_flt = OPTION_TABLE_FILTER

    os_children = []
    for _i in range(MAX_OPTION_STACKS):
        _slot_id = f'cdx-option-ladder-{_i}'
        os_children.append(html.Div(id=f'{_slot_id}-wrap', style={'display': 'none'}, children=[
            html.Div([
                html.Span('', id=f'{_slot_id}-title', style=GRID_HEADER),
                html.Span('', id=f'{_slot_id}-subtitle', style=GRID_SUBTITLE),
            ], style={'marginBottom': '3px', 'lineHeight': '1.3'}),
            dash_table.DataTable(
                id=_slot_id, columns=[], data=[], style_data_conditional=[],
                style_cell=_os_cell, style_header=_os_hdr, style_data=_os_data,
                style_table=_os_tbl, style_filter=_os_flt,
                sort_action='native', filter_action='native', page_size=50,
            ),
        ]))

    def rv_leg(number):
        prefix = f'cdx-rv-opt{number}'
        return html.Div([
            html.H6(f'Leg {number}', style=SECTION_HEADER),
            html.Div([
                _control('Product', dcc.Dropdown(id=f'{prefix}-product', options=[], value=None,
                         style={**DROPDOWN_STYLE, 'width': '210px'})),
                _control('Tenor', dcc.Dropdown(id=f'{prefix}-tenor',
                         options=[{'label': t, 'value': t} for t in TENOR_OPTIONS], value='3m',
                         clearable=False, style={**SELECT_SMALL_STYLE, 'width': '90px'})),
                _control('Strike space', dcc.Dropdown(id=f'{prefix}-space',
                         options=[{'label': 'Delta', 'value': 'delta'}], value='delta', clearable=False,
                         style={**SELECT_SMALL_STYLE, 'width': '165px'})),
                _control(html.Span('Delta (0–1)', id=f'{prefix}-input-label'),
                         dcc.Input(id=f'{prefix}-delta', type='number', value=.25, step='any',
                                   debounce=True, style={**SMALL_INPUT_STYLE, 'width': '110px'})),
            ], className='cdx-rv-fields'),
        ], className='cdx-rv-leg')

    rv_children = [
        html.Div([
            html.H6('Relative Vol Comparison', style=SECTION_HEADER),
            html.Div([rv_leg(1), rv_leg(2)], className='cdx-rv-legs'),
            html.Div(html.Button('Compare volatility', id=CDX_RV_SUBMIT_ID,
                     style={**ACTION_BUTTON_STYLE, 'backgroundColor': '#006d77', 'color': '#ffffff',
                            'fontSize': '12px', 'padding': '8px 16px'}), className='cdx-rv-actions'),
            html.Div('Select a product for Leg 1; Leg 2 is optional. Delta uses 0.25 for 25 delta.',
                     id='cdx-rv-feedback', role='status', **{'aria-live': 'polite'},
                     style={'fontSize': '12px', 'paddingTop': '10px', 'color': '#52657a'}),
        ], style=GRAPH_CARD_STYLE),
        html.Div([dcc.Loading(_graph('cdx-rv-chart'), type='circle', color='#006d77',
                             delay_show=200, target_components={'cdx-rv-chart': 'figure'},
                             overlay_style={'visibility': 'visible', 'opacity': .4})], style=GRAPH_CARD_STYLE),
    ]

    def _panel(panel_id, children, vis):
        return html.Div(id=panel_id, style=vis, children=children)

    tab_content = html.Div(id=CDX_OPTIONS_TAB_CONTENT_ID, className='cdx-tab-content', children=[
        _panel(TAB_PANEL_VOL_GRIDS, [_card(vg_children)], visible_style),
        _panel(TAB_PANEL_TRADE_BLOTTER, [_card(blotter_children)], hidden_style),
        _panel(TAB_PANEL_POSITIONS, [_card(pos_children)], hidden_style),
        _panel(TAB_PANEL_EXPIRY_CHARTS, [_card([
            html.Div([
                html.Span('Drag chart handles to arrange. Order is saved in this browser.'),
                html.Button('Reset order', id='cdx-chart-order-reset', style=BTN_STYLE),
                html.Span(id='cdx-chart-order-status', role='status', **{'aria-live': 'polite'}),
            ], className='cdx-chart-order-toolbar'),
            html.Div(exp_children, id='cdx-exp-chart-grid', style=GRAPH_GRID_STYLE),
        ])], hidden_style),
        _panel(TAB_PANEL_OPTION_STACK, [_card(os_children)], hidden_style),
        _panel(TAB_PANEL_RELATIVE_VOL, [_card(rv_children)], hidden_style),
    ])

    stores = html.Div([
        dcc.Location(id=CDX_OPTIONS_URL, refresh=False),
        dcc.Interval(id=CDX_OPTIONS_AUTOBOOT_INTERVAL, interval=500, max_intervals=1),
        dcc.Interval(id=CDX_OPTIONS_STATUS_TICK, interval=1000),
        dcc.Interval(id=CDX_OPTIONS_DD_DEBOUNCE, interval=350, disabled=True),
        html.Div(WebSocket(id=CDX_OPTIONS_WS, url='/cdx-options/ws'), id=CDX_OPTIONS_WS_CONTAINER),
        dcc.Store(id=CDX_OPTIONS_WS_RECOVERY, data={}),
        dcc.Store(id=CDX_OPTIONS_SESSION_STORE, storage_type='session'),
        dcc.Store(id=CDX_OPTIONS_WS_PROGRESS_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_LIGHT_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_ACTIVITY_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_COMPUTE_STATUS_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_SPOT_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_STATUS_INFO_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_TASKS_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_TAB_DATA_STORE),
        dcc.Store(id=CDX_OPTIONS_WS_DEFAULTS_STORE),
        dcc.Store(id=CDX_OPTIONS_OVERRIDE_STORE, data={}),
        dcc.Store(id=CDX_OPTIONS_TAB_STORE, data={'value': 'vol-grids', 'ts': 0}),
        dcc.Store(id=CDX_OPTIONS_DD_PENDING, data={}),
        dcc.Store(id=CDX_OPTIONS_DD_COMMITTED, data={}),
        dcc.Store(id=CDX_OPTIONS_TAB_CACHE, data={}),
        dcc.Store(id=CDX_OPTIONS_TAB_CACHE_TRIGGER, data={}),
        dcc.Store(id=CDX_OPTIONS_LOADING_STORE, data={}),
        dcc.Store(id=CDX_OPTIONS_LIFECYCLE, data={'phase': 'init'}),
        dcc.Store(id=CDX_OPTIONS_URL_SYNC, data={}),
        dcc.Store(id=CDX_OPTIONS_PRODUCT_MEMORY, data={}),
        dcc.Store(id=CDX_OPTIONS_EVENT_LOG_STORE, data=[]),
    ])

    status_bar = html.Div(id=CDX_OPTIONS_STATUS_BAR, style=STATUS_BAR_STYLE, children=[
        html.Div([
            html.Span('CDX OPTIONS TRADING > PROCESSING / ACTIVITY'),
            html.Span('▾', id='cdx-activity-toggle-icon', style={'fontSize': '12px'}),
        ], id='cdx-activity-toggle', style=STATUS_BAR_HEADER_STYLE),
        html.Div('Waiting for initialization...', id='cdx-activity-body',
                 style={**STATUS_BAR_BODY_STYLE, 'color': '#8b949e'}),
    ])

    static = html.Div([controls, tabs], id='cdx-page-chrome', className='cdx-dashboard', style=PAGE_CHROME_STYLE)
    dynamic = html.Div([
        override_row, tab_content, stores,
        html.Div(style={'height': '160px'}),
        status_bar,
    ], id='cdx-page-content', className='cdx-dashboard', style=PAGE_CONTENT_STYLE)

    return static, dynamic
