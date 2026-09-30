import logging

import pandas as pd

from common.utils import sqlutil, dtutil
from common.utils.dfutil import tolist
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.const import CUSTOM_FE_FIELDS, FE_SQL, FE_ASOF_SQL, FE_DETAIL_SQL
from data_access.dbtables.factset.utils import add_custom_ff_fields, get_metrics_reqd_by_custom_ff
from data_access.utils import get_where_clause, join_where_clauses
from data_access.utils import tosqllist


def _get_custom_ff_fields(financial_items):
    custom_ff_fields = {k: v for k, v in CUSTOM_FE_FIELDS.items() if k in tolist(financial_items)}
    return custom_ff_fields


def _get_financial_items(financial_items):
    custom_ff_fields = _get_custom_ff_fields(financial_items)
    financial_items = [x for x in tolist(financial_items) if x not in custom_ff_fields]
    metrics_reqd_by_custom_ff = get_metrics_reqd_by_custom_ff([v[0] for _, v in custom_ff_fields.items()],
                                                              CUSTOM_FE_FIELDS)
    return financial_items + metrics_reqd_by_custom_ff


PERIOD_TYPE_MAP = {'q': 'QF', 'a': 'AF', 's': 'SAF'}


def _resolve_period_type(period_type):
    if not period_type:
        return 'QF'
    return PERIOD_TYPE_MAP[period_type.lower()[0]]


def get_estimates(fromdt, todt, financial_items, tickers=None, entity_ids=None, rel_per=None, period_type=None):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    ticker_clause = get_where_clause('tr.ticker_region', tickers)
    fe_id_clause = get_where_clause('ec.factset_entity_id', entity_ids)
    rel_per_clause = get_where_clause('f1.FE_PER_REL', rel_per)
    if rel_per_clause is not None:
        rel_per_clause += ' AND CONS_END_DATE IS NULL'

    where_clause = join_where_clauses(' OR ', [ticker_clause, fe_id_clause])
    where_clause = join_where_clauses(' AND ', [rel_per_clause, where_clause])

    suffix = _resolve_period_type(period_type)
    sql = FE_SQL.format(fromdt, todt, where_clause, tosqllist(_get_financial_items(financial_items)), suffix)
    df = sqlutil.readdfsql(sql, app=APP)

    df = add_custom_ff_fields(df, _get_custom_ff_fields(financial_items), 'FE_ITEM',
                              ['FE_ITEM_DESC'],
                              'FE_MEAN',
                              ['TICKER_REGION', 'PROPER_NAME'],
                              ['CONS_START_DATE', 'CONS_END_DATE', 'FE_FP_END', 'FE_PER_REL'],
                              other_field_name_cols=[],
                              other_field_value_cols=[])
    return df


def get_estimates_history(fromdt, todt, financial_items, tickers=None, entity_ids=None, rel_per=None, period_type=None):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    ticker_clause = get_where_clause('tr.ticker_region', tickers)
    fe_id_clause = get_where_clause('ec.factset_entity_id', entity_ids)
    rel_per_clause = get_where_clause('f1.FE_PER_REL', rel_per)

    where_clause = join_where_clauses(' OR ', [ticker_clause, fe_id_clause])
    where_clause = join_where_clauses(' AND ', [rel_per_clause, where_clause])

    suffix = _resolve_period_type(period_type)
    sql = FE_ASOF_SQL.format(fromdt, todt, where_clause, tosqllist(_get_financial_items(financial_items)), suffix)
    df = sqlutil.readdfsql(sql, app=APP)

    df = add_custom_ff_fields(df, _get_custom_ff_fields(financial_items), 'FE_ITEM',
                              ['FE_ITEM_DESC'],
                              'FE_MEAN',
                              ['TICKER_REGION', 'PROPER_NAME'],
                              ['CONS_START_DATE', 'CONS_END_DATE', 'FE_FP_END', 'FE_PER_REL'],
                              other_field_name_cols=[],
                              other_field_value_cols=[])

    # Expand to daily ASOF_DATE time series from CONS_START_DATE to CONS_END_DATE
    # For rows where CONS_END_DATE is null (most recent consensus), use min(today, todt)
    effective_end = min(dtutil.today(), todt)
    df['ASOF_DATE'] = df.apply(
        lambda r: pd.date_range(
            r['CONS_START_DATE'],
            r['CONS_END_DATE'] if pd.notna(r['CONS_END_DATE']) else effective_end,
            freq='D'
        ).date.tolist(),
        axis=1
    )
    df = df.explode('ASOF_DATE').reset_index(drop=True)

    return df


def get_recent_estimate_detail(financial_items, tickers=None, entity_ids=None):
    ticker_clause = get_where_clause('tr.ticker_region', tickers)
    fe_id_clause = get_where_clause('ec.factset_entity_id', entity_ids)
    where_clause = join_where_clauses(' OR ', [ticker_clause, fe_id_clause])

    sql = FE_DETAIL_SQL.format(where_clause, tosqllist(_get_financial_items(financial_items)))
    df = sqlutil.readdfsql(sql, app=APP)

    df = add_custom_ff_fields(df, _get_custom_ff_fields(financial_items), 'FE_ITEM',
                              ['FE_ITEM_DESC'],
                              'ESTIMATE_VALUE',
                              ['TICKER_REGION', 'PROPER_NAME', 'FACTSET_PERSON_ID', 'PERSON_NAME', 'FACTSET_BROKER_ID',
                               'BROKER_NAME'],
                              ['CONS_START_DATE', 'CONS_END_DATE', 'FE_FP_END', 'FE_PER_REL', 'ESTIMATE_DATE'],
                              other_field_name_cols=['formula_metric'],
                              other_field_value_cols=['dataitem_value'])
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    df = get_estimates(dtutil.dateadd(dtutil.today(), '-5y'),dtutil.today(),  ['SALES'],
                       #entity_ids=['000C7F-E', '000Q07-E', '001MF1-E', '00208X-E', '006XY7-E', '0077YX-E', '0FPWZZ-E'],
                       tickers=['SNOW-US']
                       #rel_per=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
                        )
    df1 = get_estimates_history(dtutil.dateadd(dtutil.today(), '-5y'),dtutil.today(),  ['SALES'],
                       #entity_ids=['000C7F-E', '000Q07-E', '001MF1-E', '00208X-E', '006XY7-E', '0077YX-E', '0FPWZZ-E'],
                       tickers=['SNOW-US'],
                       rel_per=[5]
                        )
    logging.info(df1.to_string())
