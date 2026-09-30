import datetime
import glob
import json
import logging
import numbers
import os
import re
import shutil
import uuid
import zipfile
from itertools import zip_longest

import numpy as np
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, Schema, Field
from deltalake.writer import write_deltalake

from common.utils.logutil import setuplogger

WRITE_DF_CHUNK_SIZE = 1000_000
MAX_DELTATABLE_FILES = 250


def drop_columns(df, cols):
    cols = tolist(cols)
    cols = [x for x in cols if x in df.columns]
    if len(cols) > 0:
        df = df.drop(columns=cols)
    return df


def select_cols(df, cols):
    sel_cols = [x for x in cols if x in df.columns]
    assert len(sel_cols) > 0, f'no cols in df: {cols}'
    return df[sel_cols]


def logdebugdf(df, msg):
    logging.debug('{0} df shape:{1}'.format(msg, df.shape))


def custom_agg(df, id_cols):
    def unique_sorted_concat(values):
        unique_vals = sorted(set([str(x) for x in values if pd.notnull(x)]))
        if len(unique_vals) > 0:
            return '|'.join(map(str, unique_vals))

    def non_null_avg(values):
        unique_vals = sorted([x for x in values if pd.notnull(x)])
        if len(unique_vals) > 0:
            return np.average(unique_vals)

    def aggregate_func(column):
        if np.issubdtype(column.dtype, np.number):
            return non_null_avg
        elif column.dtype == np.dtype('O') or column.dtype == 'string':
            return unique_sorted_concat
        else:
            return unique_sorted_concat

    agg_funcs = {col: aggregate_func(df[col]) for col in df.columns if col not in id_cols}
    grouped_df = df.groupby(id_cols).agg(agg_funcs).reset_index()
    return grouped_df


def report_diff(lf, rt, tolerance=0.0, lnum=1, diff_tolerance=1):
    if pd.isnull(lf) and pd.isnull(rt):
        return ''
    if (pd.isnull(lf) and rt == 0) or (pd.isnull(rt) and lf == 0):
        return ''

    x = [lf, rt]
    if isinstance(lf, str) or isinstance(rt, str):
        return '' if str(lf) == str(rt) else '{} | {}'.format(*x)
    if isinstance(lf, numbers.Number) and isinstance(rt, numbers.Number):
        if lf == 0 and rt == 0:
            return ''
        if ((np.abs(lf) <= lnum) or (np.abs(rt) <= lnum)) and lf != 0:
            return '' if np.abs(rt - lf) <= diff_tolerance else '{} | {}'.format(*x)
        elif lf == 0:
            return '' if np.abs(rt - lf) <= diff_tolerance else '{} | {}'.format(*x)
        else:
            return '' if np.abs(rt / lf - 1) <= tolerance else '{} | {}'.format(*x)
    return '' if lf == rt else '{} | {}'.format(*x)


vectorized_report_diff = np.vectorize(report_diff)


def _prepare_dataframes(df1, df2, index_cols, ignore_cols):
    comparison_df1 = df1.drop(columns=ignore_cols, errors='ignore')
    comparison_df2 = df2.drop(columns=ignore_cols, errors='ignore')

    comparison_df1 = comparison_df1[comparison_df2.columns]
    if set(comparison_df1.columns) != set(comparison_df2.columns):
        logging.error("Dataframes do not have the same columns")
        raise ValueError("Dataframes do not have the same columns")

    comparison_df1 = custom_agg(comparison_df1, index_cols)
    comparison_df2 = custom_agg(comparison_df2, index_cols)

    merged = comparison_df1.merge(comparison_df2, how='outer', indicator=True, on=index_cols, suffixes=('_old', '_new'))

    return merged


def _compute_diff_columns(merged, tolerance, lnum, diff_tolerance):
    diff_data = {}
    comparison_cols = [col for col in merged.columns if col.endswith('_old') or col.endswith('_new')]
    for col in comparison_cols:
        if col.endswith('_old'):
            new_col = col.replace('_old', '_new')
            diff_col = col.replace('_old', '_diff')
            diff_data[diff_col] = vectorized_report_diff(merged[col].values, merged[new_col].values, tolerance, lnum,
                                                         diff_tolerance)
    return diff_data


def _append_diff_columns(merged, diff_data):
    diff_df = pd.DataFrame(diff_data, index=merged.index)
    merged = pd.concat([merged, diff_df], axis=1)
    return merged


def _identify_changes(merged, diff_cols):
    merged['changed'] = np.any(merged[diff_cols] != '', axis=1)
    return merged


def _extract_changed_rows(merged, index_cols, diff_cols):
    changed_df = merged[merged['changed'] == True][index_cols + diff_cols]
    return changed_df


def comparedfs(df1: pd.DataFrame, df2: pd.DataFrame, index_cols: list, ignore_cols: list = [],
               tolerance=0.0, lnum=1.0, diff_tolerance=1) -> pd.DataFrame:
    merged = _prepare_dataframes(df1, df2, index_cols, ignore_cols)
    diff_data = _compute_diff_columns(merged, tolerance, lnum, diff_tolerance)
    merged = _append_diff_columns(merged, diff_data)
    diff_cols = list(diff_data.keys())
    merged = _identify_changes(merged, diff_cols)
    changed_df = _extract_changed_rows(merged, index_cols, diff_cols)
    changed_df.columns = changed_df.columns.map(lambda x: x.replace('_diff', ''))

    if changed_df.empty:
        logging.info("No changes between dataframes")
        return pd.DataFrame()

    logging.info(f"Found {len(changed_df)} changed rows")
    return changed_df.replace('', np.nan).dropna(how='all', axis=0).dropna(how='all', axis=1).replace(np.nan, '')


def get_changed_df(df1: pd.DataFrame, df2: pd.DataFrame, index_cols: list, ignore_cols: list = []) -> pd.DataFrame:
    return_cols = df1.columns.tolist()
    comparison_df1 = df1.drop(columns=ignore_cols, errors='ignore')
    comparison_df2 = df2.drop(columns=ignore_cols, errors='ignore')

    comparison_df1 = comparison_df1[comparison_df2.columns]
    if set(comparison_df1.columns) != set(comparison_df2.columns):
        logging.error("Dataframes do not have the same columns")
        raise ValueError("Dataframes do not have the same columns")

    merged = comparison_df1.merge(comparison_df2, how='right', indicator=True, on=index_cols, suffixes=('_old', '_new'))

    comparison_cols = [col for col in merged.columns if col.endswith('_old') or col.endswith('_new')]
    merged['changed'] = merged[comparison_cols].apply(lambda row: any(
        row[col] != row[col.replace('_old', '_new')] for col in comparison_cols if col.endswith('_old')), axis=1)

    changed_df = comparison_df2[
        comparison_df2[index_cols].apply(tuple, 1).isin(merged[merged['changed'] == True][index_cols].apply(tuple, 1))]
    add_cols = index_cols + list(set(return_cols) - set(changed_df.columns.tolist()))
    if len(add_cols) > len(index_cols):
        changed_df = pd.merge(changed_df, df1[add_cols], on=index_cols, how='left')

    if changed_df.empty:
        logging.info("No changes between dataframes")
        return pd.DataFrame()

    logging.info(f"Found {len(changed_df)} changed rows")
    return changed_df


def tolist(s, replacenone=False):
    if s is None:
        if replacenone:
            return []
        else:
            return None
    if isinstance(s, pd.Series):
        return s.dropna().tolist()
    if isinstance(s, list):
        return s
    if isinstance(s, (tuple, set, np.ndarray)):
        return list(s)
    return [s]


def upsert(df1, df2):
    df = pd.concat([df1, df2[~df2.index.isin(df1.index)]])
    df.update(df2)
    return df


def chunks_df(df, n):
    for i in range(0, len(df), n):
        yield df.iloc[i:i + n]


def chunks_list(lst, n):
    lst = tolist(lst)
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def unique_ordered(lst):
    seen = set()
    unique_list = [x for x in lst if not (x in seen or seen.add(x))]
    return unique_list


def _updatedf(df, schema, keep_new_cols):
    if schema is None:
        return df

    def _cast_type(sfield, col):
        if df[col].dtype == 'object':
            df[col] = df[col].replace(' ', np.nan)

        if sfield == 'integer':
            df[col] = np.floor(pd.to_numeric(df[col], errors='coerce')).astype('Int64')
        elif sfield == 'boolean':
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('boolean')
        elif sfield in ['double', 'float']:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype(float)
        elif sfield == 'date':
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.date
        elif sfield == 'timestamp':
            df[col] = pd.to_datetime(df[col], errors='coerce')
        elif sfield == 'string':
            df[col] = df[col].astype(str).where(df[col].notna())

    sel_cols = []

    schema = json.loads(schema.to_json())['fields']
    schema_dict = {x['name']: x['type'] for x in schema if not x['name'].startswith('__')}
    for col in df.columns:
        sfield = schema_dict.get(col, None)
        if sfield is None:
            if keep_new_cols:
                sel_cols.append(col)
        else:
            try:
                sel_cols.append(col)
                _cast_type(sfield, col)

            except Exception as e:
                raise Exception(f"Error converting column: {col} to "
                                f"datatype: {sfield} original: {df[col].dtype}") from e

    df = df[sel_cols]
    for x in schema:
        if (not x['name'].startswith('__')) and x['name'] not in df.columns and x['nullable']:
            df[x['name']] = np.nan
            _cast_type(schema_dict.get(x['name'], None), x['name'])

    cols_order = [col for col in schema_dict.keys() if col in df.columns]
    if keep_new_cols:
        cols_order += [x for x in sel_cols if x not in cols_order]

    return df[cols_order]


def is_delta(delta_path):
    return os.path.exists(delta_path) and os.path.exists(os.path.join(delta_path, '_delta_log'))


def get_delta_schema(delta_path):
    schema = None
    if is_delta(delta_path):
        delta_table = DeltaTable(delta_path)
        pa_schema = delta_table.schema().to_pyarrow()
        remove_indices = []
        for i, f in enumerate(pa_schema.names):
            if f.startswith('__index'): remove_indices.append(i)

        offset = 0
        for i in remove_indices:
            pa_schema = pa_schema.remove(i - offset)
            offset += 1

        schema = schema_from_pyarrow(pa_schema)

    return schema


def update_schema_using_df(delta_schema, df):
    pa_schema = None
    if delta_schema is not None:
        pa_schema = delta_schema.to_pyarrow()
        if df is not None:
            new_cols = []
            for col in df.columns:
                if col not in [x['name'] for x in json.loads(delta_schema.to_json())['fields']]:
                    new_cols.append(col)
            if len(new_cols) > 0:
                logging.info(f'new columns in schema: {new_cols}')
                new_schema = pa.Schema.from_pandas(df)
                for nc in new_cols:
                    col_schema = new_schema.field_by_name(nc)
                    if pa.types.is_null(col_schema.type) or nc.startswith('__index'):
                        logging.warning(f'null data type or invalid column: {nc}. dropping.')
                    else:
                        pa_schema = pa_schema.append(col_schema)

    elif df is not None:
        object_columns = df.select_dtypes(include=['object']).columns
        df[object_columns] = df[object_columns].astype(str)
        pa_schema = pa.Schema.from_pandas(df)

    remove_indices = []
    for i, pf in enumerate(pa_schema.names):
        col_schema = pa_schema.field_by_name(pf)
        if pa.types.is_null(col_schema.type) or pf.startswith('__index'):
            logging.warning(f'null data type or invalid column: {pf}. dropping.')
            remove_indices.append(i)

    offset = 0
    for i in remove_indices:
        pa_schema = pa_schema.remove(i - offset)
        offset += 1

    ret_schema = schema_from_pyarrow(pa_schema)

    return ret_schema


def _handle_failed_df(full_path, new_schema_df, partitioncolumn, pa_schema, delta_schema, mode):
    def combined_schema():
        if delta_schema is None:
            return pa_schema
        return '\n'.join([f'{x} | {y}' for x, y in zip_longest(pa_schema.to_string().split('\n'),
                                                               delta_schema.to_pyarrow().to_string().split('\n'),
                                                               fillvalue=None)])

    logging.error(f'error {mode} writing to: {full_path}: {new_schema_df.shape}\n'
                  f'schema:\n{combined_schema()}')
    if mode == 'append':
        # 1. rename the old schema table to another folder created at that timestamp
        current_time = datetime.datetime.now()
        new_path = full_path + '__clone__' + current_time.strftime("%Y%m%d_%H%M%S_%f") + '__' + str(uuid.uuid4())
        os.rename(full_path, new_path)

        # 2. write combined schema table WITHOUT old data but with new data to same path
        create_new_delta_using_existing_delta(new_path, full_path, new_schema_df, partitioncolumn)
        return True
    else:
        return False


def write_delta_wrapper(write_path, df, schema, mode):
    write_engine = 'pyarrow' if len(df) == 0 else 'rust'
    schema_mode = 'merge' if write_engine == 'rust' else 'overwrite'
    schema_str = 'using schema' if schema is not None else ''
    logging.info(f'writing {schema_str}, {write_engine}, {schema_mode} df: {df.shape}, {write_path}')
    write_deltalake(write_path, df, schema=schema.to_pyarrow(), mode=mode, schema_mode=schema_mode, engine=write_engine,
                    configuration={"delta.logRetentionDuration": "interval 1 day"})


def create_new_delta_using_existing_delta(existing_path, write_path, write_df=None, partitioncolumn=None):
    delta_schema = get_delta_schema(existing_path)

    if write_df is None:
        write_df = delta_schema.to_pyarrow().empty_table()
    else:
        write_df = _updatedf(write_df, delta_schema, True)

    delta_schema_updated = update_schema_using_df(delta_schema, write_df)
    logging.info(f'attempting write to: {write_path}: {write_df.shape}')

    write_delta_wrapper(write_path, write_df, schema=delta_schema_updated, mode='overwrite')


def clear_delta_log(full_path):
    logging.info(f'clearing delta log: {full_path}')
    new_temp_path = full_path + '_' + str(uuid.uuid4())
    if is_delta(full_path):
        dt = DeltaTable(full_path)

        partition_cols = dt.metadata().partition_columns

        logging.info(f'reading to pandas: {full_path}')
        df = DeltaTable(full_path).to_pandas()

        logging.info(f'creating clone in temp path: {full_path} to {new_temp_path}')
        create_new_delta_using_existing_delta(full_path, new_temp_path, df, partition_cols)

        logging.info(f'deleting path: {full_path}')
        delete_dir = full_path + f'_delete_{str(uuid.uuid4())}'
        os.rename(full_path, delete_dir)
        shutil.rmtree(delete_dir)

        logging.info(f'renaming temp path to: {full_path}')
        os.rename(new_temp_path, full_path)


def clear_delta_rows_log(full_path):
    clear_delta_log(full_path)
    clear_delta_rows(full_path, vacuum=True)


def clear_delta_rows(full_path, vacuum=True):
    if is_delta(full_path):
        logging.info(f'clearing all rows from delta: {full_path}')
        dlfile = DeltaTable(full_path)
        dlfile.delete()
        if vacuum:
            dlfile.vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
        dlfile.optimize.compact()

    for f in glob.glob(full_path + '__clone__*'):
        shutil.rmtree(f)


def optimize_table(full_path, columns=None):
    dt = DeltaTable(full_path)
    if len(dt.files()) > MAX_DELTATABLE_FILES:
        logging.info(f'optimizing: {full_path}')
        dt.optimize.compact()
        dt.cleanup_metadata()
        dt.vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)

    if not columns is None:
        logging.info(f'z-optimizing: {full_path} {columns}')
        dt.optimize.z_order(columns)


def pandastodelta(df, table, dir, partitioncolumn=None, overwrite=False, is_schema_chg=False, auto_schema_chg=True,
                  delta_schema=None):
    if not is_schema_chg:
        try:
            return pandastodelta_noschemachg(df, table, dir, partitioncolumn=partitioncolumn, overwrite=overwrite,
                                             delta_schema=delta_schema)
        except Exception as e:
            if not auto_schema_chg:
                raise e

    logging.warning(f"schema changed!"
                    f"writing df to delta file: "
                    f"{dir}/{table}, partitioned by: {partitioncolumn}, size: {df.shape}, overwrite: {overwrite}")

    full_path = os.path.join(dir, table)
    if is_delta(full_path):
        dt = DeltaTable(full_path)
        if partitioncolumn is None:
            partitioncolumn = dt.metadata().partition_columns

    delta_schema = get_delta_schema(os.path.join(dir, table))
    if delta_schema:
        logging.info(f'old schema:\n{delta_schema.to_pyarrow()}')
    df = _updatedf(df, delta_schema, keep_new_cols=True)
    delta_schema_updated = update_schema_using_df(delta_schema, df)
    logging.info(f'new schema:\n{delta_schema_updated.to_pyarrow()}')

    dt = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    uid = str(uuid.uuid4())
    new_table = f'{table}__clone__{dt}__{uid}'
    logging.info(f'writing new schema df: {dir}/{new_table}')
    pandastodelta_noschemachg(df, new_table, dir, partitioncolumn, overwrite=True, delta_schema=delta_schema_updated)

    logging.info(f'merging {table} with {new_table}')
    dt1 = DeltaTable(os.path.join(dir, table))
    dt2 = DeltaTable(os.path.join(dir, new_table))
    dt2.merge(source=dt1.to_pyarrow_dataset(), predicate='1 = 2', source_alias='source',
              target_alias='target').when_not_matched_insert_all().execute()

    logging.info(f'swapping {new_table} with {table}')
    os.rename(os.path.join(dir, table), os.path.join(dir, f"{new_table}_completed"))
    os.rename(os.path.join(dir, new_table), os.path.join(dir, table))

    logging.info(f'removing {new_table}_completed')
    shutil.rmtree(os.path.join(dir, f"{new_table}_completed"))


def pandastodelta_noschemachg(df, table, dir, partitioncolumn=None, overwrite=False, delta_schema=None):
    logging.info(f"writing df to delta file: "
                 f"{dir}/{table}, partitioned by: {partitioncolumn}, size: {df.shape}, overwrite: {overwrite}")

    full_path = os.path.join(dir, table)
    if is_delta(full_path):
        dt = DeltaTable(full_path)
        partitioncolumn = dt.metadata().partition_columns
        optimize_table(full_path)

    def _write_df(dftowrite):
        delta_schema_updated = get_delta_schema(full_path) or delta_schema
        delta_schema_updated = update_schema_using_df(delta_schema_updated, dftowrite)
        dftowrite = _updatedf(dftowrite, delta_schema_updated, True)
        write_delta_wrapper(full_path, dftowrite, schema=delta_schema_updated, mode=mode)

    logging.info(f'writing df: {df.shape} using partition column: {partitioncolumn}')
    os.makedirs(full_path, exist_ok=True)

    if len(df) == 0:
        mode = 'overwrite' if overwrite else 'append'
        _write_df(df)

    elif (partitioncolumn is None) or (len(partitioncolumn) == 0):
        mode = 'overwrite' if overwrite else 'append'
        for i, _df_chunk in enumerate(chunks_df(df, WRITE_DF_CHUNK_SIZE)):
            logging.info(f'writing {i}th chunk of df: {_df_chunk.shape} / {df.shape}')
            _write_df(_df_chunk)
            mode = 'append'

    else:
        for group_id, _dfgroup in df.groupby(partitioncolumn):
            mode = 'overwrite' if overwrite else 'append'
            logging.info(f'writing df group: {group_id} {_dfgroup.shape} {mode}')
            for i, _df_chunk in enumerate(chunks_df(_dfgroup, WRITE_DF_CHUNK_SIZE)):
                logging.info(f'writing {i}th chunk of df group: {group_id} {_df_chunk.shape} / {_dfgroup.shape}')
                _write_df(_df_chunk)
                mode = 'append'


def merge_df_with_delta(source_df, target_dt, id_cols, overwrite=False):
    if target_dt is None:
        logging.warning("Target Delta table doesn't exist. Creating a new one.")
        return

    source_columns = set(source_df.columns)
    target_columns = set([x['name'] for x in json.loads(target_dt.schema().to_json())['fields']])

    duplicate_columns = list(source_columns.intersection(target_columns) - set(id_cols))
    new_columns = list(source_columns - target_columns)

    merge_condition = ' AND '.join([f"source.{col} = target.{col}" for col in id_cols])

    insert_dict = {col: f"source.{col}" for col in source_columns}
    update_dict = {col: f"source.{col}" for col in new_columns}
    if overwrite:
        update_dict.update({col: f"source.{col}" for col in duplicate_columns})

    source_pa = pa.Table.from_pandas(source_df)

    logging.info(f'merging source_df: {source_df.shape} with target_dt on: {merge_condition}')
    target_dt.merge(
        source=source_pa,
        predicate=merge_condition,
        source_alias="source",
        target_alias="target") \
        .when_matched_update(updates=update_dict) \
        .when_not_matched_insert(updates=insert_dict).execute()

    logging.info("merge completed successfully!")
    return target_dt


def schema_from_pyarrow(arrow_schema):
    adjusted_schema = pa.schema([
        (name, pa.timestamp('us') if pa.types.is_timestamp(field_type) else field_type)
        for name, field_type in zip(arrow_schema.names, arrow_schema.types)
    ])

    null_adjusted_schema = pa.schema([
        (field.name, pa.string() if field.type == pa.null() else field.type)
        for field in adjusted_schema
    ])

    delta_schema = Schema.from_pyarrow(null_adjusted_schema)
    schema_fields = delta_schema.fields
    schema_fields_updated = [x if not 'timestamp' in str(x.type) else Field(x.name, 'timestamp') for x in
                             schema_fields]
    return Schema(schema_fields_updated)


def combine_csvs_from_zip(zip_file_path, read_func, file_ext):
    dfs = []

    with zipfile.ZipFile(zip_file_path, 'r') as z:
        for file_name in z.namelist():
            if file_name.endswith(file_ext):
                with z.open(file_name) as f:
                    df = read_func(f)
                    dfs.append(df)

    combined_df = pd.concat(dfs, ignore_index=True)
    return combined_df


def to_naming_convention_string(s, replace_symbols=True, keep_unchanged=None):
    keep_unchanged = keep_unchanged or ['_']

    keep_pattern = '|'.join(re.escape(word) for word in keep_unchanged)

    def process_part(part):
        if replace_symbols:
            part = part.replace(' ', '_').replace('.', '_').replace('/', '_').replace('-', '_')
        part = re.sub(r'\W+', '', part)

        part = re.sub('([a-z0-9])([A-Z])', r'\1_\2', part)
        part = re.sub(r'_{2,}', '_', part)
        return part.strip('_').lower()

    parts = re.split(f'({keep_pattern})', s)

    processed_parts = [
        process_part(part) if part not in keep_unchanged else part for part in parts
    ]

    ret = '_'.join(processed_parts)
    if ret.startswith('_'):
        ret = ret[1:]
    if ret.endswith('_'):
        ret = ret[:-1]
    return ret.replace('__', '_').replace(' ', '').lower()


if __name__ == "__main__":
    setuplogger('dfutil3', loglevel=logging.DEBUG)
    from apo_config.config import config
    from data_access.spark.azuredb import AzureDB

    config.init('dev')

    remotedb = AzureDB(local_dir=None, remote_dir='tables/features/ice')
    source_df = pd.DataFrame({'isin_number': ['US251526CM97', 'US036752AQ61'], 'test5': ['TEST-1', 'TEST-2']})

    remotedb.merge_df(source_df, 'ticker_isin_univ', ['isin_number'], overwrite=True)
