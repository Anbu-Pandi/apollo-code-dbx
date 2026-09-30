from common.utils.dtutil import todate
from common.utils.sqlutil import readdfsql
from . import APP


def get_spx_data(fromdt, todt):
    sql = '''
    SELECT b.conm AS index_name,
        a.gvkeyx AS gvkey_index,
        a.datadate AS asof_date,
        a.prccd AS px_close,
        a.prccddiv AS px_close_div_adjusted,
        a.prchd AS px_high,
        a.prcld AS px_low
    FROM idx_daily a
    JOIN idx_index b ON a.gvkeyx = b.gvkeyx
    WHERE b.conm = 'S&P 500 Comp-Ltd'
        AND a.datadate BETWEEN '{}' AND '{}'
    ORDER BY a.datadate DESC
    '''

    df = readdfsql(sql.format(todate(fromdt), todate(todt)), app=APP)
    return df
