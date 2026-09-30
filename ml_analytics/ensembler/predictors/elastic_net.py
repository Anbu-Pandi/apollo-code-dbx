import logging
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import ElasticNetCV
import matplotlib.pyplot as plt
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

from ml_analytics.ensembler.predictors.base_predictor import BasePredictor


class ElasticNetPredictor(BasePredictor):
    def __init__(self, alpha: float = 1.0, train_split: float = 0.8):
        self.alpha = alpha
        self.train_split = train_split
        self.model = None
        self.scaler = StandardScaler()
        self._is_fitted = False
        self.train_indices = None
        self.val_indices = None

    def fit(self, X: np.ndarray, y: np.ndarray, diagnostics: bool = True) -> None:
        try:
            if len(X) < 2 or len(y) < 2:
                return

            split_idx = int(len(X) * self.train_split)
            self.train_indices = np.arange(split_idx)
            self.val_indices = np.arange(split_idx, len(X))

            X_train = X[:split_idx]
            y_train = y[:split_idx]
            X_val = X[split_idx:] if len(self.val_indices) > 0 else None

            X_train_scaled = self.scaler.fit_transform(X_train)
            X_val_scaled = self.scaler.transform(X_val) if X_val is not None else None

            self.model = ElasticNetCV(
                alphas=np.logspace(-3, 2, 10),
                l1_ratio=np.linspace(0.1, 0.9, 9),
                cv=[(self.train_indices, self.val_indices)] if X_val is not None else 5,
                fit_intercept=True,
                max_iter=1000,
                tol=1e-4
            )

            if X_val is not None:
                X_combined = np.vstack([X_train_scaled, X_val_scaled])
                y_combined = np.hstack([y_train, y[split_idx:]])
                self.model.fit(X_combined, y_combined)
            else:
                self.model.fit(X_train_scaled, y_train)

            self._is_fitted = True

            if diagnostics:
                self._run_diagnostics(X, y)

        except Exception as e:
            logging.error(f"Error fitting elastic net: {str(e)}")

    def predict(self, X: np.ndarray, **kwargs) -> np.ndarray:
        if not self._is_fitted:
            return np.zeros(X.shape[0])
        try:
            X_scaled = self.scaler.transform(X)
            return self.model.predict(X_scaled)
        except Exception as e:
            logging.error(f"Error in elastic net prediction: {str(e)}")
            return np.zeros(X.shape[0])

    def _run_diagnostics(self, X: np.ndarray, y: np.ndarray):
        logging.info("Running ElasticNet model diagnostics")

        all_preds = self.predict(X)
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

        logging.info(f"Optimal parameters: alpha={self.model.alpha_:.6f}, l1_ratio={self.model.l1_ratio_:.3f}")

        self._create_diagnostic_plots(
            train_actual, train_predicted, train_mse, train_mae, train_r2,
            val_actual, val_predicted, val_mse, val_mae, val_r2
        )

        self._plot_coefficients()
        self._analyze_extreme_points(y, all_preds)
        logging.info("ElasticNet diagnostics complete")

    def _plot_coefficients(self):
        feature_coeffs = self.model.coef_

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

        ax1.bar(range(len(feature_coeffs)), feature_coeffs)
        ax1.set_title(f'ElasticNet Coefficients (α={self.model.alpha_:.4f}, l1_ratio={self.model.l1_ratio_:.3f})')
        ax1.set_xlabel('Feature Index')
        ax1.set_ylabel('Coefficient Value')
        ax1.grid(True, alpha=0.3)

        non_zero_coeffs = np.sum(np.abs(feature_coeffs) > 1e-6)
        total_coeffs = len(feature_coeffs)
        sparsity = (total_coeffs - non_zero_coeffs) / total_coeffs * 100

        ax2.bar(['Non-zero', 'Zero'], [non_zero_coeffs, total_coeffs - non_zero_coeffs],
                color=['blue', 'red'], alpha=0.7)
        ax2.set_title(f'Coefficient Sparsity: {sparsity:.1f}% zero')
        ax2.set_ylabel('Number of Coefficients')

        plt.tight_layout()
        plt.savefig('charts/elasticnet_coefficients.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _create_diagnostic_plots(self, train_actual, train_predicted, train_mse, train_mae, train_r2,
                                 val_actual=None, val_predicted=None, val_mse=None, val_mae=None, val_r2=None):
        has_validation = val_actual is not None and val_predicted is not None

        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        axes = axes.flatten()

        if has_validation:
            fig.suptitle('ElasticNet Model Diagnostics - Training vs Validation', fontsize=16)
            all_actual = np.concatenate([train_actual, val_actual])
            all_predicted = np.concatenate([train_predicted, val_predicted])
            train_residuals = train_actual - train_predicted
            val_residuals = val_actual - val_predicted
            all_residuals = np.concatenate([train_residuals, val_residuals])
        else:
            fig.suptitle('ElasticNet Model Diagnostics - Training Only', fontsize=16)
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

        axes[3].scatter(train_predicted, train_residuals, alpha=0.6, color='blue', label='Training', s=20)
        if has_validation:
            axes[3].scatter(val_predicted, val_residuals, alpha=0.6, color='green', label='Validation', s=20)

        axes[3].axhline(y=0, color='red', linestyle='--', alpha=0.8)
        axes[3].set_xlabel('Predicted Values')
        axes[3].set_ylabel('Residuals')
        axes[3].set_title('Residuals vs Predicted (Heteroscedasticity Check)')
        axes[3].legend()
        axes[3].grid(True, alpha=0.3)

        pred_min, pred_max = np.percentile(all_predicted, [5, 95])
        axes[3].set_xlim(pred_min - 0.05 * (pred_max - pred_min), pred_max + 0.05 * (pred_max - pred_min))
        if res_range > 0:
            axes[3].set_ylim(res_min - 0.2 * res_range, res_max + 0.2 * res_range)

        plt.tight_layout()
        plt.savefig('charts/elasticnet_diagnostics.png', dpi=300, bbox_inches='tight')
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
            plt.savefig('charts/elasticnet_extreme_points.png', dpi=300, bbox_inches='tight')
            plt.close()

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted