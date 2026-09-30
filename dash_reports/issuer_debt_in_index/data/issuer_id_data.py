import pandas as pd

from common.utils import sqlutil
from dash_reports.issuer_debt_in_index.data.sql_const import ISSUER_ID_SQL
from data_access.dbtables import BOND_REFERENCEDATA_APP
from data_access.utils import tosqllist


def get_issuer_id_df(id_bb_cos):
    ids = tosqllist([int(x) for x in id_bb_cos if pd.notnull(x)])
    sql = ISSUER_ID_SQL.format(f'id_bb_company IN {ids}')
    issuer_id_df = sqlutil.readdfsql(sql, app=BOND_REFERENCEDATA_APP)
    return issuer_id_df
