import logging
from typing import Dict, List, Optional
import pandas as pd
import sys
import os

# Add path for absolute imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from pyspark.sql import DataFrame as SparkDataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from ..utils import SignalParameters
from .predictors import DistributedHybridEnsemble
from data_access.spark.api import sparkmanager


class DistributedSignalGenerator:
    
    def __init__(self, y_feature: str, x_features: List[str], 
                 signal_params: SignalParameters = None,
                 use_spark_ml: bool = True, use_neural: bool = True,
                 neural_predictors: Optional[List[str]] = None,
                 partitions_path: str = "/tmp/nf_partitions",
                 num_nodes: int = 2, devices_per_node: int = 1):
        self.y_feature = y_feature
        self.x_features = x_features
        self.all_features = [y_feature] + x_features
        self.signal_params = signal_params or SignalParameters()
        
        feature_cols = []
        for feature in self.all_features:
            feature_cols.append(f"{feature}_return")
            for lag in range(1, 6):
                feature_cols.append(f"{feature}_return_lag_{lag}")
        
        self.predictor = DistributedHybridEnsemble(
            feature_cols=feature_cols,
            target_col="target",
            use_spark_ml=use_spark_ml,
            use_neural=use_neural,
            neural_predictors=neural_predictors or ['auto_tft', 'auto_lstm'] if use_neural else None,
            partitions_path=partitions_path,
            num_nodes=num_nodes,
            devices_per_node=devices_per_node
        )
        
        if not hasattr(sparkmanager, '_spark') or sparkmanager._spark is None:
            sparkmanager.init()
        self.spark = sparkmanager._spark

    def _calculate_rolling_stats_group(self, pdf: pd.DataFrame) -> pd.DataFrame:
        try:
            pdf = pdf.sort_values('asof_date')
            y_col = f"{self.y_feature}_return"
            
            if y_col in pdf.columns and len(pdf) > 10:
                window = min(100, len(pdf))
                pdf[f"{y_col}_rolling_mean"] = pdf[y_col].rolling(window=window).mean()
                pdf[f"{y_col}_rolling_std"] = pdf[y_col].rolling(window=window).std()
                pdf[f"{y_col}_zscore"] = (pdf[y_col] - pdf[f"{y_col}_rolling_mean"]) / pdf[f"{y_col}_rolling_std"]
                
                for x_feature in self.x_features:
                    x_col = f"{x_feature}_return"
                    if x_col in pdf.columns:
                        pdf[f"{x_col}_corr_with_y"] = pdf[y_col].rolling(window=window).corr(pdf[x_col])
            
            return pdf
        except:
            return pdf

    def calculate_rolling_stats_spark(self, df: SparkDataFrame) -> SparkDataFrame:
        df = df.withColumn("_group", F.coalesce(F.col("_group"), F.lit("default")))
        
        result_df = df.groupby("_group").applyInPandas(
            self._calculate_rolling_stats_group, 
            schema=df.schema
        )
        
        return result_df

    def _calculate_regime_weights_group(self, pdf: pd.DataFrame) -> pd.DataFrame:
        try:
            pdf = pdf.sort_values('asof_date')
            y_col = f"{self.y_feature}_return"
            
            if y_col in pdf.columns and len(pdf) > 20:
                window = min(20, len(pdf))
                pdf['rolling_vol'] = pdf[y_col].rolling(window=window).std()
                
                vol_33 = pdf['rolling_vol'].quantile(0.33)
                vol_67 = pdf['rolling_vol'].quantile(0.67)
                
                pdf['regime'] = 'Normal'
                pdf.loc[pdf['rolling_vol'] < vol_33, 'regime'] = 'Low Vol'
                pdf.loc[pdf['rolling_vol'] > vol_67, 'regime'] = 'High Vol'
                
                regime_weights = {'Low Vol': 0.2, 'Normal': 0.3, 'High Vol': 0.4}
                pdf['statistical_weight'] = pdf['regime'].map(regime_weights)
                pdf['predictive_weight'] = pdf['regime'].map({'Low Vol': 0.6, 'Normal': 0.4, 'High Vol': 0.2})
            else:
                pdf['regime'] = 'Normal'
                pdf['statistical_weight'] = 0.3
                pdf['predictive_weight'] = 0.4
            
            return pdf
        except:
            pdf['regime'] = 'Normal'
            pdf['statistical_weight'] = 0.3
            pdf['predictive_weight'] = 0.4
            return pdf

    def calculate_regime_weights(self, df: SparkDataFrame) -> SparkDataFrame:
        """Calculate regime-based weights using Spark SQL operations instead of pandas UDF"""
        df = df.withColumn("_group", F.coalesce(F.col("_group"), F.lit("default")))
        
        # Calculate rolling volatility and regimes using Spark operations
        y_col = f"{self.y_feature}_return"
        window_spec = Window.partitionBy("_group").orderBy("asof_date").rowsBetween(-23, 0)  # 24-period rolling window
        
        # Calculate rolling volatility
        df = df.withColumn("rolling_vol", F.stddev(y_col).over(window_spec))
        
        # Calculate volatility quantiles for regime classification
        vol_quantiles = df.select(
            F.expr("percentile_approx(rolling_vol, 0.33)").alias("vol_33"),
            F.expr("percentile_approx(rolling_vol, 0.67)").alias("vol_67")
        ).collect()[0]
        
        vol_33, vol_67 = vol_quantiles["vol_33"], vol_quantiles["vol_67"]
        
        # Assign regimes based on volatility
        df = df.withColumn(
            "regime",
            F.when(F.col("rolling_vol") < vol_33, "Low Vol")
            .when(F.col("rolling_vol") > vol_67, "High Vol")
            .otherwise("Normal")
        )
        
        # Assign weights based on regime
        df = df.withColumn(
            "statistical_weight",
            F.when(F.col("regime") == "Low Vol", 0.2)
            .when(F.col("regime") == "Normal", 0.3)
            .when(F.col("regime") == "High Vol", 0.4)
            .otherwise(0.3)
        )
        
        df = df.withColumn(
            "predictive_weight",
            F.when(F.col("regime") == "Low Vol", 0.6)
            .when(F.col("regime") == "Normal", 0.4)
            .when(F.col("regime") == "High Vol", 0.2)
            .otherwise(0.4)
        )
        
        return df

    def fit_predictors(self, df: SparkDataFrame) -> None:
        logging.info("Fitting hybrid distributed predictors (Spark ML + Neural)")
        
        group_col = "_group"
        window_spec = Window.partitionBy(group_col).orderBy("asof_date")
        
        for lag in range(1, 6):
            for feature in self.all_features:
                feature_col = f"{feature}_return" if f"{feature}_return" in df.columns else feature
                df = df.withColumn(f"{feature_col}_lag_{lag}", F.lag(feature_col, lag).over(window_spec))
        
        target_col = f"{self.y_feature}_return" if f"{self.y_feature}_return" in df.columns else self.y_feature
        df = df.withColumn("target", F.lead(target_col, 1).over(window_spec))
        
        df = df.dropna()
        logging.info("Training hybrid ensemble: Spark ML (distributed) + Neural (sampled)")
        self.predictor.fit(df)

    def generate_signals(self, df: SparkDataFrame) -> SparkDataFrame:
        logging.info("Generating signals using hybrid distributed ensemble")
        
        df = self.calculate_rolling_stats_spark(df)
        df = self.calculate_regime_weights(df)
        
        if self.predictor.is_fitted:
            df = self.predictor.predict(df)
            prediction_col = "final_ensemble_prediction"
        else:
            logging.warning("Predictor not fitted, using zero predictions")
            df = df.withColumn(prediction_col, F.lit(0.0))
        
        df = self._generate_statistical_signals(df)
        df = self._generate_composite_signals(df, prediction_col)
        
        return df

    def _generate_statistical_signals(self, df: SparkDataFrame) -> SparkDataFrame:
        y_col = f"{self.y_feature}_return"
        
        if f"{y_col}_zscore" in df.columns:
            df = df.withColumn(
                "statistical_signal",
                F.when(F.col(f"{y_col}_zscore") > self.signal_params.zscore_entry, -1.0)
                .when(F.col(f"{y_col}_zscore") < -self.signal_params.zscore_entry, 1.0)
                .when(F.abs(F.col(f"{y_col}_zscore")) < self.signal_params.zscore_exit, 0.0)
                .otherwise(F.lit(None))
            )
            
            window_spec = Window.partitionBy("_group").orderBy("asof_date").rowsBetween(Window.unboundedPreceding, 0)
            df = df.withColumn("statistical_signal", F.last("statistical_signal", ignorenulls=True).over(window_spec))
        else:
            df = df.withColumn("statistical_signal", F.lit(0.0))
        
        return df

    def _generate_composite_signals(self, df: SparkDataFrame, prediction_col: str) -> SparkDataFrame:
        df = df.withColumn(
            "composite_signal",
            (F.col("statistical_signal") * F.col("statistical_weight")) +
            (F.col(prediction_col) * F.col("predictive_weight"))
        )
        
        signal_threshold = 0.1
        df = df.withColumn(
            "final_signal",
            F.when(F.col("composite_signal") > signal_threshold, 1.0)
            .when(F.col("composite_signal") < -signal_threshold, -1.0)
            .otherwise(0.0)
        )
        
        df = df.withColumn("signal_strength", F.abs(F.col("composite_signal")))
        return df

    def evaluate_signals(self, df: SparkDataFrame) -> Dict[str, float]:
        stats = df.select(
            F.avg("final_signal").alias("mean_signal"),
            F.stddev("final_signal").alias("std_signal"),
            F.count("final_signal").alias("total_signals"),
            F.sum(F.when(F.col("final_signal") != 0, 1).otherwise(0)).alias("active_signals")
        ).collect()[0]
        
        results = {
            "mean_signal": float(stats["mean_signal"] or 0),
            "std_signal": float(stats["std_signal"] or 0),
            "total_signals": int(stats["total_signals"]),
            "active_signals": int(stats["active_signals"]),
            "signal_activity_rate": float(stats["active_signals"]) / float(stats["total_signals"]) if stats["total_signals"] > 0 else 0
        }
        
        try:
            regime_dist = df.groupBy("regime").count().collect()
            for row in regime_dist:
                results[f"regime_{row['regime'].replace(' ', '_').lower()}_count"] = row["count"]
        except:
            pass
        
        return results

    def backtest_signals(self, df: SparkDataFrame, initial_capital: float = 100000) -> SparkDataFrame:
        logging.info("Running distributed signal backtest using hybrid ensemble")
        
        if "final_signal" not in df.columns:
            df = self.generate_signals(df)
        
        window_spec = Window.partitionBy("_group").orderBy("asof_date")
        
        y_col = f"{self.y_feature}_return"
        df = df.withColumn("forward_return", F.lead(y_col, 1).over(window_spec))
        df = df.withColumn("signal_return", F.col("final_signal") * F.col("forward_return"))
        df = df.withColumn("cumulative_return", F.sum("signal_return").over(window_spec.rowsBetween(Window.unboundedPreceding, 0)))
        df = df.withColumn("portfolio_value", initial_capital * (1 + F.col("cumulative_return")))
        df = df.withColumn("running_max", F.max("portfolio_value").over(window_spec.rowsBetween(Window.unboundedPreceding, 0)))
        df = df.withColumn("drawdown", (F.col("portfolio_value") - F.col("running_max")) / F.col("running_max"))
        
        return df 