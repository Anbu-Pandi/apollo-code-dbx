import logging
import numpy as np
from neuralforecast import NeuralForecast
from neuralforecast.auto import AutoNLinear
from .base_nf_predictor import BaseNFPredictor


class AutoNLinearPredictor(BaseNFPredictor):
    def __init__(self, feature_names, input_size: int = 48, lookback: int = 5, train_split: float = 0.8):
        super(AutoNLinearPredictor, self).__init__(feature_names, input_size, lookback, train_split)

        def config_nlinear(trial):
            return {
                "input_size": trial.suggest_categorical("input_size", (8, 12, 16, 24)),
                "learning_rate": trial.suggest_float("learning_rate", low=1e-4, high=1e-2, log=True),
                "scaler_type": 'robust',
                "max_steps": trial.suggest_categorical("max_steps", (300, 500, 800)),
                "batch_size": trial.suggest_categorical("batch_size", (16, 32, 64)),
                "val_check_steps": trial.suggest_categorical("val_check_steps", (25, 50, 100)),
                "early_stop_patience_steps": trial.suggest_categorical("early_stop_patience_steps", (30, 50, 80)),
                "random_seed": trial.suggest_int("random_seed", low=1, high=100),
                **self.cpu_kwargs
            }

        self.auto_model = AutoNLinear(
            h=self.h,
            config=config_nlinear,
            backend=self.backend,
            search_alg=self.search_alg,
            num_samples=15,
            alias=self.pred_col_name
        )

        self.nf = NeuralForecast(models=[self.auto_model], freq='1d')