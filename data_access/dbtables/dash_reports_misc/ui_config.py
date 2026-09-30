from common.utils.sqlutil import insertdf_batch, execsql
from data_access.dbtables import UI_CONFIG_APP
from data_access.spark.azuredb import AzureDB


def get_ui_config():
    azdb = AzureDB(local_dir=None)
    sql = f"select * from dbo.ui_config"
    df = azdb.read_sql(sql)
    return df


def delete_ui_config(user, page):
    execsql(f"DELETE FROM dbo.ui_config where user_id='{user}' and page='{page}'", app=UI_CONFIG_APP)


def save_ui_config(df):
    if len(df) == 1:
        delete_ui_config(df.iloc[0]['user_id'], df.iloc[0]['page'])
        insertdf_batch('dbo.ui_config', df, app=UI_CONFIG_APP)

