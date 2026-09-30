from itertools import combinations

from dash_reports.vol_tracker_common.base_config import (
    DomainConfig, VolTrackerConfig, RVOL_WINDOWS, ANNUALIZE, SIGNAL_COLORS_BASE,
)


OPTION_TENORS = ['1m', '3m', '6m', '1y']
FWD_TENORS = ['1m', '3m', '6m', '1y']
SPREAD_PAIRS = list(combinations(OPTION_TENORS, 2))
FLY_COMBOS = list(combinations(OPTION_TENORS, 3))

CACHE_SUBDIR = '__fx_vol_tracker__'
DATA_KEYS = ['fx_spot', 'fx_fwd', 'fx_vol', 'vol_spreads', 'vol_flies']

PRODUCT_TABS = [
    ('fx_spot',      'Spot Rates'),
    ('fx_fwd',       'Forward Rates'),
    ('fx_vol',       'Implied Vols'),
    ('realized_vol', 'Realized Vol'),
    ('vol_spreads',  'Vol Term Spreads'),
    ('vol_flies',    'Vol Term Butterflies'),
]

SPOT_FWD_INSTRUMENTS = {
    'Spot':    ('fx_spot', 'spot', 'outright'),
    '1m Fwd':  ('fx_fwd',  '1m',  'outright'),
    '3m Fwd':  ('fx_fwd',  '3m',  'outright'),
    '6m Fwd':  ('fx_fwd',  '6m',  'outright'),
    '1y Fwd':  ('fx_fwd',  '1y',  'outright'),
}

VOL_INSTRUMENTS = {
    '1m Vol':      ('fx_vol',      '1m',        'outright'),
    '3m Vol':      ('fx_vol',      '3m',        'outright'),
    '1y Vol':      ('fx_vol',      '1y',        'outright'),
    'TS 3m/1m':    ('vol_spreads', '3m/1m',     'spread'),
    'TS 1y/1m':    ('vol_spreads', '1y/1m',     'spread'),
    'TS 1m/3m/1y': ('vol_flies',   '1m/3m/1y',  'fly'),
}

SPOT_FWD = DomainConfig(
    name='spot_fwd', label='G10 FX Spot & Forwards', tab_label='Spot / Fwd Matrix',
    instruments=SPOT_FWD_INSTRUMENTS,
    ref_key='fx_spot', ref_col='spot',
    outright_scale=1, spread_scale=1,
    outright_unit='', spread_unit='',
)

VOLS = DomainConfig(
    name='vols', label='G10 FX vols', tab_label='Vol Signal Matrix',
    instruments=VOL_INSTRUMENTS,
    ref_key='fx_vol', ref_col='1m',
    outright_scale=100, spread_scale=100,
    outright_unit='%', spread_unit='%',
)

DOMAINS = [SPOT_FWD, VOLS]

VOL_UNIT_OPTIONS = [
    {'label': '% (annualized)', 'value': 'pct'},
    {'label': '% daily', 'value': 'pctd'},
]
VOL_UNIT_SCALES = {'pct': 1.0, 'pctd': 1.0 / ANNUALIZE}

ALL_INSTRUMENTS = {**SPOT_FWD_INSTRUMENTS, **VOL_INSTRUMENTS}

VOL_PRODUCT_KEYS = {'fx_vol', 'vol_spreads', 'vol_flies', 'realized_vol'}
SPOT_FWD_KEYS = {'fx_spot', 'fx_fwd'}

PRODUCT_SCALE = {
    'fx_spot': 1, 'fx_fwd': 1,
    'fx_vol': 100, 'vol_spreads': 100, 'vol_flies': 100, 'realized_vol': 100,
}

PRODUCT_YAXIS_BASE = {
    'fx_spot': 'spot', 'fx_fwd': 'fwd rate',
    'fx_vol': '%', 'vol_spreads': '%', 'vol_flies': '%', 'realized_vol': '%',
}

SIGNAL_COLORS = {
    **SIGNAL_COLORS_BASE,
    'RISING': '#3fb950',
    'FALLING': '#f85149',
}


FX_CONFIG = VolTrackerConfig(
    page_title='FX Vol Market',
    page_route='/fx-vol-tracker/',
    prefix='fxv',
    entity_label='CCY Pairs',
    default_product='fx_spot',
    cache_subdir=CACHE_SUBDIR,
    domains=DOMAINS,
    product_tabs=PRODUCT_TABS,
    all_instruments=ALL_INSTRUMENTS,
    product_scale=PRODUCT_SCALE,
    product_yaxis_base=PRODUCT_YAXIS_BASE,
    vol_product_keys=VOL_PRODUCT_KEYS,
    vol_unit_options=VOL_UNIT_OPTIONS,
    vol_unit_scales=VOL_UNIT_SCALES,
    signal_colors=SIGNAL_COLORS,
)
