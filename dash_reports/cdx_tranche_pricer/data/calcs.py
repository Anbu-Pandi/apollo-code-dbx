import pandas as pd

from common.utils import dtutil
from dash_reports.report_common.data.data_model import datastore
from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
from pricing_wrapper.market.members.cds_basket import basket_df_to_cds_curves
from pricing_wrapper.products.credit import cds_tranche
from pricing_wrapper.utils import risk_calculations


def get_libor_curve(dt, currency='USD'):
    app_key = f'{currency}_{dt}_curve'
    libor_curves = datastore.get_app_key(app_key)
    if libor_curves is None:
        libor_curves = get_fin_curves_by_ccy(dt, dt, currency)
        datastore.set_app_key(app_key, libor_curves)
    return libor_curves


def calc_tranche(dt, df, k1=0.0, k2=1.0, running_coupon=0.05, maturity_date='5Y', corr1=0.30, corr2=0.30):
    dt = dtutil.todate(dt)
    if dtutil.isreldate(maturity_date):
        maturity_date = dtutil.dateadd(dt, maturity_date)

    libor_curves = get_libor_curve(dt)
    df['asof_date'] = dtutil.todate(dt)

    res = []
    for ticker, df_ticker in df.groupby('ticker', as_index=False):
        wts = None
        recovery_rates = None
        if 'wts' in df_ticker.columns:
            df_ticker['wts'] = pd.to_numeric(df_ticker['wts'], errors='coerce')
            df_ticker = df_ticker[df_ticker['wts'].fillna(0) > 0]
            wts = df_ticker['wts'].tolist()
        if 'recovery_rates' in df_ticker.columns:
            df_ticker['recovery_rates'] = pd.to_numeric(df_ticker['recovery_rates'], errors='coerce').fillna(0.0)
            recovery_rates = df_ticker['recovery_rates'].tolist()
        if len(df_ticker) == 0:
            continue
        ic_dict = basket_df_to_cds_curves(df_ticker, libor_curves)
        ic = list(ic_dict.values())[0]
        tranche = cds_tranche.create_security(**{
            'maturity_date': maturity_date,
            'k1': k1,
            'k2': k2,
            'running_coupon': running_coupon
        })

        pricing_inputs = {
            'value_date': dt,
            'corr1': corr1,
            'corr2': corr2,
            'issuer_curves': ic,
            'running_coupon': running_coupon,
            'issuer_weights': wts,
            'issuer_recovery_rates': recovery_rates
        }

        px = risk_calculations.calc_risk_metrics(tranche, ['price'], **pricing_inputs)

        out = {}
        out['ticker'] = df_ticker.iloc[0]['ticker']
        out['spread'] = px['price'][3] * 1e4
        out['px'] = 100 - px['price'][0] / 1e6 * 100
        out['risky_pv01'] = px['price'][2] / px['price'][3] / 1e6
        out['pts_upfront'] = px['price'][0] / 1e6 * 100
        res.append(out)

    return res
