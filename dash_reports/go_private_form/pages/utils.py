import dash_html_components as html
import dash_core_components as dcc
import dash
import json
import pandas as pd

from dash_reports.go_private_form.data.utils import get_analyst_company_attribs, get_analyst_issuer_coverage
from dash_reports.report_common.utils.components import get_data_list_component, create_link
from common.utils.dtutil import today

MARKDOWN_STYLE = {'font-size': '14px', 'color': 'red', 'font-weight': 'bold'}

UPDATE_ANALYST_DIV_CONTENT_ID = 'update_go_private_from_div_content_id'
ANALYST_SUBMIT = 'go_private_analyst_submit'
ANALYST_RADIO_TYPE_ID = 'analyst_radio_type_id'
ANALYST_TEXT_TYPE_ID = 'analyst_text_type_id'
ANALYST_TEXT_AREA_TYPE_ID = 'analyst_text_type_id'
ANALYST_DROPDOWN_TYPE_ID = 'analyst_dropdown_type_id'
ANALYST_DATE_TYPE_ID = 'analyst_date_type_id'
ANALYST_ATTACHMENT_TYPE_ID = 'analyst_attachment_type_id'
ANALYST_COMPANY_NAME_LIST_ID = 'company_name_list_id'
ANALYST_COMPANY_WEBSITE_LIST_ID = 'company_website_list_id'
ANALYST_SUBMIT_STATUS = 'analyst_submit_status'
FILE_UPLOAD_ID = 'additional_attachment'
UPLOAD_COMPONENT = 'upload_component'


def get_section_title(section_title):
    return html.Div([section_title], className="sub_header")


def get_section_heading(section):
    return ' '.join(word for word in section.split('_')).title()


def get_component_html_div(label, component, required_asterisk=False):
    class_name = 'li'
    if required_asterisk == 'yes':
        class_name = class_name + ' required'
    return html.Div(style=dict(display='flex'),
                    children=[html.Label("{:<25}".format(label), className=class_name, style=dict(width='40%')),
                              component])


def get_input_component(id, input_type='text', list_id='', required='', style={'width': '15%'}, persistence=False):
    return dcc.Input(id=id,
                     type=input_type, placeholder='', list=list_id, required=required,
                     style=style, persistence_type='local', persistence=persistence
                     )


def get_submit_button(title='Submit', id='', container_id=ANALYST_SUBMIT_STATUS):
    return html.Div(className='fund_filter',
                    children=[html.Button(title, id=id, className='submit-button'),
                              html.Div(id=container_id, children=[])],
                    style={'width': '100%', 'display': 'flex'})


def get_dropdown_component(option, id, placeholder='', disabled=False, multi=False, display='inline-block'):
    if isinstance(option, str):
        option = option.split(',')

    return dcc.Dropdown(id=id,
                        options=option,
                        multi=multi,
                        placeholder=placeholder,
                        disabled=disabled,
                        style={'height': '30px', 'width': '50%', 'display': display}
                        )


def get_callback_trigger_component_id():
    ctx = dash.callback_context
    component_key = json.loads(ctx.triggered[0]["prop_id"].split(".")[0])['index']
    return component_key, len(ctx.inputs_list[0])


def get_company_name_dropdown(selected_value=None, component_id=''):
    options = []
    if selected_value and options:
        df = get_analyst_issuer_coverage(selected_value, search_key='id_bb_company')
        options = [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
                   for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]

    component = get_dropdown_component(options, component_id)

    return component


def get_component(id, input_type, input_properties={}):
    if input_properties is None:
        input_properties = {}
    else:
        input_properties = json.loads(input_properties)

    if input_type == 'radio':
        component = dcc.RadioItems(id=id,
                                   className='radio_type',
                                   **input_properties
                                   )
    elif input_type == 'text':
        component = dcc.Input(id=id,
                              style={'width': '15%'},
                              **input_properties)

    elif input_type == 'dropdown':
        component = dcc.Dropdown(id=id,
                                 style={'height': '30px', 'width': '50%', 'display': 'inline-block'},
                                 **input_properties
                                 )
    elif input_type == 'date':
        component = dcc.DatePickerSingle(id=id,
                                         month_format='MMM Do YYYY',
                                         placeholder='MMM Do YYYY',
                                         style={"margin-right": "15px"},
                                         **input_properties)
    elif input_type == 'text_area':
        component = dcc.Textarea(id=id,
                                 style={'width': '20%', 'height': '800%'},
                                 spellCheck=True,
                                 **input_properties
                                 )
    elif input_type == 'attachment':
        component = get_file_upload_component(id, True)
    else:
        component = []

    return component


def get_analyst_company_detail_data_list(list_id, request_type):
    option = get_analyst_company_attribs(request_type)
    return get_data_list_component(list_id, option)


def get_file_upload_component(id, multiple_upload=False):
    return dcc.Upload(
        id=id,
        children=html.Div(
            ["Click to select a file to upload. \n"
             "Multiple selection press Ctrl + Click. "]
        ),
        style={
            "width": "100%",
            "height": "60px",
            "lineHeight": "60px",
            "borderWidth": "1px",
            "borderStyle": "dashed",
            "borderRadius": "5px",
            "textAlign": "center",
            "margin": "10px",
        },
        multiple=multiple_upload,
    )
