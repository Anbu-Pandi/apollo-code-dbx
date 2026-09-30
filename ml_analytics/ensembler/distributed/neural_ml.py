import logging
from typing import Dict, List, Any, Optional

from .dependencies import deps, get_spark_types

SparkDataFrame, F = get_spark_types()


class NeuralMLManager:
    """Neural forecasting manager using neuralforecast DistributedConfig"""

    def __init__(self, predictors: List[str], partitions_path: str, 
                 num_nodes: int = 2, devices_per_node: int = 1):
        self.predictors = predictors
        self.partitions_path = partitions_path
        self.num_nodes = num_nodes
        self.devices_per_node = devices_per_node
        self.nf_model = None
        self.is_trained = False

        deps.validate_environment(require_neural=True)

    def create_models(self) -> List[Any]:
        """Create neural forecasting models using proper auto model pattern"""
        neural_classes = deps.require_neuralforecast()
        
        # Training parameters for config functions (auto models)
        training_kwargs = {
            'accelerator': 'gpu' if self.devices_per_node > 0 else 'cpu',
            'enable_progress_bar': False,
            'logger': False,
            'enable_checkpointing': False,
        }
        
        # Regular models accept distributed configuration directly
        distributed_kwargs = {
            'accelerator': 'gpu' if self.devices_per_node > 0 else 'cpu',
            'enable_progress_bar': False,
            'logger': False,
            'enable_checkpointing': False,
        }

        models = []
        for predictor in self.predictors:
            try:
                if predictor == 'auto_nhits':
                    # Auto models use config function pattern like in predictors folder
                    def config_nhits(trial):
                        return {
                            "input_size": trial.suggest_categorical("input_size", [12, 18, 24]),
                            "learning_rate": trial.suggest_categorical("learning_rate", [1e-4, 1e-3]),
                            "max_steps": 500,
                            "batch_size": 32,
                            "n_blocks": [1, 1, 1],
                            "stack_types": ['identity', 'identity', 'identity'],
                            "mlp_units": [[2, 2], [2, 2], [2, 2]],
                            **training_kwargs
                        }
                    
                    # Import optuna locally if needed
                    try:
                        import optuna
                        search_alg = optuna.samplers.TPESampler(seed=42)
                    except ImportError:
                        search_alg = None
                    
                    models.append(neural_classes['AutoNHITS'](
                        h=1,
                        config=config_nhits,
                        backend='optuna',
                        search_alg=search_alg,
                        num_samples=2,  # Quick for distributed
                        alias=f'auto_nhits_dist'
                    ))
                    
                elif predictor == 'auto_tft':
                    # Auto TFT with config function
                    def config_tft(trial):
                        return {
                            "input_size": trial.suggest_categorical("input_size", [12, 16, 24]),
                            "hidden_size": trial.suggest_categorical("hidden_size", [16, 32]),
                            "n_head": trial.suggest_categorical("n_head", [2, 4]),
                            "learning_rate": trial.suggest_categorical("learning_rate", [1e-4, 1e-3]),
                            "max_steps": 500,
                            "batch_size": 32,
                            **training_kwargs
                        }
                    
                    # Import optuna locally if needed
                    try:
                        import optuna
                        search_alg = optuna.samplers.TPESampler(seed=42)
                    except ImportError:
                        search_alg = None
                    
                    models.append(neural_classes['AutoTFT'](
                        h=1,
                        config=config_tft,
                        backend='optuna',
                        search_alg=search_alg,
                        num_samples=2,  # Quick for distributed
                        alias=f'auto_tft_dist'
                    ))
                    
                elif predictor == 'auto_lstm':
                    # Auto LSTM with config function
                    def config_lstm(trial):
                        return {
                            "input_size": trial.suggest_categorical("input_size", [12, 18, 24]),
                            "encoder_hidden_size": trial.suggest_categorical("encoder_hidden_size", [24, 48]),
                            "encoder_n_layers": trial.suggest_categorical("encoder_n_layers", [1, 2]),
                            "learning_rate": trial.suggest_categorical("learning_rate", [1e-4, 1e-3]),
                            "max_steps": 500,
                            "batch_size": 32,
                            **training_kwargs
                        }
                    
                    # Import optuna locally if needed
                    try:
                        import optuna
                        search_alg = optuna.samplers.TPESampler(seed=42)
                    except ImportError:
                        search_alg = None
                    
                    models.append(neural_classes['AutoLSTM'](
                        h=1,
                        config=config_lstm,
                        backend='optuna',
                        search_alg=search_alg,
                        num_samples=2,  # Quick for distributed
                        alias=f'auto_lstm_dist'
                    ))
                    
                elif predictor == 'nhits':
                    models.append(neural_classes['NHITS'](h=1, input_size=24, max_steps=2000, **distributed_kwargs))
                elif predictor == 'lstm':
                    models.append(neural_classes['LSTM'](h=1, input_size=24, max_steps=2000, **distributed_kwargs))
                else:
                    logging.warning(f"Unknown neural predictor: {predictor}")
                    
                logging.info(f"Successfully created {predictor} model")
                
            except Exception as e:
                logging.error(f"Failed to create {predictor} model: {e}")
                # Skip this model and continue with others

        return models

    def train(self, df: SparkDataFrame) -> Dict[str, Any]:
        """Train neural models using DistributedConfig"""
        logging.info(f"Training neural models: {self.predictors}")

        try:
            neural_classes = deps.require_neuralforecast()
            
            # Configure distributed training
            dist_cfg = neural_classes['DistributedConfig'](
                partitions_path=self.partitions_path,
                num_nodes=self.num_nodes,
                devices=self.devices_per_node,
            )

            # Create models
            models = self.create_models()
            
            self.nf_model = neural_classes['NeuralForecast'](models=models, freq=1)
            
            # Train with distributed config
            self.nf_model.fit(df, distributed_config=dist_cfg, val_size=24)
            
            self.is_trained = True
            
            return {"status": "success", "models_trained": len(models)}
            
        except Exception as e:
            logging.error(f"Neural training failed: {e}")
            return {"status": "error", "error": str(e)}

    def predict(self, df: SparkDataFrame) -> Optional[SparkDataFrame]:
        """Generate neural predictions"""
        if not self.is_trained or self.nf_model is None:
            raise ValueError("Neural models not trained yet")

        try:
            # Use native Spark prediction
            from data_access.spark.api import sparkmanager
            predictions = self.nf_model.predict(
                futr_df=df.drop("y") if "y" in df.columns else df, 
                engine=sparkmanager._spark
            )
            return predictions
            
        except Exception as e:
            logging.error(f"Neural prediction failed: {e}")
            return None

    def evaluate(self, df: SparkDataFrame) -> Dict[str, Any]:
        """Evaluate neural model performance"""
        if not self.is_trained:
            return {"error": "Models not trained"}

        try:
            predictions = self.predict(df)
            if predictions is None:
                return {"error": "Prediction failed"}

            # Simple evaluation metrics
            metrics = {
                "models_count": len(self.predictors),
                "prediction_columns": len([col for col in predictions.columns if col.endswith("_prediction")])
            }
            
            return metrics
            
        except Exception as e:
            return {"error": str(e)} 