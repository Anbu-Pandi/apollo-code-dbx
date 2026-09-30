import logging

import numpy as np
import pandas as pd

from common.utils import dtutil
from common.utils.sqlutil import readdfsql
from data_access.dbtables import GS_MARKETDATA_APP, FXSPOT_APP
from data_access.dbtables import market_data
from data_access.dbtables.market_data.spot import fx
from data_access.dbtables.market_data.vol import fx_vol_data
from data_access.dbtables.market_data.fwd import fx_forward_data

from dash_reports.fx_vol_tracker.config import (
    OPTION_TENORS, FWD_TENORS, SPREAD_PAIRS, FLY_COMBOS, CACHE_SUBDIR, DATA_KEYS,
)
from dash_reports.vol_tracker_common.cache_utils import CacheManager

_cache = CacheManager(CACHE_SUBDIR)


def get_available_ccy_pairs():
    vol_df = readdfsql('SELECT DISTINCT ccy_pair FROM dbo.fx_vol_data', app=GS_MARKETDATA_APP)
    vol_pairs = set(vol_df['ccy_pair'].tolist()) if len(vol_df) > 0 else set()

    vol_ccys = {p.replace('USD', '') for p in vol_pairs}

    excluded = ','.join(f"'{x}'" for x in fx.EXCLUDED_FX_CURRENCIES)
    spot_sql = (f"SELECT DISTINCT FROM_CURRENCY_CODE FROM {fx.FX_VIEW}"
                f" WHERE TO_CURRENCY_CODE = 'USD' AND FROM_CURRENCY_CODE NOT IN ({excluded})")
    spot_df = readdfsql(spot_sql, app=FXSPOT_APP)
    spot_ccys = set(spot_df.iloc[:, 0].tolist()) if len(spot_df) > 0 else set()
    spot_ccys.discard('USD')

    all_pairs = set(vol_pairs)
    for ccy in spot_ccys - vol_ccys:
        all_pairs.add(f'USD{ccy}')

    return sorted(all_pairs)


def _extract_ccy(ccy_pair):
    return ccy_pair.replace('USD', '')


def get_fx_spot(ccy_pair, fromdt, todt):
    ccy = _extract_ccy(ccy_pair)
    df = fx.get_fx(fromdt, todt, ccys=[ccy])
    if df is None or df.empty:
        return pd.DataFrame()

    ts = df.set_index('asof_date')['value'].sort_index()
    if not ccy_pair.startswith('USD'):
        ts = 1.0 / ts

    result = pd.DataFrame({'spot': ts})
    result.index = pd.to_datetime(result.index)
    result.index.name = 'date'
    return result


def get_fx_forwards(ccy_pair, fromdt, todt):
    source = market_data.GS_SOURCE
    df = fx_forward_data.get_fx_forward_data_by_fx(ccy_pair, source, fromdt, todt)
    if df is None or df.empty:
        return pd.DataFrame()

    pivot = df.pivot_table(
        index='asof_date', columns='fwd_tenor',
        values='forward_rate', aggfunc='mean',
    )
    keep = [t for t in FWD_TENORS if t in pivot.columns]
    if not keep:
        return pd.DataFrame()

    result = pivot[keep]
    result.index = pd.to_datetime(result.index)
    result.index.name = 'date'
    return result


def get_fx_implied_vol(ccy_pair, fromdt, todt):
    source = market_data.GS_SOURCE
    df = fx_vol_data.get_fx_vol_data_by_fx(ccy_pair, source, fromdt, todt)
    if df is None or df.empty:
        return pd.DataFrame()

    df['rel_strike_abs'] = df['relative_strike'].astype(float).abs()
    atm = df[df['rel_strike_abs'] < 0.01]
    if atm.empty:
        atm = df.loc[df.groupby(['asof_date', 'option_tenor'])['rel_strike_abs'].idxmin()]

    pivot = atm.pivot_table(
        index='asof_date', columns='option_tenor',
        values='implied_volatility', aggfunc='mean',
    )
    keep = [t for t in OPTION_TENORS if t in pivot.columns]
    if not keep:
        return pd.DataFrame()

    result = pivot[keep]
    result.index = pd.to_datetime(result.index)
    result.index.name = 'date'
    return result


def calc_realized_vol(spot_df, window=21):
    if spot_df is None or spot_df.empty or 'spot' not in spot_df.columns:
        return pd.DataFrame()
    returns = spot_df['spot'].pct_change()
    rv = returns.rolling(window, min_periods=max(window // 2, 5)).std() * np.sqrt(252)
    return rv


def calc_all_vol_spreads(df):
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    for short, long in SPREAD_PAIRS:
        if short in df.columns and long in df.columns:
            out[f'{long}/{short}'] = df[long] - df[short]
    return out


def calc_all_vol_flies(df):
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    for short, mid, long in FLY_COMBOS:
        if all(t in df.columns for t in [short, mid, long]):
            out[f'{short}/{mid}/{long}'] = 2 * df[mid] - df[short] - df[long]
    return out


def load_ccy_data(ccy_pair, fromdt, todt):
    logging.info(f'loading fx vol market data for {ccy_pair} from {fromdt} to {todt}')
    fx_spot = get_fx_spot(ccy_pair, fromdt, todt)
    fx_fwd = get_fx_forwards(ccy_pair, fromdt, todt)
    fx_vol = get_fx_implied_vol(ccy_pair, fromdt, todt)

    return {
        'fx_spot': fx_spot,
        'fx_fwd': fx_fwd,
        'fx_vol': fx_vol,
        'vol_spreads': calc_all_vol_spreads(fx_vol),
        'vol_flies': calc_all_vol_flies(fx_vol),
    }


def load_and_cache(ccy_pair, asof_date, lookback='1y'):
    fromdt = dtutil.dateadd(asof_date, f'-{lookback}')
    data = load_ccy_data(ccy_pair, fromdt, asof_date)
    for key, df in data.items():
        _cache.write_cache(df, asof_date, ccy_pair, key)
    return data


def get_cached_or_load(ccy_pair, asof_date, lookback='1y'):
    data = {}
    for key in DATA_KEYS:
        cached = _cache.read_cache(asof_date, ccy_pair, key)
        if cached is not None:
            data[key] = cached
        else:
            return load_and_cache(ccy_pair, asof_date, lookback)
    return data


def get_cached_or_load_many(ccy_pairs, asof_date, lookback='1y'):
    data, missing = _cache.read_many(asof_date, ccy_pairs, DATA_KEYS)
    loaded = {
        ccy_pair: load_ccy_data(
            ccy_pair,
            dtutil.dateadd(asof_date, f'-{lookback}'),
            asof_date,
        )
        for ccy_pair in missing
    }
    _cache.write_many(asof_date, loaded)
    data.update(loaded)
    return data


def purge_old_cache(keep_days=7):
    _cache.purge_old(keep_days)


def refresh_all_ccy_caches(asof_date=None, lookback='1y'):
    asof_date = asof_date or dtutil.dateadd(dtutil.today(), '-1b')
    all_pairs = get_available_ccy_pairs()
    logging.info(f'refreshing fx vol market caches for {asof_date}, pairs={all_pairs}')
    for ccy_pair in all_pairs:
        load_and_cache(ccy_pair, asof_date, lookback)
    purge_old_cache()
    logging.info('fx vol market cache refresh complete')
