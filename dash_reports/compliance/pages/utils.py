import dash_html_components as html
import dash_core_components as dcc
import dash
import json

from dash_reports.report_common.utils.components import create_link

MARKDOWN_STYLE = {'font-size': '14px', 'color': 'red', 'font-weight': 'bold'}

COMPLIANCE_SUBMIT = 'go_private_compliance_submit'
COMPLIANCE_RADIO_TYPE_ID = 'compliance_radio_type_id'
COMPLIANCE_TEXT_TYPE_ID = 'compliance_text_type_id'
COMPLIANCE_TEXT_AREA_TYPE_ID = 'compliance_text_type_id'
COMPLIANCE_DROPDOWN_TYPE_ID = 'compliance_dropdown_type_id'
COMPLIANCE_DATE_TYPE_ID = 'compliance_date_type_id'
COMPLIANCE_OUTPUT_CONTAINER_ID = 'compliance_output'
COMPLIANCE_APPROVE_BUTTON_ID = 'compliance_approve'
COMPLIANCE_REJECT_BUTTON_ID = 'compliance_reject'
COMPLIANCE_ADD_RESTRICTION_BUTTON_ID = 'add_restriction_button'
COMPLIANCE_REQUEST_ID_SELECTION = 'compliance_request_id_selection'
COMPLIANCE_ON_APPROVE_REJECT_OUTPUT = 'compliance_approve_reject_output'
COMPLIANCE_STATUS_CHECK_LIST = 'compliance_status_check_list'
COMPLIANCE_SUBMIT_TYPE = 'compliance_submit_button'
COMPLIANCE_SUBMIT_APPROVAL = 'compliance_approve'
COMPLIANCE_SUBMIT_REJECT = 'compliance_reject'
COMPLIANCE_REMOVE_RESTRICTION_TYPE = 'restriction_remove_button'
COMPLIANCE_DATE_ENABLE_CHECKLIST_ID = 'date_enable'
COMPLIANCE_SUBMIT_STATUS = 'compliance_submit_status'
COMPLIANCE_RESTRICTION_DETAIL_CONTAINER = 'restriction_detail_container'

COMPLIANCE_STATUS_DICT = {COMPLIANCE_APPROVE_BUTTON_ID: 'approved',
                          COMPLIANCE_REJECT_BUTTON_ID: 'rejected'}


def get_links():
    menu = html.Div([create_link('compliance', 'go-private-request', 'Go Private Request'),
                     create_link('compliance', 'go-private-request-status', 'Status'),
                     create_link('compliance', 'go-private-request-group-request-id', 'Group Request ID'),
                     ],
                    className="row all-tabs", )
    return menu


def get_section_title(section_title):
    return html.Div([section_title], className="sub_header")


def get_section_heading(section):
    return ' '.join(word for word in section.split('_')).title()


def get_callback_trigger_component_id():
    ctx = dash.callback_context
    component_key = json.loads(ctx.triggered[0]["prop_id"].split(".")[0])['index']
    return component_key, len(ctx.inputs_list[0])