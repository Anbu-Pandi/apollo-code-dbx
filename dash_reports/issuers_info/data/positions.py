import logging

import pandas as pd
import datetime
from pandas.api.types import is_numeric_dtype
from data_access.utils import tosqllist
from common.utils.dfutil import tolist
from common.utils import sqlutil

EDW_POSITIONS_SQL = '''
select * from edw.f_get_positions('{0}', '{1}', '{2}', '{3}')
'''

ISG_POSITIONS_SQL = '''
select * from isg.f_get_positions('{0}', '{1}', '{2}', '{3}')
'''


def get_positions_for_issuer_securities(fromdt, todt, tickers, instrument_id, debt_type):
    instrument_id = tolist(instrument_id, replacenone=True)
    tickers = tolist(tickers, replacenone=True)

    query_ids = ",".join([str(x) for x in instrument_id + tickers if pd.notnull(x)])
    if len(query_ids) == 0: query_ids = '#n/a'
    edw_sql = EDW_POSITIONS_SQL.format(fromdt, todt, query_ids, debt_type)
    isg_sql = ISG_POSITIONS_SQL.format(fromdt, todt, query_ids, debt_type)

    edw_df = sqlutil.readdfsql(edw_sql)
    isg_df = sqlutil.readdfsql(isg_sql)
    df = pd.concat([isg_df, edw_df], ignore_index=True)
    return df


def get_positions_agg_by_instrument_id(fromdt, todt, tickers, instrument_ids, debt_type):
    try:
        positions_df = get_positions_for_issuer_securities(fromdt, todt, tickers, instrument_ids, debt_type)
        positions_df = positions_df.reset_index(drop=True)

        def agg_func(x):
            if is_numeric_dtype(x.dtype):
                if x.name in ['price_local']:
                    return x.mean()
                return x.sum()

            return ','.join(sorted(x.unique()))

        positions_df['as_of_date'] = pd.to_datetime(positions_df['as_of_date']).dt.date
        positions_df = positions_df.groupby(['as_of_date', 'instrument_id']).agg(agg_func).reset_index()
        if len(positions_df) > 0:
            positions_df = positions_df.sort_values('as_of_date', ascending=False)

        return positions_df
    except:
        logging.error(f'error getting positions: {(fromdt, todt, tickers, instrument_ids, debt_type)}')
        return pd.DataFrame()
