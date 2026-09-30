import pandas as pd
import numpy as np
import logging
import scipy.optimize

from common.utils import dtutil, sqlutil, jobutil
from data_access.dbtables import ICE_INDEX_APP
from data_access.utils import tosqllist
from data_access.spark.api import sparkmanager

from pricing_wrapper.market.curves.zerorate import get_fin_curve_by_curve_name
from pricing_wrapper.utils.risk_calculations import calc_risk_metrics
from pricing_wrapper.products.bonds import bond
from pricing_wrapper.scenarios.curves.zerorate import shift_curve
from dash_reports.issuer_debt_in_index.data.sql_const import BOND_SQL, ASOF_DATE_EQUAL_CLAUSE, ICE_INDICES


def _get_jobber():
    return jobutil.JobExecutor('process', max_workers=8)
    # return sparkmanager


def _get_bond(issue_dt, maturity_dt, coupon):
    securitydict = {
        'security_type': 'BOND',
        'issue_date': issue_dt,
        'maturity_date': maturity_dt,
        'coupon': coupon,
        'notional': 100,
    }
    bondobj = bond.create_security(**securitydict)
    return bondobj


def price_ice_bond_yield_chg(dfrow, dy):
    bd = _get_bond(dtutil.todate(dfrow['issue_date']), dtutil.todate(dfrow['maturity_date']),
                   dfrow['par_wtd_coupon'] / 100)
    yield_scenario = dfrow['yield_to_worst_conv'] / 100 + dy
    risk_vals = calc_risk_metrics(bd, ['clean_price'],
                                  yield_to_maturity=yield_scenario,
                                  settlement_date=dfrow['asof_date'])
    return risk_vals


def price_ice_bond(dfrow, h, rr, discount_curve, metrics=['clean_price']):
    try:
        bd = _get_bond(dtutil.todate(dfrow['issue_date']), dtutil.todate(dfrow['maturity_date']),
                       dfrow['par_wtd_coupon'] / 100)
        risk_vals = calc_risk_metrics(bd, metrics,
                                      discount_curve=discount_curve,
                                      hazard_rate=h,
                                      recovery_rate=rr,
                                      settlement_date=dfrow['asof_date'])
        return risk_vals
    except:
        return {}


def price_ice_bond_oas(dfrow, oas, discount_curve, metrics=['clean_price']):
    bd = _get_bond(dtutil.todate(dfrow['issue_date']), dtutil.todate(dfrow['maturity_date']),
                   dfrow['par_wtd_coupon'] / 100)
    risk_vals = calc_risk_metrics(bd, metrics,
                                  discount_curve=discount_curve,
                                  oas=oas,
                                  settlement_date=dfrow['asof_date'])
    return risk_vals


def get_hazard_rate(dfrow, rr, discount_curve, price_col):
    def solve_h(h_guess):
        return price_ice_bond(dfrow, h_guess, rr=rr, discount_curve=discount_curve).get('clean_price', np.nan) \
            - dfrow[price_col]

    h = scipy.optimize.root(solve_h, 0.001)
    return h.x[0]


def _get_rr_header(rr):
    return f'rr={round(float(rr), 2)}'


def _get_res_map(ice_bonds, recovery_rates):
    res_map = []
    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            res_map.append([i, rr])
    return res_map


def get_df_from_parallel_job(res, res_map, col_prefix):
    df = pd.DataFrame({tuple(k): [v['result']] for k, v in zip(res_map, res)}, index=[None]).T.unstack()
    df.columns = df.columns.map(lambda x: f'{col_prefix}_{_get_rr_header(x[1])}')
    return df


def get_surv_pecs_df(ice_bonds, discount_curves, recovery_rates):
    params = []
    for i, x in ice_bonds.iterrows():
        for rr in recovery_rates:
            params.append([x, rr, discount_curves[x['iso_currency_code']], 'price'])

    jobber = _get_jobber()
    res = jobber.submitjob(get_hazard_rate, params)

    surv_df = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), 'surv')
    pecs_df = surv_df * (1 - np.array(recovery_rates).T) * 1e4
    pecs_df.columns = pecs_df.columns.map(lambda x: x.replace('surv_', 'pecs_'))

    return surv_df.join(pecs_df)


def get_rate_duration(ice_bonds, discount_curves, recovery_rates, bump):
    shifted_plus_curves = {}
    shifted_minus_curves = {}

    for k, v in discount_curves.items():
        if v is not None:
            shifted_plus_curves[k] = shift_curve(v, bump)
            shifted_minus_curves[k] = shift_curve(v, -bump)

    price_bump_plus_params = []
    price_bump_minus_params = []

    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            price_bump_plus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                           shifted_plus_curves[dfrow['iso_currency_code']],
                                           ['clean_price']])
            price_bump_minus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                            shifted_minus_curves[dfrow['iso_currency_code']],
                                            ['clean_price']])

    jobber = _get_jobber()
    res = jobber.submitjob(price_ice_bond, price_bump_plus_params)
    df_rate_plus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))
    res = jobber.submitjob(price_ice_bond, price_bump_minus_params)
    df_rate_minus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))

    rate_duration = (-(df_rate_plus.T / ice_bonds['price'] - 1).T +
                     (df_rate_minus.T / ice_bonds['price'] - 1).T) / 2 / bump
    rate_duration.columns = rate_duration.columns.map(lambda x: x.replace('__', 'rate_duration'))

    return rate_duration


def get_spread_duration(ice_bonds, discount_curves, recovery_rates, bump):
    price_spd_bump_plus_params = []
    price_spd_bump_minus_params = []

    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            price_spd_bump_plus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'] + bump / (1 - rr), rr,
                                               discount_curves[dfrow['iso_currency_code']],
                                               ['clean_price']])
            price_spd_bump_minus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'] - bump / (1 - rr), rr,
                                                discount_curves[dfrow['iso_currency_code']],
                                                ['clean_price']])

    jobber = _get_jobber()
    res = jobber.submitjob(price_ice_bond, price_spd_bump_plus_params)
    df_spd_plus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))
    res = jobber.submitjob(price_ice_bond, price_spd_bump_minus_params)
    df_spd_minus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))

    spread_duration = (-(df_spd_plus.T / ice_bonds['price'] - 1).T + (
            df_spd_minus.T / ice_bonds['price'] - 1).T) / 2 / bump
    spread_duration.columns = spread_duration.columns.map(lambda x: x.replace('__', 'spread_duration'))

    return spread_duration


def calc_pecs(dt):
    select_cols = 'asof_date,ice_index_ticker,ticker,isin_number,description,price,oas,libor_oas,yield_to_worst_conv,' \
                  'modified_duration_to_worst_sa,frequency,par_wtd_coupon,maturity_date,years_since_issue, iso_currency_code'
    ice_bonds = sqlutil.readdfsql(
        BOND_SQL.format(select_cols, tosqllist(ICE_INDICES), ASOF_DATE_EQUAL_CLAUSE.format(dt)), app=ICE_INDEX_APP)
    ice_bonds['issue_date'] = ice_bonds['years_since_issue'].fillna(0) \
        .map(lambda x: dtutil.dateadd(dt, f'-{round(x * 365)}d'))

    discount_curves = {}
    discount_curves['USD'] = get_fin_curve_by_curve_name(dt, 'SOFR OIS')
    discount_curves['GBP'] = get_fin_curve_by_curve_name(dt, 'GBP 3m Libor')
    discount_curves['EUR'] = get_fin_curve_by_curve_name(dt, 'EUR 3m Euribor')

    recovery_rates = [0, 0.4, 0.5]

    surv_pecs_df = get_surv_pecs_df(ice_bonds, discount_curves, recovery_rates)

    ice_bonds = ice_bonds.join(surv_pecs_df)

    bump = 1 / 1e4
    rate_duration = get_rate_duration(ice_bonds, discount_curves, recovery_rates, bump)
    spread_duration = get_spread_duration(ice_bonds, discount_curves, recovery_rates, bump)

    ice_bonds = ice_bonds.join(rate_duration).join(spread_duration)

    return ice_bonds


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    sparkmanager.init()

    df = calc_pecs('2022-05-31')
    logging.info(df)
