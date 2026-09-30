import logging

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ALL, callback, ctx
from dash.exceptions import PreventUpdate
import dash_core_components as dcc
import dash_html_components as html

from dash_reports.vol_market_dashboard.config import (
    DASHBOARD_CONFIG, ASSET_BY_KEY, ASSET_CLASSES, MAX_SELECTIONS, COMPARISON_COLORS,
)
from dash_reports.vol_market_dashboard.pages.dashboard_page import (
    PREFIX, build_comparison_panel, build_asset_tabs,
    WRAPPING_TABS_STYLE, ACCENT_COLOR,
)
from dash_reports.vol_tracker_common.styles import (
    STY, TAB_BORDER, TAB_BORDER_THICK, SPACING_MD, SECTION_DIVIDER,
    MATRIX_TAB_STYLE, MATRIX_TAB_SELECTED,
    ENTITY_TAB_STYLE, ENTITY_TAB_SELECTED,
    PRODUCT_TAB_STYLE, PRODUCT_TAB_SELECTED,
    build_summary_box, make_ts_chart, make_recent_table, build_product_tab_content,
    build_grid_style, build_signal_matrix_html, build_xccy_row,
)
from dash_reports.vol_tracker_common.signal_matrix import conviction_opacity
from dash_reports.vol_tracker_common.base_config import ANNUALIZE

from dash_reports.cdx_vol_tracker.pages.cdx_vol_page import page_instance as cdx_page
from dash_reports.cdx_vol_tracker.data import cdx_data_loader as cdx_loader
from dash_reports.cdx_vol_tracker.data.signal_matrix import build_signal_matrix as cdx_build_matrix

from dash_reports.rate_vol_tracker.pages.rate_vol_page import page_instance as rate_page
from dash_reports.rate_vol_tracker.data import vol_data_loader as rate_loader
from dash_reports.rate_vol_tracker.data.signal_matrix import build_signal_matrix as rate_build_matrix

from dash_reports.fx_vol_tracker.pages.fx_vol_page import page_instance as fx_page
from dash_reports.fx_vol_tracker.data import fx_data_loader as fx_loader
from dash_reports.fx_vol_tracker.data.signal_matrix import build_signal_matrix as fx_build_matrix


ASSET_PAGES = {
    'cdx': (cdx_page, cdx_loader, cdx_build_matrix),
    'rate': (rate_page, rate_loader, rate_build_matrix),
    'fx': (fx_page, fx_loader, fx_build_matrix),
}


def _get_entities_for_asset(asset_key, loader):
    if asset_key == 'cdx':
        available = loader.get_available_tickers()
        return [t for t in ['CDX-NAHY', 'CDX-NAIG', 'ITRAXX-Europe', 'ITRAXX-Xover'] if t in available] or available
    if asset_key == 'rate':
        return ['USD', 'EUR', 'GBP', 'JPY']
    return loader.get_available_ccy_pairs()


def _collect_all_zscores(asof_date, lookback: str) -> list:
    all_zscores = []
    for asset_key, (page, loader, build_matrix_fn) in ASSET_PAGES.items():
        cfg = page.config
        asset_cfg = ASSET_BY_KEY.get(asset_key)
        entities = _get_entities_for_asset(asset_key, loader)
        all_entity_data = loader.get_cached_or_load_many(entities, asof_date, lookback)

        for domain in cfg.domains:
            matrix_data = build_matrix_fn(all_entity_data, domain)
            for entity, entity_cells in matrix_data['cells'].items():
                for inst_name, cell in entity_cells.items():
                    if cell is not None:
                        all_zscores.append({
                            'asset': asset_key,
                            'domain': domain.name,
                            'entity': entity,
                            'inst': inst_name,
                            'z_1m': round(cell['z_1m'], 3),
                            'abs_z': round(abs(cell['z_1m']), 3),
                            'regime': cell['regime'],
                            'regime_color': cell['regime_color'],
                            'color': asset_cfg.color if asset_cfg else '#8b949e',
                        })

    all_zscores.sort(key=lambda x: x['abs_z'], reverse=True)
    return all_zscores


def _pick_top_zscores(zscores: list, top_n: int) -> list:
    from collections import defaultdict
    buckets = defaultdict(list)
    for z in zscores:
        buckets[(z['asset'], z['domain'])].append(z)

    top = []
    round_idx = 0
    while len(top) < top_n:
        round_picks = [items[round_idx] for items in buckets.values() if round_idx < len(items)]
        if not round_picks:
            break
        round_picks.sort(key=lambda x: x['abs_z'], reverse=True)
        for pick in round_picks:
            top.append(pick)
            if len(top) >= top_n:
                break
        round_idx += 1
    return top


def _get_vol_params(vol_unit: str):
    scales = {'bpd': 1.0, 'bpyr': ANNUALIZE, 'pct': 1.0, 'pctd': 1.0 / ANNUALIZE}
    labels = {'bpd': 'bpd', 'bpyr': 'bp/yr', 'pct': '%', 'pctd': '% daily'}
    return scales.get(vol_unit, 1.0), labels.get(vol_unit, vol_unit)


def _build_signal_cell_div(cell: dict, asset_key: str, entity: str, inst: str,
                           is_selected: bool, is_most_confident: bool,
                           vol_mult: float, vol_label: str, page) -> html.Div:
    cell_id = {'type': 'vmd-signal-cell', 'asset': asset_key, 'entity': entity, 'inst': inst}

    if cell is None:
        return html.Div('\u2014', id=cell_id, n_clicks=0,
                        style={**STY['matrix_cell'], 'color': '#ccc', 'textAlign': 'center',
                               'border': '1px solid #e2e6ea'})

    opacity = conviction_opacity(cell['conviction'])
    bg = cell['regime_color']
    r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)

    border = '3px solid #d4a017' if is_selected else ('2px solid #d4a017' if is_most_confident else '1px solid #e2e6ea')
    shadow = '0 0 10px rgba(212,160,23,0.6)' if is_selected else ('0 0 8px rgba(212,160,23,0.4)' if is_most_confident else 'none')

    style = {
        **STY['matrix_cell'],
        'backgroundColor': f'rgba({r},{g},{b},{opacity})',
        'border': border,
        'boxShadow': shadow,
    }

    is_vol = cell['level_unit'] in ('bpd', '%')
    mult = vol_mult if is_vol else 1.0
    use_pct = cell.get('use_pct', False)
    lv = cell['level_display'] * mult

    from dash_reports.vol_tracker_common.styles import arrow, delta_color

    if use_pct:
        level_str = f'{lv:.4f}' if lv < 10 else f'{lv:.2f}'
        dm = cell['delta_1m'] * 100
        dw = cell['delta_1w'] * 100
        dm_str = f'1m: {arrow(dm)} {dm:+.2f}%'
        dw_str = f'1w: {arrow(dw)} {dw:+.2f}%'
    elif is_vol:
        dm = cell['delta_1m_bp'] * mult
        dw = cell['delta_1w_bp'] * mult
        level_str = f'{lv:.2f}{vol_label}' if '%' in vol_label else f'{lv:.1f} {vol_label}'
        dm_str = f'1m: {arrow(dm)} {dm:+.1f} {vol_label}'
        dw_str = f'1w: {arrow(dw)} {dw:+.1f} {vol_label}'
    else:
        dm = cell['delta_1m_bp']
        dw = cell['delta_1w_bp']
        unit = cell['level_unit'] or 'bp'
        level_str = f'{lv:.1f} {unit}'
        dm_str = f'1m: {arrow(dm)} {dm:+.1f} {unit}'
        dw_str = f'1w: {arrow(dw)} {dw:+.1f} {unit}'

    return html.Div([
        html.Div(level_str, style={'fontWeight': '700', 'fontSize': '13px', 'color': '#1a1a2e'}),
        html.Div(dm_str, style={'color': delta_color(dm), 'fontWeight': '600', 'marginTop': '2px'}),
        html.Div(dw_str, style={'color': delta_color(dw), 'marginTop': '1px'}),
        html.Div(cell['regime'], style={
            **STY['regime_tag'], 'backgroundColor': cell['regime_color'], 'color': 'white'}),
        html.Div(cell['watch'], style=STY['watch']),
    ], id=cell_id, n_clicks=0, style=style)


def _build_matrix_grid(matrix_data: dict, asset_key: str, page, selections: list,
                       vol_mult: float, vol_label: str, entity_col_width: str) -> html.Div:
    cells = matrix_data['cells']
    most_confident = matrix_data['most_confident']
    instruments = matrix_data['instruments']
    entities = list(cells.keys())

    selected_set = {(s['asset'], s['entity'], s['inst']) for s in selections}

    grid_children = [html.Div('')]
    grid_children += [html.Div(inst, style=STY['matrix_header']) for inst in instruments]

    for entity in entities:
        grid_children.append(html.Div(entity, style={**STY['entity_label'], 'fontSize': '11px'}))
        for inst in instruments:
            cell = cells[entity].get(inst)
            is_best = most_confident.get(inst) == entity
            is_selected = (asset_key, entity, inst) in selected_set
            grid_children.append(_build_signal_cell_div(
                cell, asset_key, entity, inst, is_selected, is_best,
                vol_mult, vol_label, page))

    grid = html.Div(grid_children, style=build_grid_style(len(instruments), entity_col_width))
    xccy_items = page.build_xccy_items(matrix_data, vol_mult, vol_label)
    xccy = build_xccy_row(xccy_items, entity_col_width) if xccy_items else html.Div()
    return build_signal_matrix_html(grid, xccy)


def _build_asset_content(asset_key: str, asof_date, lookback: str, vol_unit: str,
                         selections: list) -> html.Div:
    page, loader, build_matrix_fn = ASSET_PAGES[asset_key]
    cfg = page.config
    entities = _get_entities_for_asset(asset_key, loader)

    all_entity_data = loader.get_cached_or_load_many(entities, asof_date, lookback)

    vol_mult, vol_label = _get_vol_params(vol_unit)

    matrix_tabs = []
    for domain in cfg.domains:
        matrix_data = build_matrix_fn(all_entity_data, domain)
        vm = vol_mult if domain.name == 'vols' else 1.0
        vl = vol_label if domain.name == 'vols' else 'bp'
        if asset_key == 'fx' and domain.name == 'vols':
            vm, vl = 1.0, '%'

        matrix_tabs.append(dcc.Tab(
            label=domain.tab_label, value=domain.name,
            style=MATRIX_TAB_STYLE, selected_style=MATRIX_TAB_SELECTED,
            children=[
                build_summary_box(matrix_data),
                _build_matrix_grid(matrix_data, asset_key, page, selections,
                                   vm, vl, page.get_entity_col_width()),
            ],
        ))

    entity_tabs = []
    for entity in entities:
        data = all_entity_data[entity]
        roll_dates = page.get_roll_dates(data) if hasattr(page, 'get_roll_dates') else None
        filters = {'vol_unit': vol_unit, 'rvol_window': 21}

        if asset_key == 'rate':
            filters['fwd_terms'] = ['spot', '1m', '3m', '6m', '1y']
            filters['und_tenors'] = ['3m', '2y', '5y', '10y']
            filters['spread_pairs'] = ['10y/2y', '5y/2y', '10y/5y']
            filters['fly_combos'] = ['2y/5y/10y']
        elif asset_key == 'fx':
            filters['option_tenors'] = ['1m', '3m', '6m', '1y']
            filters['spread_pairs'] = ['3m/1m', '6m/1m', '1y/1m']
            filters['fly_combos'] = ['1m/3m/1y']

        product_tabs = []
        for key, label in cfg.product_tabs:
            display_df = page.get_display_df(data, key, filters, vol_mult)
            product_tabs.append(
                dcc.Tab(
                    label=label, value=key,
                    style=PRODUCT_TAB_STYLE, selected_style=PRODUCT_TAB_SELECTED,
                    children=[build_product_tab_content(
                        make_ts_chart(display_df, f'{entity} - {label}',
                                      yaxis_title=page.vol_yaxis(key, vol_label),
                                      roll_dates=roll_dates),
                        make_recent_table(display_df),
                    )],
                )
            )

        entity_tabs.append(dcc.Tab(
            label=entity, value=entity,
            style=ENTITY_TAB_STYLE, selected_style=ENTITY_TAB_SELECTED,
            children=[
                dcc.Tabs(product_tabs,
                         id={'type': f'{PREFIX}_{asset_key}_product_tabs', 'entity': entity},
                         value=cfg.default_product, style={'borderBottom': TAB_BORDER}),
            ]))

    return html.Div([
        dcc.Tabs(matrix_tabs, id=f'{PREFIX}_{asset_key}_matrix_tabs',
                 value=cfg.domains[0].name,
                 style={'borderBottom': TAB_BORDER, 'marginBottom': '8px'}),
        html.Div(style=SPACING_MD),
        html.Div(style=SECTION_DIVIDER),
        html.Div([
            dcc.Tabs(entity_tabs, id=f'{PREFIX}_{asset_key}_entity_tabs',
                     value=entities[0] if entities else None),
        ], style=WRAPPING_TABS_STYLE),
    ])


def _get_series_for_selection_from_cache(selection: dict, cached_data: dict, vol_mult: float):
    asset_key = selection['asset']
    entity = selection['entity']
    inst = selection['inst']

    cache_key = f'{asset_key}_{entity}_{inst}'
    if cache_key in cached_data:
        cached = cached_data[cache_key]
        return {
            'label': cached['label'],
            'ts': pd.Series(cached['values'], index=pd.to_datetime(cached['index'])),
            'unit': cached['unit'],
            'asset': asset_key,
        }
    return None


def _get_series_for_selection(selection: dict, asof_date, lookback: str, vol_mult: float, data=None):
    asset_key = selection['asset']
    entity = selection['entity']
    inst = selection['inst']

    page, loader, _ = ASSET_PAGES.get(asset_key, (None, None, None))
    if not page or not loader:
        return None

    cfg = page.config
    if inst not in cfg.all_instruments:
        return None

    data_key, col_name, inst_type = cfg.all_instruments[inst]
    if data is None:
        data = loader.get_cached_or_load(entity, asof_date, lookback)
    df = data.get(data_key)

    if df is None or col_name not in df.columns:
        return None

    ts = df[col_name].dropna()
    if ts.empty:
        return None

    domain = cfg.domain_by_data_key.get(data_key, cfg.domains[0])
    scale = domain.scale_for(inst_type)
    unit = domain.unit_for(inst_type)

    if domain.name == 'vols':
        scale *= vol_mult

    return {
        'label': f'{entity} {inst}',
        'ts': ts * scale,
        'unit': unit,
        'asset': asset_key,
    }


def _cache_series_data(selections: list, asof_date, lookback: str, vol_mult: float) -> dict:
    entities_by_asset = {}
    for sel in selections[:MAX_SELECTIONS]:
        entities = entities_by_asset.setdefault(sel['asset'], [])
        if sel['entity'] not in entities:
            entities.append(sel['entity'])

    prefetched = {}
    for asset_key, entities in entities_by_asset.items():
        loader = ASSET_PAGES[asset_key][1]
        loaded = loader.get_cached_or_load_many(entities, asof_date, lookback)
        for entity, data in loaded.items():
            prefetched[(asset_key, entity)] = data

    cached = {}
    for sel in selections[:MAX_SELECTIONS]:
        series = _get_series_for_selection(
            sel,
            asof_date,
            lookback,
            vol_mult,
            prefetched.get((sel['asset'], sel['entity'])),
        )
        if series and series['ts'] is not None:
            cache_key = f"{sel['asset']}_{sel['entity']}_{sel['inst']}"
            cached[cache_key] = {
                'label': series['label'],
                'values': series['ts'].tolist(),
                'index': [str(d) for d in series['ts'].index],
                'unit': series['unit'],
            }
    return cached


def _transform_series(ts, scale_mode: str):
    if ts is None or len(ts) == 0:
        return ts, ''

    first_val = ts.iloc[0]
    if first_val == 0:
        first_val = ts[ts != 0].iloc[0] if (ts != 0).any() else 1

    if scale_mode == 'norm100':
        return (ts / first_val) * 100, 'idx'
    elif scale_mode == 'pct_chg':
        return (ts / first_val - 1) * 100, '%'
    else:
        return ts, None


def _build_comparison_chart(series_data: list, scale_mode: str = 'raw') -> dcc.Graph:
    if not series_data:
        return html.Div()

    fig = go.Figure()

    if scale_mode in ('norm100', 'pct_chg'):
        yaxis_title = 'Index (start=100)' if scale_mode == 'norm100' else '% Change'

        for i, item in enumerate(series_data):
            ts = item.get('ts')
            if ts is None:
                continue

            label = item.get('label', f'Series {i+1}')
            color = COMPARISON_COLORS[i % len(COMPARISON_COLORS)]
            transformed_ts, _ = _transform_series(ts, scale_mode)

            fig.add_trace(go.Scatter(
                x=transformed_ts.index,
                y=transformed_ts.values,
                mode='lines',
                name=label,
                line=dict(width=2, color=color),
            ))

        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='white',
            font=dict(family='Helvetica, Arial, sans-serif', size=11, color='#3a3a3a'),
            hovermode='x unified',
            autosize=True,
            height=350,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                        font=dict(size=10), bgcolor='rgba(255,255,255,0.8)'),
            margin=dict(l=60, r=30, t=40, b=50),
            xaxis=dict(tickformat='%b %Y', hoverformat='%d %b %Y', tickangle=-45,
                       gridcolor='#eee', showline=True, linecolor='#ccc'),
            yaxis=dict(gridcolor='#eee', showline=True, linecolor='#ccc', title=yaxis_title),
        )

        return dcc.Graph(figure=fig, config={'displayModeBar': True, 'displaylogo': False},
                         style={'width': '100%'})

    n_series = len(series_data)

    left_count = (n_series + 1) // 2
    right_count = n_series // 2

    left_offset = 0.05 * left_count
    right_offset = 0.05 * right_count
    x_domain = [left_offset, 1.0 - right_offset]

    for i, item in enumerate(series_data):
        ts = item.get('ts')
        if ts is None:
            continue

        label = item.get('label', f'Series {i+1}')
        unit = item.get('unit', '')
        color = COMPARISON_COLORS[i % len(COMPARISON_COLORS)]
        axis_name = 'y' if i == 0 else f'y{i + 1}'

        fig.add_trace(go.Scatter(
            x=ts.index,
            y=ts.values,
            mode='lines',
            name=f'{label} ({unit})',
            line=dict(width=2, color=color),
            yaxis=axis_name,
        ))

    layout = dict(
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='white',
        font=dict(family='Helvetica, Arial, sans-serif', size=11, color='#3a3a3a'),
        hovermode='x unified',
        autosize=True,
        height=380,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                    font=dict(size=9), bgcolor='rgba(255,255,255,0.8)'),
        margin=dict(l=50, r=50, t=40, b=50),
        xaxis=dict(tickformat='%b %Y', hoverformat='%d %b %Y', tickangle=-45,
                   gridcolor='#eee', showline=True, linecolor='#ccc', domain=x_domain),
    )

    left_idx = 0
    right_idx = 0

    for i, item in enumerate(series_data):
        unit = item.get('unit', '')
        color = COMPARISON_COLORS[i % len(COMPARISON_COLORS)]
        axis_key = 'yaxis' if i == 0 else f'yaxis{i + 1}'

        if i % 2 == 0:
            side = 'left'
            pos = left_offset - 0.05 * (left_idx + 1) + 0.025
            left_idx += 1
        else:
            side = 'right'
            pos = 1.0 - right_offset + 0.05 * (right_idx + 1) - 0.025
            right_idx += 1

        axis_config = dict(
            title=dict(text=unit, font=dict(color=color, size=9)),
            tickfont=dict(color=color, size=8),
            showline=True,
            linecolor=color,
            side=side,
            anchor='free',
            position=pos,
        )

        if i == 0:
            axis_config['gridcolor'] = '#eee'
        else:
            axis_config['overlaying'] = 'y'
            axis_config['showgrid'] = False

        layout[axis_key] = axis_config

    fig.update_layout(**layout)

    return dcc.Graph(figure=fig, config={'displayModeBar': True, 'displaylogo': False},
                     style={'width': '100%'})


@callback(
    Output(f'{PREFIX}_output', 'children'),
    Output(f'{PREFIX}_cached_data', 'data'),
    Output(f'{PREFIX}_zscore_store', 'data'),
    Output(f'{PREFIX}_selections', 'data', allow_duplicate=True),
    Input(f'{PREFIX}_submit', 'n_clicks'),
    State(f'{PREFIX}_asof_date', 'date'),
    State(f'{PREFIX}_lookback', 'value'),
    State(f'{PREFIX}_vol_unit', 'value'),
    State(f'{PREFIX}_top_n_dropdown', 'value'),
    prevent_initial_call=True,
)
def on_submit(n_clicks, asof_date, lookback, vol_unit, top_n):
    if n_clicks is None:
        raise PreventUpdate

    asof_date = pd.to_datetime(asof_date).date()
    vol_unit = vol_unit or 'bpd'
    top_n = top_n or 5
    vol_mult, _ = _get_vol_params(vol_unit)

    all_zscores = _collect_all_zscores(asof_date, lookback)
    top_10 = _pick_top_zscores(all_zscores, 10)
    auto_sels_10 = [{'asset': z['asset'], 'entity': z['entity'], 'inst': z['inst']} for z in top_10]
    active_sels = auto_sels_10[:top_n]

    cached_data = _cache_series_data(auto_sels_10, asof_date, lookback, vol_mult)

    series_data = []
    for sel in active_sels:
        s = _get_series_for_selection_from_cache(sel, cached_data, vol_mult)
        if not s:
            s = _get_series_for_selection(sel, asof_date, lookback, vol_mult)
        if s:
            series_data.append(s)

    chart_div = _build_comparison_chart(series_data, 'raw')
    comparison_panel = build_comparison_panel(active_sels, chart_div, 'raw')

    cdx_content = _build_asset_content('cdx', asof_date, lookback, vol_unit, active_sels)
    rate_content = _build_asset_content('rate', asof_date, lookback, vol_unit, active_sels)
    fx_content = _build_asset_content('fx', asof_date, lookback, vol_unit, active_sels)

    asset_tabs = build_asset_tabs(cdx_content, rate_content, fx_content, active_asset='cdx')

    layout = html.Div([
        comparison_panel,
        html.Div(asset_tabs, id=f'{PREFIX}_asset_tabs_wrapper'),
    ])

    return layout, cached_data, all_zscores, active_sels


@callback(
    Output(f'{PREFIX}_selections', 'data'),
    Output(f'{PREFIX}_comparison_panel', 'children'),
    Output(f'{PREFIX}_cached_data', 'data', allow_duplicate=True),
    Input({'type': 'vmd-signal-cell', 'asset': ALL, 'entity': ALL, 'inst': ALL}, 'n_clicks'),
    State(f'{PREFIX}_selections', 'data'),
    State(f'{PREFIX}_cached_data', 'data'),
    State(f'{PREFIX}_asof_date', 'date'),
    State(f'{PREFIX}_lookback', 'value'),
    State(f'{PREFIX}_vol_unit', 'value'),
    State(f'{PREFIX}_scale_mode', 'value'),
    prevent_initial_call=True,
)
def on_cell_click(n_clicks_list, selections, cached_data, asof_date, lookback, vol_unit, scale_mode):
    if not ctx.triggered_id or all(not n for n in n_clicks_list):
        raise PreventUpdate

    triggered = ctx.triggered_id
    asset_key = triggered['asset']
    entity = triggered['entity']
    inst = triggered['inst']

    selections = selections or []
    cached_data = cached_data or {}
    scale_mode = scale_mode or 'raw'
    new_selection = {'asset': asset_key, 'entity': entity, 'inst': inst}

    existing_idx = None
    for i, sel in enumerate(selections):
        if sel['asset'] == asset_key and sel['entity'] == entity and sel['inst'] == inst:
            existing_idx = i
            break

    if existing_idx is not None:
        selections = selections[:existing_idx] + selections[existing_idx + 1:]
    else:
        if len(selections) >= MAX_SELECTIONS:
            logging.warning(f'Max selections ({MAX_SELECTIONS}) reached')
        else:
            selections = selections + [new_selection]

    asof_date = pd.to_datetime(asof_date).date()
    vol_unit = vol_unit or 'bpd'
    vol_mult, _ = _get_vol_params(vol_unit)

    series_data = []
    for sel in selections[:MAX_SELECTIONS]:
        s = _get_series_for_selection_from_cache(sel, cached_data, vol_mult)
        if not s:
            s = _get_series_for_selection(sel, asof_date, lookback, vol_mult)
            if s and s['ts'] is not None:
                cache_key = f"{sel['asset']}_{sel['entity']}_{sel['inst']}"
                cached_data[cache_key] = {
                    'label': s['label'],
                    'values': s['ts'].tolist(),
                    'index': [str(d) for d in s['ts'].index],
                    'unit': s['unit'],
                }
        if s:
            series_data.append(s)

    chart_div = _build_comparison_chart(series_data, scale_mode)
    panel_children = build_comparison_panel(selections, chart_div, scale_mode).children

    return selections, panel_children, cached_data


@callback(
    Output(f'{PREFIX}_selections', 'data', allow_duplicate=True),
    Output(f'{PREFIX}_comparison_panel', 'children', allow_duplicate=True),
    Input(f'{PREFIX}_clear_btn', 'n_clicks'),
    State(f'{PREFIX}_scale_mode', 'value'),
    prevent_initial_call=True,
)
def on_clear(n_clicks, scale_mode):
    if not n_clicks:
        raise PreventUpdate

    scale_mode = scale_mode or 'raw'
    panel_children = build_comparison_panel([], html.Div(), scale_mode).children

    return [], panel_children


@callback(
    Output(f'{PREFIX}_selections', 'data', allow_duplicate=True),
    Output(f'{PREFIX}_comparison_panel', 'children', allow_duplicate=True),
    Input(f'{PREFIX}_top_n_dropdown', 'value'),
    State(f'{PREFIX}_zscore_store', 'data'),
    State(f'{PREFIX}_cached_data', 'data'),
    State(f'{PREFIX}_vol_unit', 'value'),
    State(f'{PREFIX}_scale_mode', 'value'),
    prevent_initial_call=True,
)
def on_top_n_change(top_n, zscore_data, cached_data, vol_unit, scale_mode):
    if not zscore_data:
        raise PreventUpdate

    top_n = top_n or 5
    scale_mode = scale_mode or 'raw'
    vol_mult, _ = _get_vol_params(vol_unit or 'bpd')
    cached_data = cached_data or {}

    top_picks = _pick_top_zscores(zscore_data, top_n)
    new_sels = [{'asset': z['asset'], 'entity': z['entity'], 'inst': z['inst']} for z in top_picks]

    series_data = []
    for sel in new_sels:
        s = _get_series_for_selection_from_cache(sel, cached_data, vol_mult)
        if s:
            series_data.append(s)

    chart_div = _build_comparison_chart(series_data, scale_mode)
    panel_children = build_comparison_panel(new_sels, chart_div, scale_mode).children

    return new_sels, panel_children


@callback(
    Output(f'{PREFIX}_comparison_panel', 'children', allow_duplicate=True),
    Input(f'{PREFIX}_scale_mode', 'value'),
    State(f'{PREFIX}_selections', 'data'),
    State(f'{PREFIX}_cached_data', 'data'),
    State(f'{PREFIX}_asof_date', 'date'),
    State(f'{PREFIX}_lookback', 'value'),
    State(f'{PREFIX}_vol_unit', 'value'),
    prevent_initial_call=True,
)
def on_scale_change(scale_mode, selections, cached_data, asof_date, lookback, vol_unit):
    scale_mode = scale_mode or 'raw'
    selections = selections or []
    cached_data = cached_data or {}

    if not asof_date:
        raise PreventUpdate

    asof_date = pd.to_datetime(asof_date).date()
    vol_unit = vol_unit or 'bpd'
    vol_mult, _ = _get_vol_params(vol_unit)

    series_data = []
    for sel in selections[:MAX_SELECTIONS]:
        s = _get_series_for_selection_from_cache(sel, cached_data, vol_mult)
        if not s:
            s = _get_series_for_selection(sel, asof_date, lookback, vol_mult)
        if s:
            series_data.append(s)

    chart_div = _build_comparison_chart(series_data, scale_mode)
    panel_children = build_comparison_panel(selections, chart_div, scale_mode).children

    return panel_children
