import pandas as pd

import dash_reports.compliance.data.compliance as compliance_data
import dash_reports.go_private_form.data.utils as data_utils
import data_access.dbtables.analyst_questionnaire.analyst_response as analyst_queries
from dash_reports.go_private_form.pages.utils import *
from dash_reports.compliance.pages.utils import *
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.issuers_info.data.capital_structure import get_company_tree_view
from dash_reports.issuers_info.pages.capital_structure import create_corp_tree_graph

SECTION = 'compliance_info'
RESTRICTED_NODE = 'node'


def _status_check_list():
    component = dcc.Checklist(id=COMPLIANCE_STATUS_CHECK_LIST,
                              options=[{'label': 'Pending', 'value': 'pending'},
                                       {'label': 'Approved', 'value': 'approved'},
                                       {'label': 'Rejected', 'value': 'rejected'}],
                              value=['pending'],
                              inline=True
                              )

    return html.Div([get_section_title('Include'),
                     component
                     ],
                    style={'display': 'flex'}
                    )


def _compliance_dropdown():
    options = data_utils.get_requested_company_details_dict()
    component = get_dropdown_component(option=options, id={'type': COMPLIANCE_DROPDOWN_TYPE_ID,
                                                           'index': 'compliance_selection'})
    return html.Div([get_section_title('Select Company'),
                     component
                     ],
                    style={'display': 'flex'}
                    )


def _compliance_view_request_button():
    return html.Div(get_submit_button(title='View Request', id='compliance_view_request'))


def compliance_section_header():
    div_kids = [html.Br(),
                _compliance_dropdown(),
                html.Br(),
                _status_check_list(),
                html.Br(),
                html.Br(),
                _compliance_view_request_button(),
                html.Br()]
    dynamic_kids = [html.Div(id=COMPLIANCE_OUTPUT_CONTAINER_ID, className='page')]
    return div_kids, dynamic_kids


def _approve_reject_button():
    return [html.Div([
        html.Div(
            [get_submit_button(title='Approve', id={'type': COMPLIANCE_SUBMIT, 'index': COMPLIANCE_APPROVE_BUTTON_ID}),
             get_submit_button(title='Reject', id={'type': COMPLIANCE_SUBMIT, 'index': COMPLIANCE_REJECT_BUTTON_ID})],
            style={'display': 'flex'}),
        html.Div(id=COMPLIANCE_ON_APPROVE_REJECT_OUTPUT, children=[]),
        html.Br()
    ])]


def _request_id_dropdown(id, checked_status):
    request_id = analyst_queries.get_request_tracker_id_by_company(id, checked_status, 'go_private')
    request_id.sort(reverse=True)
    heading = get_section_title('Select Request ID')

    dropdown_options = [{'label': 'ALL', 'value': request_id}]
    dropdown_options.extend([{'label': x, 'value': x} for x in request_id])

    component = get_dropdown_component(dropdown_options,
                                       {'type': COMPLIANCE_DROPDOWN_TYPE_ID, 'index': COMPLIANCE_REQUEST_ID_SELECTION},
                                       multi=True, display='inline-block')

    return [html.Div([heading, component], style={'display': 'flex'}), html.Br()]


def _create_corp_tree_chart(selected_value):
    corp_tree_df, graph_tree_df, debt_df = get_company_tree_view(selected_value)
    return [html.Br(),
            get_section_title('Corporate Tree'),
            html.Div(children=[create_corp_tree_graph(graph_tree_df)]),
            html.Br()]


def create_compliance_layout(company_id, checked_status):
    df = compliance_data.get_request_tracker_id_by_company(company_id, checked_status)

    if not df.empty:
        kids = [html.Div([html.Br(),
                          get_section_title('Analyst Requests'),
                          html.Br(),
                          html.Div(make_dash_table(df, id='go_pvt_request_table',
                                                   cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                   txt_col_width='250px',
                                                   fixed_columns=1
                                                   ), className='list'),
                          html.Br(),
                          html.Br()
                          ]
                         )
                ]
        kids.extend(_request_id_dropdown(company_id, checked_status))
        kids.extend(_approve_reject_button())
        # kids.extend(_create_corp_tree_chart(company_id))
        return [kids]
    else:
        return [dcc.Markdown([f'No {checked_status} request found for selected company'], style=MARKDOWN_STYLE)]


def get_restricted_node_dropdown(selected_value=None, component_id=''):
    try:
        corp_tree_df, graph_tree_df, debt_df = get_company_tree_view(selected_value, only_security_issuers=False)
        options = [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
                   for _, x in graph_tree_df.iterrows() if pd.notnull(x['id_bb_company'])]
    except:
        df = analyst_queries.get_analyst_company_detail()
        df = df[df['company_id'] == int(selected_value)]
        if ~df.empty:
            options = [{'label': x['company_name'], 'value': int(x['company_id'])} for _, x in df.iterrows()]
        else:
            options = []

    component = get_dropdown_component(options, {'type': COMPLIANCE_DROPDOWN_TYPE_ID, 'index': component_id})

    return component


def on_approve_reject_layout(key, selected_company_id):
    section_div_kids = [dcc.Markdown([f'Please fill below details and click submit'], style=MARKDOWN_STYLE), html.Br()]
    kids = []
    button_id = 'submit'
    if key == COMPLIANCE_APPROVE_BUTTON_ID:
        kids = compliance_info_section(selected_company_id)
        kids.append(html.Div(id=COMPLIANCE_RESTRICTION_DETAIL_CONTAINER, children=[]))
        kids.append(html.Div([html.Button(html.Span('+',
                                                    style={'color': 'red', 'fontSize': '35px', 'fontWeight': 'bold',
                                                           'backgroundColor': '#E9F4E9'}),
                                          id=COMPLIANCE_ADD_RESTRICTION_BUTTON_ID),
                              ],
                             className='fund_filter'
                             ))
        button_id = COMPLIANCE_SUBMIT_APPROVAL
    elif key == COMPLIANCE_REJECT_BUTTON_ID:
        kids = reject_section()
        button_id = COMPLIANCE_SUBMIT_REJECT

    section_div_kids.extend(kids)
    section_div_kids.extend([html.Br(), html.Br()])
    section_div_kids.append(get_submit_button('Submit', {'type': COMPLIANCE_SUBMIT_TYPE,
                                                         'index': button_id},
                                              container_id=COMPLIANCE_SUBMIT_STATUS))
    return section_div_kids


def reject_section():
    questions_df = data_utils.get_form_questions('compliance_reject')
    section_div_kids = []
    ques_div_kids = []

    for _, row in questions_df.iterrows():
        question_id = row['qid']
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']

        if input_type == 'text_area':
            component_id = {'type': 'compliance_text_type_id', 'index': question_id}
        else:
            component_id = {'type': f'compliance_{input_type}_type_id', 'index': question_id}

        component = get_component(component_id, input_type, input_properties)

        ques_div_kids.append(get_component_html_div(ques, component, required_asterisk))
        ques_div_kids.append(html.Br())

    section_div_kids.append(html.Div(children=ques_div_kids, className='list'))
    return section_div_kids


def remove_restriction_button(id):
    return html.Button(html.Span('X', style={'color': 'red', 'fontSize': '15px', 'fontWeight': 'bold',
                                             'backgroundColor': '#E9F4E9'}),
                       id={"type": COMPLIANCE_REMOVE_RESTRICTION_TYPE, "index": id},
                       style={'marginLeft': '80%'}
                       )


def compliance_info_section(company_id=None, counter=0):
    counter += 1
    section_title = [get_section_heading(f'Restriction Info - {counter}')]

    if counter != 1:
        section_title.append(remove_restriction_button(counter))

    section_div_kids = [html.Div(section_title, className="sub_header"), html.Br()]
    ques_div_kids = []

    questions_df = data_utils.get_form_questions(SECTION)

    for _, row in questions_df.iterrows():
        question_id = f"{row['qid']}:{counter}"
        ques = row['description']
        input_type = row['input_type']
        input_properties = row['input_properties']
        required_asterisk = row['required']

        if input_type == 'text_area':
            component_id = {'type': 'compliance_text_type_id', 'index': question_id}
        else:
            component_id = {'type': f'compliance_{input_type}_type_id', 'index': question_id}

        if 'node' in question_id:
            component = get_restricted_node_dropdown(company_id, component_id=question_id)
        elif ('restriction_end_dt' in question_id) or ('alert_date' in question_id):
            component = html.Div([get_component(component_id, input_type, input_properties),
                                  dcc.Checklist(id={'type': COMPLIANCE_DATE_ENABLE_CHECKLIST_ID, 'index': question_id},
                                                options=[{'label': 'Enable', 'value': 'enable'}])
                                  ],
                                 style={'display': 'flex'}
                                 )
        else:
            component = get_component(component_id, input_type, input_properties)

        ques_div_kids.append(get_component_html_div(ques, component, required_asterisk))
        ques_div_kids.append(html.Br())

    section_div_kids.append(html.Div(children=ques_div_kids, className='list'))
    return [html.Div(section_div_kids, id={'type': 'restriction_info_div', 'index': counter})]


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')

    compliance_info_section()
