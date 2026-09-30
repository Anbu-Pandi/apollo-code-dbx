import logging

import pandas as pd
from scipy.interpolate import interp1d

from common.utils import dfutil, dtutil
from common.utils.envsetup import get_base_parser, init_app_using_parser
from data_access.dbtables.market_data.fwd.extended.fx import get_fx_fwd
from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
from pricing_wrapper.utils import date_funcs


def _get_discount_curves(fromdt, todt, ccys):
    dc = {}
    ccys = dfutil.tolist(ccys, replacenone=True)
    logging.info(f'getting discount curves for {len(ccys)} currencies: {fromdt} to {todt} | {ccys}')
    for ccy in ccys:
        dc[ccy] = get_fin_curves_by_ccy(fromdt, todt, ccy)
    if 'USD' not in dc:
        dc['USD'] = get_fin_curves_by_ccy(fromdt, todt, 'USD')
    return dc


def _calc_xccy_basis_for_date(dt, ccy_pair, usd_curve, non_usd_curve, fxspot, fwd_tenor_dates, fx_fwd_rates):
    ccy_pair = ccy_pair.upper()
    is_usd_base = ccy_pair[:3] == 'USD'

    dt = dtutil.todate(dt)
    fwd_tenor_dates = pd.to_datetime(fwd_tenor_dates)
    yfs = (fwd_tenor_dates - pd.Timestamp(dt)).dt.days / 365.25

    findts = date_funcs.tofindates(fwd_tenor_dates)
    
    usd_df = usd_curve[dt].df(findts)
    non_usd_df = non_usd_curve[dt].df(findts)
    usd_rate = usd_curve[dt].zero_rate(findts)
    non_usd_rate = non_usd_curve[dt].zero_rate(findts)

    mult = -1 if is_usd_base else 1
    implied_usd_rate = (((fx_fwd_rates / fxspot) ** mult) / non_usd_df) ** (1 / yfs) - 1
    xccy_basis_bps = (usd_rate - implied_usd_rate) * 1e4

    return pd.DataFrame({
        'yf': yfs.values,
        'usd_df': usd_df,
        'usd_rate': usd_rate,
        'non_usd_df': non_usd_df,
        'non_usd_rate': non_usd_rate,
        'implied_usd_rate': implied_usd_rate,
        'xccy_basis_bps': xccy_basis_bps
    }, index=fwd_tenor_dates.index)


def _interpolate_xccy(xccy_df, target_dates, interp_cols, extrapolate=True):
    if len(xccy_df) < 2:
        return pd.DataFrame()
    
    xccy_df = xccy_df.sort_values('fwd_tenor_date').drop_duplicates('fwd_tenor_date')
    asof_dt = dtutil.todate(xccy_df['asof_date'].iloc[0])
    x = (pd.to_datetime(xccy_df['fwd_tenor_date']) - pd.Timestamp(asof_dt)).dt.days.values
    target_days = [(dtutil.todate(d) - asof_dt).days for d in target_dates]
    
    fill_value = 'extrapolate' if extrapolate else None
    
    result = {
        'asof_date': asof_dt,
        'ccy_pair': xccy_df['ccy_pair'].iloc[0],
        'fwd_tenor_date': [dtutil.todate(d) for d in target_dates],
    }
    
    for col in interp_cols:
        if col in xccy_df.columns:
            y = xccy_df[col].values
            interp_func = interp1d(x, y, kind='linear', bounds_error=False, fill_value=fill_value)
            result[col] = interp_func(target_days)
    
    return pd.DataFrame(result)


def _parse_tenors_or_dates(tenors_or_dates, asof_dt):
    tenors_or_dates = dfutil.tolist(tenors_or_dates)
    target_dates = []
    tenor_labels = []

    for item in tenors_or_dates:
        item_str = str(item)
        if dtutil.isreldate(item_str):
            target_dates.append(dtutil.dateadd(asof_dt, item_str))
            tenor_labels.append(item_str)
        else:
            target_dates.append(dtutil.todate(item))
            tenor_labels.append(f'{(dtutil.todate(item) - asof_dt).days}d')

    return target_dates, tenor_labels


INTERP_COLS = ['yf', 'fx_spot', 'fx_fwd', 'usd_df', 'usd_rate', 'non_usd_df', 'non_usd_rate', 'implied_usd_rate', 'xccy_basis_bps']


def get_xccy_basis_history(fromdt, todt, ccy_pairs=None, tenors_or_dates=None, extrapolate=True):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    logging.info(f'calculating xccy basis history: {fromdt} to {todt} | pairs: {ccy_pairs} | tenors_or_dates: {tenors_or_dates}')

    fx_df = get_fx_fwd(fromdt, todt)
    if len(fx_df) == 0:
        logging.warning(f'no fx forward data found for {fromdt} to {todt}')
        return pd.DataFrame()

    ccy_pairs = dfutil.tolist(ccy_pairs, replacenone=True)
    if ccy_pairs:
        ccy_pairs = [p.upper() for p in ccy_pairs]
        fx_df = fx_df[fx_df['ccy_pair'].isin(ccy_pairs)]
        if len(fx_df) == 0:
            logging.warning(f'no fx forward data for pairs: {ccy_pairs}')
            return pd.DataFrame()

    non_usd_ccys = list(set(fx_df['ccy_pair'].str.replace('USD', '').unique()))
    dc = _get_discount_curves(fromdt, todt, non_usd_ccys + ['USD'])

    results = []
    for (ccy_pair, dt), grp in fx_df.groupby(['ccy_pair', 'asof_date']):
        dt = dtutil.todate(dt)
        non_usd_ccy = ccy_pair.replace('USD', '')

        if non_usd_ccy not in dc or dt not in dc[non_usd_ccy]:
            logging.debug(f'skipping {ccy_pair} on {dt}: missing curve for {non_usd_ccy}')
            continue
        if dt not in dc['USD']:
            logging.debug(f'skipping {ccy_pair} on {dt}: missing USD curve')
            continue

        fx_spot = grp['fx_spot'].iloc[0]
        calc_df = _calc_xccy_basis_for_date(
            dt, ccy_pair, dc['USD'], dc[non_usd_ccy],
            fx_spot, grp['fwd_tenor_date'], grp['fx_fwd']
        )

        result_df = grp[['ccy_pair', 'asof_date', 'fwd_tenor', 'fwd_tenor_date', 'fx_spot', 'fx_fwd', 'fx_rate_diff']].copy()
        for col in calc_df.columns:
            result_df[col] = calc_df[col].values
        results.append(result_df)

    if not results:
        logging.warning('no xccy basis results computed')
        return pd.DataFrame()

    df = pd.concat(results, ignore_index=True)

    if tenors_or_dates is not None:
        custom_results = []
        for (ccy_pair, asof_date), grp in df.groupby(['ccy_pair', 'asof_date']):
            asof_dt = dtutil.todate(asof_date)
            custom_dts, tenor_labels = _parse_tenors_or_dates(tenors_or_dates, asof_dt)

            interp_df = _interpolate_xccy(grp, custom_dts, INTERP_COLS, extrapolate=extrapolate)
            if len(interp_df) > 0:
                interp_df['tenor'] = tenor_labels
                custom_results.append(interp_df)

        if custom_results:
            df = pd.concat(custom_results, ignore_index=True)
        else:
            logging.warning('no custom tenor results after interpolation')
            return pd.DataFrame()

    df = df.sort_values(['ccy_pair', 'asof_date', 'fwd_tenor_date']).reset_index(drop=True)
    logging.info(f'xccy basis history computed: {len(df)} rows for {df["ccy_pair"].nunique()} pairs')
    return df


def get_xccy_basis_for_date(asof_date, ccy_pairs=None, tenors_or_dates=None, extrapolate=True):
    asof_date = dtutil.todate(asof_date)
    return get_xccy_basis_history(asof_date, asof_date, ccy_pairs, tenors_or_dates, extrapolate)


if __name__ == '__main__':
    init_app_using_parser(get_base_parser('xccy_basis'))

    from apo_config.config import config
    config.init('prod')

    df = get_xccy_basis_history(
        '2026-02-03', '2026-02-03',
        ccy_pairs=['EURUSD', 'USDJPY'],
        tenors_or_dates=['3m', '6m', '1y', '2y', '5y', '10y'],
        extrapolate=True
    )
    logging.info(f'\n{df.to_string()}')
