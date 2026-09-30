from datetime import datetime

from data_access.validation.helpers import price_tsdict
from data_access.validation.validate_template import Validator

MARKITPRICINGLOAN_TSV_PARAMS = dict(
    datecol='mark_date',
    groupbycol=['loanx_id'],
    cols={'bid': price_tsdict,
          'offer': price_tsdict,
          'evaluated_price': price_tsdict,
          'close_bid': price_tsdict,
          'close_offer': price_tsdict
          }
)
MARKITPRICINGLOAN_SCHEMA = {'allowempty': False,
                            'uniquecols': ['mark_date', 'loanx_id'],
                            'forcetypes': False,
                            'cols': {'loanx_id': {'ismandatory': True,
                                                  'allownans': False,
                                                  'isunique': False,
                                                  'dtypes': (str,),
                                                  'description': 'LoanX ID'},
                                     'mark_date': {'ismandatory': True,
                                                   'allownans': False,
                                                   'isunique': False,
                                                   'dtypes': (datetime,),
                                                   'description': 'Mark Date'},
                                     'bid': {'ismandatory': True,
                                             'allownans': False,
                                             'isunique': False,
                                             'dtypes': (float,),
                                             'description': 'Bid'},
                                     'offer': {'ismandatory': True,
                                               'allownans': False,
                                               'isunique': False,
                                               'dtypes': (float,),
                                               'description': 'Offer'},
                                     'depth': {'ismandatory': True,
                                               'allownans': False,
                                               'isunique': False,
                                               'dtypes': (str,),
                                               'description': 'Depth'},
                                     'evaluated_price': {'ismandatory': True,
                                                         'allownans': False,
                                                         'isunique': False,
                                                         'dtypes': (float,),
                                                         'description': 'Evaluated Price'},
                                     'close_bid': {'ismandatory': True,
                                                   'allownans': False,
                                                   'isunique': False,
                                                   'dtypes': (float,),
                                                   'description': 'Close Bid'},
                                     'close_offer': {'ismandatory': True,
                                                     'allownans': False,
                                                     'isunique': False,
                                                     'dtypes': (float,),
                                                     'description': 'Close Offer'},
                                     'close_date': {'ismandatory': True,
                                                    'allownans': False,
                                                    'isunique': False,
                                                    'dtypes': (datetime,),
                                                    'description': 'Close Date'},
                                     'contributed': {'ismandatory': True,
                                                     'allownans': False,
                                                     'isunique': False,
                                                     'dtypes': (str,),
                                                     'description': 'Contributed'}}}

validate = Validator('markit_pricing_loan_index', MARKITPRICINGLOAN_TSV_PARAMS, MARKITPRICINGLOAN_SCHEMA)

if __name__ == '__main__':
    from data_access.spark.api import sparkmanager
    from common.utils.envsetup import setuplogger
    from apo_config.config import config
    from data_access.spark.dbtables.markit_loan_pricing.api import get_loan_pricing
    import logging

    setuplogger('markit_loan_pricing')
    config.init('prod')

    sparkmanager.init()

    dates = {'from_yyyymmdd': 20201201, 'to_yyyy': '2021'}
    df = get_loan_pricing(dates)

    errs = validate.validatedf(df, fromdate='20210201').toPandas()
    logging.info(errs.head())
    logging.info(errs.shape)

    logging.info('completed')

    sparkmanager.stop()

    pass
