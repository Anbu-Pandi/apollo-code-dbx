import pandas as pd
from dash_reports.liquidity.data.utils import MV_COL, sort_df, read_risk_data

LEVERAGED_FUND_CODE = ['CLFND', 'MLFND', 'LLFND']


def _get_oustanding_cash(df):
    outstanding_df = df.loc[df['Security'].str.endswith('FUN'), ['Fund Entity Code', 'Fund Code', MV_COL]].groupby(
        ['Fund Entity Code', 'Fund Code']).sum()
    outstanding_df = outstanding_df.rename(columns={MV_COL: 'Total Outstanding'})
    return outstanding_df


def _get_ex_financing_info(df):
    ex_financing_info = df.loc[:, ['Fund Entity Code', 'Fund Code', MV_COL, 'Quantity']].groupby(
        ['Fund Entity Code', 'Fund Code']).sum()
    ex_financing_info = ex_financing_info.rename(columns={MV_COL: 'Total MV', 'Quantity': 'Total Qty'})
    return ex_financing_info


def _total_cost(df):
    df = df.loc[:, ['Fund Entity Code', 'Fund Code', 'Quantity', 'Cost Local']]
    cost_df = \
        df.assign(Cost=df['Quantity'] * df['Cost Local']).groupby(['Fund Entity Code', 'Fund Code']).sum()[
            'Cost'].to_frame()
    return cost_df


def _get_cash_info(df):
    df = df.loc[df['Security'] == 'Fund Cash', ['Fund Entity Code', 'Fund Code', MV_COL]].groupby(
        ['Fund Entity Code', 'Fund Code']).sum()
    df = df.rename(columns={MV_COL: 'Cash'})
    return df


def _additional_leverage_metric(df):
    df['Net Collateral Balance'] = df['Total MV'] + df['Excess concentration haircuts']
    df['Borrowing Base'] = df['Cash'] + (df['Net Collateral Balance'] * df['Adv Rate (%)'])
    df['Current Mark (%)'] = df['Total MV'] / df['Total Qty']
    df['Excess'] = df['Borrowing Base'] - df['Total Outstanding']
    df['Spendable'] = df.apply(lambda row: min(row['Cash'], (row['Excess'] / (1 - row['Adv Rate (%)']))), axis=1)
    df['Margin Call (%)'] = (df['Total Outstanding'] - df['Cash']) / (df['Adv Rate (%)'] * df['Total Qty'])
    df['Cushion (%)'] = df['Current Mark (%)'] - df['Margin Call (%)']
    df['Equity Posted'] = df['Cost'] + df['Cash'] - df['Total Outstanding']
    df[['Adv Rate (%)', 'Current Mark (%)', 'Margin Call (%)', 'Cushion (%)']] = df[['Adv Rate (%)', 'Current Mark (%)',
                                                                                     'Margin Call (%)',
                                                                                     'Cushion (%)']] * 100
    df = df.T
    df.index.name = 'Metric'
    df = df.reset_index()

    return df


def get_max_leverage_facility():
    risk_df = read_risk_data()
    filter_condition_1 = risk_df['Fund Code'].isin(LEVERAGED_FUND_CODE)
    filter_condition_2 = risk_df['Security Sub Type'].isin(['Financing', 'Financing (RC)'])

    df = risk_df.loc[(filter_condition_1) & (filter_condition_2)]
    df = df.loc[
        (df['Security'].str.endswith('FUN')) | (df['Security'].str.endswith('UNF')), ['Fund Code', MV_COL]].groupby(
        ['Fund Code']).sum()
    df = df.reset_index()
    max_facility = dict(zip(df['Fund Code'], df[MV_COL]))
    return max_facility


def leverage_analysis(risk_df=None):
    if risk_df is None:
        risk_df = read_risk_data()

    sort_order = {'Equity Posted': 0, 'Borrowing Base': 1, 'Total Outstanding': 2, 'Cash': 3, 'Total MV': 4,
                  'Total Qty': 5, 'Adv Rate (%)': 6, 'Current Mark (%)': 7, 'Cost': 8, 'Excess': 9, 'Spendable': 10,
                  'Margin Call (%)': 11, 'Cushion (%)': 12, 'Excess concentration haircuts': 13,
                  'Net Collateral Balance': 14}

    ADV_RATE = {'CLFND': 0.60, 'MLFND': 0.60, 'LLFND': 0.60}
    EXCESS_CONCEN_HAIRCUTS = {'CLFND': -11591845, 'MLFND': -11818361, 'LLFND': -3249648}

    filter_condition_1 = risk_df['Fund Code'].isin(LEVERAGED_FUND_CODE)
    filter_condition_2 = risk_df['Security Sub Type'].isin(['Financing', 'Financing (RC)'])
    filter_condition_3 = risk_df['Security Sub Type'].str.contains('Revolving')

    financing_df = risk_df.loc[(filter_condition_1) & (filter_condition_2)]
    non_financing_revolving_df = risk_df.loc[(filter_condition_1) & (~filter_condition_2) & (~filter_condition_3)]

    outstanding_df = _get_oustanding_cash(financing_df)
    ex_financing_info = _get_ex_financing_info(non_financing_revolving_df)
    cost_df = _total_cost(non_financing_revolving_df)
    fund_cash_df = _get_cash_info(non_financing_revolving_df)

    leverage_df = pd.concat([outstanding_df, ex_financing_info, cost_df, fund_cash_df], axis=1)
    leverage_df = leverage_df.div(1000000)
    leverage_df['Adv Rate (%)'] = leverage_df.index.get_level_values('Fund Code').map(lambda x: ADV_RATE[x])
    leverage_df['Excess concentration haircuts'] = leverage_df.index.get_level_values('Fund Code').map(
        lambda x: EXCESS_CONCEN_HAIRCUTS[x])

    leverage_df = _additional_leverage_metric(leverage_df)
    leverage_df = sort_df(leverage_df, 'Metric', sort_order)

    return leverage_df


if __name__ == '__main__':
    get_max_leverage_facility()
