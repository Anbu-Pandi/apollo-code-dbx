import pandas as pd

from dash_reports.liquidity.data.utils import _add_total_line, sort_df
from dash_reports.liquidity.data.fund_commitment import fund_commitment_df


def _get_fund_capital(df, key):
    idx = pd.IndexSlice
    df = df.fillna(0)
    df = df.loc[idx[:, key], :]
    result_df = df.groupby('Group').sum()
    result_df = result_df.div(1000000)
    return result_df.reset_index()


def fund_cash_details(cash_df):
    cash_df_pivot = cash_df.pivot_table(index=['As Of Date', 'Group'], columns='Fund Entity Code', values='Amount USD',
                                        aggfunc={'Amount USD': 'sum'})
    fund_capital_df = _get_fund_capital(cash_df_pivot, key=['Unfunded Commitments', 'Funded Commitments'])

    fund_capital_df = _add_total_line(fund_capital_df, 'Total Capital Commitment')
    fund_capital_df = fund_capital_df.rename(columns={'Group': 'Metric'})

    key_order = {'Net Cash on Hand': 0, 'Invoices/Payables': 1, 'Available Cash, net of Invoices/Payables': 2,
                 'Cash in Transit': 3, 'Outstanding Trades': 4, 'Trade Date Cash excluding FX': 5,
                 'Unfunded Liabilities': 6, 'Adjusted Trade Date Cash': 7}

    fund_other_cash_df = _get_fund_capital(cash_df_pivot, key=list(key_order.keys()))
    fund_other_cash_df = sort_df(fund_other_cash_df, 'Group', key_order)
    fund_other_cash_df = fund_other_cash_df.rename(columns={'Group': 'Metric'})

    return fund_capital_df, fund_other_cash_df


def get_fund_cash_summary(cash_df, commits_data_df):
    fund_commitment = fund_commitment_df(commits_data_df)
    fund_capital_df, fund_other_cash_df = fund_cash_details(cash_df)
    cash_summary_df = pd.concat([fund_capital_df, fund_commitment, fund_other_cash_df], sort=False)
    cash_summary_df.loc['Total Capital Commitment', 'Metric'] = 'Total Capital Commitment'
    cash_summary_df.loc['Total Fund Commitment', 'Metric'] = 'Total Fund Commitment'
    cash_summary_df = cash_summary_df.reset_index(drop=True)
    return cash_summary_df, fund_commitment, fund_capital_df, fund_other_cash_df
