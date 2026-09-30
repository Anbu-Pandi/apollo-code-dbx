import logging

import numpy as np
import pandas as pd

from dash_reports.report_common.utils.components import map_dash_type, is_dfcol_int


def filter_df_string(df, col, filterstring):
    oper = filterstring[:2]
    if oper[0] == '=':
        flags = df[col].fillna('0').astype(str).str.lower() == filterstring.lower().replace('=', '')
    elif oper == '!=':
        flags = df[col].fillna('0').astype(str).str.lower() != filterstring.lower().replace('!=', '')
    elif oper[0] == '!':
        flags = ~df[col].fillna('0').astype(str).str.lower().str.contains(filterstring.lower().replace('!', ''))
    else:
        flags = df[col].fillna('0').astype(str).str.lower().str.contains(filterstring.lower())
    return flags


def filter_df_alltypes(df, col, filterstring, dtype):
    flags = [True] * len(df)
    filterstring = filterstring.strip()
    if len(filterstring) > 0:
        try:
            if dtype == 'numeric':
                if is_dfcol_int(df, col):
                    flags = df[col].fillna(0) == int(filterstring)
                else:
                    flags = df[col].fillna(0) == float(filterstring)
            elif dtype == 'datetime':
                flags = df[col] == pd.to_datetime(filterstring)
            else:
                flags = filter_df_string(df, col, filterstring)
            flags = flags.values
        except:
            logging.warning(f"df filter failed for: {col} == {filterstring}, dtype: {dtype}")

    return flags


def filter_df(df, filters=None):
    logging.info(f"filtering df: {df.shape} for filters: {filters}")
    filters = filters or {}
    dtypes = {col: map_dash_type(df, col) for col, _ in filters.items()}
    flags = [True] * len(df)
    for col, filter in filters.items():
        filter = filter or ''
        flags_filter = [False] * len(df)
        for filterstring in filter.split(','):
            logging.info(f'filtering {col} for: {filterstring}')
            flags_i = filter_df_alltypes(df, col, filterstring, dtypes[col])
            flags_filter = np.array(flags_filter) | np.array(flags_i)
        flags = np.array(flags) & np.array(flags_filter)
    df = df.loc[flags, :]
    logging.info(f"post filtering df: {df.shape} for filters: {filters}")
    return df


def ui_column_name(x):
    return (x or '').replace('_', ' ').title()

listify = lambda x: [x1.strip() for x1 in x.split(',')]
