import pandas as pd

from common.date_utilities import DateTimeUtilities as dtu
from common.utils.logutil import *
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.data.config import *


def generate_dollar_pnl_data():
    raw_df = datastore.get_pnl()

    """Pre-processing of data"""
    raw_df = raw_df.loc[raw_df['Fund Entity Code'].isin(FUND_CODE_LIST)]

    """Beta Pnl DataFrame, broken into dollar df and returns df"""

    beta_pnl_df = raw_df.loc[:, PNL_ATTRIB_RULES['beta_pnl_cols']]
    beta_pnl_df = beta_pnl_df.loc[beta_pnl_df.loc[:, 'Reference Index'].isin(REFERENCE_INDEX.keys())]
    beta_pnl_df.loc[:, 'Reference Index'] = beta_pnl_df.loc[:, 'Reference Index'].apply(
        lambda x: REFERENCE_INDEX[x] if x in REFERENCE_INDEX.keys() else x)
    beta_pnl_df = beta_pnl_df.groupby(['Fund Entity Code', 'FO Strategy Broad Category', 'Reference Index']).sum()
    beta_pnl_df = beta_pnl_df.unstack('Reference Index')

    beta_dollar_df = beta_pnl_df['Beta PnL']
    beta_dollar_df.columns = pd.MultiIndex.from_product([['Beta'], beta_dollar_df.columns])
    beta_dollar_df['Total Beta'] = beta_dollar_df.sum(axis=1, skipna=True)

    beta_return_df = beta_pnl_df['Beta Return']
    beta_return_df.columns = pd.MultiIndex.from_product([['Beta'], beta_return_df.columns])
    beta_return_df['Total Beta'] = beta_return_df.sum(axis=1, skipna=True)

    """Alpha Pnl DataFrame, broken into Dollar df and return df"""

    alpha_pnl_df = raw_df.loc[:, PNL_ATTRIB_RULES['alpha_pnl_cols']]
    alpha_pnl_df = alpha_pnl_df.groupby(['Fund Entity Code', 'FO Strategy Broad Category']).sum()

    alpha_dollar_df = alpha_pnl_df.loc[:, ['Alpha PnL', 'Rate PnL']]
    alpha_dollar_df = alpha_dollar_df.rename(columns={'Alpha PnL': 'CR & EQ', 'Rate PnL': 'IR'})
    alpha_dollar_df.columns = pd.MultiIndex.from_product([['Alpha'], alpha_dollar_df.columns])
    alpha_dollar_df['Total Alpha'] = alpha_dollar_df.sum(axis=1, skipna=True)

    alpha_return_df = alpha_pnl_df.loc[:, ['Alpha Return', 'Rate Return']]
    alpha_return_df = alpha_return_df.rename(columns={'Alpha Return': 'CR & EQ', 'Rate Return': 'IR'})
    alpha_return_df.columns = pd.MultiIndex.from_product([['Alpha'], alpha_return_df.columns])
    alpha_return_df['Total Alpha'] = alpha_return_df.sum(axis=1, skipna=True)

    """Total Pnl DataFrame"""

    total_pnl_df = raw_df.loc[:, PNL_ATTRIB_RULES['total_pnl_cols']]
    total_pnl_df = total_pnl_df.groupby(['Fund Entity Code', 'FO Strategy Broad Category']).sum()

    total_dollar_df = total_pnl_df.loc[:, 'P&L DTD Total x FX USD']
    total_dollar_df.name = 'Total'
    total_dollar_df = total_dollar_df.to_frame()
    total_dollar_df.columns = pd.MultiIndex.from_product([['Total'], ['']])

    total_return_df = total_pnl_df.loc[:, 'Total Return']
    total_return_df.name = 'Total'
    total_return_df = total_return_df.to_frame()
    total_return_df.columns = pd.MultiIndex.from_product([['Total'], ['']])

    """Combine dollar / returns components to create dollar / return attrib pnl"""
    dollar_attrib_df = pd.concat([total_dollar_df, beta_dollar_df, alpha_dollar_df], axis=1)
    dollar_attrib_df = dollar_attrib_df.div(1000)

    return_attrib_df = pd.concat([total_return_df, beta_return_df, alpha_return_df], axis=1)
    return_attrib_df = return_attrib_df.mul(100)

    dollar_pnl_df = pd.DataFrame()
    return_pnl_df = pd.DataFrame()

    for fund_name in FUND_CODE_LIST:
        temp_dollar_df = dollar_attrib_df[dollar_attrib_df.index.isin([fund_name], level='Fund Entity Code')]
        temp_dollar_total = temp_dollar_df.sum()
        temp_dollar_total.name = (fund_name, 'Total')
        temp_dollar_total = temp_dollar_total.to_frame().T
        temp_dollar_total.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']
        temp_dollar_total = pd.concat([temp_dollar_total, temp_dollar_df], sort=False)
        dollar_pnl_df = pd.concat([dollar_pnl_df, temp_dollar_total], sort=False)

        temp_return_df = return_attrib_df[return_attrib_df.index.isin([fund_name], level='Fund Entity Code')]
        temp_return_total = temp_return_df.sum()
        temp_return_total.name = (fund_name, 'Total')
        temp_return_total = temp_return_total.to_frame().T
        temp_return_total.index.names = ['Fund Entity Code', 'FO Strategy Broad Category']
        temp_return_total = pd.concat([temp_return_total, temp_return_df], sort=False)
        return_pnl_df = pd.concat([return_pnl_df, temp_return_total], sort=False)

        del temp_dollar_df, temp_dollar_total, temp_return_df, temp_return_total

    """Formatting for Dash Report"""
    dollar_pnl_df = dollar_pnl_df.reset_index()
    return_pnl_df = return_pnl_df.reset_index()

    if SAVE_OUTPUT:
        if SAVE_OUTPUT:
            writer = pd.ExcelWriter(os.path.join(OUTPUT_PATH, 'pnl_attribution.xlsx'), engine='xlsxwriter')
            dollar_pnl_df.to_excel(writer, sheet_name='dollar_pnl_attrib')
            return_pnl_df.to_excel(writer, sheet_name='return_pnl_attrib')
            writer.save()

    return dollar_pnl_df, return_pnl_df


def generate_top_beta_contrib():
    raw_df = datastore.get_pnl()

    """Pre-processing of data"""
    raw_df = raw_df.loc[raw_df['Fund Entity Code'].isin(FUND_CODE_LIST)]
    dates = list(set(raw_df['As Of Date']))
    dates.sort(reverse=True)

    for date in dates:
        if date.isoweekday() not in [6, 7]:
            date = date
            break

    us_holidays = dtu.getAllTradingHolidays()

    if date.date() in us_holidays:
        date = pd.to_datetime(dtu.getPrevBusiDate(date, us_holidays))

    logging.info("Filtering data for date - {}".format(str(date)))
    raw_df_filtered = raw_df.loc[raw_df['As Of Date'] == date]

    raw_df_pivot = pd.pivot_table(raw_df_filtered, index=['Fund Entity Code', 'Issuer Ult'],
                                  values=['MV Equivalent USD', 'Beta Contribution', 'Beta', 'Fund Wght'],
                                  aggfunc={'MV Equivalent USD': np.sum, 'Beta Contribution': np.sum,
                                           'Fund Wght': np.sum, 'Beta': np.mean})
    top_beta_contib_df = pd.DataFrame()
    bottom_beta_contrib_df = pd.DataFrame()

    for fund_name in FUND_CODE_LIST:
        temp_df = raw_df_pivot.loc[raw_df_pivot.index.isin([fund_name], level='Fund Entity Code'), :]

        temp_top_beta_df = temp_df.sort_values(by='Beta Contribution', ascending=False).iloc[:15, :]
        temp_bottom_beta_df = temp_df.sort_values(by='Beta Contribution', ascending=True).iloc[:15, :]

        temp_top_beta_df.index.names = ['Fund Entity Code', 'Issuer Ult']
        temp_bottom_beta_df.index.names = ['Fund Entity Code', 'Issuer Ult']

        top_beta_contib_df = pd.concat([top_beta_contib_df, temp_top_beta_df], sort=False)
        bottom_beta_contrib_df = pd.concat([bottom_beta_contrib_df, temp_bottom_beta_df], sort=False)

        del temp_df, temp_top_beta_df, temp_bottom_beta_df

    """Formatting for Dash"""
    top_beta_contib_df['MV Equivalent USD'] = top_beta_contib_df['MV Equivalent USD'].round(0)
    top_beta_contib_df.loc[:, ['Beta Contribution', 'Fund Wght']] = top_beta_contib_df.loc[:,
                                                                    ['Beta Contribution', 'Fund Wght']].mul(100)

    bottom_beta_contrib_df['MV Equivalent USD'] = bottom_beta_contrib_df['MV Equivalent USD'].round(0)
    bottom_beta_contrib_df.loc[:, ['Beta Contribution', 'Fund Wght']] = bottom_beta_contrib_df.loc[:,
                                                                        ['Beta Contribution', 'Fund Wght']].mul(100)

    top_beta_contib_df = top_beta_contib_df.reset_index()
    bottom_beta_contrib_df = bottom_beta_contrib_df.reset_index()

    if SAVE_OUTPUT:
        writer = pd.ExcelWriter(os.path.join(OUTPUT_PATH, 'beta_contributors.xlsx'), engine='xlsxwriter')
        top_beta_contib_df.to_excel(writer, sheet_name='top_beta_contrib')
        bottom_beta_contrib_df.to_excel(writer, sheet_name='bottom_beta_contrib')
        writer.save()

    # cstrat_top_beta_contrib = top_beta_contib_df.loc[idx['CSTRAT', :], :]
    # cstrat_bottom_beta_contrib = bottom_beta_contrib_df.loc[idx['CSTRAT', :], :]

    return top_beta_contib_df, bottom_beta_contrib_df


if __name__ == '__main__':
    generate_top_beta_contrib()
