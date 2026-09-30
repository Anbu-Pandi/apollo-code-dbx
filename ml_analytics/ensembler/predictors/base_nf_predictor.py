import logging

import numpy as np
import optuna
import pandas as pd
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from utilsforecast.evaluation import evaluate
from utilsforecast.losses import rmse, smape, mae

from ml_analytics.ensembler.predictors.base_predictor import BasePredictor
from ml_analytics.ensembler.predictors.visualizer import Visualizer


class BaseNFPredictor(BasePredictor):
    def __init__(self, feature_names, input_size: int = 48, lookback: int = 5, train_split: float = 0.8, seed=42):
        super(BaseNFPredictor, self).__init__()
        self.input_size = input_size
        self.lookback = lookback
        self.train_split = train_split
        self.h = 1
        self.feature_names = feature_names
        self.pred_col_name = 'auto_tuned'
        self.seed = seed
        self.visualizer = Visualizer(self.__class__.__name__.replace('Predictor', ''))
        self.backend = 'optuna'
        self.search_alg = optuna.samplers.TPESampler(seed=self.seed)

        self.cpu_kwargs = dict(
            accelerator='cpu',
            enable_progress_bar=True,
            logger=True,
            enable_checkpointing=False,
        )

        self.target_mean = 0.0
        self.target_std = 1.0
        self._is_fitted = False
        self.train_df = None
        self.valid_df = None
        self.nf = None
        self.auto_model = None

    def _prepare_data(self, X: np.ndarray, y: np.ndarray) -> pd.DataFrame:
        dates = pd.date_range(start='2020-01-01', periods=len(y), freq='D')
        df = pd.DataFrame({
            'unique_id': 'series_1',
            'ds': dates,
            'y': y
        })

        if X.ndim > 1 and X.shape[1] > 1:
            for i, x in enumerate(self.feature_names):
                df[x] = X[:, i]

        return df

    def predict(self, X: np.ndarray, **kwargs) -> np.ndarray:
        if not self._is_fitted:
            logging.warning("Model not fitted, returning target mean")
            return np.array([self.target_mean])

        try:
            X = X[[-1]]
            futr_df = self.nf.make_future_dataframe()
            futr_df = pd.concat([futr_df] * X.shape[0], axis=0).reset_index(drop=True)
            futr_df['ds'] = pd.date_range(futr_df['ds'].min(), periods=X.shape[0], freq='D')
            futr_df = pd.concat([futr_df, pd.DataFrame(X, columns=self.feature_names)], axis=1)
            predictions = self.nf.predict(futr_df=futr_df, random_seed=self.seed)
            prediction_value = predictions[self.pred_col_name].iloc[-1]

            logging.info(f"{self.__class__.__name__} prediction: {prediction_value:.6f}")
            return np.array([prediction_value])

        except Exception as e:
            logging.error(f"Prediction error: {str(e)}")
            return np.array([self.target_mean])

    def _run_diagnostics(self):
        logging.info(f"Running {self.__class__.__name__} model diagnostics")
        try:
            train_predictions = self.nf.predict_insample()
            train_eval_df = self.train_df.merge(train_predictions, on=['unique_id', 'ds'], how='left',
                                                suffixes=('', '_pred')) \
                .dropna(subset=['y', self.pred_col_name], how='any')

            train_actual = train_eval_df['y'].values
            train_predicted = train_eval_df[self.pred_col_name].values

            train_mse = mean_squared_error(train_actual, train_predicted)
            train_mae = mean_absolute_error(train_actual, train_predicted)
            train_r2 = r2_score(train_actual, train_predicted)

            train_eval_metrics = evaluate(
                train_eval_df,
                metrics=[mae, rmse, smape],
                models=[self.pred_col_name]
            )

            logging.info(f"Training Set - MSE: {train_mse:.6f}, MAE: {train_mae:.6f}, R²: {train_r2:.6f}")
            logging.info(
                f"Training - MAE: {train_eval_metrics.set_index('metric').loc['mae'].iloc[1]:.6f}, "
                f"RMSE: {train_eval_metrics.set_index('metric').loc['rmse'].iloc[1]:.6f}, "
                f"SMAPE: {train_eval_metrics.set_index('metric').loc['smape'].iloc[1]:.6f}"
            )

            if self.valid_df is not None and len(self.valid_df) > 0:
                val_futr_df = self.valid_df[['unique_id', 'ds'] + self.feature_names].copy()

                val_predictions = []
                for i, _df in val_futr_df.assign(ds=val_futr_df.iloc[0]['ds']).iterrows():
                    _df = _df.to_frame().T
                    _df['ds'] = pd.to_datetime(_df['ds'])
                    val_predictions.append(self.nf.predict(futr_df=_df))

                val_predictions = pd.concat(val_predictions, ignore_index=True)
                val_predictions['ds'] = val_futr_df['ds'].values
                val_eval_df = self.valid_df.merge(val_predictions, on=['unique_id', 'ds'], how='left',
                                                  suffixes=('', '_pred')) \
                    .dropna(subset=['y', self.pred_col_name], how='any')

                if len(val_eval_df) > 0:
                    val_actual = val_eval_df['y'].values
                    val_predicted = val_eval_df[self.pred_col_name].values

                    val_mse = mean_squared_error(val_actual, val_predicted)
                    val_mae = mean_absolute_error(val_actual, val_predicted)
                    val_r2 = r2_score(val_actual, val_predicted)

                    val_eval_metrics = evaluate(
                        val_eval_df,
                        metrics=[mae, rmse, smape],
                        models=[self.pred_col_name]
                    )

                    logging.info(f"Validation Set - MSE: {val_mse:.6f}, MAE: {val_mae:.6f}, R²: {val_r2:.6f}")
                    logging.info(
                        f"Validation - MAE: {val_eval_metrics.set_index('metric').loc['mae'].iloc[1]:.6f}, "
                        f"RMSE: {val_eval_metrics.set_index('metric').loc['rmse'].iloc[1]:.6f}, "
                        f"SMAPE: {val_eval_metrics.set_index('metric').loc['smape'].iloc[1]:.6f}"
                    )

                    self._create_diagnostic_plots(
                        train_actual, train_predicted, train_mse, train_mae, train_r2,
                        val_actual, val_predicted, val_mse, val_mae, val_r2
                    )
                else:
                    logging.warning("No valid predictions on validation set, creating plots with training data only")
                    self._create_diagnostic_plots(train_actual, train_predicted, train_mse, train_mae, train_r2)
            else:
                logging.warning("No validation set available, creating plots with training data only")
                self._create_diagnostic_plots(train_actual, train_predicted, train_mse, train_mae, train_r2)

        except Exception as e:
            logging.error(f"Diagnostics error: {str(e)}")

        logging.info(f"{self.__class__.__name__} diagnostics complete")

    def _create_diagnostic_plots(self, train_actual, train_predicted, train_mse, train_mae, train_r2,
                                 val_actual=None, val_predicted=None, val_mse=None, val_mae=None, val_r2=None):

        has_validation = val_actual is not None and val_predicted is not None
        train_indices = np.arange(len(train_actual))
        if has_validation:
            val_indices = np.arange(len(train_actual), len(train_actual) + len(val_actual))

        # Plot predictions
        self.visualizer.plot_predictions(
            train_indices, train_actual, train_predicted,
            val_indices, val_actual, val_predicted
        )

        # Plot extreme points
        self.visualizer.plot_extreme_points(
            train_actual,
            train_predicted
        )

        study = self.nf.models[0].results
        self.visualizer.plot_param_importance(study)
        self.visualizer.plot_hyperparameters(study.best_params)

        # Plot loss curves if available
        # self.visualizer.plot_losses(train_losses, val_losses)

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = 100, batch_size: int = 16, diagnostics: bool = True):
        if len(X) < self.input_size + self.h:
            logging.warning(f"Not enough data points. Need at least {self.input_size + self.h}, got {len(X)}")
            return

        self.target_mean = float(np.mean(y))
        self.target_std = float(np.std(y))
        logging.info(f"Target stats: mean={self.target_mean:.4f}, std={self.target_std:.4f}")

        self.train_df = self._prepare_data(X, y)

        val_size = int(len(self.train_df) * (1 - self.train_split))
        self.valid_df = self.train_df.groupby('unique_id').tail(val_size)
        train_df_split = self.train_df.drop(self.valid_df.index)

        model_name = self.__class__.__name__.replace('Predictor', '')
        logging.info(
            f"Training {model_name} with {len(train_df_split)} samples, validation with {len(self.valid_df)} samples")

        try:
            self.nf.fit(df=train_df_split, val_size=val_size)
            self._is_fitted = True
            logging.info(f"{model_name} training completed successfully")

            if diagnostics:
                self._run_diagnostics()

        except Exception as e:
            logging.error(f"Error during {model_name} training: {str(e)}")
            raise

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted
