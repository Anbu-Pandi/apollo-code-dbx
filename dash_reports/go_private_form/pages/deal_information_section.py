import logging

from dash_reports.go_private_form.data.utils import get_form_questions
from dash_reports.go_private_form.pages.utils import *

SECTION = 'deal_information'


def deal_info_section():
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

        if input_type == 'text_area':
            component_id = {'type': 'analyst_text_type_id', 'index': qid}
        else:
            component_id = {'type': f'analyst_{input_type}_type_id', 'index': qid}

        component = get_component(component_id, input_type, input_properties)

        ques_div_kids.append(get_component_html_div(ques, component, required_asterisk))

        if qid in ['deal_transaction_info', 'other_circumstances']:
            ques_div_kids.append(html.Div(id={'type': UPDATE_ANALYST_DIV_CONTENT_ID, 'index': qid}, children=[]))

        ques_div_kids.append(html.Br())

    section_div_kids.append(html.Div(children=ques_div_kids, className='list'))
    return section_div_kids


def on_info_to_receive_selection(component_key, value):
    df = get_form_questions(component_key)
    child = []
    for _, row in df.iterrows():
        question_id = row['qid']
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']

        id = {'type': f'analyst_{input_type}_type_id', 'index': question_id}

        if (value == 'Other') & (component_key == 'deal_transaction_info'):
            component = get_component(id, input_type, input_properties)
            child = html.Div([html.Br(), get_component_html_div(ques, component, required_asterisk)])
        elif (value == 'Yes') & (component_key == 'other_circumstances'):
            component = get_component(id, input_type, input_properties)
            child = html.Div([html.Br(), get_component_html_div(ques, component, required_asterisk)])

    return child


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')

    deal_info_section()
