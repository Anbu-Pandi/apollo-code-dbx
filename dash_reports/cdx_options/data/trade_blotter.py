import pandas as pd
import numpy as np
from dash.dash_table.Format import Format, Scheme, Group


TRADE_DISPLAY_COLS = [
    'execution_timestamp', 'product', 'option', 'dlr_dir',
    'estimated_notional', 'option_premium_bps', 'option_premium_chg',
    'vol_ann', 'live_vol_ann', 'vol_ann_mid_chg', 'vol_ann_tcost',
    'spot', 'live_spot',
    'delta', 'live_delta',
    'est_MVE', 'live_MVE',
    'net_vega_ann', 'live_vega',
    'intraday_tcost', 'live_pnl',
    'expiration_date', 'strike_price', 'is_adj'
]

NUMERIC_COLS = [
    'estimated_notional', 'option_premium_bps', 'option_premium_chg',
    'vol_ann', 'vol_ann_mid_chg', 'vol_ann_tcost',
    'intraday_tcost', 'live_pnl',
    'live_vol_ann', 'live_delta',
    'delta', 'est_MVE', 'live_MVE',
    'net_vega_ann', 'live_vega', 'strike_price',
]

MONEY_COLS = ['estimated_notional', 'intraday_tcost', 'live_pnl', 'est_MVE', 'live_MVE', 'net_vega_ann', 'live_vega']
PLAIN_COLS = ['option_premium_bps', 'option_premium_chg', 'strike_price']
PNL_COLS = ['live_pnl', 'est_MVE', 'live_MVE', 'intraday_tcost']

SUMMARY_AGG_COLS = {
    'estimated_notional': 'sum',
    'net_vega_ann': 'sum',
    'live_vega': 'sum',
    'est_MVE': 'sum',
    'live_MVE': 'sum',
    'intraday_tcost': 'sum',
    'live_pnl': 'sum',
}

_MONEY_FMT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes)
_COMMA_FMT = Format(precision=2, scheme=Scheme.fixed, group=Group.yes)
_PLAIN_FMT = Format(precision=2, scheme=Scheme.fixed)
_INT_FMT = Format(precision=0, scheme=Scheme.fixed)


def format_trade_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    keep = list(TRADE_DISPLAY_COLS) + ['live_px']
    available = [c for c in keep if c in df.columns]
    out = df[available].copy()

    if 'execution_timestamp' in out.columns:
        out = out.sort_values('execution_timestamp', ascending=False).reset_index(drop=True)
        out['execution_timestamp'] = pd.to_datetime(
            out['execution_timestamp'], format='mixed'
        ).dt.strftime('%Y-%m-%d %H:%M')

    if 'expiration_date' in out.columns:
        out['expiration_date'] = pd.to_datetime(
            out['expiration_date'], format='mixed'
        ).dt.strftime('%Y-%m-%d')

    for c in NUMERIC_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')

    if 'spot' in out.columns:
        def _reformat_spot(val):
            if not isinstance(val, str) or '/' not in val:
                return val
            parts = val.split('/')
            if len(parts) != 2:
                return val
            return f'{parts[1]} | {parts[0]}'
        out['spot'] = out['spot'].apply(_reformat_spot)

    if 'live_spot' in out.columns:
        px = pd.to_numeric(out.get('live_px', pd.Series(dtype=float)), errors='coerce')
        spd = pd.to_numeric(out['live_spot'], errors='coerce')
        out['live_spot'] = [
            ' | '.join(([f'{p:.2f}'] if pd.notnull(p) else []) +
                        ([f'{s:.2f}'] if pd.notnull(s) else []))
            for p, s in zip(px, spd)
        ]
    out.drop(columns=['live_px'], errors='ignore', inplace=True)

    if 'option' in out.columns:
        is_payer = out['option'].str.endswith('P')
        if 'delta' in out.columns:
            cd = pd.to_numeric(out['delta'], errors='coerce').abs()
            out['delta'] = np.where(is_payer, -cd, 1 - cd).round(4)
        if 'live_delta' in out.columns:
            cd = pd.to_numeric(out['live_delta'], errors='coerce').abs()
            out['live_delta'] = np.where(is_payer, -cd, 1 - cd).round(4)

    final = [c for c in TRADE_DISPLAY_COLS if c in out.columns]
    return out[final]


def build_trade_columns(df):
    if df.empty:
        return []
    cols = []
    for c in df.columns:
        col = {'name': _ui_col_name(c), 'id': c}
        if c in MONEY_COLS:
            col.update(type='numeric', format=_MONEY_FMT)
        elif c in PLAIN_COLS:
            col.update(type='numeric', format=_PLAIN_FMT)
        elif c in NUMERIC_COLS:
            col.update(type='numeric', format=_COMMA_FMT)
        cols.append(col)
    return cols


def build_trade_blotter_data(plot_data):
    result = {}
    for key, label in [
        ('intraday_trades_df', 'Intraday Trades'),
        ('priorday_trades_df', 'Prior Day Trades'),
        ('all_trades_wtd_df', 'WTD Trades (10d)')
    ]:
        raw = plot_data.get(key, pd.DataFrame())
        formatted = format_trade_df(raw)
        result[key] = {
            'title': label,
            'columns': build_trade_columns(formatted),
            'data': formatted.to_dict('records') if not formatted.empty else [],
        }
    return result


def build_blotter_summary(virtual_data):
    if not virtual_data:
        return pd.DataFrame()
    df = pd.DataFrame(virtual_data)
    for c in SUMMARY_AGG_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    if 'product' not in df.columns:
        return pd.DataFrame()

    agg = {c: fn for c, fn in SUMMARY_AGG_COLS.items() if c in df.columns}
    summary = df.groupby('product').agg(agg)
    summary['trades'] = df.groupby('product').size()
    summary = summary.reset_index()
    summary.columns = [_ui_col_name(c) for c in summary.columns]
    return summary


def build_summary_columns(summary_df):
    if summary_df.empty:
        return []
    cols = []
    money_ui = {_ui_col_name(c) for c in MONEY_COLS}
    plain_ui = {_ui_col_name(c) for c in PLAIN_COLS}
    numeric_ui = {_ui_col_name(c) for c in NUMERIC_COLS}
    for c in summary_df.columns:
        col = {'name': c, 'id': c}
        if c == 'Trades':
            col.update(type='numeric', format=_INT_FMT)
        elif c in money_ui:
            col.update(type='numeric', format=_MONEY_FMT)
        elif c in plain_ui:
            col.update(type='numeric', format=_PLAIN_FMT)
        elif c in numeric_ui:
            col.update(type='numeric', format=_COMMA_FMT)
        cols.append(col)
    return cols


def _ui_col_name(col):
    return col.replace('_', ' ').title()
