import logging

import numpy as np
import pandas as pd

from common.utils import dtutil
from data_access.dbtables.market_data.credit import cds_index, cds_index_vol

from dash_reports.cdx_vol_tracker.config import (
    DEFAULT_TICKERS, TENOR, CACHE_SUBDIR, DATA_KEYS, N_EXPIRIES, EXPIRY_LABELS,
)
from dash_reports.vol_tracker_common.cache_utils import CacheManager

_cache = CacheManager(CACHE_SUBDIR)


def get_available_tickers():
    index_info = cds_index.get_index_info()
    if index_info is None or index_info.empty:
        return DEFAULT_TICKERS
    otr = index_info[index_info['on_the_run'].fillna(0) == 1]
    tickers = sorted(otr['ticker'].unique().tolist())
    return tickers or DEFAULT_TICKERS


def get_cdx_otr_data(ticker, fromdt, todt):
    df = cds_index.get_otr_index_data(
        tickers=[ticker], tenor=TENOR,
        start_date=dtutil.todate(fromdt), end_date=dtutil.todate(todt),
        on_the_run='True',
    )
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df['asof_date'] = pd.to_datetime(df['asof_date'])
    df = df.sort_values('asof_date').drop_duplicates(subset='asof_date', keep='last')

    spread_df = pd.DataFrame({
        'conv_spd_bps': df['conv_spd_bps'].values,
        'series': df['series'].values,
        'version': df['version'].values,
    }, index=df['asof_date'].values)
    spread_df.index.name = 'date'

    price_df = pd.DataFrame({
        'mkt_px': df['mkt_px'].values,
        'series': df['series'].values,
        'version': df['version'].values,
    }, index=df['asof_date'].values)
    price_df.index.name = 'date'

    return spread_df, price_df


def get_cdx_otr_vol(ticker, fromdt, todt, otr_ref_df):
    vol_df = cds_index_vol.get_vol_mark(
        fromdt=dtutil.todate(fromdt), todt=dtutil.todate(todt),
        ticker=ticker,
    )
    if vol_df is None or vol_df.empty:
        return pd.DataFrame()

    vol_df['asof_date'] = pd.to_datetime(vol_df['asof_date'])
    vol_df['option_end_date'] = pd.to_datetime(vol_df['option_end_date'])

    if otr_ref_df is not None and not otr_ref_df.empty:
        otr_lookup = otr_ref_df[['series', 'version']].copy()
        otr_lookup.index = pd.to_datetime(otr_lookup.index)
        otr_lookup = otr_lookup.rename_axis('asof_date')

        vol_df = vol_df.merge(
            otr_lookup.reset_index().rename(columns={'series': 'otr_series', 'version': 'otr_version'}),
            on='asof_date', how='inner',
        )
        vol_df = vol_df[
            (vol_df['series'] == vol_df['otr_series'].astype(float)) &
            (vol_df['version'] == vol_df['otr_version'].astype(float))
        ]

    vol_df = vol_df[vol_df['option_end_date'] > vol_df['asof_date']]
    if vol_df.empty:
        return pd.DataFrame()

    vol_df['expiry_rank'] = (
        vol_df.groupby('asof_date')['option_end_date']
        .rank(method='dense').astype(int)
    )
    vol_df = vol_df[vol_df['expiry_rank'] <= N_EXPIRIES]

    daily = (
        vol_df.groupby(['asof_date', 'expiry_rank'])['vol_mid_bpd']
        .median()
        .unstack('expiry_rank')
    )
    daily.columns = [EXPIRY_LABELS[int(c) - 1] for c in daily.columns]
    daily.index = pd.to_datetime(daily.index)
    daily.index.name = 'date'
    return daily.sort_index()


def calc_realized_vol(spread_df, window=21):
    if spread_df is None or spread_df.empty or 'conv_spd_bps' not in spread_df.columns:
        return pd.DataFrame()
    changes = spread_df['conv_spd_bps'].diff()
    rv = changes.rolling(window, min_periods=max(window // 2, 5)).std() * np.sqrt(252)
    return pd.DataFrame({'realized_vol': rv})


def detect_roll_dates(spread_df):
    if spread_df is None or spread_df.empty or 'series' not in spread_df.columns:
        return []
    series = spread_df['series'].dropna()
    if series.empty:
        return []
    changes = series != series.shift(1)
    roll_idx = series.index[changes & (changes.index != series.index[0])]
    return list(roll_idx)


def load_ticker_data(ticker, fromdt, todt):
    logging.info(f'loading cdx vol market data for {ticker} from {fromdt} to {todt}')
    spread_df, price_df = get_cdx_otr_data(ticker, fromdt, todt)
    vol_df = get_cdx_otr_vol(ticker, fromdt, todt, spread_df)
    return {'cdx_spread': spread_df, 'cdx_price': price_df, 'cdx_vol': vol_df}


def load_and_cache(ticker, asof_date, lookback='1y'):
    fromdt = dtutil.dateadd(asof_date, f'-{lookback}')
    data = load_ticker_data(ticker, fromdt, asof_date)
    for key, df in data.items():
        _cache.write_cache(df, asof_date, ticker, key)
    return data


def get_cached_or_load(ticker, asof_date, lookback='1y'):
    data = {}
    for key in DATA_KEYS:
        cached = _cache.read_cache(asof_date, ticker, key)
        if cached is not None:
            data[key] = cached
        else:
            return load_and_cache(ticker, asof_date, lookback)
    return data


def get_cached_or_load_many(tickers, asof_date, lookback='1y'):
    data, missing = _cache.read_many(asof_date, tickers, DATA_KEYS)
    loaded = {
        ticker: load_ticker_data(
            ticker,
            dtutil.dateadd(asof_date, f'-{lookback}'),
            asof_date,
        )
        for ticker in missing
    }
    _cache.write_many(asof_date, loaded)
    data.update(loaded)
    return data


def purge_old_cache(keep_days=7):
    _cache.purge_old(keep_days)


def refresh_all_ticker_caches(asof_date=None, lookback='1y'):
    asof_date = asof_date or dtutil.dateadd(dtutil.today(), '-1b')
    tickers = get_available_tickers()
    logging.info(f'refreshing cdx vol market caches for {asof_date}, tickers={tickers}')
    for ticker in tickers:
        load_and_cache(ticker, asof_date, lookback)
    purge_old_cache()
    logging.info('cdx vol market cache refresh complete')
