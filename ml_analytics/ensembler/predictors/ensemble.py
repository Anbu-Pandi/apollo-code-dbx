import logging
import traceback
from typing import Dict, List, Tuple, Optional, Union

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ml_analytics.ensembler.predictors.auto_lstm import AutoLSTMPredictor
from ml_analytics.ensembler.predictors.auto_mlp import AutoMLPPredictor
from ml_analytics.ensembler.predictors.auto_nbeatsx import AutoNBEATSxPredictor
from ml_analytics.ensembler.predictors.auto_nhits import AutoNHITSPredictor
from ml_analytics.ensembler.predictors.auto_nlinear import AutoNLinearPredictor
from ml_analytics.ensembler.predictors.auto_tft import AutoTFTPredictor
from ml_analytics.ensembler.predictors.kalman import KalmanPredictor
from ml_analytics.ensembler.predictors.neural import NeuralPredictor
from ml_analytics.ensembler.predictors.elastic_net import ElasticNetPredictor
from ml_analytics.ensembler.predictors.xgb import XGBPredictor


class EnsemblePredictor:
    def __init__(self, column_names: Optional[List[str]] = None,
                 predictors: Optional[List[str]] = None, lookback: int = 5):
        self.base_columns = column_names
        self.lookback = lookback
        self.selected_predictors = predictors

        self.feature_names = []
        self._is_fitted = False
        self.predictors = None
        self.scalers = {name: StandardScaler() for name in self.selected_predictors}

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        train_split = 0.9
        if self.predictors is None:
            self.predictors = {
                'xgb': XGBPredictor(train_split=train_split),
                'neural': NeuralPredictor(input_size=X.shape[1], lookback=self.lookback, train_split=train_split),
                'elasticnet': ElasticNetPredictor(alpha=0.5, train_split=train_split),
                'kalman': KalmanPredictor(X.shape[1], r=0.01, q=0.01),
                'autonlinear': AutoNLinearPredictor(self.feature_names[1:], input_size=X.shape[1],
                                                    train_split=train_split),
                'autolstm': AutoLSTMPredictor(self.feature_names[1:], input_size=X.shape[1], train_split=train_split, lookback=self.lookback),
                'automlp': AutoMLPPredictor(self.feature_names[1:], input_size=X.shape[1], train_split=train_split, lookback=self.lookback),
                'autonbeatsx': AutoNBEATSxPredictor(self.feature_names[1:], input_size=X.shape[1],
                                                   train_split=train_split, lookback=self.lookback),
                'autonhits': AutoNHITSPredictor(self.feature_names[1:], input_size=X.shape[1], train_split=train_split, lookback=self.lookback),
                'autotft': AutoTFTPredictor(self.feature_names[1:], input_size=X.shape[1], train_split=train_split, lookback=self.lookback),
            }

            if self.selected_predictors is not None:
                self.predictors = {name: self.predictors[name] for name in self.selected_predictors if
                                   name in self.predictors}

        try:
            logging.info(f"Fitting ensemble with features shape: {X.shape}, y shape: {y.shape}")

            if len(y) != len(X):
                logging.error(f"Target length {len(y)} doesn't match features length {len(X)}")
                return

            for name, predictor in self.predictors.items():
                try:
                    logging.info(f"Fitting {name} predictor...")
                    X_scaled = self.scalers[name].fit_transform(X)
                    diagnostics = True
                    if name == 'neural':
                        predictor.fit(X_scaled, y.reshape(-1), diagnostics=diagnostics)
                    elif name.startswith('auto'):
                        predictor.fit(X_scaled, y.reshape(-1), diagnostics=diagnostics)
                    elif name == 'kalman':
                        for i in range(1, len(X_scaled)):
                            diagnostics = False
                            if i == len(X_scaled) - 1:
                                diagnostics = True
                            predictor.fit(X_scaled[i:i + 1], y[i:i + 1], diagnostics=diagnostics)
                    elif name == 'elasticnet':
                        predictor.fit(X_scaled, y.reshape(-1), diagnostics=diagnostics)
                    elif name == 'xgb':
                        predictor.fit(X_scaled, y, diagnostics=diagnostics)
                    else:
                        raise ValueError(f"Unknown predictor: {name}")

                    predictor._is_fitted = True
                    logging.info(f"Successfully fitted {name} predictor")

                except Exception as e:
                    logging.error(f"Error fitting {name} predictor: {str(e)}")
                    logging.error(f"Stack trace: {traceback.format_exc()}")
                    predictor._is_fitted = False

            self._is_fitted = True
            logging.info("Completed ensemble fitting")

        except Exception as e:
            logging.error(f"Error in ensemble fitting: {str(e)}")
            logging.error(f"Stack trace: {traceback.format_exc()}")
            self._is_fitted = False

    def predict(self, X: np.ndarray) -> Dict[str, np.ndarray]:
        predictions = {}

        try:
            if len(X) == 0:
                return {name: np.array([np.nan]) for name in self.predictors.keys()}

            logging.debug(f"Feature shape for prediction: {X.shape}")

            for name, predictor in self.predictors.items():
                try:
                    if not predictor.is_fitted:
                        logging.warning(f"{name} predictor not fitted")
                        predictions[name] = np.array([np.nan])
                        continue

                    X_scaled = self.scalers[name].transform(X)

                    if name == 'kalman':
                        pred = predictor.predict(X_scaled[-1:])
                        predictions[name] = np.array([float(pred)])
                    else:
                        pred = predictor.predict(X_scaled, look_back=self.lookback)
                        if isinstance(pred, np.ndarray):
                            if len(pred.shape) > 1:
                                pred = pred[-1]
                            if len(pred) > 0:
                                pred = pred[-1]
                        predictions[name] = np.array([float(pred)])

                    logging.info(f"{name} prediction: {predictions[name]}")

                except Exception as e:
                    logging.error(f"Error getting {name} prediction: {str(e)}")
                    predictions[name] = np.array([np.nan])

            return predictions

        except Exception as e:
            logging.error(f"Error in ensemble prediction: {str(e)}")
            logging.error(f"Stack trace: {traceback.format_exc()}")
            return {name: np.array([np.nan]) for name in self.predictors.keys()}

    def get_model_weights(self, regime: str) -> Dict[str, float]:
        weights = {
            'Low Vol': {'xgb': 0.25, 'neural': 0.25, 'elasticnet': 0.25, 'kalman': 0.1, 'autolstm': 0.15},
            'Normal': {'xgb': 0.3, 'neural': 0.2, 'elasticnet': 0.2, 'kalman': 0.1, 'autolstm': 0.2},
            'High Vol': {'xgb': 0.2, 'neural': 0.2, 'elasticnet': 0.3, 'kalman': 0.1, 'autolstm': 0.2},
        }
        return weights.get(regime, weights['Normal'])

    def get_weighted_prediction(self, X: np.ndarray, regime: str) -> Tuple[float, Dict[str, float]]:
        try:
            predictions = self.predict(X)
            weights = self.get_model_weights(regime)

            current_preds = {}
            for name, pred in predictions.items():
                if name != 'ensemble':
                    try:
                        if pred is not None and len(pred) > 0 and not np.any(np.isnan(pred)):
                            current_preds[name] = float(pred[0])
                        else:
                            current_preds[name] = np.nan
                    except (IndexError, TypeError):
                        current_preds[name] = np.nan

            weighted_pred = 0.0
            total_weight = 0.0

            for model, cpred in current_preds.items():
                wt = weights.get(model, 0)
                if not np.isnan(cpred):
                    weighted_pred += cpred * wt
                    total_weight += wt

            if total_weight > 0:
                weighted_pred /= total_weight
            else:
                weighted_pred = np.nan

            current_preds['ensemble'] = weighted_pred
            return weighted_pred, current_preds

        except Exception as e:
            logging.error(f"Error in weighted prediction: {str(e)}")
            return np.nan, {name: np.nan for name in self.predictors.keys()}

    def clean_data(self, data: np.ndarray) -> np.ndarray:
        data = np.where(np.isinf(data), np.nan, data)

        for col in range(data.shape[1]):
            col_data = data[:, col]
            col_median = np.nanmedian(col_data)
            col_std = np.nanstd(col_data)

            col_data = np.where(np.isnan(col_data), col_median, col_data)

            lower_bound = col_median - 5 * col_std
            upper_bound = col_median + 5 * col_std
            col_data = np.clip(col_data, lower_bound, upper_bound)

            data[:, col] = col_data

        return data

    def prepare_features(self, data: Union[pd.DataFrame, np.ndarray]) -> Tuple[
        np.ndarray, List[str]]:
        try:
            if isinstance(data, np.ndarray):
                expected_features = self._calculate_expected_features()
                if data.shape[1] == expected_features:
                    cleaned_data = self.clean_data(data)
                    return cleaned_data, self.feature_names

            if isinstance(data, np.ndarray):
                if self.base_columns is None:
                    raise ValueError("Column names must be provided when using numpy array input")
                if data.shape[1] != len(self.base_columns):
                    raise ValueError(
                        f"Input data has {data.shape[1]} columns but {len(self.base_columns)} names provided")
                df = pd.DataFrame(data, columns=self.base_columns)
            else:
                df = data.copy()
                if self.base_columns is None:
                    self.base_columns = df.columns.tolist()
                    logging.info(f"Initialized base columns: {self.base_columns}")

            missing_cols = set(self.base_columns) - set(df.columns)
            if missing_cols:
                logging.error(f"Missing columns: {missing_cols}")
                return np.empty((0, self._calculate_expected_features())), []

            df = df[self.base_columns]

            features = []
            feature_names = []

            # First column is Y feature, add it directly without deriving
            y_col = self.base_columns[0]
            series = df[y_col].replace([np.inf, -np.inf], np.nan)
            features.append(series.fillna(method='ffill').fillna(method='bfill').values)
            feature_names.append(f"{y_col}")

            # Process other columns (starting from second column)
            for col in self.base_columns[1:]:
                try:
                    series = df[col].replace([np.inf, -np.inf], np.nan)

                    # Basic return
                    features.append(series.fillna(method='ffill').fillna(method='bfill').values)
                    feature_names.append(f"{col}_return")

                    # Multiple rolling window statistics
                    for window in [10]:
                        roll = df[col].rolling(window, min_periods=1)

                        # Basic statistics
                        rolling_mean = roll.mean().fillna(method='bfill')
                        rolling_std = roll.std().fillna(method='bfill')
                        rolling_min = roll.min().fillna(method='bfill')
                        rolling_max = roll.max().fillna(method='bfill')

                        # Percentiles
                        rolling_q25 = roll.quantile(0.25).fillna(method='bfill')
                        rolling_q75 = roll.quantile(0.75).fillna(method='bfill')
                        rolling_median = roll.median().fillna(method='bfill')

                        # Range and IQR
                        rolling_range = (rolling_max - rolling_min)
                        rolling_iqr = (rolling_q75 - rolling_q25)

                        # Position relative to range
                        current_value = series.fillna(method='ffill').fillna(method='bfill')
                        rolling_position = (current_value - rolling_min) / (rolling_range + 1e-8)
                        rolling_z_score = (current_value - rolling_mean) / (rolling_std + 1e-8)

                        # Add features
                        features.extend([
                            rolling_mean.values,
                            rolling_std.values,
                            rolling_min.values,
                            rolling_max.values,
                            rolling_median.values,
                            rolling_iqr.values,
                            rolling_position.values,
                            rolling_z_score.values
                        ])

                        feature_names.extend([
                            f"{col}_mean_{window}",
                            f"{col}_std_{window}",
                            f"{col}_min_{window}",
                            f"{col}_max_{window}",
                            f"{col}_median_{window}",
                            f"{col}_iqr_{window}",
                            f"{col}_position_{window}",
                            f"{col}_zscore_{window}"
                        ])

                    # Exponential moving averages (captures different time scales)
                    for span in [20]:
                        ema = series.ewm(span=span).mean().fillna(method='bfill')
                        features.append(ema.values)
                        feature_names.append(f"{col}_ema_{span}")

                    # Rate of change features
                    for period in [5, 10]:
                        roc = series.pct_change(period).fillna(0)
                        features.append(roc.values)
                        feature_names.append(f"{col}_roc_{period}")

                except Exception as e:
                    logging.error(f"Error processing column {col}: {str(e)}")
                    return np.empty((0, self._calculate_expected_features())), []

            # Assemble final feature matrix
            feature_arrays = [f.reshape(-1, 1) if len(f.shape) == 1 else f for f in features]
            feature_matrix = np.hstack(feature_arrays)
            feature_matrix = self.clean_data(feature_matrix)
            self.feature_names = feature_names

            logging.info(f"Generated {len(feature_names)} features from {len(self.base_columns)} base columns")
            logging.debug(f"Generated features shape: {feature_matrix.shape}")
            return feature_matrix, feature_names

        except Exception as e:
            logging.error(f"Error preparing features: {str(e)}")
            stack_trace = traceback.format_exc()
            logging.error(f"Stack trace: {stack_trace}")
            return np.empty((0, 1)), []

    def _calculate_expected_features(self) -> int:
        """Calculate expected number of features based on base columns"""
        if not self.base_columns:
            return 1

        # Y column: 1 feature (unchanged)
        y_features = 1

        features_per_column = 12 + (1 if len(self.base_columns) > 2 else 0)

        other_features = (len(self.base_columns) - 1) * features_per_column

        return y_features + other_features

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted and all(predictor.is_fitted for predictor in self.predictors.values())
