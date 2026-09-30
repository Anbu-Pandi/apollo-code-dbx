import logging
from typing import Dict, List, Any

from .dependencies import deps, get_spark_types

SparkDataFrame, F = get_spark_types()


class SparkMLManager:
    """Spark MLlib manager for distributed machine learning"""

    def __init__(self, feature_cols: List[str], target_col: str = "target"):
        self.feature_cols = feature_cols
        self.target_col = target_col
        self.models = {}
        self.pipelines = {}
        
        # Validate environment and get ML imports
        deps.validate_environment(require_spark_ml=True)
        self.ml_classes = deps.get_spark_ml_imports()

    def create_pipeline(self, model_type: str):
        """Create ML pipeline with preprocessing and model"""
        assembler = self.ml_classes['VectorAssembler'](
            inputCols=self.feature_cols,
            outputCol="raw_features"
        )

        scaler = self.ml_classes['StandardScaler'](
            inputCol="raw_features",
            outputCol="features",
            withStd=True,
            withMean=True
        )

        model = self._create_model(model_type)
        return self.ml_classes['Pipeline'](stages=[assembler, scaler, model])

    def _create_model(self, model_type: str):
        """Create specific model based on type"""
        if model_type == "rf":
            return self.ml_classes['RandomForestRegressor'](
                featuresCol="features", labelCol=self.target_col,
                numTrees=100, maxDepth=10, seed=42
            )
        elif model_type == "gbt":
            return self.ml_classes['GBTRegressor'](
                featuresCol="features", labelCol=self.target_col,
                maxIter=100, maxDepth=8, seed=42
            )
        elif model_type == "linear":
            return self.ml_classes['LinearRegression'](
                featuresCol="features", labelCol=self.target_col,
                regParam=0.01, elasticNetParam=0.5
            )
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    def train(self, df: SparkDataFrame, model_type: str) -> Any:
        """Train model and store it"""
        logging.info(f"Training {model_type} model on {df.count()} samples")
        
        # Handle missing values
        for col in self.feature_cols:
            df = df.fillna(0.0, subset=[col])

        pipeline = self.create_pipeline(model_type)
        model = pipeline.fit(df)

        self.models[model_type] = model
        self.pipelines[model_type] = pipeline

        logging.info(f"Successfully trained {model_type} model")
        return model

    def predict(self, df: SparkDataFrame, model_type: str) -> SparkDataFrame:
        """Generate predictions"""
        if model_type not in self.models:
            raise ValueError(f"Model {model_type} not trained yet")

        model = self.models[model_type]
        predictions = model.transform(df)
        return predictions.select("*", F.col("prediction").alias(f"pred_{model_type}"))

    def evaluate(self, df: SparkDataFrame, model_type: str) -> Dict[str, float]:
        """Evaluate model performance"""
        if model_type not in self.models:
            raise ValueError(f"Model {model_type} not trained yet")

        predictions_df = self.predict(df, model_type)

        metrics = {}
        for metric in ["rmse", "mae", "r2"]:
            evaluator = self.ml_classes['RegressionEvaluator'](
                labelCol=self.target_col,
                predictionCol="prediction",
                metricName=metric
            )
            metrics[metric] = evaluator.evaluate(predictions_df)

        logging.info(f"Model {model_type}: RMSE={metrics['rmse']:.4f}, "
                    f"MAE={metrics['mae']:.4f}, R2={metrics['r2']:.4f}")
        return metrics 