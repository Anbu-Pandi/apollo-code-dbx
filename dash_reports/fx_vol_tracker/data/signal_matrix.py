from collections import Counter
from typing import Tuple

from dash_reports.fx_vol_tracker.config import (
    SPOT_FWD, VOLS, ALL_INSTRUMENTS, SIGNAL_COLORS, SPOT_FWD_KEYS, FX_CONFIG,
)
from dash_reports.vol_tracker_common.base_config import DomainConfig
from dash_reports.vol_tracker_common.signal_matrix import (
    RegimeClassifier, SignalMatrixBuilder, XCcySummaryBuilder,
    conviction_opacity, build_signal_matrix as base_build_signal_matrix,
)

MATRIX_INSTRUMENTS = ALL_INSTRUMENTS


class FXRegimeClassifier(RegimeClassifier):
    @property
    def signal_colors(self) -> dict:
        return SIGNAL_COLORS

    def classify(self, inst_type: str, delta_1m: float, z_1m: float,
                 domain: DomainConfig, data_key: str = None,
                 ref_ts=None, **kwargs) -> Tuple[str, str]:
        if inst_type == 'spread':
            return self._classify_vol_spread(delta_1m, z_1m)
        if inst_type == 'fly':
            return self._classify_fly(z_1m)
        return self._classify_outright(z_1m, domain, data_key)

    def _classify_vol_spread(self, delta_1m, z_1m):
        if abs(z_1m) < 1.5:
            return 'RANGE', SIGNAL_COLORS['RANGE']
        tag = 'STEEPENING' if delta_1m > 0 else 'FLATTENING'
        return tag, SIGNAL_COLORS[tag]

    def _classify_fly(self, z_1m):
        if abs(z_1m) > 2.0:
            return 'BELLY DISLOCATING', SIGNAL_COLORS['BELLY DISLOCATING']
        if z_1m > 1.5:
            return 'BELLY CHEAP', SIGNAL_COLORS['BELLY CHEAP']
        if z_1m < -1.5:
            return 'BELLY RICH', SIGNAL_COLORS['BELLY RICH']
        return 'FLY NEUTRAL', SIGNAL_COLORS['FLY NEUTRAL']

    def _classify_outright(self, z_1m, domain, data_key):
        if abs(z_1m) < 1.5:
            return 'STABLE', SIGNAL_COLORS['STABLE']
        key_to_group = {'fx_spot': 'spot', 'fx_fwd': 'spot', 'fx_vol': 'vols'}
        outright_labels = {
            'spot': {1: 'RISING', -1: 'FALLING'},
            'vols': {1: 'VOL BID', -1: 'VOL OFFERED'},
        }
        group = key_to_group.get(data_key, domain.name)
        labels = outright_labels.get(group, outright_labels['spot'])
        tag = labels[1] if z_1m > 0 else labels[-1]
        return tag, SIGNAL_COLORS[tag]


class FXXCcySummaryBuilder(XCcySummaryBuilder):
    def build(self, cells: dict, instruments: dict) -> dict:
        rv = {}
        vol_key = next((k for k in instruments if 'vol' in k.lower() and 'ts' not in k.lower()), None)
        fly_key = next((k for k in instruments if 'fly' in k.lower() or 'ts' in k.lower()), None)

        if vol_key:
            vols = [(ccy, c['level_display'], c['level_unit'])
                    for ccy, cc in cells.items()
                    if (c := cc.get(vol_key)) is not None]
            if vols:
                by_level = sorted(vols, key=lambda x: x[1])
                rv['highest_vol'] = (by_level[-1][0], by_level[-1][1], by_level[-1][2])
                rv['lowest_vol'] = (by_level[0][0], by_level[0][1], by_level[0][2])

        if fly_key:
            flies = [(ccy, c['z_1m'])
                     for ccy, cc in cells.items()
                     if (c := cc.get(fly_key)) is not None]
            if flies:
                by_z = sorted(flies, key=lambda x: x[1])
                rv['steepest_curve'] = f'{by_z[-1][0]} (z={by_z[-1][1]:+.1f})'
                rv['flattest_curve'] = f'{by_z[0][0]} (z={by_z[0][1]:+.1f})'

        all_regimes = {}
        for ccy, cc in cells.items():
            regimes = [c['regime'] for inst, c in cc.items() if c]
            if regimes:
                all_regimes[ccy] = Counter(regimes).most_common(1)[0][0]

        if all_regimes:
            mode_regime = Counter(all_regimes.values()).most_common(1)[0][0]
            divergent = [c for c, r in all_regimes.items() if r != mode_regime]
            rv['most_divergent'] = f'{divergent[0]} ({all_regimes[divergent[0]]})' if divergent else 'All aligned'
        else:
            rv['most_divergent'] = 'N/A'

        return rv


_classifier = FXRegimeClassifier()
_builder = SignalMatrixBuilder(FX_CONFIG, _classifier)
_xccy_builder = FXXCcySummaryBuilder()


def build_signal_matrix(all_ccy_data: dict, domain) -> dict:
    if not isinstance(domain, DomainConfig):
        domain = {'spot_fwd': SPOT_FWD, 'vols': VOLS}.get(domain, SPOT_FWD)
    return base_build_signal_matrix(
        all_ccy_data, domain, _builder, _xccy_builder,
        use_pct_keys=SPOT_FWD_KEYS)
