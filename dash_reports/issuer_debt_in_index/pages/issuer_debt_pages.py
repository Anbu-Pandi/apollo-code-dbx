from dash import dcc

from common.utils import dtutil
from dash_reports.risk_report.pages.utils import get_datepicker_control
from ...issuer_debt_in_index.pages import utils

import dash_html_components as html
from dash_reports.report_common.pages import ui_config
from dash_reports.report_common.utils import combos


def create_layout_issuer_debt(page, user_settings):
    static_kids = [utils.get_debt_view_index_menu(page)]
    dt = dtutil.dateadd(dtutil.today(), '-1b')
    is_movers_page = True
    if page == 'issuer-spread-curve':
        is_movers_page = False

    issuer = ui_config.get_control_value(utils.INPUT_CONTROL_ID, user_settings)
    industry = ui_config.get_control_value('industry', user_settings)
    instrument_id = ui_config.get_control_value('instrument_id', user_settings)

    dt_control = get_datepicker_control(utils.DATE_CONTROL_ID, dt=dt, disabled=False)
    dt_control = html.Div(style=dict(display='inline' if is_movers_page else 'none'),
                          children=combos.get_labelled_component('Date:', dt_control))

    issuer_input = dcc.Input(id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': utils.INPUT_CONTROL_ID},
                             className='input', value=issuer)
    issuer_input = html.Div(children=combos.get_labelled_component('Issuer:', issuer_input))

    ind_input = dcc.Input(id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'industry'},
                          className='input', value=industry)
    ind_input = html.Div(style=dict(display='inline' if is_movers_page else 'none'),
                         children=combos.get_labelled_component('Industry:', ind_input))

    instru_input = dcc.Input(id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'instrument_id'},
                             className='input', value=instrument_id)
    instru_input = html.Div(style=dict(display='inline' if is_movers_page else 'none'),
                            children=combos.get_labelled_component('Instrument Id:', instru_input))

    submit_btn = html.Div([html.Button('Go', id=utils.DEBT_VIEW_SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=utils.DEBT_VIEW_RESULT_ID, children=[])
    dynamic_kids = html.Div(
        [html.Div([html.Br(), dt_control, issuer_input, ind_input, instru_input, html.Br(), submit_btn, ],
                  className='fund_filter'),
         search_results])
    return static_kids, dynamic_kids
