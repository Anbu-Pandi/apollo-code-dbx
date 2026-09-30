import logging
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

from ml_analytics.ensembler.data_manager import DataManager
from ml_analytics.ensembler.utils import ReturnType


class HistoricalDataTracker:
    def __init__(self, max_history: int = 1e6):
        self.max_history = max_history
        self.data = pd.DataFrame(columns=[
            'timestamp',
            'feature',
            'actual_price',
            'predicted_type',
            'predicted_return',
            'predicted_price',
            'prediction_error'
        ])

    def add_record(self,
                   timestamp: datetime = None,
                   feature: str = None,
                   actual_price: float = np.nan,
                   predicted_type: str = None,
                   predicted_return: float = np.nan,
                   return_type: ReturnType = None) -> None:
        try:

            recent_df = self.get_recent_data(lookback_periods=1, feature=feature)
            if len(recent_df) == 0:
                previous_price = actual_price
            else:
                previous_price = recent_df.iloc[-1]['actual_price']
                if pd.isna(previous_price):
                    previous_price = recent_df.iloc[-1]['predicted_price']

            predicted_price = DataManager.convert_return_to_price(previous_price, predicted_return, return_type)
            prediction_error = predicted_price - actual_price

            new_record = pd.DataFrame([{
                'timestamp': timestamp,
                'feature': feature,
                'actual_price': actual_price,
                'predicted_type': predicted_type,
                'predicted_return': predicted_return,
                'predicted_price': predicted_price,
                'prediction_error': prediction_error
            }])

            self.data = pd.concat([self.data, new_record], ignore_index=True)
            if len(self.data) > self.max_history:
                self.data = self.data.iloc[-self.max_history:]

        except Exception as e:
            logging.error(f"Error adding historical record: {str(e)}")

    def get_predictions(self, feature: str = None) -> pd.DataFrame:
        result = self.data

        if feature is not None:
            result = result[result['feature'] == feature]

        return result[['timestamp', 'feature', 'predicted_return', 'predicted_price', 'predicted_type']].dropna()

    def get_recent_data(self, lookback_periods: int = None, feature: str = None) -> pd.DataFrame:
        result = self.data

        if feature is not None:
            result = result[(result['feature'] == feature)].dropna(subset=['actual_price'])

        if ((lookback_periods or -1) < 0) or lookback_periods >= len(result):
            return result

        return result.iloc[-lookback_periods:]

    def calculate_metrics(self, feature: str = None) -> Dict[str, float]:
        try:
            data_to_use = self.data

            if feature is not None:
                data_to_use = data_to_use[data_to_use['feature'] == feature]

            if len(data_to_use) == 0:
                return {}

            recent_data = data_to_use.tail(100)

            return {
                'mae': np.abs(recent_data['prediction_error']).mean(),
                'rmse': np.sqrt((recent_data['prediction_error'] ** 2).mean()),
                'bias': recent_data['prediction_error'].mean(),
                'hit_rate': (np.sign(recent_data['predicted_return']) ==
                             np.sign(recent_data['actual_price'].diff())).mean(),
                'num_observations': len(recent_data)
            }
        except Exception as e:
            logging.error(f"Error calculating metrics: {str(e)}")
            return {}

    def get_features(self) -> List[str]:
        return self.data['feature'].unique().tolist()

    def get_feature_timestamps(self, feature: str = None) -> pd.DataFrame:
        if feature is not None:
            result = self.data[self.data['feature'] == feature][['feature', 'timestamp']]
        else:
            result = self.data[['feature', 'timestamp']]

        return result.sort_values(['feature', 'timestamp'])
