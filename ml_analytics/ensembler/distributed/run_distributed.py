#!/usr/bin/env python3
"""
Runner script for Distributed Statistical Arbitrage
This script can be run directly and handles all import issues properly.
"""

import os
import sys



# Now import and run the main function
try:
    from ml_analytics.ensembler.distributed.main import main
    
    if __name__ == "__main__":
        main()
        
except ImportError as e:
    import logging
    logging.error(f"Import error: {e}")
    logging.error("Please ensure all dependencies are installed:")
    logging.error("- pip install neuralforecast")
    logging.error("- pip install optuna") 
    logging.error("- pip install pyspark")
    logging.error("- pip install pandas")
    logging.error("- pip install plotly")
    
    logging.debug(f"Current Python path: {sys.path}")
    logging.debug(f"Current working directory: {os.getcwd()}")
    logging.debug(f"Script location: {__file__}")
    
    sys.exit(1)
    
except Exception as e:
    import logging
    logging.error(f"Error running distributed stat arb: {e}")
    logging.exception("Full traceback:")
    sys.exit(1) 