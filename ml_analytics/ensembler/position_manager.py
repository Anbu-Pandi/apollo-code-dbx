import logging
import numpy as np
from datetime import datetime
from typing import Dict, List, Any

from ml_analytics.ensembler.utils import RiskParameters


class PositionManager:
    def __init__(self, risk_params=None):
        self.risk_params = risk_params or RiskParameters()
        self.positions = {}
        self.entry_prices = {}
        self.entry_times = {}
        self.stop_levels = {}

        # Add default account value if not present in risk params
        if not hasattr(self.risk_params, 'account_value'):
            self.risk_params.account_value = 1000000.0  # Default to $1M

    def update_positions(self, signals, current_prices, volatilities, regime="Normal"):
        try:
            new_positions = {}

            regime_risk_factor = self._get_regime_risk_factor(regime)
            now = datetime.now()

            for ticker, signal in signals.items():
                current_pos = self.positions.get(ticker, 0)
                current_price = current_prices.get(ticker, np.nan)
                volatility = volatilities.get(ticker, 0.01)  # Default small vol if missing

                if np.isnan(current_price):
                    new_positions[ticker] = current_pos
                    continue

                # Determine target position based on signal strength
                target_pos = self._calculate_target_position(signal, regime_risk_factor)

                # Apply risk limits
                max_size = self.risk_params.max_position_size / volatility
                target_pos = np.clip(target_pos, -max_size, max_size)

                # Check stop loss if we have a position
                if ticker in self.entry_prices and current_pos != 0:
                    entry_price = self.entry_prices[ticker]
                    stop_triggered = self._check_stop_loss(
                        current_pos, entry_price, current_price, volatility
                    )

                    # Check position timeout
                    entry_time = self.entry_times.get(ticker, now)
                    timeout = (now - entry_time).total_seconds() > self.risk_params.position_timeout

                    if stop_triggered or timeout:
                        target_pos = 0

                # If we're entering a new position, record entry data
                if current_pos == 0 and target_pos != 0:
                    self.entry_prices[ticker] = current_price
                    self.entry_times[ticker] = now
                    stop_distance = volatility * self.risk_params.stop_loss_std
                    self.stop_levels[ticker] = current_price * (1 - np.sign(target_pos) * stop_distance)

                # If we're closing a position, clean up tracking data
                if current_pos != 0 and target_pos == 0:
                    if ticker in self.entry_prices:
                        del self.entry_prices[ticker]
                    if ticker in self.entry_times:
                        del self.entry_times[ticker]
                    if ticker in self.stop_levels:
                        del self.stop_levels[ticker]

                new_positions[ticker] = target_pos

            # Check overall leverage
            total_exposure = sum(abs(pos * current_prices.get(ticker, 0))
                                 for ticker, pos in new_positions.items())

            if total_exposure > 0:
                leverage = total_exposure / self.risk_params.account_value
                if leverage > self.risk_params.max_leverage:
                    scale_factor = self.risk_params.max_leverage / leverage
                    new_positions = {ticker: pos * scale_factor for ticker, pos in new_positions.items()}

            self.positions = new_positions
            return new_positions

        except Exception as e:
            logging.error(f"Error updating positions: {str(e)}")
            return self.positions.copy()

    def _get_regime_risk_factor(self, regime):
        if regime == "High Vol":
            return 0.5  # Reduce risk in high volatility regimes
        elif regime == "Low Vol":
            return 1.2  # Increase risk in low volatility regimes
        else:
            return 1.0  # Normal regime

    def _calculate_target_position(self, signal, regime_factor):
        if abs(signal) < 0.2:
            return 0

        # Scale position size based on signal strength
        if abs(signal) > 0.8:
            size_factor = 1.0
        elif abs(signal) > 0.5:
            size_factor = 0.7
        else:
            size_factor = 0.3

        return np.sign(signal) * size_factor * regime_factor

    def _check_stop_loss(self, position, entry_price, current_price, volatility):
        # For long positions, stop is triggered if price falls below stop level
        if position > 0 and current_price < entry_price * (1 - volatility * self.risk_params.stop_loss_std):
            return True

        # For short positions, stop is triggered if price rises above stop level
        if position < 0 and current_price > entry_price * (1 + volatility * self.risk_params.stop_loss_std):
            return True

        return False

    def calculate_pnl(self, current_prices, previous_prices=None):
        if not previous_prices:
            return 0.0

        pnl = 0.0
        for ticker, position in self.positions.items():
            curr_price = current_prices.get(ticker, np.nan)
            prev_price = previous_prices.get(ticker, np.nan)

            if not np.isnan(curr_price) and not np.isnan(prev_price) and position != 0:
                pnl += position * (curr_price - prev_price)

        return pnl