import logging

import pandas as pd

from common.utils import dtutil, sqlutil
from common.utils.dfutil import tolist
from data_access.dbtables.spglobal import APP
from data_access.utils import tosqllist

FINANCIALS_QUERY = '''
select company_id, company_name, dataitem_name as metrics, dataitem_value as value, period_end_date, calendar_year, calendar_quarter
from workspace.f_get_company_financials_csv('{}', {}, '{}', '{}', '{}') f
order by company_id, period_end_date desc
'''


def get_company_financials(company_id, from_dt, to_dt, period_type, dataitems='NULL'):
    logging.info(f'fetching company_financials for company id: {company_id}')
    if dataitems != 'NULL':
        dataitems = ','.join(tolist(dataitems))
        dataitems = f"'{dataitems}'"
    company_id = ','.join([str(x) for x in tolist(company_id)])
    query = FINANCIALS_QUERY.format(company_id, dataitems, from_dt, to_dt, period_type)
    df = sqlutil.readdfsql(query, app=APP)
    df = df[df['period_end_date'] >= pd.to_datetime(from_dt)]

    df['qtr'] = df['calendar_year'].astype(str) + ' Q' + df['calendar_quarter'].astype(str)

    return df


def get_company_dcs(company_id, from_dt, to_dt):
    logging.info(f'fetching company dcs for company id: {company_id}')
    dataitems = ['IQ_RC', 'IQ_SR_DEBT', 'IQ_BANK_DEBT', 'IQ_TOTAL_SR_SECURED', 'IQ_UNSECURED_DEBT',
                 'IQ_VARIABLE_RATE_DEBT', 'IQ_LEASES_TOTAL', 'IQ_OTHER_DEBT', 'IQ_UNDRAWN_CREDIT', 'IQ_SR_BONDS_NOTES',
                 'IQ_CP', 'IQ_TOTAL_DEBT', 'IQ_NET_DEBT']
    return get_company_financials(company_id, from_dt, to_dt, 'Quarterly', dataitems=dataitems)


FILING_DATE_QUERY = '''
SELECT 
    cc.companyId, max(fi.periodEndDate) as periodEndDate, max(fi.filingDate) as filingDate
FROM
    dbo.ciqCompany cc (nolock)
JOIN dbo.ciqFinPeriod fp (nolock)
        ON
    fp.companyId = cc.companyId
JOIN dbo.ciqPeriodType pt (nolock)
        ON
    pt.periodTypeId = fp.periodTypeId
JOIN dbo.ciqFinInstance fi (nolock)
        ON
    fi.financialPeriodId = fp.financialPeriodId
WHERE
    fi.latestForFinancialPeriodFlag = 1
    AND pt.periodTypeName = '{}'
    AND cc.companyId in {}
GROUP BY
    cc.companyId
'''


def get_latest_filing_date(snp_company_ids, period='LTM'):
    query = FILING_DATE_QUERY.format(period, tosqllist(snp_company_ids))
    df = sqlutil.readdfsql(query, app=APP)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('dev')
    company_id = '318091'
    df = get_company_financials(company_id, '2021-01-01', '2022-12-31', 'Annual')
    logging.info(df)
