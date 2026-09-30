from common.utils import sqlutil
from data_access.dbtables.factset.utils import normalize_df
from data_access.dbtables.factset import APP
from data_access.utils import tosqllist
import logging

def get_company_industry_by_id(company_id):
    sql = '''
    SELECT 
	ec.FACTSET_ENTITY_ID, ec.ENTITY_NAME, ec.ENTITY_PROPER_NAME, ec.PRIMARY_SIC_CODE, ec.INDUSTRY_CODE , ec.SECTOR_CODE , i1.SIC_DESC , i2.FACTSET_INDUSTRY_DESC , i3.FACTSET_SECTOR_DESC , i4.* 
    FROM ENT_V1.ENT_ENTITY_COVERAGE ec
    JOIN SYM_V1.SYM_ENTITY_SECTOR ses ON ec.FACTSET_ENTITY_ID = ses.FACTSET_ENTITY_ID 
    JOIN SYM_V1.SYM_ENTITY_SECTOR_RBICS sesr ON ec.FACTSET_ENTITY_ID = sesr.FACTSET_ENTITY_ID
    JOIN REF_V2.SIC_MAP i1 ON ses.PRIMARY_SIC_CODE = i1.SIC_CODE  
    JOIN REF_V2.FACTSET_INDUSTRY_MAP i2 ON ses.INDUSTRY_CODE  = i2.FACTSET_INDUSTRY_CODE  
    JOIN REF_V2.FACTSET_SECTOR_MAP i3 ON ses.SECTOR_CODE  = i3.FACTSET_SECTOR_CODE 
    JOIN REF_V2.RBICS_STRUCTURE_L2_CURR i4 ON sesr.L2_ID = i4.L2_ID 
    WHERE 
    ec.FACTSET_ENTITY_ID IN {}
    '''
    df = sqlutil.readdfsql(sql.format(tosqllist(company_id)), app=APP)
    df = normalize_df(df)
    return df


def get_peer_companies():
    query = '''
    SELECT 
        DISTINCT ec.FACTSET_ENTITY_ID, ec.ENTITY_PROPER_NAME, i1.SIC_DESC , i2.FACTSET_INDUSTRY_DESC , i3.FACTSET_SECTOR_DESC
    FROM FF_V3.FF_SEC_COVERAGE fsc
    JOIN SYM_V1.SYM_COVERAGE sc ON fsc.FSYM_ID = sc.FSYM_REGIONAL_ID 
    JOIN SYM_V1.SYM_SEC_ENTITY sse ON sc.FSYM_ID = sse.FSYM_ID 
    JOIN ENT_V1.ENT_ENTITY_COVERAGE ec ON ec.FACTSET_ENTITY_ID = sse.FACTSET_ENTITY_ID
    JOIN SYM_V1.SYM_ENTITY_SECTOR ses ON ec.FACTSET_ENTITY_ID = ses.FACTSET_ENTITY_ID 
    JOIN REF_V2.SIC_MAP i1 ON ses.PRIMARY_SIC_CODE = i1.SIC_CODE  
    JOIN REF_V2.FACTSET_INDUSTRY_MAP i2 ON ses.INDUSTRY_CODE  = i2.FACTSET_INDUSTRY_CODE  
    JOIN REF_V2.FACTSET_SECTOR_MAP i3 ON ses.SECTOR_CODE  = i3.FACTSET_SECTOR_CODE 
    WHERE 
        ec.ISO_COUNTRY IN ('US', 'CA', 'DE', 'GB')
        AND fsc.FA_LATEST_ANN_UPDATE >= YEAR(getdate())-5
    '''
    df = sqlutil.readdfsql(query, app=APP)
    df = normalize_df(df, rename_dict={'factset_entity_id': 'company_id', 'factset_industry_desc': 'industry'})
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger


    setuplogger()
    from apo_config.config import config

    config.init('dev')
    company_name = '000C7F-E'

    temp_df = get_peer_companies()
    temp_df = temp_df[temp_df['industry'] == 'Regional Banks']
    logging.info(temp_df.to_string())
