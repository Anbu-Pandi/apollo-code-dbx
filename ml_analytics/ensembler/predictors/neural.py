import logging
import random
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import optuna
import torch
from sklearn.decomposition import PCA
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from torch import nn
from torch.optim.lr_scheduler import ReduceLROnPlateau

from ml_analytics.ensembler.predictors.base_predictor import BasePredictor
from ml_analytics.ensembler.predictors.visualizer import Visualizer


@dataclass
class TrainingConfig:
    epochs: int = 100
    batch_size: int = 16
    early_stopping_patience: int = 30
    min_delta: float = 0.1
    learning_rate: float = 0.001
    weight_decay: float = 1e-4
    dropout: float = 0.4
    hidden_factor: float = 0.6
    n_layers: int = 2
    activation: str = 'gelu'
    lookback: int = 1
    use_pca: bool = False
    n_components: int = 20


@dataclass
class TrainingResult:
    model_state: Dict
    train_loss: float
    val_loss: float
    r2_score: float
    train_indices: np.ndarray
    val_indices: np.ndarray
    train_losses: List[float]
    val_losses: List[float]


@dataclass
class EvaluationResult:
    mse: float
    mae: float
    r2: float
    predictions: np.ndarray
    actual: np.ndarray
    indices: np.ndarray


class NotFittedError(Exception):
    pass


class EarlyStopping:
    def __init__(self, patience=15, min_delta=0.001):
        self.patience = patience
        self.min_delta = min_delta
        self.best_loss = float('inf')
        self.counter = 0
        self.best_weights = None

    def __call__(self, val_loss, model):
        if val_loss < self.best_loss - self.min_delta:
            self.best_loss = val_loss
            self.counter = 0
            self.best_weights = model.state_dict().copy()
        else:
            self.counter += 1
        return self.counter >= self.patience

    def restore_best_weights(self, model):
        if self.best_weights is not None:
            model.load_state_dict(self.best_weights)


class TimeSeriesPreprocessor:
    def __init__(self, lookback, input_size, use_pca=False, n_components=20, seed=42):
        self.lookback = lookback
        self.input_size = input_size
        self.use_pca = use_pca
        self.n_components = n_components
        self.seed = seed
        self.pca = PCA(n_components=n_components, random_state=seed) if use_pca else None

    def get_feature_size(self):
        if self.use_pca:
            return self.n_components * self.lookback
        return self.input_size * self.lookback

    def transform(self, X, y=None, fit=False):
        # Apply PCA if needed
        if self.use_pca:
            X_transformed = self.pca.fit_transform(X) if fit else self.pca.transform(X)
        else:
            X_transformed = X

        windows = []
        targets = []
        indices = []

        # Simple case: just return the data if lookback is 1
        if self.lookback == 1:
            for i in range(len(X_transformed)):
                windows.append(X_transformed[i])
                indices.append(i)
                if y is not None:
                    targets.append(y[i])
        else:
            # Normal sliding window for lookback > 1
            for i in range(len(X_transformed) - self.lookback + 1):
                if i + self.lookback <= len(X_transformed):
                    windows.append(X_transformed[i:i + self.lookback].flatten())
                    indices.append(i + self.lookback - 1)
                    if y is not None:
                        targets.append(y[i + self.lookback - 1])

        if not windows:
            return None, None, None

        X_windows = np.array(windows)
        if y is not None:
            return X_windows, np.array(targets), np.array(indices)
        return X_windows, None, np.array(indices)

    def update_config(self, config=None):
        if config is not None:
            if hasattr(config, 'lookback'):
                self.lookback = config.lookback
            if hasattr(config, 'use_pca'):
                self.use_pca = config.use_pca
            if hasattr(config, 'n_components') and self.use_pca:
                self.n_components = config.n_components
                self.pca = PCA(n_components=self.n_components, random_state=self.seed)


class NeuralArchitecture:
    def __init__(self, feature_size, seed=42):
        self.feature_size = feature_size
        self.seed = seed
        torch.manual_seed(seed)

    def get_activation(self, name):
        """Get activation function by name"""
        if name.lower() == 'relu':
            return nn.ReLU()
        elif name.lower() == 'leakyrelu':
            return nn.LeakyReLU(0.1)
        else:  # Default to GELU
            return nn.GELU()

    def build_model(self, config: TrainingConfig):
        layers = []
        input_size = self.feature_size

        # First layer
        hidden_size = int(input_size * config.hidden_factor)
        layers.append(nn.Linear(input_size, hidden_size))
        layers.append(nn.BatchNorm1d(hidden_size))
        layers.append(self.get_activation(config.activation))
        layers.append(nn.Dropout(config.dropout))

        # Hidden layers
        for i in range(1, config.n_layers):
            in_size = int(input_size * (config.hidden_factor ** i))
            out_size = int(input_size * (config.hidden_factor ** (i + 1)))
            layers.append(nn.Linear(in_size, out_size))
            layers.append(nn.BatchNorm1d(out_size))
            layers.append(self.get_activation(config.activation))
            layers.append(nn.Dropout(config.dropout))

        # Output layer
        layers.append(nn.Linear(int(input_size * (config.hidden_factor ** config.n_layers)), 1))

        return nn.Sequential(*layers)

    def update_feature_size(self, feature_size):
        self.feature_size = feature_size


class ModelTrainer:
    def __init__(self, model, seed=42):
        self.model = model
        self.seed = seed
        self.optimizer = None
        self.scheduler = None
        self.loss_fn = nn.MSELoss()
        self.generator = torch.Generator().manual_seed(seed)

    def setup_optimizer(self, config: TrainingConfig):
        self.optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=config.learning_rate,
            weight_decay=config.weight_decay
        )

        self.scheduler = ReduceLROnPlateau(
            self.optimizer,
            mode='min',
            factor=0.5,
            patience=30,
            verbose=True
        )

    def train_epoch(self, X_train, y_train, batch_size):
        self.model.train()
        total_loss = 0
        n_batches = 0
        min_batch_size = 5
        batch_size = max(batch_size, min_batch_size)

        indices = torch.randperm(len(X_train), generator=self.generator)
        for start in range(0, len(indices), batch_size):
            end = min(start + batch_size, len(indices))
            if end - start < min_batch_size:
                continue

            batch_idx = indices[start:end]

            self.optimizer.zero_grad()

            outputs = self.model(X_train[batch_idx])
            loss = self.loss_fn(outputs, y_train[batch_idx])

            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            self.optimizer.step()

            total_loss += loss.item()
            n_batches += 1

        return total_loss / max(1, n_batches)

    def validate(self, X_val, y_val):
        self.model.eval()
        with torch.no_grad():
            val_outputs = self.model(X_val)
            val_loss = self.loss_fn(val_outputs, y_val).item()

        return val_loss

    def train(self, X_tensor, y_tensor, indices, train_split, config: TrainingConfig) -> TrainingResult:
        # Split data
        train_size = int(train_split * len(X_tensor))
        X_train, X_val = X_tensor[:train_size], X_tensor[train_size:]
        y_train, y_val = y_tensor[:train_size], y_tensor[train_size:]

        # Save indices for diagnostics
        train_indices = indices[:train_size]
        val_indices = indices[train_size:]

        # Setup early stopping
        early_stopping = EarlyStopping(
            patience=config.early_stopping_patience,
            min_delta=config.min_delta
        )

        train_losses = []
        val_losses = []

        # Training loop
        for epoch in range(config.epochs):
            train_loss = self.train_epoch(X_train, y_train, config.batch_size)
            val_loss = self.validate(X_val, y_val)

            train_losses.append(train_loss)
            val_losses.append(val_loss)

            self.scheduler.step(val_loss)

            if early_stopping(val_loss, self.model):
                logging.info(f"Early stopping at epoch {epoch}")
                break

            if epoch % 10 == 0 or epoch == config.epochs - 1:
                logging.info(f"Epoch {epoch}: train_loss={train_loss:.6f}, val_loss={val_loss:.6f}")

        # Restore best weights
        early_stopping.restore_best_weights(self.model)

        # Calculate final R² score
        self.model.eval()
        with torch.no_grad():
            val_pred = self.model(X_val).numpy()

        return TrainingResult(
            model_state=self.model.state_dict().copy(),
            train_loss=train_losses[-1],
            val_loss=val_losses[-1],
            r2_score=0.0,  # Will be calculated by the caller with unscaled data
            train_indices=train_indices,
            val_indices=val_indices,
            train_losses=train_losses,
            val_losses=val_losses
        )

    def predict(self, X):
        self.model.eval()
        with torch.no_grad():
            return self.model(X).numpy()


class ModelEvaluator:
    """Handles model evaluation and cross-validation"""

    @staticmethod
    def evaluate(model, preprocessor, X, y=None):
        """Evaluate model on data"""
        if y is None:
            raise ValueError("Target values are required for evaluation")

        X_scaled, y_scaled, indices = preprocessor.transform(X, y)

        if X_scaled is None:
            return None

        X_tensor = torch.FloatTensor(X_scaled)

        model.eval()
        with torch.no_grad():
            predictions_scaled = model(X_tensor).numpy()

        predictions = predictions_scaled.flatten()
        actual = y[indices]

        # Calculate metrics
        mse = mean_squared_error(actual, predictions)
        mae = mean_absolute_error(actual, predictions)
        r2 = r2_score(actual, predictions)

        return EvaluationResult(
            mse=mse,
            mae=mae,
            r2=r2,
            predictions=predictions,
            actual=actual,
            indices=indices
        )

    @staticmethod
    def run_diagnostics(visualizer, evaluation_result, train_indices=None, val_indices=None, train_losses=None,
                        val_losses=None):
        """Run comprehensive diagnostics on evaluation results"""
        if train_indices is not None and val_indices is not None:
            # Split evaluation results into train and validation sets
            train_mask = np.isin(evaluation_result.indices, train_indices)
            val_mask = np.isin(evaluation_result.indices, val_indices)

            train_actual = evaluation_result.actual[train_mask]
            train_predictions = evaluation_result.predictions[train_mask]
            train_indices_filtered = evaluation_result.indices[train_mask]

            val_actual = evaluation_result.actual[val_mask]
            val_predictions = evaluation_result.predictions[val_mask]
            val_indices_filtered = evaluation_result.indices[val_mask]

            # Plot predictions
            visualizer.plot_predictions(
                train_indices_filtered, train_actual, train_predictions,
                val_indices_filtered, val_actual, val_predictions
            )
        else:
            # Plot without train/val split
            visualizer.plot_predictions(
                evaluation_result.indices,
                evaluation_result.actual,
                evaluation_result.predictions
            )

        # Plot extreme points
        visualizer.plot_extreme_points(
            evaluation_result.actual,
            evaluation_result.predictions
        )

        # Plot loss curves if available
        if train_losses and val_losses:
            visualizer.plot_losses(train_losses, val_losses)

        return {
            'mse': evaluation_result.mse,
            'mae': evaluation_result.mae,
            'r2': evaluation_result.r2
        }

    @staticmethod
    def run_cross_validation(predictor_class, X, y, config, train_split, n_folds=None, seed=42):
        """Run cross-validation with time series split"""
        data_size = len(X)
        if n_folds is None:
            n_folds = int(data_size / 30)
        n_folds = min(5, max(3, n_folds))  # Ensure at least 3 folds

        test_size = min(int(data_size * 0.2), max(5, int(data_size / (n_folds + 1))))
        test_size = max(1, test_size)  # Ensure test_size is at least 1

        logging.info(f"CV setup: data_size={data_size}, n_folds={n_folds}, test_size={test_size}")

        try:
            tscv = TimeSeriesSplit(n_splits=n_folds, test_size=test_size)
            cv_scores = []
            cv_results = []

            for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
                logging.info(f"Training fold {fold + 1}/{n_folds}")

                # Split data for this fold
                X_train, X_val = X[train_idx], X[val_idx]
                y_train, y_val = y[train_idx], y[val_idx]

                # Create and train a new model for this fold
                fold_model = predictor_class(
                    input_size=X.shape[1],
                    lookback=config.lookback if hasattr(config, 'lookback') else 1,
                    use_pca=config.use_pca if hasattr(config, 'use_pca') else False,
                    n_components=config.n_components if hasattr(config, 'n_components') else 20,
                    seed=seed + fold  # Different seed for each fold
                )

                # Train the model without recursive CV
                fold_model.fit(X_train, y_train, config, cv=False, hyper_opt=False, diagnostics=False)

                # Evaluate on validation set
                fold_preds = fold_model.predict(X_val)

                # Make sure we have the right indices for scoring
                val_indices = fold_model.preprocessor.transform(X_val, y_val)[2]
                if val_indices is not None and len(val_indices) > 0:
                    fold_r2 = r2_score(y_val[val_indices], fold_preds)
                    cv_scores.append(fold_r2)
                    cv_results.append({
                        'fold': fold,
                        'r2': fold_r2,
                        'model': fold_model
                    })
                    logging.info(f"Fold {fold + 1} R²: {fold_r2:.3f}")
                else:
                    logging.warning(f"Fold {fold + 1}: No validation indices available, skipping")

            if cv_scores:
                logging.info(f"CV Mean R²: {np.mean(cv_scores):.3f} ± {np.std(cv_scores):.3f}")
                return {
                    'mean_r2': np.mean(cv_scores),
                    'std_r2': np.std(cv_scores),
                    'scores': cv_scores,
                    'results': cv_results
                }
            else:
                logging.warning("No valid CV scores were produced")
                return {
                    'mean_r2': 0.0,
                    'std_r2': 0.0,
                    'scores': [],
                    'results': []
                }
        except Exception as e:
            logging.error(f"Cross-validation error: {str(e)}")
            # Provide a fallback result
            return {
                'mean_r2': 0.0,
                'std_r2': 0.0,
                'scores': [],
                'results': [],
                'error': str(e)
            }


class HyperparameterOptimizer:
    """Handles hyperparameter optimization using Optuna"""

    def __init__(self, seed=42, visualizer=None):
        self.seed = seed
        self.visualizer = visualizer

    def create_objective(self, X, y, predictor_class, non_trained_config, n_folds=5):
        """Create an objective function that uses cross-validation for evaluation"""

        def get_param(name, default_func):
            val = non_trained_config.get(name)
            return val if val is not None else default_func()

        def objective(trial):
            config = TrainingConfig(
                dropout=get_param('dropout', lambda: trial.suggest_float('dropout', 0.3, 0.6)),
                learning_rate=get_param('learning_rate',
                                        lambda: trial.suggest_float('learning_rate', 1e-4, 1e-1, log=True)),
                weight_decay=get_param('weight_decay',
                                       lambda: trial.suggest_float('weight_decay', 1e-6, 1e-3, log=True)),
                hidden_factor=get_param('hidden_factor', lambda: trial.suggest_float('hidden_factor', 0.3, 0.8)),
                batch_size=get_param('batch_size', lambda: trial.suggest_int('batch_size', 16, 64)),
                n_layers=get_param('n_layers', lambda: trial.suggest_int('n_layers', 1, 3)),
                activation=get_param('activation',
                                     lambda: trial.suggest_categorical('activation', ['gelu', 'relu', 'leakyrelu'])),
                n_components=get_param('n_components', lambda: trial.suggest_int('n_components', 5, 30)),
                early_stopping_patience=get_param('early_stopping_patience',
                                                  lambda: trial.suggest_int('patience', 50, 80)),
                min_delta=get_param('min_delta', lambda: trial.suggest_float('min_delta', 0.01, 0.2, log=True)),
                use_pca=get_param('use_pca', lambda: trial.suggest_categorical('use_pca', [True, False])),
                lookback=get_param('lookback', lambda: trial.suggest_int('lookback', 1, 10)),
                epochs=get_param('epochs', lambda: 100)
            )

            # Run cross-validation with these hyperparameters
            cv_results = ModelEvaluator.run_cross_validation(
                predictor_class=predictor_class,
                X=X,
                y=y,
                config=config,
                train_split=None,
                n_folds=n_folds,
                seed=self.seed
            )

            # Return negative mean CV score (since Optuna minimizes)
            return -cv_results.get('mean_r2', float('inf'))

        return objective

    def optimize(self, X, y, predictor_class, non_trained_config, n_trials=50):
        """Run hyperparameter optimization"""
        study = optuna.create_study(
            sampler=optuna.samplers.TPESampler(seed=self.seed),
            direction='minimize'
        )

        objective = self.create_objective(X, y, predictor_class, non_trained_config)
        study.optimize(objective, n_trials=n_trials)

        try:
            self.visualizer.plot_param_importance(study)
            self.visualizer.plot_hyperparameters(study.best_params)
        except Exception as e:
            logging.warning(f"Failed to plot hyperparameter diagnostics: {e}")

        return study


class NeuralPredictor(BasePredictor):
    """Neural network-based time series predictor with hyperparameter optimization"""

    @staticmethod
    def _init_config(**kwargs):
        config_dict = {}
        default_config = TrainingConfig()

        for key in default_config.__dict__:
            config_dict[key] = None

        for key, value in kwargs.items():
            if key in default_config.__dict__:
                config_dict[key] = value

        if 'epochs' not in kwargs:
            config_dict['epochs'] = 100

        return TrainingConfig(**config_dict)

    def __init__(self, input_size, lookback=5, train_split=0.8,
                 use_pca=False, n_components=20, seed=42):
        super(NeuralPredictor, self).__init__()
        self._set_seeds(seed)

        self.input_size = input_size
        self.train_split = train_split
        self.seed = seed
        self.visualizer = Visualizer(self.__class__.__name__.replace('Predictor', ''))

        # Initialize components
        self.config_untrained = NeuralPredictor._init_config(
            lookback=lookback,
            use_pca=use_pca,
            n_components=n_components
        )

        self.config = TrainingConfig(
            lookback=lookback,
            use_pca=use_pca,
            n_components=n_components
        )

        self.preprocessor = TimeSeriesPreprocessor(
            lookback=lookback,
            input_size=input_size,
            use_pca=use_pca,
            n_components=n_components,
            seed=seed
        )

        self.architecture = NeuralArchitecture(
            feature_size=self.preprocessor.get_feature_size(),
            seed=seed
        )

        self.model = self.architecture.build_model(self.config)

        self.update_config_dependencies(config=self.config)

        # Initialize state variables
        self._is_fitted = False
        self.target_mean = 0.0
        self.target_std = 0.0
        self.train_indices = None
        self.val_indices = None
        self.best_config = None
        self.train_losses = []
        self.val_losses = []

    def update_config_dependencies(self, config=None):
        if config is not None:
            self.config = config

        if hasattr(self.config, 'lookback'):
            self.lookback = self.config.lookback
        if hasattr(self.config, 'use_pca'):
            self.use_pca = self.config.use_pca
        if hasattr(self.config, 'n_components'):
            self.n_components = self.config.n_components

        self.preprocessor.update_config(config=self.config)

        new_feature_size = self.preprocessor.get_feature_size()
        self.architecture.update_feature_size(new_feature_size)

        self.model = self.architecture.build_model(self.config)

        self.trainer = ModelTrainer(self.model, seed=self.seed)
        self.trainer.setup_optimizer(self.config)

    def _set_seeds(self, seed):
        """Set random seeds for reproducibility"""
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False

    def fit(self, X, y, config=None, hyper_opt=True, n_trials=100, cv=False, diagnostics=True):
        if config is not None:
            self.config = config

        # Calculate target statistics for fallback predictions
        self.target_mean = float(np.mean(y))
        self.target_std = float(np.std(y))
        logging.info(f"Target stats: mean={self.target_mean:.4f}, std={self.target_std:.4f}")

        # Step 1: Hyperparameter optimization if requested
        if hyper_opt:
            logging.info("Starting hyperparameter optimization with cross-validation")
            optimizer = HyperparameterOptimizer(seed=self.seed, visualizer=self.visualizer)
            study = optimizer.optimize(
                X=X,
                y=y,
                predictor_class=type(self),
                non_trained_config=self.config_untrained.__dict__,
                n_trials=n_trials
            )

            # Update model configuration with best parameters
            for key, value in study.best_params.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
            logging.info(f"Found best hyperparameters: {study.best_params}")

            self.update_config_dependencies(config=self.config)

        # Step 2: Cross-validation if requested (for reporting purposes only)
        cv_score = None
        if cv:
            try:
                logging.info("Running cross-validation for performance reporting")
                cv_results = ModelEvaluator.run_cross_validation(
                    predictor_class=type(self),
                    X=X,
                    y=y,
                    config=self.config,
                    train_split=self.train_split,
                    seed=self.seed
                )
                if 'mean_r2' in cv_results:
                    cv_score = cv_results['mean_r2']
                    logging.info(f"CV Mean R²: {cv_score:.3f} ± {cv_results['std_r2']:.3f}")

            except Exception as e:
                logging.warning(f"Cross-validation failed: {str(e)}. Continuing with training.")

        # Step 3: ALWAYS train the final model on all data
        logging.info("Training final model on all data")
        X_windows, y_windows, indices = self.preprocessor.transform(X, y, fit=True)

        if X_windows is None or len(X_windows) == 0:
            logging.error("Failed to create training windows")
            return 0.0

        # Convert to tensors
        X_tensor = torch.FloatTensor(X_windows)
        y_tensor = torch.FloatTensor(y_windows).view(-1, 1)

        # Train the model on ALL data
        training_result = self.trainer.train(
            X_tensor,
            y_tensor,
            indices,
            self.train_split,
            self.config
        )

        # Save results
        self.train_indices = training_result.train_indices
        self.val_indices = training_result.val_indices
        self.train_losses = training_result.train_losses
        self.val_losses = training_result.val_losses
        self._is_fitted = True

        # Calculate final performance
        train_size = int(self.train_split * len(X_tensor))
        X_val_tensor = X_tensor[train_size:]

        with torch.no_grad():
            val_pred_scaled = self.model(X_val_tensor).numpy()
            val_pred = val_pred_scaled.flatten()
            y_val = y[self.val_indices]
            val_r2 = r2_score(y_val, val_pred)

        # Run diagnostics if requested
        if diagnostics:
            logging.info("Running diagnostics")
            evaluation = ModelEvaluator.evaluate(self.model, self.preprocessor, X, y)
            if evaluation is not None:
                ModelEvaluator.run_diagnostics(
                    self.visualizer,
                    evaluation,
                    train_indices=self.train_indices,
                    val_indices=self.val_indices,
                    train_losses=self.train_losses,
                    val_losses=self.val_losses
                )

        # Return the CV score if available, otherwise the validation R²
        final_score = cv_score if cv_score is not None else val_r2
        logging.info(f"Final model score: {final_score:.4f}")
        return final_score

    def predict(self, X, **kwargs):
        """Make predictions on new data"""
        if not self._is_fitted:
            raise ValueError("Model must be fitted before prediction")

        if len(X) < self.lookback:
            logging.warning(f"Not enough data for prediction, need {self.lookback} rows")
            return np.array([self.target_mean])

        try:
            # Prepare data
            X_windows, _, indices = self.preprocessor.transform(X)

            if X_windows is None or len(X_windows) == 0:
                return np.array([self.target_mean])

            # Convert to tensor
            X_tensor = torch.FloatTensor(X_windows)

            # Make predictions
            self.model.eval()
            with torch.no_grad():
                pred_scaled = self.model(X_tensor).numpy()
                pred = pred_scaled.flatten()

            return pred

        except Exception as e:
            logging.error(f"Prediction error: {str(e)}")
            return np.array([self.target_mean])

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted
