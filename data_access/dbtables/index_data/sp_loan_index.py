from common.utils.sqlutil import readdfsql
# from data_access.dbtables import APP
from data_access.spark.dbtables.sp_loan_index.api import get_sp_loan_index_query
import pandas as pd

APP = 'databricks'


def get_constituents(datefilters, issuer=None, industry=None, pricing_ids=None):
    sql = get_sp_loan_index_query(datefilters, issuer, industry, pricing_ids)
    df = readdfsql(sql, app=APP)
    df['asof_date'] = pd.to_datetime(df['asof_date']).dt.tz_localize(None)
    return df
