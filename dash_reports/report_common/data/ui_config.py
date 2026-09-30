import datetime
import json
import logging

import pandas as pd

from dash_reports.report_common.data.data_model import datastore
from data_access.dbtables.dash_reports_misc import ui_config

UI_CONFIG_KEY = 'DASH_UI_CONFIG'


def get_page_config_by_user(user, page):
    df = datastore.get_app_key(UI_CONFIG_KEY)
    if df is None:
        df = ui_config.get_ui_config()
        datastore.set_app_key(UI_CONFIG_KEY, df)

    df = df[(df['user_id'] == user) & (df['page'] == page)]

    page_config = {}
    if len(df) > 1:
        logging.warning(f'multiple configs found for user: {user}, page: {page}\n{df}')
    if len(df) > 0:
        df = df.sort_values('last_updated')
        page_config = json.loads(df.iloc[0]['settings'])

    return page_config


def save_page_config_by_user(user, page, settings):
    dt = datetime.datetime.now()
    user_df = pd.DataFrame(
        {'user_id': [user], 'page': [page], 'settings': [json.dumps(settings)], 'last_updated': [dt]})
    ui_config.save_ui_config(user_df)

    df = datastore.get_app_key(UI_CONFIG_KEY)
    if df is None:
        df = pd.DataFrame()
    else:
        df = df[~((df['user_id'] == user) & (df['page'] == page))]

    user_df = user_df.astype(df.dtypes.to_dict())
    df = pd.concat([user_df, df])
    datastore.set_app_key(UI_CONFIG_KEY, df)


def reset_page_config_by_user(user, page):
    ui_config.delete_ui_config(user, page)

    df = datastore.get_app_key(UI_CONFIG_KEY)
    if not df is None:
        df = df[~((df['user_id'] == user) & (df['page'] == page))]
        datastore.set_app_key(UI_CONFIG_KEY, df)
