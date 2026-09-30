"""
Distributed Statistical Arbitrage using Hybrid ML Approach

This module provides a truly scalable implementation that combines:
- Spark ML models (fully distributed)
- neuralforecast DistributedConfig (native distributed neural training)

Key Components:
- DistributedStatArbConfig: Configuration for hybrid distributed training
- DistributedStatArb: Main orchestration class
- HybridEnsemble: Hybrid predictor combining Spark ML + neural models
- DistributedSignalGenerator: Signal generation using hybrid approach

Usage:
    from ml_analytics.ensembler.distributed import DistributedStatArb, DistributedStatArbConfig
    
    config = DistributedStatArbConfig.create_preset('macro')
    stat_arb = DistributedStatArb(config)
    stat_arb.train('2023-01-01', '2024-01-01')
    signals = stat_arb.generate_live_signals('2024-06-01')
"""

try:
    from .config import DistributedStatArbConfig
    from .stat_arb import DistributedStatArb
    from .signal_generator import DistributedSignalGenerator
    
    # New modular components
    from .ensemble import HybridEnsemble
    from .spark_ml import SparkMLManager
    from .neural_ml import NeuralMLManager
    
    # Backwards compatibility
    from .predictors import (
        DistributedSparkMLManager,
        DistributedNeuralForecastManager,
        DistributedHybridEnsemble
    )
    from .visualizer import DistributedVisualizer
    
    __all__ = [
        'DistributedStatArbConfig',
        'DistributedStatArb', 
        'DistributedSignalGenerator',
        'HybridEnsemble',
        'SparkMLManager',
        'NeuralMLManager',
        'DistributedSparkMLManager',
        'DistributedNeuralForecastManager',
        'DistributedHybridEnsemble',
        'DistributedVisualizer'
    ]
    
except ImportError as e:
    import warnings
    warnings.warn(
        f"Distributed components not available: {str(e)}. "
        "Ensure neuralforecast, pyspark, and other distributed dependencies are installed.",
        ImportWarning
    )
    
    __all__ = [] 