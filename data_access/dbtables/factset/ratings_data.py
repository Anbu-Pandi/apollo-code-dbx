import logging

import pandas as pd

from common.utils import sqlutil
from data_access.dbtables import BOND_REFERENCEDATA_APP
from data_access.dbtables.factset.utils import normalize_df
from data_access.dbtables.spglobal import ratings_data as spglobal_ratings_data
from data_access.utils import tosqllist

SP_FACTSET_COMPANY_ID_SQL = '''
select id_bb_company, sp_company_id, factset_entity_id from f_get_issuer_cross_ref(null)
where factset_entity_id IN {}
'''


def get_ratings_by_company_id(company_id, rating_type_code=['STDLONG', 'STDSHORT', 'PRLMSTDLNG', 'PRLMSTDST']):
    logging.info('fetching ratings data ')
    sp_co_id_df = sqlutil.readdfsql(SP_FACTSET_COMPANY_ID_SQL.format(tosqllist(company_id, isstr=True)),
                                    app=BOND_REFERENCEDATA_APP)
    sp_co_ids = pd.to_numeric(sp_co_id_df['sp_company_id'], errors='coerce', downcast='integer') \
        .dropna().unique().tolist()
    rating_df = spglobal_ratings_data.get_ratings_by_company_id([int(x) for x in sp_co_ids], rating_type_code)
    rating_df = pd.merge(rating_df.rename(columns={'company_id': 'sp_company_id'}), sp_co_id_df, on=['sp_company_id'])
    rating_df = normalize_df(rating_df)
    return rating_df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')
    company_name = "Macy's, Inc."  # "Foot Locker, Inc.'
    company_id = 315394
    temp_df = get_ratings_by_company_id(company_id, 'STDLONG')
    print(temp_df)
