from datetime import datetime

from data_access.validation.helpers import ratepct_tsdict, ratebps_tsdict, qty_tsdict, return_tsdict, weights_tsdict
from data_access.validation.validate_template import Validator

SPLOANINDEX_TSV_PARAMS = dict(
    datecol='asof_date',
    groupbycol=['facility_id'],
    cols=dict(
        original_amount=qty_tsdict,
        current_outstanding=qty_tsdict,
        libor_spread=ratebps_tsdict,
        original_libor_spread=ratebps_tsdict,
        weights=weights_tsdict,
        market_return=return_tsdict,
        interest_return=return_tsdict,
        total_return=return_tsdict,
        libor_floor=ratepct_tsdict
    )
)

SPLOANINDEX_SCHEMA = {'allowempty': False,
                      'uniquecols': ['asof_date', 'facility_id'],
                      'forcetypes': False,
                      'cols': {'id': {'ismandatory': True,
                                      'allownans': False,
                                      'isunique': False,
                                      'dtypes': (float,),
                                      'description': 'ID'},
                               'asof_date': {'ismandatory': True,
                                              'allownans': False,
                                              'isunique': False,
                                              'dtypes': (datetime,),
                                              'description': 'AS_OF_DATE'},
                               'issuer': {'ismandatory': True,
                                          'allownans': False,
                                          'isunique': False,
                                          'dtypes': (str,),
                                          'description': 'ISSUER'},
                               'facility_name': {'ismandatory': True,
                                                 'allownans': False,
                                                 'isunique': False,
                                                 'dtypes': (str,),
                                                 'description': 'FACILITY_NAME'},
                               'maturity_date': {'ismandatory': True,
                                                 'allownans': False,
                                                 'isunique': False,
                                                 'dtypes': (datetime,),
                                                 'description': 'MATURITY_DATE'},
                               'original_amount': {'ismandatory': True,
                                                   'allownans': False,
                                                   'isunique': False,
                                                   'dtypes': (float,),
                                                   'description': 'ORIGINAL_AMOUNT'},
                               'current_outstanding': {'ismandatory': True,
                                                       'allownans': False,
                                                       'isunique': False,
                                                       'dtypes': (float,),
                                                       'description': 'CURRENT_OUTSTANDING'},
                               'libor_spread': {'ismandatory': True,
                                                'allownans': False,
                                                'isunique': False,
                                                'dtypes': (float,),
                                                'description': 'LIBOR_SPREAD'},
                               'original_libor_spread': {'ismandatory': True,
                                                         'allownans': False,
                                                         'isunique': False,
                                                         'dtypes': (float,),
                                                         'description': 'ORIGINAL_LIBOR_SPREAD'},
                               'launch_date': {'ismandatory': True,
                                               'allownans': False,
                                               'isunique': False,
                                               'dtypes': (datetime,),
                                               'description': 'LAUNCH_DATE'},
                               'initial_rating': {'ismandatory': True,
                                                  'allownans': False,
                                                  'isunique': False,
                                                  'dtypes': (str,),
                                                  'description': 'INITIAL_RATING'},
                               'current_rating': {'ismandatory': True,
                                                  'allownans': False,
                                                  'isunique': False,
                                                  'dtypes': (str,),
                                                  'description': 'CURRENT_RATING'},
                               'lcd_broad_industry': {'ismandatory': True,
                                                      'allownans': False,
                                                      'isunique': False,
                                                      'dtypes': (str,),
                                                      'description': 'LCD_BROAD_INDUSTRY'},
                               'narrow_industry': {'ismandatory': True,
                                                   'allownans': False,
                                                   'isunique': False,
                                                   'dtypes': (str,),
                                                   'description': 'NARROW_INDUSTRY'},
                               'weights': {'ismandatory': True,
                                           'allownans': False,
                                           'isunique': False,
                                           'dtypes': (float,),
                                           'description': 'WEIGHTS'},
                               'market_return': {'ismandatory': True,
                                                 'allownans': False,
                                                 'isunique': False,
                                                 'dtypes': (float,),
                                                 'description': 'MARKET_RETURN'},
                               'interest_return': {'ismandatory': True,
                                                   'allownans': False,
                                                   'isunique': False,
                                                   'dtypes': (float,),
                                                   'description': 'INTEREST_RETURN'},
                               'total_return': {'ismandatory': True,
                                                'allownans': False,
                                                'isunique': False,
                                                'dtypes': (float,),
                                                'description': 'TOTAL_RETURN'},
                               'currency': {'ismandatory': True,
                                            'allownans': False,
                                            'isunique': False,
                                            'dtypes': (str,),
                                            'description': 'CURRENCY'},
                               'country': {'ismandatory': True,
                                           'allownans': False,
                                           'isunique': False,
                                           'dtypes': (str,),
                                           'description': 'COUNTRY'},
                               'region': {'ismandatory': True,
                                          'allownans': False,
                                          'isunique': False,
                                          'dtypes': (str,),
                                          'description': 'REGION'},
                               'facility_id': {'ismandatory': True,
                                               'allownans': False,
                                               'isunique': False,
                                               'dtypes': (int,),
                                               'description': 'FACILITY_ID'},
                               'pricing_id': {'ismandatory': True,
                                              'allownans': False,
                                              'isunique': False,
                                              'dtypes': (str,),
                                              'description': 'PRICING_ID'},
                               'sponsored': {'ismandatory': True,
                                             'allownans': False,
                                             'isunique': False,
                                             'dtypes': (str,),
                                             'description': 'SPONSORED'},
                               'ccr_rating': {'ismandatory': True,
                                              'allownans': False,
                                              'isunique': False,
                                              'dtypes': (str,),
                                              'description': 'CCR_RATING'},
                               'libor_floor': {'ismandatory': True,
                                               'allownans': False,
                                               'isunique': False,
                                               'dtypes': (float,),
                                               'description': 'LIBOR_FLOOR'},
                               'fl_sl': {'ismandatory': True,
                                         'allownans': False,
                                         'isunique': False,
                                         'dtypes': (str,),
                                         'description': 'FL_SL'},
                               'covenant_light': {'ismandatory': True,
                                                  'allownans': False,
                                                  'isunique': False,
                                                  'dtypes': (str,),
                                                  'description': 'COVENANT_LIGHT'}}}

validate = Validator('sp_loan_index', SPLOANINDEX_TSV_PARAMS, SPLOANINDEX_SCHEMA)

if __name__ == '__main__':
    from data_access.spark.api import sparkmanager
    from common.utils.envsetup import setuplogger
    from apo_config.config import config
    from data_access.spark.dbtables.sp_loan_index.api import get_constituents
    import logging
    from datetime import timedelta

    import pandas as pd


    def dateadd(dt, days):
        dt = pd.to_datetime(dt)
        enddt = dt + timedelta(days=days)
        return enddt


    setuplogger('sp_loan_index')
    config.init('prod')

    sparkmanager.init()

    fromdt = '20210210'
    todt = fromdt
    datefilters = {'from_yyyymmdd': dateadd(fromdt, -31).strftime('%Y%m%d'), 'to_yyyymmdd': todt}

    df = get_constituents(datefilters)

    errs = validate.validatedf(df, fromdate=fromdt, todate=todt).toPandas()
    logging.info(errs.head())

    logging.info('completed')

    sparkmanager.stop()

    pass
