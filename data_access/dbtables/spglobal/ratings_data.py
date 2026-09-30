import logging
from common.utils import dtutil, sqlutil
from data_access.utils import tosqllist
from data_access.dbtables.spglobal import APP


def get_ratings_by_company_id(company_id, rating_type_code=['STDLONG', 'STDSHORT', 'PRLMSTDLNG', 'PRLMSTDST']):
    logging.info('fetching ratings data ')

    query = f'''
                SELECT cc.companyId as company_id, cc.companyName as company_name, srd.ratingtypecode, srd.ratingsymbol as rating, srd.ratingdate 
                FROM dbo.ciqCompany cc 
                JOIN dbo.spRatingIdentifier sri ON sri.objectid = cc.companyId 
                JOIN dbo.spratingdata srd ON srd.entitySymbolValue = sri.symbolValue 
                AND srd.ratingtypecode in {tosqllist(rating_type_code)}
                WHERE cc.companyId in {tosqllist(company_id)}
            '''
    df = sqlutil.readdfsql(query, app=APP)
    df['qtr'] = df['ratingdate'].map(dtutil.calendar_qtr)

    return df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')
    company_name = "Macy's, Inc."  # "Foot Locker, Inc.'
    company_id = 315394
    temp_df = get_ratings_by_company_id(company_id, 'STDLONG')
    print(temp_df)
