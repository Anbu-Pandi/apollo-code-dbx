import pandas as pd
from common.utils import dtutil
from dash_reports.report_common.elastic_search.api import default_es

from dash_reports.issuers_info.data.issuers import get_bbg_issuers, get_bbg_bonds, get_wso_loans, get_markit_cds, \
    get_bbg_bonds_attr

ES_BBG_ISSUERS_INDEX = 'bbg_issuers'

ES_BBG_BONDS_INDEX = 'bbg_bonds'

ES_WSO_LOANS_INDEX = 'wso_loans'

ES_MARKIT_CDS_INDEX = 'markit_cds'


def refresh_bbg_issuers():
    dt = dtutil.dateadd(dtutil.today(use_bday=True), '-1w')
    df = get_bbg_issuers(dt)
    default_es.add_df(df, ES_BBG_ISSUERS_INDEX, dfindex_cols=['id_bb_company'])


def refresh_bbg_bonds():
    dt = dtutil.dateadd(dtutil.today(use_bday=True), '-1w')

    df = get_bbg_bonds(dt)
    default_es.add_df(df, ES_BBG_BONDS_INDEX, dfindex_cols=['id_bb_global'])

    dfa = get_bbg_bonds_attr()
    bond_df = default_es.search_exact(ES_BBG_BONDS_INDEX, ['id_isin'], dfa['id_isin'].tolist())
    dfa = pd.merge(dfa, bond_df[['id_isin', 'id_bb_global']], on='id_isin')

    default_es.add_df(dfa, ES_BBG_BONDS_INDEX, dfindex_cols=['id_bb_global'], op_type='update')


def refresh_wso_loans():
    df = get_wso_loans()
    default_es.add_df(df, ES_WSO_LOANS_INDEX, dfindex_cols=['wso_data_facility_id'])


def refresh_markit_cds():
    df = get_markit_cds()
    default_es.add_df(df, ES_MARKIT_CDS_INDEX, dfindex_cols=['red_code', 'id_bb_company'])


def refresh_sec_master():
    refresh_bbg_issuers()
    refresh_bbg_bonds()
    refresh_wso_loans()
    refresh_markit_cds()


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger(logfile='caching')

    from apo_config.config import config

    config.init('prod')
    refresh_bbg_bonds()
