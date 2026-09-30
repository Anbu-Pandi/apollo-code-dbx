import datetime
import logging

import pandas as pd

from common.utils import dtutil, sqlutil
from common.utils.dfutil import tolist
from data_access.dbtables import GS_MARKETDATA_APP, BARC_LCS_APP
from data_access.spark.api import sparkmanager
from data_access.utils import tosqllist

COVERAGE_SQL = '''
with bbid_latest as (
select asset_id, max(knowledge_date) as max_kd 
from bond_liquidity_gs_queries
where knowledge_date <= '{0}'
{1}
group by asset_id
)
select bl_x.*, 
row_number() over(partition by bl_x.asset_id order by bl_x.knowledge_date desc) as rk 
from bond_liquidity_gs_queries bl_x
join bbid_latest bl_y on bl_x.asset_id=bl_y.asset_id and bl_x.knowledge_date=bl_y.max_kd
where bl_x.knowledge_date <= '{0}'
{1}
'''

LIQ_SCORE_SQL = '''
with bbid_latest as (
select sec_id, name, asof_date, max(knowledge_date) as max_kd 
from bond_liquidity
where knowledge_date <= '{0}'
and asof_date between '{1}' and '{2}' 
{3}
group by sec_id, name, asof_date
)
select bl_x.*,
row_number() over(partition by bl_x.sec_id, bl_x.name, bl_x.asof_date order by bl_x.knowledge_date desc) as rk 
from bond_liquidity bl_x
join bbid_latest bl_y on bl_x.sec_id=bl_y.sec_id and bl_x.name=bl_y.name and bl_x.asof_date=bl_y.asof_date and bl_x.knowledge_date=bl_y.max_kd
where bl_x.knowledge_date <= '{0}'
and bl_x.asof_date between '{1}' and '{2}' 
{3}
'''

GS_LIQUIDITY_ASOF_SQL = '''
with eligible as (
    select
        bl.sec_id,
        bl.liquidity_score_buy,
        bl.liquidity_score_sell,
        bl.asof_date,
        bl.knowledge_date,
        row_number() over (
            partition by bl.sec_id
            order by bl.asof_date desc, bl.knowledge_date desc
        ) as rk
    from bond_liquidity bl
    where cast(bl.asof_date as date) <= '{0}'
    and bl.knowledge_date <= '{1}'
    {2}
)
select
    sec_id,
    liquidity_score_buy,
    liquidity_score_sell,
    asof_date as gs_liquidity_asof_date
from eligible
where rk = 1
and sec_id is not null
'''

BARC_LCS_MONTHLY_ASOF_SQL = '''
with eligible as (
    select
        bl.identifier,
        bl.cusip,
        bl.lcs,
        bl.asof_date,
        bl.knowledge_date,
        row_number() over (
            partition by bl.identifier
            order by bl.asof_date desc, bl.knowledge_date desc
        ) as rk
    from barc_lcs_monthly bl
    where cast(bl.asof_date as date) <= '{0}'
    {1}
)
select
    identifier,
    cusip,
    lcs as barc_lcs,
    asof_date as barc_lcs_asof_date
from eligible
where rk = 1
and identifier is not null
'''


def get_coverage(cusips=None, knowledge_date=None):
    cusips = tolist(cusips, replacenone=True)
    cusip_clause = ''
    if len(cusips) != 0:
        cusip_clause = f'AND bbid in {tosqllist(cusips, isstr=True)}'

    knowledge_date = pd.to_datetime(knowledge_date or dtutil.dateadd(dtutil.today(), '1d')) \
        .strftime('%Y-%m-%d %H:%M:%S')
    sql = COVERAGE_SQL.format(knowledge_date, cusip_clause)
    df = sqlutil.readdfsql(sql, app=GS_MARKETDATA_APP)
    df = df[df['rk'] == 1]
    return df


def get_gs_liquidity_asof(asof_date, cusips=None, knowledge_date=None):
    cusips = tolist(cusips, replacenone=True)
    cusip_clause = ''
    if len(cusips) != 0:
        cusip_clause = f'AND sec_id in {tosqllist(cusips, isstr=True)}'

    asof_date = dtutil.todate(asof_date)
    knowledge_date = pd.to_datetime(knowledge_date or dtutil.dateadd(dtutil.today(), '1d')) \
        .strftime('%Y-%m-%d %H:%M:%S')
    sql = GS_LIQUIDITY_ASOF_SQL.format(asof_date, knowledge_date, cusip_clause)
    return sqlutil.readdfsql(sql, app=GS_MARKETDATA_APP)


def get_barc_lcs_monthly_asof(asof_date, identifiers=None):
    identifiers = tolist(identifiers, replacenone=True)
    identifier_clause = ''
    if len(identifiers) != 0:
        identifier_clause = f'AND identifier in {tosqllist(identifiers, isstr=True)}'

    asof_date = dtutil.todate(asof_date)
    sql = BARC_LCS_MONTHLY_ASOF_SQL.format(asof_date, identifier_clause)
    return sqlutil.readdfsql(sql, app=BARC_LCS_APP)


def get_liquidity_score(fromdt, todt, cusips=None, knowledge_date=None):
    cusips = tolist(cusips, replacenone=True)
    cusip_clause = ''
    if len(cusips) != 0:
        cusip_clause = f'AND sec_id in {tosqllist(cusips, isstr=True)}'

    knowledge_date = pd.to_datetime(knowledge_date or dtutil.dateadd(dtutil.today(), '1d')) \
        .strftime('%Y-%m-%d %H:%M:%S')
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    sql = LIQ_SCORE_SQL.format(knowledge_date, fromdt, todt, cusip_clause)
    df = sqlutil.readdfsql(sql, app=GS_MARKETDATA_APP)
    df = df[df['rk'] == 1]
    return df


def write_barc_lcs(product_id, asof_date, df, overwrite=True):
    PRODUCT_TO_TBL = {'QPS_LCS_MONTHLY': 'barc_lcs_monthly', 'QPS_LCS_DAILY': 'barc_lcs_daily', 'QPS_LCS_YEARLY': None}
    tbl = PRODUCT_TO_TBL.get(product_id, None)
    if tbl is not None:
        if overwrite:
            additional_clause = f" where asof_date='{asof_date}'"
            if 'daily' in product_id.lower():
                additional_clause = ""
            del_stmt = f"DELETE FROM {tbl} {additional_clause}"
            sqlutil.execsql(del_stmt, app=BARC_LCS_APP)

        sparkmanager.pandastosql(df, tbl, app=BARC_LCS_APP)


def write_gs_bond_liquidity_coverage(df):
    if len(df) > 0:
        df['knowledge_date'] = datetime.datetime.now()
        df['load_date'] = datetime.datetime.now()

        sparkmanager.pandastosql(df, 'bond_liquidity_gs_queries', app=GS_MARKETDATA_APP)


def write_gs_bond_liquidity(df):
    if len(df) > 0:
        df['sec_id_type'] = 'cusip'
        df['load_date'] = datetime.datetime.now()
        sparkmanager.pandastosql(df, 'bond_liquidity', app=GS_MARKETDATA_APP)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()

    config.init('dev')
    fromdt = '20230101'
    todt = '20231231'
    df = get_liquidity_score(fromdt, todt)
    logging.info(df)
