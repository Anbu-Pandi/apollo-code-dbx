import datetime

from data_access.validation.helpers import price_tsdict, ratepct_tsdict, ratebps_tsdict, qty_tsdict
from data_access.validation.validate_template import Validator

TRACE_TSV_PARAMS = dict(
    datecol='trace_datetime',
    groupbycol=['issue_id'],
    cols={
        'price': price_tsdict,
        'yield': ratepct_tsdict,
        'oas_spread': ratebps_tsdict,
        'par_spread': ratebps_tsdict,
        'mkt_spread': ratebps_tsdict,
        'i_spread': ratebps_tsdict,
        'coupon': dict(change_threshold=.001),
        'quantity': qty_tsdict,
        'estimated_quantity': qty_tsdict
    }
)

TRACESCHEMA = {
    'allowempty': False,
    'uniquecols': ['issue_price_id', 'report_date'],
    'forcetypes': False,
    'cols': {
        'apo_transaction_id': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                               'description': 'APO_TRANSACTIONID'},
        'issue_price_id': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                           'description': 'issue_price_id'},
        'sequence_number': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                            'description': 'SEQUENCENUMBER'},
        'original_sequence_number': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                                     'description': 'ORIGINALSEQUENCENUMBER'},
        'issue_id': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                     'description': 'issue_id'},
        'cusip': {'ismandatory': True, 'allownans': True, 'isunique': False,
                  'dtypes': ('r:^[A-Z0-9]{3}[a-zA-Z0-9]{2}[a-zA-Z0-9*@#]{3}[0-9]$',),
                  'description': 'CUSIP'},
        'isin': {'ismandatory': True, 'allownans': True, 'isunique': False,
                 'dtypes': ('r:^[a-zA-Z]{2}[a-zA-Z0-9]{9}[0-9]$',),
                 'description': 'ISIN'},
        'nasd_symbol': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                        'description': 'NASDSYMBOL'},
        'price_change_code': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (int,),
                              'description': 'priceCHANGECODE'},
        'apo_price_change_code': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                                  'description': 'APO_priceCHANGECODE'},
        'sales_cond_code': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('T', 'Z', 'U', 'S'),
                            'description': 'SALESCONDCODE'},
        'apo_sales_cond_code': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str),
                                'description': 'APO_SALESCONDCODE'},
        'second_modifier': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('W', 'S'),
                            'description': 'SECONDMODIFIER'},
        'apo_second_modifier': {'ismandatory': True, 'allownans': True, 'isunique': False,
                                'dtypes': (str), 'description': 'APO_SECONDMODIFIER'},
        'wi_flag': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('W', 'N'),
                    'description': 'WIFLAG'},
        'apo_wi_flag': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                        'description': 'APO_WIFLAG'},
        'commission_flag': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                            'description': 'COMMISSIONFLAG'},
        'asof_flag': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': ('A', 'R', 'S'),
                      'description': 'ASOFFLAG'},
        'apo_asof_flag': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                          'description': 'APO_ASOFFLAG'},
        'special_price_indicator': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('Y', 'N'),
                                    'description': 'SPECIALpriceINDICATOR'},
        'quantity_indicator': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': ('A', 'E'),
                               'description': 'quantityINDICATOR'},
        'apo_quantity_indicator': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (),
                                   'description': 'APO_quantityINDICATOR'},
        'seller_sale_days': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (int,),
                             'description': 'SELLERSALEDAYS'},
        'data_source': {'ismandatory': True, 'allownans': True, 'isunique': False,
                        'dtypes': ('BTDS', 'BTDS-144a', 'BTDS-144A'),
                        'description': 'DATASOURCE'},
        'price': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                  'description': 'price'},
        'yield': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                  'description': 'yield'},
        'yield_direction': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': ('-', 'S'),
                            'description': 'yieldDIRECTION'},
        'quantity': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                     'description': 'quantity'},
        'estimated_quantity': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                               'description': 'estimated_quantity'},
        'effective_date': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (datetime.date,),
                           'description': 'EFFECTIVEDATE'},
        'effective_time': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                           'description': 'EFFECTIVETIME'},
        'report_date': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (datetime.date,),
                        'description': 'report_date'},
        'report_time': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                        'description': 'REPORTTIME'},
        'halt': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('Y', 'N'),
                 'description': 'HALT'},
        'cancel_flag': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('Y', 'N'),
                        'description': 'CANCELFLAG'},
        'correction_flag': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('Y', 'N'),
                            'description': 'CORRECTIONFLAG'},
        'benchmark_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                             'description': 'BENCHMARKSPREAD'},
        'benchmark_issue_id': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                               'description': 'BENCHMARKissue_id'},
        'benchmark_cusip': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str),
                            'description': 'BENCHMARKCUSIP'},
        'benchmark_isin': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str),
                           'description': 'BENCHMARKISIN'},
        'benchmark_ticker': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str),
                             'description': 'BENCHMARKTICKER'},
        'benchmark_coupon': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                             'description': 'BENCHMARKcoupon'},
        'benchmark_maturity': {'ismandatory': True, 'allownans': True, 'isunique': False,
                               'dtypes': (datetime.date), 'description': 'BENCHMARKMATURITY'},
        'oas_spread': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                       'description': 'oas_spread'},
        'par_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                       'description': 'par_spread'},
        'mkt_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                       'description': 'mkt_spread'},
        'i_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                     'description': 'i_spread'},
        'is_suspect': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': ('Y', 'N'),
                       'description': 'IS_SUSPECT'},
        'ticker': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                   'description': 'TICKER'},
        'coupon': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (float,),
                   'description': 'coupon'},
        'maturity': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (datetime.date,),
                     'description': 'MATURITY'},
        'change_day_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                              'description': 'CHANGEDAYSPREAD'},
        'change_week_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                               'description': 'CHANGEWEEKSPREAD'},
        'change_mtd_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                              'description': 'CHANGEMTDSPREAD'},
        'trade_type': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': ('B', 'S', 'D'),
                       'description': 'TRADE_TYPE'},
        'apo_trade_type': {'ismandatory': True, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                           'description': 'APO_TRADE_TYPE'},
        'sector': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                   'description': 'SECTOR'},
        'market_segment': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str,),
                           'description': 'MARKETSEGMENT'},
        'original_report_date': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                                 'description': 'ORIGINALreport_date'},
        'g_spread': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                     'description': 'GSPREAD'},
        'settlement_date': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (datetime.date),
                            'description': 'SETTLEMENTDATE'},
        'bsym': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (str), 'description': 'BSYM'},
        'subtype': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('CORP', 'ELN', 'CHRC'),
                    'description': 'SUBTYPE'},
        'apo_subtype': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                        'description': 'APO_SUBTYPE'},
        'is_investment_grade': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('I', 'H'),
                                'description': 'ISINVESTMENTGRADE'},
        'apo_is_investment_grade': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                                    'description': 'APO_ISINVESTMENTGRADE'},
        'side': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('B', 'S'),
                 'description': 'SIDE'},
        'apo_side': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                     'description': 'APO_SIDE'},
        'reporting_party_type': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('T', 'D'),
                                 'description': 'REPORTINGPARTYTYPE'},
        'apo_reporting_party_type': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                                     'description': 'APO_REPORTINGPARTYTYPE'},
        'contra_party_type': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('T', 'D', 'A', 'C'),
                              'description': 'CONTRAPARTYTYPE'},
        'apo_contra_party_type': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                                  'description': 'APO_CONTRAPARTYTYPE'},
        'remuneration': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('C', 'M', 'N', 'S'),
                         'description': 'REMUNERATION'},
        'apo_remuneration': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (),
                             'description': 'APO_REMUNERATION'},
        'ats_indicator': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': ('Y', 'N'),
                          'description': 'ATSINDICATOR'},
        'change_day_price': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                             'description': 'CHANGEDAYprice'},
        'change_week_price': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                              'description': 'CHANGEWEEKprice'},
        'change_mtd_price': {'ismandatory': True, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                             'description': 'CHANGEMTDprice'},
        'real_quantity': {'ismandatory': False, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                          'description': 'REALquantity'},
        'net_quantity': {'ismandatory': False, 'allownans': True, 'isunique': False, 'dtypes': (float,),
                         'description': 'netmv'},
        'portfolio_cusip_count': {'ismandatory': False, 'allownans': False, 'isunique': False, 'dtypes': (int,),
                                  'description': 'portfolio_cusip_count'},
        'is_portfolio_trade': {'ismandatory': False, 'allownans': False, 'isunique': False, 'dtypes': (bool,),
                               'description': 'is_portfolio_trade'},
        'interval_code': {'ismandatory': False, 'allownans': False, 'isunique': False, 'dtypes': (str,),
                          'description': 'interval_code'},
        'trace_datetime': {'ismandatory': False, 'allownans': False, 'isunique': False,
                           'dtypes': (datetime.datetime,),
                           'description': 'trace_datetime'},
    }
}

validate = Validator('trace', TRACE_TSV_PARAMS, TRACESCHEMA, uniq_ts=False)

if __name__ == '__main__':
    from datetime import timedelta

    import pandas as pd


    def dateadd(dt, days):
        dt = pd.to_datetime(dt)
        enddt = dt + timedelta(days=days)
        return enddt


    from data_access.spark.api import sparkmanager
    from common.utils.envsetup import setuplogger
    from apo_config.config import config
    from data_access.spark.dbtables.trace.api import get_trades, add_tracedatetime
    import logging

    setuplogger('trace_validation_test')
    config.init('prod')
    sparkmanager.init()

    fromdt = '20210210'
    todt = fromdt
    datefilters = {'from_yyyymmdd': dateadd(fromdt, -31).strftime('%Y%m%d'), 'to_yyyymmdd': todt}
    tracedf = get_trades(datefilters, transform=False)
    tracedf = add_tracedatetime(tracedf)

    errs = validate.validatedf(tracedf, fromdate=fromdt, todate=todt).toPandas()
    logging.info(errs.head())

    logging.info('completed')

    sparkmanager.stop()

    pass
