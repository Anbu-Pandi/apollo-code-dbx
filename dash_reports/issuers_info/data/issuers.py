from common.utils import sqlutil, dtutil
from data_access.dbtables import MARKIT_CDS_PRICING_APP, BOND_REFERENCEDATA_APP, LOAN_REFERENCEDATA_APP

BBG_ISSUERS_SQL = "select * from f_get_bond_issuer_master(NULL) where knowledge_date >= '{}'"

BBG_BONDS_SQL = "select * from f_get_bond_security_master(NULL) where knowledge_date >= '{}'"

WSO_LOANS_SQL = "select * from f_get_loan_security_master(NULL)"

MARKIT_CDS_SQL = '''
WITH max_dt AS (
SELECT max(asof_date) AS max_dt FROM markit_cds
)
SELECT icr.id_bb_company, icr.id_bb_global_company_name, cds.* FROM markit_cds cds
JOIN max_dt md ON md.max_dt = cds.asof_date
LEFT JOIN f_get_issuer_cross_ref(null) icr
ON cds.red_code=icr.markit_red_code
WHERE cds.tenor='5Y'
AND cds.primary_coupon='Y'
AND primary_curve='Y'
'''

BBG_BONDS_ATTR_SQL = '''
select * from f_get_bond_security_master_attributes(null)  where knowledge_date >= '{}'
'''


def get_bbg_issuers(dt='1900-01-01'):
    df = sqlutil.readdfsql(BBG_ISSUERS_SQL.format(dtutil.todate(dt)), app=BOND_REFERENCEDATA_APP)
    return df


def get_bbg_bonds(dt='1900-01-01'):
    df = sqlutil.readdfsql(BBG_BONDS_SQL.format(dtutil.todate((dt))), app=BOND_REFERENCEDATA_APP)
    return df


def get_bbg_bonds_attr(dt='1900-01-01'):
    df = sqlutil.readdfsql(BBG_BONDS_ATTR_SQL.format(dtutil.todate((dt))), app=BOND_REFERENCEDATA_APP)
    df = df.set_index(['id_isin', 'attribute_name'])['attribute_value'].unstack().reset_index()
    df.columns = df.columns.map(str.lower)
    return df


def get_wso_loans():
    df = sqlutil.readdfsql(WSO_LOANS_SQL, app=LOAN_REFERENCEDATA_APP)
    return df


def get_markit_cds():
    df = sqlutil.readdfsql(MARKIT_CDS_SQL, app=MARKIT_CDS_PRICING_APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')
    get_bbg_bonds_attr()
