import logging

from common.utils import dtutil, sqlutil
from data_access.dbtables import MARKIT_CDS_PRICING_APP
from data_access.utils import get_where_clause, join_where_clauses

MARKIT_SQL = '''
select * from markit_cds
WHERE
{}
'''


def get_cds_curve(fromdt, todt, tickers, ccy='USD', tier='SNRFOR', doc_clause=None, tenor=None, raw_df=False):
    where_clause = '''(TRY_CAST(dp as DOUBLE) is NOT NULL) AND asof_date BETWEEN '{}' AND '{}' '''.format(dtutil.todate(fromdt),
                                                                                     dtutil.todate(todt))
    tkr_clause = get_where_clause('ticker', tickers)
    tenor_clause = get_where_clause('tenor', tenor)
    ccy_clause = get_where_clause('ccy', ccy)
    tier_clause = get_where_clause('tier', tier)
    doc_clause_clause = get_where_clause('doc_clause', doc_clause)
    where_clause = join_where_clauses(' AND ', [where_clause, tkr_clause, ccy_clause, tier_clause, doc_clause_clause,
                                                tenor_clause])
    sql = MARKIT_SQL.format(where_clause)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_PRICING_APP)

    primary_curve_order = {'Y': 1}
    primary_coupon_order = {'Y': 1}
    doc_clause_order = {'CR14': 0, 'XR14': 1, 'MR14': 2}
    df['temp1'] = df['primary_coupon'].map(lambda x: primary_curve_order.get(x, -1))
    df['temp2'] = df['primary_curve'].map(lambda x: primary_coupon_order.get(x, -1))
    df['temp3'] = df['doc_clause'].map(lambda x: doc_clause_order.get(x, -1))
    df = df.sort_values(by=['asof_date', 'ticker', 'primary_curve', 'temp1', 'temp2', 'temp3']) \
        .drop(columns=['temp1', 'temp2', 'temp3'])
    uncalib_rows = (df['conv_spread'] == 0)
    df.loc[uncalib_rows, 'assumed_recovery'] = df.loc[uncalib_rows, 'real_recovery']
    if not raw_df:
        df.drop_duplicates(subset=['asof_date', 'ticker', 'tenor'], keep='last', inplace=True)

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init('prod')

    df = get_cds_curve('2023-03-01', '2023-03-06', 'IBM')
    logging.info(df)
    pass
