import pandas as pd
import numpy as np
from sdr.dtccpy.credit_utils import _is_px_index


GREEKS_COLS = ['gamma_usd', 'vega_ann_usd', 'delta_usd', 'theta']
NOTIONAL_COLS = ['prob_wt_notional', 'prob_wt_estimated_notional', 'notional', 'estimated_notional']


def get_active_strikes_df(df):
    if df.empty:
        return df

    strike_quote_type = 'spot_spread_scenario'
    lower_bound = 35
    upper_bound = 180

    if df['product'].str.contains('NAHY').any():
        strike_quote_type = 'spot_price_scenario'
        lower_bound = 90
        upper_bound = 110

    base_filter = (df['time_scenario'].isna()) & (df[strike_quote_type].isna())
    strike_list = df[base_filter][['strike_price', 'prob_wt_estimated_notional']].abs().groupby('strike_price').sum()
    active = strike_list[strike_list['prob_wt_estimated_notional'] > 1e7]

    trimmed = df[
        (df['strike_price'] <= upper_bound) &
        (df['strike_price'] >= lower_bound)
    ]

    if active.empty:
        return trimmed

    active_min = active.index.min()
    active_max = active.index.max()

    scenario_col = trimmed[strike_quote_type]
    scenario_in_range = scenario_col.isna() | ((scenario_col >= active_min) & (scenario_col <= active_max))
    return trimmed[scenario_in_range]


def build_position_greeks_data(posdf, product):
    if posdf.empty:
        return {}

    is_px = _is_px_index(product)
    active = get_active_strikes_df(posdf)
    vol_ref_spread = posdf['vol_ref_spot_spread'].mean() if 'vol_ref_spot_spread' in posdf.columns else None
    vol_ref_price = posdf['vol_ref_spot_price'].mean() if 'vol_ref_spot_price' in posdf.columns else None

    result = {}

    available_greeks = [c for c in GREEKS_COLS if c in active.columns]
    available_notionals = [c for c in NOTIONAL_COLS if c in active.columns]

    if 'spot_spread_scenario' in active.columns and 'expiration_date' in active.columns and available_greeks:
        result['greeks_by_spread'] = _safe_groupby(
            active, ['spot_spread_scenario', 'expiration_date'], available_greeks, 'sum'
        )

    if 'spot_price_scenario' in active.columns and 'expiration_date' in active.columns and available_greeks:
        result['greeks_by_price'] = _safe_groupby(
            active, ['spot_price_scenario', 'expiration_date'], available_greeks, 'sum'
        )

    if 'strike_price' in active.columns and 'expiration_date' in active.columns and available_notionals:
        bucket = 0.5 if is_px else 1.0
        bucketed = active.assign(strike_price=(active['strike_price'] / bucket).round() * bucket)
        result['notional_by_strike'] = _safe_groupby(
            bucketed, ['strike_price', 'expiration_date'], available_notionals, 'mean'
        )

    result['vol_ref_spot_spread'] = vol_ref_spread
    result['vol_ref_spot_price'] = vol_ref_price
    result['is_px_index'] = is_px

    return result


def _safe_groupby(df, by_cols, val_cols, agg_func):
    existing_by = [c for c in by_cols if c in df.columns]
    existing_val = [c for c in val_cols if c in df.columns]
    if not existing_by or not existing_val:
        return pd.DataFrame()
    return df.groupby(existing_by)[existing_val].agg(agg_func).reset_index()
