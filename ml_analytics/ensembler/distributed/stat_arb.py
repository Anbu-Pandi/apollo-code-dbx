import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import sys
import os



from pyspark.sql import DataFrame as SparkDataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from ml_analytics.ensembler.distributed.config import DistributedStatArbConfig
from ml_analytics.ensembler.distributed.signal_generator import DistributedSignalGenerator
from ml_analytics.ensembler.distributed.visualizer import DistributedVisualizer
from data_access.spark.api import sparkmanager
from ml_analytics.ensembler.distributed.data_manager import DistributedDataManager
from ml_analytics.ensembler.distributed.predictors import DistributedHybridEnsemble


class DistributedStatArb:
    """
    Distributed Statistical Arbitrage using hybrid ML approach:
    - Spark ML models for traditional ML (fully distributed)
    - neuralforecast DistributedConfig for neural models (native distributed)
    - Comprehensive visualization and analytics
    """
    
    def __init__(self, config: DistributedStatArbConfig):
        self.config = config
        self.config.validate_config()
        
        # Initialize Spark with proper error handling
        if not hasattr(sparkmanager, '_spark') or sparkmanager._spark is None:
            try:
                sparkmanager.init()
            except Exception as e:
                logging.warning(f"Failed to initialize Spark: {str(e)}")
        
        self.spark = sparkmanager._spark
        
        # Apply optimized Spark configuration only if Spark is available
        if self.spark is not None:
            logging.info("Applying optimized Spark configuration")
            spark_config = config.get_spark_config_dict()
            applied_count = 0
            for key, value in spark_config.items():
                try:
                    self.spark.conf.set(key, value)
                    applied_count += 1
                except Exception as e:
                    logging.debug(f"Skipped {key}: {str(e)}")
            logging.info(f"Applied {applied_count}/{len(spark_config)} Spark configuration settings")
        else:
            logging.error("Spark session not available. This system requires a Spark cluster.")
            logging.error("Please ensure you are running in a Databricks environment or have Spark properly configured.")
            raise RuntimeError("Spark session required for distributed statistical arbitrage. "
                             "Please run in a Databricks environment or configure Spark properly.")
        
        # Create DistributedDataManager that uses original pandas data loading
        logging.info("Creating DistributedDataManager with original pandas data loading")
        self.data_manager = DistributedDataManager(
            features=[config.y_feature] + config.x_features,
            return_type=config.get_return_types_mapping(),
            window_days=config.window_days,
            periodicity=config.periodicity,
            id_column=config.id_column,
            num_partitions=config.spark_config.num_partitions
        )
        
        self.signal_generator = DistributedSignalGenerator(
            y_feature=config.y_feature,
            x_features=config.x_features,
            signal_params=config.signal_params,
            use_spark_ml=config.model_config.use_spark_ml,
            use_neural=config.model_config.use_neural,
            neural_predictors=config.neural_config.predictors,
            partitions_path=config.neural_config.partitions_path,
            num_nodes=config.neural_config.num_nodes,
            devices_per_node=config.neural_config.devices_per_node
        )
        
        # Initialize distributed visualizer
        self.visualizer = DistributedVisualizer(
            y_feature=config.y_feature,
            x_features=config.x_features,
            models=self._get_model_names()
        )
        
        self.is_trained = False
        self.training_metrics = {}
        self.latest_signals = None
        self.latest_backtest = None

    def _get_model_names(self) -> list:
        """Get list of model names for visualization"""
        models = []
        if self.config.model_config.use_spark_ml:
            models.extend(self.config.model_config.spark_ml_models)
        if self.config.model_config.use_neural:
            models.extend(self.config.neural_config.predictors)
        models.append('ensemble')
        return models

    def load_data(self, start_date: str, end_date: str) -> SparkDataFrame:
        """Load and prepare data using original pandas approach converted to Spark"""
        logging.info(f"Loading data using original pandas DataManager: {start_date} to {end_date}")
        
        # Use the DistributedDataManager which leverages original pandas loading
        df = self.data_manager.load_data(start_date=start_date, end_date=end_date)
        
        # Calculate returns using Spark operations
        df = self.data_manager.calculate_returns_spark(df)
        
        # Cache for performance
        df = df.cache()
        df.count()  # Force caching
        
        row_count = df.count()
        logging.info(f"Loaded and processed {row_count} rows using distributed processing")
        
        return df

    def train(self, start_date: str, end_date: str, save_diagnostics: bool = True) -> Dict[str, float]:
        """Train hybrid ensemble using distributed approach"""
        logging.info("Starting distributed hybrid training (Spark ML + neuralforecast)")
        
        training_data = self.load_data(start_date, end_date)
        
        # Generate features using existing feature engineering
        logging.info("Generating distributed features")
        training_data = self._generate_features(training_data)
        
        # Fit hybrid predictors (Spark ML + neural with DistributedConfig)
        logging.info("Training hybrid ensemble models")
        self.signal_generator.fit_predictors(training_data)
        
        # Evaluate training performance
        logging.info("Evaluating training performance")
        self.training_metrics = self.signal_generator.predictor.evaluate(training_data)
        
        self.is_trained = True
        
        # Save model diagnostics if requested
        if save_diagnostics:
            self.visualizer.save_model_diagnostics(
                self.training_metrics, 
                f"training_diagnostics_{start_date}_{end_date}.html"
            )
        
        logging.info(f"Training completed. Metrics: {self.training_metrics}")
        return self.training_metrics

    def _generate_features(self, df: SparkDataFrame) -> SparkDataFrame:
        """Generate features using distributed processing"""
        
        # Calculate returns for all features
        feature_cols = [self.config.y_feature] + self.config.x_features
        
        for feature in feature_cols:
            if f"{feature}_return" not in df.columns:
                window_spec = Window.partitionBy("_group").orderBy("asof_date")
                df = df.withColumn(
                    f"{feature}_return",
                    (F.col(feature) / F.lag(feature, 1).over(window_spec) - 1.0)
                )
        
        # Create lagged features
        window_spec = Window.partitionBy("_group").orderBy("asof_date")
        
        for feature in feature_cols:
            feature_col = f"{feature}_return"
            for lag in range(1, 6):
                df = df.withColumn(
                    f"{feature_col}_lag_{lag}",
                    F.lag(feature_col, lag).over(window_spec)
                )
        
        # Remove rows with NaN values
        df = df.dropna()
        
        return df

    def generate_live_signals(self, date: str, create_dashboard: bool = True) -> SparkDataFrame:
        """Generate signals for a specific date using hybrid models"""
        if not self.is_trained:
            raise ValueError("Model must be trained before generating signals")
        
        logging.info(f"Generating live signals for {date}")
        
        # Load data up to the signal date
        signal_data = self.load_data(
            start_date=(datetime.strptime(date, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d'),
            end_date=date
        )
        
        # Generate features
        signal_data = self._generate_features(signal_data)
        
        # Generate signals using hybrid ensemble
        signals_df = self.signal_generator.generate_signals(signal_data)
        
        # Filter to signal date only
        signals_df = signals_df.filter(F.col("asof_date") == date)
        
        # Store for visualization
        self.latest_signals = signals_df
        
        # Create dashboard if requested
        if create_dashboard:
            logging.info("Creating live signals dashboard")
            self.visualizer.create_dashboard(signals_df)
        
        logging.info(f"Generated {signals_df.count()} signals for {date}")
        return signals_df

    def backtest(self, start_date: str, end_date: str, 
                 initial_capital: float = 100000, 
                 create_dashboard: bool = True) -> Tuple[SparkDataFrame, Dict[str, float]]:
        """Run distributed backtest using hybrid ensemble"""
        if not self.is_trained:
            raise ValueError("Model must be trained before backtesting")
        
        logging.info(f"Running distributed backtest: {start_date} to {end_date}")
        
        # Load backtest data
        backtest_data = self.load_data(start_date, end_date)
        backtest_data = self._generate_features(backtest_data)
        
        # Run backtest using signal generator
        backtest_results = self.signal_generator.backtest_signals(
            backtest_data, initial_capital
        )
        
        # Calculate backtest metrics
        metrics = self._calculate_backtest_metrics(backtest_results, initial_capital)
        
        # Store for visualization
        self.latest_backtest = backtest_results
        
        # Create comprehensive dashboard
        if create_dashboard:
            logging.info("Creating backtest dashboard")
            if self.latest_signals is not None:
                self.visualizer.create_dashboard(self.latest_signals, backtest_results)
            else:
                # Generate recent signals for dashboard
                recent_signals = self.signal_generator.generate_signals(
                    backtest_data.filter(F.col("asof_date") == end_date)
                )
                self.visualizer.create_dashboard(recent_signals, backtest_results)
        
        logging.info(f"Backtest completed. Metrics: {metrics}")
        return backtest_results, metrics

    def _calculate_backtest_metrics(self, backtest_df: SparkDataFrame, 
                                   initial_capital: float) -> Dict[str, float]:
        """Calculate comprehensive backtest metrics"""
        
        # Aggregate metrics across all groups
        agg_metrics = backtest_df.agg(
            F.last("portfolio_value").alias("final_value"),
            F.min("drawdown").alias("max_drawdown"),
            F.stddev("signal_return").alias("return_volatility"),
            F.mean("signal_return").alias("mean_return"),
            F.count("signal_return").alias("total_trades"),
            F.sum(F.when(F.col("signal_return") > 0, 1).otherwise(0)).alias("winning_trades"),
            F.corr("final_signal", "forward_return").alias("signal_correlation")
        ).collect()[0]
        
        # Calculate derived metrics
        total_return = float(agg_metrics["final_value"]) / initial_capital - 1.0
        annualized_return = total_return * 252  # Assuming daily frequency
        volatility = float(agg_metrics["return_volatility"] or 0) * (252 ** 0.5)
        sharpe_ratio = annualized_return / volatility if volatility > 0 else 0
        win_rate = float(agg_metrics["winning_trades"]) / float(agg_metrics["total_trades"]) if agg_metrics["total_trades"] > 0 else 0
        
        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": float(agg_metrics["max_drawdown"] or 0),
            "win_rate": win_rate,
            "total_trades": int(agg_metrics["total_trades"]),
            "signal_correlation": float(agg_metrics["signal_correlation"] or 0)
        }

    def batch_inference(self, start_date: str, end_date: str, 
                       output_table: Optional[str] = None,
                       create_dashboard: bool = True) -> SparkDataFrame:
        """Run batch inference for a date range"""
        if not self.is_trained:
            raise ValueError("Model must be trained before batch inference")
        
        logging.info(f"Running batch inference: {start_date} to {end_date}")
        
        # Load data for inference
        inference_data = self.load_data(start_date, end_date)
        inference_data = self._generate_features(inference_data)
        
        # Generate signals for all dates
        signals_df = self.signal_generator.generate_signals(inference_data)
        
        # Store for visualization
        self.latest_signals = signals_df
        
        # Create dashboard if requested
        if create_dashboard:
            logging.info("Creating batch inference dashboard")
            self.visualizer.create_dashboard(signals_df, self.latest_backtest)
        
        # Save to output table if specified
        if output_table:
            logging.info(f"Saving results to {output_table}")
            signals_df.write.mode("overwrite").saveAsTable(output_table)
        
        logging.info(f"Batch inference completed for {signals_df.count()} records")
        return signals_df

    def create_dashboard(self, include_backtest: bool = True) -> str:
        """Create comprehensive dashboard from latest data"""
        if self.latest_signals is None:
            raise ValueError("No signals available. Run generate_live_signals() or batch_inference() first.")
        
        backtest_data = self.latest_backtest if include_backtest else None
        return self.visualizer.create_dashboard(self.latest_signals, backtest_data)

    def evaluate_model(self) -> Dict[str, float]:
        """Evaluate the trained hybrid model"""
        if not self.is_trained:
            raise ValueError("Model must be trained before evaluation")
        
        metrics = {}
        metrics.update(self.training_metrics)
        
        # Add model-specific metrics
        if self.signal_generator.predictor.use_spark_ml:
            metrics["uses_spark_ml"] = 1.0
            metrics["spark_ml_models"] = len(self.config.model_config.spark_ml_models)
        
        if self.signal_generator.predictor.use_neural:
            metrics["uses_neural"] = 1.0
            metrics["neural_models"] = len(self.config.neural_config.predictors)
            metrics["neural_nodes"] = self.config.neural_config.num_nodes
            metrics["neural_devices"] = self.config.neural_config.devices_per_node
        
        return metrics

    def get_feature_importance(self) -> Optional[SparkDataFrame]:
        """Get feature importance from Spark ML models"""
        if not self.is_trained or not self.signal_generator.predictor.use_spark_ml:
            return None
        
        try:
            # Extract feature importances from Spark ML models
            importances = {}
            
            for name, pipeline in self.signal_generator.predictor.spark_ml.pipelines.items():
                model = pipeline.stages[-1]  # Last stage is the model
                
                if hasattr(model, 'featureImportances'):
                    feature_names = self.signal_generator.predictor.feature_cols
                    importance_values = model.featureImportances.toArray()
                    
                    for i, feature in enumerate(feature_names):
                        if feature not in importances:
                            importances[feature] = 0.0
                        importances[feature] += importance_values[i] / len(self.signal_generator.predictor.spark_ml.pipelines)
            
            # Convert to Spark DataFrame
            if importances:
                importance_data = [(feature, float(importance)) for feature, importance in importances.items()]
                importance_df = self.spark.createDataFrame(importance_data, ["feature", "importance"])
                return importance_df.orderBy(F.desc("importance"))
        
        except Exception as e:
            logging.error(f"Error extracting feature importance: {str(e)}")
        
        return None

    def get_dashboard_url(self) -> str:
        """Get URL to the generated dashboard"""
        import os
        dashboard_path = os.path.join(os.getcwd(), 'distributed_stats_page.html')
        if os.path.exists(dashboard_path):
            return f"file://{dashboard_path}"
        else:
            return "Dashboard not yet generated. Run create_dashboard() first."

    def export_results(self, output_dir: str = "stat_arb_results"):
        """Export all results and visualizations"""
        import os
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            # Export training metrics
            if self.training_metrics:
                import json
                with open(os.path.join(output_dir, "training_metrics.json"), 'w') as f:
                    json.dump(self.training_metrics, f, indent=2)
            
            # Export latest signals
            if self.latest_signals:
                self.latest_signals.coalesce(1).write.mode("overwrite") \
                    .option("header", "true") \
                    .csv(os.path.join(output_dir, "latest_signals"))
            
            # Export backtest results
            if self.latest_backtest:
                self.latest_backtest.coalesce(1).write.mode("overwrite") \
                    .option("header", "true") \
                    .csv(os.path.join(output_dir, "backtest_results"))
            
            # Copy dashboard
            dashboard_path = os.path.join(os.getcwd(), 'distributed_stats_page.html')
            if os.path.exists(dashboard_path):
                import shutil
                shutil.copy(dashboard_path, os.path.join(output_dir, "dashboard.html"))
            
            logging.info(f"Results exported to {output_dir}")
            
        except Exception as e:
            logging.error(f"Error exporting results: {str(e)}") 