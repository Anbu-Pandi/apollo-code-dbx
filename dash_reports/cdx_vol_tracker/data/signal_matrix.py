from collections import Counter
from typing import Tuple

from dash_reports.cdx_vol_tracker.config import (
    SPREADS, VOLS, ALL_INSTRUMENTS, SIGNAL_COLORS, PRICE_DATA_KEYS, CDX_CONFIG,
)
from dash_reports.vol_tracker_common.base_config import DomainConfig
from dash_reports.vol_tracker_common.signal_matrix import (
    RegimeClassifier, SignalMatrixBuilder, XCcySummaryBuilder,
    conviction_opacity, build_signal_matrix as base_build_signal_matrix,
)

MATRIX_INSTRUMENTS = ALL_INSTRUMENTS


class CDXRegimeClassifier(RegimeClassifier):
    @property
    def signal_colors(self) -> dict:
        return SIGNAL_COLORS

    def classify(self, inst_type: str, delta_1m: float, z_1m: float,
                 domain: DomainConfig, data_key: str = None,
                 ref_ts=None, **kwargs) -> Tuple[str, str]:
        if abs(z_1m) < 1.5:
            return 'STABLE', SIGNAL_COLORS['STABLE']

        labels = self._get_labels(data_key)
        tag = labels[1] if z_1m > 0 else labels[-1]
        return tag, SIGNAL_COLORS.get(tag, '#8b949e')

    def _get_labels(self, data_key: str) -> dict:
        outright_labels = {
            'cdx_spread': {1: 'WIDENING', -1: 'TIGHTENING'},
            'cdx_price':  {1: 'RALLYING', -1: 'SELLING OFF'},
            'cdx_vol':    {1: 'VOL BID',  -1: 'VOL OFFERED'},
        }
        return outright_labels.get(data_key, {1: 'WIDENING', -1: 'TIGHTENING'})


class CDXXCcySummaryBuilder(XCcySummaryBuilder):
    def build(self, cells: dict, instruments: dict) -> dict:
        rv = {}
        spread_key = next((k for k in instruments if 'spread' in k.lower()), None)
        vol_key = next((k for k in instruments if 'vol' in k.lower()), None)

        if spread_key:
            spreads = [(t, c['level_display'], c['level_unit'])
                       for t, tc in cells.items()
                       if (c := tc.get(spread_key)) is not None]
            if spreads:
                by_level = sorted(spreads, key=lambda x: x[1])
                rv['widest_spread'] = (by_level[-1][0], by_level[-1][1], by_level[-1][2])
                rv['tightest_spread'] = (by_level[0][0], by_level[0][1], by_level[0][2])

        if vol_key:
            vols = [(t, c['level_display'], c['level_unit'])
                    for t, tc in cells.items()
                    if (c := tc.get(vol_key)) is not None]
            if vols:
                by_level = sorted(vols, key=lambda x: x[1])
                rv['highest_vol'] = (by_level[-1][0], by_level[-1][1], by_level[-1][2])
                rv['lowest_vol'] = (by_level[0][0], by_level[0][1], by_level[0][2])

        all_regimes = {}
        for t, tc in cells.items():
            regimes = [c['regime'] for inst, c in tc.items() if c]
            if regimes:
                all_regimes[t] = Counter(regimes).most_common(1)[0][0]

        if all_regimes:
            mode_regime = Counter(all_regimes.values()).most_common(1)[0][0]
            divergent = [t for t, r in all_regimes.items() if r != mode_regime]
            rv['most_divergent'] = f'{divergent[0]} ({all_regimes[divergent[0]]})' if divergent else 'All aligned'
        else:
            rv['most_divergent'] = 'N/A'

        return rv


_classifier = CDXRegimeClassifier()
_builder = SignalMatrixBuilder(CDX_CONFIG, _classifier)
_xccy_builder = CDXXCcySummaryBuilder()


def build_signal_matrix(all_ticker_data: dict, domain) -> dict:
    if not isinstance(domain, DomainConfig):
        domain = SPREADS if domain == 'spreads' else VOLS
    return base_build_signal_matrix(
        all_ticker_data, domain, _builder, _xccy_builder,
        use_pct_keys=PRICE_DATA_KEYS)
