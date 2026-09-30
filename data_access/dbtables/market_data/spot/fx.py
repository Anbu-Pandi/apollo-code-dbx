import logging
from datetime import datetime

from common.utils import sqlutil, dfutil, dtutil
from common.utils.envsetup import get_base_parser, init_app_using_parser
from data_access.utils import get_where_clause
from data_access.dbtables import FXSPOT_APP

FX_VIEW = 'PORTFOLIO_DB.PORTFOLIO.V_DDM_EXCHANGE_RATE_CROSS'

EXCLUDED_FX_CURRENCIES = ['ATS', 'BEF', 'ESP', 'FIM', 'FRF', 'ITL', 'JMD', 'LTL', 'LVL', 
                          'NLG', 'OMR', 'PTE', 'RMB', 'TRL', 'XBT', 'XCD']


def clear_fx(fromdt, todt, ccys=None):
    raise NotImplementedError("clear_fx not supported - using read-only Snowflake view")


def save_fx(df):
    raise NotImplementedError("save_fx not supported - using read-only Snowflake view")


def get_fx(fromdt, todt, ccys=None):
    logging.info(f"retrieving fx data from: {fromdt}, to: {todt}")
    dt_clause = f"AS_OF_DATE between '{fromdt}' and '{todt}'"
    ccys = dfutil.tolist(ccys, replacenone=True)
    ccy_clause = get_where_clause('FROM_CURRENCY_CODE', ccys) if ccys else None
    where_clauses = [dt_clause, "TO_CURRENCY_CODE = 'USD'"]
    if EXCLUDED_FX_CURRENCIES:
        excluded_list = ', '.join([f"'{x}'" for x in EXCLUDED_FX_CURRENCIES])
        excluded_clause = f"FROM_CURRENCY_CODE NOT IN ({excluded_list})"
        where_clauses.append(excluded_clause)
    if ccy_clause:
        where_clauses.append(ccy_clause)
    where_clause = ' AND '.join(where_clauses)
    
    include_usd = not ccys or 'USD' in ccys
    usd_union = """
        UNION ALL
        SELECT AS_OF_DATE, 'USD', 1.0 FROM fx_data 
        WHERE NOT EXISTS (SELECT 1 FROM fx_data WHERE FROM_CURRENCY_CODE = 'USD')
        GROUP BY AS_OF_DATE
    """ if include_usd else ""
    
    sql = f"""
        WITH fx_data AS (
            SELECT AS_OF_DATE, FROM_CURRENCY_CODE, 1/FX_RATE as FX_RATE 
            FROM {FX_VIEW} 
            WHERE {where_clause} AND FX_RATE != 0
        )
        SELECT * FROM fx_data
        {usd_union}
        ORDER BY FROM_CURRENCY_CODE, AS_OF_DATE
    """
    df = sqlutil.readdfsql(sql, app=FXSPOT_APP)
    df = df.rename(columns={'AS_OF_DATE': 'asof_date', 'FROM_CURRENCY_CODE': 'ccy', 'FX_RATE': 'value'})
    df['ccy_pair'] = 'USD' + df['ccy']
    df['knowledge_date'] = df['asof_date']
    df['load_date'] = datetime.now()
    return df[['ccy_pair', 'asof_date', 'value', 'ccy', 'load_date', 'knowledge_date']]


if __name__ == '__main__':
    init_app_using_parser(get_base_parser('fx_spot'))
    df = get_fx(dtutil.dateadd(dtutil.today(), '0b'), dtutil.today())
    # df = df[df['ccy'].isin(['EUR', 'GBP', 'JMD', 'JPY', 'INR'])]
    df.to_clipboard()
    logging.info(df.to_string())
