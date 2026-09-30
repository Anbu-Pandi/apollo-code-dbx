"""
Dependency management for distributed statistical arbitrage
Centralizes optional imports and provides clean fallbacks
"""

import logging
from typing import Optional, Any, Dict
from dataclasses import dataclass


@dataclass
class DependencyStatus:
    """Track availability of optional dependencies"""
    pyspark: bool = False
    neuralforecast: bool = False
    optuna: bool = False
    torch: bool = False
    pandas: bool = False


class DependencyManager:
    """Centralized dependency management"""
    
    def __init__(self):
        self.status = DependencyStatus()
        self._spark_module = None
        self._neural_module = None
        self._check_dependencies()
    
    def _check_dependencies(self):
        """Check which dependencies are available"""
        # Check PySpark
        try:
            import pyspark
            self.status.pyspark = True
            logging.debug("PySpark available")
        except ImportError:
            logging.debug("PySpark not available")
        
        # Check neuralforecast
        try:
            import neuralforecast
            self.status.neuralforecast = True
            logging.debug("neuralforecast available")
        except ImportError:
            logging.debug("neuralforecast not available")
        
        # Check other dependencies
        for dep in ['optuna', 'torch', 'pandas']:
            try:
                __import__(dep)
                setattr(self.status, dep, True)
                logging.debug(f"{dep} available")
            except ImportError:
                logging.debug(f"{dep} not available")
    
    def require_pyspark(self):
        """Ensure PySpark is available"""
        if not self.status.pyspark:
            raise ImportError("PySpark is required but not available. Install with: pip install pyspark")
        
        if self._spark_module is None:
            import pyspark.sql.functions as F
            from pyspark.sql import DataFrame as SparkDataFrame
            self._spark_module = {'F': F, 'SparkDataFrame': SparkDataFrame}
        
        return self._spark_module
    
    def require_neuralforecast(self):
        """Ensure neuralforecast is available"""
        if not self.status.neuralforecast:
            raise ImportError("neuralforecast is required but not available. Install with: pip install neuralforecast")
        
        if self._neural_module is None:
            from neuralforecast import NeuralForecast, DistributedConfig
            from neuralforecast.auto import AutoNHITS, AutoLSTM, AutoTFT
            from neuralforecast.models import NHITS, LSTM
            
            self._neural_module = {
                'NeuralForecast': NeuralForecast,
                'DistributedConfig': DistributedConfig,
                'AutoNHITS': AutoNHITS,
                'AutoLSTM': AutoLSTM,
                'AutoTFT': AutoTFT,
                'NHITS': NHITS,
                'LSTM': LSTM
            }
        
        return self._neural_module
    
    def get_spark_ml_imports(self):
        """Get Spark ML imports with Connect compatibility"""
        if not self.status.pyspark:
            raise ImportError("PySpark not available")
        
        try:
            # Try Databricks Connect imports first
            from pyspark.ml.connect.pipeline import Pipeline
            from pyspark.ml.connect.evaluation import RegressionEvaluator
            from pyspark.ml.connect.feature import StandardScaler
            from pyspark.ml.regression import RandomForestRegressor, GBTRegressor, LinearRegression
            from pyspark.ml.feature import VectorAssembler
            
            return {
                'Pipeline': Pipeline,
                'RegressionEvaluator': RegressionEvaluator,
                'StandardScaler': StandardScaler,
                'VectorAssembler': VectorAssembler,
                'RandomForestRegressor': RandomForestRegressor,
                'GBTRegressor': GBTRegressor,
                'LinearRegression': LinearRegression,
                'connect_mode': True
            }
        except ImportError:
            # Fallback to standard PySpark ML
            from pyspark.ml import Pipeline
            from pyspark.ml.evaluation import RegressionEvaluator
            from pyspark.ml.feature import StandardScaler, VectorAssembler
            from pyspark.ml.regression import RandomForestRegressor, GBTRegressor, LinearRegression
            
            return {
                'Pipeline': Pipeline,
                'RegressionEvaluator': RegressionEvaluator,
                'StandardScaler': StandardScaler,
                'VectorAssembler': VectorAssembler,
                'RandomForestRegressor': RandomForestRegressor,
                'GBTRegressor': GBTRegressor,
                'LinearRegression': LinearRegression,
                'connect_mode': False
            }
    
    def validate_environment(self, require_neural: bool = False, require_spark_ml: bool = False):
        """Validate that required dependencies are available"""
        missing = []
        
        if require_neural and not self.status.neuralforecast:
            missing.append("neuralforecast")
        
        if require_spark_ml and not self.status.pyspark:
            missing.append("pyspark")
        
        if missing:
            raise EnvironmentError(
                f"Missing required dependencies: {missing}. "
                f"Install with: pip install {' '.join(missing)}"
            )
        
        return True


# Global dependency manager instance
deps = DependencyManager()


def get_spark_types():
    """Get Spark types with fallback for linting"""
    try:
        spark_module = deps.require_pyspark()
        return spark_module['SparkDataFrame'], spark_module['F']
    except ImportError:
        # Fallback for linting environments
        class SparkDataFrame:
            pass
        
        class F:
            @staticmethod
            def col(name): return name
            @staticmethod
            def lit(value): return value
            @staticmethod
            def expr(expr): return expr
        
        return SparkDataFrame, F


def get_neural_classes():
    """Get neural forecasting classes"""
    return deps.require_neuralforecast()


def get_spark_ml_classes():
    """Get Spark ML classes"""
    return deps.get_spark_ml_imports() 