from itertools import combinations

from dash_reports.vol_tracker_common.base_config import (
    DomainConfig, VolTrackerConfig, RVOL_WINDOWS, ANNUALIZE, SIGNAL_COLORS_BASE,
)


CCYS = ['USD', 'EUR', 'GBP', 'JPY']
FWD_STARTS = ['1m', '3m', '6m', '1y']
FWD_STARTS_WITH_SPOT = ['spot'] + FWD_STARTS
UNDERLYING_TENORS = ['3m', '2y', '5y', '10y']
OPTION_TENORS = ['1m', '3m', '6m', '1y']

SPREAD_PAIRS = list(combinations(UNDERLYING_TENORS, 2))
FLY_COMBOS = list(combinations(UNDERLYING_TENORS, 3))

CCY_FLOAT_TENOR = {'USD': '3m', 'EUR': '6m', 'GBP': '6m', 'JPY': '6m'}
CACHE_SUBDIR = '__rate_vol_tracker__'
DATA_KEYS = ['fwd_rates', 'swaption_vols', 'fwd_spreads', 'vol_spreads', 'fwd_flies', 'vol_flies']

PRODUCT_TABS = [
    ('fwd_rates',     'Forward Rates'),
    ('swaption_vols', 'Implied Vols'),
    ('realized_vol',  'Realized Vol'),
    ('fwd_spreads',   'Fwd Spreads'),
    ('vol_spreads',   'Vol Spreads'),
    ('fwd_flies',     'Fwd Butterflies'),
    ('vol_flies',     'Vol Butterflies'),
]

RATE_INSTRUMENTS = {
    '2s10s':      ('fwd_spreads', 'spot_10y/2y',    'spread'),
    '2s5s':       ('fwd_spreads', 'spot_5y/2y',     'spread'),
    '5s10s':      ('fwd_spreads', 'spot_10y/5y',    'spread'),
    '2-5-10 Fly': ('fwd_flies',   'spot_2y/5y/10y', 'fly'),
    '2y':         ('fwd_rates',   'spot_2y',         'outright'),
    '5y':         ('fwd_rates',   'spot_5y',         'outright'),
    '10y':        ('fwd_rates',   'spot_10y',        'outright'),
}

VOL_INSTRUMENTS = {
    'Vol 2s10s':      ('vol_spreads',   '1m_10y/2y',    'spread'),
    'Vol 2s5s':       ('vol_spreads',   '1m_5y/2y',     'spread'),
    'Vol 5s10s':      ('vol_spreads',   '1m_10y/5y',    'spread'),
    'Vol 2-5-10 Fly': ('vol_flies',     '1m_2y/5y/10y', 'fly'),
    'Vol 2y':         ('swaption_vols', '1m_2y',         'outright'),
    'Vol 5y':         ('swaption_vols', '1m_5y',         'outright'),
    'Vol 10y':        ('swaption_vols', '1m_10y',        'outright'),
}

RATES = DomainConfig(
    name='rates', label='DM rates', tab_label='Rates Signal Matrix',
    instruments=RATE_INSTRUMENTS,
    ref_key='fwd_rates', ref_col='spot_10y',
    outright_scale=100, spread_scale=10_000,
)

VOLS = DomainConfig(
    name='vols', label='DM vols', tab_label='Vol Signal Matrix',
    instruments=VOL_INSTRUMENTS,
    ref_key='swaption_vols', ref_col='1m_10y',
    outright_scale=1, spread_scale=1,
    outright_unit='bpd', spread_unit='bpd',
)

DOMAINS = [RATES, VOLS]

VOL_UNIT_OPTIONS = [
    {'label': 'bpd (bp/day)', 'value': 'bpd'},
    {'label': 'bp/yr (annualized)', 'value': 'bpyr'},
]
VOL_UNIT_SCALES = {'bpd': 1.0, 'bpyr': ANNUALIZE}

ALL_INSTRUMENTS = {**RATE_INSTRUMENTS, **VOL_INSTRUMENTS}

VOL_PRODUCT_KEYS = {'swaption_vols', 'vol_spreads', 'vol_flies', 'realized_vol'}

PRODUCT_SCALE = {
    'fwd_rates': RATES.outright_scale,
    'fwd_spreads': RATES.spread_scale,
    'fwd_flies': RATES.spread_scale,
    'swaption_vols': 1, 'vol_spreads': 1, 'vol_flies': 1, 'realized_vol': 1,
}

PRODUCT_YAXIS_BASE = {
    'fwd_rates': '%', 'fwd_spreads': 'bp', 'fwd_flies': 'bp',
    'swaption_vols': 'bpd', 'vol_spreads': 'bpd', 'vol_flies': 'bpd', 'realized_vol': 'bpd',
}

SIGNAL_COLORS = {
    **SIGNAL_COLORS_BASE,
    'BULL STEEP': '#3fb950',
    'BEAR STEEP': '#d29922',
    'TWIST STEEP': '#58a6ff',
    'BULL FLAT': '#d4a017',
    'BEAR FLAT': '#f85149',
    'TWIST FLAT': '#8b5cf6',
}


RATE_CONFIG = VolTrackerConfig(
    page_title='Rate Vol Market',
    page_route='/rate-vol-tracker/',
    prefix='ivt',
    entity_label='Currencies',
    default_product='fwd_rates',
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
