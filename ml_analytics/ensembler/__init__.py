# Main ensembler module exports
from .config import StatArbConfig
from .stat_arb import StatArb
from .signal_generator import SignalGenerator
from .data_manager import DataManager
from .position_manager import PositionManager
from .regime_detector import MarketRegimeDetector
from .visualizer import Visualizer

__all__ = [
    'StatArbConfig',
    'StatArb',
    'SignalGenerator', 
    'DataManager',
    'PositionManager',
    'MarketRegimeDetector',
    'Visualizer'
]

# Try to import distributed components with graceful fallback
try:
    from .distributed import (
        DistributedStatArbConfig,
        DistributedStatArb,
        DistributedSignalGenerator,
        DistributedHybridEnsemble
    )
    
    __all__.extend([
        'DistributedStatArbConfig',
        'DistributedStatArb', 
        'DistributedSignalGenerator',
        'DistributedHybridEnsemble'
    ])
    
    DISTRIBUTED_AVAILABLE = True
    
except ImportError:
    # Distributed components not available (missing dependencies)
    DISTRIBUTED_AVAILABLE = False

def check_distributed_availability():
    """Check if distributed components are available"""
    return DISTRIBUTED_AVAILABLE

def get_available_components():
    """Get list of available components"""
    components = {
        'core': [
            'StatArbConfig', 'StatArb', 'SignalGenerator', 
            'DataManager', 'PositionManager', 'MarketRegimeDetector',
            'Visualizer'
        ],
        'distributed': []
    }
    
    if DISTRIBUTED_AVAILABLE:
        components['distributed'] = [
            'DistributedStatArbConfig', 'DistributedStatArb',
            'DistributedSignalGenerator', 'DistributedHybridEnsemble'
        ]
    
    return components
