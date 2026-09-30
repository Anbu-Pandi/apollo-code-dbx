import logging

import pandas as pd

from data_access.dbtables import BOND_REFERENCEDATA_APP
from data_access.utils import tosqllist
from common.utils import sqlutil, dtutil
from data_access.dbtables.factset.financial_data import get_company_financials

CROSS_REF_SQL = '''
SELECT
	*
FROM
	f_get_issuer_cross_ref(NULL)
WHERE
	id_bb_company IN {}
'''

FINANCIALS_SQL = '''
SELECT
	*
FROM
	workspace.f_get_company_financials_csv('{}',
	NULL,
	'{}',
	'{}',
	'{}')
ORDER BY
	company_id,
	period_end_date DESC
'''


def get_financials_by_company(id_bb_company, fromdt, todt, freq='Q'):
    fromdt = dtutil.dateadd(fromdt, '-3m')
    todt = dtutil.todate(todt)
    id_bb_company = tosqllist(id_bb_company)
    sql = CROSS_REF_SQL.format(id_bb_company)
    df = sqlutil.readdfsql(sql, app=BOND_REFERENCEDATA_APP)
    df = df.dropna(subset=['factset_entity_id']).drop_duplicates(subset=['factset_entity_id']) \
        .set_index('factset_entity_id')

    factset_company_ids = df.index.tolist()
    findfs = []
    for f in freq.split(','):
        findfs.append(get_company_financials(factset_company_ids, fromdt, todt, f))
    findf = pd.concat(findfs)
    findf['id_bb_company'] = findf['company_id'].map(df['id_bb_company'])
    findf['id_bb_global_company_name'] = findf['company_id'].map(df['id_bb_global_company_name'])

    fin_dict = {}
    for co in findf['id_bb_global_company_name'].unique():
        fin_dict[co] = findf[findf['id_bb_global_company_name'] == co]

    return fin_dict


if __name__ == '__main__':
    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('prod')
    id_bb_company = [101382, 36269471, 51360217, 227672, 209899]
    fromdt = '2022-01-01'
    todt = '2022-01-01'

    fin_dict = get_financials_by_company(id_bb_company, fromdt, todt)
    logging.info(fin_dict)
