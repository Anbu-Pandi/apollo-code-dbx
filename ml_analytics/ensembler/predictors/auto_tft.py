from neuralforecast import NeuralForecast
from neuralforecast.auto import AutoTFT

from .base_nf_predictor import BaseNFPredictor


class AutoTFTPredictor(BaseNFPredictor):
    def __init__(self, feature_names, input_size: int = 24, lookback: int = 5, train_split: float = 0.8):
        super(AutoTFTPredictor, self).__init__(feature_names, input_size, lookback, train_split)

        def config_tft(trial):
            return {
                "futr_exog_list": self.feature_names,
                "input_size": trial.suggest_categorical("input_size", (8, 12, 16)),
                "hidden_size": trial.suggest_categorical("hidden_size", (16, 32, 64)),
                "n_head": trial.suggest_categorical("n_head", (2, 4)),
                "attn_dropout": trial.suggest_float("attn_dropout", low=0.0, high=0.3),
                "dropout": trial.suggest_float("dropout", low=0.0, high=0.3),
                "learning_rate": trial.suggest_float("learning_rate", low=1e-4, high=1e-2, log=True),
                "scaler_type": 'robust',
                "max_steps": trial.suggest_categorical("max_steps", (300, 500, 800)),
                "batch_size": trial.suggest_categorical("batch_size", (16, 32, 64)),
                "val_check_steps": trial.suggest_categorical("val_check_steps", (25, 50, 100)),
                "early_stop_patience_steps": trial.suggest_categorical("early_stop_patience_steps", (30, 50, 80)),
                "random_seed": trial.suggest_int("random_seed", low=1, high=100),
                **self.cpu_kwargs
            }

        self.auto_model = AutoTFT(
            h=self.h,
            config=config_tft,
            backend=self.backend,
            search_alg=self.search_alg,
            num_samples=12,
            alias=self.pred_col_name
        )

        self.nf = NeuralForecast(models=[self.auto_model], freq='1d')
