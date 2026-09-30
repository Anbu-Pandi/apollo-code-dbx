from dash import dcc

LOGGED_USER_ID = 'session_user_name'
LOGGED_USER_GROUPS_ID = 'session_user_groups'


def get_logged_user_controls():
    return [dcc.Store(id=LOGGED_USER_ID, storage_type='session'),
            dcc.Store(id=LOGGED_USER_GROUPS_ID, storage_type='session')]
