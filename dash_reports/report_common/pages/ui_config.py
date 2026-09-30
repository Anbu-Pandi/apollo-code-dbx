import logging

from dash import html
from dash import callback, ALL, Input, Output, State

from dash_reports.report_common.data import ui_config
from dash_reports.report_common.pages import user_info

SAVE_CONFIG_INPUT_TYPE = 'save_config_input_type'
SAVE_CONFIG_SUBMIT_ID = 'save_config_submit_id'
RESET_CONFIG_SUBMIT_ID = 'reset_config_submit_id'
SAVE_CONFIG_MSG_ID = 'save_config_msg_id'
RESET_CONFIG_MSG_ID = 'reset_config_msg_id'


def get_control_value(control_id, user_settings, default_value=None):
    return user_settings.get(control_id, default_value)


def get_save_config_submit():
    return html.Div(style=dict(display='flex'),
                    children=[html.Div([html.Div(id=SAVE_CONFIG_MSG_ID, style=dict(fontSize='xx-small')),
                                        html.Div(id=RESET_CONFIG_MSG_ID, style=dict(fontSize='xx-small'))],
                                       style=dict(width='50%')),
                              html.Button('Save View', id=SAVE_CONFIG_SUBMIT_ID,
                                          className='submit-button',
                                          style=dict(fontSize='xx-small', width='25%')),
                              html.Button('Reset View', id=RESET_CONFIG_SUBMIT_ID,
                                          className='submit-button',
                                          style=dict(fontSize='xx-small', width='25%')),
                              ],
                    className='fund_filter')


@callback([Output(SAVE_CONFIG_MSG_ID, "children"), ],
          [Input(SAVE_CONFIG_SUBMIT_ID, 'n_clicks'), ],
          [State({'type': SAVE_CONFIG_INPUT_TYPE, 'index': ALL}, 'id'),
           State({'type': SAVE_CONFIG_INPUT_TYPE, 'index': ALL}, 'value'),
           State(user_info.LOGGED_USER_ID, 'data'),
           State("url", "pathname")],
          prevent_initial_call=True)
def on_save_config(n_clicks, input_keys, input_values, user, page):
    settings = {k['index']: v for k, v in zip(input_keys or [], input_values or [])}
    if len(settings) > 0:
        ui_config.save_page_config_by_user(user, page, settings)
        logging.info(f'saved ui config for user: {user}, {page}, {settings}')
        return ['View saved successfully!']
    return ['']


@callback([Output(RESET_CONFIG_MSG_ID, "children"), ],
          [Input(RESET_CONFIG_SUBMIT_ID, 'n_clicks'), ],
          [State(user_info.LOGGED_USER_ID, 'data'),
           State("url", "pathname")],
          prevent_initial_call=True)
def on_reset_config(n_clicks, user, page):
    ui_config.reset_page_config_by_user(user, page)
    logging.info(f'resetted ui config for user: {user}, {page}')
    return ['View reset successfully!']
