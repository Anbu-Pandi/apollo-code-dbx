import logging

import numpy as np
import pandas as pd

from sdr.dtccpy.credit_utils import _is_px_index

from dash_reports.cdx_options.data.quote_adjust import vol_mid_at_common_spot

GRID_TO_MARK = {
    'payer_premium': 'payer_premium',
    'receiver_premium': 'receiver_premium',
    'delta': 'delta',
    'gamma': 'gamma',
    'vega': 'vega',
    'vega_ann': 'vega_ann',
}

SPOT_TO_MARK = {
    'par_spread': 'par_spread_spot_calc',
    'clean_price': 'price_spot_calc',
    'risky_pv01': 'risky_pv01_spot_calc',
}

VOL_SOLVE_COLS = [
    ('pay_vol_bid_calc', 'payer_bid_adj', 'payer_bid', 'payer_premium'),
    ('pay_vol_ask_calc', 'payer_ask_adj', 'payer_ask', 'payer_premium'),
    ('rec_vol_bid_calc', 'receiver_bid_adj', 'receiver_bid', 'receiver_premium'),
    ('rec_vol_ask_calc', 'receiver_ask_adj', 'receiver_ask', 'receiver_premium'),
]


class GridPricingEngine:
    """All live pricing flows through pre-built scenario grids.

    Three events:
        1. on_spot_update  -- SDR delta-one trade gives a new spot
        2. on_quote_vols   -- dealer quotes carry premiums, reverse-solve vols
        3. enrich_marks    -- forward-lookup all Greeks at current (spot, vol)

    Two grid operations:
        forward:  (spot, vol) -> premiums, Greeks, spread, price, rpv01
        reverse:  (spot, prem) -> implied vol
    """

    def __init__(self, grids):
        self.grids = grids

    # ── Event 1: New spot ───────────────────────────────────

    def on_spot_update(self, curr_mark, product, spot_source, spot_value, sdr_price=None):
        grid = self.grids.get(product)
        mask = curr_mark['product'] == product
        is_px = _is_px_index(product)
        has_grid = grid is not None and bool(grid.spot_measures)

        old_spot_bps = curr_mark.loc[mask, 'spot_spread'].dropna()
        old_spot_bps = float(old_spot_bps.iloc[0]) if not old_spot_bps.empty else None

        if spot_source == 'sdr':
            spot_dec = spot_value
            curr_mark.loc[mask, 'spot_spread'] = spot_dec * 1e4
            if sdr_price is not None and pd.notnull(sdr_price):
                curr_mark.loc[mask, 'spot_price'] = sdr_price
            elif has_grid:
                curr_mark.loc[mask, 'spot_price'] = grid.interp_spot_measure(spot_dec, 'clean_price')
            logging.info(f'[{product}] on_spot_update: sdr → spd_bps={spot_dec * 1e4:.2f}, '
                         f'sdr_price={sdr_price}, has_grid={has_grid}')
        elif is_px:
            curr_mark.loc[mask, 'spot_price'] = spot_value
            if has_grid:
                spot_dec = grid.price_to_spread(spot_value)
                curr_mark.loc[mask, 'spot_spread'] = spot_dec * 1e4
                logging.info(f'[{product}] on_spot_update: price → px={spot_value}, '
                             f'spd_bps={spot_dec * 1e4:.2f}')
            else:
                logging.warning(f'[{product}] on_spot_update: price-based but no grid '
                                f'-- spot_spread NOT updated (price={spot_value})')
        else:
            spot_bps = spot_value
            spot_dec = spot_bps / 1e4 if spot_bps > 1 else spot_bps
            curr_mark.loc[mask, 'spot_spread'] = spot_bps
            if has_grid:
                curr_mark.loc[mask, 'spot_price'] = grid.interp_spot_measure(spot_dec, 'clean_price')
            logging.info(f'[{product}] on_spot_update: spread → spd_bps={spot_bps}, has_grid={has_grid}')

        if old_spot_bps and old_spot_bps > 0 and 'forward_spread' in curr_mark.columns:
            new_spot_bps = curr_mark.loc[mask, 'spot_spread'].dropna()
            if not new_spot_bps.empty:
                ratio = float(new_spot_bps.iloc[0]) / old_spot_bps
                fwd_vals = pd.to_numeric(curr_mark.loc[mask, 'forward_spread'], errors='coerce')
                if (fwd_vals > 0).any():
                    curr_mark.loc[mask, 'forward_spread'] = fwd_vals * ratio

    # ── Event 2: New quotes with premiums → reverse-solve vols

    def on_quote_vols(self, quotes, product):
        grid = self.grids.get(product)
        if grid is None or not grid.grids:
            return

        for col, _, _, _ in VOL_SOLVE_COLS:
            if col not in quotes.columns:
                quotes[col] = np.nan

        sel = quotes['product'] == product
        sub = quotes.loc[sel]
        if sub.empty:
            return
        grid_keys = set(grid._interpolators.keys())

        spot_col = 'par_spread_spot_calc'
        spot_arr = sub[spot_col].values.copy() if spot_col in sub.columns else np.full(len(sub), np.nan)
        raw_spot = sub['spot'].values if 'spot' in sub.columns else np.full(len(sub), np.nan)
        needs_raw = np.isnan(spot_arr)
        raw_valid = ~np.isnan(raw_spot)
        is_px = _is_px_index(product)
        use_raw = raw_valid if (is_px and grid.spot_measures) else (needs_raw & raw_valid)
        if is_px and grid.spot_measures:
            # For price-based products, raw 'spot' is a price -- convert via grid
            for idx_i in np.where(use_raw)[0]:
                spot_arr[idx_i] = grid.price_to_spread(raw_spot[idx_i])
        else:
            spot_arr[use_raw] = np.where(raw_spot[use_raw] > 1, raw_spot[use_raw] / 1e4, raw_spot[use_raw])

        # For spread-based products, 'spot' is NaN — fall back to 'spread_spot'
        if not is_px and 'spread_spot' in sub.columns:
            ss = sub['spread_spot'].values
            still_nan = np.isnan(spot_arr)
            ss_valid = ~np.isnan(ss)
            use_ss = still_nan & ss_valid
            spot_arr[use_ss] = np.where(ss[use_ss] > 1, ss[use_ss] / 1e4, ss[use_ss])

        expiry_arr = sub['expiry'].values if 'expiry' in sub.columns else np.full(len(sub), np.nan)
        strike_arr = sub['strike_display'].values if 'strike_display' in sub.columns else np.full(len(sub), np.nan)

        filled, skipped, solve_fail = 0, 0, 0
        indices = sub.index.values
        for i in range(len(indices)):
            idx = indices[i]
            spot_dec = spot_arr[i]
            expiry = expiry_arr[i]
            strike = strike_arr[i]

            if pd.isnull(spot_dec) or pd.isnull(expiry) or pd.isnull(strike):
                skipped += 1
                continue

            key = grid._norm_key(expiry, strike)
            if key not in grid_keys:
                skipped += 1
                continue

            for vol_col, adj_col, raw_col, measure in VOL_SOLVE_COLS:
                prem = sub.at[idx, adj_col] if adj_col in sub.columns else np.nan
                if pd.isnull(prem) or prem <= 0:
                    prem = sub.at[idx, raw_col] if raw_col in sub.columns else np.nan
                if pd.isnull(prem) or prem <= 0:
                    continue
                vol = grid.solve_implied_vol(expiry, strike, spot_dec, prem, measure)
                if pd.notnull(vol) and vol > 0:
                    quotes.at[idx, vol_col] = vol * 100
                    filled += 1
                else:
                    solve_fail += 1

        logging.info(f'[{product}] quote vols: {filled}/{sel.sum()} filled, '
                 f'{skipped} skipped, {solve_fail} solve_fail')

    # ── Event 3: Forward-lookup all Greeks at current (spot, vol)

    def enrich_marks(self, curr_mark, product):
        grid = self.grids.get(product)
        if grid is None or not grid.grids:
            return curr_mark

        for col in list(GRID_TO_MARK.values()) + list(SPOT_TO_MARK.values()):
            if col not in curr_mark.columns:
                curr_mark[col] = np.nan

        grid_keys = set(grid._interpolators.keys())
        has_spot = bool(grid.spot_measures)
        is_px = _is_px_index(product)
        mask = curr_mark['product'] == product

        curr_mark = self._ensure_grid_rows(curr_mark, product, grid_keys)
        mask = curr_mark['product'] == product

        # For price-based products: if spot_spread is missing but spot_price is
        # available, derive spot_spread from spot_price via the grid.
        if is_px and has_spot and 'spot_price' in curr_mark.columns:
            need_spread = mask & curr_mark['spot_spread'].isna() & curr_mark['spot_price'].notna()
            if need_spread.any():
                px_val = curr_mark.loc[need_spread, 'spot_price'].iloc[0]
                spread_dec = grid.price_to_spread(px_val)
                if pd.notnull(spread_dec):
                    curr_mark.loc[need_spread, 'spot_spread'] = spread_dec * 1e4
                    logging.info(f'[{product}] enrich_marks: derived spot_spread={spread_dec*1e4:.2f}bps '
                                 f'from spot_price={px_val:.2f}')

        spot_vals = curr_mark.loc[mask, 'spot_spread'].dropna()
        spot_bps = spot_vals.iloc[0] if not spot_vals.empty else np.nan
        if pd.notnull(spot_bps):
            curr_mark.loc[mask & curr_mark['spot_spread'].isna(), 'spot_spread'] = spot_bps

        for idx in curr_mark.index[mask]:
            spot = curr_mark.at[idx, 'spot_spread']
            if pd.isnull(spot) or pd.isnull(curr_mark.at[idx, 'option_end_date']):
                continue
            spot_dec = spot / 1e4 if spot > 1 else spot
            if has_spot:
                for sm, mc in SPOT_TO_MARK.items():
                    curr_mark.at[idx, mc] = grid.interp_spot_measure(spot_dec, sm)

        # Ensure spot_price is consistent with spot_spread via grid
        if has_spot and 'price_spot_calc' in curr_mark.columns:
            need_px = mask & curr_mark['price_spot_calc'].notna()
            if 'spot_price' not in curr_mark.columns:
                curr_mark['spot_price'] = np.nan
            no_px = need_px & curr_mark['spot_price'].isna()
            if no_px.any():
                curr_mark.loc[no_px, 'spot_price'] = curr_mark.loc[no_px, 'price_spot_calc']

        self._fill_forward_refs(curr_mark, mask)
        self._fill_vol_gaps(curr_mark, mask)
        self._refresh_ann_vol(curr_mark, mask)

        enriched = 0
        for idx in curr_mark.index[mask]:
            vol = curr_mark.at[idx, 'implied_vol']
            strike = curr_mark.at[idx, 'strike']
            spot = curr_mark.at[idx, 'spot_spread']
            if pd.isnull(vol) or pd.isnull(strike) or pd.isnull(spot):
                continue
            exp = curr_mark.at[idx, 'option_end_date']
            key = grid._norm_key(exp, strike)
            if key not in grid_keys:
                continue
            spot_dec = spot / 1e4 if spot > 1 else spot
            measures = grid.interpolate(exp, strike, spot_dec, vol)
            for grid_key, mark_col in GRID_TO_MARK.items():
                val = measures.get(grid_key, np.nan)
                if pd.notnull(val):
                    curr_mark.at[idx, mark_col] = val
            enriched += 1

        logging.info(f'[{product}] enrich_marks: {enriched}/{mask.sum()} enriched, '
                 f'{len(grid_keys)} grid keys')
        return curr_mark

    @staticmethod
    def _ensure_grid_rows(curr_mark, product, grid_keys):
        from dash_reports.cdx_options.data.pricing_grid import PricingGrid
        existing = set()
        mask = curr_mark['product'] == product
        for _, row in curr_mark.loc[mask, ['option_end_date', 'strike']].iterrows():
            exp, k = row['option_end_date'], row['strike']
            if pd.notnull(exp) and pd.notnull(k):
                existing.add(PricingGrid._norm_key(exp, k))
        missing = grid_keys - existing
        if not missing:
            return curr_mark
        new_rows = [{'product': product,
                     'option_end_date': pd.Timestamp(exp),
                     'strike': float(strike)}
                    for exp, strike in missing]
        result = pd.concat([curr_mark, pd.DataFrame(new_rows)], ignore_index=True)
        result['option_end_date'] = pd.to_datetime(result['option_end_date'], format='mixed', dayfirst=False)
        return result

    @staticmethod
    def _fill_vol_gaps(curr_mark, mask):
        sub = curr_mark.loc[mask]
        if 'implied_vol' not in sub.columns:
            return

        for exp, grp in sub.groupby('option_end_date'):
            filled = grp[grp['implied_vol'].notna()].set_index('strike')['implied_vol'].sort_index()
            if len(filled) < 2:
                continue
            missing_idx = grp.index[grp['implied_vol'].isna()]
            for idx in missing_idx:
                k = curr_mark.at[idx, 'strike']
                if pd.isnull(k):
                    continue
                curr_mark.at[idx, 'implied_vol'] = float(
                    np.interp(k, filled.index.values, filled.values, left=np.nan, right=np.nan)
                )

    @staticmethod
    def _fill_forward_refs(curr_mark, mask):
        sub = curr_mark.loc[mask]
        if sub.empty or 'option_end_date' not in sub.columns:
            return
        for col in ('forward_spread', 'par_spread_fwd_calc', 'forward_price'):
            if col not in curr_mark.columns:
                continue
            vals = pd.to_numeric(curr_mark.loc[mask, col], errors='coerce')
            if vals.notna().sum() == 0:
                continue
            for exp, grp in sub.groupby('option_end_date'):
                exp_vals = pd.to_numeric(grp[col], errors='coerce').dropna()
                exp_vals = exp_vals[exp_vals > 0]
                if exp_vals.empty:
                    continue
                missing_idx = grp.index[pd.to_numeric(grp[col], errors='coerce').isna()]
                if len(missing_idx):
                    curr_mark.loc[missing_idx, col] = float(exp_vals.iloc[0])

    @classmethod
    def _refresh_ann_vol(cls, curr_mark, mask):
        if 'implied_vol' not in curr_mark.columns:
            return
        if 'vol_mid_ann' not in curr_mark.columns:
            curr_mark['vol_mid_ann'] = np.nan
        idx = curr_mark.index[mask]
        for row_idx in idx:
            vol = pd.to_numeric(curr_mark.at[row_idx, 'implied_vol'], errors='coerce')
            fwd_bps = cls._row_fwd_bps(curr_mark, row_idx)
            if pd.notna(vol) and pd.notna(fwd_bps) and fwd_bps > 0:
                curr_mark.at[row_idx, 'vol_mid_ann'] = vol * fwd_bps

    # ── Bulk: run all events for a light refresh ────────────

    def refresh(self, curr_mark, quotes, products, spot_sources, progress_cb=None):
        logging.info(f'grid_engine.refresh: {len(products)} products, '
                     f'{len(spot_sources)} spot_sources, '
                     f'{len(quotes)} quotes')
        for i, product in enumerate(products, 1):
            if progress_cb:
                progress_cb(f'Refreshing {product} ({i}/{len(products)})...')
            grid = self.grids.get(product)
            if grid is not None and getattr(grid, 'grids', None):
                curr_mark = self._ensure_grid_rows(curr_mark, product, set(grid._interpolators.keys()))

            spot_info = spot_sources.get(product)
            if spot_info:
                self.on_spot_update(curr_mark, product, spot_info['source'], spot_info['value'],
                                    sdr_price=spot_info.get('price'))

            if grid is None or not grid.grids:
                continue

            pq = (quotes[quotes['product'] == product].copy()
                  if not quotes.empty and 'product' in quotes.columns
                  else pd.DataFrame())
            if pq.empty:
                continue
            is_px = _is_px_index(product)
            if 'strike_display' not in pq.columns:
                pq['strike_display'] = pq['strike'] if is_px else pq.get('spread_strike', pq['strike'])
            if 'par_spread_spot_calc' not in pq.columns or pq['par_spread_spot_calc'].isna().all():
                raw = pq['spot'].dropna()
                if not raw.empty:
                    s = raw.iloc[-1]
                    if is_px and grid.spot_measures:
                        pq['par_spread_spot_calc'] = grid.price_to_spread(s)
                    elif is_px:
                        # Price-based but no grid -- cannot convert; leave NaN
                        logging.warning(f'[{product}] refresh: price-based spot={s} but no grid '
                                        f'-- par_spread_spot_calc left NaN')
                    else:
                        pq['par_spread_spot_calc'] = (s / 1e4) if s > 1 else s
            self.on_quote_vols(pq, product)
            if not spot_info:
                self._seed_spot_from_quotes(curr_mark, curr_mark['product'] == product, pq, grid, is_px)
            self._update_live_vols(curr_mark, pq, product)
            self.enrich_marks(curr_mark, product)

        return curr_mark

    @staticmethod
    def _seed_spot_from_quotes(curr_mark, mask, quotes, grid, is_px):
        if quotes.empty or 'spot' not in quotes.columns:
            return
        spot_values = pd.to_numeric(quotes['spot'], errors='coerce').dropna()
        if spot_values.empty:
            return
        spot_value = float(spot_values.iloc[-1])
        if 'spot_spread' not in curr_mark.columns:
            curr_mark['spot_spread'] = np.nan
        if 'spot_price' not in curr_mark.columns:
            curr_mark['spot_price'] = np.nan
        if is_px:
            curr_mark.loc[mask & curr_mark['spot_price'].isna(), 'spot_price'] = spot_value
            if grid is not None and getattr(grid, 'spot_measures', None):
                spread = grid.price_to_spread(spot_value)
                if pd.notna(spread):
                    curr_mark.loc[mask & curr_mark['spot_spread'].isna(), 'spot_spread'] = spread * 1e4
        else:
            spot_bps = spot_value if spot_value > 1 else spot_value * 1e4
            curr_mark.loc[mask & curr_mark['spot_spread'].isna(), 'spot_spread'] = spot_bps

    def _update_live_vols(self, curr_mark, quotes, product):
        if 'expiry' not in quotes.columns or 'strike_display' not in quotes.columns:
            return
        grid = self.grids.get(product)
        is_px = _is_px_index(product)

        common_spot_dec, common_spot_px, common_spot_bps = self._resolve_common_spot(
            curr_mark, product, grid, is_px,
        )
        vol_mid = pd.Series(dtype=float)
        if grid is not None and pd.notna(common_spot_dec):
            vol_mid = vol_mid_at_common_spot(
                quotes, grid, product, is_px,
                common_spot_dec, common_spot_px=common_spot_px, common_spot_bps=common_spot_bps,
            )

        if vol_mid.empty:
            vol_mid = self._legacy_dealer_spot_vol_mid(quotes)
            if not vol_mid.empty:
                logging.info(f'[{product}] _update_live_vols: falling back to dealer-spot vol median '
                             f'(grid or common_spot unavailable)')

        if vol_mid.empty:
            return

        if 'vol_mid_ann' not in curr_mark.columns:
            curr_mark['vol_mid_ann'] = np.nan

        mask = curr_mark['product'] == product
        updated = 0
        for idx in curr_mark.index[mask]:
            exp = curr_mark.at[idx, 'option_end_date']
            strike = curr_mark.at[idx, 'strike']
            if pd.isnull(exp) or pd.isnull(strike):
                continue
            exp_dt = pd.to_datetime(exp)
            key = (exp_dt, float(strike))
            if key in vol_mid.index:
                vol = vol_mid[key]
                curr_mark.at[idx, 'implied_vol'] = vol
                fwd_bps = self._row_fwd_bps(curr_mark, idx)
                if pd.notna(vol) and pd.notna(fwd_bps) and fwd_bps > 0:
                    curr_mark.at[idx, 'vol_mid_ann'] = vol * fwd_bps
                updated += 1
        if updated:
            logging.info(f'[{product}] _update_live_vols: {updated} rows updated '
                         f'(common_spot={common_spot_bps:.2f}bps)' if pd.notna(common_spot_bps)
                         else f'[{product}] _update_live_vols: {updated} rows updated (legacy path)')

    @staticmethod
    def _row_fwd_bps(curr_mark, idx):
        if 'forward_spread' in curr_mark.columns:
            v = pd.to_numeric(curr_mark.at[idx, 'forward_spread'], errors='coerce')
            if pd.notna(v) and v > 0:
                return v * 1e4
        if 'par_spread_fwd_calc' in curr_mark.columns:
            v = pd.to_numeric(curr_mark.at[idx, 'par_spread_fwd_calc'], errors='coerce')
            if pd.notna(v) and v > 0:
                return v
        if 'spot_spread' in curr_mark.columns:
            v = pd.to_numeric(curr_mark.at[idx, 'spot_spread'], errors='coerce')
            if pd.notna(v) and v > 0:
                return v
        return np.nan

    @staticmethod
    def _resolve_common_spot(curr_mark, product, grid, is_px):
        mask = curr_mark['product'] == product
        spot_bps_series = curr_mark.loc[mask, 'spot_spread'].dropna() if 'spot_spread' in curr_mark.columns else pd.Series(dtype=float)
        common_spot_bps = float(spot_bps_series.iloc[0]) if not spot_bps_series.empty else np.nan
        common_spot_dec = common_spot_bps / 1e4 if pd.notna(common_spot_bps) else np.nan
        common_spot_px = np.nan
        if is_px and grid is not None and pd.notna(common_spot_dec) and grid.spot_measures:
            common_spot_px = grid.spread_to_price(common_spot_dec)
        elif is_px and 'spot_price' in curr_mark.columns:
            px_series = curr_mark.loc[mask, 'spot_price'].dropna()
            if not px_series.empty:
                common_spot_px = float(px_series.iloc[0])
        return common_spot_dec, common_spot_px, common_spot_bps

    @staticmethod
    def _legacy_dealer_spot_vol_mid(quotes):
        vol_cols = [c for c in VOL_SOLVE_COLS if c[0] in quotes.columns]
        if not vol_cols:
            return pd.Series(dtype=float)
        all_vol_series = []
        for vol_col, _, _, _ in vol_cols:
            valid = quotes[['expiry', 'strike_display', vol_col]].dropna(subset=[vol_col])
            if not valid.empty:
                med = valid.groupby(['expiry', 'strike_display'])[vol_col].median()
                all_vol_series.append(med)
        if not all_vol_series:
            return pd.Series(dtype=float)
        combined = pd.concat(all_vol_series, axis=1)
        return combined.mean(axis=1) / 100
