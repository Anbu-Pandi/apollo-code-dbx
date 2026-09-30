import os
from datetime import datetime

from common.db_utilities import *
from data_access.beta_attribution.data_index_attrib import index_data
from dash_reports.report_common.data.config import *


def generate_snp_attribution(start_date, end_date):
    obj = index_data(start_date, end_date)
    industry_return, rating_return = obj.get_snp_mtd_data()

    """Returns DF"""
    return_df = pd.pivot_table(rating_return, index=['LCD_BROAD_INDUSTRY'], columns=['RATING'],
                               values=['RET_IN_BUCKET'], aggfunc={'RET_IN_BUCKET': np.sum})
    return_df = return_df.mul(100)
    return_df = return_df.rename(columns={'RET_IN_BUCKET': 'Returns'}, level=0)

    """Industry wise % Contribution & % Wght"""
    industry_contrib_df = industry_return[['LCD_BROAD_INDUSTRY', 'BUCKET_WEIGHT', 'MTD_RET_CONTR']].groupby(
        'LCD_BROAD_INDUSTRY').sum().mul(100)

    industry_contrib_df.insert(0, 'Contr / Wght', (
            industry_contrib_df['MTD_RET_CONTR'] / (industry_contrib_df['BUCKET_WEIGHT'].mul(0.01))))

    industry_contrib_df = industry_contrib_df.rename(columns={'MTD_RET_CONTR': '% Contr', 'BUCKET_WEIGHT': '% Wght'})

    mtd_return = industry_contrib_df['% Contr'].sum()
    # snp_mtd_return_df = pd.DataFrame(mtd_return, index=['Return'], columns=['MTD'])

    industry_contrib_df.columns = pd.MultiIndex.from_product([[''], industry_contrib_df.columns])

    """Rating wise % Contribution & % Wght"""
    rating_contrib_df = industry_return[['RATING', 'BUCKET_WEIGHT', 'MTD_RET_CONTR']].groupby(
        'RATING').sum().mul(100)

    rating_contrib_df.insert(0, 'Contr / Wght', (
            rating_contrib_df['MTD_RET_CONTR'] / (rating_contrib_df['BUCKET_WEIGHT'].mul(0.01))))

    rating_contrib_df = rating_contrib_df.rename(columns={'MTD_RET_CONTR': '% Contr', 'BUCKET_WEIGHT': '% Wght'})
    rating_contrib_df = rating_contrib_df.T
    rating_contrib_df.columns = pd.MultiIndex.from_product([['Returns'], rating_contrib_df.columns])

    """Weights DF"""
    weight_df = pd.pivot_table(industry_return, index=['LCD_BROAD_INDUSTRY'], columns=['RATING'],
                               values=['BUCKET_WEIGHT'], aggfunc={'BUCKET_WEIGHT': np.sum})
    weight_df = weight_df.mul(100)
    weight_df = weight_df.rename(columns={'BUCKET_WEIGHT': 'Weights'}, level=0)

    """Loan Count DF"""
    loan_count_df = pd.pivot_table(industry_return, index=['LCD_BROAD_INDUSTRY'], columns=['RATING'],
                                   values=['LOAN_COUNT'], aggfunc={'LOAN_COUNT': np.sum})
    loan_count_df = loan_count_df.rename(columns={'LOAN_COUNT': 'Loan Counts'}, level=0)

    """Combining above parts into one dataframe"""

    loan_attrib_df = pd.concat([return_df, industry_contrib_df, weight_df, loan_count_df], sort=False, axis=1)
    loan_attrib_df = pd.concat([loan_attrib_df, rating_contrib_df], sort=False, axis=0)
    loan_attrib_df.columns.names = return_df.columns.names

    """Formatting for Dash Report"""
    loan_attrib_df = loan_attrib_df.reset_index()
    mtd_return = mtd_return.round(2)

    if SAVE_OUTPUT:
        loan_attrib_df.to_csv(os.path.join(OUTPUT_PATH, 'SnP_attrib.csv'))

    return loan_attrib_df, mtd_return


def generate_index_beta_corr(fund_name, start_date, end_date):
    obj = index_data(start_date, end_date)
    corr_df, beta_df = obj.get_beta_corr_data(fund_name)

    """Beta by Industry and Rating"""
    beta_metric_df = pd.pivot_table(beta_df, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                    values=['BETA'], aggfunc={'BETA': np.sum})

    """Correlation by Industry and Rating"""
    corr_metric_df = pd.pivot_table(corr_df, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                    values=['CORR'], aggfunc={'CORR': np.sum})

    """Factor Weight by Industry"""
    industry_wght_df = pd.pivot_table(corr_df, index=['Industry Lvl 3 Desc'], values=['FactorWeight'],
                                      aggfunc={'FactorWeight': np.sum})

    industry_wght_df = industry_wght_df.rename(columns={'FactorWeight': '% Wght'})
    industry_wght_df.columns = pd.MultiIndex.from_product([[''], industry_wght_df.columns])

    """Factor Weight by Rating"""
    rating_wght_df = pd.pivot_table(corr_df, index=['Rating_L1'], values=['FactorWeight'],
                                    aggfunc={'FactorWeight': np.sum})

    rating_wght_df = rating_wght_df.rename(columns={'FactorWeight': '% Wght'})
    rating_wght_df = rating_wght_df.T

    rating_wght_df.columns = pd.MultiIndex.from_product([['BETA'], rating_wght_df.columns])

    """Combining all above """
    beta_corr_df = pd.concat([beta_metric_df, corr_metric_df, industry_wght_df], sort=False, axis=1)
    beta_corr_df = pd.concat([beta_corr_df, rating_wght_df], sort=False, axis=0)
    beta_corr_df.columns.names = rating_wght_df.columns.names

    """Formatting for Dash Report"""
    beta_corr_df = beta_corr_df.reset_index()

    if SAVE_OUTPUT:
        beta_corr_df.to_csv(os.path.join(OUTPUT_PATH, '{}_beta_corr.csv'.format(fund_name)))

    return beta_corr_df


def generate_index_alpha_excess(fund_name, start_date, end_date):
    min_bond_count = 5

    obj = index_data(start_date, end_date)

    corr_df, beta_df = obj.get_beta_corr_data(fund_name)
    excess_return_df, CDX_HY_df = obj.get_excess_mtd_data(fund_name)
    excess_return_df = excess_return_df.loc[excess_return_df['BOND_COUNT'] > min_bond_count]

    """MTD Alpha by Industry and Rating"""
    mtd_alpha_metric_df = pd.pivot_table(excess_return_df, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                         values=['Alpha'], aggfunc={'Alpha': np.sum})

    """MTD Excess Returns by Industry and Rating"""
    excess_corr_metric_df = pd.pivot_table(excess_return_df, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                           values=['MTD_ExcessReturn_Factor'],
                                           aggfunc={'MTD_ExcessReturn_Factor': np.sum})

    """Factor Weight by Industry"""
    industry_wght_df = pd.pivot_table(corr_df, index=['Industry Lvl 3 Desc'], values=['FactorWeight'],
                                      aggfunc={'FactorWeight': np.sum})

    industry_wght_df = industry_wght_df.rename(columns={'FactorWeight': '% Wght'})
    industry_wght_df.columns = pd.MultiIndex.from_product([[''], industry_wght_df.columns])

    """Combining all above """
    alpha_excess_ret_df = pd.concat([mtd_alpha_metric_df, excess_corr_metric_df, industry_wght_df], sort=False, axis=1)
    alpha_excess_ret_df.columns.names = mtd_alpha_metric_df.columns.names

    """CDXHY Return Data"""
    CDX_HY_df = CDX_HY_df.reset_index()

    """Formatting for Dash Report"""
    alpha_excess_ret_df = alpha_excess_ret_df.reset_index()

    if SAVE_OUTPUT:
        alpha_excess_ret_df.to_csv(os.path.join(OUTPUT_PATH, '{}_alpha_returns.csv'.format(fund_name)))

    return alpha_excess_ret_df, CDX_HY_df


def get_top_bottom_contributors(mtd_rtrns_ticker):
    col_rename = {'MTD_Return_Contr': 'MTD % Return', 'MTD_Return_bondPTS_Factor': 'MTD Bond Pts Factor'}
    top_contrib = mtd_rtrns_ticker.sort_values(by=['MTD_Return_Contr'], ascending=False).iloc[0:18].loc[:,
                  ['Ticker', 'MTD_Return_Contr']].reset_index(drop=True)

    top_contrib = top_contrib.rename(columns=col_rename)
    top_contrib.columns = pd.MultiIndex.from_product([['Top % Return Contributors'], top_contrib.columns])

    bottom_contrib = mtd_rtrns_ticker.sort_values(by=['MTD_Return_Contr'], ascending=True).iloc[0:18].loc[:,
                     ['Ticker', 'MTD_Return_Contr']].reset_index(drop=True)

    bottom_contrib = bottom_contrib.rename(columns=col_rename)
    bottom_contrib.columns = pd.MultiIndex.from_product([['Bottom % Return Contributors'], bottom_contrib.columns])

    bottom_mtd = mtd_rtrns_ticker.sort_values(by=['MTD_Return_bondPTS_Factor'], ascending=True).iloc[0:18].loc[:,
                 ['Ticker', 'MTD_Return_bondPTS_Factor']].reset_index(drop=True)

    bottom_mtd = bottom_mtd.rename(columns=col_rename)
    bottom_mtd.columns = pd.MultiIndex.from_product([['Bottom Return (Bond Pts)'], bottom_mtd.columns])

    top_mtd = mtd_rtrns_ticker.sort_values(by=['MTD_Return_bondPTS_Factor'], ascending=False).iloc[0:18].loc[:,
              ['Ticker', 'MTD_Return_bondPTS_Factor']].reset_index(drop=True)

    top_mtd = top_mtd.rename(columns=col_rename)
    top_mtd.columns = pd.MultiIndex.from_product([['Top Return (Bond Pts)'], top_mtd.columns])

    top_bottom_contrib = pd.concat([top_contrib, bottom_contrib, top_mtd, bottom_mtd], axis=1, sort=False)

    return top_bottom_contrib


def generate_bnchmrk_attribution(fund_name, start_date, end_date):
    obj = index_data(start_date, end_date)
    mtd_rtrns, mtd_rtrns_ticker = obj.get_mtd_data(fund_name)

    """Returns DF"""
    return_df = pd.pivot_table(mtd_rtrns, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                               values=['MTD_Return_Factor'], aggfunc={'MTD_Return_Factor': np.sum})
    return_df = return_df.mul(100)
    return_df = return_df.rename(columns={'MTD_Return_Factor': 'Returns'}, level=0)

    """Industry wise % Contribution & % Wght"""
    industry_contrib_df = mtd_rtrns[['Industry Lvl 3 Desc', 'FactorWeight', 'MTD_Return_Contr']].groupby(
        'Industry Lvl 3 Desc').sum()

    industry_contrib_df.insert(0, 'Contr / Wght', (
            industry_contrib_df['MTD_Return_Contr'] / (industry_contrib_df['FactorWeight'].mul(0.01))))

    industry_contrib_df = industry_contrib_df.rename(columns={'MTD_Return_Contr': '% Contr', 'FactorWeight': '% Wght'})

    mtd_return = industry_contrib_df['% Contr'].sum()
    # index_mtd_return_df = pd.DataFrame(mtd_return, index=['Return'], columns=['MTD'])

    industry_contrib_df.columns = pd.MultiIndex.from_product([[''], industry_contrib_df.columns])

    """Rating wise % Contribution & % Wght"""
    rating_contrib_df = mtd_rtrns[['Rating_L1', 'FactorWeight', 'MTD_Return_Contr']].groupby(
        'Rating_L1').sum()

    rating_contrib_df.insert(0, 'Contr / Wght', (
            rating_contrib_df['MTD_Return_Contr'] / (rating_contrib_df['FactorWeight'].mul(0.01))))

    rating_contrib_df = rating_contrib_df.rename(columns={'MTD_Return_Contr': '% Contr', 'FactorWeight': '% Wght'})
    rating_contrib_df = rating_contrib_df.T
    rating_contrib_df.columns = pd.MultiIndex.from_product([['Returns'], rating_contrib_df.columns])

    """Spreads DF"""
    spreads_df = pd.pivot_table(mtd_rtrns, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                values=['curr_Spd_Factor'], aggfunc={'curr_Spd_Factor': np.sum})
    spreads_df = spreads_df.rename(columns={'curr_Spd_Factor': 'Spreads'}, level=0)

    """Weights DF"""
    weight_df = pd.pivot_table(mtd_rtrns, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                               values=['FactorWeight'], aggfunc={'FactorWeight': np.sum})
    weight_df = weight_df.rename(columns={'FactorWeight': 'Weights'}, level=0)

    """Loan Count DF"""
    bond_count_df = pd.pivot_table(mtd_rtrns, index=['Industry Lvl 3 Desc'], columns=['Rating_L1'],
                                   values=['BOND_COUNT'], aggfunc={'BOND_COUNT': np.sum})
    bond_count_df = bond_count_df.rename(columns={'BOND_COUNT': 'Bond Counts'}, level=0)

    """Combining above parts into one dataframe"""

    bond_attrib_df = pd.concat([return_df, industry_contrib_df, spreads_df, weight_df, bond_count_df], sort=False,
                               axis=1)
    bond_attrib_df = pd.concat([bond_attrib_df, rating_contrib_df], sort=False, axis=0)
    bond_attrib_df.columns.names = return_df.columns.names

    """Top & Bottom Contributors"""
    top_bottom_contribs = get_top_bottom_contributors(mtd_rtrns_ticker)

    """Formatting for Dash Report"""
    bond_attrib_df = bond_attrib_df.reset_index()
    mtd_return = mtd_return.round(2)

    if SAVE_OUTPUT:
        writer = pd.ExcelWriter(os.path.join(OUTPUT_PATH, '{}_attrib.xlsx'.format(fund_name)), engine='xlsxwriter')
        bond_attrib_df.to_excel(writer, sheet_name='attribution')
        top_bottom_contribs.to_excel(writer, sheet_name='top_bopttm_contrib')
        writer.save()

    return bond_attrib_df, top_bottom_contribs, mtd_return


if __name__ == '__main__':
    from common.utils.envsetup import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    generate_snp_attribution(datetime(2020, 10, 19), datetime(2020, 10, 25))
    generate_index_alpha_excess('H0A0', datetime(2020, 10, 19), datetime(2020, 10, 25))
