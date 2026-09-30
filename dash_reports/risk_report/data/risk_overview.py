import pandas as pd
from scipy.stats import percentileofscore

from common.utils.logutil import *
from dash_reports.report_common.data.data_model import datastore
from data_access.beta_attribution.data_beta_report import get_benchmark_beta
from dash_reports.report_common.data.config import *


def cal_net_gross_exposure(df):
    logging.info("calculating net / gross exposure")

    """consider relevant columns and apply rules"""
    df = df.loc[:, RISK_OVERVIEW['exposure']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['exposure']['rules']['Strategy Desc'])]
    df = df.loc[df['Is Liability'].isin(RISK_OVERVIEW['exposure']['rules']['Is Liability'])]
    df = df.loc[~ df['Issuer Ult'].isin(RISK_OVERVIEW['exposure']['rules']['Issuer Ult'])]

    """Converting MVEs into millions and Fund Wght into %"""
    df['MV Equivalent USD'] = df['MV Equivalent USD'].div(1000000)
    df['Fund Wght'] = df['Fund Wght'].mul(100)

    """Calculation"""
    df = df.groupby(['Fund Entity Code', 'FO Strategy Broad Category', 'Risk Direction']).sum()
    df = df.unstack('Risk Direction')
    df.columns = df.columns.swaplevel()

    df[('', 'Net MVE')] = df[('Long', 'MV Equivalent USD')].replace(np.nan, 0) + df[
        ('Short', 'MV Equivalent USD')].replace(np.nan, 0)

    df[('', 'Gross MVE')] = abs(df[('Long', 'MV Equivalent USD')].replace(np.nan, 0)) + abs(
        df[('Short', 'MV Equivalent USD')].replace(np.nan, 0))

    df[('', 'Long % NAV')] = df[('Long', 'Fund Wght')]
    df[('', 'Short % NAV')] = df[('Short', 'Fund Wght')]

    df[('', 'Net % NAV')] = df[('Long', 'Fund Wght')].replace(np.nan, 0) + df[('Short', 'Fund Wght')].replace(np.nan, 0)

    df[('', 'Gross % NAV')] = abs(df[('Long', 'Fund Wght')].replace(np.nan, 0)) + abs(
        df[('Short', 'Fund Wght')].replace(np.nan, 0))

    df.columns = df.columns.droplevel('Risk Direction')
    df = df.loc[:, ['Net MVE', 'Gross MVE', 'Long % NAV', 'Short % NAV', 'Net % NAV', 'Gross % NAV']]

    """Rounding numbers upto two decimal places"""

    return df


def cal_unlev_ytw(df):
    logging.info('Calculating Unlev YTW')

    df = df.loc[:, RISK_OVERVIEW['unlev_ytw']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['unlev_ytw']['rules']['Strategy Desc'])]
    df['Unlev YTW'] = df['Fund Wght'] * df['Yield to Worst']

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Fund Wght', 'Yield to Worst'})
    df = df.loc[:, rqrd_cols]

    grp_df = df.groupby(RISK_OVERVIEW['grp_by']).sum()

    """Converting vales into % and rounding to two decimal place"""
    grp_df = grp_df.mul(100)

    return grp_df


def cal_theta_yield(df):
    logging.info('Calculating theta yield')

    df = df.loc[:, RISK_OVERVIEW['theta_usd']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['theta_usd']['rules']['Strategy Desc'])]
    df['Annual Theta Yield'] = 365 * (df['Theta USD'] / df['NAV BOM USD']) * 100

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Theta USD', 'NAV BOM USD'})
    df = df.loc[:, rqrd_cols]

    grp_df = df.groupby(RISK_OVERVIEW['grp_by']).sum()

    """Converting vales into % and rounding to two decimal place"""

    return grp_df


def cal_carry_yield(df):
    logging.info('Calculating carry yield')

    df = df.loc[:, RISK_OVERVIEW['carry_usd']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['carry_usd']['rules']['Strategy Desc'])]
    df['Annual Carry Yield'] = 365 * (df['Carry USD'] / df['NAV BOM USD']) * 100

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Carry USD', 'NAV BOM USD'})
    df = df.loc[:, rqrd_cols]

    grp_df = df.groupby(RISK_OVERVIEW['grp_by']).sum()

    """Converting vales into % and rounding to two decimal place"""

    return grp_df


def cal_spread_dur(df):
    logging.info('Calculating spread duration')

    df = df.loc[:, RISK_OVERVIEW['sprd_dur']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['sprd_dur']['rules']['Strategy Desc'])]
    df['Spd Dur'] = df['Fund Wght'] * df['Spread Duration']

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Spread Duration', 'Fund Wght'})
    df = df.loc[:, rqrd_cols]

    grp_df = df.groupby(RISK_OVERVIEW['grp_by']).sum()

    """Converting vales into % and rounding to two decimal place"""

    return grp_df


def cal_mod_dur(df):
    logging.info('Calculating modified duration')

    df = df.loc[:, RISK_OVERVIEW['mod_dur']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['mod_dur']['rules']['Strategy Desc'])]
    df['Mod Dur'] = df['Fund Wght'] * df['Modified Duration']

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Modified Duration', 'Fund Wght'})
    df = df.loc[:, rqrd_cols]

    grp_df = df.groupby(RISK_OVERVIEW['grp_by']).sum()
    grp_df = grp_df.mul(100)

    """Converting vales into % and rounding to two decimal place"""

    return grp_df


def cal_factor_beta(df):
    logging.info('Calculating Factor Beta')

    df = df.loc[:, RISK_OVERVIEW['factor_beta']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['factor_beta']['rules']['Strategy Desc'])]
    df = df.loc[df['Is Liability'].isin(RISK_OVERVIEW['factor_beta']['rules']['Is Liability'])]

    beta_contrib_df = pd.pivot_table(df, index=['Fund Entity Code', 'FO Strategy Broad Category'],
                                     columns=['Reference Index'], values=['Beta Contribution'], aggfunc=np.sum)

    beta_contrib_df.columns = beta_contrib_df.columns.droplevel(None)
    beta_contrib_df.columns.name = None

    ir_beta_contrib_df = df[['Fund Entity Code', 'FO Strategy Broad Category', 'IR Beta Contrib']].groupby(
        ['Fund Entity Code', 'FO Strategy Broad Category']).sum()

    factor_beta_df = pd.concat([beta_contrib_df, ir_beta_contrib_df], sort=False, axis=1)
    factor_beta_df['Net Beta'] = factor_beta_df.sum(axis=1)

    """converting into % and rounding values upto 1 decimal"""
    factor_beta_df = factor_beta_df.mul(100)
    factor_beta_df.loc[:, list(set(factor_beta_df.columns) - {'Net Beta'})] = factor_beta_df.loc[:, list(
        set(factor_beta_df.columns) - {'Net Beta'})]
    factor_beta_df = factor_beta_df.drop('MISC', axis=1)

    return factor_beta_df


def cal_stress_scenario(df):
    logging.info('Calculating Stress Scenario')

    df = df.loc[:, RISK_OVERVIEW['stress_scenario']['rqrd_cols']]
    df = df.loc[~ df['Strategy Desc'].isin(RISK_OVERVIEW['stress_scenario']['rules']['Strategy Desc'])]
    df = df.loc[df['Is Liability'].isin(RISK_OVERVIEW['stress_scenario']['rules']['Is Liability'])]

    df['P&L Bull Market Total USD'] = df['P&L Bull Market Total USD'].div(df['NAV BOM USD'])
    df['P&L Correction Total USD'] = df['P&L Correction Total USD'].div(df['NAV BOM USD'])
    df['P&L Recession Total USD'] = df['P&L Recession Total USD'].div(df['NAV BOM USD'])
    df['P&L Fed Severely Adverse Total USD'] = df['P&L Fed Severely Adverse Total USD'].div(df['NAV BOM USD'])

    rqrd_cols = list(set(df.columns) - {'Strategy Desc', 'Is Liability', 'NAV BOM USD'})

    df = df[rqrd_cols]

    grp_df = df.groupby(['Fund Entity Code', 'FO Strategy Broad Category']).sum()

    """converting into % and rounding values upto 1 decimal"""
    grp_df = grp_df.mul(100).round(1)
    grp_df = grp_df.rename(columns=STRESS_SCENARIO_RENAME)

    return grp_df


def cal_roll_pnl(df):
    logging.info('Calculating rolling pnl')

    df = df.loc[:, RISK_OVERVIEW['pnl_roll_strat']['rqrd_cols']]
    grp_df = df.groupby(['Fund Entity Code', 'FO Strategy Broad Category']).sum()

    """converting into % and rounding values upto 1 decimal"""

    return grp_df


def rename_prev_cols(df, prepend='Prev'):
    col_rename = {}
    for col in df.columns:
        col_rename[col] = prepend + ' ' + col

    return col_rename


def exposure_metric(cur_raw_df, prev_raw_df):
    cur_exposures_df = cal_net_gross_exposure(cur_raw_df)
    prev_exposures_df = cal_net_gross_exposure(prev_raw_df)
    prev_exposures_df = prev_exposures_df.rename(columns=rename_prev_cols(prev_exposures_df))
    exposures_df = pd.concat([cur_exposures_df, prev_exposures_df], axis=1, sort=False)
    exposures_df['Chg Net % NAV'] = exposures_df['Net % NAV'] - exposures_df['Prev Net % NAV']
    exposures_df['Chg Gross % NAV'] = exposures_df['Gross % NAV'] - exposures_df['Prev Gross % NAV']
    exposures_df = exposures_df.loc[:, ['Net MVE', 'Gross MVE', 'Net % NAV',
                                        'Gross % NAV', 'Chg Net % NAV',
                                        'Chg Gross % NAV']]

    return exposures_df


def spread_metric(cur_raw_df, prev_raw_df):
    """Calculate Spread Duration"""
    cur_spd_dur_df = cal_spread_dur(cur_raw_df)
    prev_spd_dur_df = cal_spread_dur(prev_raw_df)
    prev_spd_dur_df = prev_spd_dur_df.rename(columns=rename_prev_cols(prev_spd_dur_df))
    spd_dur_df = pd.concat([cur_spd_dur_df, prev_spd_dur_df], axis=1, sort=False)
    # spd_dur_df['Chg Spd Dur'] = spd_dur_df['Spd Dur'] - spd_dur_df['Prev Spd Dur']

    """Calculate modified duration"""
    cur_mod_dur_df = cal_mod_dur(cur_raw_df)
    prev_mod_dur_df = cal_mod_dur(prev_raw_df)
    prev_mod_dur_df = prev_mod_dur_df.rename(columns=rename_prev_cols(prev_mod_dur_df))
    mod_dur_df = pd.concat([cur_mod_dur_df, prev_mod_dur_df], axis=1, sort=False)
    # mod_dur_df['Chg Mod Dur'] = mod_dur_df['Mod Dur'] - mod_dur_df['Prev Mod Dur']

    duration_metric = pd.concat([spd_dur_df, mod_dur_df], axis=1, sort=False)
    duration_metric = duration_metric.loc[:, ['Spd Dur', 'Mod Dur']]

    return duration_metric


def factor_beta_metric(cur_raw_df, prev_raw_df):
    cur_factor_beta_df = cal_factor_beta(cur_raw_df)
    prev_factor_beta_df = cal_factor_beta(prev_raw_df)
    prev_factor_beta_df = prev_factor_beta_df.rename(columns=rename_prev_cols(prev_factor_beta_df))
    factor_beta_df = pd.concat([cur_factor_beta_df, prev_factor_beta_df], axis=1, sort=False)
    factor_beta_df['Chg Beta'] = factor_beta_df['Net Beta'] - factor_beta_df['Prev Net Beta']

    factor_beta_df = factor_beta_df.loc[:, ~factor_beta_df.columns.str.contains('Prev')]
    ref_column_map = REFERENCE_INDEX
    ref_column_map['IR Beta Contrib'] = '10Y Rates'
    factor_beta_df = factor_beta_df.rename(columns=REFERENCE_INDEX)

    return factor_beta_df


def cal_BOM_NAV(df):
    df = df.loc[:, ['Fund Entity Code', 'NAV BOM USD']]
    df = df.drop_duplicates()
    df = df.set_index(['Fund Entity Code'])
    df = df.rename(columns={'NAV BOM USD': 'BOM NAV'})
    df = df.div(1000000).round(1)
    df.index = pd.MultiIndex.from_product([df.index, ['Total']])
    df.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']

    return df


def stres_scenario_metric(cur_raw_df, prev_raw_df):
    cur_stress_scenario_df = cal_stress_scenario(cur_raw_df)
    prev_stress_scenario_df = cal_stress_scenario(prev_raw_df)
    prev_stress_scenario_df = prev_stress_scenario_df.rename(columns=rename_prev_cols(prev_stress_scenario_df))
    stress_scenario_df = pd.concat([cur_stress_scenario_df, prev_stress_scenario_df], axis=1, sort=False)
    stress_scenario_df['Chg Recession (35.0%)'] = stress_scenario_df['Recession (35.0%)'] - stress_scenario_df[
        'Prev Recession (35.0%)']
    stress_scenario_df['Chg Fed Severe (50.0%)'] = stress_scenario_df['Fed Severe (50%)'] - stress_scenario_df[
        'Prev Fed Severe (50%)']
    stress_scenario_df = stress_scenario_df.loc[:,
                         ['Bull 10.0%', 'Correction (10.0%)', 'Recession (35.0%)', 'Fed Severe (50%)',
                          'Chg Recession (35.0%)', 'Chg Fed Severe (50.0%)']]
    stress_scenario_df = stress_scenario_df.round(1)

    return stress_scenario_df


def generate_risk_overview():
    cur_raw_df = datastore.get_cur_risk()
    prev_raw_df = datastore.get_prev_risk()
    raw_roll_pnl_df = datastore.get_roll_strategy()

    """Pre-processing of data"""
    cur_raw_df = cur_raw_df.loc[cur_raw_df['Fund Entity Code'].isin(FUND_CODE_LIST)]
    prev_raw_df = prev_raw_df.loc[prev_raw_df['Fund Entity Code'].isin(FUND_CODE_LIST)]
    raw_roll_pnl_df = raw_roll_pnl_df.loc[raw_roll_pnl_df['Fund Entity Code'].isin(FUND_CODE_LIST)]

    """Calculate BOM NAV"""
    bom_nav_df = cal_BOM_NAV(cur_raw_df)

    """Calculate Net_Gross_exposure"""
    exposures_df = exposure_metric(cur_raw_df, prev_raw_df)

    """Calculate unlev ytw"""
    unlev_ytw_df = cal_unlev_ytw(cur_raw_df)

    """Calculate theta yield"""
    theta_yield = cal_theta_yield(cur_raw_df)

    """Calculate carry yield"""
    carry_yield = cal_carry_yield(cur_raw_df)

    """Calculate spread duration"""
    spread_df = spread_metric(cur_raw_df, prev_raw_df)

    """Calculate Factor Beta"""
    factor_beta_df = factor_beta_metric(cur_raw_df, prev_raw_df)

    """Calculate Stress Scenario"""
    stress_scenario_df = stres_scenario_metric(cur_raw_df, prev_raw_df)

    """Calculate Rolling Pnl"""
    rolling_pnl = cal_roll_pnl(raw_roll_pnl_df)

    """Concat all above metrics into one dataframe"""
    risk_metrics = pd.concat(
        [exposures_df, unlev_ytw_df, theta_yield, carry_yield, spread_df, factor_beta_df,
         rolling_pnl], axis=1, sort=False)

    """Adding Total line"""
    risk_metrics_total_df = risk_metrics.groupby('Fund Entity Code').sum()
    risk_metrics_total_df.index = pd.MultiIndex.from_product([risk_metrics_total_df.index, ['Total']])
    risk_metrics_total_df.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']
    risk_metrics_total_df = pd.concat([bom_nav_df, risk_metrics_total_df], axis=1, sort=False)

    """Compute Percentile Beta"""
    percentile_beta_df = compute_percentile_beta(risk_metrics_total_df['Net Beta'])

    idx = pd.IndexSlice

    final_df = pd.DataFrame()

    for fund_name in FUND_CODE_LIST:
        temp_1 = risk_metrics.loc[idx[fund_name, :], :]
        temp_total = risk_metrics_total_df.loc[idx[fund_name, :], :]
        final_df = pd.concat([final_df, temp_total, temp_1], sort=False)
        del temp_1, temp_total

    """Adding BOM Column"""
    final_df = pd.concat([final_df, percentile_beta_df], sort=False, axis=1)
    final_df.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']

    """formatting for dash"""
    final_df = final_df.reset_index()
    stress_scenario_df = stress_scenario_df.reset_index()
    final_df = final_df.loc[:, ['Fund Entity Code', 'FO Strategy Broad Category', 'BOM NAV', 'Net MVE',
                                'Gross MVE', 'Net % NAV', 'Gross % NAV', 'Chg Net % NAV',
                                'Chg Gross % NAV', 'Unlev YTW', 'Annual Theta Yield',
                                'Annual Carry Yield', 'Spd Dur', 'Mod Dur', 'HY', 'LL', 'EQ',
                                '10Y Rates', 'Net Beta', 'Chg Beta', 'YTD %-ile Beta', '1 Week Rolling PnL', 'MTD PnL',
                                'QTD PnL', 'YTD PnL']]
    final_df['Mod Dur'] = final_df['Mod Dur'] / 100
    if SAVE_OUTPUT:
        final_df.to_csv(os.path.join(OUTPUT_PATH, 'risk_overview.csv'))

    return final_df, stress_scenario_df


def compute_bnchmrk_mvag(window=78):
    """
    This funciton computes benchmark moving average.
    :param window: lookback
    :return: latest date moving average
    """
    """Get Data"""
    df = get_benchmark_beta()

    pivot_df = pd.pivot_table(df, values='Beta to CDX HY', index='ASOFDATE', columns='Benchmark')
    beta_mvng_avg = pivot_df.rolling(window=window).mean()

    final_df = beta_mvng_avg.iloc[-1].to_frame().T
    final_df = final_df.rename(columns=BENCHMARK_MAP)
    final_df['HY'] = 1
    final_df = final_df[['HY', 'LL TRS', 'SPY', '10Y TRSY']]
    final_df = final_df.rename(columns={'LL TRS': 'LL', 'SPY': 'EQ', '10Y TRSY': '10Y Rates'})

    return final_df


def compute_percentile_beta(beta_df):
    """Compute %-ile Beta"""
    cur_raw_df = datastore.get_risk_series()
    cur_raw_df['Total Beta Contr'] = cur_raw_df['Beta Contribution'] + cur_raw_df['IR Beta Contrib']

    pivot_df = pd.pivot_table(cur_raw_df, index='As Of Date', columns=['Fund Entity Code'], values=['Total Beta Contr'],
                              aggfunc={'Total Beta Contr': np.sum})

    pivot_df.columns = pivot_df.columns.droplevel(0)

    df = pd.DataFrame()
    for fund in FUND_CODE_LIST:
        ytd_beta_percentile = np.round(percentileofscore(pivot_df[fund].values, beta_df[fund]['Total'] / 100), 0)
        idx = pd.MultiIndex.from_product([[fund], ['Total']])
        temp_df = pd.DataFrame(ytd_beta_percentile, index=idx, columns=['YTD %-ile Beta'])
        df = pd.concat([df, temp_df], sort=False)
        del temp_df

    df.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']

    return df


if __name__ == '__main__':
    generate_risk_overview()
