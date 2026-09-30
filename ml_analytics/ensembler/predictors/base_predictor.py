from abc import ABC, abstractmethod

import numpy as np
from torch import nn


class BasePredictor(nn.Module, ABC):
    """Abstract base class for all predictors."""

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """Fit the model to training data."""
        pass

    @abstractmethod
    def predict(self, X: np.ndarray, **kwargs) -> np.ndarray:
        """Generate predictions."""
        pass

    @property
    @abstractmethod
    def is_fitted(self) -> bool:
        """Check if model is fitted."""
        pass

    def get_name(self) -> str:
        """Get predictor name"""
        return self.__class__.__name__
