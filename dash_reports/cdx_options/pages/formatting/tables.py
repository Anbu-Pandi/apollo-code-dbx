import logging
import numpy as np
import pandas as pd
from dash import dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Group

from dash_reports.cdx_options.pages.formatting.styles import (
    SUMMARY_HEADER_STYLE, SUMMARY_CELL_STYLE,
    SECTION_HEADER, GRID_HEADER, GRID_SUBTITLE,
    LIGHT_TABLE_CELL, LIGHT_TABLE_HEADER, LIGHT_TABLE_WRAP, INPUT_STYLE,
    OPTION_TABLE_CELL, OPTION_TABLE_HEADER, OPTION_TABLE_DATA,
    OPTION_TABLE_WRAP, OPTION_TABLE_FILTER, EMPTY_BANNER_STYLE,
)

FILTER_TOOLTIP = 'col:val for column filter (prod:NAIG, strike:>60, dir:long). | OR, & AND, ~regex. Ex: prod:NAIG&dir:long'

HEATMAP_COLS = [
    'option_premium_chg', 'vol_ann', 'live_vol_ann', 'vol_ann_mid_chg', 'vol_ann_tcost',
    'est_MVE', 'live_MVE', 'net_vega_ann', 'live_vega', 'intraday_tcost', 'live_pnl',
]

SIGNED_HEATMAP_COLS = [
    'option_premium_chg', 'vol_ann_mid_chg', 'vol_ann_tcost',
    'est_MVE', 'live_MVE', 'net_vega_ann', 'live_vega',
    'intraday_tcost', 'live_pnl',
]

MAGNITUDE_HEATMAP_COLS = [
    'estimated_notional', 'option_premium_bps', 'vol_ann', 'live_vol_ann',
]

LIVE_COLS = ['live_vol_ann', 'live_spot', 'live_delta', 'live_MVE', 'live_vega', 'live_pnl']

_WHITE_BLUE = [
    (0.0, (255, 255, 255)),
    (1.0, (91, 155, 213)),
]


from dash_reports.cdx_options.pages.formatting.colors import rgb_gradient, text_color_for_bg, GRAD_RED_GREEN


def _build_heatmap_styles(data, base_styles=None):
    styles = list(base_styles) if base_styles else []
    if not data:
        return styles
    df = pd.DataFrame(data)
    if df.empty:
        return styles

    for col in HEATMAP_COLS:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors='coerce')
        valid = series.dropna()
        if valid.empty:
            continue
        vmin = float(valid.quantile(0.05))
        vmax = float(valid.quantile(0.95))
        if abs(vmax - vmin) < 1e-9:
            vmin, vmax = float(valid.min()), float(valid.max())
        if abs(vmax - vmin) < 1e-9:
            vmin, vmax = vmin - 1, vmax + 1

        for idx, val in series.items():
            if pd.isna(val):
                continue
            bg = rgb_gradient(val, vmin, vmax, GRAD_RED_GREEN)
            if bg:
                styles.append({
                    'if': {'row_index': idx, 'column_id': col},
                    'backgroundColor': bg,
                    'color': text_color_for_bg(bg),
                })
    return styles


def _scale_bounds(series, symmetric=False):
    valid = pd.to_numeric(series, errors='coerce').dropna()
    if valid.empty:
        return None, None
    if symmetric:
        vmax = float(valid.abs().quantile(0.95))
        if vmax <= 0:
            vmax = float(valid.abs().max())
        if vmax <= 0:
            vmax = 1.0
        return -vmax, vmax
    vmin = float(valid.quantile(0.05))
    vmax = float(valid.quantile(0.95))
    if abs(vmax - vmin) < 1e-9:
        vmin, vmax = float(valid.min()), float(valid.max())
    if abs(vmax - vmin) < 1e-9:
        vmin, vmax = vmin - 1.0, vmax + 1.0
    return vmin, vmax


def build_trade_blotter_styles(data, base_styles=None):
    styles = list(base_styles) if base_styles else [
        {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafbfc'},
        {'if': {'state': 'active'},
         'backgroundColor': 'rgba(0,116,217,0.08)', 'border': '1px solid #74b9ff'},
        {'if': {'state': 'selected'},
         'backgroundColor': 'rgba(88,166,255,0.14)', 'border': '1px solid #58a6ff'},
        {'if': {'column_id': 'execution_timestamp'}, 'textAlign': 'left', 'color': '#6e7681'},
        {'if': {'column_id': 'product'},
         'textAlign': 'left', 'fontWeight': '700', 'color': '#0b57a4', 'backgroundColor': '#f4f8ff'},
        {'if': {'column_id': 'option'}, 'textAlign': 'left', 'fontWeight': '600'},
        {'if': {'column_id': 'spot'}, 'textAlign': 'left', 'color': '#8a6d1d'},
        {'if': {'column_id': 'live_spot'},
         'textAlign': 'left', 'fontWeight': '600', 'color': '#0b57a4', 'backgroundColor': '#eef6ff'},
        {'if': {'column_id': 'expiration_date'}, 'color': '#6e7681'},
        {'if': {'column_id': 'is_adj', 'filter_query': '{is_adj} = true'},
         'backgroundColor': '#fff6dd', 'color': '#8a5a00', 'fontWeight': '700'},
        {'if': {'column_id': 'dlr_dir', 'filter_query': '{dlr_dir} = "long"'},
         'backgroundColor': '#edf8f0', 'color': '#1b7a3d', 'fontWeight': '700'},
        {'if': {'column_id': 'dlr_dir', 'filter_query': '{dlr_dir} = "short"'},
         'backgroundColor': '#fdeeee', 'color': '#c0392b', 'fontWeight': '700'},
    ]

    for col in LIVE_COLS:
        styles.append({
            'if': {'column_id': col},
            'backgroundColor': '#f5faff',
            'borderLeft': '1px solid #d6e9ff',
        })

    for col in ['live_pnl', 'est_MVE', 'live_MVE', 'intraday_tcost', 'option_premium_chg', 'vol_ann_mid_chg']:
        styles.extend([
            {'if': {'filter_query': f'{{{col}}} > 0', 'column_id': col},
             'color': '#1b7a3d', 'fontWeight': '700'},
            {'if': {'filter_query': f'{{{col}}} < 0', 'column_id': col},
             'color': '#c0392b', 'fontWeight': '700'},
        ])

    if not data:
        return styles
    df = pd.DataFrame(data)
    if df.empty:
        return styles

    for col in SIGNED_HEATMAP_COLS:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors='coerce')
        vmin, vmax = _scale_bounds(series, symmetric=True)
        if vmin is None:
            continue
        for idx, val in series.items():
            if pd.isna(val):
                continue
            bg = rgb_gradient(val, vmin, vmax, GRAD_RED_GREEN)
            if bg:
                styles.append({
                    'if': {'row_index': idx, 'column_id': col},
                    'backgroundColor': bg,
                    'color': text_color_for_bg(bg),
                })

    for col in MAGNITUDE_HEATMAP_COLS:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors='coerce')
        vmin, vmax = _scale_bounds(series, symmetric=False)
        if vmin is None:
            continue
        for idx, val in series.items():
            if pd.isna(val):
                continue
            bg = rgb_gradient(val, vmin, vmax, _WHITE_BLUE)
            if bg:
                styles.append({
                    'if': {'row_index': idx, 'column_id': col},
                    'backgroundColor': bg,
                    'color': text_color_for_bg(bg),
                })

    return styles
from dash_reports.cdx_options.pages.cdx_options import (
    CDX_RV_OPT1_PRODUCT_ID, CDX_RV_OPT1_TENOR_ID, CDX_RV_OPT1_DELTA_ID,
    CDX_RV_OPT2_PRODUCT_ID, CDX_RV_OPT2_TENOR_ID, CDX_RV_OPT2_DELTA_ID,
    CDX_RV_SUBMIT_ID,
)
from dash_reports.cdx_options.data.trade_blotter import MONEY_COLS, NUMERIC_COLS, PLAIN_COLS, PNL_COLS

_MONEY_FMT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes)
_COMMA_FMT = Format(precision=2, scheme=Scheme.fixed, group=Group.yes)
_NUM_FMT = Format(precision=2, scheme=Scheme.fixed)
_INT_FMT = Format(precision=0, scheme=Scheme.fixed)

TENOR_OPTIONS = ['1m', '2m', '3m', '6m', '9m', '12m']
DELTA_OPTIONS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def make_vol_grid_table(grid_info, table_id):
    if not grid_info.get('data'):
        return html.Div(f"No data for {grid_info.get('title', '')}", style={'padding': '10px'})

    cols = grid_info['columns']
    idx_col = cols[0]['id'] if cols else None
    cell_cond = []
    if idx_col:
        cell_cond.append({'if': {'column_id': idx_col}, 'width': '140px', 'minWidth': '140px',
                          'maxWidth': '140px', 'textAlign': 'left'})
    for c in cols[1:]:
        cell_cond.append({'if': {'column_id': c['id']}, 'width': '62px', 'minWidth': '62px',
                          'maxWidth': '62px'})

    grid_label = grid_info.get('grid_label', '')
    title = grid_info.get('title', '')
    header = html.Div([
        html.Span(grid_label, style=GRID_HEADER),
        html.Span(f'  {title}', style=GRID_SUBTITLE),
    ], style={'marginBottom': '3px', 'lineHeight': '1.3'})

    styles = grid_info.get('style', [])
    if not styles:
        logging.warning(f'make_vol_grid_table({table_id}): no styles received!')
    return html.Div([
        header,
        dash_table.DataTable(
            id=table_id,
            columns=cols,
            data=grid_info['data'],
            style_data_conditional=styles,
            style_cell={**LIGHT_TABLE_CELL, 'padding': '4px'},
            style_cell_conditional=cell_cond,
            style_header={**LIGHT_TABLE_HEADER, 'fontSize': '11px'},
            style_table=LIGHT_TABLE_WRAP,
            style_filter={'fontSize': '10px', 'padding': '3px 5px', 'backgroundColor': '#f8fafd'},
            cell_selectable=True,
            sort_action='native',
            filter_action='native',
            page_size=50,
            tooltip_header={c['id']: FILTER_TOOLTIP for c in cols},
            tooltip_delay=500,
            tooltip_duration=None,
        ),
        html.Br()
    ])


def _rebuild_trade_columns(server_columns):
    columns = []
    for col in server_columns:
        c = {'name': col['name'], 'id': col['id']}
        cid = col['id']
        if cid in MONEY_COLS:
            c.update(type='numeric', format=_MONEY_FMT)
        elif cid in PLAIN_COLS:
            c.update(type='numeric', format=_NUM_FMT)
        elif cid in NUMERIC_COLS:
            c.update(type='numeric', format=_COMMA_FMT)
        columns.append(c)
    return columns


def make_trade_blotter_table(blotter_info, table_id, include_filter=True):
    title = blotter_info.get('title', '')
    has_data = bool(blotter_info.get('data'))

    children = [
        html.H6(title, style=SECTION_HEADER),
    ]

    if include_filter:
        filter_input = html.Div([
            dcc.Input(
                id=f'{table_id}-adv-filter',
                type='text',
                placeholder='Filter: | OR, & AND, ~regex, ><=  (e.g. CDX|iTraxx, long&>50, >60&<=100)',
                debounce=True,
                style={**INPUT_STYLE, 'width': '100%', 'fontFamily': 'Consolas, monospace'},
            ),
        ], style={'marginBottom': '6px'})
        children.append(filter_input)

    children.append(dcc.Store(id=f'{table_id}-full-data', data=blotter_info.get('data', [])))
    children.append(dcc.Store(id=f'{table_id}-columns', data=blotter_info.get('columns', [])))
    children.append(html.Div(id=f'{table_id}-summary', style={'marginBottom': '6px'}))

    if has_data:
        columns = _rebuild_trade_columns(blotter_info['columns'])
        heatmap_styles = build_trade_blotter_styles(blotter_info['data'])

        children.append(dash_table.DataTable(
            id=table_id,
            columns=columns,
            data=blotter_info['data'],
            style_cell={**LIGHT_TABLE_CELL, 'minWidth': '72px', 'maxWidth': '140px'},
            style_cell_conditional=[
                {'if': {'column_id': 'execution_timestamp'}, 'textAlign': 'left', 'minWidth': '120px'},
                {'if': {'column_id': 'product'}, 'textAlign': 'left'},
                {'if': {'column_id': 'option'}, 'textAlign': 'left'},
                {'if': {'column_id': 'dlr_dir'}, 'fontWeight': '600'},
                {'if': {'column_id': 'spot'}, 'textAlign': 'left'},
                {'if': {'column_id': 'fwd'}, 'textAlign': 'left'},
            ],
            style_data_conditional=heatmap_styles,
            style_header={**LIGHT_TABLE_HEADER, 'textAlign': 'center'},
            style_data={'backgroundColor': '#ffffff', 'color': '#172033'},
            style_table=LIGHT_TABLE_WRAP,
            sort_action='native',
            filter_action='native',
            page_size=10,
        ))
    else:
        children.append(dash_table.DataTable(id=table_id, columns=[], data=[]))
        children.append(html.Div(f'No trades for {title}',
                                 style=EMPTY_BANNER_STYLE))

    children.append(html.Div(style={'height': '10px'}))
    return html.Div(children)


def make_option_stack_table(ladder_df, product, expiry=None, strike=None):
    fmt2 = Format(precision=2, scheme=Scheme.fixed)
    fmt1 = Format(precision=1, scheme=Scheme.fixed)
    fmt0 = Format(precision=0, scheme=Scheme.fixed)

    bid_side = ['B Time', 'B#', 'B Dlr', 'B Prem', 'B Vol', 'B Vol Ann']
    ask_side = ['A Vol Ann', 'A Vol', 'A Prem', 'A Dlr', 'A#', 'A Time']

    col_cfg = {
        'B Time':    {'w': '52px', 'fmt': None, 'name': 'Time'},
        'B#':        {'w': '30px', 'fmt': fmt0, 'name': '#'},
        'B Dlr':     {'w': '90px', 'fmt': None, 'name': 'Dealer'},
        'B Prem':    {'w': '110px', 'fmt': None, 'name': 'Pay | Rec'},
        'B Vol':     {'w': '54px', 'fmt': fmt1, 'name': 'Vol'},
        'B Vol Ann': {'w': '60px', 'fmt': fmt1, 'name': 'Vol Ann'},
        'A Vol Ann': {'w': '60px', 'fmt': fmt1, 'name': 'Vol Ann'},
        'A Vol':     {'w': '54px', 'fmt': fmt1, 'name': 'Vol'},
        'A Prem':    {'w': '110px', 'fmt': None, 'name': 'Pay | Rec'},
        'A Dlr':     {'w': '90px', 'fmt': None, 'name': 'Dealer'},
        'A#':        {'w': '30px', 'fmt': fmt0, 'name': '#'},
        'A Time':    {'w': '52px', 'fmt': None, 'name': 'Time'},
    }
    cols = []
    for c in ladder_df.columns:
        cfg = col_cfg.get(c, {'w': '60px', 'fmt': None, 'name': c})
        col = {'name': cfg['name'], 'id': c}
        if cfg['fmt']:
            col.update(type='numeric', format=cfg['fmt'])
        cols.append(col)

    styles = [
        {'if': {'column_id': bid_side}, 'backgroundColor': '#0a1929', 'color': '#6cb4ee'},
        {'if': {'column_id': 'B Vol'}, 'fontWeight': '700', 'color': '#79c0ff'},
        {'if': {'column_id': 'B Vol Ann'}, 'fontWeight': '600', 'color': '#8bb8e0',
         'borderRight': '2px solid #30363d'},
        {'if': {'column_id': 'B Dlr'}, 'fontWeight': '600', 'textAlign': 'left'},
        {'if': {'column_id': 'B Prem'}, 'fontSize': '11px', 'color': '#8bb8e0'},
        {'if': {'column_id': 'B Time'}, 'color': '#4a7fba', 'fontSize': '11px'},
        {'if': {'column_id': 'B#'}, 'color': '#4a7fba', 'fontSize': '11px'},

        {'if': {'column_id': ask_side}, 'backgroundColor': '#1f1a00', 'color': '#d4a843'},
        {'if': {'column_id': 'A Vol Ann'}, 'fontWeight': '600', 'color': '#c9a84a',
         'borderLeft': '2px solid #30363d'},
        {'if': {'column_id': 'A Vol'}, 'fontWeight': '700', 'color': '#e3b341'},
        {'if': {'column_id': 'A Dlr'}, 'fontWeight': '600', 'textAlign': 'left'},
        {'if': {'column_id': 'A Prem'}, 'fontSize': '11px', 'color': '#c9a84a'},
        {'if': {'column_id': 'A Time'}, 'color': '#a08930', 'fontSize': '11px'},
        {'if': {'column_id': 'A#'}, 'color': '#a08930', 'fontSize': '11px'},

        {'if': {'row_index': 0, 'column_id': 'B Vol'}, 'backgroundColor': '#0d2847',
         'color': '#a5d6ff', 'fontWeight': '800'},
        {'if': {'row_index': 0, 'column_id': 'B Dlr'}, 'color': '#f0f6fc', 'fontWeight': '800'},
        {'if': {'row_index': 0, 'column_id': 'A Vol'}, 'backgroundColor': '#332d00',
         'color': '#f0d959', 'fontWeight': '800'},
        {'if': {'row_index': 0, 'column_id': 'A Dlr'}, 'color': '#f0f6fc', 'fontWeight': '800'},
    ]

    cell_styles = [{'if': {'column_id': c}, 'width': col_cfg.get(c, {}).get('w', '60px'),
                     'minWidth': col_cfg.get(c, {}).get('w', '60px'),
                     'maxWidth': '120px'} for c in ladder_df.columns]

    expiry_str = pd.to_datetime(expiry).strftime('%Y-%m-%d') if expiry else None
    table_id = 'cdx-option-ladder'
    title = f'Option Stack: {product}'
    if expiry_str:
        title += f' | {expiry_str}'
    if strike:
        title += f' | K={strike}'

    best_bvol = ladder_df['B Vol'].max() if 'B Vol' in ladder_df.columns else None
    best_avol = ladder_df['A Vol'].min() if 'A Vol' in ladder_df.columns else None
    has_both = best_bvol is not None and best_avol is not None and pd.notna(best_bvol) and pd.notna(best_avol)
    mid_vol = ((best_bvol + best_avol) / 2) if has_both else None
    vol_spread = (best_avol - best_bvol) if has_both else None
    n_bid = ladder_df['B Dlr'].nunique() if 'B Dlr' in ladder_df.columns else 0
    n_ask = ladder_df['A Dlr'].nunique() if 'A Dlr' in ladder_df.columns else 0

    summary_parts = []
    if best_bvol is not None:
        summary_parts.append(f'Bid {best_bvol:.1f}')
    if mid_vol is not None:
        summary_parts.append(f'Mid {mid_vol:.1f}')
    if best_avol is not None:
        summary_parts.append(f'Ask {best_avol:.1f}')
    if vol_spread is not None:
        summary_parts.append(f'Spd {vol_spread:.1f}')
    summary_parts.append(f'{n_bid}\u00d7{n_ask} dlrs')
    subtitle = ' · '.join(summary_parts)

    return html.Div([
        html.Div([
            html.Span(title, style=GRID_HEADER),
            html.Span(f'  {subtitle}', style=GRID_SUBTITLE),
        ], style={'marginBottom': '3px', 'lineHeight': '1.3'}),
        dash_table.DataTable(
            id=table_id,
            columns=cols,
            data=ladder_df.to_dict('records'),
            style_data_conditional=styles,
            style_cell=OPTION_TABLE_CELL,
            style_cell_conditional=cell_styles,
            style_header=OPTION_TABLE_HEADER,
            style_header_conditional=[
                {'if': {'column_id': bid_side}, 'color': '#58a6ff', 'borderBottom': '2px solid #1f6feb'},
                {'if': {'column_id': ask_side}, 'color': '#d4a843', 'borderBottom': '2px solid #c69026'},
            ],
            style_data=OPTION_TABLE_DATA,
            style_table=OPTION_TABLE_WRAP,
            style_filter=OPTION_TABLE_FILTER,
            sort_action='native',
            filter_action='native',
            page_size=50,
            tooltip_header={c['id']: FILTER_TOOLTIP for c in cols},
            tooltip_delay=500,
        ),
    ])


def make_summary_table(columns, data):
    return dash_table.DataTable(
        columns=columns,
        data=data,
        style_header=SUMMARY_HEADER_STYLE,
        style_cell=SUMMARY_CELL_STYLE,
        style_data={'backgroundColor': '#f8f9fa', 'color': '#222'},
        style_data_conditional=[
            {'if': {'filter_query': '{Live Pnl} > 0', 'column_id': 'Live Pnl'},
             'color': '#1b7a3d', 'fontWeight': '700'},
            {'if': {'filter_query': '{Live Pnl} < 0', 'column_id': 'Live Pnl'},
             'color': '#c0392b', 'fontWeight': '700'},
            {'if': {'filter_query': '{Est Mve} > 0', 'column_id': 'Est Mve'},
             'color': '#1b7a3d', 'fontWeight': '700'},
            {'if': {'filter_query': '{Est Mve} < 0', 'column_id': 'Est Mve'},
             'color': '#c0392b', 'fontWeight': '700'},
            {'if': {'filter_query': '{Live Mve} > 0', 'column_id': 'Live Mve'},
             'color': '#1b7a3d', 'fontWeight': '700'},
            {'if': {'filter_query': '{Live Mve} < 0', 'column_id': 'Live Mve'},
             'color': '#c0392b', 'fontWeight': '700'},
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#eef0f2'},
        ],
        style_table={'overflowX': 'auto', 'marginBottom': '4px',
                     'border': '1px solid #ccc', 'borderRadius': '4px'},
        sort_action='native',
        filter_action='native',
        page_size=50,
    )


def make_rv_controls(product_options=None):
    product_options = product_options or []
    default_product = product_options[0]['value'] if product_options else None
    return html.Div([
        html.H6('Relative Vol Comparison', style=SECTION_HEADER),
        html.Div([
            html.Div([
                html.Label('Opt 1 Product'),
                dcc.Dropdown(id=CDX_RV_OPT1_PRODUCT_ID, options=product_options, value=default_product, style={'width': '200px'}),
            ], style={'marginRight': '12px'}),
            html.Div([
                html.Label('Tenor'),
                dcc.Dropdown(id=CDX_RV_OPT1_TENOR_ID,
                             options=[{'label': t, 'value': t} for t in TENOR_OPTIONS],
                             value='3m', style={'width': '100px'}),
            ], style={'marginRight': '12px'}),
            html.Div([
                html.Label('Delta'),
                dcc.Dropdown(id=CDX_RV_OPT1_DELTA_ID,
                             options=[{'label': str(d), 'value': d} for d in DELTA_OPTIONS],
                             value=0.25, style={'width': '100px'}),
            ], style={'marginRight': '30px'}),
            html.Div([
                html.Label('Opt 2 Product'),
                dcc.Dropdown(id=CDX_RV_OPT2_PRODUCT_ID, options=product_options, value=None, style={'width': '200px'}),
            ], style={'marginRight': '12px'}),
            html.Div([
                html.Label('Tenor'),
                dcc.Dropdown(id=CDX_RV_OPT2_TENOR_ID,
                             options=[{'label': t, 'value': t} for t in TENOR_OPTIONS],
                             value='3m', style={'width': '100px'}),
            ], style={'marginRight': '12px'}),
            html.Div([
                html.Label('Delta'),
                dcc.Dropdown(id=CDX_RV_OPT2_DELTA_ID,
                             options=[{'label': str(d), 'value': d} for d in DELTA_OPTIONS],
                             value=0.25, style={'width': '100px'}),
            ], style={'marginRight': '12px'}),
            html.Div([
                html.Button('Compare', id=CDX_RV_SUBMIT_ID, className='submit-button', style={'marginTop': '18px'}),
            ]),
        ], style={'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'flex-end'}),
        html.Br(),
        dcc.Graph(id='cdx-rv-chart'),
    ])
