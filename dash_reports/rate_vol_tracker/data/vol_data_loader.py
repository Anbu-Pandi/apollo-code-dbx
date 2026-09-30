import logging

import numpy as np
import pandas as pd
from financepy.products.rates.ibor_swaption import IborSwap
from financepy.utils.calendar import CalendarTypes, BusDayAdjustTypes, DateGenRuleTypes
from financepy.utils.day_count import DayCountTypes
from financepy.utils.frequency import FrequencyTypes
from financepy.utils.global_types import SwapTypes
from financepy.utils.math import ONE_MILLION

from common.utils import dtutil
from data_access.dbtables import market_data
from data_access.dbtables.market_data.vol import irswaption_vol_curve_data
from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
from pricing_wrapper.utils.date_funcs import tofindate, tofindate_relative

from dash_reports.rate_vol_tracker.config import (
    CCYS, FWD_STARTS_WITH_SPOT, UNDERLYING_TENORS, OPTION_TENORS,
    SPREAD_PAIRS, FLY_COMBOS, CCY_FLOAT_TENOR, CACHE_SUBDIR, DATA_KEYS,
)
from dash_reports.vol_tracker_common.cache_utils import CacheManager

_cache = CacheManager(CACHE_SUBDIR)


def _make_fwd_swap(expiry_date, underlying_tenor):
    maturity = tofindate_relative(underlying_tenor, expiry_date)
    return IborSwap(expiry_date, maturity, SwapTypes.PAY, 0.0001,
                    FrequencyTypes.SEMI_ANNUAL, DayCountTypes.THIRTY_E_360_ISDA,
                    ONE_MILLION, 0.0, FrequencyTypes.QUARTERLY, DayCountTypes.ACT_360,
                    CalendarTypes.WEEKEND, BusDayAdjustTypes.NONE, DateGenRuleTypes.BACKWARD)


def _calc_fwd_swap_rate(value_date, fwd_start, underlying_tenor, discount_curve):
    expiry_date = tofindate_relative(fwd_start, value_date) if fwd_start != 'spot' else value_date
    swap = _make_fwd_swap(expiry_date, underlying_tenor)
    return swap.swap_rate(value_date, discount_curve, discount_curve)


def get_fwd_rates(ccy, fromdt, todt):
    dcurves = get_fin_curves_by_ccy(fromdt, todt, ccy)
    if not dcurves:
        return pd.DataFrame()

    rows = []
    for dt, dcurve in sorted(dcurves.items()):
        if dcurve is None:
            continue
        fin_dt = tofindate(dt)
        row = {'date': dt}
        for fwd_start in FWD_STARTS_WITH_SPOT:
            for tenor in UNDERLYING_TENORS:
                col = f'{fwd_start}_{tenor}'
                try:
                    row[col] = _calc_fwd_swap_rate(fin_dt, fwd_start, tenor, dcurve)
                except Exception as e:
                    logging.warning(f'fwd rate calc failed {ccy} {fwd_start} {tenor} {dt}: {e}')
                    row[col] = None
        rows.append(row)

    return pd.DataFrame(rows).set_index('date') if rows else pd.DataFrame()


def get_swaption_vols(ccy, fromdt, todt):
    float_tenor = CCY_FLOAT_TENOR.get(ccy, '3m')
    source = market_data.GS_SOURCE
    frames = []

    for tenor in UNDERLYING_TENORS:
        for swap_type in ['Payer', 'Receiver']:
            swap_name = f'{ccy}-{float_tenor} {swap_type} {tenor}'
            df = irswaption_vol_curve_data.get_irswaption_vol_curve_data_by_swap(
                swap_name, source, fromdt, todt)
            if df is not None and len(df) > 0:
                frames.append(df)

    if not frames:
        return pd.DataFrame()

    all_vols = pd.concat(frames, ignore_index=True)

    atm = all_vols[all_vols['rel_strike'].astype(str).str.strip() == '0'].copy()
    if atm.empty:
        atm = all_vols.copy()
        atm['abs_strike'] = atm['rel_strike'].astype(float).abs()
        atm = atm.loc[atm.groupby(['asof_date', 'option_tenor', 'swap_name'])['abs_strike'].idxmin()]
        atm = atm.drop(columns=['abs_strike'])

    pivot_frames = []
    for tenor in UNDERLYING_TENORS:
        tenor_vols = atm[atm['swap_name'].str.contains(tenor, case=False)]
        for opt_tenor in OPTION_TENORS:
            subset = tenor_vols[tenor_vols['option_tenor'] == opt_tenor]
            if subset.empty:
                continue
            ts = subset.groupby('asof_date')['implied_normal_vol'].mean()
            pivot_frames.append(ts.rename(f'{opt_tenor}_{tenor}'))

    if not pivot_frames:
        return pd.DataFrame()

    result = pd.concat(pivot_frames, axis=1)
    result.index = pd.to_datetime(result.index)
    result.index.name = 'date'
    return result


def calc_realized_vol(df, window=21):
    return df.diff().rolling(window, min_periods=max(window // 2, 5)).std() * np.sqrt(252)


def _calc_spread(df, short_tenor, long_tenor):
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        parts = col.rsplit('_', 1)
        if len(parts) == 2 and parts[1] == long_tenor:
            short_col = f'{parts[0]}_{short_tenor}'
            if short_col in df.columns:
                out[f'{parts[0]}_{long_tenor}/{short_tenor}'] = df[col] - df[short_col]
    return out


def _calc_fly(df, short_tenor, mid_tenor, long_tenor):
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        parts = col.rsplit('_', 1)
        if len(parts) == 2 and parts[1] == mid_tenor:
            short_col = f'{parts[0]}_{short_tenor}'
            long_col = f'{parts[0]}_{long_tenor}'
            if short_col in df.columns and long_col in df.columns:
                out[f'{parts[0]}_{short_tenor}/{mid_tenor}/{long_tenor}'] = (
                    2 * df[col] - df[short_col] - df[long_col]
                )
    return out


def _concat_non_empty(frames):
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, axis=1) if non_empty else pd.DataFrame()


def calc_all_spreads(df):
    return _concat_non_empty([_calc_spread(df, s, l) for s, l in SPREAD_PAIRS])


def calc_all_flies(df):
    return _concat_non_empty([_calc_fly(df, s, m, l) for s, m, l in FLY_COMBOS])


def load_ccy_data(ccy, fromdt, todt):
    logging.info(f'loading rate vol market data for {ccy} from {fromdt} to {todt}')
    fwd_rates = get_fwd_rates(ccy, fromdt, todt)
    swaption_vols = get_swaption_vols(ccy, fromdt, todt)

    return {
        'fwd_rates': fwd_rates,
        'swaption_vols': swaption_vols,
        'fwd_spreads': calc_all_spreads(fwd_rates) if not fwd_rates.empty else pd.DataFrame(),
        'vol_spreads': calc_all_spreads(swaption_vols) if not swaption_vols.empty else pd.DataFrame(),
        'fwd_flies': calc_all_flies(fwd_rates) if not fwd_rates.empty else pd.DataFrame(),
        'vol_flies': calc_all_flies(swaption_vols) if not swaption_vols.empty else pd.DataFrame(),
    }


def load_and_cache(ccy, asof_date, lookback='1y'):
    fromdt = dtutil.dateadd(asof_date, f'-{lookback}')
    data = load_ccy_data(ccy, fromdt, asof_date)
    for key, df in data.items():
        _cache.write_cache(df, asof_date, ccy, key)
    return data


def get_cached_or_load(ccy, asof_date, lookback='1y'):
    data = {}
    for key in DATA_KEYS:
        cached = _cache.read_cache(asof_date, ccy, key)
        if cached is not None:
            data[key] = cached
        else:
            return load_and_cache(ccy, asof_date, lookback)
    return data


def get_cached_or_load_many(ccys, asof_date, lookback='1y'):
    data, missing = _cache.read_many(asof_date, ccys, DATA_KEYS)
    loaded = {
        ccy: load_ccy_data(
            ccy,
            dtutil.dateadd(asof_date, f'-{lookback}'),
            asof_date,
        )
        for ccy in missing
    }
    _cache.write_many(asof_date, loaded)
    data.update(loaded)
    return data


def purge_old_cache(keep_days=7):
    _cache.purge_old(keep_days)


def refresh_all_ccy_caches(asof_date=None, lookback='1y'):
    asof_date = asof_date or dtutil.dateadd(dtutil.today(), '-1b')
    logging.info(f'refreshing rate vol market caches for {asof_date}')
    for ccy in CCYS:
        load_and_cache(ccy, asof_date, lookback)
    purge_old_cache()
    logging.info('rate vol market cache refresh complete')
