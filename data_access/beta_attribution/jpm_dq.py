from data_access.beta_attribution import readdfsql_schema
from data_access.dbtables import BETA_EST_SUBAPP as APP
from data_access.utils import tosqllist
from common.utils.dfutil import tolist

SQL = r'''
SELECT 
    JDRC.symbol as SYMBOL, 
    JDRC.attribute as ATTRIBUTE, 
    JDRC.frequency as FREQUENCY, 
    JDC.query as QUERY
FROM 
    dbo.jpm_dq_run_config JDRC
INNER JOIN
    dbo.jpm_dq_config JDC
    ON 
        JDRC.symbol = JDC.symbol
        AND JDRC.attribute = JDC.attribute
WHERE 
    lower(JDRC.symbol) IN {1}
    AND lower(frequency) = '{0}'
ORDER BY SYMBOL, ATTRIBUTE
'''


def get_treasury_symbols(freq):
    tsy_secids = ['tsy_1m', 'tsy_3m', 'tsy_12m', 'tsy_2y', 'tsy_3y', 'tsy_5y', 'tsy_7y', 'tsy_10y', 'tsy_30y']
    tsy_secids = [str(x).lower() for x in tsy_secids]
    df = get_symbols_by_secid(tsy_secids, freq.lower())
    return df


def get_symbols_by_secid(secid, freq):
    secid = tolist(secid)
    secid = [str(x).lower() for x in secid]
    df = readdfsql_schema(SQL.format(freq.lower(), tosqllist(secid)), app=APP)
    return df
