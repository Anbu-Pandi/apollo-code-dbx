from typing import Dict, List, Optional
from dataclasses import dataclass

from ml_analytics.ensembler.utils import ReturnType, SignalParameters, RiskParameters


@dataclass
class StatArbConfig:
    y_feature: str
    x_features: List[str]
    return_types: Dict[str, ReturnType]
    window_days: float = 365
    signal_params: Optional[SignalParameters] = None
    risk_params: Optional[RiskParameters] = None
    poll_interval_seconds: int = 60
    periodicity: str = 'DAILY'
    id_column: Optional[str] = None
    predictors: Optional[List[str]] = None

    @classmethod
    def create_default(cls):
        return cls(
            y_feature="PCE CMOM Index",
            x_features=[
                "CPI CHNG Index",
                "PPI CHNG Index",
                "AHE MOM% Index",
                "RSTAMOM Index",
                "NAPMPMI Index",
                "NAPMNMI Index",
                "USURTOT Index",
                "INJCJC Index"
            ],
            return_types={
                "NAPMPMI Index": ReturnType.PRICE_DIFF,
                "NAPMNMI Index": ReturnType.PRICE_DIFF,
                "USURTOT Index": ReturnType.PRICE_DIFF,
                "INJCJC Index": ReturnType.PCT_CHANGE
            },
            window_days=365 * 5,
            signal_params=SignalParameters(
                zscore_entry=2.0,
                zscore_exit=0.0,
                min_correlation=0.3,
                vol_lookback=20,
                halflife=10,
                ml_window=24,
                regime_window=12
            ),
            risk_params=RiskParameters(
                max_position_size=1.0,
                max_leverage=2.0,
                stop_loss_std=3.0,
                position_timeout=60,
                max_drawdown=0.1
            ),
            periodicity='MONTHLY',
            poll_interval_seconds=60,
            predictors=['xgb', 'elasticnet', 'kalman', 'ensemble'],
        )

    @classmethod
    def create_equity_pair(cls):
        return cls(
            y_feature="MSFT US Equity",
            x_features=[
                "AAPL US Equity",
                "GOOGL US Equity",
                "AMZN US Equity",
                "META US Equity"
            ],
            return_types={},  # Default to price level
            window_days=252,
            signal_params=SignalParameters(
                zscore_entry=2.0,
                zscore_exit=0.5,
                min_correlation=0.6
            ),
            risk_params=RiskParameters(
                max_position_size=0.5,
                max_leverage=1.5,
                stop_loss_std=2.5
            ),
            periodicity='DAILY',
            poll_interval_seconds=300
        )

    @classmethod
    def create_fx_pair(cls):
        return cls(
            y_feature="EURUSD Curncy",
            x_features=[
                "GBPUSD Curncy",
                "USDJPY Curncy",
                "USDCHF Curncy",
                "DXY Index"
            ],
            return_types={},  # Default to price level
            window_days=60,
            signal_params=SignalParameters(
                zscore_entry=1.5,
                zscore_exit=0.3,
                min_correlation=0.5,
                kalman_q=0.01,
                kalman_r=0.5
            ),
            risk_params=RiskParameters(
                max_position_size=2.0,
                max_leverage=3.0,
                stop_loss_std=2.0,
                position_timeout=120
            ),
            periodicity='DAILY',
            poll_interval_seconds=300
        )