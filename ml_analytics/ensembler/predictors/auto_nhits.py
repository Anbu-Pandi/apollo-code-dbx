from neuralforecast import NeuralForecast
from neuralforecast.auto import AutoNHITS

from .base_nf_predictor import BaseNFPredictor


class AutoNHITSPredictor(BaseNFPredictor):
    def __init__(self, feature_names, input_size: int = 8, lookback: int = 5, train_split: float = 0.8):
        super(AutoNHITSPredictor, self).__init__(feature_names, input_size, lookback, train_split)

        def config_nhits(trial):
            return {
                "futr_exog_list": self.feature_names,
                "input_size": trial.suggest_categorical("input_size", [4, 6, 8]),
                "learning_rate": trial.suggest_categorical("learning_rate", (1e-4, 1e-3,)),
                "max_steps": 200,
                "num_lr_decays": 3,
                "early_stop_patience_steps": -1,
                "val_check_steps": 100,
                "batch_size": 32,
                "valid_batch_size": None,
                "windows_batch_size": 5,
                "inference_windows_batch_size": -1,
                "n_blocks": [1, 1, 1],
                "stack_types": ['identity', 'identity', 'identity'],
                "mlp_units": [[2, 2], [2, 2], [2, 2]],
                "n_pool_kernel_size": [1, 1, 1],
                "n_freq_downsample": [1, 1, 1],
                "pooling_mode": 'MaxPool1d',
                "dropout_prob_theta": 0.0,
                "activation": 'ReLU',
                "scaler_type": 'identity',
                "start_padding_enabled": False,
                "step_size": 1,
                "drop_last_loader": False,
                "random_seed": 1,
                "alias": None,
                **self.cpu_kwargs
            }

        self.auto_model = AutoNHITS(
            h=self.h,
            config=config_nhits,
            backend=self.backend,
            search_alg=self.search_alg,
            num_samples=2,
            alias=self.pred_col_name
        )

        self.nf = NeuralForecast(models=[self.auto_model], freq='1d')
