import numpy as np
import logging

from common.utils.sqlutil import readdfsql
from data_access.dbtables import ANALYST_QUEST_APP
from data_access.utils import tosqllist


def _analyst_override_data(company_id, metrics):
    query = f''' select * 
                from analyst_data_override
                where company_id = {company_id} and
                metric in {tosqllist(metrics)}
            '''

    df = readdfsql(query, app=ANALYST_QUEST_APP)

    return df


def get_latest_override_df(company_id, metrics):
    df = _analyst_override_data(company_id, np.unique(metrics).tolist())
    if not df.empty:
        df = df.set_index(['company_id', 'company_name', 'metric', 'source_col', 'last_update_ts'])

        df = df['updated_value']
        df = df.unstack('last_update_ts')
        df = df.sort_index(ascending=False, axis=1)
        df = df.fillna(method='bfill', axis=1)
        df = df.iloc[:, 0]
        df.name = 'value'
        df = df.reset_index()

    return df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')

    company_name = "Foot Locker, Inc."  # "Macy's, Inc."
    company_id = 315394
    metric_list = ['Cash And Equivalents', 'EBITDA, Lease Adjusted', 'Net Debt', 'Net Debt / EBITDA',
                   'Outstanding Balance for Revolving Credit - Total', 'Senior Bonds and Notes', 'Total Bank Debt',
                   'Total Debt', 'Total Debt / EBITDA', 'Total Lease Liabilities (Incl. Curr. Portion)',
                   'Total Senior Debt', 'Total Senior Secured Debt', 'Total Undrawn Credit', 'Unsecured Debt',
                   'Variable Rate Debt']

    df = get_latest_override_df(company_id, metric_list)
    print(df)
