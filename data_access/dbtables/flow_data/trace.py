from common.utils.sqlutil import readdfsql
from data_access.dbtables import TRACE_APP as APP
from data_access.spark.dbtables.trace.api import get_trace_query


def get_trades(datefilters, knowledgedatetime=None,
               cusip_or_isin=None, sector=None, join_uncapped=True):
    tracequerysql = get_trace_query(datefilters, knowledgedatetime, cusip_or_isin, sector, join_uncapped=join_uncapped)
    return readdfsql(tracequerysql, app=APP)


def get_trace_trades_summary(from_dt, to_dt, is_portfolio_trade=True):
    sql = (f"select * from trace_portfolio_summary "
           f"where effective_date between '{from_dt}' and '{to_dt}'")

    if is_portfolio_trade:
        sql = f"{sql} and portfolio_trade_index not in ('etrade', 'vtrade')"

    df = readdfsql(sql, app=APP)
    return df


def get_trace_portfolio_trades(from_dt, to_dt, is_portfolio_trade=True):
    sql = (f"select * from dbo.trace_portfolio_trades "
           f"where effective_date between '{from_dt}' and '{to_dt}'")

    if is_portfolio_trade:
        sql = f"{sql} and btrades_portfolio_trade_index_2s not in ('etrade', 'vtrade')"

    df = readdfsql(sql, app=APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger('trace_api_test')

    config.init('prod')

    trades = get_trades({'from_yyyymmdd': '20231207', 'to_yyyymmdd': '20231207'}, cusip_or_isin='AZN', join_uncapped=True)

    print(trades)
    pass
