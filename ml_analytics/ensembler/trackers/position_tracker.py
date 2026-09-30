import logging
from datetime import datetime
from typing import Dict

import numpy as np

from ml_analytics.ensembler.utils import RiskParameters


class PositionManager:
    def __init__(self, risk_params: RiskParameters):
        self.risk_params = risk_params
        self.positions = {}
        self.position_times = {}
        self.pnl_history = []
        self.trade_history = []
        self.exposure_history = []

    def calculate_position_size(self, signal: float, volatility: float, current_exposure: float,
                                regime: str) -> float:
        """Calculate position size with regime-based adjustments"""
        try:
            # Base position size from signal
            position = signal * self.risk_params.max_position_size

            # Regime-based adjustment
            regime_scalars = {
                'Low Vol': 1.2,  # More aggressive in low vol
                'Normal': 1.0,  # Normal sizing
                'High Vol': 0.8  # More conservative in high vol
            }
            regime_scalar = regime_scalars.get(regime, 1.0)
            position *= regime_scalar

            # Volatility scaling
            vol_scalar = 0.2 / volatility if volatility > 0 else 0
            position *= vol_scalar

            # Apply leverage limits
            if abs(current_exposure + position) > self.risk_params.max_leverage:
                position = np.sign(position) * (self.risk_params.max_leverage - abs(current_exposure))

            return position

        except Exception as e:
            logging.error(f"Error calculating position size: {str(e)}")
            return 0.0

    def check_stop_loss(self, ticker: str, current_price: float,
                        avg_price: float, volatility: float) -> bool:
        try:
            if ticker not in self.positions:
                return False

            price_change = (current_price - avg_price) / avg_price
            stop_level = self.risk_params.stop_loss_std * volatility

            return abs(price_change) > stop_level

        except Exception as e:
            logging.error(f"Error checking stop loss: {str(e)}")
            return False

    def calculate_portfolio_metrics(self, positions: Dict[str, float],
                                    prices: Dict[str, float]) -> Dict[str, float]:
        """Calculate portfolio risk metrics"""
        try:
            metrics = {}

            # Gross exposure
            metrics['gross_exposure'] = sum(abs(pos) for pos in positions.values())

            # Net exposure
            metrics['net_exposure'] = sum(positions.values())

            # Portfolio value
            portfolio_value = sum(pos * prices.get(ticker, 0)
                                  for ticker, pos in positions.items())
            metrics['portfolio_value'] = portfolio_value

            # Concentration
            if metrics['gross_exposure'] > 0:
                max_position = max(abs(pos) for pos in positions.values())
                metrics['concentration'] = max_position / metrics['gross_exposure']
            else:
                metrics['concentration'] = 0

            return metrics

        except Exception as e:
            logging.error(f"Error calculating portfolio metrics: {str(e)}")
            return {}

    def update_positions(self, signals: Dict[str, float],
                         prices: Dict[str, float],
                         volatilities: Dict[str, float],
                         regime: str) -> Dict[str, float]:
        """Update positions with enhanced risk management"""
        try:
            new_positions = {}
            current_exposure = sum(abs(pos) for pos in self.positions.values())

            # Calculate portfolio metrics before trade
            pre_trade_metrics = self.calculate_portfolio_metrics(self.positions, prices)

            for ticker, signal in signals.items():
                # Check position timeout
                if ticker in self.position_times:
                    time_in_position = (datetime.now() - self.position_times[ticker]).total_seconds() / 60
                    if time_in_position > self.risk_params.position_timeout:
                        logging.info(f"Position timeout for {ticker}")
                        continue

                # Calculate new position
                vol = volatilities.get(ticker, 0.2)
                new_pos = self.calculate_position_size(signal, vol, current_exposure, regime)

                # Check stop loss
                if ticker in self.positions and ticker in prices:
                    if self.check_stop_loss(ticker, prices[ticker], prices[ticker], vol):
                        logging.info(f"Stop loss triggered for {ticker}")
                        new_pos = 0

                new_positions[ticker] = new_pos

                # Record trade if position changed
                if new_pos != self.positions.get(ticker, 0):
                    trade = {
                        'timestamp': datetime.now(),
                        'ticker': ticker,
                        'old_position': self.positions.get(ticker, 0),
                        'new_position': new_pos,
                        'price': prices.get(ticker, 0),
                        'signal': signal,
                        'volatility': vol,
                        'regime': regime
                    }
                    self.trade_history.append(trade)
                    self.position_times[ticker] = datetime.now()

            # Calculate post-trade metrics
            post_trade_metrics = self.calculate_portfolio_metrics(new_positions, prices)

            # Record exposure
            self.exposure_history.append({
                'timestamp': datetime.now(),
                'gross_exposure': post_trade_metrics['gross_exposure'],
                'net_exposure': post_trade_metrics['net_exposure'],
                'regime': regime
            })

            self.positions = new_positions
            return new_positions

        except Exception as e:
            logging.error(f"Error updating positions: {str(e)}")
            return self.positions
