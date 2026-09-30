import pandas as pd
import numbers

from apo_config.config import config
from common.utils import sqlutil
from common.utils.dfutil import tolist
from data_access.dbtables.spglobal import APP
from data_access.utils import tosqllist


def get_csv_values(data):
    if isinstance(data, (str, numbers.Number)):
        data = [data]
    return ",".join(map(str, data))


def _get_sql_equivalent(value):
    return value.replace("'", "''")


def get_company_industry_by_id(company_id):
    company_id = ",".join([str(int(x)) for x in tolist(company_id) if pd.notnull(x)])
    sql = f'''
    SELECT ci.*, cc.companyName  FROM workspace.f_get_industry_csv('{company_id}') ci
    JOIN ciqCompany cc ON ci.companyId=cc.companyId 
    '''
    df = sqlutil.readdfsql(sql, app=APP)
    return df


def get_data_id_by_formula_metric(metrics):
    query = f"select formulametric as metric, dataitemid as data_item_id " \
            f"from ciqdataitemformulametric" \
            f" where formulametric in {metrics}"

    df = sqlutil.readdfsql(query, app=APP)

    return get_csv_values(tuple(df['data_item_id']))


def get_peer_companies():
    query = f'''
SELECT DISTINCT
    ccit.companyid as company_id, cst.subtypevalue as industry
FROM dbo.ciqcompanyindustrytree ccit
    LEFT JOIN dbo.ciqsubtype cst
        ON cst.subtypeid = ccit.subtypeid
    JOIN ciqCompany c
        ON c.companyId = ccit.companyId
    JOIN ciqFinPeriod fp
        ON fp.companyId = c.companyId
    JOIN ciqPeriodType pt
        ON pt.periodTypeId = fp.periodTypeId
    JOIN ciqFinInstance fi
        ON fi.financialPeriodId = fp.financialPeriodId
    JOIN ciqFinInstanceToCollection ic
        ON ic.financialInstanceId = fi.financialInstanceId
    JOIN ciqFinCollection fc
        ON fc.financialCollectionId = ic.financialCollectionId
    JOIN ciqFinCollectionData fd
        ON fd.financialCollectionId = ic.financialCollectionId
    JOIN ciqDataItem di
        ON di.dataItemId = fd.dataItemId
WHERE ccit.primaryflag = 1
      AND childlevel = 4
      AND c.companyStatusTypeId IN ( 1, 2, 20 )
      AND c.countryId IN ( 213, 212, 37 )
      AND fi.latestForFinancialPeriodFlag = 1
      AND fd.dataItemId = 28
      AND fp.periodTypeId = 1
--AND fi.periodEndDate >= '2020-01-01'
    '''
    df = sqlutil.readdfsql(query, app=APP)
    return df


def get_company_type(company_id):
    query = f'''
                select cc.companyId as sp_company_id, cct.companyTypeName as sp_company_type 
                from ciqCompany cc left join ciqCompanyType cct
                on cc.companyTypeId = cct.companyTypeId 
                where companyid in {tosqllist(company_id, isstr=False)}
            '''

    df = sqlutil.readdfsql(query, app=APP)
    return df


def get_distinct_industry_all_levels():
    query = '''
SELECT distinct
    Concat('industry_level_', Cast(cst.childlevel AS VARCHAR(100))) AS industry_level,
    cst.subtypevalue AS industry
FROM dbo.ciqcompanyindustrytree ccit
    LEFT JOIN dbo.ciqsubtype cst
        ON cst.subtypeid = ccit.subtypeid
WHERE ccit.primaryflag = 1
    '''

    df = sqlutil.readdfsql(query, app=APP)
    return df


def get_distinct_industry_for_level(lvl):
    query = f'''
SELECT distinct
    Concat('industry_level_', Cast(cst.childlevel AS VARCHAR(100))) AS industry_level,
    cst.subtypevalue AS industry
FROM dbo.ciqcompanyindustrytree ccit
    LEFT JOIN dbo.ciqsubtype cst
        ON cst.subtypeid = ccit.subtypeid
WHERE ccit.primaryflag = 1
    AND cst.childlevel={lvl}
    '''

    df = sqlutil.readdfsql(query, app=APP)
    return df


if __name__ == '__main__':
    config.init('dev')
    company_name = 'Foot Locker, Inc.'
    temp_df = get_company_industry_by_name(company_name)
    print(temp_df)
