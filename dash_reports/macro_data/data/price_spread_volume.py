import pandas as pd
import numpy as np
import data_access.dbtables.market_data.credit.cds_index as cds_index
from data_access.dbtables.market_data.fwd.extended.fx import get_fx
from common.utils import dtutil

CDX_TICKERS_SHORT_NAME = {'CDX-NAHY': 'HY', 'CDX-NAIG': 'IG', 'CDX-EM': 'EM', 'ITRAXX-Xover': 'XO',
                          'ITRAXX-Europe': 'MN', 'ITRAXX-FinSen': 'FN', 'ITRAXX-FinSub': 'FS',
                          'ITRAXX-Japan': 'JP'}

CDX_TICKERS = list(CDX_TICKERS_SHORT_NAME.keys())

DISPLAY_ORDER = ['idx_short_name', 'ticker', 'series', 'asof_date', 'prev_asof_date',
                 'maturity_date', 'coupon', 'tenor', 'factor', 'recovery_rate',
                 'mkt_px', 'prev_mkt_px', 'model_px', 'price_skew', 'prev_price_skew',
                 'conv_spd_bps', 'prev_conv_spd_bps', 'model_spd_bps', 'spd_skew_bps', 'prev_spd_skew_bps',
                 'credit_dv01', 'risky_pv01', 'ir_dv01',
                 'daily_gross_notional', '5d_avg_daily_gross_notional', 'gross_transaction', 'weekly_contracts'
                 ]


def add_default_probab(df):
    dp_cal_step = df['conv_spd_bps'].mul(0.0001).div(1 - df['recovery_rate']).mul(
        pd.to_datetime(df['maturity_date']).sub(pd.to_datetime(df['asof_date'])).dt.days.div(365))

    df['default_probability'] = np.maximum(0, 1 - np.exp(-dp_cal_step))
    df['default_probability'] *= 100

    return df


def get_price_spread_volume_df(asof_date, lookback=5):
    prev_date = dtutil.dateadd(asof_date, f'-{lookback}b')

    pricing_df = cds_index.get_markit_index_pricing(prev_date, asof_date, CDX_TICKERS)
    volume_df = cds_index.get_markit_index_volume(prev_date, asof_date, CDX_TICKERS)

    price_vol_df = pd.merge(pricing_df,
                            volume_df,
                            on=['asof_date', 'mnemonic', 'ticker', 'maturity_date'], how='outer')

    price_vol_df['maturity_date'] = pd.to_datetime(price_vol_df['maturity_date'])
    price_vol_df['idx_short_name'] = price_vol_df['ticker'].apply(lambda x: CDX_TICKERS_SHORT_NAME[x])

    daily_gross_notional_avg_df = price_vol_df.groupby(['mnemonic'])[
        'daily_gross_notional'].mean().reset_index().dropna().rename(
        columns={'daily_gross_notional': f'{lookback}d_avg_daily_gross_notional'})

    prev_day_df = price_vol_df[price_vol_df['asof_date'] == prev_date][
        ['asof_date', 'mnemonic', 'price_skew', 'spd_skew_bps', 'mkt_px', 'conv_spd_bps', 'model_px']]

    prev_day_df = prev_day_df.rename(
        columns={col: f'prev_{col}' if col != 'mnemonic' else col for col in prev_day_df.columns})

    curr_day_df = price_vol_df[price_vol_df['asof_date'] == asof_date]

    data_df = curr_day_df.merge(prev_day_df, on='mnemonic', how='left').merge(daily_gross_notional_avg_df,
                                                                              on='mnemonic', how='left')

    curr_dt_fx = get_fx(asof_date)
    # prev_dt_fx = get_fx(prev_date)

    fx_conversion_condition = data_df['ticker'] == 'ITRAXX-Japan'
    fx_conversion_cols = ['daily_gross_notional', 'gross_transaction', f'{lookback}d_avg_daily_gross_notional']

    data_df.loc[fx_conversion_condition, fx_conversion_cols] = data_df.loc[fx_conversion_condition,
    fx_conversion_cols].mul(curr_dt_fx['JPY'])

    data_df = add_default_probab(data_df)

    return data_df


def get_otr_data(tenor='5Y', lookback='5y'):
    end_dt = dtutil.today()
    start_dt = dtutil.dateadd(end_dt, f'-{lookback}')

    df = cds_index.get_otr_index_data(CDX_TICKERS, tenor, start_dt, end_dt)
    df['idx_short_name'] = df['ticker'].apply(lambda x: CDX_TICKERS_SHORT_NAME[x])
    df['asof_date'] = pd.to_datetime(df['asof_date'])
    df['daily_gross_notional'] = pd.to_numeric(df['daily_gross_notional'], errors='coerce')

    return df


def get_cdx_ex_default(ticker, dt):
    df = cds_index.get_cdx_ex_default_data(ticker)
    df['asof_date'] = pd.to_datetime(df['asof_date']).dt.date
    df = df[df['asof_date'] <= dt]
    df = df.drop('load_date', axis=1)
    df['idx_short_name'] = df['ticker'].apply(lambda x: CDX_TICKERS_SHORT_NAME[x])
    return df
