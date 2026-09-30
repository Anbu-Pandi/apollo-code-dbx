from copy import deepcopy

import pandas as pd

from dash_reports.liquidity.data.utils import _add_total_line, sort_df, cash_report_file, capital_activity_file, \
    read_risk_data, MV_COL
from dash_reports.liquidity.data.fund_commitment import fund_commitment_summary
from dash_reports.liquidity.data.leverage import leverage_analysis, get_max_leverage_facility

COMMITMENTS_FROM_CAPITAL_ACTIVITY = ['CSTRAT', 'CF', 'TRF']

OVERWRITE_CAPITAL_COMMITMENT_FUNDS = ['ATLAS', 'GIACF', 'DEFINED', 'ADSBDC', 'CSTRAT', 'CF', 'TRF']


def _get_fund_capital(df, key):
    idx = pd.IndexSlice
    df = df.fillna(0)
    df = df.loc[idx[:, key], :]
    result_df = df.groupby('Group').sum()
    result_df = result_df.div(1000000)
    return result_df.reset_index()


def overwrite_commitments_from_capital_activity(df, fund_entity_code):
    capital_activity_df = pd.read_excel(capital_activity_file)

    capital_activity_commit = capital_activity_df.loc[
        capital_activity_df['Fund Entity Code'].isin(fund_entity_code), ['Fund Entity Code',
                                                                         'Less : Total Net Capital (Cont)/Dist']]
    capital_activity_commit = capital_activity_commit.groupby('Fund Entity Code').sum()
    capital_activity_commit = capital_activity_commit / 1000000

    for fund in fund_entity_code:
        df.loc[df['Group'] == 'Unfunded Commitments', fund] = capital_activity_commit.loc[
            fund, 'Less : Total Net Capital (Cont)/Dist']

    return df


def overwrite_capital_commitments(df, fund_entity_code):
    for fund in fund_entity_code:
        df.loc[df['Group'] == 'Unfunded Commitments', fund] = 0
        df.loc['Total Capital Commitment', fund] = 0

    return df


def get_prime_broker_capital(cash_df):
    cash_df_pivot = cash_df.pivot_table(index=['As Of Date', 'Group'], columns='Fund Entity Code', values='Amount USD',
                                        aggfunc={'Amount USD': 'sum'})
    cash_df_balance = cash_df.loc[
        (cash_df['Group'] == 'Cash balances') & cash_df['Description'].str.endswith('MM')]

    cash_balance_pivot = cash_df_balance.pivot_table(index=['As Of Date', 'Group'], columns='Fund Entity Code',
                                                     values='Amount USD',
                                                     aggfunc={'Amount USD': 'sum'})

    buying_power_df = _get_fund_capital(cash_df_pivot, key=['Buying Power/Available Excess'])
    cash_balance_df = _get_fund_capital(cash_balance_pivot, key=['Cash balances'])

    pb_capital_df = pd.concat([buying_power_df, cash_balance_df], sort=False)

    pb_capital_df = _add_total_line(pb_capital_df, 'Prime Broker Buying Power')

    pb_capital_df = pb_capital_df.rename(columns={'Group': 'Metric', })
    col_rename = {'Buying Power/Available Excess': 'Prime Broker Gross Buying Power',
                  'Cash balances': 'Prime Broker Money Market Balance'}

    for col, value in col_rename.items():
        pb_capital_df.loc[pb_capital_df['Metric'] == col, 'Metric'] = value

    pb_capital_df.loc['Prime Broker Buying Power', 'Metric'] = 'Prime Broker Buying Power'
    pb_capital_df = pb_capital_df.reset_index(drop=True)

    return pb_capital_df


def fund_cash_details(cash_df):
    cash_df_pivot = cash_df.pivot_table(index=['As Of Date', 'Group'], columns='Fund Entity Code', values='Amount USD',
                                        aggfunc={'Amount USD': 'sum'})

    fund_capital_df = _get_fund_capital(cash_df_pivot, key=['Unfunded Commitments', 'Funded Commitments'])
    fund_capital_df = _add_total_line(fund_capital_df, 'Total Capital Commitment')
    fund_capital_df = overwrite_capital_commitments(fund_capital_df, OVERWRITE_CAPITAL_COMMITMENT_FUNDS)
    fund_capital_df = fund_capital_df.rename(columns={'Group': 'Metric'})

    prime_broker_capital = get_prime_broker_capital(cash_df)

    key_order = {'Net Cash on Hand': 0, 'Invoices/Payables': 1, 'Available Cash, net of Invoices/Payables': 2,
                 'Cash in Transit': 3, 'Outstanding Trades': 4, 'Trade Date Cash excluding FX': 5,
                 'Unfunded Liabilities': 6, 'Adjusted Trade Date Cash': 7}

    fund_other_cash_df = _get_fund_capital(cash_df_pivot, key=list(key_order.keys()))
    fund_other_cash_df = sort_df(fund_other_cash_df, 'Group', key_order)
    fund_other_cash_df = fund_other_cash_df.rename(columns={'Group': 'Metric'})

    return fund_capital_df, fund_other_cash_df, prime_broker_capital


def get_fund_cash_summary(cash_df):
    fund_commitment = fund_commitment_summary(keys=['Drawn Commitments', 'Undrawn Commitments'])
    fund_capital_df, fund_other_cash_df, prime_broker_capital = fund_cash_details(cash_df)
    cash_summary_df = pd.concat([fund_capital_df, fund_commitment, fund_other_cash_df], sort=False)
    cash_summary_df.loc['Total Capital Commitment', 'Metric'] = 'Total Capital Commitment'
    cash_summary_df.loc['Total Fund Commitment', 'Metric'] = 'Total Fund Commitment'
    cash_summary_df = cash_summary_df.reset_index(drop=True)
    return cash_summary_df, prime_broker_capital


def _additional_metrics_enhance_cash_summary(df):
    max_leverage_facility = get_max_leverage_facility()
    MAX_ASSET_VALUE_FACILITY_FACTOR = 0.65

    df['Maximum Leverage Facility Size'] = df.index.get_level_values('Fund Code').map(
        lambda x: max_leverage_facility[x] / 1000000)
    df['Maximum Asset Value of Facility'] = df['Maximum Leverage Facility Size'] / MAX_ASSET_VALUE_FACILITY_FACTOR
    df['Posted Equity Required to Max Facility'] = df['Maximum Asset Value of Facility'] - df[
        'Maximum Leverage Facility Size']
    df['Current Assets Value of Facility'] = df['Total Outstanding'] + df['Equity Posted']
    df['Net Eligible Asset Balance'] = df['Excess concentration haircuts'] + df['Total MV']
    df['Leverage Against Net Eligible Assets'] = df['Total Outstanding'] / df['Net Eligible Asset Balance']
    df['Capacity at Leverage Facility'] = df['Total Outstanding']
    df['Additional Equity Required'] = df['Posted Equity Required to Max Facility'] - df['Equity Posted']
    df['Remaining Asset Value to Max Facility'] = df['Capacity at Leverage Facility'] + df['Additional Equity Required']

    return df


def enhance_cash_summary(risk_df):
    df = leverage_analysis(risk_df)
    cols_rename = {'Total Outstanding': 'Drawn Amount of Leverage Facility', 'Equity Posted': 'Posted Equity',
                   'Total MV': 'Market Value of Assets in Facility',
                   'Excess concentration haircuts': 'Less: Excess Asset Concentration Haircuts'}
    df = df.set_index('Metric').T
    df = df[list(cols_rename.keys())]
    df = df.div(1000000)

    enhanced_cash_df = _additional_metrics_enhance_cash_summary(df)
    enhanced_cash_df = enhanced_cash_df.rename(columns=cols_rename)
    enhanced_cash_df = enhanced_cash_df.T
    enhanced_cash_df.columns = enhanced_cash_df.columns.droplevel('Fund Code')
    enhanced_cash_df = enhanced_cash_df.reset_index()
    return enhanced_cash_df


def _compute_dry_powder(df, prime_broker_capital):
    df = pd.concat([df, prime_broker_capital], sort=False)
    df = df.set_index('Metric')
    df = df.T
    df['Undrawn less Unfunded Commitments'] = df['Unfunded Commitments'].fillna(0) - df['Undrawn Commitments'].fillna(0)
    df['Adjusted Trade Date - Cash'] = df['Adjusted Trade Date Cash']
    df['Subline Drawn'] = float('NaN')
    df['Other Adj. to Dry Powder'] = float('NaN')
    df['Dry Powder'] = df['Undrawn less Unfunded Commitments'].fillna(0) + df['Adjusted Trade Date - Cash'].fillna(0) + \
                       df['Subline Drawn'].fillna(0) + df['Other Adj. to Dry Powder'].fillna(0) + df[
                           'Prime Broker Buying Power'].fillna(0)

    high_level_summary_cols = ['Undrawn less Unfunded Commitments', 'Adjusted Trade Date - Cash', 'Subline Drawn',
                               'Other Adj. to Dry Powder', 'Prime Broker Buying Power', 'Dry Powder']

    summary_df = df[high_level_summary_cols].T
    summary_df = summary_df.reset_index()

    return summary_df


def _get_liquid_cash_available(risk_df):
    fund_entity_code = ['ACCORD5', 'ACRDPLUS', 'ADSBDC', 'AEDL', 'AMEDL', 'ANCF', 'ANOVER2', 'AOP', 'ATLAS', 'CENTRE',
                        'CF', 'CHIRON', 'CSTRAT', 'DEFINED', 'EXCELSIO', 'GIACF', 'LINCFI', 'MOULTRIE', 'OASISFND',
                        'SPNTACT', 'TOWER', 'TRF', 'UNIONST', 'VGPDF']

    security_sub_type = ['Corporate Bond', 'Common Stock', 'Credit Default Swap', 'Equity Option', 'Equity Unit',
                         'Forward Currency Contract', 'Convertible Bond']

    filter_condition_1 = risk_df['Fund Entity Code'].isin(fund_entity_code)
    filter_condition_2 = risk_df['Security Sub Type'].isin(security_sub_type)

    df = risk_df.loc[filter_condition_1 & filter_condition_2 & (risk_df['Security'] != 'Fund Cash')]

    df = df.loc[df['Mean Days To Liquidate'] <= 5.0]
    df_summary = df[['Fund Entity Code', MV_COL]]
    df_summary = df_summary.groupby(['Fund Entity Code']).sum()
    df_summary = df_summary.div(1000000)
    df_summary = df_summary.T
    df_summary = df_summary.reset_index()

    df_detail = df.pivot_table(index=['Security Sub Type', 'Issuer Ult'], columns='Fund Entity Code', values='MV USD',
                               aggfunc={'MV USD': 'sum'})
    df_detail = df_detail.div(1000000)
    df_detail = df_detail.reset_index()

    return df_summary, df_detail


def get_cash_summary():
    cash_df = pd.read_excel(cash_report_file)
    risk_df = read_risk_data()

    cash_summary_df, prime_broker_capital = get_fund_cash_summary(cash_df)
    enhanced_cash_df = enhance_cash_summary(risk_df)

    detailed_df = pd.concat([cash_summary_df,enhanced_cash_df], sort=False)

    summary_df = _compute_dry_powder(detailed_df, prime_broker_capital)

    liquid_capital, liquid_capital_detail = _get_liquid_cash_available(risk_df)

    result = {'Cash Summary': summary_df, 'Details': detailed_df, 'Liquid Cash in <= 5 days': liquid_capital,
              'Liquid Cash Details': liquid_capital_detail}

    return result


if __name__ == '__main__':
    get_cash_summary()
