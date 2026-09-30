import logging
from datetime import datetime
from typing import Dict, List

import pandas as pd

from common.utils import bbgutil, cacheutil
from ml_analytics.ensembler.data.fetcher import DataFetcher


class BloombergDataFetcher(DataFetcher):
    def __init__(self):
        pass

    @cacheutil.cache_data()
    def fetch_data(self, features: List[str], start_time: datetime, end_time: datetime, **kwargs) -> Dict[
        str, pd.Series]:
        """Fetch and synchronize data across all tickers"""
        try:
            data = {}
            min_length = float('inf')

            # First pass: fetch all data and find minimum length
            for ticker in features:
                try:
                    if (end_time.date() - start_time.date()).days > 31:
                        bbg_data_df = bbgutil.bbg_bdh_request(ticker, 'PX_LAST', start_time.date(), end_time.date(),
                                                              **kwargs)
                        bbg_data_df['Close'] = bbg_data_df[bbg_data_df.columns[0]]
                        bbg_data_df['DateTime'] = bbg_data_df.index
                    else:
                        bbg_data_df = bbgutil.bbg_intraday(
                            security=ticker,
                            event_type='TRADE',
                            start_datetime=start_time,
                            end_datetime=end_time,
                            interval=1,
                            tz='America/New_York'
                        )

                    if isinstance(bbg_data_df, tuple):
                        df = bbg_data_df[0]
                    else:
                        df = bbg_data_df

                    if df is not None and not df.empty:
                        df.index = pd.to_datetime(df['DateTime'])

                        series = df['Close']
                        data[ticker] = series
                        min_length = min(min_length, len(series))
                        logging.info(f"Fetched {len(series)} minutes of data for {ticker}")
                    else:
                        logging.warning(f"No data received for {ticker}")
                        return {}  # Return empty if any ticker fails

                except Exception as e:
                    logging.error(f"Error fetching data for {ticker}: {str(e)}")
                    return {}  # Return empty if any ticker fails

            # Second pass: synchronize all series to the same length
            logging.info(f"Successfully synchronized all data to length: {min_length}")
            data_df = pd.DataFrame(data)
            data_df.index.name = 'asof_date'
            data_df = data_df.reset_index().fillna(method='ffill').fillna(method='bfill')

            return data_df

        except Exception as e:
            logging.error(f"Error in fetch_data: {str(e)}")
            return pd.DataFrame()
