import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union
import logging

# Use relative imports to avoid path conflicts
from ..config import StatArbConfig
from ..utils import ReturnType, SignalParameters, RiskParameters


@dataclass
class SparkConfig:
    """Spark-specific configuration"""
    num_partitions: int = 20
    cache_level: str = "MEMORY_AND_DISK"
    checkpoint_interval: int = 10
    executor_memory: str = "8g"
    executor_cores: str = "4"
    driver_memory: str = "8g"


@dataclass
class NeuralConfig:
    """Neural forecasting configuration"""
    predictors: List[str] = field(default_factory=lambda: ['auto_nhits', 'auto_lstm'])
    partitions_path: str = "/tmp/nf_partitions"
    num_nodes: int = 2
    devices_per_node: int = 1
    max_epochs: int = 100
    learning_rate: float = 1e-3
    batch_size: int = 32


@dataclass
class ModelConfig:
    """Model selection configuration"""
    use_spark_ml: bool = False
    use_neural: bool = True
    spark_ml_models: List[str] = field(default_factory=lambda: ['random_forest', 'gbt', 'linear'])


@dataclass 
class DistributedStatArbConfig(StatArbConfig):
    """Simplified distributed statistical arbitrage configuration"""
    
    # Core configurations as composition
    model_config: ModelConfig = field(default_factory=ModelConfig)
    spark_config: SparkConfig = field(default_factory=SparkConfig)
    neural_config: NeuralConfig = field(default_factory=NeuralConfig)
    
    # Data configuration
    data_source_table: str = "features_table"
    output_table_prefix: str = "stat_arb_distributed"
    
    # Training parameters
    early_stopping_patience: int = 10

    @classmethod
    def create_preset(cls, preset: str) -> 'DistributedStatArbConfig':
        """Factory method for common presets"""
        presets = {
            'macro': cls._macro_preset,
            'equity': cls._equity_preset,
            'fx': cls._fx_preset,
            'eur_rates': cls._eur_rates_preset
        }
        
        if preset not in presets:
            raise ValueError(f"Unknown preset: {preset}. Available: {list(presets.keys())}")
        
        return presets[preset]()

    @staticmethod
    def _create_base_config(y_feature: str, x_features: List[str], 
                           periodicity: str, window_days: int,
                           signal_params: SignalParameters = None,
                           risk_params: RiskParameters = None,
                           return_types: Dict[str, ReturnType] = None) -> Dict[str, Any]:
        """Extract common configuration pattern"""
        return {
            'y_feature': y_feature,
            'x_features': x_features,
            'periodicity': periodicity,
            'window_days': window_days,
            'signal_params': signal_params or SignalParameters(),
            'risk_params': risk_params or RiskParameters(),
            'return_types': return_types or {}
        }

    @classmethod
    def _macro_preset(cls) -> 'DistributedStatArbConfig':
        """Macro strategy preset"""
        base = cls._create_base_config(
            y_feature="PCE CMOM Index",
            x_features=[
                'FDIDPGCC Index', 'FDIUPGCC Index', 'FDIUPGBC Index', 'FDIDPGBC Index', 
                'PPMMCMF Index', 'FDIDPGBB Index', 'FDIUPGBB Index', 'PPIJLBAB Index',
                "CPRTMVUS Index", "CPSTAIRY Index", "CPRPENER Index", "CPRFHOME Index", 
                "CPRPAXFE Index", "CPUMTOT Index", "CPURNSA Index", "CPRHOERY Index", 
                "CPI INDX Index", "CPSHSHLT Index", "CPSTUCTR Index"
            ],
            periodicity='MONTHLY',
            window_days=365 * 20,
            signal_params=SignalParameters(
                zscore_entry=2.0, zscore_exit=0.0, min_correlation=0.3,
                vol_lookback=20, halflife=10, ml_window=365 * 12, regime_window=12
            ),
            risk_params=RiskParameters(
                max_position_size=1.0, max_leverage=2.0, stop_loss_std=3.0,
                position_timeout=60, max_drawdown=0.1
            ),
            return_types={feature: ReturnType.PCT_CHANGE for feature in 
                         ["NAPMPMI Index", "NAPMNMI Index", "USURTOT Index", "INJCJC Index",
                          "CPRTMVUS Index", "CPSTAIRY Index", "CPRPENER Index", "CPRFHOME Index",
                          "CPRPAXFE Index", "CPUMTOT Index", "CPURNSA Index", "CPRHOERY Index",
                          "CPI INDX Index", "CPSHSHLT Index", "CPSTUCTR Index", "PCE CMOM Index"]}
        )
        
        return cls(
            **base,
            neural_config=NeuralConfig(
                predictors=['auto_nhits', 'auto_lstm', 'auto_tft'],
                partitions_path="/Volumes/default/default/stat_arb_partitions",
                num_nodes=4, devices_per_node=1, max_epochs=100, batch_size=64
            ),
            spark_config=SparkConfig(num_partitions=50)
        )

    @classmethod
    def _equity_preset(cls) -> 'DistributedStatArbConfig':
        """Equity pair trading preset"""
        base = cls._create_base_config(
            y_feature="MSFT US Equity",
            x_features=["AAPL US Equity", "GOOGL US Equity", "AMZN US Equity", "META US Equity"],
            periodicity='DAILY',
            window_days=252,
            signal_params=SignalParameters(zscore_entry=2.0, zscore_exit=0.5, min_correlation=0.6),
            risk_params=RiskParameters(max_position_size=0.5, max_leverage=1.5, stop_loss_std=2.5)
        )
        
        return cls(
            **base,
            model_config=ModelConfig(use_spark_ml=True, spark_ml_models=['random_forest', 'linear']),
            neural_config=NeuralConfig(
                predictors=['lstm'], partitions_path="/Volumes/default/default/equity_partitions",
                num_nodes=2, max_epochs=50, learning_rate=5e-4, batch_size=32
            ),
            spark_config=SparkConfig(num_partitions=30)
        )

    @classmethod
    def _fx_preset(cls) -> 'DistributedStatArbConfig':
        """FX pair trading preset"""
        base = cls._create_base_config(
            y_feature="EURUSD Curncy",
            x_features=["GBPUSD Curncy", "USDJPY Curncy", "USDCHF Curncy", "DXY Index"],
            periodicity='DAILY',
            window_days=60,
            signal_params=SignalParameters(
                zscore_entry=1.5, zscore_exit=0.3, min_correlation=0.5,
                kalman_q=0.01, kalman_r=0.5
            ),
            risk_params=RiskParameters(
                max_position_size=2.0, max_leverage=3.0, stop_loss_std=2.0, position_timeout=120
            )
        )
        
        return cls(
            **base,
            model_config=ModelConfig(use_spark_ml=True, spark_ml_models=['linear']),
            neural_config=NeuralConfig(
                predictors=['auto_tft', 'auto_lstm'], 
                partitions_path="/Volumes/default/default/fx_partitions",
                num_nodes=4, max_epochs=200, batch_size=128
            ),
            spark_config=SparkConfig(num_partitions=40)
        )

    @classmethod
    def _eur_rates_preset(cls) -> 'DistributedStatArbConfig':
        """EUR vs rates preset"""
        base = cls._create_base_config(
            y_feature='EUR Curncy',
            x_features=['USGG10YR Index', 'USGG2YR Index', 'GECU2YR Index', 'GECU10YR Index'],
            periodicity='DAILY',
            window_days=365,
            signal_params=SignalParameters(zscore_entry=1.8, zscore_exit=0.3, min_correlation=0.4),
            risk_params=RiskParameters(max_position_size=1.5, max_leverage=2.5, stop_loss_std=2.5),
            return_types={
                **{feature: ReturnType.PRICE_DIFF for feature in 
                   ['USGG10YR Index', 'USGG2YR Index', 'GECU2YR Index', 'GECU10YR Index']},
                'EUR Curncy': ReturnType.PCT_CHANGE
            }
        )
        
        return cls(
            **base,
            model_config=ModelConfig(use_spark_ml=True, spark_ml_models=['random_forest', 'linear']),
            neural_config=NeuralConfig(
                predictors=['auto_tft'], partitions_path="/Volumes/default/default/eur_rates_partitions",
                num_nodes=2, max_epochs=100, batch_size=32
            ),
            spark_config=SparkConfig(num_partitions=20)
        )

    @classmethod
    def create_distributed_default(cls) -> 'DistributedStatArbConfig':
        """Backwards compatibility"""
        return cls.create_preset('macro')

    @classmethod
    def create_distributed_equity_pair(cls) -> 'DistributedStatArbConfig':
        """Backwards compatibility"""
        return cls.create_preset('equity')

    @classmethod
    def create_distributed_fx_pair(cls) -> 'DistributedStatArbConfig':
        """Backwards compatibility"""
        return cls.create_preset('fx')

    @classmethod
    def create_distributed_eur_rates(cls) -> 'DistributedStatArbConfig':
        """Backwards compatibility"""
        return cls.create_preset('eur_rates')

    def get_spark_config_dict(self) -> Dict[str, str]:
        """Get Spark configuration as dictionary"""
        return {
            "spark.sql.adaptive.enabled": "true",
            "spark.sql.adaptive.coalescePartitions.enabled": "true",
            "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
            "spark.sql.adaptive.advisoryPartitionSizeInBytes": "128MB",
            "spark.sql.adaptive.skewJoin.enabled": "true",
            "spark.executor.memory": self.spark_config.executor_memory,
            "spark.executor.cores": self.spark_config.executor_cores,
            "spark.driver.memory": self.spark_config.driver_memory,
            "spark.driver.maxResultSize": "4g"
        }

    def get_neural_distributed_config(self) -> Dict[str, Any]:
        """Get neuralforecast DistributedConfig parameters"""
        return {
            'partitions_path': self.neural_config.partitions_path,
            'num_nodes': self.neural_config.num_nodes,
            'devices': self.neural_config.devices_per_node
        }

    def get_return_types_mapping(self) -> Dict[str, ReturnType]:
        """Get return types mapping with fallback"""
        if hasattr(self, 'return_types') and self.return_types:
            return self.return_types
        
        # Default all features to percentage change
        all_features = [self.y_feature] + self.x_features
        return {feature: ReturnType.PCT_CHANGE for feature in all_features}

    def validate_config(self) -> None:
        """Validate configuration"""
        if hasattr(super(), 'validate_config'):
            try:
                super().validate_config()
            except Exception as e:
                logging.warning(f"Parent validation failed: {str(e)}")
        
        if self.model_config.use_neural:
            if not self.neural_config.partitions_path:
                raise ValueError("neural_partitions_path required when use_neural=True")
            if self.neural_config.num_nodes < 1:
                raise ValueError("neural_num_nodes must be >= 1")
        
        if self.spark_config.num_partitions < 1:
            raise ValueError("num_partitions must be >= 1")
        
        if not self.model_config.use_spark_ml and not self.model_config.use_neural:
            raise ValueError("At least one model type must be enabled")
        
        if not self.model_config.use_spark_ml:
            logging.info("Using neural forecasting only for Databricks Connect compatibility") 