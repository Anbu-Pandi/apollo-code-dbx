import pandas as pd

from data_access.beta_attribution import APP
from data_access.dbtables import SNP_APP
from data_access.beta_attribution.utils import readdfsql_schema
from data_access.dbtables import MARKIT_BOND_PRICING_APP, MARKIT_LOAN_PRICING_APP
from common.utils.sqlutil import sql_replace_escape_char


def getMaxDateIce():
    query = "Select max(asof_date) as [MaxDate] from dbo.ice_index_data_new"
    data = readdfsql_schema(query, app=APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date


def getMaxDateMarkitBonds():
    query = "Select max([date]) as [MaxDate] from dbo.markit_pricing_data"
    data = readdfsql_schema(sql_replace_escape_char(query), app=MARKIT_BOND_PRICING_APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date


def getMaxDateMarkitLoan():
    query = "Select max([mark_date]) as [MaxDate] from dbo.markit_pricing_data_loan"
    data = readdfsql_schema(sql_replace_escape_char(query), app=MARKIT_LOAN_PRICING_APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date


def getMaxDateMarkitCDS():
    query = "Select max([date]) as [MaxDate] from dbo.MARKIT_CDS"
    data = readdfsql_schema(query, app=APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date


def getMaxDateSnP():
    query = "Select max(asof_date) as MaxDate from dbo.sp_loan_index"
    data = readdfsql_schema(query, app=SNP_APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date


def getMaxDateSPACBeta():
    query = "Select max([start_date]) as [MaxDate] from dbo.v_spac_beta"
    data = readdfsql_schema(query, app=APP)
    date = pd.to_datetime(data['MaxDate'][0])

    return date
