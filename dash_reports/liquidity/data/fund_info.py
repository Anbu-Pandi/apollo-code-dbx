import pandas as pd

from dash_reports.liquidity.data.utils import fund_info_file
from dash_reports.liquidity.data.utils import sort_df


def get_fund_info_tbl():
    df = pd.read_excel(fund_info_file)
    sort_order = {'Controller': 0, 'Hurdle Rate': 1, 'Prime Broker (Shorts)': 2, 'ISDAs (Derivs.)': 3,
                  'Origination': 4, 'Cross Trades': 5, 'Short Sales': 6, 'Stock Options': 7, 'Futures Options': 8,
                  'Commodity Future Options': 9, 'FX Options': 10, 'Index Options': 11, 'Swaptions': 12,
                  'Interest Rate Swaps': 13,
                  'Currency Swaps': 14, 'CDS': 15, 'Index CDS': 16, 'ETFs': 17, 'Equity Limit': 18,
                  'Single Issuer Limit': 19,
                  'Single Industry Limit': 20, 'Leverage Facilities Limit': 21, 'Additional LP Notes': 22}

    fund_limit_fields = ['Equity Limit', 'Single Issuer Limit', 'Single Industry Limit', 'Leverage Facilities Limit',
                         'Additional LP Notes']

    df = df.drop('asof_date', axis=1)
    df = df.set_index(['Field', 'fund_entity_code'])
    df = df.unstack('fund_entity_code')
    df.columns = df.columns.droplevel(0)
    df = df.reset_index()
    df = sort_df(df, 'Field', sort_order)
    fund_limit_df = df.loc[df['Field'].isin(fund_limit_fields)]
    fund_info_df = df.loc[~df['Field'].isin(fund_limit_fields)]

    return {'Fund Information': fund_info_df, 'Fund Limits': fund_limit_df}


