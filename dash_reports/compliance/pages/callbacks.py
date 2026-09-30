import json

from datetime import datetime
from dash.exceptions import PreventUpdate
import dash
from dash import Input, Output, State, ALL, MATCH, callback, dcc

import dash_reports.compliance.data.compliance as compliance_data
import dash_reports.compliance.data.group_request as group_request_data
import dash_reports.compliance.pages.utils as go_pvt_utils
import dash_reports.compliance.pages.compliance_info_section as compliance_section
import dash_reports.compliance.pages.group_request_id as group_request
from dash_reports.report_common.utils.dash_authentication import dash_access_check
from dash_reports.report_common.pages import user_info


@callback([Output(component_id=go_pvt_utils.COMPLIANCE_OUTPUT_CONTAINER_ID, component_property='children')],
          [Input('compliance_view_request', 'n_clicks')],
          [State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': 'compliance_selection'}, 'value'),
           State(go_pvt_utils.COMPLIANCE_STATUS_CHECK_LIST, 'value'),
           ],
          prevent_initial_call=True
          )
def on_compliance_view_button(n_clicks, ult_company_id, checked_status):
    if n_clicks is None:
        raise PreventUpdate
    if ult_company_id is None:
        return [dcc.Markdown(['Select Company can not be blank'], style=go_pvt_utils.MARKDOWN_STYLE)]
    else:
        return compliance_section.create_compliance_layout(ult_company_id, checked_status)


@callback([Output(component_id=go_pvt_utils.COMPLIANCE_ON_APPROVE_REJECT_OUTPUT, component_property='children')],
          [Input({'type': go_pvt_utils.COMPLIANCE_SUBMIT, 'index': go_pvt_utils.COMPLIANCE_APPROVE_BUTTON_ID},
                 'n_clicks'),
           Input({'type': go_pvt_utils.COMPLIANCE_SUBMIT, 'index': go_pvt_utils.COMPLIANCE_REJECT_BUTTON_ID},
                 'n_clicks')],
          State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': 'compliance_selection'}, 'value'),
          State(
              {'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': go_pvt_utils.COMPLIANCE_REQUEST_ID_SELECTION},
              'value'),
          prevent_initial_call=True
          )
def on_compliance_approve_reject_button(approve_n_clicks, reject_n_clicks, selected_company_id, request_ids):
    if (approve_n_clicks is None) and (reject_n_clicks is None):
        raise PreventUpdate

    component_key, input_count = go_pvt_utils.get_callback_trigger_component_id()

    if (request_ids is None) or (len(request_ids) == 0):
        return [dcc.Markdown(['Select request ids to continue'], style=go_pvt_utils.MARKDOWN_STYLE)]

    kids = compliance_section.on_approve_reject_layout(component_key, selected_company_id)

    return [kids]


@callback([Output(component_id=go_pvt_utils.COMPLIANCE_SUBMIT_STATUS, component_property='children')],
          [Input({'type': go_pvt_utils.COMPLIANCE_SUBMIT_TYPE, 'index': ALL},
                 'n_clicks')],
          [State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.COMPLIANCE_TEXT_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.COMPLIANCE_RADIO_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.COMPLIANCE_DATE_TYPE_ID, 'index': ALL}, 'date'),
           State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.COMPLIANCE_TEXT_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.COMPLIANCE_RADIO_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.COMPLIANCE_DATE_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID,
                  'index': go_pvt_utils.COMPLIANCE_REQUEST_ID_SELECTION},
                 'value'),
           State(user_info.LOGGED_USER_ID, 'data'),
           State(user_info.LOGGED_USER_GROUPS_ID, 'data'),
           State("url", "pathname")
           ],
          prevent_initial_call=True
          )
def on_compliance_submit_approval(n_clicks, dropdown_container_values, text_box_values,
                                  radio_button_values, date_box_values, dropdown_container_id, text_box_id,
                                  radio_button_id, date_box_id, selected_requested_id, user_id, user_groups, pathname):
    if n_clicks is None:
        raise PreventUpdate

    if dash_access_check(user_groups, pathname, access_level='write'):

        ctx = dash.callback_context
        button_id = json.loads(ctx.triggered[0]['prop_id'].split('.')[0])['index']

        if button_id == go_pvt_utils.COMPLIANCE_APPROVE_BUTTON_ID:
            status = 'approved'
        else:
            status = 'rejected'

        selected_request_id_idx = dropdown_container_id.index({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID,
                                                               'index': go_pvt_utils.COMPLIANCE_REQUEST_ID_SELECTION})
        selected_request_ids = dropdown_container_values[selected_request_id_idx]

        last_updated = datetime.now()

        compliance_response = {
            'dropdown': {'ids': dropdown_container_id, 'response': dropdown_container_values},
            'radio': {'ids': radio_button_id, 'response': radio_button_values},
            'txt': {'ids': text_box_id, 'response': text_box_values},
            'date': {'ids': date_box_id, 'response': date_box_values},
            'user_id': {'ids': [{'type': 'user_id', 'index': 'user_id'}], 'response': [user_id]}
        }

        display_string = compliance_data.process_compliance_submission(compliance_response, selected_request_ids,
                                                                       status,
                                                                       last_updated, user_id)
    else:
        display_string = "You don't have write permissions"
    return [dcc.Markdown([display_string], style=go_pvt_utils.MARKDOWN_STYLE)]


@callback([Output(component_id=go_pvt_utils.COMPLIANCE_RESTRICTION_DETAIL_CONTAINER, component_property='children')],
          [Input(go_pvt_utils.COMPLIANCE_ADD_RESTRICTION_BUTTON_ID, 'n_clicks'),
           Input({'type': go_pvt_utils.COMPLIANCE_REMOVE_RESTRICTION_TYPE, 'index': ALL}, 'n_clicks')],
          [State(component_id=go_pvt_utils.COMPLIANCE_RESTRICTION_DETAIL_CONTAINER, component_property='children'),
           State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': 'compliance_selection'}, 'value')],
          prevent_initial_call=True
          )
def manage_compliance_restriction(add_n_clicks, remove_n_clicks, existing_children, selected_company_id):
    ctx = dash.callback_context

    if not ctx.triggered:
        raise PreventUpdate
    else:
        button_id, _ = ctx.triggered[0]["prop_id"].split(".")

    if button_id == go_pvt_utils.COMPLIANCE_ADD_RESTRICTION_BUTTON_ID:
        existing_children.extend(compliance_section.compliance_info_section(selected_company_id, counter=add_n_clicks))
    else:
        button_id = json.loads(button_id)
        index_to_remove = button_id["index"]
        existing_children = [child for child in existing_children
                             if child['props']['id']['index'] != index_to_remove]
    return [existing_children]


@callback([Output(component_id={'type': go_pvt_utils.COMPLIANCE_DATE_TYPE_ID, 'index': MATCH},
                  component_property='disabled')],
          [Input({'type': go_pvt_utils.COMPLIANCE_DATE_ENABLE_CHECKLIST_ID, 'index': MATCH}, 'value')],
          [State({'type': go_pvt_utils.COMPLIANCE_DATE_ENABLE_CHECKLIST_ID, 'index': MATCH}, 'options')],
          prevent_initial_call=True
          )
def on_compliance_date_enable(enable_flag, options):
    if enable_flag is None:
        raise PreventUpdate

    flag = [option["value"] for option in options if enable_flag]

    if 'enable' in flag:
        return [False]
    else:
        return [True]


@callback([Output(component_id=group_request.GROUP_REQUEST_OUTPUT_DIV_ID, component_property='children')],
          [Input(group_request.GROUP_REQUEST_SUBMIT_BUTTON_ID, 'n_clicks')],
          [
              State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID,
                     'index': group_request.GROUP_REQUEST_COMPANY_SELECTION},
                    'value'),
              State({'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID,
                     'index': group_request.GROUP_REQUEST_ANALYST_SELECTION},
                    'value'),
              State('group_request_checklist', 'value')],
          prevent_initial_call=True
          )
def on_group_request_submit(n_clicks, company, analyst, status):
    if n_clicks is None:
        raise PreventUpdate

    if (company is None or len(company) == 0) & (analyst is None or len(analyst) == 0):
        return [dcc.Markdown(['Select company or analyst name to get requests'], style=go_pvt_utils.MARKDOWN_STYLE)]

    kids = group_request.on_group_restrictions_submit(company, analyst, status)
    return [kids]


@callback([Output(component_id=group_request.GROUP_REQUEST_INSERT_STATUS, component_property='children')],
          [Input(group_request.GROUP_REQUEST_INSERT_BUTTON, 'n_clicks')],
          [
              State(
                  {'type': go_pvt_utils.COMPLIANCE_DROPDOWN_TYPE_ID, 'index': group_request.GROUP_REQUEST_ID_SELECTION},
                  'value'),
              State(user_info.LOGGED_USER_ID, 'data'),
              State(user_info.LOGGED_USER_GROUPS_ID, 'data'),
              State("url", "pathname")
          ],
          prevent_initial_call=True
          )
def on_group_request_insert(n_clicks, request_id, user, user_groups, pathname):
    if n_clicks is None:
        raise PreventUpdate

    if dash_access_check(user_groups, pathname, access_level='write'):

        display_string = 'Select Request ID to assign an Event ID'
        if not len(request_id) == 0:
            display_string = group_request_data.insert_request_event_map(request_id, user)
    else:
        display_string = "You don't have write permissions"

    return [dcc.Markdown([display_string], style=go_pvt_utils.MARKDOWN_STYLE)]
