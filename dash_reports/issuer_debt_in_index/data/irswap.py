import logging

from common.utils import dtutil
from pricing_wrapper.market.curves.zerorate import get_fin_curve_by_ccy
from pricing_wrapper.products.rates import irswap
from pricing_wrapper.utils.date_funcs import tofindate
from pricing_wrapper.utils.risk_calculations import calc_risk_metrics


def _get_test_irswap_obj(value_date, underlying_tenor, notional=100e6):
    securitydict = {
        'security_type': 'IRSWAP',
        'settlement_date': dtutil.dateadd(value_date, '2b'),
        'swap_maturity_date': underlying_tenor,
        'swap_type': 'PAY',
        'fixed_coupon': 0.01,
        'fixed_freq': 'SEMI_ANNUAL',
        'float_freq': 'QUARTERLY',
        'notional': notional
    }

    irswapobj = irswap.create_security(**securitydict)
    return irswapobj


def get_irswap_par(value_dt, mty, ccys):
    dt1 = value_dt
    dt2 = dtutil.dateadd(dt1, '3m')

    par_by_ccy = {}
    for ccy in ccys:
        dcurve = get_fin_curve_by_ccy(dt1, ccy)
        if dcurve is not None:
            fwd_curve = dcurve
            first_fixing = fwd_curve.zero_rate(tofindate(dt2))
            irswapobj = _get_test_irswap_obj(value_dt, mty)
            risk_vals = calc_risk_metrics(irswapobj, ['price', 'forward', 'pv01'],
                                          value_date=value_dt,
                                          first_fixing=first_fixing,
                                          curve=dcurve,
                                          forward_curve=fwd_curve)
            par_by_ccy[ccy] = risk_vals['forward']

    return par_by_ccy


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')
    df = get_irswap_par('2022-09-30', '2y', ['USD', 'EUR'])
    logging.info(df)
