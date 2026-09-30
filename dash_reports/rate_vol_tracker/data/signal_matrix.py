from collections import Counter
from typing import Tuple

from dash_reports.rate_vol_tracker.config import (
    RATES, VOLS, ALL_INSTRUMENTS, SIGNAL_COLORS, RATE_CONFIG,
)
from dash_reports.vol_tracker_common.base_config import DomainConfig
from dash_reports.vol_tracker_common.signal_matrix import (
    RegimeClassifier, SignalMatrixBuilder, XCcySummaryBuilder, _safe_get,
    conviction_opacity, build_signal_matrix as base_build_signal_matrix,
)

MATRIX_INSTRUMENTS = ALL_INSTRUMENTS


class RateRegimeClassifier(RegimeClassifier):
    @property
    def signal_colors(self) -> dict:
        return SIGNAL_COLORS

    def classify(self, inst_type: str, delta_1m: float, z_1m: float,
                 domain: DomainConfig, data_key: str = None,
                 ref_ts=None, **kwargs) -> Tuple[str, str]:
        if inst_type == 'spread':
            if domain.name == 'vols':
                return self._classify_vol_spread(delta_1m, z_1m)
            return self._classify_rate_spread(delta_1m, z_1m, ref_ts)
        if inst_type == 'fly':
            return self._classify_fly(z_1m)
        return self._classify_outright(z_1m, domain)

    def _classify_rate_spread(self, delta_1m, z_1m, rate_ts):
        if abs(z_1m) < 1.5:
            return 'RANGE', SIGNAL_COLORS['RANGE']

        widening = delta_1m > 0
        rate_delta_1m = None
        if rate_ts is not None and not rate_ts.dropna().empty and len(rate_ts.dropna()) >= 22:
            rts = rate_ts.dropna()
            rate_delta_1m = rts.iloc[-1] - rts.iloc[-21]

        if rate_delta_1m is None:
            direction = 'TWIST'
        elif rate_delta_1m < 0:
            direction = 'BULL'
        elif rate_delta_1m > 0:
            direction = 'BEAR'
        else:
            direction = 'TWIST'

        tag = f'{direction} {"STEEP" if widening else "FLAT"}'
        return tag, SIGNAL_COLORS.get(tag, '#8b949e')

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

    def _classify_outright(self, z_1m, domain):
        if abs(z_1m) < 1.5:
            return 'STABLE', SIGNAL_COLORS['STABLE']
        outright_labels = {
            'rates': {1: 'SELLING OFF', -1: 'RALLYING'},
            'vols':  {1: 'VOL BID',     -1: 'VOL OFFERED'},
        }
        labels = outright_labels.get(domain.name, outright_labels['rates'])
        tag = labels[1] if z_1m > 0 else labels[-1]
        return tag, SIGNAL_COLORS[tag]


class RateXCcySummaryBuilder(XCcySummaryBuilder):
    def build(self, cells: dict, instruments: dict) -> dict:
        rv = {}
        spread_key = next((k for k in instruments if '2s10s' in k.lower()), None)
        fly_key = next((k for k in instruments if 'fly' in k.lower()), None)

        if spread_key:
            spreads = [(ccy, c['level_display'], c['level_unit'])
                       for ccy, cc in cells.items()
                       if (c := cc.get(spread_key)) is not None]
            if spreads:
                by_level = sorted(spreads, key=lambda x: x[1])
                rv['widest_2s10s'] = (by_level[-1][0], by_level[-1][1], by_level[-1][2])
                rv['tightest_2s10s'] = (by_level[0][0], by_level[0][1], by_level[0][2])

        if fly_key:
            flies = [(ccy, c['z_1m'])
                     for ccy, cc in cells.items()
                     if (c := cc.get(fly_key)) is not None]
            if flies:
                by_z = sorted(flies, key=lambda x: x[1])
                rv['cheapest_belly'] = f'{by_z[-1][0]} (z={by_z[-1][1]:+.1f})'
                rv['richest_belly'] = f'{by_z[0][0]} (z={by_z[0][1]:+.1f})'

        all_spread_regimes = {}
        for ccy, cc in cells.items():
            regimes = [c['regime'] for inst, c in cc.items()
                       if c and instruments[inst][2] == 'spread']
            if regimes:
                all_spread_regimes[ccy] = Counter(regimes).most_common(1)[0][0]

        if all_spread_regimes:
            mode_regime = Counter(all_spread_regimes.values()).most_common(1)[0][0]
            divergent = [c for c, r in all_spread_regimes.items() if r != mode_regime]
            rv['most_divergent'] = f'{divergent[0]} ({all_spread_regimes[divergent[0]]})' if divergent else 'All aligned'
        else:
            rv['most_divergent'] = 'N/A'

        return rv


_classifier = RateRegimeClassifier()
_builder = SignalMatrixBuilder(RATE_CONFIG, _classifier)
_xccy_builder = RateXCcySummaryBuilder()


def build_signal_matrix(all_ccy_data: dict, domain) -> dict:
    if not isinstance(domain, DomainConfig):
        domain = RATES if domain == 'rates' else VOLS
    return base_build_signal_matrix(
        all_ccy_data, domain, _builder, _xccy_builder,
        use_ref_ts=True)
