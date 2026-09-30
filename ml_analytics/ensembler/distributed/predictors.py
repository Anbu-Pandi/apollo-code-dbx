import logging
from typing import Dict, List, Optional, Any

try:
    import pyspark.sql.functions as F
    from pyspark.sql import DataFrame as SparkDataFrame
    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False
    class SparkDataFrame: pass

from .ensemble import HybridEnsemble
from .spark_ml import SparkMLManager
from .neural_ml import NeuralMLManager


# Backwards compatibility aliases
class DistributedSparkMLManager(SparkMLManager):
    """Backwards compatibility wrapper"""
    
    def __init__(self, feature_cols: List[str], target_col: str = "target"):
        super().__init__(feature_cols, target_col)
    
    def prepare_features(self, df: SparkDataFrame) -> SparkDataFrame:
        """Prepare features (backwards compatibility)"""
        if not PYSPARK_AVAILABLE:
            raise ImportError("PySpark not available")
            
        # Handle missing values
        for col in self.feature_cols:
            df = df.fillna(0.0, subset=[col])
        return df
    
    def create_ml_pipeline(self, df, model_type: str = "rf"):
        """Backwards compatibility wrapper"""
        return self.create_pipeline(model_type)
    
    def train_model(self, df: SparkDataFrame, model_type: str = "rf", use_cv: bool = False):
        """Backwards compatibility wrapper"""
        return self.train(df, model_type)
    
    def evaluate_model(self, df: SparkDataFrame, model_type: str = "rf") -> Dict[str, float]:
        """Backwards compatibility wrapper"""
        return self.evaluate(df, model_type)


class DistributedNeuralForecastManager(NeuralMLManager):
    """Backwards compatibility wrapper for neural forecasting"""
    
    def __init__(self, predictors: List[str], partitions_path: str, 
                 num_nodes: int = 2, devices_per_node: int = 1):
        super().__init__(predictors, partitions_path, num_nodes, devices_per_node)
    
    def _train_neural_models(self, df: SparkDataFrame) -> Dict[str, Any]:
        """Backwards compatibility wrapper"""
        return self.train(df)
    
    def _predict_neural_models(self, df: SparkDataFrame):
        """Backwards compatibility wrapper"""
        return self.predict(df)


class DistributedHybridEnsemble(HybridEnsemble):
    """Main ensemble class with backwards compatibility"""
    
    def __init__(self, feature_cols: List[str], target_col: str = "target",
                 use_spark_ml: bool = True, use_neural: bool = True,
                 neural_predictors: Optional[List[str]] = None,
                 partitions_path: str = "/tmp/nf_partitions",
                 num_nodes: int = 2, devices_per_node: int = 1):
        
        super().__init__(
            feature_cols=feature_cols,
            target_col=target_col,
            use_spark_ml=use_spark_ml,
            use_neural=use_neural,
            neural_predictors=neural_predictors,
            partitions_path=partitions_path,
            num_nodes=num_nodes,
            devices_per_node=devices_per_node
        )
        
        # Backwards compatibility attributes
        self.models_trained = False
    
    def train(self, df: SparkDataFrame, train_config: Dict = None) -> Dict[str, Any]:
        """Train with backwards compatibility"""
        results = super().train(df)
        self.models_trained = True
        return results
