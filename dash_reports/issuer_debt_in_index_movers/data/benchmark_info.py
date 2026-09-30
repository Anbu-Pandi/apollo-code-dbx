from common.utils.sqlutil import readdfsql
from data_access.dbtables import JPM_DQ_APP


def get_index_desc():
    sql = 'select * from benchmark_info'
    index_desc = readdfsql(sql, app=JPM_DQ_APP)
    return index_desc.drop_duplicates(subset=['benchmark_ticker']).set_index('benchmark_ticker')['col_name']
