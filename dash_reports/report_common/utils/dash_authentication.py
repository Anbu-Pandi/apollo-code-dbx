from dash_reports.report_common.data.data_model import datastore
from data_access.dbtables.dash_reports_misc import access_logs


def dash_access_check(ad_group, link, access_level='read'):
    key = 'dash_access'
    df = datastore.get_app_key(key)
    if df is None:
        df = access_logs.get_dash_app_access()
        datastore.set_app_key(key, df)

    path = str(link or '').rstrip('/')
    routes = df['link'].astype(str).str.rstrip('/')
    matches = routes.map(lambda route: path == route or path.startswith(f'{route}/'))
    allowed = df[matches & (df['access_level'] == access_level)]
    groups = [ad_group] if isinstance(ad_group, str) else ad_group or []
    return allowed.empty or allowed['ad_group'].isin(groups).any()
