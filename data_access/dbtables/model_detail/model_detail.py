from common.utils.sqlutil import readdfsql, execsql
from data_access.dbtables import BETA_EST_SUBAPP as APP


def get_model_name(app):
    sql = f"select model_name from model_detail " \
          f"where release_date in (" \
          f"                        select max(release_date) from model_detail where app = '{app}'" \
          f")"
    df = readdfsql(sql, app=APP)
    return df.iloc[0, 0]
