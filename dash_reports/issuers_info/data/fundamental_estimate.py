from data_access.dbtables.factset import estimates
from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX
from dash_reports.report_common.elastic_search.api import default_es

FE_UNIVERSE = {'EPS': 'Earnings Per Share', 'SALES': 'Sales', 'EBIT': 'EBIT', 'NET': 'Net Profit', 'EBITDA': 'EBITDA',
               'REV_TOT': 'Total Revenues', 'CAPEX': 'CAPEX', 'EBITDA_ADJ': 'Adjusted EBITDA', 'FCF': 'Free Cash Flow',
               'INT_EXP': 'Interest Expense', 'TOTAL_DEBT': 'Total Debt', 'DPS': 'Dividends Per Share',
               'BPS': 'Book Value Per Share', 'CUSTOM_FE_SHARE_COUNT': 'Sharecount',
               'CUSTOM_FE_DIV_PAID': 'Dividends Paid Total',
               }


def get_fe_items_universe():
    return FE_UNIVERSE


def get_tickers_from_id(id_bb_company):
    df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_company', id_bb_company)
    if len(df) > 0:
        return df.iloc[0]['company_corp_ticker'] + '-US'


def get_fundamental_estimates(fromdt, todt, id_bb_company, financial_items):
    tickers = get_tickers_from_id(id_bb_company)
    if (tickers is None) or len(tickers) == 0:
        return {}
    df = estimates.get_estimates(fromdt, todt, financial_items, tickers=tickers)
    dfs = {i: dfgrouped for i, dfgrouped in df.groupby('FE_ITEM')}
    return dfs


def get_recent_fundamental_estimate_detail(id_bb_company, financial_items):
    tickers = get_tickers_from_id(id_bb_company)
    if (tickers is None) or len(tickers) == 0:
        return {}
    df = estimates.get_recent_estimate_detail(financial_items, tickers=tickers)
    dfs = {i: dfgrouped for i, dfgrouped in df.groupby('FE_ITEM')}
    return dfs
