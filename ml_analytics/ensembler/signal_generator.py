import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from ml_analytics.ensembler.predictors.ensemble import EnsemblePredictor
from ml_analytics.ensembler.predictors.kalman import KalmanFilter
from ml_analytics.ensembler.utils import SignalParameters


class SignalGenerator:
    REGIME_WEIGHTS = {
        'Low Vol': {'statistical': 0.2, 'kalman': 0.2, 'predictive': 0.6},
        'Normal': {'statistical': 0.3, 'kalman': 0.3, 'predictive': 0.4},
        'High Vol': {'statistical': 0.4, 'kalman': 0.4, 'predictive': 0.2}
    }

    def __init__(self, y_feature, x_features, signal_params=None,
                 predictors: Optional[List[str]] = None):
        self.y_feature = y_feature
        self.x_features = x_features
        self.all_features = [y_feature] + x_features
        self.signal_params = signal_params or SignalParameters()

        self.predictor = EnsemblePredictor(
            column_names=self.all_features,
            predictors=predictors,
            lookback=self.signal_params.feature_lookback_window,
        )

        self.kalman_filters = {}

    def _get_or_create_kalman_filter(self, id_val):
        if id_val not in self.kalman_filters:
            self.kalman_filters[id_val] = KalmanFilter(
                n_states=len(self.x_features),
                r=self.signal_params.kalman_r,
                q=self.signal_params.kalman_q
            )
        return self.kalman_filters[id_val]

    def calculate_rolling_stats(self, y_returns, x_returns):
        try:
            window = min(100, len(y_returns) if isinstance(y_returns, pd.Series) else 100)

            if not isinstance(y_returns, pd.Series):
                y_returns = pd.Series([float(y_returns)] * (window + 1))
            if not isinstance(x_returns, pd.Series):
                x_returns = pd.Series([float(x_returns)] * (window + 1))

            common_index = y_returns.index.intersection(x_returns.index)
            y_returns = y_returns[common_index]
            x_returns = x_returns[common_index]

            if len(y_returns) < 2 or len(x_returns) < 2:
                return {'beta': 0.0, 'zscore': 0.0, 'correlation': 0.0, 'spread_mean': 0.0, 'spread_std': 0.0}

            y_vals = y_returns.values
            x_vals = x_returns.values

            y_mean = pd.Series(y_vals).rolling(window, min_periods=1).mean().fillna(0).values
            x_mean = pd.Series(x_vals).rolling(window, min_periods=1).mean().fillna(0).values

            cov_vals = np.zeros(len(y_vals))
            var_vals = np.zeros(len(x_vals))

            for i in range(len(y_vals)):
                start_idx = max(0, i - window + 1)
                y_window = y_vals[start_idx:i + 1]
                x_window = x_vals[start_idx:i + 1]
                if len(y_window) > 1:
                    y_win_mean = y_mean[i]
                    x_win_mean = x_mean[i]
                    cov_vals[i] = np.mean((y_window - y_win_mean) * (x_window - x_win_mean))
                    var_vals[i] = np.mean((x_window - x_win_mean) ** 2)

            beta_vals = np.zeros_like(cov_vals)
            valid_mask = var_vals != 0
            beta_vals[valid_mask] = cov_vals[valid_mask] / var_vals[valid_mask]

            spread = y_vals - (beta_vals * x_vals)
            spread_series = pd.Series(spread)
            spread_mean = spread_series.rolling(window, min_periods=1).mean().fillna(0).values
            spread_std = spread_series.rolling(window, min_periods=1).std().fillna(0).values

            zscore_vals = np.zeros_like(spread)
            std_mask = spread_std != 0
            zscore_vals[std_mask] = (spread[std_mask] - spread_mean[std_mask]) / spread_std[std_mask]

            correlation = pd.Series(y_vals).rolling(window, min_periods=1).corr(pd.Series(x_vals)).fillna(0).values

            return {
                'beta': float(beta_vals[-1]),
                'zscore': float(zscore_vals[-1]),
                'correlation': float(correlation[-1]),
                'spread_mean': float(spread_mean[-1]),
                'spread_std': float(spread_std[-1])
            }

        except Exception as e:
            logging.error(f"Error calculating rolling stats: {str(e)}")
            return {'beta': 0.0, 'zscore': 0.0, 'correlation': 0.0, 'spread_mean': 0.0, 'spread_std': 0.0}

    def _calculate_ticker_signal(self, current_return, rolling_stats, kalman_beta, weighted_pred, regime):
        try:
            weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS['Normal'])

            min_corr = self.signal_params.min_correlation
            corr = rolling_stats.get('correlation', 0)

            corr_strength = min(1.0, abs(corr) / min_corr) if min_corr > 0 else 1.0

            if abs(corr) > min_corr:
                stat_signal = -np.sign(rolling_stats.get('zscore', 0)) * corr_strength
                kalman_signal = -np.sign(kalman_beta) * corr_strength
            else:
                stat_signal = 0
                kalman_signal = 0

            try:
                current_return = float(current_return)
                weighted_pred = float(weighted_pred)
            except (TypeError, ValueError):
                current_return = 0.0
                weighted_pred = 0.0

            pred_diff = weighted_pred - current_return
            pred_signal = -np.sign(pred_diff)
            pred_signal_scaled = pred_signal * min(1.0, abs(pred_diff) * 10)

            combined_signal = (
                    weights['statistical'] * stat_signal +
                    weights['kalman'] * kalman_signal +
                    weights['predictive'] * pred_signal_scaled
            )

            if abs(combined_signal) < 0.2:
                combined_signal = 0

            return float(combined_signal)

        except Exception as e:
            logging.error(f"Error calculating ticker signal: {str(e)}")
            return 0.0

    def generate_signals(self, returns_df, regime, group_id=None):
        try:
            signals = {}
            stats = {
                'model_predictions': {},
                'volatilities': {},
                'current_prices': {},
                'regime': regime,
            }

            if returns_df.empty:
                return signals, stats

            features, feature_names = self.predictor.prepare_features(returns_df)

            if len(features) > 0:
                weighted_pred, individual_preds = self.predictor.get_weighted_prediction(
                    features[-self.signal_params.feature_lookback_window:, 1:],  # TODO: hardcoded to -1 as last row predicted
                    regime
                )

                stats['model_predictions'][self.y_feature] = individual_preds
                stats['volatilities'] = returns_df.std().to_dict()

                for ticker in self.all_features:
                    if ticker in returns_df.columns and len(returns_df[ticker]) > 0:
                        stats['current_prices'][ticker] = returns_df[ticker].iloc[-1]

                y_returns = returns_df[self.y_feature]

                regime_weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS['Normal'])
                stats['regime_weights'] = regime_weights

                kalman_filter = self._get_or_create_kalman_filter(group_id or 'default')

                for x_idx, x_ticker in enumerate(self.x_features):
                    try:
                        if x_ticker in returns_df.columns:
                            x_returns = returns_df[x_ticker]
                            current_y_return = float(y_returns.iloc[-1])

                            rolling_stats = self.calculate_rolling_stats(y_returns, x_returns)
                            kalman_beta = kalman_filter.state[x_idx]

                            signal = self._calculate_ticker_signal(
                                current_y_return,
                                rolling_stats,
                                kalman_beta,
                                weighted_pred,
                                regime
                            )

                            signals[x_ticker] = signal
                            stats[f"{x_ticker}_stats"] = {
                                **rolling_stats,
                                'signal': signal,
                                'kalman_beta': kalman_beta
                            }

                    except Exception as e:
                        logging.error(f"Error processing ticker {x_ticker}: {str(e)}")
                        signals[x_ticker] = 0.0
                        stats[f"{x_ticker}_stats"] = {'signal': 0.0}

                if signals:
                    # Calculate the y signal as the negative sum of all x signals
                    # This ensures market neutrality
                    y_signal = -sum(signals.values())
                    signals[self.y_feature] = y_signal
                    stats[f"{self.y_feature}_stats"] = {'signal': y_signal}

            return signals, stats

        except Exception as e:
            logging.error(f"Error generating signals: {str(e)}")
            return {}, {}

    def train_models(self, data):
        try:
            if data.empty:
                logging.warning("No data available for training")
                return False

            features, feature_names = self.predictor.prepare_features(data)

            if len(features) <= self.signal_params.ml_window + 20:
                logging.warning(f"Insufficient data for training: {len(features)} samples")
                # return False

            self.predictor.fit(features[:, 1:], features[:, 0])
            return True

        except Exception as e:
            logging.error(f"Error training models: {str(e)}")
            return False
