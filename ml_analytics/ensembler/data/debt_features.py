import logging
from datetime import datetime
from typing import Dict, List

import pandas as pd

from common.utils import sqlutil, dtutil
from ml_analytics.ensembler.data.fetcher import DataFetcher


class DebtFeaturesFetcher(DataFetcher):

    def fetch_data(self, features: List[str], start_time: datetime, end_time: datetime, **kwargs) \
            -> Dict[str, pd.Series]:
        if not features:
            raise ValueError("Features list cannot be empty")

        feature_str = ", ".join([f"`{x}`" for x in features])

        query = f"""
        SELECT asof_date, instrument_id_uniq, {feature_str}
        FROM debt_features 
        WHERE cast(asof_date as date) BETWEEN '{dtutil.todate(start_time)}' AND '{dtutil.todate(end_time)}'
        ORDER BY asof_date
        """

        logging.info(f"Executing query for features: {features}")
        df = sqlutil.readdfsql(query, app='databricks')
        df = df.loc[:, ~df.columns.duplicated()]

        logging.info(f"Data fetched for features: {features}, shape = {df.shape}")

        df.dropna(inplace=True, how='any')
        logging.info(f"Data fetched for features: {features}, shape after dropping NA = {df.shape}")

        result = {}
        for feature in features:
            result[feature] = df.set_index(['asof_date', 'instrument_id_uniq'])[feature]

        logging.info(f"Successfully fetched {len(features)} features")
        return result

    def get_available_features(self) -> List[str]:
        query = """
        DESCRIBE debt_features
        """
        logging.info("Fetching available features")
        df = sqlutil.readdfsql(query)
        features = [row['col_name'] for row in df.collect() if row['col_name'] != 'asof_date']
        logging.info(f"Found {len(features)} available features")
        return features


# Example usage
if __name__ == "__main__":
    from common.utils.logutil import setuplogger


    setuplogger()

    from apo_config.config import config

    config.init('prod')

    fetcher = DebtFeaturesFetcher()
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 12, 31)
    columns = ["1m_return", "1w_return"]

    data = fetcher.fetch_data(columns, start_date, end_date)

    for key, series in data.items():
        logging.info(f"Data fetched for {key}: shape = {series.shape}")
