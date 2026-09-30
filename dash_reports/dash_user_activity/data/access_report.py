import pandas as pd
from data_access.dbtables.dash_reports_misc.access_logs import get_dash_access_logs

ADMIN_USER_IDS = ['hkim', 'agite', 'pbhargava']


def get_dash_access_summary():
    df = get_dash_access_logs()
    df = df[~df['user_id'].isin(ADMIN_USER_IDS)]
    df['last_access_ts'] = pd.to_datetime(df['last_access_ts']).dt.date

    df_user_activity = df.pivot_table(index=['last_access_ts'], values=['user_id'], aggfunc={'user_id': 'count'})
    df_user_activity['rolling_5_day_avg'] = df_user_activity.sort_index().rolling(5).mean()
    df_user_activity.reset_index(inplace=True)
    df_user_activity.rename(columns={'user_id': 'page_views', 'last_access_ts': 'view_date'}, inplace=True)
    df_user_activity.sort_values('view_date', ascending=False, inplace=True)

    df_leaderboard = df.pivot_table(index=['user_id'], values=['page', 'last_access_ts'],
                                    aggfunc={'page': 'count', 'last_access_ts': 'max'})
    df_leaderboard.rename(columns={'page': 'page_views', 'last_access_ts': 'last_activity'}, inplace=True)
    df_leaderboard.sort_values('page_views', ascending=False, inplace=True)
    df_leaderboard.reset_index(inplace=True)

    return df_user_activity, df_leaderboard


if __name__ == '__main__':
    from apo_config.config import config

    config.init('prod')
    get_dash_access_summary()
