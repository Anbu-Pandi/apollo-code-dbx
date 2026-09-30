import hashlib
import json
import logging
import numbers

import pandas as pd

from common.utils import dtutil, sqlutil
from common.utils.dfutil import tolist
from dash_reports.risk_query.data.queries import RISK_QUERY_SQL, SUMMARY_RISK_QUERY_SQL, RISKMETRIC_COL_AGG_MAP, \
    _get_col_str, _get_where_clause, _get_join_on, _get_sort_clause, _get_sel_clause, _get_result_sel_clause
from data_access.dbtables import BETA_EST_SUBAPP
from data_access.storage import SharedFrameCache

_shared_cache = None
_CACHE_SECONDS = 15 * 60


def _cache():
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = SharedFrameCache.local()
    return _shared_cache

# Function to combine duplicated columns intelligently
def combine_first_columns(df):
    columns_dict = {}
    # Iterate over each column by index to ensure dealing with Series
    for i in range(df.shape[1]):
        column_name = df.columns[i]
        column_series = df.iloc[:, i]
        # If the column name already exists in the dictionary, combine it with the new column
        if column_name in columns_dict:
            columns_dict[column_name] = columns_dict[column_name].combine_first(column_series)
        else:
            # Otherwise, simply add the new column to the dictionary
            columns_dict[column_name] = column_series

    # Create a new DataFrame from the dictionary of combined columns
    combined_df = pd.DataFrame(columns_dict)
    return combined_df


def get_query_results(fromdt, todt, cols, gby, filters, keep_contribution_column, summarize=False):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    cols = tolist(cols)
    gby = tolist(gby)
    cache_payload = json.dumps(
        [str(fromdt), str(todt), cols, gby, filters, keep_contribution_column, summarize],
        sort_keys=True,
        default=str,
    )
    cache_key = hashlib.sha256(cache_payload.encode()).hexdigest()
    cached = _cache().read(
        "risk_query",
        dtutil.today(use_bday=False),
        cache_key,
        "result",
        max_age_seconds=_CACHE_SECONDS,
    )
    if cached is not None:
        return cached

    where_clause = _get_where_clause(filters)
    pnl_col_str, risk_col_str = _get_col_str(cols, keep_contribution_column)
    sel_clause = _get_sel_clause(gby)
    join_on_str = _get_join_on(gby)
    sort_clause = _get_sort_clause(gby)
    col_str = lambda x: ',' + ',\n'.join(x) if len(x) > 0 else ''
    base_query = SUMMARY_RISK_QUERY_SQL if summarize else RISK_QUERY_SQL
    result_sel_clause = '*' if summarize else _get_result_sel_clause(gby, pnl_col_str, risk_col_str)
    sql = base_query.format(fromdt, todt, where_clause, sel_clause,
                            col_str(pnl_col_str),
                            col_str(risk_col_str),
                            join_on_str,
                            sort_clause,
                            result_sel_clause)

    df = sqlutil.readdfsql(sql, app=BETA_EST_SUBAPP)
    df = combine_first_columns(df)
    if not summarize:
        df = df.sort_values('asof_date')
        ret_cols = ['asof_date'] + gby + cols
    else:
        ret_cols = cols

    for c in cols:
        if c not in RISKMETRIC_COL_AGG_MAP and c.startswith('cum_'):
            if summarize or len(df) == 0 or len(gby) == 0:
                df[c] = df.fillna(0)[c.replace('cum_', '')].astype(float).cumsum()
            else:
                df[c] = df.fillna(0).groupby(gby)[c.replace('cum_', '')].cumsum()

    df = df[ret_cols]
    if not summarize:
        df = df.sort_values(['asof_date'] + cols,
                            ascending=[False] + [False] * len(cols),
                            key=lambda x: [abs(x_) if isinstance(x_, numbers.Number) else x_ for x_ in x])

    _cache().write(
        "risk_query",
        dtutil.today(use_bday=False),
        cache_key,
        "result",
        df,
    )
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    fromdt = '2022-01-01'
    todt = '2022-08-31'

    cols = ['daily_pnl_bps', 'beta']
    gby = ['fund_entity_code']

    filter = {'fund_entity_code': ['CSTRAT']}

    df = get_query_results(fromdt, todt, cols, gby, filter, summarize=False)
    logging.info(df)
