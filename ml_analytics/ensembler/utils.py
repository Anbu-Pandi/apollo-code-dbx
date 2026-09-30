from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReturnType(Enum):
    PCT_CHANGE = "pct_change"
    PRICE_DIFF = "price_diff"
    LOG_RETURN = "log_return"
    LEVEL = "level"


@dataclass
class SignalParameters:
    zscore_entry: float = 2.0
    zscore_exit: float = 0.0
    min_correlation: float = 0.3
    vol_lookback: int = 20
    halflife: Optional[int] = None
    kalman_r: float = 0.01
    kalman_q: float = 0.01
    ml_window: int = 100  # Window for ML feature generation
    regime_window: int = 50  # Window for regime detection
    feature_lookback_window: int = 1  # Lookback for features in model


@dataclass
class RiskParameters:
    max_position_size: float = 1.0
    max_leverage: float = 2.0
    stop_loss_std: float = 3.0
    position_timeout: int = 60  # minutes
    max_drawdown: float = 0.1
    var_percentile: float = 0.99
    max_correlation: float = 0.7
