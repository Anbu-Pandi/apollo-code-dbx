import pandas as pd
import numpy as np
import logging
import sys

from common.utils import dtutil, sqlutil, jobutil
from data_access.dbtables import ICE_INDEX_APP
from data_access.utils import tosqllist

from pricing_wrapper.market.curves.zerorate import get_fin_curve_by_curve_name
from pricing_wrapper.utils.risk_calculations import calc_risk_metrics
from pricing_wrapper.products.bonds import bond
from pricing_wrapper.scenarios.curves.zerorate import shift_curve, scenario_curve
import scipy.optimize
from dash_reports.issuer_debt_in_index.data.sql_const import BOND_SQL, ASOF_DATE_EQUAL_CLAUSE, ICE_INDICES
from data_access.spark.api import sparkmanager


def _get_jobber():
    return sparkmanager
    # return jobutil.JobExecutor('process', max_workers=8)


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


def adjust_spread_scenario(spread, scenario=None):
    scenario = scenario or {}
    spread_mult = scenario.get('spread_mult', 1)
    spread_bump = scenario.get('spread_bump', 0)
    spread_cap = scenario.get('spread_cap', sys.maxsize)
    spread_floor = scenario.get('spread_floor', -sys.maxsize - 1)
    spread_bumped = max(spread_floor, min(spread_cap, spread * spread_mult + spread_bump))
    return spread_bumped


def get_dh(h, rr, scenario, h_0):
    if h_0:
        h = h_0
        rr = 0
    spread = h * (1 - rr)
    spread_bumped = adjust_spread_scenario(spread, scenario)
    h_bumped = spread_bumped / (1 - rr)
    return (h_bumped - h)


def price_ice_bond_scenario(dfrow, h, rr, discount_curve, metrics=['clean_price'], scenario=None, h_0=None):
    try:
        h_bumped = h + get_dh(h, rr, scenario, h_0)
        bd = _get_bond(dtutil.todate(dfrow['issue_date']), dtutil.todate(dfrow['maturity_date']),
                       dfrow['par_wtd_coupon'] / 100)
        scenario_dc = scenario_curve(discount_curve, scenario)
        risk_vals = calc_risk_metrics(bd, metrics,
                                      discount_curve=scenario_dc,
                                      hazard_rate=h_bumped,
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


def get_hazard_rate(dfrow, rr, discount_curve, price_col, scenario=None):
    def solve_h(h_guess):
        return price_ice_bond_scenario(dfrow, h_guess, rr=rr, discount_curve=discount_curve, scenario=scenario).get(
            'clean_price', np.nan) \
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
            params.append([x, rr, discount_curves[x['iso_currency_code']], 'price', {}])

    jobber = _get_jobber()
    res = jobber.submitjob(get_hazard_rate, params)

    surv_df = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), 'surv')
    pecs_df = surv_df * (1 - np.array(recovery_rates).T) * 1e4
    pecs_df.columns = pecs_df.columns.map(lambda x: x.replace('surv_', 'pecs_'))

    return surv_df.join(pecs_df)


def get_scenario_price(ice_bonds, discount_curves, recovery_rates, scenario=None):
    scenario = scenario or {}
    rate_only_scenario = {k: v for k, v in scenario.items() if k.startswith('rate_')}
    spread_only_scenario = {k: v for k, v in scenario.items() if k.startswith('spread_')}
    params = []
    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                           discount_curves[dfrow['iso_currency_code']],
                           ['clean_price'], scenario, dfrow[f'surv_{_get_rr_header(0)}']])
    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                           discount_curves[dfrow['iso_currency_code']],
                           ['clean_price'], rate_only_scenario, dfrow[f'surv_{_get_rr_header(0)}']])
    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                           discount_curves[dfrow['iso_currency_code']],
                           ['clean_price'], spread_only_scenario, dfrow[f'surv_{_get_rr_header(0)}']])

    jobber = _get_jobber()
    res = jobber.submitjob(price_ice_bond_scenario, params)
    res_parts = np.split(np.array(res), 3)
    dfs = []
    for k, v in zip(['price', 'price_rate_only', 'price_spread_only'], res_parts):
        df_px = get_df_from_parallel_job(v, _get_res_map(ice_bonds, recovery_rates), '__') \
            .applymap(lambda x: x.get('clean_price'))
        df_px.columns = df_px.columns.map(lambda x: x.replace('__', k))
        dfs.append(df_px)

    return pd.concat(dfs, axis=1)


def get_rate_duration(ice_bonds, discount_curves, recovery_rates, bump, scenario=None):
    scenario = scenario or {}

    shifted_plus_scenarios = scenario.copy()
    shifted_minus_scenarios = scenario.copy()

    if not 'rate_bump' in shifted_plus_scenarios:
        shifted_plus_scenarios['rate_bump'] = 0
    shifted_plus_scenarios['rate_bump'] += bump

    if not 'rate_bump' in shifted_minus_scenarios:
        shifted_minus_scenarios['rate_bump'] = 0
    shifted_minus_scenarios['rate_bump'] -= bump

    price_bump_plus_params = []
    price_bump_minus_params = []

    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            price_bump_plus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                           discount_curves[dfrow['iso_currency_code']],
                                           ['clean_price'], shifted_plus_scenarios])
            price_bump_minus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                            discount_curves[dfrow['iso_currency_code']],
                                            ['clean_price'], shifted_minus_scenarios])

    jobber = _get_jobber()
    res = jobber.submitjob(price_ice_bond_scenario, price_bump_plus_params)
    df_rate_plus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))
    res = jobber.submitjob(price_ice_bond_scenario, price_bump_minus_params)
    df_rate_minus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))

    rate_duration = (-(df_rate_plus.T / ice_bonds['price'] - 1).T +
                     (df_rate_minus.T / ice_bonds['price'] - 1).T) / 2 / bump
    rate_duration.columns = rate_duration.columns.map(lambda x: x.replace('__', 'rate_duration'))

    return rate_duration


def get_spread_duration(ice_bonds, discount_curves, recovery_rates, bump, scenario=None):
    scenario = scenario or {}

    shifted_plus_scenarios = scenario.copy()
    shifted_minus_scenarios = scenario.copy()

    if not 'spread_bump' in shifted_plus_scenarios:
        shifted_plus_scenarios['spread_bump'] = 0
    shifted_plus_scenarios['spread_bump'] += bump

    if not 'spread_bump' in shifted_minus_scenarios:
        shifted_minus_scenarios['spread_bump'] = 0
    shifted_minus_scenarios['spread_bump'] -= bump

    price_spd_bump_plus_params = []
    price_spd_bump_minus_params = []

    for i, dfrow in ice_bonds.iterrows():
        for rr in recovery_rates:
            price_spd_bump_plus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                               discount_curves[dfrow['iso_currency_code']],
                                               ['clean_price'], shifted_plus_scenarios])
            price_spd_bump_minus_params.append([dfrow, dfrow[f'surv_{_get_rr_header(rr)}'], rr,
                                                discount_curves[dfrow['iso_currency_code']],
                                                ['clean_price'], shifted_minus_scenarios])

    jobber = _get_jobber()
    res = jobber.submitjob(price_ice_bond_scenario, price_spd_bump_plus_params)
    df_spd_plus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))
    res = jobber.submitjob(price_ice_bond_scenario, price_spd_bump_minus_params)
    df_spd_minus = get_df_from_parallel_job(res, _get_res_map(ice_bonds, recovery_rates), '__') \
        .applymap(lambda x: x.get('clean_price'))

    spread_duration = (-(df_spd_plus.T / ice_bonds['price'] - 1).T + (
            df_spd_minus.T / ice_bonds['price'] - 1).T) / 2 / bump
    spread_duration.columns = spread_duration.columns.map(lambda x: x.replace('__', 'spread_duration'))

    return spread_duration


def calc_pecs(dt, scenario=None):
    # ice_bonds = pd.read_pickle(r'c:/temp/ice_bonds.pkl')
    select_cols = 'asof_date,ice_index_ticker,ticker,isin_number,description,price,oas,libor_oas,yield_to_worst_conv,' \
                  'modified_duration_to_worst_sa,spread_duration,frequency,par_wtd_coupon,maturity_date,years_since_issue, iso_currency_code'
    ice_bonds = sqlutil.readdfsql(
        BOND_SQL.format(select_cols, tosqllist(ICE_INDICES), ASOF_DATE_EQUAL_CLAUSE.format(dt)), app=ICE_INDEX_APP)

    ice_bonds.to_pickle(r'c:/temp/ice_bonds.pkl')

    ice_bonds['issue_date'] = ice_bonds['years_since_issue'].map(lambda x: dtutil.dateadd(dt, f'-{round(x * 365)}d'))

    discount_curves = {}
    discount_curves['USD'] = get_fin_curve_by_curve_name(dt, 'SOFR OIS')
    discount_curves['GBP'] = get_fin_curve_by_curve_name(dt, 'GBP 3m Libor')
    discount_curves['EUR'] = get_fin_curve_by_curve_name(dt, 'EUR 3m Euribor')

    recovery_rates = [0, 0.4, 0.5]

    surv_pecs_df = get_surv_pecs_df(ice_bonds, discount_curves, recovery_rates)
    ice_bonds = ice_bonds.join(surv_pecs_df)
    df_px = get_scenario_price(ice_bonds, discount_curves, recovery_rates, scenario)

    ice_bonds = ice_bonds.join(df_px)

    return ice_bonds


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    overrides = {
        'server': 'pue2azuresynapsergsqldwserver.database.windows.net',
        'connection_type': '',
        'database_name': 'marketdata',
        'driver': 'sql',
    }
    config.init('dev', overrides=overrides)
    sparkmanager.init()
    from dash_reports.issuer_debt_in_index.data.pecs_calcs.scenarios import SCENARIOS

    import os

    dt = '2022-06-09'
    folder = r'C:\Users\agite\Box\par equiv credit spd\scenarios'
    tag = f'all_{dt}'

    dfs = {}
    for scenario_name, scenario in SCENARIOS.items():
        df = calc_pecs(dt, scenario)
        dfs[scenario_name] = df
        with pd.ExcelWriter(os.path.join(folder, f'scenario_results_{tag}_{scenario_name}.xlsx')) as writer:
            df.to_excel(writer, sheet_name=scenario_name)

    with pd.ExcelWriter(os.path.join(folder, f'scenario_results_{tag}.xlsx')) as writer:
        for scenario_name, df in dfs.items():
            df.to_excel(writer, sheet_name=scenario_name)
