import numpy as np
import pandas as pd

from common.utils.logutil import *
from dash_reports.risk_data_center.data.config import *


def _check_instance_list(val):
    if not isinstance(val, list):
        val = list(val)
    return val

def _concat_rqrd_computed_cols(rqrd_cols, computed_cols):
    return rqrd_cols + computed_cols

def cal_net_gross_exposure(df, grp_by=RISK_OVERVIEW['grp_by'],
                           rqrd_cols=[]):
    logging.info("calculating net / gross exposure")

    grp_by = _check_instance_list(grp_by)
    if 'risk_direction' not in grp_by:
        grp_by.append('risk_direction')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['Net MVE', 'Gross MVE', 'Long % NAV', 'Short % NAV', 'Net % NAV', 'Gross % NAV']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    """consider relevant columns and apply rules"""
    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['exposure']['rules']['strategy_desc'])]
    df = df.loc[df['is_liability'].isin(RISK_OVERVIEW['exposure']['rules']['is_liability'])]
    df = df.loc[~ df['issuer_ult'].isin(RISK_OVERVIEW['exposure']['rules']['issuer_ult'])]

    """Converting MVEs into millions and fund_wght into %"""
    df['mv_equivalent_usd'] = df['mv_equivalent_usd'].div(1000000)
    df['fund_wght'] = df['fund_wght'].mul(100)

    """Calculation"""
    df = df.groupby(grp_by).sum()
    df = df.unstack('risk_direction')
    df.columns = df.columns.swaplevel()

    df[('', 'Net MVE')] = df[('Long', 'mv_equivalent_usd')].replace(np.nan, 0) + df[
        ('Short', 'mv_equivalent_usd')].replace(np.nan, 0)

    df[('', 'Gross MVE')] = abs(df[('Long', 'mv_equivalent_usd')].replace(np.nan, 0)) + abs(
        df[('Short', 'mv_equivalent_usd')].replace(np.nan, 0))

    df[('', 'Long % NAV')] = df[('Long', 'fund_wght')]
    df[('', 'Short % NAV')] = df[('Short', 'fund_wght')]

    df[('', 'Net % NAV')] = df[('Long', 'fund_wght')].replace(np.nan, 0) + df[('Short', 'fund_wght')].replace(np.nan, 0)

    df[('', 'Gross % NAV')] = abs(df[('Long', 'fund_wght')].replace(np.nan, 0)) + abs(
        df[('Short', 'fund_wght')].replace(np.nan, 0))

    df.columns = df.columns.droplevel('risk_direction')
    df = df.loc[:, rqrd_cols]

    return df.reset_index()


def cal_unlev_ytw(df, grp_by=RISK_OVERVIEW['grp_by'], rqrd_cols=[]):
    logging.info('Calculating Unlev YTW')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['unlev_ytw']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['unlev_ytw']['rules']['strategy_desc'])]
    df['unlev_ytw'] = df['fund_wght'] * df['yield_to_worst']

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]

    """Converting vales into % and rounding to two decimal place"""
    grp_df = grp_df.mul(100)

    return grp_df.reset_index()


def cal_theta_yield(df, grp_by=RISK_OVERVIEW['grp_by'], rqrd_cols=[]):
    logging.info('Calculating theta yield')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['annual_theta_yield']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['theta_usd']['rules']['strategy_desc'])]
    df['annual_theta_yield'] = 365 * (df['theta_usd'] / df['nav_bom_usd']) * 100

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]

    return grp_df.reset_index()


def cal_carry_yield(df, grp_by=RISK_OVERVIEW['grp_by'], rqrd_cols=[]):
    logging.info('Calculating carry yield')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['annual_carry_yield']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['carry_usd']['rules']['strategy_desc'])]
    df['annual_carry_yield'] = 365 * (df['carry_usd'] / df['nav_bom_usd']) * 100

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]

    return grp_df.reset_index()


def cal_spread_dur(df, grp_by=RISK_OVERVIEW['grp_by'], rqrd_cols=[]):
    logging.info('Calculating spread_duration')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['spd_dur']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['sprd_dur']['rules']['strategy_desc'])]
    df['spd_dur'] = df['fund_wght'] * df['spread_duration']

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]

    return grp_df.reset_index()


def cal_mod_dur(df, grp_by=RISK_OVERVIEW['grp_by'], rqrd_cols=[]):
    logging.info('Calculating modified_duration')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['mod_dur']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['mod_dur']['rules']['strategy_desc'])]
    df['mod_dur'] = df['fund_wght'] * df['modified_duration']

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]
    grp_df = grp_df.mul(100)

    return grp_df.reset_index()


def cal_stress_scenario(df, grp_by=RISK_OVERVIEW['grp_by'],
                        rqrd_cols=[]):
    logging.info('Calculating Stress Scenario')

    rqrd_cols = _check_instance_list(rqrd_cols)
    _computed_cols = ['pnl_bull_market_usd', 'pnl_correction_usd', 'pnl_recession_usd', 'pnl_fed_severely_adverse_usd']
    rqrd_cols = _concat_rqrd_computed_cols(rqrd_cols, _computed_cols)

    df = df.loc[~ df['strategy_desc'].isin(RISK_OVERVIEW['stress_scenario']['rules']['strategy_desc'])]
    df = df.loc[df['is_liability'].isin(RISK_OVERVIEW['stress_scenario']['rules']['is_liability'])]

    df['pnl_bull_market_usd'] = df['pnl_bull_market_usd'].div(df['nav_bom_usd'])
    df['pnl_correction_usd'] = df['pnl_correction_usd'].div(df['nav_bom_usd'])
    df['pnl_recession_usd'] = df['pnl_recession_usd'].div(df['nav_bom_usd'])
    df['pnl_fed_severely_adverse_usd'] = df['pnl_fed_severely_adverse_usd'].div(df['nav_bom_usd'])

    grp_df = df.groupby(grp_by).sum()
    grp_df = grp_df.loc[:, rqrd_cols]

    """converting into % and rounding values upto 1 decimal"""
    grp_df = grp_df.mul(100).round(1)
    grp_df = grp_df.rename(columns=STRESS_SCENARIO_RENAME)

    return grp_df.reset_index()

def get_beta_ts(df, grp_by=RISK_OVERVIEW['grp_by']):

    df = df.loc[df['reference_index'].isin(['CDX_HY', 'LOAN_TRS', 'SPY Equity'])]
    df = df.set_index(grp_by)

    ref_df = df.loc[:, ['reference_index', 'beta_contribution']]
    ref_df = ref_df.pivot_table(index=grp_by, columns=['reference_index'],
                                values=['beta_contribution'], aggfunc={'beta_contribution': np.sum})
    ref_df.columns = ref_df.columns.droplevel(0)

    ir_df = df.pivot_table(index=grp_by, values=['ir_beta_contrib'],
                                aggfunc={'ir_beta_contrib': np.sum})

    colsname = {'CDX_HY': 'HY', 'LOAN_TRS': 'LL', 'SPY Equity': 'SPX', 'ir_beta_contrib': 'IR'}

    master_df = pd.concat([ref_df, ir_df], axis=1, sort=False)
    master_df = master_df.rename(columns=colsname)
    master_df = master_df.fillna(0)
    master_df['Total Beta to HY'] = master_df['HY'] + master_df['LL'] + master_df['SPX'] + master_df['IR']
    master_df = master_df.mul(100)
    master_df = master_df.reset_index()

    return master_df

