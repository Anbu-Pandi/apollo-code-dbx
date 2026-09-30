from neuralforecast import NeuralForecast
from neuralforecast.auto import AutoMLP

from .base_nf_predictor import BaseNFPredictor


class AutoMLPPredictor(BaseNFPredictor):
    def __init__(self, feature_names, input_size: int = 12, lookback: int = 5, train_split: float = 0.8):
        super(AutoMLPPredictor, self).__init__(feature_names, input_size, lookback, train_split)

        def config_mlp(trial):
            return {
                "futr_exog_list": self.feature_names,
                "input_size": trial.suggest_categorical("input_size", [1]),
                "hidden_size": trial.suggest_categorical("hidden_size",
                                                         [1 * input_size, 2 * input_size]),
                "num_layers": trial.suggest_categorical("num_layers", [1, 2, 3]),
                "learning_rate": trial.suggest_float("learning_rate", 1e-8, 1e-1, log=True),
                "scaler_type": 'robust',
                "max_steps": trial.suggest_categorical("max_steps", [10, 100, 500]),
                "batch_size": trial.suggest_categorical("batch_size", [16, 32, 64]),
                "val_check_steps": 25,
                "early_stop_patience_steps": trial.suggest_categorical("early_stop_patience_steps", [20, 40, 60]),
                "random_seed": self.seed,
                **self.cpu_kwargs
            }

        self.auto_model = AutoMLP(
            h=self.h,
            config=config_mlp,
            num_samples=100,
            backend=self.backend,
            search_alg=self.search_alg,
            alias=self.pred_col_name
        )

        self.nf = NeuralForecast(models=[self.auto_model], freq='1d')
