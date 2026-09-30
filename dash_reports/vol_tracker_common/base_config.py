from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np


RVOL_WINDOWS = {'1m': 21, '3m': 63, '6m': 126, '1y': 252}
LOOKBACK_OPTIONS = ['3m', '6m', '1y', '2y', '5y']
ANNUALIZE = np.sqrt(252)


@dataclass(frozen=True)
class DomainConfig:
    name: str
    label: str
    tab_label: str
    instruments: Dict[str, Tuple[str, str, str]]
    ref_key: str
    ref_col: str
    outright_scale: float
    spread_scale: float
    outright_unit: str = '%'
    spread_unit: str = 'bp'

    def scale_for(self, inst_type: str) -> float:
        return self.outright_scale if inst_type == 'outright' else self.spread_scale

    def unit_for(self, inst_type: str) -> str:
        return self.outright_unit if inst_type == 'outright' else self.spread_unit


@dataclass
class VolTrackerConfig:
    page_title: str
    page_route: str
    prefix: str
    entity_label: str
    default_product: str
    cache_subdir: str
    domains: list
    product_tabs: list
    all_instruments: dict
    product_scale: dict
    product_yaxis_base: dict
    vol_product_keys: set
    vol_unit_options: list
    vol_unit_scales: dict
    signal_colors: dict

    @property
    def inst_to_product(self) -> dict:
        return {name: spec[0] for name, spec in self.all_instruments.items()}

    @property
    def domain_by_data_key(self) -> dict:
        mapping = {}
        for d in self.domains:
            for _, (dk, _, _) in d.instruments.items():
                mapping.setdefault(dk, d)
        return mapping


SIGNAL_COLORS_BASE = {
    'STABLE': '#8b949e',
    'RANGE': '#8b949e',
    'VOL BID': '#f85149',
    'VOL OFFERED': '#3fb950',
    'RALLYING': '#3fb950',
    'SELLING OFF': '#f85149',
    'STEEPENING': '#d29922',
    'FLATTENING': '#58a6ff',
    'BELLY CHEAP': '#3fb950',
    'BELLY RICH': '#f85149',
    'BELLY DISLOCATING': '#d29922',
    'FLY NEUTRAL': '#8b949e',
}
