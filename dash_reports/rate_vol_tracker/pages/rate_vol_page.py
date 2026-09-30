from typing import List, Optional, Tuple

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd

from dash_reports.rate_vol_tracker.config import (
    RATE_CONFIG, CCYS, FWD_STARTS_WITH_SPOT, UNDERLYING_TENORS, SPREAD_PAIRS, FLY_COMBOS,
)
from dash_reports.rate_vol_tracker.data import vol_data_loader as loader
from dash_reports.rate_vol_tracker.data.signal_matrix import build_signal_matrix
from dash_reports.vol_tracker_common.base_config import DomainConfig, RVOL_WINDOWS
from dash_reports.vol_tracker_common.base_page import VolTrackerPage
from dash_reports.vol_tracker_common.styles import STY, make_filter


SPREAD_OPTIONS = [{'label': f'{s}/{l}', 'value': f'{l}/{s}'} for s, l in SPREAD_PAIRS]
FLY_OPTIONS = [{'label': f'{s}/{m}/{l}', 'value': f'{s}/{m}/{l}'} for s, m, l in FLY_COMBOS]


class RateVolTrackerPage(VolTrackerPage):
    @property
    def config(self):
        return RATE_CONFIG

    @property
    def fwd_term_id(self) -> str:
        return f'{self.prefix}_fwd_term'

    @property
    def und_tenor_id(self) -> str:
        return f'{self.prefix}_und_tenor'

    @property
    def spread_pair_id(self) -> str:
        return f'{self.prefix}_spread_pair'

    @property
    def fly_combo_id(self) -> str:
        return f'{self.prefix}_fly_combo'

    def get_entity_options(self) -> List[dict]:
        return [{'label': c, 'value': c} for c in CCYS]

    def get_default_entities(self) -> list:
        return CCYS

    def build_entity_selector(self, options: list, default: list) -> html.Div:
        return dcc.Dropdown(
            id=self.entity_id,
            options=options,
            value=default,
            multi=True,
            style={'width': '280px'},
            clearable=False,
        )

    def build_extra_primary_filters(self) -> Optional[List[html.Div]]:
        return [
            make_filter('Fwd Term', dcc.Dropdown(
                id=self.fwd_term_id,
                options=[{'label': t, 'value': t} for t in FWD_STARTS_WITH_SPOT],
                value=FWD_STARTS_WITH_SPOT, multi=True, style={'width': '280px'}, clearable=False,
            )),
            make_filter('Underlying', dcc.Dropdown(
                id=self.und_tenor_id,
                options=[{'label': t, 'value': t} for t in UNDERLYING_TENORS],
                value=UNDERLYING_TENORS, multi=True, style={'width': '240px'}, clearable=False,
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
                value='bpd',
                style={'width': '140px'}, clearable=False,
            )),
            make_filter('Spread', dcc.Dropdown(
                id=self.spread_pair_id,
                options=SPREAD_OPTIONS, value=[o['value'] for o in SPREAD_OPTIONS],
                multi=True, style={'width': '340px'}, clearable=False,
            )),
            make_filter('Butterfly', dcc.Dropdown(
                id=self.fly_combo_id,
                options=FLY_OPTIONS, value=[o['value'] for o in FLY_OPTIONS],
                multi=True, style={'width': '340px'}, clearable=False,
            )),
        ], style={**STY['filter_row'], 'marginTop': '8px'})

    def load_entity_data(self, entity: str, asof_date, lookback: str) -> dict:
        return loader.get_cached_or_load(entity, asof_date, lookback)

    def build_signal_matrix_data(self, all_entity_data: dict, domain: DomainConfig) -> dict:
        return build_signal_matrix(all_entity_data, domain)

    def build_filters(self, rvol_window, vol_unit, fwd_terms=None, und_tenors=None,
                      spread_pairs=None, fly_combos=None) -> dict:
        return {
            'fwd_terms': fwd_terms if isinstance(fwd_terms, list) else [fwd_terms] if fwd_terms else FWD_STARTS_WITH_SPOT,
            'und_tenors': und_tenors if isinstance(und_tenors, list) else [und_tenors] if und_tenors else UNDERLYING_TENORS,
            'rvol_window': rvol_window or 21,
            'spread_pairs': spread_pairs if isinstance(spread_pairs, list) else [spread_pairs] if spread_pairs else [],
            'fly_combos': fly_combos if isinstance(fly_combos, list) else [fly_combos] if fly_combos else [],
            'vol_unit': vol_unit or 'bpd',
        }

    def get_display_df(self, data: dict, key: str, filters: dict, vol_mult: float) -> pd.DataFrame:
        fwd_terms = filters.get('fwd_terms', FWD_STARTS_WITH_SPOT)
        und_tenors = filters.get('und_tenors', UNDERLYING_TENORS)
        rvol_window = filters.get('rvol_window', 21)
        spread_pairs = filters.get('spread_pairs', [])
        fly_combos = filters.get('fly_combos', [])

        if key == 'realized_vol':
            fwd_rates = data.get('fwd_rates', pd.DataFrame())
            raw = loader.calc_realized_vol(fwd_rates, window=rvol_window) if not fwd_rates.empty else pd.DataFrame()
            return self.scale_df(self._filter_cols(raw, fwd_terms, und_tenors), key, vol_mult)
        if key in ('fwd_rates', 'swaption_vols'):
            return self.scale_df(self._filter_cols(data.get(key, pd.DataFrame()), fwd_terms, und_tenors), key, vol_mult)
        if key in ('fwd_spreads', 'vol_spreads'):
            return self.scale_df(self._filter_by_substrings(data.get(key, pd.DataFrame()), spread_pairs), key, vol_mult)
        if key in ('fwd_flies', 'vol_flies'):
            return self.scale_df(self._filter_by_substrings(data.get(key, pd.DataFrame()), fly_combos), key, vol_mult)
        return self.scale_df(data.get(key, pd.DataFrame()), key, vol_mult)

    def _filter_cols(self, df: pd.DataFrame, fwd_terms: list, und_tenors: list) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        keep = [c for c in df.columns
                if any(c.startswith(f'{ft}_') for ft in fwd_terms)
                and any(c.endswith(f'_{ut}') for ut in und_tenors)]
        return df[keep] if keep else pd.DataFrame()

    def _filter_by_substrings(self, df: pd.DataFrame, substrings: list) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        keep = [c for c in df.columns if any(s in c for s in substrings)]
        return df[keep] if keep else pd.DataFrame()

    def get_entity_col_width(self) -> str:
        return '60px'

    def build_xccy_items(self, matrix_data: dict, vol_mult: float, vol_label: str) -> List[Tuple[str, str]]:
        rv = matrix_data.get('xccy', {})
        return [
            ('Widest 2s10s', self._fmt_xccy(rv.get('widest_2s10s'), vol_mult, vol_label)),
            ('Tightest 2s10s', self._fmt_xccy(rv.get('tightest_2s10s'), vol_mult, vol_label)),
            ('Cheapest Belly', rv.get('cheapest_belly', 'N/A')),
            ('Richest Belly', rv.get('richest_belly', 'N/A')),
            ('Most Divergent', rv.get('most_divergent', 'N/A')),
        ]

    def _fmt_xccy(self, tup, vol_mult, vol_label):
        if not isinstance(tup, tuple):
            return str(tup) if tup else 'N/A'
        ccy, val, unit = tup
        if unit == 'bpd':
            return f'{ccy} ({val * vol_mult:+.1f} {vol_label})'
        return f'{ccy} ({val:+.0f}bp)'


_page = RateVolTrackerPage()

OUTPUT_DIV = _page.output_div
SUBMIT_ID = _page.submit_id
DATE_ID = _page.date_id
LOOKBACK_ID = _page.lookback_id
CCY_ID = _page.entity_id
FWD_TERM_ID = _page.fwd_term_id
UND_TENOR_ID = _page.und_tenor_id
RVOL_WINDOW_ID = _page.rvol_window_id
SPREAD_PAIR_ID = _page.spread_pair_id
FLY_COMBO_ID = _page.fly_combo_id
VOL_UNIT_ID = _page.vol_unit_id
CCY_TABS_ID = _page.entity_tabs_id
PRODUCT_TABS_TYPE = _page.product_tabs_type
FOCUS_CHART_ID = _page.focus_chart_id
MATRIX_TABS_ID = _page.matrix_tabs_id


def get_rate_vol_headers(app=None):
    return _page.get_headers(app)


def create_rate_vol_layout():
    return _page.create_layout()


def build_focus_chart(ccy, inst, ts, vol_mult=1.0, vol_label='bpd'):
    return _page.build_focus_chart(ccy, inst, ts, vol_mult, vol_label)


def generate_result_tabs(asof_date, lookback, ccys, filters):
    return _page.generate_result_tabs(asof_date, lookback, ccys, filters)


page_instance = _page
