from common.utils import sqlutil, dtutil
from dash_reports.issuer_debt_in_index.data.sql_const import BOND_OAS_PCTILE_SQL
from data_access.dbtables import ICE_INDEX_APP
from data_access.utils import tosqllist


def get_bond_oas_percentile_df(ice_index_tickers, dt):
    sql = BOND_OAS_PCTILE_SQL.format(dtutil.dateadd(dt, '-1y'), dt, tosqllist(ice_index_tickers))
    oas_df = sqlutil.readdfsql(sql, app=ICE_INDEX_APP)
    return oas_df
