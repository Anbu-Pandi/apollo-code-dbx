import logging
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler


class MarketRegimeDetector:
    """Detect market regimes using Hidden Markov Model with improved group handling"""

    def __init__(self, n_regimes: int = 3, window_size: int = 250, group_id: tuple = None):
        self.n_regimes = n_regimes
        self.model = hmm.GaussianHMM(
            n_components=n_regimes,
            covariance_type="full",
            n_iter=100,
            random_state=42,
            init_params="")

        # Initialize HMM parameters explicitly
        self.model.startprob_ = np.ones(n_regimes) / n_regimes
        self.model.transmat_ = np.ones((n_regimes, n_regimes)) / n_regimes

        # Initialize means and covariances with reasonable values
        self.model.means_ = np.zeros((n_regimes, 6))  # 6 features
        self.model.covars_ = np.array([np.eye(6) * (i + 1) for i in range(n_regimes)])

        self.regime_labels = ['Low Vol', 'Normal', 'High Vol']
        self.is_fitted = False
        self.scaler = StandardScaler()
        self.regime_mapping = {i: i for i in range(n_regimes)}  # Initialize mapping

        self.window_size = window_size
        self.group_id = group_id  # Store the group identifier

        # Store historical regime data for this group
        self.regime_history = []

    def prepare_regime_features(self, returns_df: pd.DataFrame) -> np.ndarray:
        """Prepare features for regime detection with improved error handling"""
        try:
            # Handle empty or invalid input
            if returns_df.empty:
                logging.warning(f"Empty DataFrame provided for regime detection {self.group_id or 'global'}")
                return np.array([])

            # Adjust window sizes based on data availability
            min_window = min(len(returns_df), self.window_size)
            short_window = max(5, int(min_window / 10))
            med_window = max(10, int(min_window / 4))
            long_window = max(20, int(min_window / 2))

            # Volatility features with NaN handling
            vol_short = returns_df.rolling(window=short_window, min_periods=1).std().fillna(method='bfill')
            vol_med = returns_df.rolling(window=med_window, min_periods=1).std().fillna(method='bfill')
            vol_long = returns_df.rolling(window=long_window, min_periods=1).std().fillna(method='bfill')

            # Trend features
            trend_short = returns_df.rolling(window=max(3, min(5, short_window)), min_periods=1).mean().fillna(
                method='bfill')
            trend_med = returns_df.rolling(window=max(5, min(20, med_window)), min_periods=1).mean().fillna(
                method='bfill')

            # Correlation features - handle small dataframes
            min_corr_window = len(returns_df) #min(20, max(3, int(len(returns_df) / 3)))
            if len(returns_df.columns) > 1:  # Only calculate correlation if we have multiple columns
                corr_matrices = []
                corr_matrices.append(0)
                for i in range(len(returns_df) - min_corr_window + 1):
                    window_data = returns_df.iloc[i:i + min_corr_window]
                    if len(window_data) > 1:
                        corr_matrix = window_data.corr()
                        # Extract upper triangle without diagonal
                        mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
                        corr_values = corr_matrix.where(mask).stack().mean()
                        corr_matrices.append(corr_values)
                    else:
                        corr_matrices.append(0)

                # Pad the beginning if needed
                if len(corr_matrices) < len(returns_df):
                    padding = [corr_matrices[0]] * (len(returns_df) - len(corr_matrices))
                    corr_matrices = padding + corr_matrices

                avg_corr = pd.Series(corr_matrices, index=returns_df.index)
            else:
                # If only one column, set correlation to 0
                avg_corr = pd.Series(0, index=returns_df.index)

            # Combine features
            features = np.column_stack([
                vol_short.mean(axis=1),
                vol_med.mean(axis=1),
                vol_long.mean(axis=1),
                trend_short.mean(axis=1),
                trend_med.mean(axis=1),
                avg_corr
            ])

            # Handle any remaining NaN values
            features = np.nan_to_num(features, 0)

            return features

        except Exception as e:
            logging.error(f"Error preparing regime features for {self.group_id or 'global'}: {str(e)}")
            return np.array([])

    def fit(self, features: np.ndarray):
        """Fit the HMM model with improved validation"""
        try:
            if len(features) < 2:
                logging.warning(f"Insufficient data for regime detection for {self.group_id or 'global'}")
                return

            if np.any(np.isnan(features)):
                logging.warning(
                    f"NaN values detected in features for {self.group_id or 'global'}, replacing with zeros")
                features = np.nan_to_num(features, 0)

            # Ensure features have the right scale for stable HMM fitting
            self.scaler.fit(features)
            features_scaled = self.scaler.transform(features)

            # Fit HMM
            self.model.fit(features_scaled)
            self.is_fitted = True

            # Analyze regime characteristics and map them to labels
            regimes = self.model.predict(features_scaled)
            regime_stats = {}

            for i in range(self.n_regimes):
                regime_mask = (regimes == i)
                if np.any(regime_mask):  # Check if regime has any samples
                    regime_data = features[regime_mask]
                    regime_stats[i] = {
                        'volatility': np.mean(regime_data[:, 0]),  # short-term vol
                        'trend': np.mean(regime_data[:, 3]),  # short-term trend
                        'correlation': np.mean(regime_data[:, -1]),  # correlation
                        'samples': np.sum(regime_mask)  # number of samples in regime
                    }

            # Sort regimes by volatility to assign labels
            sorted_regimes = sorted(regime_stats.items(), key=lambda x: x[1]['volatility'])
            self.regime_mapping = {reg[0]: i for i, reg in enumerate(sorted_regimes)}

            logging.info(f"Successfully fitted regime detection model for {self.group_id or 'global'}")
            logging.debug(f"Regime characteristics for {self.group_id or 'global'}: {regime_stats}")

        except Exception as e:
            logging.error(f"Error fitting regime detection model for {self.group_id or 'global'}: {str(e)}")

    def predict_regime(self, features: np.ndarray) -> Tuple[int, str, Dict]:
        """Predict current market regime with characteristics"""
        if not self.is_fitted or len(features) == 0:
            return 1, 'Normal', {'volatility': 0, 'trend': 0, 'correlation': 0}

        try:
            # Scale features
            features_scaled = self.scaler.transform(features)

            # Get regime prediction
            regime = self.model.predict(features_scaled)[-1]

            # Map raw regime to ordered regime (by volatility)
            mapped_regime = self.regime_mapping.get(regime, 1)  # Default to "Normal" if mapping fails

            # Get regime characteristics from the latest data point
            regime_chars = {
                'volatility': features[-1, 0],  # current short-term vol
                'trend': features[-1, 3],  # current short-term trend
                'correlation': features[-1, -1]  # current correlation
            }

            # Store in history
            self.regime_history.append({
                'regime': mapped_regime,
                'label': self.regime_labels[mapped_regime],
                'characteristics': regime_chars
            })

            # Limit history size
            if len(self.regime_history) > 100:
                self.regime_history = self.regime_history[-100:]

            return mapped_regime, self.regime_labels[mapped_regime], regime_chars

        except Exception as e:
            logging.error(f"Error predicting regime for {self.group_id or 'global'}: {str(e)}")
            return 1, 'Normal', {'volatility': 0, 'trend': 0, 'correlation': 0}

    def get_regime_stability(self) -> float:
        """Calculate how stable the regime has been recently"""
        if len(self.regime_history) < 5:
            return 1.0  # Assume stable if not enough history

        # Count regime changes in recent history
        recent_regimes = [h['regime'] for h in self.regime_history[-10:]]
        changes = sum(1 for i in range(1, len(recent_regimes)) if recent_regimes[i] != recent_regimes[i - 1])

        # Return stability score (1 = very stable, 0 = constantly changing)
        return 1.0 - (changes / (len(recent_regimes) - 1))
