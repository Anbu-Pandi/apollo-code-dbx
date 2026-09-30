import logging

import pandas as pd

from data_access.dbtables.factset import financial_data as factset_fin
from data_access.dbtables.findox import financial_data as findox_fin


def get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, dataitems=None):
    df1 = findox_fin.get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, dataitems) \
        .assign(source='findox')
    df2 = factset_fin.get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, dataitems) \
        .assign(source='factset')

    df = pd.concat([df1, df2], ignore_index=True)

    df['qtr'] = df['qtr'].str.replace(' ', '')

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    df = get_company_financials(['AAPL-US', 'META-US', 'GOOGL-US', 'MSFT-US', 'AMZN-US', 'NVDA-US', 'TSLA-US'],
                                '20220101', '20241231', 'ltm', dataitems=None)

    logging.info(df.to_string())
