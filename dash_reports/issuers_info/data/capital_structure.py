import logging

import networkx as nx
import numpy as np
import pandas as pd

from common.utils import sqlutil
from common.utils.dfutil import tolist
from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX, ES_BBG_BONDS_INDEX
from dash_reports.issuers_info.data.debt import get_debt_df, get_sec_count_df
from dash_reports.report_common.elastic_search.api import default_es
from data_access.dbtables import BOND_REFERENCEDATA_APP


def find_nearest_parent(df_tree, match_col='company_status', match_val='PUB', init_lvl_stop_values=['PUB'],
                        col_prefix='public_parent', search_ids=[], default_value=None):
    def func(grouped_df):
        def func_for_id(id_bb_company, default_value):
            max_lvls = 100
            logging.debug(f'finding nearest parent for: {match_col}={match_val} with condn: {init_lvl_stop_values} '
                          f'ids:\n {id_bb_company}')
            lvl = 0
            default_value = default_value or grouped_df.loc[id_bb_company, 'parent_id_bb_company']
            while (True):
                if lvl > max_lvls:
                    logging.warning(f'max levels: {max_lvls} crossed. defaulting parent to: {default_value}')
                    return default_value

                if id_bb_company not in grouped_df.index:
                    logging.warning(
                        f'cannot find id_bb_company:{id_bb_company} in grouped_df. defaulting parent to: {default_value}')
                    return default_value

                matches = grouped_df.loc[id_bb_company, :]
                init_match_pred = (lvl == 0) and (matches[match_col] in init_lvl_stop_values)
                match_pred = (lvl != 0) and (matches[match_col] == match_val)
                if (match_pred or
                        pd.isnull(matches['parent_id_bb_company']) or
                        matches['parent_id_bb_company'] == id_bb_company or
                        init_match_pred):
                    logging.debug(f'found nearest public parent: {int(id_bb_company)}')
                    return int(id_bb_company)

                id_bb_company = matches['parent_id_bb_company']
                lvl += 1

        return pd.DataFrame(grouped_df.index.map(lambda x: func_for_id(x, default_value)), index=grouped_df.index)

    id_col = 'id_bb_company'
    df_tree[id_col] = df_tree['id_bb_company'].map(int)
    df_tree.set_index(id_col, inplace=True)

    search_df = df_tree
    if len(search_ids) > 0:
        search_df = df_tree.reindex(search_ids).dropna(how='all')
    df_pub_parent = func(search_df)
    df_tree[f'{col_prefix}_{id_col}'] = df_pub_parent.rename(columns={id_col: f'{col_prefix}_{id_col}'})
    return df_tree.reset_index()


def get_bbg_corp_sup_tree_db(bbg_ids):
    sql = "select * from dbo.f_get_bbg_corp_sup_tree('{}', NULL)"
    bbg_ids = set((bbg_ids))

    company_ids_csv = ",".join([str(int(x)) for x in bbg_ids if pd.notnull(x)])
    df = sqlutil.readdfsql(sql.format(company_ids_csv), app=BOND_REFERENCEDATA_APP)
    return df


def get_bbg_corp_tree_db(bbg_ids):
    sql = "select * from dbo.f_get_bbg_corp_tree('{}', NULL)"
    bbg_ids = set((bbg_ids))

    company_ids_csv = ",".join([str(int(x)) for x in bbg_ids if pd.notnull(x)])
    df = sqlutil.readdfsql(sql.format(company_ids_csv), app=BOND_REFERENCEDATA_APP)
    return df


def get_bbg_obligor_corp_tree_db(bbg_ids):
    sql = "select * from dbo.f_get_bbg_obligor_corp_tree('{}', NULL)"
    bbg_ids = set(tolist(bbg_ids))

    company_ids_csv = ",".join([str(int(x)) for x in bbg_ids if pd.notnull(x)])
    df = sqlutil.readdfsql(sql.format(company_ids_csv), app=BOND_REFERENCEDATA_APP)
    return df


def construct_bbg_tree_levels(bbg_corp_tree_df):
    ret = {}
    ult_count = bbg_corp_tree_df.groupby(['ult_parent_id_bb_company'])['id_bb_company'].count()
    if len(ult_count) > 0:
        ult_parent = int(ult_count[ult_count == ult_count.max()].index[0])
        drop_rows = pd.isnull(bbg_corp_tree_df['parent_id_bb_company']) & \
                    (bbg_corp_tree_df['id_bb_company'] != bbg_corp_tree_df['ult_parent_id_bb_company'])
        sub_df = bbg_corp_tree_df.loc[~drop_rows, :]
        g = nx.DiGraph()
        g.add_nodes_from(sub_df['id_bb_company'])
        g.add_edges_from(sub_df[['parent_id_bb_company', 'id_bb_company']] \
                         .dropna(how='any').astype(int).to_records(index=False))
        levels = nx.single_source_shortest_path_length(g, ult_parent)
        paths = nx.single_source_shortest_path(g, ult_parent)

        for k, v in levels.items():
            ret.update({k: (v + 1, paths.get(k, []))})

    return ret


def get_compliance_restrictions(ult_parent_bbg_ids):
    return pd.DataFrame()


def get_bbg_corp_sup_tree(bbg_co_ids):
    bbg_co_ids = [int(float(x)) for x in tolist(bbg_co_ids) if
                  str(x).replace('.', '').isnumeric() and int(float(x)) != 0]
    co_df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_company', bbg_co_ids)
    if len(co_df) > 0:
        co_df = co_df[co_df['id_bb_company'].isin(bbg_co_ids)]
    siblings_df1 = pd.DataFrame()
    if len(co_df) > 0:
        ult_parent_ids = co_df['id_bb_ultimate_parent_co'].combine_first(co_df['id_bb_company']).unique().tolist()
        ult_parent_ids = [int(float(x)) for x in ult_parent_ids if
                          str(x).replace('.', '').isnumeric() and int(float(x)) != 0]
        siblings_df1 = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_ultimate_parent_co', ult_parent_ids)
        if len(siblings_df1) > 0:
            siblings_df1 = siblings_df1[siblings_df1['id_bb_ultimate_parent_co'].isin(ult_parent_ids)]
        else:
            siblings_df1 = co_df

    siblings_df2 = pd.DataFrame()
    if len(siblings_df1) > 0:
        ult_parent_tickers = siblings_df1['ult_parent_corp_ticker'].unique().tolist()
        siblings_df2 = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'ult_parent_corp_ticker', ult_parent_tickers)
        if len(siblings_df2) > 0:
            siblings_df2 = siblings_df1[siblings_df1['ult_parent_corp_ticker'].isin(ult_parent_tickers)]

    siblings_df = pd.concat([siblings_df1, siblings_df2]).drop_duplicates(subset=['id_bb_company'])
    sel_cols = [
        'id_bb_company', 'id_bb_global_company_name', 'company_status', 'id_bb_parent_co',
        'id_bb_global_parent_company_name', 'id_bb_ultimate_parent_co',
        'id_bb_global_ult_parent_co_name', 'company_corp_ticker',
        'obligor_bbid', 'id_bb_global_obligor_name',
        'parent_obligor_id', 'parent_obligor_name', 'ultimate_obligor_id', 'ultimate_obligor_name',
        'company_to_parent_relationship', 'acquired_by_parent', 'knowledge_date',
    ]
    if len(siblings_df) > 0:
        siblings_df = siblings_df.reindex(columns=sel_cols)
        col_map = {'id_bb_parent_co': 'parent_id_bb_company',
                   'id_bb_ultimate_parent_co': 'ult_parent_id_bb_company',
                   'id_bb_global_parent_company_name': 'parent_id_bb_global_company_name',
                   'id_bb_global_ult_parent_co_name': 'ult_parent_id_bb_global_company_name'}
        siblings_df = siblings_df.rename(columns=col_map)

        siblings_df = siblings_df.sort_values(['parent_id_bb_company'])

    return siblings_df


def get_bbg_obligor_corp_tree(bbg_co_ids):
    bbg_co_ids = [int(float(x)) for x in tolist(bbg_co_ids) if
                  str(x).replace('.', '').isnumeric() and int(float(x)) != 0]
    co_df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_company', bbg_co_ids)
    siblings_df = pd.DataFrame()
    if len(co_df) > 0:
        ult_parent_ids = co_df['ultimate_obligor_id'].unique().tolist()
        ult_parent_ids = [int(float(x)) for x in ult_parent_ids if
                          str(x).replace('.', '').isnumeric() and int(float(x)) != 0]
        siblings_df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'ultimate_obligor_id', ult_parent_ids)

    if len(siblings_df) > 0:
        obligor_cols = {'obligor_bbid': 'id_bb_company',
                        'id_bb_global_obligor_name': 'id_bb_global_company_name',
                        'parent_obligor_id': 'parent_id_bb_company',
                        'parent_obligor_name': 'parent_id_bb_global_company_name',
                        'ultimate_obligor_id': 'ult_parent_id_bb_company',
                        'ultimate_obligor_name': 'ult_parent_id_bb_global_company_name',
                        'id_bb_company': 'id_bb_company_for_obligor',
                        'id_bb_global_company_name': 'id_bb_global_company_name_for_obligor'}
        siblings_df = siblings_df[obligor_cols.keys()].rename(columns=obligor_cols)
        siblings_df = siblings_df[siblings_df['id_bb_company'].fillna(0) != 0]

        for parent_id_col in ['parent_id_bb_company', 'ult_parent_id_bb_company']:
            null_parent = (siblings_df[parent_id_col] == siblings_df['id_bb_company']) | \
                          (siblings_df[parent_id_col] == 0) | \
                          (~siblings_df[parent_id_col].isin(siblings_df['id_bb_company']))
            siblings_df.loc[null_parent, parent_id_col] = None
            null_parent = siblings_df[parent_id_col].isnull()
            siblings_df.loc[null_parent, parent_id_col.replace('company', 'global_company_name')] = None
            siblings_df.loc[null_parent, parent_id_col] = siblings_df['id_bb_company']
            siblings_df.loc[null_parent, parent_id_col.replace('company', 'global_company_name')] = \
                siblings_df['id_bb_global_company_name']

        siblings_df['additional_info'] = siblings_df['id_bb_company_for_obligor'].astype(str)

    return siblings_df


def get_bbg_corp_tree_with_public_parent(bbg_ids, use_db=False):
    if use_db:
        df = get_bbg_corp_sup_tree_db(bbg_ids)
    else:
        df = get_bbg_corp_sup_tree(bbg_ids)

    if len(df) > 0:
        logging.info('adding levels and path to bbg corp tree')
        lvls = construct_bbg_tree_levels(df)
        df['level'] = df['id_bb_company'].map(lambda x: lvls.get(x, [1, None])[0])
        df['path'] = df['id_bb_company'].map(lambda x: lvls.get(x, [None, []])[1] + [x])

        logging.info('adding restrictions to bbg corp tree')

        def _get_restriction(ids):
            cols = ['restriction_type', 'restriction_scope', 'color_code', 'start_date', 'end_date']
            for id in reversed(ids):
                if id in restricted_df.index:
                    return tuple(restricted_df.loc[id, cols].values())
            return tuple(len(cols) * [None])

        ult_parent_ids = df['ult_parent_id_bb_company'].combine_first(df['id_bb_company']).unique().tolist()
        restricted_df = get_compliance_restrictions(ult_parent_ids)
        if len(restricted_df) > 0:
            restricted_df.set_index('id_bb_company', inplace=True)
            (df['restriction_type'],
             df['restriction_scope'],
             df['restriction_color_code'],
             df['restriction_start_date'],
             df['restriction_end_date']) = list(zip(*df['path'].map(_get_restriction)))

        logging.info('adding nearest public parent for bbg corp tree')
        df = find_nearest_parent(df)
        df = df.sort_values(['level', 'parent_id_bb_company']).drop(columns=['path'])

    return df


def get_bbg_obligor_corp_tree_with_calc(bbg_ids, use_db=False):
    if use_db:
        df = get_bbg_corp_sup_tree_db(bbg_ids)
    else:
        df = get_bbg_obligor_corp_tree(bbg_ids)
    bonds_df = default_es.search_exact(ES_BBG_BONDS_INDEX, 'id_bb_company', bbg_ids)
    if len(df) > 0:
        lvls = construct_bbg_tree_levels(df)
        df['level'] = df['id_bb_company'].map(lambda x: lvls.get(x, [1, None])[0])
        df['path'] = df['id_bb_company'].map(lambda x: lvls.get(x, [None, []])[1] + [x])

        df = df.sort_values(['level', 'parent_id_bb_company']).drop(columns=['path'])

        df_temp = df.sort_values(['id_bb_company', 'parent_id_bb_company']) \
            .drop_duplicates(subset=['id_bb_company'], keep='first')
        logging.info('adding nearest public parent for bbg obligor corp tree')
        struct_cols = ['business_structure', 'cast_parent_id', 'credit_family_company_id', 'id_bb_guarantor']
        if (len(bonds_df) > 0) and ('business_structure' in bonds_df.columns):
            struct = bonds_df[['id_bb_company', ] + struct_cols].dropna(subset=['business_structure']) \
                .sort_values(['id_bb_company', 'business_structure']) \
                .drop_duplicates(subset=['id_bb_company'])
            df_temp = pd.merge(df_temp, struct, on='id_bb_company', how='left')
        else:
            for scol in struct_cols:
                df_temp[scol] = np.nan
        df_temp = find_nearest_parent(df_temp, match_col='business_structure',
                                      match_val='Holdco',
                                      init_lvl_stop_values=['Opco'])
        df = pd.merge(df, df_temp[['id_bb_company', 'public_parent_id_bb_company', ] + struct_cols],
                      on='id_bb_company', how='left')
        df.rename(columns={'public_parent_id_bb_company': 'calc_obligor_id_bb_company'}, inplace=True)
    return df


def add_sec_count_to_corp_tree(df, sec_count_df):
    logging.info(f'filtering df for instrument counts: {df.shape}')
    if len(sec_count_df) > 0:
        df = pd.merge(df, sec_count_df, on='id_bb_company', how='left')
        df['security_count'] = df['security_count'].fillna(0)
    else:
        df['security_count'] = 0

    count_by_id = {}
    df.set_index('id_bb_company', inplace=True)
    for i, dfrow in df.iterrows():
        node_id = int(i)
        parent_id = dfrow['parent_id_bb_company']
        if not node_id in count_by_id:
            count_by_id[node_id] = 0
        count_by_id[node_id] = count_by_id[node_id] + dfrow['security_count']
        while (pd.notnull(parent_id)):
            parent_id = int(parent_id)
            if not parent_id in count_by_id:
                count_by_id[parent_id] = 0
            count_by_id[parent_id] = count_by_id[parent_id] + count_by_id[node_id]
            if parent_id in df.index:
                parent_id = df.loc[parent_id, 'parent_id_bb_company']
            else:
                parent_id = None
    df_count = pd.DataFrame(count_by_id.items(), columns=['id_bb_company', 'security_count_all_child_issuers'])
    df = pd.merge(df.reset_index(), df_count, on='id_bb_company')
    logging.info(f'filtered df down to: {df.shape}')
    return df


def get_company_tree_view(id_bb_company, only_security_issuers=True):
    corp_tree_df = get_bbg_corp_tree_with_public_parent(id_bb_company)

    if len(corp_tree_df) > 0:
        rels_cos = corp_tree_df['id_bb_company'].tolist()
        rels_tickers = corp_tree_df['company_corp_ticker'].unique().tolist()
        debt_df = get_debt_df(rels_cos)

        sec_count_df = get_sec_count_df(debt_df)
        corp_tree_df = add_sec_count_to_corp_tree(corp_tree_df, sec_count_df)
        graph_tree_df = corp_tree_df.sort_values('security_count_all_child_issuers', ascending=False)
        if only_security_issuers:
            graph_tree_df = graph_tree_df[graph_tree_df['security_count_all_child_issuers'] > 0]

        graph_tree_df['additional_info'] = graph_tree_df['security_count_all_child_issuers'].astype(str)

        return corp_tree_df, graph_tree_df, debt_df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('dev')
    df = get_bbg_corp_tree_with_public_parent('31881101')

    logging.info(df)
