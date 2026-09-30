import pandas as pd

from common.utils.logutil import *
from dash_reports.report_common.data.config import *
from dash_reports.report_common.data.data_model import datastore


def generate_rolling_pnl_data():
    raw_df = datastore.get_roll_issuer()

    """Pre-processing of data"""
    raw_df = raw_df.loc[raw_df['Fund Entity Code'].isin(FUND_CODE_LIST)]

    raw_df_pivot = pd.pivot_table(raw_df, values=['1 Week Rolling PnL', 'MTD PnL', 'YTD PnL'],
                                  index=['Fund Entity Code', 'Issuer Ult'])

    wtd_df = pd.DataFrame()
    mtd_df = pd.DataFrame()
    ytd_df = pd.DataFrame()

    for fund_name in FUND_CODE_LIST:
        temp_df = raw_df_pivot[raw_df_pivot.index.get_level_values('Fund Entity Code') == fund_name]

        temp_wtd_df = temp_df.sort_values(by='1 Week Rolling PnL', ascending=True).iloc[:10, :]
        temp_mtd_df = temp_df.sort_values(by='MTD PnL', ascending=True).iloc[:10, :]
        temp_ytd_df = temp_df.sort_values(by='YTD PnL', ascending=True).iloc[:10, :]

        wtd_df = pd.concat([wtd_df, temp_wtd_df], sort=False)
        mtd_df = pd.concat([mtd_df, temp_mtd_df], sort=False)
        ytd_df = pd.concat([ytd_df, temp_ytd_df], sort=False)

        del temp_df, temp_wtd_df, temp_mtd_df, temp_ytd_df

    """Formatting for dash"""
    wtd_df = wtd_df.div(1000).round(0)
    mtd_df = mtd_df.div(1000).round(0)
    ytd_df = ytd_df.div(1000).round(0)

    wtd_df = wtd_df.reset_index()
    mtd_df = mtd_df.reset_index()
    ytd_df = ytd_df.reset_index()

    if SAVE_OUTPUT:
        writer = pd.ExcelWriter(os.path.join(OUTPUT_PATH, 'rolling_pnl.xlsx'), engine='xlsxwriter')
        wtd_df.to_excel(writer, sheet_name='wtd')
        mtd_df.to_excel(writer, sheet_name='mtd')
        ytd_df.to_excel(writer, sheet_name='ytd')
        writer.save()

    return wtd_df, mtd_df, ytd_df


if __name__ == '__main__':
    datastore.init()
    a = generate_rolling_pnl_data()
