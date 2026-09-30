import logging

import matplotlib.pyplot as plt
import numpy as np
import xgboost as xgb
from scipy.stats import uniform, randint
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import RandomizedSearchCV
from sklearn.preprocessing import StandardScaler

from ml_analytics.ensembler.predictors.base_predictor import BasePredictor


class XGBPredictor(BasePredictor):
    def __init__(self, train_split: float = 0.8):
        self.model = None
        self.best_params = None
        self.scaler = StandardScaler()
        self.train_split = train_split
        self._is_fitted = False
        self.train_indices = None
        self.val_indices = None
        self._set_default_params()

    def _set_default_params(self):
        self.model = xgb.XGBRegressor(
            objective='reg:squarederror',
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            min_child_weight=3,
            reg_alpha=0.2,
            reg_lambda=3,
            gamma=0,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method='hist',
            random_state=42,
        )
        return self.model.get_params()

    def _get_param_search_space(self):
        param_space = {
            'n_estimators': randint(100, 300),
            'learning_rate': uniform(0.01, 0.2),
            'max_depth': randint(3, 8),
            'min_child_weight': randint(1, 5),
            'reg_alpha': uniform(0, 0.5),
            'reg_lambda': uniform(1, 3),
            'subsample': uniform(0.7, 0.3),
            'colsample_bytree': uniform(0.7, 0.3),
        }
        return param_space

    def _hyperparameter_optimize(self, X: np.ndarray, y: np.ndarray, n_iter: int = 20):
        logging.info("Starting XGBoost hyperparameter optimization")
        param_space = self._get_param_search_space()

        base_model = xgb.XGBRegressor(
            objective='reg:squarederror',
            eval_metric='rmse',
            tree_method='hist',
            random_state=42
        )

        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=param_space,
            n_iter=n_iter,
            scoring='neg_mean_squared_error',
            cv=3,
            verbose=0,
            random_state=42,
            n_jobs=-1
        )

        try:
            search.fit(X, y)
            best_params = search.best_params_
            self.param_importances = self._calculate_param_importance(search)
            logging.info(f"Best hyperparameters found: {best_params}")

            self.model = xgb.XGBRegressor(
                objective='reg:squarederror',
                eval_metric='rmse',
                tree_method='hist',
                random_state=42,
                **best_params
            )

            self.best_params = best_params
            return best_params

        except Exception as e:
            logging.error(f"Error in hyperparameter optimization: {str(e)}")
            self._set_default_params()
            return None

    def _calculate_param_importance(self, search):
        results = search.cv_results_
        importances = {}

        for param_name in self._get_param_search_space().keys():
            param_values = results[f'param_{param_name}'].data
            try:
                param_numeric = [float(str(v)) if v is not None else 0 for v in param_values]
                importance = abs(np.corrcoef(param_numeric, results['mean_test_score'])[0, 1])
                if not np.isnan(importance):
                    importances[param_name] = importance
            except:
                continue

        total = sum(importances.values())
        if total > 0:
            importances = {k: v / total for k, v in importances.items()}

        return importances

    def fit(self, X: np.ndarray, y: np.ndarray, diagnostics: bool = True, hyper_opt: bool = True,
            hyper_opt_iters: int = 500) -> None:
        try:
            if len(X) < 2 or len(y) < 2:
                return

            split_idx = int(len(X) * self.train_split)
            self.train_indices = np.arange(split_idx)
            self.val_indices = np.arange(split_idx, len(X))

            X_train = X[:split_idx]
            y_train = y[:split_idx]
            X_val = X[split_idx:]
            y_val = y[split_idx:]

            X_train_scaled = self.scaler.fit_transform(X_train)

            if hyper_opt and len(X_train) >= 100:
                logging.info("Performing hyperparameter optimization")
                self._hyperparameter_optimize(X_train_scaled, y_train, n_iter=hyper_opt_iters)
            else:
                self._set_default_params()

            if len(X_val) > 0:
                X_val_scaled = self.scaler.transform(X_val)
                self.model.set_params(early_stopping_rounds=20)
                self.model.fit(
                    X_train_scaled, y_train,
                    eval_set=[(X_val_scaled, y_val)],
                    verbose=False
                )
            else:
                self.model.fit(X_train_scaled, y_train)

            self._is_fitted = True

            if diagnostics:
                self._run_diagnostics(X, y)

        except Exception as e:
            logging.error(f"Error fitting XGBoost: {str(e)}")

    def predict(self, X: np.ndarray, **kwargs) -> np.ndarray:
        if not self._is_fitted:
            return np.zeros(X.shape[0])

        try:
            X_scaled = self.scaler.transform(X)
            return self.model.predict(X_scaled)

        except Exception as e:
            logging.error(f"Error in XGBoost prediction: {str(e)}")
            return np.zeros(X.shape[0])

    def _run_diagnostics(self, X: np.ndarray, y: np.ndarray):
        logging.info("Running XGBoost model diagnostics")

        X_scaled = self.scaler.transform(X)
        all_preds = self.model.predict(X_scaled)

        train_actual = y[self.train_indices]
        train_predicted = all_preds[self.train_indices]

        has_validation = len(self.val_indices) > 0
        if has_validation:
            val_actual = y[self.val_indices]
            val_predicted = all_preds[self.val_indices]
        else:
            val_actual = val_predicted = None

        train_mse = mean_squared_error(train_actual, train_predicted)
        train_mae = mean_absolute_error(train_actual, train_predicted)
        train_r2 = r2_score(train_actual, train_predicted)

        logging.info(f"Training Set - MSE: {train_mse:.6f}, MAE: {train_mae:.6f}, R²: {train_r2:.6f}")

        if has_validation:
            val_mse = mean_squared_error(val_actual, val_predicted)
            val_mae = mean_absolute_error(val_actual, val_predicted)
            val_r2 = r2_score(val_actual, val_predicted)
            logging.info(f"Validation Set - MSE: {val_mse:.6f}, MAE: {val_mae:.6f}, R²: {val_r2:.6f}")
        else:
            val_mse = val_mae = val_r2 = None

        self._create_diagnostic_plots(
            train_actual, train_predicted, train_mse, train_mae, train_r2,
            val_actual, val_predicted, val_mse, val_mae, val_r2
        )

        plt.figure(figsize=(10, 6))
        xgb.plot_importance(self.model)
        plt.title('XGBoost Feature Importance')
        plt.tight_layout()
        plt.savefig('charts/xgb_feature_importance.png', dpi=300, bbox_inches='tight')
        plt.close()

        self._analyze_extreme_points(y, all_preds)

        if hasattr(self, 'param_importances') and self.best_params:
            self._plot_hyperparameter_importance()
            self._plot_hyperparameter_summary()

        logging.info("XGBoost diagnostics complete")

    def _plot_hyperparameter_importance(self):
        if not hasattr(self, 'param_importances') or not self.param_importances:
            return

        plt.figure(figsize=(10, 6))
        params = list(self.param_importances.keys())
        values = list(self.param_importances.values())

        sorted_idx = np.argsort(values)
        plt.barh([params[i] for i in sorted_idx], [values[i] for i in sorted_idx])
        plt.title('Hyperparameter Importance')
        plt.xlabel('Relative Importance')
        plt.tight_layout()
        plt.savefig('charts/xgb_hyperparam_importance.png', dpi=300)
        plt.close()

    def _plot_hyperparameter_summary(self):
        if not self.best_params:
            return

        plt.figure(figsize=(12, 8))
        plt.axis('off')
        sorted_params = sorted(self.best_params.items())
        param_names = [p[0] for p in sorted_params]

        param_values_str = []
        for val in [p[1] for p in sorted_params]:
            if isinstance(val, float):
                param_values_str.append(f"{val:.4f}" if val >= 0.01 else f"{val:.2e}")
            else:
                param_values_str.append(str(val))

        table = plt.table(
            cellText=[[v] for v in param_values_str],
            rowLabels=param_names,
            colLabels=["Value"],
            cellLoc='center',
            loc='center',
            colWidths=[0.5]
        )

        table.auto_set_font_size(False)
        table.set_fontsize(12)
        table.scale(1.2, 1.8)
        plt.title('Optimized Hyperparameters', fontsize=16, y=0.9)
        plt.savefig('charts/xgb_hyperparam_summary.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _create_diagnostic_plots(self, train_actual, train_predicted, train_mse, train_mae, train_r2,
                                 val_actual=None, val_predicted=None, val_mse=None, val_mae=None, val_r2=None):
        has_validation = val_actual is not None and val_predicted is not None

        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        axes = axes.flatten()

        if has_validation:
            fig.suptitle('XGBoost Model Diagnostics - Training vs Validation', fontsize=16)

            all_actual = np.concatenate([train_actual, val_actual])
            all_predicted = np.concatenate([train_predicted, val_predicted])
            train_residuals = train_actual - train_predicted
            val_residuals = val_actual - val_predicted
            all_residuals = np.concatenate([train_residuals, val_residuals])
        else:
            fig.suptitle('XGBoost Model Diagnostics - Training Only', fontsize=16)
            all_actual = train_actual
            all_predicted = train_predicted
            train_residuals = train_actual - train_predicted
            all_residuals = train_residuals

        train_indices = self.train_indices
        axes[0].plot(train_indices, train_actual, label='Train Actual', alpha=0.8, color='blue')
        axes[0].plot(train_indices, train_predicted, label='Train Predicted', alpha=0.8, color='orange')

        if has_validation:
            val_indices = self.val_indices
            axes[0].plot(val_indices, val_actual, label='Val Actual', alpha=0.8, color='green')
            axes[0].plot(val_indices, val_predicted, label='Val Predicted', alpha=0.8, color='red')
            axes[0].axvline(x=len(train_indices), color='gray', linestyle='--', alpha=0.7, label='Train/Val Split')
            title = f'Predictions vs Actual\nTrain R²: {train_r2:.3f}, Val R²: {val_r2:.3f}'
        else:
            title = f'Predictions vs Actual\nMSE: {train_mse:.6f}, R²: {train_r2:.3f}'

        axes[0].set_title(title)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        axes[0].set_xlabel('Sample Index')
        axes[0].set_ylabel('Value')

        y_min, y_max = np.percentile(all_actual, [1, 99])
        y_range = y_max - y_min
        axes[0].set_ylim(y_min - 0.1 * y_range, y_max + 0.1 * y_range)

        axes[1].plot(train_indices, train_residuals, alpha=0.7, color='orange', label='Train Residuals')
        if has_validation:
            axes[1].plot(val_indices, val_residuals, alpha=0.7, color='red', label='Val Residuals')
            axes[1].axvline(x=len(train_indices), color='gray', linestyle='--', alpha=0.7)
            title = f'Residuals\nTrain MAE: {train_mae:.6f}, Val MAE: {val_mae:.6f}'
        else:
            title = f'Residuals (MAE: {train_mae:.6f})'

        axes[1].axhline(y=0, color='black', linestyle='--')
        axes[1].set_title(title)
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)
        axes[1].set_xlabel('Sample Index')
        axes[1].set_ylabel('Residual')

        res_min, res_max = np.percentile(all_residuals, [5, 95])
        res_range = res_max - res_min
        if res_range > 0:
            axes[1].set_ylim(res_min - 0.2 * res_range, res_max + 0.2 * res_range)

        axes[2].scatter(train_actual, train_predicted, alpha=0.6, color='blue', label='Training', s=20)
        if has_validation:
            axes[2].scatter(val_actual, val_predicted, alpha=0.6, color='green', label='Validation', s=20)

        min_val = np.percentile(all_actual, 5)
        max_val = np.percentile(all_actual, 95)
        axes[2].plot([min_val, max_val], [min_val, max_val], 'r--', label='Perfect', alpha=0.8)
        axes[2].set_xlabel('Actual')
        axes[2].set_ylabel('Predicted')

        if has_validation:
            title = f'Actual vs Predicted\nTrain R²: {train_r2:.3f}, Val R²: {val_r2:.3f}'
        else:
            title = f'Actual vs Predicted (R² = {train_r2:.3f})'
        axes[2].set_title(title)
        axes[2].legend()
        axes[2].grid(True, alpha=0.3)

        axes[2].set_xlim(min_val - 0.05 * (max_val - min_val), max_val + 0.05 * (max_val - min_val))
        axes[2].set_ylim(min_val - 0.05 * (max_val - min_val), max_val + 0.05 * (max_val - min_val))

        n_bins = min(30, max(10, int(len(all_actual) / 50)))

        axes[3].hist(train_actual, bins=n_bins, alpha=0.6, label='Train Actual', density=True, color='blue')
        axes[3].hist(train_predicted, bins=n_bins, alpha=0.6, label='Train Predicted', density=True, color='orange')
        if has_validation:
            axes[3].hist(val_actual, bins=n_bins, alpha=0.6, label='Val Actual', density=True, color='green')
            axes[3].hist(val_predicted, bins=n_bins, alpha=0.6, label='Val Predicted', density=True, color='red')

        axes[3].set_title('Value Distributions')
        axes[3].legend()
        axes[3].grid(True, alpha=0.3)
        axes[3].set_xlabel('Value')
        axes[3].set_ylabel('Density')

        axes[3].set_xlim(np.percentile(all_actual, 2.5), np.percentile(all_actual, 97.5))

        plt.tight_layout()
        plt.savefig('charts/xgb_diagnostics.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _analyze_extreme_points(self, y: np.ndarray, all_preds: np.ndarray):
        peak_indices = []
        trough_indices = []

        for i in range(1, len(y) - 1):
            if y[i] > y[i - 1] and y[i] > y[i + 1]:
                peak_indices.append(i)
            if y[i] < y[i - 1] and y[i] < y[i + 1]:
                trough_indices.append(i)

        extreme_indices = peak_indices + trough_indices

        if extreme_indices:
            extreme_actual = [y[i] for i in extreme_indices]
            extreme_pred = [all_preds[i] for i in extreme_indices]
            extreme_mse = mean_squared_error(extreme_actual, extreme_pred)
            extreme_mae = mean_absolute_error(extreme_actual, extreme_pred)
            extreme_r2 = r2_score(extreme_actual, extreme_pred)

            logging.info(f"Extreme points - MSE: {extreme_mse:.6f}, MAE: {extreme_mae:.6f}, R²: {extreme_r2:.6f}")

            plt.figure(figsize=(12, 6))

            plt.plot(y, alpha=0.3, color='gray', label='All Actual')
            plt.plot(all_preds, alpha=0.3, color='lightgray', label='All Predicted')

            if peak_indices:
                plt.scatter(peak_indices, [y[i] for i in peak_indices],
                            color='green', label='Actual Peaks', marker='^', s=50, zorder=5)
                plt.scatter(peak_indices, [all_preds[i] for i in peak_indices],
                            color='lightgreen', label='Predicted at Peaks', marker='^', s=50, zorder=5)

            if trough_indices:
                plt.scatter(trough_indices, [y[i] for i in trough_indices],
                            color='red', label='Actual Troughs', marker='v', s=50, zorder=5)
                plt.scatter(trough_indices, [all_preds[i] for i in trough_indices],
                            color='pink', label='Predicted at Troughs', marker='v', s=50, zorder=5)

            plt.title(f'Extreme Points Analysis\nMSE: {extreme_mse:.6f}, MAE: {extreme_mae:.6f}, R²: {extreme_r2:.6f}')
            plt.xlabel('Sample Index')
            plt.ylabel('Value')
            plt.legend()
            plt.grid(True, alpha=0.3)

            all_extreme_values = [y[i] for i in extreme_indices] + [all_preds[i] for i in extreme_indices]
            if all_extreme_values:
                y_min, y_max = np.percentile(all_extreme_values, [5, 95])
                y_range = y_max - y_min
                plt.ylim(y_min - 0.2 * y_range, y_max + 0.2 * y_range)

            plt.tight_layout()
            plt.savefig('charts/xgb_extreme_points.png', dpi=300, bbox_inches='tight')
            plt.close()

    def get_hyperopt_results(self):
        return {
            'best_params': self.best_params,
            'default_params': self._set_default_params()
        }

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted
