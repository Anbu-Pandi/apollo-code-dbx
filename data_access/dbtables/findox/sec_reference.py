import logging

import pandas as pd

from common.utils import dfutil, sqlutil, dtutil
from data_access.dbtables import FINDOX_APP
from data_access.utils import tosqllist

INSTRUMENT_ID_SQL = '''
with f as (
SELECT issuer_id, identifier
FROM dbo.findox_models_common_ids
qualify row_number() over(PARTITION BY issuer_id, identifier order by created_on_date_time desc) = 1
)
SELECT COALESCE(b.id_isin, split(f.identifier, ' ')[0]) AS instrument_id, f.issuer_id 
FROM f
LEFT JOIN dbo.f_get_bond_security_master('{1}') b ON split(f.identifier, ' ')[0]=substring(b.id_bb_unique, 3)
WHERE COALESCE(b.id_isin, split(f.identifier, ' ')[0]) in {0}
'''


def get_findox_ids_from_instrument_ids(instrument_ids, dt):
    dfs = []
    dt = dt or dtutil.today()

    for _ids in dfutil.chunks_list(dfutil.tolist(instrument_ids), 10_000):
        _df = sqlutil.readdfsql(INSTRUMENT_ID_SQL.format(tosqllist(_ids), dt), app=FINDOX_APP)
        dfs.append(_df)
    df = pd.concat(dfs)
    return df

def get_findox_ids_tickers():
    sql = '''
SELECT ticker, findox_id, trading_name
FROM dbo.findox_agg_universe
QUALIFY row_number() over(PARTITION BY id, findox_id ORDER BY financial_data_updated desc)=1
    '''
    return sqlutil.readdfsql(sql, app=FINDOX_APP)

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    isins = ['US20600DAA19', 'US816196AT67']
    df = get_findox_ids_tickers()
    logging.info(df.to_string())