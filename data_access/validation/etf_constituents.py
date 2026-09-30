from datetime import datetime

from data_access.validation.helpers import qty_tsdict, weights_tsdict, notional_tsdict
from data_access.validation.validate_template import Validator

ETF_TSV_PARAMS = dict(
    datecol='asof_date',
    groupbycol=['composite_ticker', 'constituent_ticker', 'constituent_name'],
    cols=dict(
        weight=weights_tsdict,
        market_value=notional_tsdict,
        shares_held=qty_tsdict
    )
)

ETF_SCHEMA = {'allowempty': False,
              'uniquecols': ['etfg_date',
                             'asof_date',
                             'composite_ticker',
                             'constituent_ticker',
                             'constituent_name',
                             'cusip',
                             'isin'],
              'forcetypes': True,
              'cols': {'etfg_date': {'ismandatory': True,
                                     'allownans': False,
                                     'isunique': False,
                                     'dtypes': (datetime,),
                                     'description': 'etfg_date'},
                       'asof_date': {'ismandatory': True,
                                      'allownans': False,
                                      'isunique': False,
                                      'dtypes': (datetime,),
                                      'description': 'as_of_date'},
                       'composite_ticker': {'ismandatory': True,
                                            'allownans': False,
                                            'isunique': False,
                                            'dtypes': (str,),
                                            'description': 'Composite_Ticker'},
                       'constituent_ticker': {'ismandatory': True,
                                              'allownans': True,
                                              'isunique': False,
                                              'dtypes': (str,),
                                              'description': 'Constituent_Ticker'},
                       'constituent_name': {'ismandatory': True,
                                            'allownans': True,
                                            'isunique': False,
                                            'dtypes': (str,),
                                            'description': 'Constituent_Name'},
                       'weight': {'ismandatory': True,
                                  'allownans': True,
                                  'isunique': False,
                                  'dtypes': (float,),
                                  'description': 'Weight'},
                       'market_value': {'ismandatory': True,
                                        'allownans': True,
                                        'isunique': False,
                                        'dtypes': (float,),
                                        'description': 'Market_Value'},
                       'cusip': {'ismandatory': True,
                                 'allownans': True,
                                 'isunique': False,
                                 'dtypes': (str,),
                                 'description': 'Cusip'},
                       'isin': {'ismandatory': True,
                                'allownans': True,
                                'isunique': False,
                                'dtypes': (str,),
                                'description': 'Isin'},
                       'figi': {'ismandatory': True,
                                'allownans': True,
                                'isunique': False,
                                'dtypes': (str,),
                                'description': 'Figi'},
                       'sedol': {'ismandatory': True,
                                 'allownans': True,
                                 'isunique': False,
                                 'dtypes': (str,),
                                 'description': 'Sedol'},
                       'country_of_exchange': {'ismandatory': True,
                                               'allownans': True,
                                               'isunique': False,
                                               'dtypes': (str,),
                                               'description': 'Country_of_Exchange'},
                       'exchange': {'ismandatory': True,
                                    'allownans': True,
                                    'isunique': False,
                                    'dtypes': (),
                                    'description': 'Exchange'},
                       'shares_held': {'ismandatory': True,
                                       'allownans': True,
                                       'isunique': False,
                                       'dtypes': (float,),
                                       'description': 'Shares_Held'},
                       'asset_class': {'ismandatory': True,
                                       'allownans': True,
                                       'isunique': False,
                                       'dtypes': (str,),
                                       'description': 'Asset_Class'},
                       'security_type': {'ismandatory': True,
                                         'allownans': True,
                                         'isunique': False,
                                         'dtypes': (str,),
                                         'description': 'Security_Type'},
                       'currency_traded': {'ismandatory': True,
                                           'allownans': True,
                                           'isunique': False,
                                           'dtypes': (str,),
                                           'description': 'Currency_Traded'}}}

validate = Validator('etf_constituents', ETF_TSV_PARAMS, ETF_SCHEMA, uniq_ts=False)

if __name__ == '__main__':
    from data_access.spark.api import sparkmanager
    from common.utils.envsetup import setuplogger
    from apo_config.config import config
    from data_access.spark.dbtables.etf_constituents.api import get_constituents
    import logging

    setuplogger('etf_constituents')
    config.init('prod')

    sparkmanager.init()

    dates = {'from_yyyymmdd': 20230923, 'to_yyyymmdd': 20231023}
    df = get_constituents(dates)

    errs = validate.validatedf(df, fromdate='20231023').toPandas()
    logging.info(errs.head())
    logging.info(errs.shape)

    logging.info('completed')

    sparkmanager.stop()

    pass
