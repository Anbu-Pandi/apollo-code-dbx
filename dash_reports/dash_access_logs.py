import codecs
import logging
import os

import pandas as pd
from dateutil import parser

from apo_config.config import required_config
from common.utils import logutil
from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.app_utils.prepare_app import get_dash_report_log_file
from data_access.dbtables.dash_reports_misc import access_logs

def _log_hosts():
    value = required_config("dash_reports", "log_hosts")
    return tuple(host.strip() for host in value.split(",") if host.strip())


def _log_folders():
    name = get_dash_report_log_file()
    candidates = [os.path.join(logutil.get_log_folder(), name)]
    candidates += [fr'\\{host}\e\apo_logs\{name}' for host in _log_hosts()]
    seen, folders = set(), []
    for path in candidates:
        if not os.path.exists(path):
            continue
        key = os.path.normcase(os.path.abspath(path)) if not path.startswith(r'\\') else os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        folders.append(path)
    return folders


def _access_log_files(log_folder):
    prefix = get_dash_report_log_file()
    return [
        f for f in os.listdir(log_folder)
        if f.startswith(prefix) and 'config_bootstrap' not in f
    ]


def _parse_log_data(files_list, path, max_ts):
    user_info_data = []
    for file in files_list:
        logging.info(f'Reading file: {path}/{file}')
        with codecs.open(os.path.join(path, file), 'r', encoding='utf-8', errors='ignore') as file_content:
            for line in file_content:
                line = line.strip()
                if 'on url request user' not in line:
                    continue
                dt = parser.parse(line.split('|')[0])
                if dt <= max_ts:
                    continue
                user_info_line = line.split('|')[-1].strip()
                user_info_data.append({
                    'last_access_ts': dt,
                    'user_id': user_info_line[user_info_line.find('user:') + len('user:'):
                                              user_info_line.find('page:')].strip(),
                    'ad_group': user_info_line[user_info_line.find('groups:') + len('groups:'):-1].strip(),
                    'page': user_info_line[user_info_line.find('page:') + len('page:'):
                                           user_info_line.find('params:')].strip(),
                })
    return user_info_data


def update_logs():
    folders = _log_folders()
    if not folders:
        logging.warning(f'No log folders found for {get_dash_report_log_file()}')
        logging.info('Completed')
        return

    max_ts = access_logs.get_user_request_log_last_accessed()
    user_info_data = []
    for log_folder in folders:
        files_list = _access_log_files(log_folder)
        logging.info(f'Parsing {len(files_list)} files from {log_folder} after max_ts={max_ts}')
        user_info_data.extend(_parse_log_data(files_list, log_folder, max_ts))

    df = pd.DataFrame(user_info_data)
    if df.empty:
        logging.info('No new access log entries')
        logging.info('Completed')
        return

    df = df[df['page'] != '/'].drop_duplicates()
    if df.empty:
        logging.info('No new non-root access log entries')
        logging.info('Completed')
        return

    logging.info(f'Inserting {len(df)} access rows')
    access_logs.update_user_request_log(df)
    logging.info('Completed')


if __name__ == '__main__':
    arg_parser = get_base_parser(os.path.split(__file__)[-1], log_file=os.path.split(__file__)[-1])
    init_app_using_parser(arg_parser)
    update_logs()
