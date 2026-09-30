from common.utils.dtutil import todate
from common.utils.sqlutil import readdfsql
from data_access.dbtables.factset import APP
import logging


def get_spx_data(fromdt, todt):
    sql = '''
SELECT distinct tr.TICKER_REGION as index_name, 
    sm.PROPER_NAME, 
    sm.CURRENCY,
    gp.PRICE_DATE as asof_date,
    gp.ONE_DAY_PCT / 100 as RETURN 
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
     JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON tr.fsym_id=sm_regional.FSYM_ID
    JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm 
        ON sm.FSYM_SECURITY_ID=sm_regional.FSYM_SECURITY_ID 
            AND sm.FREF_EXCHANGE_CODE=sm_regional.FREF_EXCHANGE_CODE
            AND sm.FIRST_PRICE_DATE IS NOT NULL
     JOIN FACTSET_DB.FGP_V1.FGP_GLOBAL_PRICES gp ON gp.FSYM_ID=sm.FSYM_ID
     LEFT JOIN FACTSET_DB.FGP_V1.FGP_CA_ADJ_FACTORS cf ON cf.FSYM_ID = sm.FSYM_ID AND cf.EFFECTIVE_DATE = gp.PRICE_DATE 
     LEFT JOIN FACTSET_DB.FGP_V1.FGP_CA_EVENTS  ce ON ce.FSYM_ID = sm.FSYM_ID AND ce.EFFECTIVE_DATE = gp.PRICE_DATE 
WHERE tr.ticker_region='SPY-US'
AND gp.PRICE_DATE BETWEEN '{}' AND '{}'
ORDER BY gp.PRICE_DATE desc
    '''

    df = readdfsql(sql.format(todate(fromdt), todate(todt)), app=APP)
    df['asof_date'] = df['ASOF_DATE']
    df['index_name'] = df['INDEX_NAME']
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('dev')

    temp_df = get_spx_data('2023-05-01', '2023-05-30')
    logging.info(temp_df.to_string())
