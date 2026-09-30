"""Canonical dealer-quote spot-adjustment helpers.

Single source of truth for delta-adjusting a dealer raw premium from the dealer's
own spot to a common reference spot, and for re-implying vols at that common spot.

Used by:
  - charts.build_option_ladder  (option stack premium display)
  - grid_engine._update_live_vols (vol-mid feeding curr_mark.implied_vol -> vol grid)

This is the standard market convention: linear delta-adjustment.
"""

import logging

import numpy as np
import pandas as pd


def delta_adjust_to_spot(raw_prem, dealer_spot, common_spot,
                          delta_val, rpv01, is_price,
                          common_spot_bps=None, dealer_spot_bps=None):
    """Linearly delta-adjust a dealer raw premium from dealer_spot to common_spot.

    Price-based products (HY): adj = raw + (common_px - dealer_px) * delta
    Spread-based products (IG): adj = raw + (common_bps - dealer_bps) * -1e-2 * delta * rpv01

    Returns NaN when any required input is missing.
    """
    if pd.isna(raw_prem) or pd.isna(delta_val) or pd.isna(dealer_spot):
        return np.nan
    if is_price:
        if pd.isna(common_spot):
            return np.nan
        spot_chg = float(common_spot) - float(dealer_spot)
        return float(raw_prem) + spot_chg * float(delta_val)
    if common_spot_bps is None or pd.isna(common_spot_bps) or pd.isna(rpv01) or rpv01 == 0:
        return np.nan
    d_bps = float(dealer_spot_bps) if dealer_spot_bps is not None and not pd.isna(dealer_spot_bps) \
        else (float(dealer_spot) if float(dealer_spot) > 1 else float(dealer_spot) * 1e4)
    spot_chg = (float(common_spot_bps) - d_bps) * -1e-2
    return float(raw_prem) + spot_chg * float(delta_val) * float(rpv01)


_VOL_SOLVE_SPECS = (
    ('payer_bid', 'payer_delta', 'payer_premium'),
    ('payer_ask', 'payer_delta', 'payer_premium'),
    ('receiver_bid', 'receiver_delta', 'receiver_premium'),
    ('receiver_ask', 'receiver_delta', 'receiver_premium'),
)


def compute_dealer_vols_at_common_spot(quotes, grid, product, is_price,
                                       common_spot_dec, common_spot_px=None,
                                       common_spot_bps=None):
    """For each dealer row, delta-adjust their raw bid/ask to common_spot and solve IV.

    Returns a DataFrame with one row per (sender_bank, expiry, strike_display) and
    columns: pay_vol_bid_at_ref, pay_vol_ask_at_ref, rec_vol_bid_at_ref, rec_vol_ask_at_ref
    (all in DECIMAL — divide by 100 vs the dealer pay_vol_*_calc PERCENT convention).
    """
    if grid is None or quotes is None or quotes.empty:
        return pd.DataFrame(columns=['sender_bank', 'expiry', 'strike_display',
                                     'pay_vol_bid_at_ref', 'pay_vol_ask_at_ref',
                                     'rec_vol_bid_at_ref', 'rec_vol_ask_at_ref'])
    cols = quotes.columns
    spot_col = 'spot' if is_price else 'par_spread_spot_calc'
    rpv01_col = 'risky_pv01_spot_calc'
    keep_cols = ['sender_bank', 'expiry', 'strike_display', spot_col,
                 'payer_bid', 'payer_ask', 'receiver_bid', 'receiver_ask',
                 'payer_delta', 'receiver_delta']
    if rpv01_col in cols:
        keep_cols.append(rpv01_col)
    keep_cols = [c for c in keep_cols if c in cols]
    sub = quotes[keep_cols].copy()
    if 'asof_date_time' in cols:
        sub['asof_date_time'] = quotes['asof_date_time']
        sub = sub.sort_values('asof_date_time') \
                  .drop_duplicates(['sender_bank', 'expiry', 'strike_display'], keep='last') \
                  .drop(columns=['asof_date_time'])

    out = sub[['sender_bank', 'expiry', 'strike_display']].copy()
    out['pay_vol_bid_at_ref'] = np.nan
    out['pay_vol_ask_at_ref'] = np.nan
    out['rec_vol_bid_at_ref'] = np.nan
    out['rec_vol_ask_at_ref'] = np.nan

    if pd.isna(common_spot_dec):
        return out
    if is_price and pd.isna(common_spot_px):
        return out
    if not is_price and pd.isna(common_spot_bps):
        return out

    out_idx = out.index.tolist()

    for i, idx in enumerate(out_idx):
        row = sub.iloc[i]
        exp = row.get('expiry')
        strike = row.get('strike_display')
        if pd.isna(exp) or pd.isna(strike):
            continue
        d_spot = row.get(spot_col)
        if pd.isna(d_spot):
            continue
        rpv01 = row.get(rpv01_col, 1.0) if rpv01_col in sub.columns else 1.0

        for raw_col, delta_col, measure in _VOL_SOLVE_SPECS:
            raw = row.get(raw_col)
            delta = row.get(delta_col)
            if pd.isna(raw) or pd.isna(delta) or float(raw) <= 0:
                continue
            adj = delta_adjust_to_spot(raw, d_spot,
                                       common_spot_px if is_price else None,
                                       delta, rpv01, is_price,
                                       common_spot_bps=common_spot_bps)
            if pd.isna(adj) or float(adj) <= 0:
                continue
            vol = grid.solve_implied_vol(exp, strike, common_spot_dec, float(adj), measure)
            if pd.notna(vol) and vol > 0:
                out_col = {'payer_bid': 'pay_vol_bid_at_ref', 'payer_ask': 'pay_vol_ask_at_ref',
                           'receiver_bid': 'rec_vol_bid_at_ref',
                           'receiver_ask': 'rec_vol_ask_at_ref'}[raw_col]
                out.at[idx, out_col] = float(vol)
    return out


def vol_mid_at_common_spot(quotes, grid, product, is_price,
                           common_spot_dec, common_spot_px=None, common_spot_bps=None):
    """Median across dealers of (vol_bid_at_ref, vol_ask_at_ref), then average -> vol_mid (DECIMAL).

    Returns a Series indexed by (expiry, strike) with the per-(K, T) vol_mid.
    """
    per_dealer = compute_dealer_vols_at_common_spot(
        quotes, grid, product, is_price,
        common_spot_dec, common_spot_px=common_spot_px, common_spot_bps=common_spot_bps,
    )
    if per_dealer.empty:
        return pd.Series(dtype=float)

    vol_cols = ['pay_vol_bid_at_ref', 'pay_vol_ask_at_ref',
                'rec_vol_bid_at_ref', 'rec_vol_ask_at_ref']
    medians = []
    for c in vol_cols:
        valid = per_dealer[['expiry', 'strike_display', c]].dropna(subset=[c])
        if not valid.empty:
            medians.append(valid.groupby(['expiry', 'strike_display'])[c].median())
    if not medians:
        return pd.Series(dtype=float)
    combined = pd.concat(medians, axis=1)
    vol_mid = combined.mean(axis=1)
    logging.info(f'[{product}] vol_mid_at_common_spot: {len(vol_mid)} (expiry,strike) pts '
                 f'from {len(per_dealer)} dealer rows')
    return vol_mid
