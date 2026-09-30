from common.utils.sqlutil import sql_replace_escape_char
from data_access.beta_attribution import readdfsql_schema
from data_access.beta_attribution.utils import todatestr
from data_access.dbtables import BETA_EST_SUBAPP as APP

FUND_SLEEVE_SQL = """
SELECT 
DISTINCT fund_code as [FUND CODE], 
         short_sleeve_name as [Short Sleeve Name]
FROM [dbo].[ba_fund_sleeve_mapping]
ORDER BY [FUND CODE]
"""


def get_fundcode_to_sleeve_mapping():
    df = readdfsql_schema(sql_replace_escape_char(FUND_SLEEVE_SQL), app=APP)
    return df


RATING_SQL = """
SELECT 
    [index] as [Index], 
    moody as [Moody], 
    sp as [SP], 
    fitch as [Fitch], 
    rating_factor as [Rating Factor], 
    rating_bucket as [Rating Bucket], 
    exp_loss_1y as [Exp Loss 1Y], 
    sp_new as [SP_NEW], 
    ice_index as [ICE_INDEX] 
FROM dbo.rating_mapping
"""


def get_rating_mapping():
    df = readdfsql_schema(sql_replace_escape_char(RATING_SQL), app=APP)
    return df


GICS_ICE_SQL = """
SELECT 
    gics_industry_group as [GICS INDUSTRY GROUP], 
    ice_sector as [ICE SECTOR]
FROM dbo.gics_ind_to_ice_sector
"""


def get_GICS_ICE_sector_mapping():
    df = readdfsql_schema(sql_replace_escape_char(GICS_ICE_SQL), app=APP)
    return df


CUSIP_LOANX_SQL = """
with latest_data as (
    select max(asof_date) as max_dt
    from dbo.jpm_loan_index
)
select 
    cusip as [CUSIP], 
    loanx as [LOANX] 
from dbo.jpm_loan_index
where asof_date in (select max_dt from latest_data)
    and cusip is not null
    and loanx is not null
"""


def get_cusip_loanxid_mapping():
    df = readdfsql_schema(sql_replace_escape_char(CUSIP_LOANX_SQL), app=APP)
    return df


SEC_REFINDEX_SQL = """
SELECT  
    security_type as [SECURITY_TYPE], 
    reference_index as [REFERENCE_INDEX], 
    instrument_type as [INSTRUMENT_TYPE] 
FROM dbo.[BA_SEC_REF_INDEX_MAPPING]
"""


def get_sec_refindex_mapping():
    df = readdfsql_schema(sql_replace_escape_char(SEC_REFINDEX_SQL), app=APP)
    return df


LOANID_SQL = """
SELECT
facility_id as [FACILITY_ID], 
cusip as [CUSIP], 
loanx_id as [LoanX-ID], 
global_id as [Global ID] 
FROM dbo.loan_id_mapping
"""


def get_loanid_mapping():
    df = readdfsql_schema(sql_replace_escape_char(LOANID_SQL), app=APP)
    return df


SEC_BETA_OVERRIDE_SQL = """
SELECT
    security as [SECURITY], 
    beta as [BETA], 
    start_date as [START_DATE], 
    end_date as [END_DATE] 
FROM dbo.ba_sec_beta_override
"""


def get_sec_beta_override_mapping():
    df = readdfsql_schema(sql_replace_escape_char(SEC_BETA_OVERRIDE_SQL), app=APP)
    return df


ICE_FEED_COL_SQL = """
SELECT * FROM dbo.ICE_FEED_COL_MAPPING
"""


def get_ice_col_mapping():
    df = readdfsql_schema(sql_replace_escape_char(ICE_FEED_COL_SQL), app=APP)
    return df


SEC_EXCEPTION_SQL = """
SELECT security
FROM dbo.ba_sec_exception
WHERE cast(start_date as DATE) <= '{0}' AND '{0}' <= cast(end_date as DATE)
"""


def get_sec_exception(dt):
    df = readdfsql_schema(SEC_EXCEPTION_SQL.format(todatestr(dt)), app=APP)
    return df['security'].to_list()


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    get_sec_exception('2024-09-06')
