import logging

from common.utils import dtutil, sqlutil
from data_access.dbtables import GS_MARKETDATA_APP

FX_SQL = '''
SELECT *
FROM (
SELECT asof_date, replace(ccy_pair,'USD','') as ccy_pair, 
CASE WHEN ccy_pair LIKE '%USD' THEN avg(spot)
ELSE 1 / avg(spot)
END AS spot,
ROW_NUMBER() OVER (PARTITION BY ccy_pair
ORDER BY asof_date DESC) AS row_num
FROM fx_forward_data
WHERE asof_date <= '{}'
GROUP BY asof_date, ccy_pair
) AS t
WHERE row_num = 1
'''

FX_FWD_SQL = '''
select
  asof_date, 
  ccy_pair, 
  fwd_tenor, 
  fwd_tenor_date, 
  ((forward_rate / spot - 1) / (DATEDIFF(fwd_tenor_date, asof_date) / 365.00)) * 1e4 AS fx_rate_diff, 
  forward_rate AS fx_fwd, 
  spot AS fx_spot 
from fx_forward_data
where 
asof_date between '{}' and '{}'
order by asof_date desc, ccy_pair, fwd_tenor_date
'''


def get_fx(dt):
    sql = FX_SQL.format(dtutil.todate(dt))
    df = sqlutil.readdfsql(sql, app=GS_MARKETDATA_APP)
    return df.set_index('ccy_pair')['spot'].to_dict()


def get_fx_fwd(fromdt, todt):
    sql = FX_FWD_SQL.format(dtutil.todate(fromdt), dtutil.todate(todt))
    df = sqlutil.readdfsql(sql,app=GS_MARKETDATA_APP)
    return df


if __name__ == '__main__':
    from apo_config.config import config
    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('prod')
    logging.info(get_fx_fwd('2024-07-06','2024-08-06'))
