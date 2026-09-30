import logging
from typing import Dict, List, Optional, Any

try:
    import pyspark.sql.functions as F
    from pyspark.sql import DataFrame as SparkDataFrame
    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False
    # Create dummy types for linting
    class SparkDataFrame: pass

from .spark_ml import SparkMLManager
from .neural_ml import NeuralMLManager


class HybridEnsemble:
    """Hybrid ensemble combining Spark ML and neural forecasting"""

    def __init__(self, feature_cols: List[str], target_col: str = "target",
                 use_spark_ml: bool = True, use_neural: bool = True,
                 neural_predictors: Optional[List[str]] = None,
                 partitions_path: str = "/tmp/nf_partitions",
                 num_nodes: int = 2, devices_per_node: int = 1):
        
        self.feature_cols = feature_cols
        self.target_col = target_col
        self.use_spark_ml = use_spark_ml
        self.use_neural = use_neural
        self.ensemble_weights = {}
        self.is_fitted = False

        # Initialize components
        if self.use_spark_ml:
            self.spark_ml = SparkMLManager(feature_cols, target_col)

        if self.use_neural:
            self.neural_ml = NeuralMLManager(
                neural_predictors or ['auto_nhits', 'lstm'],
                partitions_path, num_nodes, devices_per_node
            )

    def fit(self, df: SparkDataFrame) -> 'HybridEnsemble':
        """Fit ensemble models"""
        results = self.train(df)
        return self

    def train(self, df: SparkDataFrame) -> Dict[str, Any]:
        """Train all ensemble components"""
        logging.info("Training hybrid ensemble...")
        results = {"spark_ml": {}, "neural": {}}

        # Train Spark ML models
        if self.use_spark_ml:
            for model_type in ["rf", "gbt", "linear"]:
                try:
                    self.spark_ml.train(df, model_type)
                    metrics = self.spark_ml.evaluate(df, model_type)
                    results["spark_ml"][model_type] = metrics
                except Exception as e:
                    logging.error(f"Spark ML {model_type} training failed: {e}")
                    results["spark_ml"][model_type] = {"error": str(e)}

        # Train neural models
        if self.use_neural:
            try:
                neural_results = self.neural_ml.train(df)
                results["neural"] = neural_results
            except Exception as e:
                logging.error(f"Neural training failed: {e}")
                results["neural"] = {"error": str(e)}

        # Calculate ensemble weights
        self._calculate_weights(results)
        self.is_fitted = True
        
        logging.info("Hybrid ensemble training completed")
        return results

    def predict(self, df: SparkDataFrame) -> SparkDataFrame:
        """Generate ensemble predictions"""
        if not self.is_fitted:
            raise ValueError("Ensemble not trained yet")

        predictions_df = df
        pred_cols = []

        # Get Spark ML predictions
        if self.use_spark_ml:
            for model_type in ["rf", "gbt", "linear"]:
                if model_type in self.spark_ml.models:
                    try:
                        pred_df = self.spark_ml.predict(df, model_type)
                        predictions_df = pred_df
                        pred_cols.append(f"pred_{model_type}")
                    except Exception as e:
                        logging.error(f"Spark ML {model_type} prediction failed: {e}")

        # Get neural predictions
        if self.use_neural:
            try:
                neural_preds = self.neural_ml.predict(df)
                if neural_preds is not None:
                    # Simple join logic (would need proper implementation)
                    pred_cols.extend([col for col in neural_preds.columns 
                                    if col.endswith("_prediction")])
            except Exception as e:
                logging.error(f"Neural prediction failed: {e}")

        # Create ensemble prediction
        if pred_cols:
            predictions_df = self._combine_predictions(predictions_df, pred_cols)

        return predictions_df

    def _combine_predictions(self, df: SparkDataFrame, pred_cols: List[str]) -> SparkDataFrame:
        """Combine individual predictions into ensemble"""
        if not pred_cols:
            return df

        if self.ensemble_weights:
            # Weighted combination
            weighted_exprs = []
            total_weight = 0.0
            
            for col_name in pred_cols:
                weight = self._get_weight_for_column(col_name)
                if weight > 0:
                    weighted_exprs.append(f"coalesce({col_name}, 0.0) * {weight}")
                    total_weight += weight
            
            if weighted_exprs and total_weight > 0:
                ensemble_expr = f"({' + '.join(weighted_exprs)}) / {total_weight}"
            else:
                ensemble_expr = self._simple_average_expr(pred_cols)
        else:
            ensemble_expr = self._simple_average_expr(pred_cols)

        return df.withColumn("ensemble_prediction", F.expr(ensemble_expr))

    def _simple_average_expr(self, pred_cols: List[str]) -> str:
        """Create simple average expression"""
        coalesced_cols = [f"coalesce({col}, 0.0)" for col in pred_cols]
        return f"({' + '.join(coalesced_cols)}) / {len(pred_cols)}"

    def _get_weight_for_column(self, col_name: str) -> float:
        """Get weight for prediction column"""
        for weight_key, weight_value in self.ensemble_weights.items():
            if weight_key in col_name:
                return weight_value
        return 1.0 / len(self.ensemble_weights) if self.ensemble_weights else 1.0

    def _calculate_weights(self, results: Dict[str, Any]) -> None:
        """Calculate ensemble weights based on performance"""
        weights = {}
        
        # Spark ML weights based on R2 scores
        if "spark_ml" in results:
            for model_type, metrics in results["spark_ml"].items():
                if isinstance(metrics, dict) and "r2" in metrics:
                    weights[f"spark_{model_type}"] = max(0.1, metrics["r2"])

        # Neural weights (default for now)
        if "neural" in results and results["neural"].get("status") == "success":
            weights["neural"] = 0.5

        # Normalize weights
        if weights:
            total = sum(weights.values())
            self.ensemble_weights = {k: v/total for k, v in weights.items()}
        
        logging.info(f"Ensemble weights: {self.ensemble_weights}")

    def evaluate(self, df: SparkDataFrame) -> Dict[str, float]:
        """Evaluate ensemble performance"""
        if not self.is_fitted:
            return {"error": "Not fitted"}

        try:
            pred_df = self.predict(df)
            
            # Basic metrics
            if "ensemble_prediction" in pred_df.columns and self.target_col in pred_df.columns:
                correlation = pred_df.stat.corr("ensemble_prediction", self.target_col)
                return {"ensemble_correlation": correlation}
            else:
                return {"status": "predictions_generated"}
                
        except Exception as e:
            logging.error(f"Ensemble evaluation failed: {e}")
            return {"error": str(e)} 