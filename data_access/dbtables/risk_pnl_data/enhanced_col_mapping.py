from common.utils.sqlutil import readdfsql
from data_access.dbtables import BETA_EST_SUBAPP as APP
import logging

_BASE_QUERY = (
    "Select {columns} "
    "from dbo.enhanced_col_mapping "
    "where is_deprecated <> True"
)


def get_enhanced_tbl_names(source, db_to_file_col_map=False):
    logging.info(f'Getting column mapping for enhanced - {source} ')
    sql = _BASE_QUERY.format(columns="file_col_name, table_col_name") + \
          f" and source = '{source}'"

    df = readdfsql(sql, app=APP)
    if df.empty:
        logging.info(f'No column mapping found for enhanced - {source}')
        return {}

    if db_to_file_col_map:
        col_mapping_dict = dict(zip(df['table_col_name'], df['file_col_name']))
    else:
        col_mapping_dict = dict(zip(df['file_col_name'], df['table_col_name']))

    return col_mapping_dict


def get_enhanced_col_by_dtype(source, dtype):
    logging.info(f'Getting column of data type - {dtype} for enhanced - {source} ')
    sql = _BASE_QUERY.format(columns="file_col_name") + \
          f" and source = '{source}' and data_type = '{dtype}' order by col_order asc"

    df = readdfsql(sql, app=APP)
    if df.empty:
        logging.info(f'No columns found for enhanced - {source} and data type - {dtype}')
        return {}

    return df['file_col_name'].to_list()


def get_enhanced_file_col_order(source):
    logging.info(f'Getting column order for enhanced - {source} ')
    sql = _BASE_QUERY.format(columns="file_col_name") + \
          f" and source = '{source}' order by col_order asc"

    df = readdfsql(sql, app=APP)
    if df.empty:
        logging.info(f'No columns found for enhanced - {source}')
        return []

    return df['file_col_name'].to_list()


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')
    df = get_enhanced_col_by_dtype('pnl', 'float')
    print(df)
