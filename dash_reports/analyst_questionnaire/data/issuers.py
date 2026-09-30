import datetime
import logging

import pandas as pd
import numpy as np

from common.utils import sqlutil, dtutil, creditutil
from data_access.beta_attribution import APP
from data_access.beta_attribution.utils import tosqllist
from data_access.dbtables import ICE_INDEX_APP, BOND_REFERENCEDATA_APP
from data_access.dbtables.security_data.extended.bond_security_master import get_sec_details
from data_access.dbtables.analyst_questionnaire.analyst_response import get_analyst_response_stats, \
    get_latest_analyst_response, get_fundamental_score, get_apo_ice_industry_map
from data_access.dbtables.factset.company_data import get_company_industry_by_id
from data_access.dbtables.market_data.fwd.extended.fx import get_fx

from dash_reports.issuers_info.data.capital_structure import \
    get_bbg_corp_tree_with_public_parent, get_bbg_obligor_corp_tree_with_calc
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.questions import get_questions
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
from dash_reports.trace.data.trace_flow import get_trace_volume

ICE_INDEX_UNIVERSE_SQL = '''
with max_date as 
(
select
	max(asof_date) as max_dt
from
	dbo.ice_index_all
WHERE
    frequency='PROJ'
	and ice_index_ticker in {}
)
select
	{}
from
	dbo.ice_index_all
join max_date on asof_date = max_dt
WHERE
    frequency='PROJ'
	and ice_index_ticker in {}
'''

ISSUERS_CROSS_REF_SQL = '''
select * from dbo.f_get_issuer_cross_ref(NULL)
where id_bb_company IN {}
'''

ISSUER_COVERAGE_SQL = '''
select * from dbo.analyst_issuer_coverage
'''
BBG_BONDS_ATTR_SQL = '''
select * from f_get_bond_security_master_attributes(null) 
where id_isin in {}
'''


def get_bbg_bonds_attr(isins):
    df = sqlutil.readdfsql(BBG_BONDS_ATTR_SQL.format(tosqllist(isins)), app=BOND_REFERENCEDATA_APP)
    df = df.set_index(['id_isin', 'attribute_name'])['attribute_value'].unstack().reset_index()
    df.columns = df.columns.map(str.lower)
    return df


def _get_bbg_co_ids_from_isin(isins):
    df = get_sec_details(isins,
                         id_column='id_isin',
                         select_cols=['id_isin', 'id_bb_company', 'id_bb_global_company_name', 'amt_outstanding',
                                      'crncy', 'collat_typ', 'is_perpetual'])
    dfa = get_bbg_bonds_attr(isins)
    df = pd.merge(df, dfa, on='id_isin', how='left')

    return df


def get_bond_universe_from_ice():
    logging.info(f'starting get issuer coverage from ice')
    refdt = dtutil.today()
    sel_cols = ','.join(['isin_number', 'ice_index_ticker', 'maturity_date', 'ticker',
                         'industry_lvl1_desc', 'industry_lvl2_desc', 'industry_lvl3_desc', 'industry_lvl4_desc',
                         'rating', 'years_since_issue', 'region'])

    ice_indexes = ['C0A0', 'H0A0']
    sql = ICE_INDEX_UNIVERSE_SQL.format(tosqllist(ice_indexes), sel_cols, tosqllist(ice_indexes))
    df_ice = sqlutil.readdfsql(sql, app=ICE_INDEX_APP)

    ids = df_ice['isin_number'].unique().tolist()
    df_bbg_isins = _get_bbg_co_ids_from_isin(ids)
    fx_dict = get_fx(refdt)
    df_bbg_isins['fx'] = df_bbg_isins['crncy'].map(lambda x: fx_dict.get(x, 1.0))
    df_bbg_isins['amt_outstanding_usd'] = df_bbg_isins['amt_outstanding'] * df_bbg_isins['fx'] / 1e6

    trace_df = get_trace_volume(refdt, refdt, tenor='3m', id_col='isin')

    df_merged = pd.merge(df_ice, df_bbg_isins, left_on='isin_number', right_on='id_isin')
    df_merged = pd.merge(df_merged, trace_df, left_on='isin_number', right_on='isin', how='left')

    df_merged['maturity_years'] = pd.to_datetime(df_merged['maturity_date']).dt.date \
        .map(lambda x: np.round((x - refdt).days / 365, 2))

    df_merged['maturity_bucket'] = creditutil.map_maturity_bucket(df_merged['maturity_years'])
    df_merged['rating_bucket'] = creditutil.map_rating(df_merged['rating'])
    df_merged['rating_bucket_int'] = creditutil.map_rating_int(df_merged['rating'])
    df_merged['sec_unsec'] = creditutil.map_secured_unsecured(df_merged['collat_typ'], df_merged['is_perpetual'])

    logging.info(f'completed get issuer coverage from ice')
    return df_merged


def _get_issuer_cross_ref(bbg_ids):
    ids = tosqllist(bbg_ids, isstr=False)
    df = sqlutil.readdfsql(ISSUERS_CROSS_REF_SQL.format(ids), app=BOND_REFERENCEDATA_APP)
    df = df.drop_duplicates(subset=['id_bb_company'], keep='first')
    return df


def _get_analyst_responses():
    df = get_latest_analyst_response()
    qdf = get_questions()
    df = pd.merge(df, qdf, left_on='question', right_on='qid', how='outer')

    df['company_id'] = pd.to_numeric(df['company_id'], errors='coerce')

    df = df.drop(columns=['asof_date', 'last_update_ts', 'last_updated_by', 'company_name'])
    df = df.pivot_table(index='company_id', columns='qid', values='answer', aggfunc=max, dropna=False) \
        .reset_index()
    df.columns = df.columns.map(lambda x: (x[0] if x[1] == '' else x[1]) if isinstance(x, tuple) else x)

    return df.dropna(subset=['company_id'])


def _map_bbg_company(df, df_tree, cross_ref):
    cross_ref.set_index('id_bb_company', inplace=True)
    df['mapped_id_bb_company'] = np.nan
    for col in ['public_parent_id_bb_company', 'id_bb_company', 'ult_parent_id_bb_company']:
        xr_mapped_ids = (pd.notnull(cross_ref.reindex(df[col])['factset_entity_id']).values * 1 * df[col])
        xr_mapped_ids = xr_mapped_ids.replace(0, np.nan)
        df['mapped_id_bb_company'] = df['mapped_id_bb_company'].combine_first(xr_mapped_ids)
    cross_ref.reset_index(inplace=True)

    def _map_in_tree(col):
        if col in df_tree.columns:
            df[f'mapped_{col}'] = df[f'mapped_id_bb_company'] \
                .map(lambda x: df_tree.loc[x, col] if (x in df_tree.index) else np.nan)

    df_tree.set_index('id_bb_company', inplace=True)
    for col in ['id_bb_global_company', 'id_bb_global_company_name', 'company_status']:
        _map_in_tree(col)
    df_tree.reset_index(inplace=True)

    return df


def get_issuer_universe(bonds_df):
    str_agg = lambda x: ','.join(x.dropna().sort_values().unique())
    agg_funcs = {'amt_outstanding_usd': sum, 'gross_volume_3m': sum, 'fx': np.mean,
                 'isin_number': str_agg, 'business_structure': str_agg, }
    gby_issuer = bonds_df.groupby('company_corp_ticker').agg(agg_funcs).reset_index()

    issuer_df = bonds_df.sort_values('years_since_issue') \
        .drop(columns=list(agg_funcs.keys())) \
        .drop_duplicates('company_corp_ticker', keep='first')

    df = pd.merge(issuer_df, gby_issuer, on='company_corp_ticker').rename(columns={'isin_number': 'isin_list'})
    return df


def get_liquid_bond_universe():
    ice_bonds_df = get_bond_universe_from_ice()
    pub_parent_tree_df = get_bbg_corp_tree_with_public_parent(ice_bonds_df['id_bb_company'].unique(), use_db=True)

    obg_col_rename = {'id_bb_company': 'id_bb_company_for_obligor', 'obligor_bbid': 'calc_obligor_id_bb_company'}
    co_obg_map = pub_parent_tree_df[['id_bb_company', 'obligor_bbid']].rename(columns=obg_col_rename)

    co_name_map_df = pub_parent_tree_df[['id_bb_company', 'id_bb_global_company_name']].drop_duplicates()
    co_name_map_df = co_name_map_df.rename(columns={x: f'calc_obligor_{x}' for x in co_name_map_df.columns})

    bond_df = pd.merge(ice_bonds_df, co_obg_map, left_on='id_bb_company', right_on='id_bb_company_for_obligor',
                       how='left')
    bond_df = pd.merge(bond_df, co_name_map_df, on='calc_obligor_id_bb_company', how='left')

    bond_df['original_id_bb_company'] = bond_df['id_bb_company']
    bond_df['original_id_bb_global_company_name'] = bond_df['id_bb_global_company_name']

    bond_df['id_bb_company'] = bond_df['calc_obligor_id_bb_company'].combine_first(bond_df['id_bb_company'])
    bond_df['id_bb_global_company_name'] = bond_df['calc_obligor_id_bb_global_company_name'] \
        .combine_first(bond_df['id_bb_global_company_name'])

    bond_df = pd.merge(bond_df, pub_parent_tree_df.drop(columns=['id_bb_global_company_name']), on='id_bb_company',
                       how='left')
    bond_df['company_corp_ticker'] = bond_df['ticker'].combine_first(bond_df['company_corp_ticker'])

    bbg_co_ids = bond_df[['id_bb_company', 'public_parent_id_bb_company', 'ult_parent_id_bb_company']] \
        .values.flatten().tolist()
    bbg_co_ids = list(set(bbg_co_ids))
    cross_ref = _get_issuer_cross_ref([int(x) for x in bbg_co_ids if pd.notnull(pd.to_numeric(x,errors='coerce'))])
    bond_df = _map_bbg_company(bond_df, pub_parent_tree_df, cross_ref)

    cross_ref_col = {'id_bb_company': 'mapped_id_bb_company', }
    cross_ref_drop_cols = ['id_bb_global_company', 'id_bb_global_company_name']
    bond_df = pd.merge(bond_df,
                       cross_ref.rename(columns=cross_ref_col).drop(columns=cross_ref_drop_cols),
                       on='mapped_id_bb_company', how='left')

    sp_df = get_company_industry_by_id(bond_df['factset_entity_id'].unique().tolist())
    sp_df.rename(columns={'company_id': 'factset_entity_id',
                          'industry_level_3': 'factset_company_industry',
                          'company_name': 'factset_company_name'}, inplace=True)
    bond_df = pd.merge(bond_df, sp_df[['factset_entity_id', 'factset_company_industry']],
                       on=['factset_entity_id'], how='left')

    bond_df = bond_df[bond_df['amt_outstanding_usd'] >= 450]
    bond_df = bond_df[bond_df['gross_volume_3m'] >= 33]
    bond_df = bond_df[bond_df['rating_bucket_int'] <= creditutil.rating_mapper('BB')]

    bond_df = bond_df.sort_values('years_since_issue') \
        .drop_duplicates(subset=['id_bb_company', 'rating_bucket', 'maturity_bucket', 'sec_unsec', 'crncy'],
                         keep='first')

    return bond_df


def update_apo_industry_fundamental_score(df):
    analyst_fundamental_score = get_fundamental_score()

    analyst_fundamental_score = analyst_fundamental_score.sort_values(by=['start_date']) \
        .drop_duplicates(subset=['corp_ticker'], keep='last')

    df = pd.merge(df, analyst_fundamental_score[['corp_ticker', 'score', 'start_date']],
                  left_on='company_corp_ticker', right_on='corp_ticker', how='left')

    df['start_date'] = df['start_date'].fillna(pd.NaT)

    df['ratings_mom_score'] = df.apply(lambda x: x['score']
    if pd.to_datetime(x['last_updated'], errors='coerce') < pd.to_datetime(x['start_date'], errors='coerce')
    else x['ratings_mom_score'], axis=1)

    df = df.rename(columns={'score': 'bbg_fundamental_score'})

    apo_industry_map = get_apo_ice_industry_map()

    df = pd.merge(df, apo_industry_map, left_on=['industry_lvl4_desc', 'region'],
                  right_on=['ice_industry_level4', 'ice_region'], how='left')

    df = df.drop(['start_date', 'ice_industry_level4', 'ice_region'], axis=1)

    df.loc[~df['last_updated_by'].isna(), 'apo_lead'] = df.loc[~df['last_updated_by'].isna(), 'last_updated_by']

    return df


def get_issuer_coverage_with_questionnaire():
    df = default_es.search_all(ES_ISSUER_COVERAGE_INDEX)
    analyst_response_stats_df = get_analyst_response_stats()
    df = pd.merge(df, analyst_response_stats_df[['company_id', 'last_updated_by', 'last_updated', '%_complete']],
                  left_on='id_bb_company', right_on='company_id', how='left')

    analyst_response_df = _get_analyst_responses()
    df = pd.merge(df, analyst_response_df, left_on='id_bb_company', right_on='company_id', how='left')
    df = update_apo_industry_fundamental_score(df)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    df = get_liquid_bond_universe()
    logging.info(df)
    pass
