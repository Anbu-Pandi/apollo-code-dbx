import pandas as pd
from dash_reports.liquidity.data.utils import _add_total_line, commits_data_file

COMMITMENT_TYPE_KEYS = ['Drawn Commitments', 'Undrawn Commitments', 'Total Fund Commitments']


def _get_commit_data_pivot(df):
    df = df.fillna(0)
    df = df.loc[df['apo_fund_holding'] != 'remove']
    df = df.pivot_table(columns=['fund_entity_code', 'Field'], index=['asof_date', 'apo_fund_holding'], values='Value',
                        aggfunc={'Value': 'sum'})
    return df


def _get_fund_commit_value(df, key):
    df = df.groupby(['asof_date']).sum()
    idx = pd.IndexSlice
    result_df = df.loc[:, idx[:, key]]
    result_df = result_df.stack('Field')
    return result_df.reset_index()


def read_commitment_data():
    commits_data_df = pd.read_excel(commits_data_file)
    return commits_data_df


def fund_commitment_detail():
    commits_data_df = read_commitment_data()
    leverage_df = _get_commit_data_pivot(commits_data_df)

    result_dict = {}
    idx = pd.IndexSlice

    for key in COMMITMENT_TYPE_KEYS:
        temp_df = leverage_df.loc[:, idx[:, key]]
        temp_df.columns = temp_df.columns.droplevel('Field')
        temp_df.index = temp_df.index.droplevel('asof_date')
        temp_df = _add_total_line(temp_df, 'Total')
        temp_df.index.name = 'APO Funds'
        result_dict[key] = temp_df

    return result_dict


def fund_commitment_summary(keys=['Drawn Commitments', 'Undrawn Commitments']):
    commits_data_df = read_commitment_data()
    commited_data_pivot = _get_commit_data_pivot(commits_data_df)
    fund_commitment = _get_fund_commit_value(commited_data_pivot, keys)
    fund_commitment = fund_commitment.drop(columns='asof_date', axis=1)
    fund_commitment = fund_commitment.rename(columns={'Field': 'Metric'})
    fund_commitment = _add_total_line(fund_commitment, 'Total Fund Commitment')
    return fund_commitment
