from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd


class DataFetcher(ABC):
    @abstractmethod
    def fetch_data(self, features: List[str], start_time: datetime, end_time: datetime, **kwargs) -> Dict[
        str, pd.Series]:
        pass


class MockDataFetcher(DataFetcher):
    """Mock data fetcher for testing"""

    def fetch_data(self, features: List[str], start_time: datetime, end_time: datetime, **kwargs) -> Dict[
        str, pd.Series]:
        """Generate mock price data with realistic properties"""
        data = {}
        for ticker in features:
            minutes = int((end_time - start_time).total_seconds() / 60)
            timestamps = pd.date_range(start=start_time, end=end_time, periods=minutes)

            # Generate random walk with drift and volatility clusters
            drift = np.random.normal(0, 0.0001, minutes)
            volatility = np.abs(np.sin(np.linspace(0, 10, minutes))) * 0.001 + 0.001
            returns = drift + np.random.normal(0, volatility)
            prices = 100 * np.exp(np.cumsum(returns))

            data[ticker] = pd.Series(prices, index=timestamps)
        return data
