from dataclasses import dataclass
from typing import Dict, List, Tuple

from dash_reports.cdx_vol_tracker.config import CDX_CONFIG, DEFAULT_TICKERS as CDX_TICKERS
from dash_reports.rate_vol_tracker.config import RATE_CONFIG, CCYS as RATE_CCYS
from dash_reports.fx_vol_tracker.config import FX_CONFIG


@dataclass(frozen=True)
class AssetClassConfig:
    key: str
    label: str
    color: str
    config: object
    default_entities: list
    compact_instruments: List[str]
    compact_entity_count: int = 4


CDX_ASSET = AssetClassConfig(
    key='cdx',
    label='CDX Vol Market',
    color='#1BAFDC',
    config=CDX_CONFIG,
    default_entities=CDX_TICKERS,
    compact_instruments=['Spread', '1st Expiry Vol', '2nd Expiry Vol'],
    compact_entity_count=4,
)

RATE_ASSET = AssetClassConfig(
    key='rate',
    label='Rate Vol Market',
    color='#d29922',
    config=RATE_CONFIG,
    default_entities=RATE_CCYS,
    compact_instruments=['2s10s', 'Vol 2s10s', 'Vol 10y'],
    compact_entity_count=4,
)

FX_PAIRS = ['USDJPY', 'EURUSD', 'GBPUSD', 'USDCHF', 'AUDUSD', 'USDCAD']

FX_ASSET = AssetClassConfig(
    key='fx',
    label='FX Vol Market',
    color='#8b5cf6',
    config=FX_CONFIG,
    default_entities=FX_PAIRS,
    compact_instruments=['Spot', '1m Vol', '3m Vol', 'TS 3m/1m'],
    compact_entity_count=6,
)

ASSET_CLASSES = [CDX_ASSET, RATE_ASSET, FX_ASSET]
ASSET_BY_KEY = {a.key: a for a in ASSET_CLASSES}

PAGE_TITLE = 'Vol Market Dashboard'
PAGE_ROUTE = '/vol-dashboard/'
PAGE_PREFIX = 'vmd'

MAX_SELECTIONS = 8

COMPARISON_COLORS = [
    '#1BAFDC', '#d29922', '#8b5cf6',
    '#3fb950', '#f85149', '#58a6ff',
    '#A6C773', '#006D77',
]


@dataclass
class DashboardConfig:
    page_title: str = PAGE_TITLE
    page_route: str = PAGE_ROUTE
    prefix: str = PAGE_PREFIX
    asset_classes: List[AssetClassConfig] = None
    max_selections: int = MAX_SELECTIONS
    comparison_colors: List[str] = None

    def __post_init__(self):
        if self.asset_classes is None:
            self.asset_classes = ASSET_CLASSES
        if self.comparison_colors is None:
            self.comparison_colors = COMPARISON_COLORS


DASHBOARD_CONFIG = DashboardConfig()
