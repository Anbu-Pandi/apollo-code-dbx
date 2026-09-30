from typing import List, Optional, Tuple

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd

from dash_reports.fx_vol_tracker.config import (
    FX_CONFIG, OPTION_TENORS, SPREAD_PAIRS, FLY_COMBOS,
)
from dash_reports.fx_vol_tracker.data import fx_data_loader as loader
from dash_reports.fx_vol_tracker.data.signal_matrix import build_signal_matrix
from dash_reports.vol_tracker_common.base_config import DomainConfig, RVOL_WINDOWS
from dash_reports.vol_tracker_common.base_page import VolTrackerPage
from dash_reports.vol_tracker_common.styles import ACCENT_COLOR, STY, make_filter


SPREAD_OPTIONS = [{'label': f'{l}/{s}', 'value': f'{l}/{s}'} for s, l in SPREAD_PAIRS]
FLY_OPTIONS = [{'label': f'{s}/{m}/{l}', 'value': f'{s}/{m}/{l}'} for s, m, l in FLY_COMBOS]


class FXVolTrackerPage(VolTrackerPage):
    @property
    def config(self):
        return FX_CONFIG

    @property
    def option_tenor_id(self) -> str:
        return f'{self.prefix}_option_tenor'

    @property
    def spread_pair_id(self) -> str:
        return f'{self.prefix}_spread_pair'

    @property
    def fly_combo_id(self) -> str:
        return f'{self.prefix}_fly_combo'

    @property
    def select_all_id(self) -> str:
        return f'{self.prefix}_select_all'

    @property
    def clear_all_id(self) -> str:
        return f'{self.prefix}_clear_all'

    def get_entity_options(self) -> List[dict]:
        all_pairs = loader.get_available_ccy_pairs()
        return [{'label': c, 'value': c} for c in all_pairs]

    def get_default_entities(self) -> list:
        return loader.get_available_ccy_pairs()

    def build_entity_selector(self, options: list, default: list) -> html.Div:
        return html.Div([
            html.Span('CCY Pairs', style=STY['filter_label']),
            html.Div([
                html.Button('All', id=self.select_all_id, n_clicks=0,
                            style={'fontSize': '10px', 'padding': '2px 8px', 'marginRight': '4px',
                                   'border': f'1px solid {ACCENT_COLOR}', 'borderRadius': '3px',
                                   'backgroundColor': 'white', 'color': ACCENT_COLOR, 'cursor': 'pointer'}),
                html.Button('Clear', id=self.clear_all_id, n_clicks=0,
                            style={'fontSize': '10px', 'padding': '2px 8px',
                                   'border': '1px solid #999', 'borderRadius': '3px',
                                   'backgroundColor': 'white', 'color': '#666', 'cursor': 'pointer'}),
            ], style={'marginBottom': '4px'}),
            dcc.Checklist(
                id=self.entity_id,
                options=options,
                value=default,
                style={'display': 'grid', 'gridTemplateColumns': 'repeat(6, auto)', 'gap': '2px 14px',
                       'fontSize': '11px', 'maxHeight': '120px', 'overflowY': 'auto',
                       'padding': '6px', 'border': '1px solid #e2e6ea', 'borderRadius': '4px',
                       'backgroundColor': 'white'},
                inputStyle={'marginRight': '4px'},
            ),
        ], style={'flex': '1'})

    def build_extra_primary_filters(self) -> Optional[List[html.Div]]:
        return [
            make_filter('Option Tenors', dcc.Dropdown(
                id=self.option_tenor_id,
                options=[{'label': t, 'value': t} for t in OPTION_TENORS],
                value=OPTION_TENORS, multi=True, style={'width': '240px'}, clearable=False,
            )),
        ]

    def build_advanced_filters(self) -> Optional[html.Div]:
        return html.Div([
            make_filter('Realized Vol Window', dcc.Dropdown(
                id=self.rvol_window_id,
                options=[{'label': k, 'value': v} for k, v in RVOL_WINDOWS.items()],
                value=21, style={'width': '80px'}, clearable=False,
            )),
            make_filter('Vol Display', dcc.Dropdown(
                id=self.vol_unit_id,
                options=self.config.vol_unit_options,
                value='pct',
                style={'width': '140px'}, clearable=False,
            )),
            make_filter('Term Spread', dcc.Dropdown(
                id=self.spread_pair_id,
                options=SPREAD_OPTIONS, value=[o['value'] for o in SPREAD_OPTIONS],
                multi=True, style={'width': '340px'}, clearable=False,
            )),
            make_filter('Term Butterfly', dcc.Dropdown(
                id=self.fly_combo_id,
                options=FLY_OPTIONS, value=[o['value'] for o in FLY_OPTIONS],
                multi=True, style={'width': '340px'}, clearable=False,
            )),
        ], style={**STY['filter_row'], 'marginTop': '8px'})

    def load_entity_data(self, entity: str, asof_date, lookback: str) -> dict:
        return loader.get_cached_or_load(entity, asof_date, lookback)

    def build_signal_matrix_data(self, all_entity_data: dict, domain: DomainConfig) -> dict:
        return build_signal_matrix(all_entity_data, domain)

    def build_filters(self, rvol_window, vol_unit, option_tenors=None,
                      spread_pairs=None, fly_combos=None) -> dict:
        return {
            'option_tenors': option_tenors if isinstance(option_tenors, list) else [option_tenors] if option_tenors else OPTION_TENORS,
            'rvol_window': rvol_window or 21,
            'spread_pairs': spread_pairs if isinstance(spread_pairs, list) else [spread_pairs] if spread_pairs else [],
            'fly_combos': fly_combos if isinstance(fly_combos, list) else [fly_combos] if fly_combos else [],
            'vol_unit': vol_unit or 'pct',
        }

    def get_display_df(self, data: dict, key: str, filters: dict, vol_mult: float) -> pd.DataFrame:
        option_tenors = filters.get('option_tenors', OPTION_TENORS)
        rvol_window = filters.get('rvol_window', 21)
        spread_pairs = filters.get('spread_pairs', [])
        fly_combos = filters.get('fly_combos', [])

        if key == 'realized_vol':
            fx_spot = data.get('fx_spot', pd.DataFrame())
            raw = loader.calc_realized_vol(fx_spot, window=rvol_window) if not fx_spot.empty else pd.Series()
            if raw is not None and not raw.empty:
                return self.scale_df(pd.DataFrame({'realized_vol': raw}), key, vol_mult)
            return pd.DataFrame()
        if key in ('fx_spot', 'fx_fwd'):
            return self.scale_df(data.get(key, pd.DataFrame()), key, vol_mult)
        if key == 'fx_vol':
            return self.scale_df(self._filter_vol_cols(data.get(key, pd.DataFrame()), option_tenors), key, vol_mult)
        if key == 'vol_spreads':
            return self.scale_df(self._filter_by_substrings(data.get(key, pd.DataFrame()), spread_pairs), key, vol_mult)
        if key == 'vol_flies':
            return self.scale_df(self._filter_by_substrings(data.get(key, pd.DataFrame()), fly_combos), key, vol_mult)
        return self.scale_df(data.get(key, pd.DataFrame()), key, vol_mult)

    def _filter_vol_cols(self, df: pd.DataFrame, tenors: list) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        keep = [c for c in df.columns if c in tenors]
        return df[keep] if keep else pd.DataFrame()

    def _filter_by_substrings(self, df: pd.DataFrame, substrings: list) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        keep = [c for c in df.columns if any(s in c for s in substrings)]
        return df[keep] if keep else pd.DataFrame()

    def get_entity_col_width(self) -> str:
        return '80px'

    def build_xccy_items(self, matrix_data: dict, vol_mult: float, vol_label: str) -> List[Tuple[str, str]]:
        rv = matrix_data.get('xccy', {})
        return [
            ('Highest Vol', self._fmt_xccy_val(rv.get('highest_vol'), vol_mult, vol_label)),
            ('Lowest Vol', self._fmt_xccy_val(rv.get('lowest_vol'), vol_mult, vol_label)),
            ('Steepest Curve', rv.get('steepest_curve', 'N/A')),
            ('Flattest Curve', rv.get('flattest_curve', 'N/A')),
            ('Most Divergent', rv.get('most_divergent', 'N/A')),
        ]

    def _fmt_xccy_val(self, tup, vol_mult, vol_label):
        if not isinstance(tup, tuple):
            return str(tup) if tup else 'N/A'
        ccy, val, unit = tup
        if unit == '%':
            return f'{ccy} ({val * vol_mult:.2f}{vol_label})'
        return f'{ccy} ({val:.4f})'


_page = FXVolTrackerPage()

OUTPUT_DIV = _page.output_div
SUBMIT_ID = _page.submit_id
DATE_ID = _page.date_id
LOOKBACK_ID = _page.lookback_id
CCY_PAIR_ID = _page.entity_id
OPTION_TENOR_ID = _page.option_tenor_id
RVOL_WINDOW_ID = _page.rvol_window_id
SPREAD_PAIR_ID = _page.spread_pair_id
FLY_COMBO_ID = _page.fly_combo_id
VOL_UNIT_ID = _page.vol_unit_id
CCY_TABS_ID = _page.entity_tabs_id
PRODUCT_TABS_TYPE = _page.product_tabs_type
FOCUS_CHART_ID = _page.focus_chart_id
MATRIX_TABS_ID = _page.matrix_tabs_id
SELECT_ALL_ID = _page.select_all_id
CLEAR_ALL_ID = _page.clear_all_id


def get_fx_vol_headers(app=None):
    return _page.get_headers(app)


def create_fx_vol_layout():
    return _page.create_layout()


def build_focus_chart(ccy_pair, inst, ts, vol_mult=1.0, vol_label='%'):
    return _page.build_focus_chart(ccy_pair, inst, ts, vol_mult, vol_label)


def generate_result_tabs(asof_date, lookback, ccy_pairs, filters):
    return _page.generate_result_tabs(asof_date, lookback, ccy_pairs, filters)


page_instance = _page
