import logging

import pandas as pd

from common.utils import dtutil
from data_access.dbtables.market_data.fwd.extended.fx import get_fx_fwd
from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
from pricing_wrapper.utils import date_funcs


def get_discount_curves(fromdt, todt, ccys):
    dc = {}
    logging.info(f'getting discount for {len(ccys)} currecies: {fromdt} {todt} {ccys}')
    for ccy in ccys:
        dc[ccy] = get_fin_curves_by_ccy(fromdt, todt, ccy)
    if 'USD' not in dc:
        dc['USD'] = get_fin_curves_by_ccy(fromdt, todt, 'USD')

    return dc


def calc_usd_xccy_basis(dt, ccy_pair, usd_curve, non_usd_curve, fxspot, dts, fx_fwd_rates):
    try:
        ccy_pair = ccy_pair.upper()
        is_usd_base = ccy_pair[:3] == 'USD'
        findts = date_funcs.tofindates(dts)
        non_usd_dfs = non_usd_curve[dt].df(findts)
        yfs = (dts - dt).dt.days / 365.25

        mult = -1 if is_usd_base else 1
        implied_usd_rates = (((fx_fwd_rates / fxspot) ** mult) / non_usd_dfs) ** (1 / yfs) - 1
        usd_rates = usd_curve[dt].zero_rate(findts)

        return (usd_rates - implied_usd_rates) * 1e4
    except:
        return None


DEFAULT_AMT_COLS = ['amt_issued_loc', 'amt_outstanding_loc']


def adjust_fx_amt_fields(df, spot_fx_df, amt_cols=None):
    amt_cols = amt_cols or DEFAULT_AMT_COLS
    logging.info(f'starting adjust amt fields fx, df shape: {df.shape}, cols: {amt_cols}')
    spot_fx_df = spot_fx_df.rename(columns={'ccy': 'currency', 'value': 'fx_spot', 'fx_rate': 'fx_spot'}, errors='ignore')
    spot_fx_df = spot_fx_df[['asof_date', 'currency', 'fx_spot']].drop_duplicates()

    df['asof_date'] = pd.to_datetime(df['asof_date'])
    spot_fx_df['asof_date'] = pd.to_datetime(spot_fx_df['asof_date'])
    df = pd.merge_asof(df.sort_values('asof_date'), spot_fx_df.sort_values('asof_date'), left_on='asof_date',
                       right_on='asof_date',
                       left_by='currency', right_by='currency', direction='nearest')
    for ccy_col in amt_cols:
        if ccy_col in df.columns:
            usd_col = ccy_col.replace('_loc', '_usd') if '_loc' in ccy_col else f'{ccy_col}_usd'
            df[usd_col] = df[ccy_col] / df['fx_spot']

    return df


def adjust_fx_oas(fromdt, todt, df):
    logging.info(f'starting adjust fx for: {fromdt} {todt}, df shape: {df.shape}')
    ccys = df['currency'].drop_duplicates().to_list()

    logging.info(f'get discount curves for currencies: {ccys}')
    dc = get_discount_curves(fromdt, todt, ccys)
    logging.info(f'get fx forwards')
    fx_df = get_fx_fwd(fromdt, todt)

    df['xccy'] = 0
    df['fx_rate_diff'] = 0
    df['maturity'] = pd.to_datetime(df['maturity']).dt.date

    ccy_dt_avail_df = fx_df[['ccy_pair', 'asof_date']].drop_duplicates().sort_values(['ccy_pair', 'asof_date'])
    ccy_dt_avail_df['asof_date'] = pd.to_datetime(ccy_dt_avail_df['asof_date']).dt.date

    xccy_dict = {}
    fx_rate_diff_dict = {}
    for (ccy_pair, dt), x in fx_df.groupby(['ccy_pair', 'asof_date']):
        dt = dtutil.todate(dt)
        non_usd_ccy = ccy_pair.replace('USD', '')
        if non_usd_ccy not in dc:
            continue
        key = (non_usd_ccy, dt)
        fx_spot = x['fx_spot'].iloc[0]
        xccy = calc_usd_xccy_basis(dt, ccy_pair,
                                   dc['USD'], dc[non_usd_ccy],
                                   fx_spot, x['fwd_tenor_date'], x['fx_fwd'])
        if not xccy is None:
            fx_df.loc[xccy.index, 'xccy'] = xccy
            xccy_dict[key] = fx_df[(fx_df['ccy_pair'] == ccy_pair) & (fx_df['asof_date'] == dt)] \
                [['fwd_tenor_date', 'xccy']].drop_duplicates() \
                .set_index('fwd_tenor_date')
            fx_rate_diff_dict[key] = fx_df[(fx_df['ccy_pair'] == ccy_pair) & (fx_df['asof_date'] == dt)] \
                                         [['fwd_tenor_date', 'fx_rate_diff']].drop_duplicates() \
                                         .set_index('fwd_tenor_date') * (1 if ccy_pair[-3:] == 'USD' else -1)

    for (non_usd_ccy, dt), dfi in df.groupby(['currency', 'asof_date']):
        dt = dtutil.todate(dt)
        match_flag = (ccy_dt_avail_df['ccy_pair'].str.replace('USD', '') == non_usd_ccy) & \
                     (ccy_dt_avail_df['asof_date'] <= dt)
        if len(ccy_dt_avail_df[match_flag]) > 0:
            dt = dtutil.todate(ccy_dt_avail_df[match_flag]['asof_date'].max())
        key = (non_usd_ccy, dt)
        if key in xccy_dict:
            df_mtys = dfi['maturity'].drop_duplicates().to_list()
            std_mtys = xccy_dict[key].index.tolist()
            all_mtys = sorted(list(set(df_mtys + std_mtys)))
            xccy_df = xccy_dict[key].reindex(all_mtys).interpolate()
            rate_diff_df = fx_rate_diff_dict[key].reindex(all_mtys).interpolate()

            df.loc[dfi.index, 'xccy'] = dfi['maturity'].map(lambda x: xccy_df.loc[x, 'xccy'])
            df.loc[dfi.index, 'fx_rate_diff'] = dfi['maturity'].map(lambda x: rate_diff_df.loc[x, 'fx_rate_diff'])

    df['oas_usd'] = pd.to_numeric(df['oas'], errors='coerce') + df['xccy'].fillna(0)
    df['yield_to_worst_conv_usd'] = pd.to_numeric(df['yield_to_worst_conv'], errors='coerce')  + (df['fx_rate_diff'].fillna(0) / 100)
    if 'pecs_rr=0.4' in df.columns:
        df['pecs_rr=0.4_usd'] = pd.to_numeric(df['pecs_rr=0.4'], errors='coerce') + df['xccy'].fillna(0)
        df['pecs_rr=0.4_usd'] = pd.to_numeric(df['pecs_rr=0.4_usd'], errors='coerce').combine_first(df['oas_usd'])

    logging.info(f'completed adjust fx for: {fromdt} {todt}, df shape: {df.shape}')
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from dash_reports.report_common.data.data_model import datastore

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    get_discount_curves('2022-11-01', '2022-11-30', ['CNH'])

    datastore.paraminit()
    dt = '2022-11-30'
    df = datastore.get_issuer_debt_df(dt)
    df = adjust_fx_oas(dt, dt, df)
    datastore.writedf(df, r'C:\temp\__beta_attrib_report_datastore__\2022-11-30_issuer_debt.new.cache')
    logging.info(df)
