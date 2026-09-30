import datetime
import logging
import time
import traceback

from dash_reports.analyst_questionnaire.data import issuers
from data_access.dbtables import ANALYST_QUEST_APP
from data_access.dbtables.analyst_questionnaire.analyst_response import get_analyst_questions

from data_access.dbtables.factset.ratings_data import get_ratings_by_company_id
from data_access.dbtables.factset.company_data import get_peer_companies
from data_access.dbtables.factset.pricing_returns import get_pricing
from data_access.dbtables.factset.financial_data import get_company_financials, get_company_dcs_summary
from dash_reports.analyst_questionnaire.data import acquisition, debt, equity, management_team
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX, ES_QUESTIONS, \
    ES_BOND_COVERAGE_INDEX

from common.utils import dtutil, jobutil, sqlutil


def refresh_issuers_coverage():
    logging.info('updating analyst quest issuers coverage')
    default_es.get_cached_df(ES_BOND_COVERAGE_INDEX, issuers.get_liquid_bond_universe, refresh_cache=True)

    logging.info('sleeping for elastic search to sync')
    time.sleep(1 * 60)
    logging.info('starting after elastic search sync')

    def _get_issuer_universe():
        bonds_df = default_es.search_all(ES_BOND_COVERAGE_INDEX)
        df = issuers.get_issuer_universe(bonds_df)
        return df

    default_es.get_cached_df(ES_ISSUER_COVERAGE_INDEX, _get_issuer_universe, refresh_cache=True)
    asof_date = dtutil.today(use_bday=False)
    sqlutil.execsql("delete from dbo.analyst_liquid_bond_universe where asof_date='{}'" \
                    .format(asof_date.strftime('%Y-%m-%d')), app=ANALYST_QUEST_APP)

    logging.info('sleeping for elastic search to sync')
    time.sleep(1 * 60)
    logging.info('starting after elastic search sync')

    bonds_df = default_es.search_all(ES_BOND_COVERAGE_INDEX)
    bonds_df['load_date'] = datetime.datetime.now()
    bonds_df['asof_date'] = asof_date
    sqlutil.insertdf_batch('dbo.analyst_liquid_bond_universe', bonds_df, app=ANALYST_QUEST_APP)

    logging.info('completed analyst quest issuers coverage')


def refresh_questions():
    logging.info('updating analyst quest questions cache')
    default_es.get_cached_df(ES_QUESTIONS, get_analyst_questions, refresh_cache=True)
    logging.info('completed analyst quest questions cache')


def refresh_industry_wide_data():
    logging.info('updating analyst quest industries cache')

    logging.info(f'updating analyst quest peer cache')
    peer_companies_by_industry = get_peer_companies()
    try:
        es_index = 'analyst_ques_peers'
        default_es.delete_index(es_index)
        default_es.add_df(peer_companies_by_industry, es_index)
    except:
        logging.error(f'error adding df {peer_companies_by_industry.shape} to index: {es_index}\n'
                      f'error:{traceback.format_exc()}')

    todt = dtutil.today()
    fromdt = dtutil.dateadd(dtutil.boyear(todt, False), '-5y')
    period_type = 'LTM'
    rating_type = 'STDLONG'

    jobber = jobutil.JobExecutor('process')
    industries = peer_companies_by_industry[['industry']].drop_duplicates().reset_index(drop=True)
    for i, industry in industries.iterrows():
        industry = industry['industry']
        peer_companies = peer_companies_by_industry[peer_companies_by_industry['industry'] == industry]

        logging.info(f'updating analyst quest industries cache: {industry}, {i + 1}/{len(industries)}')
        acq_func, acq_params = get_pricing, \
            (peer_companies['company_id'].unique().tolist(),
             acquisition.FORMULA_METRICS, fromdt, todt, period_type)
        financials_func1, financials_params1 = get_company_financials, \
            (peer_companies['company_id'].unique().tolist(),
             fromdt, todt, period_type, debt.FORMULA_METRICS)
        financials_func2, financials_params2 = get_company_dcs_summary, \
            (peer_companies['company_id'].unique().tolist(), fromdt, todt, False)
        financials_func3, financials_params3 = get_company_financials, \
            (peer_companies['company_id'].unique().tolist(), fromdt,
             todt, period_type, equity.FORMULA_METRICS)
        financials_func4, financials_params4 = get_company_financials, \
            (peer_companies['company_id'].unique().tolist(), fromdt,
             todt, period_type, management_team.FORMULA_METRICS)
        ratings_func, ratings_param = get_ratings_by_company_id, \
            (peer_companies['company_id'].unique().tolist(), rating_type)

        res = jobber.submitjob(
            [acq_func, financials_func1, financials_func2, financials_func3, financials_func4, ratings_func],
            params=[acq_params, financials_params1, financials_params2, financials_params3, financials_params4,
                    ratings_param],
            initconfig=True)

        for k, r in zip(['acquisition', 'debt', 'dcs', 'equity', 'mgmt', 'ratings'], res):
            es_index = f'analyst_ques_{k}'
            if r['error'] is None:
                try:
                    df = r['result']
                    df.index = df.index.map(lambda x: f'{industry}_{x}')
                    df['industry'] = industry
                    default_es.delete_doc(es_index, 'industry', industry, query_type='exact')
                    default_es.add_df(df, es_index)
                except:
                    logging.error(f'error adding key: {k} industry: {industry} '
                                  f'df {df.shape} to index: {es_index}\nerror:{traceback.format_exc()}')
            else:
                logging.error(f"error adding df to index: {es_index}\nerror: {r['error']}")

        logging.info(f'completed analyst quest industries cache: {industry}, {i + 1}/{len(industries)}')


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger('analyst_ques_caching')

    from apo_config.config import config
    from data_access.spark.api import sparkmanager

    config.init('prod')
    sparkmanager.init()
    refresh_issuers_coverage()
