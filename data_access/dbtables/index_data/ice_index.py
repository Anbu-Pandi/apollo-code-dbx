from common.utils.sqlutil import readdfsql
from data_access.dbtables import ICE_INDEX_APP
from data_access.spark.dbtables.ice_index.api import get_ice_index_query
from data_access.utils import tosqllist


def get_constituents(datefilters, index=None, cusip_or_isin=None, industry=None):
    sql = get_ice_index_query(datefilters, index, cusip_or_isin, industry)
    df = readdfsql(sql, app=ICE_INDEX_APP)
    return df


def get_ice_index_summary_data(ticker, start_date, end_date, freq='daily'):
    sql = f"""
    select *  
    from ice_index_summary
    where ice_index_ticker in {tosqllist(ticker)}
    and asof_date between '{start_date}' and '{end_date}'
    and lower(frequency) in {tosqllist(freq).lower()}
    """
    df = readdfsql(sql, app=ICE_INDEX_APP)

    return df


if __name__ == '__main__':
    from common.utils.envsetup import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    dates = {'from_yyyymmdd': '20210501', 'to_yyyymmdd': '20210531'}
    df = get_constituents(dates, index='H0A0')
    pass
