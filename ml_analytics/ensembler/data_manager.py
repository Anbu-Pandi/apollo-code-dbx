import logging
import traceback
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from ml_analytics.ensembler.data.bbg import BloombergDataFetcher
from ml_analytics.ensembler.utils import ReturnType


class DataManager:
    MAX_BUFFER_SIZE = 10000

    def __init__(self, features, return_type, window_days=365, periodicity='DAILY', id_column=None):
        self.features = features
        self.return_type = return_type
        self.window_days = window_days
        self.id_columns = [col for col in ([id_column] if id_column else []) if col is not None]
        self.id_columns.append('_group')

        self.window_units = int(window_days) if window_days > 10 else int(window_days * 24 * 60)
        self.periodicity = periodicity

        self.data_fetcher = BloombergDataFetcher()
        self.data_buffer = pd.DataFrame()
        self._return_cache = {}

    @lru_cache(maxsize=1024)
    def get_return_type(self, ticker):
        return self.return_type.get(ticker, ReturnType.LEVEL)

    def update_data(self):
        try:
            end_time = datetime.now()

            if self.data_buffer.empty:
                start_time = end_time - timedelta(days=self.window_days)
            else:
                start_time = self.data_buffer.reset_index()['asof_date'].max()

            new_data = self.data_fetcher.fetch_data(
                self.features,
                start_time,
                end_time,
                elms=[('periodicitySelection', self.periodicity)]
            )

            new_data['_group'] = 'ALL'

            key_cols = ['asof_date'] + self.id_columns
            panel_data = pd.DataFrame(new_data).reset_index()
            panel_data = panel_data.drop_duplicates(subset=key_cols, keep='last')

            self._clean_outliers(panel_data)
            panel_data = panel_data.set_index(key_cols)

            if self.data_buffer.empty:
                self.data_buffer = panel_data
            else:
                combined = pd.concat([self.data_buffer, panel_data])
                combined = combined.reset_index()
                combined = combined.drop_duplicates(subset=key_cols, keep='last')

                if len(combined) > self.MAX_BUFFER_SIZE:
                    combined = combined.sort_values('asof_date').iloc[-self.MAX_BUFFER_SIZE:]

                self.data_buffer = combined.set_index(key_cols)

        except Exception as e:
            logging.error(f"Error updating data: {str(e)}")
            logging.error(traceback.format_exc())

    def _clean_outliers(self, df):
        for ticker in self.features:
            for _, group in df.groupby(self.id_columns):
                series = group[ticker]
                if len(series) > 1 and series.std() > 0:
                    zscore = np.abs((series - series.mean()) / series.std())
                    outlier_mask = zscore >= 10
                    if outlier_mask.any():
                        df.loc[outlier_mask.index[outlier_mask], ticker] = np.nan

    def calculate_returns(self, prices, return_type, base_level=None):
        try:
            cache_key = f"{id(prices)}_{return_type}_{base_level}"
            if cache_key in self._return_cache:
                return self._return_cache[cache_key]

            if return_type == ReturnType.LEVEL:
                self._return_cache[cache_key] = prices
                return prices

            if return_type == ReturnType.PCT_CHANGE:
                result = (prices - base_level) / base_level if base_level is not None else prices.pct_change()
            elif return_type == ReturnType.PRICE_DIFF:
                result = prices - base_level if base_level is not None else prices.diff()
            elif return_type == ReturnType.LOG_RETURN:
                result = np.log(prices / base_level) if base_level is not None else np.log(prices / prices.shift(1))
            else:
                result = prices

            if isinstance(prices, pd.DataFrame) and len(self.id_columns) > 0:
                result = prices.groupby(self.id_columns).transform(lambda x: result).fillna(0)
            else:
                result = result.fillna(0)

            self._return_cache[cache_key] = result
            return result

        except Exception as e:
            logging.error(f"Error calculating returns: {str(e)}")
            return pd.Series(index=prices.index) if isinstance(prices, pd.Series) else pd.DataFrame(index=prices.index)

    def calculate_returns_dataframe(self, price_data, columns=None):
        returns = {}
        if columns is None:
            columns = self.features

        if isinstance(price_data, dict):
            for ticker, prices in price_data.items():
                if ticker in columns and len(prices) > 1:
                    return_type = self.get_return_type(ticker)
                    returns[ticker] = self.calculate_returns(prices, return_type)
        else:
            for ticker in columns:
                if ticker in price_data.columns and len(price_data[ticker]) > 1:
                    return_type = self.get_return_type(ticker)
                    returns[ticker] = self.calculate_returns(price_data[ticker], return_type)

        return pd.DataFrame(returns).dropna()

    @staticmethod
    def convert_return_to_price(current_price, predicted_return, return_type):
        try:
            if pd.isnull(predicted_return) or pd.isnull(current_price):
                return np.nan

            if return_type == ReturnType.PCT_CHANGE:
                return current_price * (1 + predicted_return)
            elif return_type == ReturnType.PRICE_DIFF:
                return current_price + predicted_return
            elif return_type == ReturnType.LOG_RETURN:
                return current_price * np.exp(predicted_return)
            elif return_type == ReturnType.LEVEL:
                return predicted_return
            else:
                return np.nan

        except Exception as e:
            logging.error(f"Error converting return to price: {str(e)}")
            return np.nan

    def get_data_by_groups(self):
        returns_by_id = {}
        for name, group in self.data_buffer.groupby(self.id_columns):
            returns_df = self.calculate_returns_dataframe(group)
            if not returns_df.empty:
                returns_by_id[name] = returns_df
        return returns_by_id
