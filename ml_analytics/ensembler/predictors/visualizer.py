import logging

import matplotlib.pyplot as plt
import numpy as np
import optuna
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score


class Visualizer:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def plot_losses(self, train_losses, val_losses, filename='charts/{}_training_loss.png'):
        filename = filename.format(self.model_name)

        plt.figure(figsize=(10, 6))
        plt.plot(train_losses, label='Training Loss', alpha=0.8, color='blue')
        plt.plot(val_losses, label='Validation Loss', alpha=0.8, color='red')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.title('{} Training and Validation Loss'.format(self.model_name))
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()

    def plot_predictions(self, train_indices, train_actual, train_predicted,
                         val_indices=None, val_actual=None, val_predicted=None,
                         filename='charts/{}_diagnostics.png'):
        filename = filename.format(self.model_name)

        has_validation = val_actual is not None and len(val_actual) > 0

        if has_validation:
            all_actual = np.concatenate([train_actual, val_actual])
            all_predicted = np.concatenate([train_predicted, val_predicted])
            train_residuals = train_actual - train_predicted
            val_residuals = val_actual - val_predicted
            all_residuals = np.concatenate([train_residuals, val_residuals])
        else:
            all_actual = train_actual
            all_predicted = train_predicted
            train_residuals = train_actual - train_predicted
            all_residuals = train_residuals

        train_mse = mean_squared_error(train_actual, train_predicted)
        train_mae = mean_absolute_error(train_actual, train_predicted)
        train_r2 = r2_score(train_actual, train_predicted)

        if has_validation:
            val_mse = mean_squared_error(val_actual, val_predicted)
            val_mae = mean_absolute_error(val_actual, val_predicted)
            val_r2 = r2_score(val_actual, val_predicted)
        else:
            val_mse = val_mae = val_r2 = None

        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        axes = axes.flatten()

        # Plot 1: Time series of actual vs predicted
        axes[0].plot(train_indices, train_actual, label='Train Actual', alpha=0.8, color='blue')
        axes[0].plot(train_indices, train_predicted, label='Train Predicted', alpha=0.8, color='orange')

        if has_validation:
            axes[0].plot(val_indices, val_actual, label='Val Actual', alpha=0.8, color='green')
            axes[0].plot(val_indices, val_predicted, label='Val Predicted', alpha=0.8, color='red')
            axes[0].axvline(x=train_indices[-1], color='gray', linestyle='--', alpha=0.7, label='Train/Val Split')
            title = f'Predictions vs Actual\nTrain R²: {train_r2:.3f}, Val R²: {val_r2:.3f}'
        else:
            title = f'Predictions vs Actual\nMSE: {train_mse:.6f}, R²: {train_r2:.3f}'

        y_min, y_max = np.percentile(all_actual, [1, 99])
        y_range = max(0.001, y_max - y_min)
        axes[0].set_ylim(y_min - 0.1 * y_range, y_max + 0.1 * y_range)

        axes[0].set_title(title)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        axes[0].set_xlabel('Sample Index')
        axes[0].set_ylabel('Value')

        # Plot 2: Residuals
        axes[1].plot(train_indices, train_residuals, alpha=0.7, color='orange', label='Train Residuals')
        if has_validation:
            axes[1].plot(val_indices, val_residuals, alpha=0.7, color='red', label='Val Residuals')
            axes[1].axvline(x=train_indices[-1], color='gray', linestyle='--', alpha=0.7)
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
        res_range = max(0.001, res_max - res_min)
        axes[1].set_ylim(res_min - 0.2 * res_range, res_max + 0.2 * res_range)

        # Plot 3: Scatter of actual vs predicted
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

        val_range = max(0.001, max_val - min_val)
        axes[2].set_xlim(min_val - 0.05 * val_range, max_val + 0.05 * val_range)
        axes[2].set_ylim(min_val - 0.05 * val_range, max_val + 0.05 * val_range)

        # Plot 4: Histograms
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

        try:
            axes[3].set_xlim(np.percentile(all_actual, 2.5), np.percentile(all_actual, 97.5))
        except:
            pass

        if has_validation:
            fig.suptitle('{} Model Diagnostics - Training vs Validation'.format(self.model_name), fontsize=16)
        else:
            fig.suptitle('{} Model Diagnostics - Training Only'.format(self.model_name), fontsize=16)

        plt.tight_layout()
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()

    def plot_extreme_points(self, actual_vals, predicted_vals, filename='charts/{}_extreme_points.png'):
        filename = filename.format(self.model_name)

        peak_indices = []
        trough_indices = []

        for i in range(1, len(actual_vals) - 1):
            if actual_vals[i] > actual_vals[i - 1] and actual_vals[i] > actual_vals[i + 1]:
                peak_indices.append(i)
            if actual_vals[i] < actual_vals[i - 1] and actual_vals[i] < actual_vals[i + 1]:
                trough_indices.append(i)

        extreme_indices = peak_indices + trough_indices

        if not extreme_indices:
            return

        extreme_actual = [actual_vals[i] for i in extreme_indices]
        extreme_pred = [predicted_vals[i] for i in extreme_indices]
        extreme_mse = mean_squared_error(extreme_actual, extreme_pred)
        extreme_mae = mean_absolute_error(extreme_actual, extreme_pred)
        extreme_r2 = r2_score(extreme_actual, extreme_pred)

        logging.info(f"Extreme points - MSE: {extreme_mse:.6f}, MAE: {extreme_mae:.6f}, R²: {extreme_r2:.6f}")

        plt.figure(figsize=(12, 6))

        plt.plot(actual_vals, alpha=0.3, color='gray', label='All Actual')
        plt.plot(predicted_vals, alpha=0.3, color='lightgray', label='All Predicted')

        if peak_indices:
            plt.scatter(peak_indices, [actual_vals[i] for i in peak_indices],
                        color='green', label='Actual Peaks', marker='^', s=50, zorder=5)
            plt.scatter(peak_indices, [predicted_vals[i] for i in peak_indices],
                        color='lightgreen', label='Predicted at Peaks', marker='^', s=50, zorder=5)

        if trough_indices:
            plt.scatter(trough_indices, [actual_vals[i] for i in trough_indices],
                        color='red', label='Actual Troughs', marker='v', s=50, zorder=5)
            plt.scatter(trough_indices, [predicted_vals[i] for i in trough_indices],
                        color='pink', label='Predicted at Troughs', marker='v', s=50, zorder=5)

        plt.title(f'Extreme Points Analysis\nMSE: {extreme_mse:.6f}, MAE: {extreme_mae:.6f}, R²: {extreme_r2:.6f}')
        plt.xlabel('Sample Index')
        plt.ylabel('Value')
        plt.legend()
        plt.grid(True, alpha=0.3)

        try:
            all_extreme_values = extreme_actual + extreme_pred
            y_min, y_max = np.percentile(all_extreme_values, [5, 95])
            y_range = max(0.001, y_max - y_min)
            plt.ylim(y_min - 0.2 * y_range, y_max + 0.2 * y_range)
        except:
            pass

        plt.tight_layout()
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()

    def plot_hyperparameters(self, params, filename='charts/{}_hyperparam_summary.png'):
        filename = filename.format(self.model_name)

        if not params:
            return

        plt.figure(figsize=(12, 8))
        plt.axis('off')
        sorted_params = sorted(params.items())
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
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()

    def plot_param_importance(self, study, filename='charts/{}_hyperparam_importance.png'):
        filename = filename.format(self.model_name)

        try:
            importances = optuna.importance.get_param_importances(study)
            params = list(importances.keys())
            values = list(importances.values())
            plt.figure(figsize=(10, 6))
            plt.barh(params, values)
            plt.title('Hyperparameter Importance')
            plt.tight_layout()
            plt.savefig(filename, dpi=300)
            plt.close()
        except Exception as e:
            logging.warning(f"Error plotting importance: {e}")
