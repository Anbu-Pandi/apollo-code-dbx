import logging
import os
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import pytz
from scipy.optimize import brentq
from scipy.interpolate import RegularGridInterpolator

from common.utils import dtutil
from common.utils.jobutil import JobExecutor
from credit_strategy.cdx_bbg_msg.cdx_option_quote_agg import CDXOptionQuoteAgg
from data_access.storage import atomic_file
from pricing_wrapper.market.curves.cds_curve import cds_curve_using_dict

GREEK_NOTIONAL = 100_000_000
GRID_CACHE_SUBDIR = '__cdx_pricing_grids__'

GRID_MEASURES = [
    'payer_price', 'payer_premium',
    'receiver_price', 'receiver_premium',
    'delta', 'gamma', 'vega', 'vega_ann',
]

SPOT_MEASURES = ['par_spread', 'clean_price', 'risky_pv01']

NY_TZ = pytz.timezone('America/New_York')


def is_price_based(product):
    return 'hy' in product.lower()


def col_or_fallback(df, primary, fallback):
    return df[primary].iloc[0] if primary in df.columns else df[fallback].iloc[0]


def get_grid_cache_dir(valuedt=None):
    from apo_config.config import config
    from data_access.dbtables import BETA_EST_SUBAPP
    base = config.getconfig(BETA_EST_SUBAPP, 'local_cache_dir')
    root = os.path.join(base, GRID_CACHE_SUBDIR)
    if valuedt:
        d = os.path.join(root, dtutil.todate(valuedt).strftime('%Y%m%d'))
    else:
        d = root
    os.makedirs(d, exist_ok=True)
    return d


def purge_old_grid_cache(keep_days=7):
    root = get_grid_cache_dir()
    cutoff = dtutil.dateadd(dtutil.today(), f'-{keep_days}d')
    removed = 0
    for name in os.listdir(root):
        subdir = os.path.join(root, name)
        if not os.path.isdir(subdir) or len(name) != 8 or not name.isdigit():
            continue
        try:
            folder_dt = dtutil.todate(name)
        except Exception:
            continue
        if folder_dt < cutoff:
            shutil.rmtree(subdir, ignore_errors=True)
            removed += 1
            logging.info(f'Purged old grid cache: {subdir}')
    return removed


class PricingGrid:

    @staticmethod
    def _norm_key(expiry, strike):
        return (dtutil.todate(expiry), round(float(strike), 6))

    @staticmethod
    def _first_number(df, columns):
        for col in columns:
            if col not in df.columns:
                continue
            vals = pd.to_numeric(df[col], errors='coerce').dropna()
            vals = vals[vals > 0]
            if not vals.empty:
                return float(vals.iloc[0])
        return np.nan

    REVERSE_MEASURES = ['payer_premium', 'receiver_premium']

    def __init__(self, product, valuedt, is_price_based):
        self.product = product
        self.valuedt = dtutil.todate(valuedt)
        self.is_price_based = is_price_based
        self.grids = {}
        self.spot_measures = {}
        self.built_at = None
        self._interpolators = {}
        self._reverse_interpolators = {}

    def build(self, curr_mark, libor_curve, index_coupon, recovery, maturity):
        maturity = dtutil.todate(maturity)
        product_mark = curr_mark[curr_mark['product'] == self.product] if 'product' in curr_mark.columns else curr_mark

        if product_mark.empty:
            logging.warning(f'[{self.product}] No marks to build grid from')
            return

        spot_spread_raw = self._first_number(product_mark, ['spot_spread'])
        if self.is_price_based:
            spot_price = self._first_number(product_mark, ['spot_price', 'price_spot_calc', 'spot'])
            spread_is_price = (
                pd.notnull(spot_spread_raw)
                and pd.notnull(spot_price)
                and 50 <= spot_spread_raw <= 150
                and 50 <= spot_price <= 150
            )
        else:
            spot_price = np.nan
            spread_is_price = False
        if self.is_price_based and (pd.isnull(spot_spread_raw) or spot_spread_raw <= 0 or spread_is_price):
            if pd.notnull(spot_price) and spot_price > 0:
                solved = CDXOptionQuoteAgg.solve_price_or_spread(
                    self.valuedt, libor_curve, maturity, index_coupon, recovery, '0d', px=spot_price
                )
                if pd.notnull(solved) and solved > 0:
                    spot_spread_raw = solved * 1e4
                    logging.info(f'[{self.product}] GRID_BUILD_BOOTSTRAP: '
                                 f'px={spot_price:.4f} -> spread={spot_spread_raw:.2f}bps')
        if self.is_price_based and spread_is_price and pd.notnull(spot_spread_raw) and 50 <= spot_spread_raw <= 150:
            logging.warning(f'[{self.product}] GRID_BUILD_ABORT: spot_spread_raw={spot_spread_raw:.4f} '
                            f'looks like clean price and spread solve failed')
            return

        spot_spread_val = spot_spread_raw / 1e4
        median_vol = product_mark['implied_vol'].median()
        if pd.isnull(median_vol) or median_vol <= 0:
            median_vol = 0.40

        logging.info(f'[{self.product}] GRID_BUILD_INPUT: spot_spread_raw={spot_spread_raw}, '
                     f'spot_spread_val={spot_spread_val:.6f}, is_price_based={self.is_price_based}, '
                     f'libor_curve_date={getattr(libor_curve, "_valuation_date", "N/A")}, '
                     f'coupon={index_coupon}, recovery={recovery}, maturity={maturity}')

        if pd.isnull(spot_spread_val) or spot_spread_val <= 0:
            logging.warning(f'[{self.product}] GRID_BUILD_ABORT: Invalid spot_spread_val={spot_spread_val}')
            return
        if self.is_price_based and spot_spread_val > 1:
            logging.error(f'[{self.product}] GRID_BUILD_ABORT: spot_spread_val={spot_spread_val:.4f} > 1 '
                          f'(raw={spot_spread_raw:.0f}) — looks like price not spread!')
            return
        if not self.is_price_based and spot_spread_val > 0.5:
            logging.error(f'[{self.product}] GRID_BUILD_ABORT: spot_spread_val={spot_spread_val:.4f} > 0.5 '
                          f'(raw={spot_spread_raw:.0f}) — spread > 5000bps is invalid!')
            return

        spot_axis = self._make_spot_axis(spot_spread_val)
        vol_axis = self._make_vol_axis(median_vol)
        logging.info(f'[{self.product}] GRID_BUILD_AXIS: spot_axis=[{spot_axis[0]:.6f}..{spot_axis[-1]:.6f}] '
                     f'({spot_axis[0]*1e4:.1f}bps..{spot_axis[-1]*1e4:.1f}bps), vol_axis=[{vol_axis[0]:.2f}..{vol_axis[-1]:.2f}]')

        option_keys = self._select_option_keys(product_mark)

        curves_by_spot = {}
        spot_measures = {m: np.full(len(spot_axis), np.nan) for m in SPOT_MEASURES}
        d1_key_map = {'clean_price': 'price'}
        failed_spots = 0
        for i, spot in enumerate(spot_axis):
            try:
                curves_by_spot[spot] = cds_curve_using_dict(
                    self.valuedt, libor_curve, {maturity: spot}, recovery_rate=recovery
                )
                d1 = CDXOptionQuoteAgg.calc_delta_one(
                    self.valuedt, libor_curve, curves_by_spot[spot], maturity,
                    index_coupon, recovery=recovery, fwd_tenor='0d',
                    risk_metrics=['par_spread', 'price', 'risky_pv01'],
                )
                for m in SPOT_MEASURES:
                    spot_measures[m][i] = d1.get(d1_key_map.get(m, m), np.nan)
            except Exception as e:
                failed_spots += 1
                if failed_spots <= 3:
                    logging.warning(f'[{self.product}] Curve build failed at spot={spot:.6f}: '
                                    f'{type(e).__name__}: {e}')

        if failed_spots:
            logging.warning(f'[{self.product}] {failed_spots}/{len(spot_axis)} spot curves failed')

        if not curves_by_spot:
            logging.error(f'[{self.product}] All {len(spot_axis)} spot curves failed for '
                          f'valuedt={self.valuedt}, spot_spread={spot_spread_val:.6f}, '
                          f'coupon={index_coupon}, recovery={recovery}, maturity={maturity} '
                          f'— cannot build grid')
            return

        n_valid = sum(1 for v in spot_measures['clean_price'] if pd.notnull(v))
        if n_valid < 2:
            logging.warning(f'[{self.product}] Only {n_valid} valid spot_measures — insufficient for interpolation')
            self.spot_measures = {}
        else:
            self.spot_measures = {'spot_axis': spot_axis, **spot_measures}
            logging.info(f'[{self.product}] spot_measures: {n_valid}/{len(spot_axis)} valid values')

        shared = dict(spot_axis=spot_axis, vol_axis=vol_axis, curves_by_spot=curves_by_spot,
                      libor_curve=libor_curve, maturity=maturity, index_coupon=index_coupon,
                      recovery=recovery)
        funcs = [
            lambda e=exp, s=stk: self._build_option_grid(e, s, **shared)
            for exp, stk in option_keys
        ]
        executor = JobExecutor('thread', max_workers=min(len(funcs), 6))
        results = executor.submitjob(funcs)
        for res in results:
            grid_data = res['result']
            if res['error']:
                logging.warning(f'[{self.product}] Grid option failed: {str(res["error"])[:120]}')
            elif grid_data:
                self.grids[grid_data['key']] = grid_data['data']

        self._build_interpolators()
        self.built_at = datetime.now()
        strikes = sorted(set(k[1] for k in self.grids.keys()))
        expiries = sorted(set(k[0] for k in self.grids.keys()))
        logging.info(f'[{self.product}] Grid built: {len(self.grids)} options, '
                 f'strikes={strikes}, expiries={[str(e) for e in expiries]}')
        logging.info(f'[{self.product}] Grid dimensions: spot={len(spot_axis)}x vol={len(vol_axis)}')

    def _build_option_grid(self, expiry, strike, spot_axis, vol_axis,
                           curves_by_spot, libor_curve, maturity, index_coupon, recovery):
        strike_spread = strike / 1e4
        if self.is_price_based:
            strike_spread = self._solve_strike_spread(
                libor_curve, maturity, index_coupon, recovery, expiry, strike
            )
            if pd.isnull(strike_spread):
                logging.warning(f'[{self.product}] Grid skipped: strike={strike} expiry={expiry} '
                            f'(strike spread solve failed)')
                return None

        ns, nv = len(spot_axis), len(vol_axis)
        payer_px = np.full((ns, nv), np.nan)
        receiver_px = np.full((ns, nv), np.nan)
        vega_ann_mx = np.full((ns, nv), np.nan)

        for i, spot in enumerate(spot_axis):
            if spot not in curves_by_spot:
                continue
            curve = curves_by_spot[spot]
            for j, vol in enumerate(vol_axis):
                shared = dict(
                    valuedt=self.valuedt, expiry=expiry, maturity=maturity,
                    strike=strike_spread, index_coupon=index_coupon,
                    libor_curve=libor_curve, index_curve=curve,
                    recovery=recovery, vol=vol, notnl=GREEK_NOTIONAL,
                    silent_errors=True,
                )
                p_out = CDXOptionQuoteAgg.calc_option(
                    **shared, option_type='payer', risk_metrics=['price', 'vega_ann'],
                )
                if isinstance(p_out, dict):
                    payer_px[i, j] = p_out['price']
                    vega_ann_mx[i, j] = p_out.get('vega_ann', np.nan)
                r_out = CDXOptionQuoteAgg.calc_option(
                    **shared, option_type='receiver', risk_metrics=['price'],
                )
                if isinstance(r_out, dict):
                    receiver_px[i, j] = r_out['price']

        if np.all(np.isnan(payer_px)):
            return None

        payer_prem = payer_px / GREEK_NOTIONAL * 1e4
        spread_bps = spot_axis * 1e4

        rpv01 = np.interp(
            spot_axis,
            self.spot_measures.get('spot_axis', spot_axis),
            self.spot_measures.get('risky_pv01', np.ones(len(spot_axis))),
        )[:, np.newaxis]

        raw_spread_delta = np.gradient(payer_prem, spread_bps, axis=0)
        delta = np.abs(raw_spread_delta) / rpv01
        gamma = np.gradient(delta, spread_bps, axis=0)
        vega = np.gradient(payer_prem, vol_axis, axis=1)

        return {
            'key': PricingGrid._norm_key(expiry, strike),
            'data': {
                'spot_axis': spot_axis,
                'vol_axis': vol_axis,
                'payer_price': payer_px,
                'payer_premium': payer_prem,
                'receiver_price': receiver_px,
                'receiver_premium': receiver_px / GREEK_NOTIONAL * 1e4,
                'delta': delta,
                'gamma': gamma,
                'vega': vega,
                'vega_ann': vega_ann_mx,
            },
        }

    @staticmethod
    def _select_option_keys(product_mark):
        keys = product_mark[['option_end_date', 'strike']].drop_duplicates()
        return [PricingGrid._norm_key(r['option_end_date'], r['strike']) for _, r in keys.iterrows()]

    def _make_spot_axis(self, current_spot):
        if self.is_price_based:
            lo = max(0.005, current_spot * 0.6)
            hi = min(0.15, current_spot * 1.8)
        else:
            lo = max(0.0005, current_spot * 0.4)
            hi = current_spot * 2.5
        return np.linspace(lo, hi, 12)

    def _make_vol_axis(self, current_vol):
        lo = max(0.05, current_vol * 0.3)
        hi = current_vol * 2.5
        return np.linspace(lo, hi, 8)

    def _solve_strike_spread(self, libor_curve, maturity, index_coupon, recovery, expiry, strike_price):
        try:
            return CDXOptionQuoteAgg.solve_price_or_spread(
                self.valuedt, libor_curve, maturity, index_coupon, recovery, expiry, px=strike_price
            )
        except Exception:
            return np.nan

    def _recompute_relative_delta(self):
        if not self.spot_measures or 'risky_pv01' not in self.spot_measures:
            return
        for g in self.grids.values():
            prem = g.get('payer_premium')
            if prem is None:
                continue
            spread_bps = g['spot_axis'] * 1e4
            rpv01 = np.interp(
                g['spot_axis'], self.spot_measures['spot_axis'], self.spot_measures['risky_pv01']
            )[:, np.newaxis]
            raw = np.gradient(prem, spread_bps, axis=0)
            g['delta'] = np.abs(raw) / rpv01
            g['gamma'] = np.gradient(g['delta'], spread_bps, axis=0)

    def _build_interpolators(self):
        self._interpolators = {}
        for key, g in self.grids.items():
            spot_axis = g['spot_axis']
            vol_axis = g['vol_axis']
            self._interpolators[key] = {}
            for measure in GRID_MEASURES:
                matrix = g.get(measure)
                if matrix is not None and not np.all(np.isnan(matrix)):
                    clean = np.nan_to_num(matrix, nan=0.0)
                    try:
                        self._interpolators[key][measure] = RegularGridInterpolator(
                            (spot_axis, vol_axis), clean,
                            method='cubic', bounds_error=False, fill_value=None
                        )
                    except ValueError as e:
                        logging.warning(f'[{self.product}] Interpolator build failed for {key}/{measure}: {e}')

    def _build_reverse_interpolators(self):
        self._reverse_interpolators = {}
        for key, g in self.grids.items():
            spot_axis = g['spot_axis']
            vol_axis = g['vol_axis']
            self._reverse_interpolators[key] = {}

            for measure in self.REVERSE_MEASURES:
                matrix = g.get(measure)
                if matrix is None or np.all(np.isnan(matrix)):
                    continue

                prem_lo = float(np.nanmin(matrix))
                prem_hi = float(np.nanmax(matrix))
                if prem_hi <= prem_lo:
                    continue
                prem_axis = np.linspace(prem_lo, prem_hi, len(vol_axis))

                vol_from_prem = np.full((len(spot_axis), len(prem_axis)), np.nan)
                for i in range(len(spot_axis)):
                    row = matrix[i, :]
                    valid = ~np.isnan(row)
                    if valid.sum() < 2:
                        continue
                    p, v = row[valid], vol_axis[valid]
                    if p[-1] < p[0]:
                        p, v = p[::-1], v[::-1]
                    vol_from_prem[i, :] = np.interp(prem_axis, p, v)

                clean = np.nan_to_num(vol_from_prem, nan=vol_axis[0])
                try:
                    self._reverse_interpolators[key][measure] = RegularGridInterpolator(
                        (spot_axis, prem_axis), clean,
                        method='cubic', bounds_error=False, fill_value=None
                    )
                except ValueError as e:
                    logging.warning(f'[{self.product}] Reverse interpolator failed for {key}/{measure}: {e}')

        n_rev = sum(len(v) for v in self._reverse_interpolators.values())
        logging.info(f'[{self.product}] Reverse interpolators built: {n_rev} '
                 f'across {len(self._reverse_interpolators)} keys')

    def has_key(self, expiry, strike):
        key = self._norm_key(expiry, strike)
        return key in self._interpolators

    def available_keys(self):
        return list(self._interpolators.keys()) if self._interpolators else []

    def interpolate(self, expiry, strike, spot, vol):
        key = self._norm_key(expiry, strike)
        interps = self._interpolators.get(key)
        if interps is None:
            return {}
        point = np.array([[spot, vol]])
        return {measure: float(interp(point)[0]) for measure, interp in interps.items()}

    def interpolate_batch(self, expiries, strikes, spots, vols, measures=('delta', 'gamma', 'vega_ann')):
        n = len(expiries)
        result = {m: np.full(n, np.nan) for m in measures}
        groups, normalized = {}, {}
        for index, pair in enumerate(zip(expiries, strikes)):
            if pair not in normalized:
                normalized[pair] = self._norm_key(*pair)
            groups.setdefault(normalized[pair], []).append(index)
        points = np.column_stack([spots, vols]).astype(float)
        for ukey, indices in groups.items():
            interps = self._interpolators.get(ukey)
            if interps is None:
                continue
            idxs = np.asarray(indices)
            pts = points[idxs]
            valid = ~(np.isnan(pts).any(axis=1))
            if not valid.any():
                continue
            for m in measures:
                if m not in interps:
                    continue
                result[m][idxs[valid]] = interps[m](pts[valid])
        return result

    def interp_spot_measure(self, spot, measure):
        if not self.spot_measures or measure not in self.spot_measures:
            return np.nan
        return float(np.interp(spot, self.spot_measures['spot_axis'], self.spot_measures[measure]))

    def price_to_spread(self, price):
        if not self.spot_measures or 'clean_price' not in self.spot_measures:
            return np.nan
        prices = self.spot_measures['clean_price']
        axis = self.spot_measures['spot_axis']
        if prices[-1] < prices[0]:
            return float(np.interp(price, prices[::-1], axis[::-1]))
        return float(np.interp(price, prices, axis))

    def spread_to_price(self, spread_decimal):
        if not self.spot_measures or 'clean_price' not in self.spot_measures:
            return np.nan
        return float(np.interp(spread_decimal, self.spot_measures['spot_axis'],
                               self.spot_measures['clean_price']))

    def solve_implied_vol(self, expiry, strike, spot, target_premium, measure='payer_premium'):
        key = self._norm_key(expiry, strike)
        interps = self._interpolators.get(key)
        if interps is None or measure not in interps:
            return np.nan
        interp = interps[measure]
        vol_axis = self.grids[key]['vol_axis']
        pts = np.column_stack([np.full(len(vol_axis), spot), vol_axis])
        prices_at_spot = interp(pts).ravel()
        if np.all(np.isnan(prices_at_spot)) or np.all(prices_at_spot == 0):
            return np.nan
        pmin, pmax = float(np.nanmin(prices_at_spot)), float(np.nanmax(prices_at_spot))
        if target_premium < pmin or target_premium > pmax:
            return np.nan
        diffs = prices_at_spot - float(target_premium)
        finite = ~(np.isnan(diffs) | np.isnan(vol_axis))
        diffs = diffs[finite]
        vol_axis = vol_axis[finite]
        if len(vol_axis) < 2:
            return np.nan
        exact = np.flatnonzero(np.isclose(diffs, 0.0, atol=1e-9, rtol=1e-9))
        if len(exact):
            return float(vol_axis[int(exact[0])])

        def _premium_delta(vol):
            return float(interp(np.array([[float(spot), float(vol)]])).ravel()[0] - float(target_premium))

        for lo, hi, dlo, dhi in zip(vol_axis[:-1], vol_axis[1:], diffs[:-1], diffs[1:]):
            if dlo * dhi > 0:
                continue
            return float(brentq(_premium_delta, float(lo), float(hi), xtol=1e-8, rtol=1e-8, maxiter=100))
        return np.nan

    def reprice_positions(self, pos_df):
        if pos_df.empty:
            return pos_df.copy()

        product_pos = pos_df[pos_df['product'] == self.product] if 'product' in pos_df.columns else pos_df
        if product_pos.empty:
            return pd.DataFrame()
        if not self.grids:
            return product_pos.copy()
        def column(name, fallback=None):
            return product_pos[name] if name in product_pos else product_pos.get(fallback, pd.Series(np.nan, index=product_pos.index))

        spots = column('spot_spread').to_numpy(dtype=float, copy=True)
        spots[spots > 1] /= 1e4
        vols = column('implied_vol', 'vol_ref_implied_vol').to_numpy(dtype=float)
        valid = ~(np.isnan(spots) | np.isnan(vols))
        if not valid.any():
            return product_pos.copy()
        measures = tuple(dict.fromkeys(m for interps in self._interpolators.values() for m in interps))
        values = self.interpolate_batch(column('expiration_date').to_numpy()[valid],
                                        column('strike_price', 'strike').to_numpy()[valid],
                                        spots[valid], vols[valid], measures=measures)
        out = product_pos.copy()
        for measure, values in values.items():
            full = np.full(len(product_pos), np.nan)
            full[valid] = values
            out[f'grid_{measure}'] = full
        return out

    def to_parquet(self, cache_dir=None):
        cache_dir = cache_dir or get_grid_cache_dir(self.valuedt)
        rows = []
        for (expiry, strike), g in self.grids.items():
            spot_axis, vol_axis = g['spot_axis'], g['vol_axis']
            for i, spot in enumerate(spot_axis):
                for j, vol in enumerate(vol_axis):
                    row = {'expiry': expiry, 'strike': strike, 'spot': spot, 'vol': vol}
                    for m in GRID_MEASURES:
                        row[m] = g[m][i, j] if m in g else np.nan
                    rows.append(row)
        if not rows:
            logging.warning(f'[{self.product}] No grid data to save')
            return None
        df = pd.DataFrame(rows)
        meta = pd.DataFrame([{
            'product': self.product, 'valuedt': str(self.valuedt),
            'is_price_based': self.is_price_based, 'built_at': str(self.built_at),
        }])
        path = os.path.join(cache_dir, self.grid_filename(self.product))
        meta_path = path.replace('.parquet', '_meta.parquet')
        atomic_file.write_parquet(df, path, index=False)

        if self.spot_measures:
            spot_path = path.replace('.parquet', '_spot.parquet')
            atomic_file.write_parquet(
                pd.DataFrame({k: v for k, v in self.spot_measures.items()}),
                spot_path,
                index=False,
            )
            logging.info(f'[{self.product}] spot_measures saved ({len(self.spot_measures)} keys)')
        else:
            logging.warning(f'[{self.product}] No spot_measures to save')

        atomic_file.write_parquet(meta, meta_path, index=False)
        logging.info(f'[{self.product}] Grid saved to {path} ({len(rows):,} rows)')
        return path

    @classmethod
    def from_parquet(cls, path):
        meta_path = path.replace('.parquet', '_meta.parquet')
        if not os.path.exists(path) or not os.path.exists(meta_path):
            return None

        try:
            meta = pd.read_parquet(meta_path)
        except Exception:
            logging.warning(f'Corrupt meta parquet, skipping: {meta_path}')
            return None
        row = meta.iloc[0]
        grid = cls(row['product'], row['valuedt'], bool(row['is_price_based']))
        grid.built_at = pd.Timestamp(row['built_at']) if pd.notnull(row.get('built_at')) else None

        try:
            df = pd.read_parquet(path)
        except Exception:
            logging.warning(f'Corrupt grid parquet, skipping: {path}')
            return None
        for (expiry, strike), grp in df.groupby(['expiry', 'strike']):
            spot_axis = np.sort(grp['spot'].unique())
            vol_axis = np.sort(grp['vol'].unique())
            indexed = grp.set_index(['spot', 'vol']).sort_index()
            data = {'spot_axis': spot_axis, 'vol_axis': vol_axis}
            for m in GRID_MEASURES:
                if m in indexed.columns:
                    matrix = indexed[m].values.reshape(len(spot_axis), len(vol_axis))
                    data[m] = matrix
            grid.grids[PricingGrid._norm_key(expiry, strike)] = data

        spot_path = path.replace('.parquet', '_spot.parquet')
        if os.path.exists(spot_path):
            try:
                sdf = pd.read_parquet(spot_path)
                grid.spot_measures = {col: sdf[col].values for col in sdf.columns}
            except Exception:
                logging.warning(f'Corrupt spot parquet, skipping: {spot_path}')

        grid._recompute_relative_delta()
        grid._build_interpolators()

        logging.info(f'[{grid.product}] Grid loaded from {path} ({len(grid.grids)} options, '
                 f'spot_measures={bool(grid.spot_measures)})')
        return grid

    @staticmethod
    def grid_filename(product):
        return f'grid_{product}.parquet'


def build_product_grid(product, todt, quotes_df, cache_dir=None):
    from credit_strategy.cdx_bbg_msg.vol_mark_intraday import calc_wt_avg

    mark_time = datetime.now(NY_TZ).strftime('%H:%M')

    product_quotes = quotes_df[quotes_df['product'] == product]
    agg = CDXOptionQuoteAgg(product_quotes)
    agg.filter_quotes(from_threshold='5h', to_threshold='0h', end_time=f'{todt} {mark_time}')

    last_quote_df = agg.quotes.groupby(
        ['sender_bank', 'product', 'asof_date', 'asof_date_time']
    )['spot'].agg(lambda x: x.iloc[-1]).reset_index()
    spot_df = last_quote_df.rename(columns={'spot': 'last_spot'}).groupby('product').apply(
        calc_wt_avg
    ).reset_index(drop=True)
    spot_value = spot_df.drop_duplicates('product', keep='last')['ewa_spot'].iloc[0]
    agg.calc_spot_adj(**{product: spot_value}, option_calcs=True)

    agg.set_median_vol_mark_df(
        mark_intervals={'NOW': mark_time}, window='5h', calc_risk=True, min_quotes=[2]
    )
    curr = agg.median_vol_mark_df.copy()

    curves = agg.cdx_curves_df_orig[agg.cdx_curves_df_orig['product'] == product]
    grid = PricingGrid(product, todt, is_price_based(product))
    grid.build(
        curr_mark=curr,
        libor_curve=agg.libor_curves[max(agg.asof_dates)],
        index_coupon=curves['running_coupon'].iloc[0],
        recovery=curves['real_recovery'].iloc[0],
        maturity=agg._get_product_mty(product),
    )

    grid.to_parquet(cache_dir or get_grid_cache_dir(todt))
    return grid


if __name__ == '__main__':
    from common.utils.envsetup import init_app_using_parser, get_base_parser

    parser = get_base_parser('pricing_grid')
    parser.add_argument('--tickers', nargs='+', default=['CDX-NAHY', 'CDX-NAIG'])
    parser.add_argument('--todt', default=None)

    args = init_app_using_parser(parser)

    from data_access.dbtables.market_data.credit import cds_index_vol

    todt = dtutil.todate(args.todt) if args.todt else dtutil.today()
    cache_dir = get_grid_cache_dir(todt)

    purged = purge_old_grid_cache()
    if purged:
        logging.info(f'Purged {purged} old grid cache folders')

    logging.info(f'Fetching quotes for {todt}, cache_dir={cache_dir}...')
    quotes_df = cds_index_vol.get_quotes(todt, todt, use_live=True)
    products = quotes_df[quotes_df['ticker'].isin(args.tickers)]['product'].unique().tolist()

    logging.info(f'Building grids for {products}...')
    funcs = [lambda p=p: build_product_grid(p, todt, quotes_df, cache_dir) for p in products]
    executor = JobExecutor('process', max_workers=len(products))
    results = executor.submitjob(funcs, initconfig=True)

    for product, res in zip(products, results):
        if res['error']:
            logging.error(f'{product}: {res["error"]}')
        else:
            logging.info(f'{product}: grid built OK')

    logging.info('Done.')
