from dash_reports.issuers_info.data.capital_structure import get_bbg_corp_tree_db, find_nearest_parent
from data_access.dbtables.olympus import restrictions
import pandas as pd


def merge_restriction_df(debt_df):
    bbg_company_ids = debt_df['id_bb_company'].unique().tolist()
    restr_df = restrictions.get_restrictions(bbg_company_ids).set_index('bbg_company_id')
    restr_df['restriction_type'] = 0
    restr_df.loc[restr_df['is_restricted'], 'restriction_type'] = 1
    restr_df.loc[restr_df['is_child_restricted'], 'restriction_type'] = 2

    corp_tree_df = get_bbg_corp_tree_db(restr_df.index.tolist())

    corp_tree_df = pd.merge(corp_tree_df, restr_df, how='left', left_on='id_bb_company', right_index=True)

    corp_tree_df = find_nearest_parent(corp_tree_df, match_col='restriction_type', match_val=2,
                                       init_lvl_stop_values=[1, 2], col_prefix='restricted',
                                       search_ids=bbg_company_ids, default_value=-1)
    corp_tree_df['is_restricted'] = False
    corp_tree_df.loc[(corp_tree_df['restricted_id_bb_company'] != -1), 'is_restricted'] = True

    df = pd.merge(debt_df, corp_tree_df[['id_bb_company', 'is_restricted']], on='id_bb_company', how='left')
    df['is_restricted'] = df['is_restricted'].fillna(False)
    df['is_restricted'] = df['is_restricted'] * 1

    return df


if __name__ == '__main__':
    import logging
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    _df = pd.read_clipboard()
    df = merge_restriction_df(_df)

    logging.info(df)
