import logging
import numpy as np
import pandas as pd

from dash_reports.cdx_options.pages.formatting.colors import (
    GRAD_GREEN_RED, GRAD_RED_GREEN, GRAD_BLUE_RED, GRAD_WHITE_GREEN, GRAD_WHITE_RED,
    lerp_color, rgb_gradient, text_color_for_bg,
)

_GRAD_GREEN_RED = GRAD_GREEN_RED
_GRAD_RED_GREEN = GRAD_RED_GREEN
_GRAD_BLUE_RED = GRAD_BLUE_RED
_GRAD_WHITE_GREEN = GRAD_WHITE_GREEN
_GRAD_WHITE_RED = GRAD_WHITE_RED

lerp_colour = lerp_color
_lerp_colour = lerp_color
_rgb_gradient = rgb_gradient
text_colour_for_bg = text_color_for_bg
_text_colour_for_bg = text_color_for_bg


def _simplify_multiindex(df):
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [str(c[-1]) if isinstance(c, tuple) else str(c) for c in df.columns]
    if isinstance(df.index, pd.MultiIndex):
        df.index = [str(i) for i in df.index]
    elif hasattr(df.index, 'astype'):
        df.index = df.index.astype(str)
    return df


# ---------- per-grid number format specs ----------

_GRID_FORMATS = {
    'spot_ref':       {'specifier': '.3f'},
    'payer_prem':     {'specifier': '.2f'},
    'receiver_prem':  {'specifier': '.2f'},
    'vol_diff':       {'specifier': '+.1f'},
    'vol_diff_1b':    {'specifier': '+.1f'},
    'vol_diff_nb':    {'specifier': '+.1f'},
    'vols':           {'specifier': '.1f'},
    'bps_d':          {'specifier': '.2f'},
    'spread_vols':    {'specifier': '.1f'},
    'deltas':         {'specifier': '.0%'},
    'vegas':          {'specifier': '.1f'},
}

GRID_PALETTE = {
    'vol_diff': GRAD_RED_GREEN,
    'vols': GRAD_RED_GREEN,
    'bps_d': GRAD_RED_GREEN,
    'spread_vols': GRAD_RED_GREEN,
    'payer_prem': GRAD_RED_GREEN,
    'receiver_prem': GRAD_RED_GREEN,
    'deltas': GRAD_RED_GREEN,
    'vegas': GRAD_RED_GREEN,
    'spot_ref': GRAD_RED_GREEN,
}
_GRID_PALETTE = GRID_PALETTE


ROW_COLORED_GRIDS = {'spot_ref'}


def _drop_sparse_rows(df):
    if df.empty:
        return df
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        return df
    has_any_value = df[numeric_cols].notna().any(axis=1)
    return df[has_any_value]


def build_vol_grid_style(df, grid_key=None):
    if df.empty:
        logging.debug(f'build_vol_grid_style({grid_key}): empty df')
        return [], [], []

    df = _simplify_multiindex(df)
    df = df.loc[:, ~df.columns.duplicated()]
    df = _drop_sparse_rows(df)
    if df.empty:
        logging.debug(f'build_vol_grid_style({grid_key}): all rows sparse')
        return [], [], []
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    logging.debug(f'build_vol_grid_style({grid_key}): {len(df)} rows, numeric_cols={numeric_cols}')

    palette = _GRID_PALETTE.get(grid_key, _GRAD_RED_GREEN)
    color_by_row = grid_key in ROW_COLORED_GRIDS

    all_vals = df[numeric_cols].values.flatten()
    all_vals = all_vals[~np.isnan(all_vals)]
    global_vmin, global_vmax = (float(np.nanmin(all_vals)), float(np.nanmax(all_vals))) if len(all_vals) > 0 else (0, 1)

    style_conditions = []
    for row_idx, (_, row) in enumerate(df.iterrows()):
        if color_by_row:
            row_vals = row[numeric_cols].values.astype(float)
            row_vals = row_vals[~np.isnan(row_vals)]
            vmin, vmax = (float(np.nanmin(row_vals)), float(np.nanmax(row_vals))) if len(row_vals) > 0 else (0, 1)
        else:
            vmin, vmax = global_vmin, global_vmax

        for col in numeric_cols:
            val = row[col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if not np.isscalar(val) or pd.isna(val):
                continue
            bg = _rgb_gradient(val, vmin, vmax, palette)
            fg = _text_colour_for_bg(bg)
            style_conditions.append({
                'if': {'row_index': row_idx, 'column_id': col},
                'backgroundColor': bg,
                'color': fg,
            })

    logging.info(f'build_vol_grid_style({grid_key}): built {len(style_conditions)} style conditions')
    if style_conditions:
        sample = style_conditions[0]
        logging.debug(f'  sample style: {sample}')

    fmt = _GRID_FORMATS.get(grid_key, {'specifier': '.2f'})

    df_reset = df.reset_index()
    columns = []
    for c in df_reset.columns:
        if c in numeric_cols:
            columns.append({
                'name': str(c), 'id': str(c),
                'type': 'numeric',
                'format': fmt,
            })
        else:
            columns.append({'name': str(c), 'id': str(c)})
    logging.debug(f'build_vol_grid_style({grid_key}): column_ids=[{[c["id"] for c in columns]}]')

    records = df_reset.to_dict('records')
    return columns, records, style_conditions


def build_styles_from_data(data, grid_key=None, idx_col=None):
    """Build heatmap styles from data records (for dynamic recalculation after filtering)."""
    if not data:
        return []

    df = pd.DataFrame(data)
    if df.empty:
        return []

    df = _drop_sparse_rows(df)
    if df.empty:
        return []

    if idx_col and idx_col in df.columns:
        numeric_cols = [c for c in df.columns if c != idx_col]
    else:
        numeric_cols = df.columns.tolist()

    numeric_cols = [c for c in numeric_cols if df[c].dtype in [np.float64, np.int64, float, int]
                    or pd.api.types.is_numeric_dtype(df[c])]

    if not numeric_cols:
        return []

    palette = GRID_PALETTE.get(grid_key, GRAD_RED_GREEN)
    color_by_row = grid_key in ROW_COLORED_GRIDS

    all_vals = df[numeric_cols].values.flatten()
    all_vals = all_vals[~pd.isna(all_vals)]
    global_vmin, global_vmax = (float(np.nanmin(all_vals)), float(np.nanmax(all_vals))) if len(all_vals) > 0 else (0, 1)

    style_conditions = []
    for row_idx in range(len(df)):
        if color_by_row:
            row_vals = df.iloc[row_idx][numeric_cols].values.astype(float)
            row_vals = row_vals[~np.isnan(row_vals)]
            vmin, vmax = (float(np.nanmin(row_vals)), float(np.nanmax(row_vals))) if len(row_vals) > 0 else (0, 1)
        else:
            vmin, vmax = global_vmin, global_vmax

        for col in numeric_cols:
            val = df.iloc[row_idx][col]
            if pd.isna(val):
                continue
            bg = rgb_gradient(val, vmin, vmax, palette)
            fg = text_colour_for_bg(bg)
            style_conditions.append({
                'if': {'row_index': row_idx, 'column_id': str(col)},
                'backgroundColor': bg,
                'color': fg,
            })

    return style_conditions


def _extract_atm_columns(deltas_df):
    if deltas_df.empty:
        return {}
    df = _simplify_multiindex(deltas_df)
    df = df.loc[:, ~df.columns.duplicated()]
    df = _drop_sparse_rows(df)
    if df.empty:
        return {}
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    atm = {}
    for row_idx, (_, row) in enumerate(df.iterrows()):
        best_col, best_dist = None, float('inf')
        for col in numeric_cols:
            val = row[col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if not np.isscalar(val) or pd.isna(val):
                continue
            dist = abs(float(val) - 0.50)
            if dist < best_dist:
                best_dist, best_col = dist, col
        if best_col is not None:
            atm[row_idx] = best_col
    return atm


def extract_atm_columns_from_data(delta_data, idx_col=None):
    if not delta_data:
        return {}
    df = pd.DataFrame(delta_data)
    if df.empty:
        return {}
    df = _drop_sparse_rows(df)
    if df.empty:
        return {}
    if idx_col and idx_col in df.columns:
        numeric_cols = [c for c in df.columns if c != idx_col]
    else:
        numeric_cols = df.columns.tolist()
    numeric_cols = [c for c in numeric_cols if pd.api.types.is_numeric_dtype(df[c])]

    atm = {}
    for row_idx in range(len(df)):
        best_col, best_dist = None, float('inf')
        for col in numeric_cols:
            val = df.iloc[row_idx][col]
            if pd.isna(val):
                continue
            dist = abs(float(val) - 0.50)
            if dist < best_dist:
                best_dist, best_col = dist, str(col)
        if best_col is not None:
            atm[row_idx] = best_col
    return atm


def _apply_atm_highlight(style_conditions, atm_map):
    for row_idx, col in atm_map.items():
        style_conditions.append({
            'if': {'row_index': row_idx, 'column_id': col},
            'border': '2px solid #000000',
            'fontWeight': '700',
        })


def apply_atm_highlight(style_conditions, atm_map):
    _apply_atm_highlight(style_conditions, atm_map)


def build_all_vol_grids(plot_data, product_title, is_px=False):
    atm_map = _extract_atm_columns(plot_data.get('deltas', pd.DataFrame()))
    vol_lookback = plot_data.get('vol_lookback', '5b')

    grids = {}
    for key, label in [
        ('spot_ref', 'Spot Reference'),
        ('payer_prem', 'Payer Premium'),
        ('receiver_prem', 'Receiver Premium'),
        ('vol_diff_1b', 'Vol Change 1b (Ann Normal)'),
        ('vol_diff_nb', f'Vol Change {vol_lookback} (Ann Normal)'),
        ('vols', 'Current Vols (Ann Normal)'),
        ('bps_d', 'Vol (bps/day)'),
        ('spread_vols', 'Spread Vol %'),
        ('deltas', 'Delta'),
        ('vegas', 'Vega Ann (k/100mm)'),
    ]:
        df = plot_data.get(key, pd.DataFrame())
        cols, records, styles = build_vol_grid_style(df, grid_key=key)
        if key != 'spot_ref' and atm_map:
            _apply_atm_highlight(styles, atm_map)
        status = plot_data.get('spot_ref_status') if key == 'spot_ref' else None
        if key in ('vol_diff_1b', 'vol_diff_nb'):
            status = {'ok': bool(records), 'message': '' if records else
                      'No comparable historical volatility for the selected product/strikes/expiries',
                      'baseline_dates': plot_data.get('vol_change_dates', {}).get(key, [])}
        grid_title = product_title
        if status and status.get('source') == 'curve':
            grid_title = f'{product_title} · curve fallback'
        elif status and not status.get('ok'):
            grid_title = f'{product_title} · {status.get("message", "unavailable")}'
        elif status and status.get('baseline_dates'):
            grid_title = f'{product_title} · {label} · vs {", ".join(status["baseline_dates"])}'
        grids[key] = {
            'title': grid_title,
            'grid_label': label,
            'columns': cols,
            'data': records,
            'style': styles,
            'status': status,
        }
        logging.info(f'build_all_vol_grids: {key} -> {len(records)} rows, {len(styles)} styles')
    return grids
