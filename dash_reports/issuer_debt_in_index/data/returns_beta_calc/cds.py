import logging

import pandas as pd

from common.utils import sqlutil, dtutil
from common.utils.sqlutil import sql_replace_escape_char
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.utils import get_period_dates, get_period_returns
from dash_reports.issuer_debt_in_index.data.sql_const import CDS_PRICE_SQL, ASOF_DATE_IN_CLAUSE
from data_access.dbtables import MARKIT_CDS_PRICING_APP
from data_access.utils import tosqllist


def get_cds_returns(asof_date, ccys, tenor):
    asof_date = dtutil.todate(asof_date)
    dts = tosqllist([asof_date] + get_period_dates(asof_date).values.tolist())
    cds_df = sqlutil.readdfsql(
        sql_replace_escape_char(CDS_PRICE_SQL).format(tosqllist(tenor), tosqllist(ccys), ASOF_DATE_IN_CLAUSE.format(dts)),
        app=MARKIT_CDS_PRICING_APP) \
        .sort_values('asof_date')
    cds_df.set_index('asof_date', inplace=True)
    cds_df.index = pd.to_datetime(cds_df.index)
    cds_df = cds_df.groupby(['ticker', 'red_code', 'tier', 'currency', 'doc_clause', 'tenor'])['price'] \
        .apply(lambda x: get_period_returns(x, asof_date)).unstack()
    return cds_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    df = get_cds_returns('2022-05-31')
    logging.info(df)
