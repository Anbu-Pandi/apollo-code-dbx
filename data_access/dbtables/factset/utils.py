import re

import numpy as np
import pandas as pd

from common.utils import sqlutil, dfutil
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.const import CAPIQ_FACTSET_MAP, NON_BS_ITEMS, \
    NON_RESTATED_ITEMS

FACTSET_NORMALIZED_COLUMNS_MAP = {'factset_entity_id': 'company_id',
                                  'entity_proper_name': 'company_name',
                                  'l1_name': 'industry_level_1',
                                  'factset_sector_desc': 'industry_level_2',
                                  'factset_industry_desc': 'industry_level_3',
                                  'sic_desc': 'industry_level_4',
                                  'date': 'period_end_date'}

META_FACTSET_QUERY = '''
SELECT DISTINCT FIELD_NAME,FIELD_DESCRIPTION FROM REF_V2.REF_METADATA_FIELDS
WHERE TABLE_NAME LIKE '%basic%'
OR TABLE_NAME LIKE '%advanced%'
OR TABLE_NAME LIKE '%basic_der%'
OR TABLE_NAME LIKE '%advanced%'
'''


def normalize_df(df, rename_dict={}):
    rename_dict = {k.lower(): v for k, v in rename_dict.items()}
    rename_dict = dict(list(FACTSET_NORMALIZED_COLUMNS_MAP.items()) + list(rename_dict.items()))
    df.columns = df.columns.map(str.lower)
    df.rename(columns=rename_dict, inplace=True)
    ordered_cols = set([x for x in rename_dict.values() if x in df.columns] + \
                       [x for x in df.columns if x not in rename_dict.values()])
    df = df[list(ordered_cols)]
    return df


def reverse_map_columns(df, metric_map, metric_col1, metric_col2):
    mapping_df = pd.DataFrame([(k, v) for k, vs in metric_map.items() for v in dfutil.tolist(vs)],
                              columns=[metric_col1, metric_col2])
    df = pd.merge(df, mapping_df, left_on='dataitem_id', right_on=metric_col2, how='left')
    df[metric_col2] = df['dataitem_id']
    df['dataitem_id'] = df[metric_col1].combine_first(df['dataitem_id'])

    df['formula_metric'] = df['dataitem_id']
    df['metrics'] = df['dataitem_name']
    df['value'] = df['dataitem_value']

    return df


def normalize_factset_fin_df(df, id_vars, rename_dict={}):
    if 'qtr' not in df.columns:
        df['qtr'] = pd.to_datetime(df['DATE']).dt.year.astype(str) + \
                    ' Q' + pd.to_datetime(df['DATE']).dt.quarter.astype(str)
        id_vars = id_vars + ['qtr']

    value_vars = [x for x in df.columns if x not in id_vars]
    if len(value_vars) > 0:
        df = pd.melt(df, id_vars=id_vars, value_vars=value_vars,
                     var_name='dataitem_id', value_name='dataitem_value')

        meta_df = sqlutil.readdfsql(META_FACTSET_QUERY, app=APP)
        meta_df = meta_df.drop_duplicates(subset=['FIELD_NAME']).set_index('FIELD_NAME')
        df['dataitem_name'] = df['dataitem_id'].map(lambda x: meta_df.loc[x.lower(), 'FIELD_DESCRIPTION'])

        df = reverse_map_columns(df, metric_map=CAPIQ_FACTSET_MAP, metric_col1='capiq_id', metric_col2='factset_id')

    df = normalize_df(df, rename_dict=rename_dict)

    return df


def normalize_dcs_df(df):
    if 'qtr' not in df.columns:
        df['qtr'] = pd.to_datetime(df['REPORT_DATE']).dt.year.astype(str) + \
                    ' Q' + pd.to_datetime(df['REPORT_DATE']).dt.quarter.astype(str)
    DCS_RENAME = {'report_date': 'period_end_date',
                  'debt_code': 'dataitem_id',
                  'os_amount': 'dataitem_value',
                  'debt_desc': 'dataitem_name',
                  'debt_code': 'formula_metric',
                  'summary_desc': 'dataitem_name',
                  'publication_date': 'filing_date',
                  'debt_desc': 'metrics',
                  'os_amount': 'value'}
    df = normalize_df(df, rename_dict=DCS_RENAME)
    if 'dataitem_name' not in df.columns:
        df['dataitem_name'] = df['metrics']
    if 'dataitem_id' not in df.columns:
        df['dataitem_id'] = df['dataitem_name']
    if 'formula_metric' not in df.columns:
        df['formula_metric'] = df['dataitem_name']
    df['metrics'] = df['dataitem_name']
    return df


def get_ff_table_for_period_type(period_type):
    if period_type.lower().startswith('q'):
        return 'QF'
    if period_type.lower().startswith('a'):
        return 'AF'
    if period_type.lower().startswith('s'):
        return 'SAF'
    if period_type.lower().startswith('ltm'):
        return 'LTM'


def get_non_bs_metric_cols():
    return NON_BS_ITEMS


def get_non_restated_cols():
    return NON_RESTATED_ITEMS


def get_metric_cols(metric_cols_in, metric_map, all_metrics, use_unmapped=False):
    metric_cols_in = dfutil.tolist(metric_cols_in, replacenone=True)
    if (len(metric_cols_in) == 0) or (metric_cols_in[0].upper() == 'NULL'):
        metric_cols = all_metrics
    else:
        metric_cols = [v for x in metric_cols_in if (x in metric_map) for v in dfutil.tolist(metric_map[x])]
        metric_cols += [x for x in metric_cols_in
                        if (use_unmapped or ((x not in metric_map) and (x in all_metrics)))]
    return list(set(metric_cols))


def get_metric_cols_csv(metric_cols, metric_map, all_metrics, start_char, use_unmapped=False):
    metric_cols = get_metric_cols(metric_cols, metric_map, all_metrics, use_unmapped=use_unmapped)
    metric_cols_csv = start_char + ','.join(metric_cols)
    if metric_cols_csv == start_char:
        metric_cols_csv = ''

    return metric_cols_csv


def get_metrics_reqd_by_custom_ff(formula, custom_ff_dict, lvl=-1):
    metrics_reqd_by_cusom_ff = []
    for f in dfutil.tolist(formula):
        metrics_reqd_by_cusom_ff.extend(re.findall("<(.*?)>", f))

    metrics_reqd_by_custom_ff_l2 = []
    metrics_reqd_by_custom_ff_delete = []
    if lvl != 1:
        for x in metrics_reqd_by_cusom_ff:
            if x in custom_ff_dict:
                metrics_reqd_by_custom_ff_delete.append(x)
                metrics_reqd_by_custom_ff_l2.extend(get_metrics_reqd_by_custom_ff(custom_ff_dict[x], custom_ff_dict))

    return list(set(metrics_reqd_by_cusom_ff + metrics_reqd_by_custom_ff_l2) - set(metrics_reqd_by_custom_ff_delete))


def add_custom_ff_fields(fs_df, custom_fields,
                         field_name_col, field_des_cols, field_value_col,
                         company_id_cols, index_cols,
                         other_field_name_cols=[],
                         other_field_value_cols=[],
                         field_rename_map={}):
    MAX_LEVELS = 3

    def calculate_custom_fields(group):
        for loop_num in range(MAX_LEVELS):
            for custom_field, v in custom_fields.items():
                formula, mnemonic_des = v
                available_fields = group[field_name_col].unique().tolist()
                canonical = field_rename_map.get(custom_field, custom_field)
                if custom_field in available_fields or canonical in available_fields:
                    continue

                required_fields = get_metrics_reqd_by_custom_ff(formula, custom_fields, lvl=1)
                if np.all([(x in available_fields) for x in required_fields]):
                    fset_fs = group.set_index([field_name_col] + index_cols)[field_value_col]

                    for x in required_fields:
                        formula = formula.replace(f'<{x}>',
                                                  f"pd.to_numeric(fset_fs.loc[['{x}'], :], errors='coerce').reset_index('{field_name_col}', drop=True).fillna(0)")
                    new_rows_df = eval(formula).reset_index()
                    new_rows_df = new_rows_df.dropna(subset=[field_value_col])
                    new_rows_df = new_rows_df.drop_duplicates(subset=index_cols)
                    if len(new_rows_df) == 0:
                        continue
                    new_rows_df.loc[:, [field_name_col] + other_field_name_cols] = custom_field
                    new_rows_df[field_name_col] = canonical
                    new_rows_df['is_new_row'] = 1
                    if len(field_des_cols) > 0:
                        new_rows_df.loc[:, field_des_cols] = mnemonic_des
                    if len(other_field_value_cols) > 0:
                        new_rows_df.loc[:, other_field_value_cols] = new_rows_df.loc[:, field_value_col]
                    group = pd.concat([group, new_rows_df])

        if ('is_new_row' in group.columns) and (group['is_new_row'].dropna().sum() > 0):
            is_new_row = (group['is_new_row'] == 1)
            for c in group.columns:
                if len(group.loc[is_new_row, c].dropna()) == 0:
                    group.loc[is_new_row, c] = group.iloc[0][c]
            group = group.drop(columns=['is_new_row'])

        return group.drop(columns=company_id_cols)

    if len(custom_fields) * len(fs_df) > 0:
        fs_df = fs_df.groupby(company_id_cols, dropna=False).apply(calculate_custom_fields) \
            .reset_index(drop=False)

    return fs_df
