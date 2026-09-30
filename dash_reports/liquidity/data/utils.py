import os
import pandas as pd

from common.utils.numutil import xlstring_to_float

path = r'\\COMMONSHARE\Common$\Hedge Fund\MicroStrategy Exports'

cash_report_file = os.path.join(path, 'Liquidity-Cash-Report-ByFund.xlsx')
capital_activity_file = os.path.join(path, 'Liquidity-Full-Capital-Activity-ByFund.xlsx')
fund_commits_file = os.path.join(path, 'Liquidity-Fund-Commits-ByFund.xlsx')
commits_data_file = os.path.join(path, 'Liquidity Dash Uploads', 'CommitsData.xlsx')
fund_info_file = os.path.join(path, 'Liquidity Dash Uploads', 'FundInfo.xlsx')
fx_forward_data_file = os.path.join(path, 'Liquidity Dash Uploads', 'fx_forward_data.xlsx')
pnl_file = os.path.join(path, 'PnL_Daily_lite_Liquidity_Report.csv')
risk_file = os.path.join(path, 'Risk_Daily_lite_Liquidity_Report.csv')

MV_COL = 'MV USD'


def _add_total_line(df, name):
    df_sum = df.sum(numeric_only=True)
    df_sum.name = name
    df = pd.concat([df, df_sum.to_frame().T], sort=False)
    return df


def sort_df(df, sort_col, sort_order):
    df['sort_order'] = df[sort_col].apply(lambda x: sort_order[x] if x in sort_order.keys() else pd.NA)
    df = df.sort_values(by=['sort_order'])
    df = df.drop(['sort_order'], axis=1)
    return df


def read_risk_data():
    risk_cols = ['MV Func', MV_COL, 'Quantity']
    risk_cols_converter = {x: xlstring_to_float for x in risk_cols}
    df = pd.read_csv(risk_file, converters=risk_cols_converter, thousands=',')

    return df
