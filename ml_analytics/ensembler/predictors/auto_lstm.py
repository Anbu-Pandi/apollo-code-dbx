from neuralforecast import NeuralForecast
from neuralforecast.auto import AutoLSTM

from .base_nf_predictor import BaseNFPredictor


class AutoLSTMPredictor(BaseNFPredictor):
    def __init__(self, feature_names, input_size: int = 48, lookback: int = 5, train_split: float = 0.8):
        super(AutoLSTMPredictor, self).__init__(feature_names, input_size, lookback, train_split)

        def config_lstm(trial):
            return {
                "futr_exog_list": self.feature_names,
                "input_size": trial.suggest_categorical("input_size", [1]),
                "encoder_hidden_size": trial.suggest_categorical("encoder_hidden_size",
                                                                 (1 * input_size, 2 * input_size, 3 * input_size)),
                "encoder_n_layers": trial.suggest_categorical("encoder_n_layers", (1, 2, 4)),
                "decoder_hidden_size": trial.suggest_categorical("decoder_hidden_size",
                                                                 (1 * input_size, 2 * input_size, 3 * input_size)),
                "decoder_layers": trial.suggest_categorical("decoder_layers", (2, 4, 8)),
                "encoder_dropout": trial.suggest_float("encoder_dropout", 0.1, 0.5),
                "learning_rate": trial.suggest_float("learning_rate", low=1e-6, high=1e-1, log=True),
                "scaler_type": 'robust',
                "max_steps": trial.suggest_categorical("max_steps", (100,)),
                "batch_size": trial.suggest_categorical("batch_size", (32, 64, 128)),
                "val_check_steps": trial.suggest_categorical("val_check_steps", (25,)),
                "early_stop_patience_steps": trial.suggest_categorical("early_stop_patience_steps", (20, 30, 40, 50)),
                "random_seed": self.seed,
                **self.cpu_kwargs
            }

        self.auto_model = AutoLSTM(
            h=self.h,
            config=config_lstm,
            num_samples=100,
            backend=self.backend,
            search_alg=self.search_alg,
            alias=self.pred_col_name
        )

        self.nf = NeuralForecast(models=[self.auto_model], freq='1d')
