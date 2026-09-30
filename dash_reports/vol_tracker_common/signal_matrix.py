from abc import ABC, abstractmethod
from collections import Counter
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from dash_reports.vol_tracker_common.base_config import DomainConfig, SIGNAL_COLORS_BASE


def _safe_get(ts, offset: int):
    if ts is None or len(ts) < abs(offset) + 1:
        return np.nan
    return ts.iloc[offset]


def _conviction(z: float) -> str:
    az = abs(z)
    if az > 2.0:
        return 'screaming'
    if az > 1.5:
        return 'strong'
    if az > 1.0:
        return 'medium'
    return 'light'


def conviction_opacity(conviction: str) -> float:
    return {'screaming': 0.35, 'strong': 0.25, 'medium': 0.15, 'light': 0.05}.get(conviction, 0.05)


def _z_label(z: float) -> str:
    az = abs(z)
    if az < 0.75:
        return 'noise'
    if az < 1.25:
        return 'mild'
    if az < 1.75:
        return 'notable'
    if az < 2.25:
        return 'strong'
    return 'extreme'


def compute_watch(delta_1w: float, delta_1m: float, z_1m: float, z_1w: float,
                  inst_type: str, regime: str) -> str:
    tag = _z_label(z_1m)

    if abs(z_1m) < 0.75:
        if abs(z_1w) < 0.75:
            return f'z={z_1m:+.1f} — quiet'
        return f'z={z_1m:+.1f}, 1w z={z_1w:+.1f} — new move'

    avg_weekly_1m = delta_1m / 4.0 if delta_1m != 0 else 0.0
    if abs(avg_weekly_1m) < 1e-10:
        return f'z={z_1m:+.1f} ({tag}) — emerging'

    momentum_ratio = delta_1w / avg_weekly_1m
    same_sign = (delta_1w * avg_weekly_1m) > 0

    if same_sign:
        if abs(momentum_ratio) > 1.5 and abs(z_1w) >= 0.75:
            return f'z={z_1m:+.1f} ({tag}) — accelerating'
        if abs(momentum_ratio) > 0.5:
            return f'z={z_1m:+.1f} ({tag}) — steady'
        return f'z={z_1m:+.1f} ({tag}) — fading'

    if abs(z_1w) >= 1.0:
        return f'z={z_1m:+.1f} ({tag}), 1w z={z_1w:+.1f} — reversing'
    return f'z={z_1m:+.1f} ({tag}) — stalling'


def _get_ts(entity_data: dict, data_key: str, col_name: str):
    df = entity_data.get(data_key)
    if df is None or df.empty or col_name not in df.columns:
        return None
    return df[col_name]


def _sort_by_z(cells: dict) -> dict:
    def _max_z(entity_cells):
        zs = [abs(c['z_1m']) for c in entity_cells.values() if c is not None]
        return max(zs) if zs else 0.0
    return dict(sorted(cells.items(), key=lambda x: _max_z(x[1]), reverse=True))


def _find_most_confident(cells: dict, inst_order: list) -> dict:
    confident = {}
    for inst_name in inst_order:
        best_entity, best_z = None, 0.0
        for entity, entity_cells in cells.items():
            cell = entity_cells.get(inst_name)
            if cell and abs(cell['z_1m']) > abs(best_z):
                best_entity = entity
                best_z = cell['z_1m']
        confident[inst_name] = best_entity
    return confident


class RegimeClassifier(ABC):
    @property
    @abstractmethod
    def signal_colors(self) -> dict:
        pass

    @abstractmethod
    def classify(self, inst_type: str, delta_1m: float, z_1m: float,
                 domain: DomainConfig, data_key: str = None,
                 ref_ts=None, **kwargs) -> Tuple[str, str]:
        pass


class SignalMatrixBuilder:
    def __init__(self, config, regime_classifier: RegimeClassifier):
        self.config = config
        self.classifier = regime_classifier

    def compute_signal_cell(self, ts, inst_type: str, domain: DomainConfig,
                            data_key: str = None, ref_ts=None, use_pct: bool = False) -> Optional[dict]:
        if ts is None or ts.dropna().empty or len(ts.dropna()) < 22:
            return None

        ts = ts.dropna()
        level = ts.iloc[-1]
        val_21d = _safe_get(ts, -21) if len(ts) >= 22 else _safe_get(ts, 0)
        val_5d = _safe_get(ts, -5) if len(ts) >= 6 else _safe_get(ts, 0)

        if use_pct and val_21d and abs(val_21d) > 1e-10:
            delta_1m = level / val_21d - 1
            delta_1w = (level / val_5d - 1) if val_5d and abs(val_5d) > 1e-10 else 0.0
            daily_std = ts.pct_change().rolling(63, min_periods=15).std()
        else:
            delta_1m = level - val_21d
            delta_1w = level - val_5d
            daily_std = ts.diff().rolling(63, min_periods=15).std()

        std_now = daily_std.iloc[-1] if not daily_std.empty else np.nan
        std_21d = std_now * np.sqrt(21) if std_now and std_now > 1e-10 else np.nan
        std_5d = std_now * np.sqrt(5) if std_now and std_now > 1e-10 else np.nan

        z_1m = delta_1m / std_21d if std_21d and std_21d > 1e-10 else 0.0
        z_1w = delta_1w / std_5d if std_5d and std_5d > 1e-10 else 0.0

        regime, regime_color = self.classifier.classify(
            inst_type, delta_1m, z_1m, domain, data_key=data_key, ref_ts=ref_ts)
        watch = compute_watch(delta_1w, delta_1m, z_1m, z_1w, inst_type, regime)

        scale = domain.scale_for(inst_type)
        unit = domain.unit_for(inst_type)

        return {
            'level': level,
            'level_display': level * scale,
            'level_unit': unit,
            'delta_1m': delta_1m,
            'delta_1m_bp': delta_1m * scale,
            'delta_1w': delta_1w,
            'delta_1w_bp': delta_1w * scale,
            'z_1m': z_1m,
            'z_1w': z_1w,
            'regime': regime,
            'regime_color': regime_color,
            'watch': watch,
            'conviction': _conviction(z_1m),
            'inst_type': inst_type,
            'use_pct': use_pct,
        }

    def build_matrix(self, all_entity_data: dict, domain: DomainConfig,
                     use_pct_keys: set = None, use_ref_ts: bool = False) -> dict:
        instruments = domain.instruments
        inst_order = list(instruments.keys())
        use_pct_keys = use_pct_keys or set()

        cells = {}
        for entity, entity_data in all_entity_data.items():
            entity_cells = {}
            ref_ts = _get_ts(entity_data, domain.ref_key, domain.ref_col) if use_ref_ts else None

            for inst_name, (data_key, col_name, inst_type) in instruments.items():
                ts = _get_ts(entity_data, data_key, col_name)
                use_pct = data_key in use_pct_keys
                cell = self.compute_signal_cell(
                    ts, inst_type, domain, data_key=data_key,
                    ref_ts=ref_ts if inst_type == 'spread' else None,
                    use_pct=use_pct)
                entity_cells[inst_name] = cell

            if any(c is not None for c in entity_cells.values()):
                cells[entity] = entity_cells

        cells = _sort_by_z(cells)
        most_confident = _find_most_confident(cells, inst_order)
        summary = self._build_summary(cells, instruments, domain)

        return {
            'cells': cells,
            'most_confident': most_confident,
            'summary': summary,
            'instruments': inst_order,
        }

    def _build_summary(self, cells: dict, instruments: dict, domain: DomainConfig) -> dict:
        all_regimes = []
        strongest_z, strongest_cell, strongest_entity, strongest_inst = 0.0, None, None, None

        for entity, entity_cells in cells.items():
            for inst_name, cell in entity_cells.items():
                if cell is None:
                    continue
                all_regimes.append((entity, cell['regime']))
                if abs(cell['z_1m']) > abs(strongest_z):
                    strongest_z = cell['z_1m']
                    strongest_cell, strongest_entity, strongest_inst = cell, entity, inst_name

        regime_counts = Counter(r for _, r in all_regimes)
        dominant = regime_counts.most_common(1)[0][0] if regime_counts else 'STABLE'

        outlier_entities = [e for e, r in all_regimes if r != dominant]
        if outlier_entities:
            outlier_regime = next((r for e, r in all_regimes if e == outlier_entities[0]), '')
            outlier = f'{outlier_entities[0]} is {outlier_regime.lower()} while rest is {dominant.lower()}'
        else:
            outlier = 'All entities aligned'

        strongest = (f'{strongest_entity} {strongest_inst} — '
                     f'{strongest_cell["regime"]} (z={strongest_z:+.1f})') if strongest_cell else 'No strong signals'

        return {
            'dominant_regime': f'Broad {dominant.lower()} across {domain.label}',
            'outlier': outlier,
            'strongest_signal': strongest,
        }


class XCcySummaryBuilder(ABC):
    @abstractmethod
    def build(self, cells: dict, instruments: dict) -> dict:
        pass


def build_signal_matrix(all_entity_data: dict, domain: DomainConfig,
                        builder: SignalMatrixBuilder, xccy_builder: XCcySummaryBuilder = None,
                        use_pct_keys: set = None, use_ref_ts: bool = False) -> dict:
    result = builder.build_matrix(all_entity_data, domain, use_pct_keys, use_ref_ts)
    if xccy_builder:
        result['xccy'] = xccy_builder.build(result['cells'], domain.instruments)
    return result
