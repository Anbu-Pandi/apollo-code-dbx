from typing import List, Tuple

import pandas as pd

from dash_reports.cdx_vol_tracker.config import CDX_CONFIG, DEFAULT_TICKERS
from dash_reports.cdx_vol_tracker.data import cdx_data_loader as loader
from dash_reports.cdx_vol_tracker.data.signal_matrix import build_signal_matrix
from dash_reports.vol_tracker_common.base_config import DomainConfig
from dash_reports.vol_tracker_common.base_page import VolTrackerPage


class CDXVolTrackerPage(VolTrackerPage):
    @property
    def config(self):
        return CDX_CONFIG

    def get_entity_options(self) -> List[dict]:
        available = loader.get_available_tickers()
        return [{'label': t, 'value': t} for t in available]

    def get_default_entities(self) -> list:
        available = loader.get_available_tickers()
        return [t for t in DEFAULT_TICKERS if t in available] or available

    def load_entity_data(self, entity: str, asof_date, lookback: str) -> dict:
        return loader.get_cached_or_load(entity, asof_date, lookback)

    def build_signal_matrix_data(self, all_entity_data: dict, domain: DomainConfig) -> dict:
        return build_signal_matrix(all_entity_data, domain)

    def build_filters(self, rvol_window, vol_unit) -> dict:
        return {
            'rvol_window': rvol_window or 21,
            'vol_unit': vol_unit or 'bpd',
        }

    def get_display_df(self, data: dict, key: str, filters: dict, vol_mult: float) -> pd.DataFrame:
        rvol_window = filters.get('rvol_window', 21)
        if key == 'realized_vol':
            spread_df = data.get('cdx_spread', pd.DataFrame())
            return self.scale_df(loader.calc_realized_vol(spread_df, window=rvol_window), key, vol_mult)
        df = data.get(key, pd.DataFrame())
        return self.scale_df(self._strip_metadata(df), key, vol_mult)

    def _strip_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        metadata_cols = {'series', 'version'}
        drop = [c for c in df.columns if c in metadata_cols]
        return df.drop(columns=drop) if drop else df

    def get_entity_col_width(self) -> str:
        return '120px'

    def get_roll_dates(self, data: dict) -> list:
        return loader.detect_roll_dates(data.get('cdx_spread', pd.DataFrame()))

    def build_xccy_items(self, matrix_data: dict, vol_mult: float, vol_label: str) -> List[Tuple[str, str]]:
        rv = matrix_data.get('xccy', {})
        return [
            ('Widest Spread', self._fmt_xcross_val(rv.get('widest_spread'), vol_mult, vol_label)),
            ('Tightest Spread', self._fmt_xcross_val(rv.get('tightest_spread'), vol_mult, vol_label)),
            ('Highest Vol', self._fmt_xcross_val(rv.get('highest_vol'), vol_mult, vol_label)),
            ('Lowest Vol', self._fmt_xcross_val(rv.get('lowest_vol'), vol_mult, vol_label)),
            ('Most Divergent', rv.get('most_divergent', 'N/A')),
        ]

    def _fmt_xcross_val(self, tup, vol_mult, vol_label):
        if not isinstance(tup, tuple):
            return str(tup) if tup else 'N/A'
        ticker, val, unit = tup
        if unit == 'bpd':
            return f'{ticker} ({val * vol_mult:.1f} {vol_label})'
        return f'{ticker} ({val:.0f} {unit})'


_page = CDXVolTrackerPage()

OUTPUT_DIV = _page.output_div
SUBMIT_ID = _page.submit_id
DATE_ID = _page.date_id
LOOKBACK_ID = _page.lookback_id
TICKER_ID = _page.entity_id
RVOL_WINDOW_ID = _page.rvol_window_id
VOL_UNIT_ID = _page.vol_unit_id
TICKER_TABS_ID = _page.entity_tabs_id
PRODUCT_TABS_TYPE = _page.product_tabs_type
FOCUS_CHART_ID = _page.focus_chart_id
MATRIX_TABS_ID = _page.matrix_tabs_id


def get_cdx_vol_headers(app=None):
    return _page.get_headers(app)


def create_cdx_vol_layout():
    return _page.create_layout()


def build_focus_chart(ticker, inst, ts, vol_mult=1.0, vol_label='bpd', roll_dates=None):
    return _page.build_focus_chart(ticker, inst, ts, vol_mult, vol_label, roll_dates)


def generate_result_tabs(asof_date, lookback, tickers, filters):
    return _page.generate_result_tabs(asof_date, lookback, tickers, filters)


page_instance = _page
