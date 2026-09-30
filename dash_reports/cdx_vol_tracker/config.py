import numpy as np

from dash_reports.vol_tracker_common.base_config import (
    DomainConfig, VolTrackerConfig, RVOL_WINDOWS, ANNUALIZE, SIGNAL_COLORS_BASE,
)


DEFAULT_TICKERS = ['CDX-NAHY', 'CDX-NAIG', 'ITRAXX-Europe', 'ITRAXX-Xover']
TENOR = '5Y'
CACHE_SUBDIR = '__cdx_vol_tracker__'
DATA_KEYS = ['cdx_spread', 'cdx_price', 'cdx_vol']

PRODUCT_TABS = [
    ('cdx_spread',   'CDX Spread'),
    ('cdx_price',    'CDX Price'),
    ('cdx_vol',      'Implied Vol'),
    ('realized_vol', 'Realized Vol'),
]

TICKER_TZ = {
    'CDX-NAHY': 'America/New_York',
    'CDX-NAIG': 'America/New_York',
    'ITRAXX-Europe': 'Europe/London',
    'ITRAXX-Xover': 'Europe/London',
}

SPREAD_INSTRUMENTS = {
    'Spread': ('cdx_spread', 'conv_spd_bps', 'outright'),
    'Price':  ('cdx_price',  'mkt_px',       'outright'),
}

N_EXPIRIES = 3
EXPIRY_LABELS = ['1st Expiry', '2nd Expiry', '3rd Expiry']

VOL_INSTRUMENTS = {
    f'{lbl} Vol': ('cdx_vol', lbl, 'outright') for lbl in EXPIRY_LABELS
}

SPREADS = DomainConfig(
    name='spreads', label='CDX spreads', tab_label='Spread Signal Matrix',
    instruments=SPREAD_INSTRUMENTS,
    ref_key='cdx_spread', ref_col='conv_spd_bps',
    outright_scale=1, spread_scale=1,
    outright_unit='bps', spread_unit='bps',
)

VOLS = DomainConfig(
    name='vols', label='CDX vols', tab_label='Vol Signal Matrix',
    instruments=VOL_INSTRUMENTS,
    ref_key='cdx_vol', ref_col=EXPIRY_LABELS[0],
    outright_scale=1, spread_scale=1,
    outright_unit='bpd', spread_unit='bpd',
)

DOMAINS = [SPREADS, VOLS]

PRICE_DATA_KEYS = {'cdx_price'}
VOL_PRODUCT_KEYS = {'cdx_vol', 'realized_vol'}

VOL_UNIT_OPTIONS = [
    {'label': 'bpd (bp/day)', 'value': 'bpd'},
    {'label': 'bp/yr (annualized)', 'value': 'bpyr'},
]
VOL_UNIT_SCALES = {'bpd': 1.0, 'bpyr': ANNUALIZE}

ALL_INSTRUMENTS = {**SPREAD_INSTRUMENTS, **VOL_INSTRUMENTS}

PRODUCT_SCALE = {
    'cdx_spread': 1, 'cdx_price': 1,
    'cdx_vol': 1, 'realized_vol': 1,
}

PRODUCT_YAXIS_BASE = {
    'cdx_spread': 'bps', 'cdx_price': 'pts',
    'cdx_vol': 'bpd', 'realized_vol': 'bpd',
}

SIGNAL_COLORS = {
    **SIGNAL_COLORS_BASE,
    'WIDENING': '#f85149',
    'TIGHTENING': '#3fb950',
}


CDX_CONFIG = VolTrackerConfig(
    page_title='CDX Vol Market',
    page_route='/cdx-vol-tracker/',
    prefix='cvt',
    entity_label='Tickers',
    default_product='cdx_spread',
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
