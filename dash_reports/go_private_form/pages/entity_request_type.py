import logging

from dash_reports.go_private_form.data.utils import get_form_questions
from dash_reports.go_private_form.pages.utils import *

questions_input_style = {
    'COMPANY_IDENTIFIER_INPUT_TYPE': 'radio',
    'REQUEST_TYPE_INPUT_TYPE': 'radio',
}

COMPANY_IDENTIFIER_OTHER_ENTITY_NAME_INPUT_ID = 'company_identifier_other_entity_name'
COMPANY_IDENTIFIER_OTHER_ENTITY_WEBSITE_INPUT_ID = 'company_identifier_other_entity_website'
COMPANY_IDENTIFIER_OTHER_ENTITY_ADDRESS_INPUT_ID = 'company_identifier_other_entity_address'
COMPANY_IDENTIFIER_OTHER_ENTITY_REGION_INPUT_ID = 'company_identifier_other_entity_region'

COMPANY_IDENTIFIER_SECURITY_ID = 'company_identifier_security_id'

ENTITY_IDENTIFIER_SUB_SECTION_ID = 'company_identifier'

DATA_LIST_CONFIG = {COMPANY_IDENTIFIER_OTHER_ENTITY_NAME_INPUT_ID: {'request_type': 'company_name',
                                                                    'id': ANALYST_COMPANY_NAME_LIST_ID},
                    COMPANY_IDENTIFIER_OTHER_ENTITY_WEBSITE_INPUT_ID: {'request_type': 'company_website',
                                                                       'id': ANALYST_COMPANY_WEBSITE_LIST_ID},
                    COMPANY_IDENTIFIER_OTHER_ENTITY_ADDRESS_INPUT_ID: {'request_type': 'company_address',
                                                                       'id': ANALYST_COMPANY_WEBSITE_LIST_ID},
                    COMPANY_IDENTIFIER_OTHER_ENTITY_REGION_INPUT_ID: {'request_type': 'company_region',
                                                                      'id': ANALYST_COMPANY_WEBSITE_LIST_ID}

                    }


def entity_name_section():
    SECTION = 'entity_identifier'
    section_title = get_section_heading(SECTION)
    section_div_kids = [get_section_title(section_title), html.Br()]
    ques_div_kids = []
    questions_df = get_form_questions(SECTION)

    for _, row in questions_df.iterrows():
        qid = row['qid']
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']
        component_id = {'type': f'analyst_{input_type}_type_id', 'index': qid}

        radio_component = get_component(component_id, input_type, input_properties)

        ques_div_kids.append(
            html.Div(style=dict(display='inline'),
                     children=[
                         get_component_html_div(ques, radio_component, required_asterisk),
                         html.Div([], id={'type': UPDATE_ANALYST_DIV_CONTENT_ID, 'index': qid})
                     ])
        )
        ques_div_kids.append(html.Br())

    section_div_kids.append(html.Div(children=ques_div_kids, className='list'))
    return section_div_kids


def on_entity_identifier_selection(component_key, option):
    questions_df = get_form_questions(component_key)
    child = []

    for _, row in questions_df.iterrows():
        qid = row['qid']
        ques = row['description']
        required_asterisk = row['required']

        if (option == 'By Company Name or Identifier (ISIN / CUSIP / Loanx ID)') & (
                qid in [COMPANY_IDENTIFIER_SECURITY_ID]):

            dropdown_component = get_company_name_dropdown(selected_value=None,
                                                           component_id={'type': ANALYST_DROPDOWN_TYPE_ID,
                                                                         'index': qid})
            dropdown_component_div = get_component_html_div(ques, dropdown_component, required_asterisk)
            child.append(html.Div([html.Br(), dropdown_component_div]))

        elif (option == 'Enter Manually') & (qid in [COMPANY_IDENTIFIER_OTHER_ENTITY_NAME_INPUT_ID,
                                                     COMPANY_IDENTIFIER_OTHER_ENTITY_WEBSITE_INPUT_ID,
                                                     COMPANY_IDENTIFIER_OTHER_ENTITY_ADDRESS_INPUT_ID,
                                                     COMPANY_IDENTIFIER_OTHER_ENTITY_REGION_INPUT_ID]):

            list_id = DATA_LIST_CONFIG[qid]['id']
            request_type = DATA_LIST_CONFIG[qid]['request_type']
            component_id = {'type': f'analyst_text_type_id', 'index': qid}
            component = get_component_html_div(ques, get_input_component(component_id, list_id=list_id),
                                               required_asterisk)
            data_list_component = get_analyst_company_detail_data_list(list_id, request_type)

            child.append(html.Div([html.Br(), component, data_list_component]))

    return child


def request_type_section():
    SECTION = 'request_type'
    section_title = get_section_heading(SECTION)
    section_div_kids = [get_section_title(section_title), html.Br()]
    ques_div_kids = []
    questions_df = get_form_questions(SECTION)

    for _, row in questions_df.iterrows():
        qid = row['qid']
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']
        component_id = {'type': f'analyst_{input_type}_type_id', 'index': qid}

        component = get_component(component_id, input_type, input_properties)

        ques_div_kids.append(get_component_html_div(ques, component, required_asterisk))
        ques_div_kids.append(html.Div(id={'type': UPDATE_ANALYST_DIV_CONTENT_ID, 'index': qid}, children=[]))

    section_div_kids.append(html.Div(children=ques_div_kids, className='list'))
    return section_div_kids


def on_request_type_selection(component_key, value):
    df = get_form_questions(f'{component_key}_inline')
    df = df[df['qid'] == f'{"_".join(value.lower().split())}_detail']
    child = []
    for _, row in df.iterrows():
        question_id = row['qid']
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']
        component_id = {'type': f'analyst_{input_type}_type_id', 'index': question_id}

        component = get_component(component_id, input_type, input_properties)
        child = html.Div([html.Br(), get_component_html_div(ques, component, required_asterisk)])

    return child


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')

    on_entity_identifier_selection('Security Identifier (ISIN / CUSIP / Loanx ID)')
