import logging
from typing import Tuple

import numpy as np
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

from ml_analytics.ensembler.predictors.base_predictor import BasePredictor


class KalmanFilter:
    """Enhanced Kalman filter for time series prediction"""

    def __init__(self, n_states: int, r: float = 0.01, q: float = 0.01):
        self.n_states = n_states
        self.r = r  # measurement noise
        self.q = q  # process noise

        # State and covariance initialization
        self.state = np.zeros(n_states)
        self.covariance = np.eye(n_states) * 1000

        # Prediction components
        self.A = np.eye(n_states)  # State transition matrix
        self.H = None  # Measurement matrix (set during first update)

        self.states_history = []
        self.covariance_history = []
        self.predictions = []
        # Added for diagnostics
        self.uncertainties = []
        self.innovations = []

    def predict(self, x: np.ndarray, **kwargs) -> float:
        """Generate prediction using current state"""
        if self.H is None:
            self.H = x.reshape(1, -1)
        return float(self.H.dot(self.state))

    def update(self, measurement: float, x: np.ndarray) -> Tuple[float, float]:
        """Update state and generate prediction with uncertainty"""
        try:
            if x.shape[0] != self.n_states:
                raise ValueError(f"Input dimension {x.shape[0]} doesn't match state dimension {self.n_states}")

            # Set measurement matrix if not initialized
            if self.H is None:
                self.H = x.reshape(1, -1)

            # Prior state and covariance
            prior_state = self.A.dot(self.state)
            prior_covariance = self.A.dot(self.covariance).dot(self.A.T) + self.q * np.eye(self.n_states)

            # Kalman gain calculation
            innovation_covariance = float(self.H.dot(prior_covariance).dot(self.H.T) + self.r)
            kalman_gain = prior_covariance.dot(self.H.T) / innovation_covariance

            # Measurement prediction
            predicted_measurement = float(self.H.dot(prior_state))

            # State and covariance update
            innovation = measurement - predicted_measurement
            self.state = prior_state + kalman_gain.flatten() * innovation
            self.covariance = (np.eye(self.n_states) - kalman_gain.dot(self.H)).dot(prior_covariance)

            # Store history
            self.states_history.append(self.state.copy())
            self.covariance_history.append(np.sqrt(np.diag(self.covariance)))
            # Added for diagnostics
            self.innovations.append(innovation)

            # Generate prediction and uncertainty
            prediction = float(self.H.dot(self.state))
            uncertainty = float(np.sqrt(self.H.dot(self.covariance).dot(self.H.T)))

            self.predictions.append(prediction)
            # Added for diagnostics
            self.uncertainties.append(uncertainty)

            return prediction, uncertainty

        except Exception as e:
            logging.error(f"Error in Kalman filter update: {str(e)}")
            return 0.0, float('inf')


class KalmanPredictor(BasePredictor):
    """Kalman filter predictor with consistent interface"""

    def __init__(self, n_states: int, r: float = 0.01, q: float = 0.01):
        self.n_states = n_states
        self.r = r
        self.q = q
        self.filter = KalmanFilter(n_states, r, q)
        self.scaler = StandardScaler()
        self._is_fitted = False
        # Added for diagnostics
        self.X_history = None
        self.y_history = None

    def fit(self, X: np.ndarray, y: np.ndarray, diagnostics: bool = False) -> None:
        try:
            if len(X) < 1 or len(y) < 1:
                return

            # Store history for diagnostics
            self.X_history = X.copy()
            self.y_history = y.copy()

            X_reshaped = X.reshape(-1, self.n_states)
            y_reshaped = y.reshape(-1)

            # Sequential update
            self.filter.update(y_reshaped[-1], X_reshaped[-1])
            self._is_fitted = True

            # Run diagnostics if requested
            if diagnostics:
                self._run_diagnostics()

        except Exception as e:
            logging.error(f"Error fitting Kalman filter: {str(e)}")
            self._is_fitted = False

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            return np.array([np.nan])

        try:
            X_reshaped = X.reshape(-1, self.n_states)
            return np.array([self.filter.predict(X_reshaped[-1])])

        except Exception as e:
            logging.error(f"Error in Kalman prediction: {str(e)}")
            return np.array([np.nan])

    def _run_diagnostics(self):
        """Run comprehensive diagnostics on the Kalman filter"""
        logging.info("Running Kalman filter diagnostics")

        if self.y_history is None:
            logging.warning("No data available for diagnostics")
            return

        # Create predictions for the entire sequence to generate a proper chart
        all_predictions = []
        all_uncertainties = []

        if self.X_history is not None and len(self.X_history) > 0:
            X_reshaped = self.X_history.reshape(-1, self.n_states)

            # Create a separate filter for diagnostics
            diagnostic_filter = KalmanFilter(self.n_states, self.r, self.q)

            # Generate predictions by sequentially updating the filter
            for i in range(len(X_reshaped)):
                if i == 0:
                    # For first point, we can't predict, so use initial state prediction
                    pred = diagnostic_filter.predict(X_reshaped[i])
                    all_predictions.append(pred)
                    all_uncertainties.append(1.0)  # Initial high uncertainty
                else:
                    # Update with previous measurement and predict current
                    diagnostic_filter.update(self.y_history[i - 1], X_reshaped[i - 1])
                    pred = diagnostic_filter.predict(X_reshaped[i])
                    uncertainty = np.sqrt(diagnostic_filter.covariance[0, 0]) if len(
                        diagnostic_filter.covariance) > 0 else 1.0
                    all_predictions.append(pred)
                    all_uncertainties.append(uncertainty)

        # Create figure with subplots
        fig = plt.figure(figsize=(15, 12))

        # 1. Predictions vs Actual Chart
        ax1 = plt.subplot(2, 3, 1)
        if all_predictions:
            time_indices = range(len(all_predictions))
            plt.plot(time_indices, self.y_history, label='Actual', color='blue', alpha=0.8, linewidth=2)
            plt.plot(time_indices, all_predictions, label='Predicted', color='red', alpha=0.8, linewidth=2)

            # Add uncertainty bounds
            pred_array = np.array(all_predictions)
            uncertainty_array = np.array(all_uncertainties)
            plt.fill_between(time_indices,
                             pred_array - 2 * uncertainty_array,
                             pred_array + 2 * uncertainty_array,
                             alpha=0.2, color='red', label='±2σ bounds')

            plt.title('Kalman Filter: Predictions vs Actual Over Time')
            plt.xlabel('Time Step')
            plt.ylabel('Value')
            plt.legend()
            plt.grid(True, alpha=0.3)

            # Calculate and display metrics
            mse = mean_squared_error(self.y_history, all_predictions)
            mae = mean_absolute_error(self.y_history, all_predictions)
            r2 = r2_score(self.y_history, all_predictions)
            plt.text(0.02, 0.98, f'MSE: {mse:.4f}\nMAE: {mae:.4f}\nR²: {r2:.4f}',
                     transform=ax1.transAxes, verticalalignment='top',
                     bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
        else:
            plt.text(0.5, 0.5, 'No prediction data available',
                     transform=ax1.transAxes, ha='center', va='center')
            plt.title('Predictions vs Actual')

        # 2. Single Point Update Visualization (last observation)
        ax2 = plt.subplot(2, 3, 2)
        plt.scatter([0], [self.y_history[-1]], color='blue', s=100, label='Actual (last)', marker='o')
        if self.filter.predictions:
            plt.scatter([0], [self.filter.predictions[-1]], color='red', s=100, label='Predicted', marker='x')
            if self.filter.uncertainties:
                plt.errorbar([0], [self.filter.predictions[-1]],
                             yerr=[2 * self.filter.uncertainties[-1]],
                             color='red', alpha=0.5, label='±2σ uncertainty')
        plt.title('Last Observation: Actual vs Predicted')
        plt.xlabel('Observation')
        plt.ylabel('Value')
        plt.legend()
        plt.grid(True, alpha=0.3)

        # Add text with values
        if self.filter.predictions and self.filter.uncertainties:
            plt.text(0.5, 0.95, f'Actual: {self.y_history[-1]:.4f}\n'
                                f'Predicted: {self.filter.predictions[-1]:.4f}\n'
                                f'Error: {abs(self.y_history[-1] - self.filter.predictions[-1]):.4f}\n'
                                f'Uncertainty: {self.filter.uncertainties[-1]:.4f}',
                     transform=ax1.transAxes, verticalalignment='top',
                     bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

        # 2. State Values
        ax2 = plt.subplot(2, 3, 2)
        if self.filter.states_history:
            state_values = self.filter.states_history[-1]
            plt.bar(range(len(state_values)), state_values, alpha=0.7)
            plt.title('Final State Values')
            plt.xlabel('State Index')
            plt.ylabel('State Value')
            plt.grid(True, alpha=0.3)

        # 3. State Uncertainties
        ax3 = plt.subplot(2, 3, 3)
        if self.filter.covariance_history:
            uncertainties = self.filter.covariance_history[-1]
            plt.bar(range(len(uncertainties)), uncertainties, alpha=0.7, color='orange')
            plt.title('Final State Uncertainties (σ)')
            plt.xlabel('State Index')
            plt.ylabel('Standard Deviation')
            plt.grid(True, alpha=0.3)
            plt.yscale('log')

        # 4. Filter Parameters Visualization
        ax4 = plt.subplot(2, 3, 4)
        params = ['Process Noise (q)', 'Measurement Noise (r)', 'n_states']
        values = [self.filter.q, self.filter.r, self.filter.n_states]
        plt.bar(params, values, alpha=0.7, color='green')
        plt.title('Filter Parameters')
        plt.ylabel('Value')
        plt.grid(True, alpha=0.3)
        plt.xticks(rotation=45)

        # 5. Input Feature Vector (last)
        ax5 = plt.subplot(2, 3, 5)
        if self.X_history is not None:
            last_features = self.X_history.reshape(-1, self.n_states)[-1]
            plt.bar(range(len(last_features)), last_features, alpha=0.7, color='purple')
            plt.title('Last Input Feature Vector')
            plt.xlabel('Feature Index')
            plt.ylabel('Feature Value')
            plt.grid(True, alpha=0.3)

        # 6. Summary Statistics
        ax6 = plt.subplot(2, 3, 6)
        ax6.axis('off')

        summary_text = f"Kalman Filter Summary\n" + "=" * 25 + "\n\n"
        summary_text += f"n_states: {self.n_states}\n"
        summary_text += f"Process noise (q): {self.filter.q}\n"
        summary_text += f"Measurement noise (r): {self.filter.r}\n\n"

        if self.filter.predictions and self.filter.uncertainties:
            summary_text += f"Last prediction: {self.filter.predictions[-1]:.4f}\n"
            summary_text += f"Prediction uncertainty: {self.filter.uncertainties[-1]:.4f}\n"
            summary_text += f"Actual value: {self.y_history[-1]:.4f}\n"
            summary_text += f"Prediction error: {abs(self.y_history[-1] - self.filter.predictions[-1]):.4f}\n\n"

        if self.filter.innovations:
            summary_text += f"Innovation: {self.filter.innovations[-1]:.4f}\n"

        plt.text(0.1, 0.9, summary_text, transform=ax6.transAxes, fontsize=11,
                 verticalalignment='top', fontfamily='monospace',
                 bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))

        plt.tight_layout()
        plt.savefig('charts/kalman_diagnostics.png', dpi=300, bbox_inches='tight')
        plt.close()

        # Since we only have one update, create a simple state transition visualization
        plt.figure(figsize=(10, 6))
        if self.filter.states_history and self.filter.covariance_history:
            state_vals = self.filter.states_history[-1]
            state_uncert = self.filter.covariance_history[-1]

            x_pos = np.arange(len(state_vals))
            plt.errorbar(x_pos, state_vals, yerr=state_uncert,
                         fmt='o', capsize=5, capthick=2, alpha=0.7)
            plt.title('Final State Values with Uncertainties')
            plt.xlabel('State Index')
            plt.ylabel('State Value ± σ')
            plt.grid(True, alpha=0.3)

            # Add value labels
            for i, (val, err) in enumerate(zip(state_vals, state_uncert)):
                plt.text(i, val + err + 0.01 * max(state_vals),
                         f'{val:.3f}', ha='center', va='bottom', fontsize=9)

        plt.savefig('charts/kalman_state_uncertainty.png', dpi=300, bbox_inches='tight')
        plt.close()

        logging.info("Kalman filter diagnostics complete")

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted