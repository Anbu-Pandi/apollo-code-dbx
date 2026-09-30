import pandas as pd
import logging
from dash.exceptions import PreventUpdate
from dash import Input, Output, State, ALL, MATCH, callback, dcc

from dash_reports.report_common.elastic_search.api import default_es
import dash_reports.go_private_form.data.analyst as analyst_data
import dash_reports.go_private_form.data.utils as data_utils

import dash_reports.go_private_form.pages.generic_info as generic_info
import dash_reports.go_private_form.pages.deal_information_section as deal_info
import dash_reports.go_private_form.pages.utils as go_pvt_utils
import dash_reports.go_private_form.pages.entity_request_type as entity_request_type
from dash_reports.report_common.utils.dash_authentication import dash_access_check
from dash_reports.report_common.pages import user_info


@callback(
    [Output({'type': go_pvt_utils.UPDATE_ANALYST_DIV_CONTENT_ID, 'index': MATCH}, component_property='children')],
    [Input({'type': go_pvt_utils.ANALYST_RADIO_TYPE_ID, 'index': MATCH}, component_property='value')],
    prevent_initial_call=True
)
def update_div_kids(value):
    component_key, input_count = go_pvt_utils.get_callback_trigger_component_id()
    if component_key == 'company_identifier':
        return [entity_request_type.on_entity_identifier_selection(component_key, value)]
    if component_key in ['deal_transaction_info', 'other_circumstances']:
        return [deal_info.on_info_to_receive_selection(component_key, value)]
    elif component_key == 'business_unit':
        return [generic_info.on_generic_other_business_unit(component_key, value)]
    elif component_key == 'request_type':
        return [entity_request_type.on_request_type_selection(component_key, value)]
    else:
        return []


@callback([Output(component_id=go_pvt_utils.ANALYST_SUBMIT_STATUS, component_property='children')],
          [Input({'type': go_pvt_utils.ANALYST_SUBMIT, 'index': 'go_pvt_submit'}, 'n_clicks')],
          [State({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.ANALYST_TEXT_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.ANALYST_RADIO_TYPE_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.UPDATE_ANALYST_DIV_CONTENT_ID, 'index': ALL}, 'value'),
           State({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.ANALYST_TEXT_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.ANALYST_RADIO_TYPE_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.UPDATE_ANALYST_DIV_CONTENT_ID, 'index': ALL}, 'id'),
           State({'type': go_pvt_utils.ANALYST_ATTACHMENT_TYPE_ID, 'index': ALL}, "filename"),
           State({'type': go_pvt_utils.ANALYST_ATTACHMENT_TYPE_ID, 'index': ALL}, "contents"),
           State(user_info.LOGGED_USER_ID, 'data'),
           State(user_info.LOGGED_USER_GROUPS_ID, 'data'),
           State("url", "pathname")

           ],
          prevent_initial_call=True
          )
def on_analyst_response_button(n_clicks, dropdown_container_values, text_box_values, radio_button_values,
                               sub_ques_id, dropdown_container_id, text_box_id, radio_button_id, sub_ques_values,
                               attachment_name, attachments, user_id, user_groups, pathname):
    if n_clicks is None:
        raise PreventUpdate

    if dash_access_check(user_groups, pathname, access_level='write'):

        if not isinstance(attachment_name[0], list):
            attachment_name = [[attachment_name[0]]]

        analyst_response = {
            'dropdown': {'ids': dropdown_container_id, 'response': dropdown_container_values},
            'radio': {'ids': radio_button_id, 'response': radio_button_values},
            'txt': {'ids': text_box_id, 'response': text_box_values},
            'attachment': {'ids': [{'index': go_pvt_utils.FILE_UPLOAD_ID, 'type': ''}],
                           'response': ','.join(str(file) for file in attachment_name[0])},
            'user_id': {'ids': [{'type': 'user_id', 'index': 'user_id'}], 'response': [user_id]}
        }

        attachment_details = {'attachment_name': attachment_name[0], 'attachments': attachments[0]}

        display_string = analyst_data.insert_analyst_response(analyst_response, attachment_details, user_id,
                                                              request_type='go_private')
    else:
        display_string = "You don't have write permissions"
    return [dcc.Markdown([display_string], style=go_pvt_utils.MARKDOWN_STYLE)]


@callback(
    [Output({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "options"),
     Output({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "value"),
     ],
    Input({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "search_value"),
    Input({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "value"),
    State({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "options"),
    State({'type': go_pvt_utils.ANALYST_DROPDOWN_TYPE_ID, 'index': MATCH}, "id"),
    prevent_initial_call=True
)
def update_options(search_value, selected_id, options, id):
    component_key, input_count = go_pvt_utils.get_callback_trigger_component_id()

    df = pd.DataFrame()
    if component_key in [entity_request_type.COMPANY_IDENTIFIER_SECURITY_ID]:
        if search_value:
            logging.info(f'searching for value: {search_value}')
            df = default_es.search_prefix([data_utils.ES_BBG_ISSUERS_INDEX,
                                           data_utils.ES_BBG_BONDS_INDEX,
                                           data_utils.ES_WSO_LOANS_INDEX],
                                          ['id_bb_global_company_name', 'id_isin', 'id_cusip', 'id_bb_global',
                                           'ticker'],
                                          search_value or -1)
            df = df.drop_duplicates(subset=['id_bb_global_company_name', 'id_bb_company'])

        if len(df) > 0:
            options = [{'label': x['id_bb_global_company_name'],
                        'value': int(x['id_bb_company'])}
                       for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]
            if len(df) == 1:
                selected_id = int(df.iloc[0]['id_bb_company'])

    return [options, selected_id]


@callback(
    Output({'type': "file-upload-status", 'index': MATCH}, "children"),
    [Input({'type': go_pvt_utils.ANALYST_ATTACHMENT_TYPE_ID, 'index': MATCH}, "filename"),
     Input({'type': go_pvt_utils.ANALYST_ATTACHMENT_TYPE_ID, 'index': MATCH}, "contents")],
    prevent_initial_call=True
)
def attachment_detail(uploaded_filename, uploaded_file_contents):
    return dcc.Markdown([f'Attachments Added - {uploaded_filename}'], style=go_pvt_utils.MARKDOWN_STYLE)
