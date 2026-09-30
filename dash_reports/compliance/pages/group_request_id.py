import logging

import dash_reports.compliance.data.group_request as data

from dash_reports.go_private_form.pages.utils import *
from dash_reports.compliance.pages.utils import *
from data_access.dbtables.analyst_questionnaire.analyst_response import get_request_tracker_data
from dash_reports.report_common.utils.components import make_dash_table

GROUP_REQUEST_CHECKLIST_ID = 'group_request_checklist'
GROUP_REQUEST_SUBMIT_BUTTON_ID = 'group_request_submit'
GROUP_REQUEST_OUTPUT_DIV_ID = 'group_request_output'
GROUP_REQUEST_EVENT_ID = 'group_request_event_id'
GROUP_REQUEST_INSERT_BUTTON = 'group_request_insert'
GROUP_REQUEST_INSERT_STATUS = 'group_request_insert_status'
GROUP_REQUEST_ID_SELECTION = 'group_request_request_ids_selection'
GROUP_REQUEST_COMPANY_SELECTION = 'group_request_company_selection'
GROUP_REQUEST_ANALYST_SELECTION = 'group_request_analyst_selection'


def _get_label(label):
    return html.Label(label, className='subtitle', style={"margin-right": "5%"})


def _status_check_list():
    component = dcc.Checklist(id=GROUP_REQUEST_CHECKLIST_ID,
                              options=[
                                  {'label': 'Approved', 'value': 'approved'},
                                  {'label': 'Rejected', 'value': 'rejected'}],
                              value=['approved'],
                              inline=True
                              )
    return html.Div([get_section_title('Status'), component], style={'display': 'flex', 'width': '40%'})


def _get_dropdown_options(key):
    df = get_request_tracker_data('go_private')
    if key == 'ult_company':
        df = df[['ult_company_id', 'ult_company_name']]
        df = df.drop_duplicates()
        options = [{'label': x['ult_company_name'], 'value': x['ult_company_id']} for _, x in df.iterrows()]
    else:
        df = df[[key]]
        df = df.drop_duplicates()
        options = [{'label': x[key], 'value': x[key]} for _, x in df.iterrows()]
    return options


def _page_header():
    kids = [html.Br(),
            get_section_title('Get New Private Request By'),
            html.Div([html.Br(),
                      html.Div(
                          [get_section_title('Company'),
                           get_dropdown_component(_get_dropdown_options('ult_company'),
                                                  {'type': COMPLIANCE_DROPDOWN_TYPE_ID,
                                                   'index': GROUP_REQUEST_COMPANY_SELECTION},
                                                  multi=True)],
                          style={'display': 'flex'}),
                      html.Br(),
                      html.Div(
                          [get_section_title('Analyst'),
                           get_dropdown_component(_get_dropdown_options('requested_by'),
                                                  {'type': COMPLIANCE_DROPDOWN_TYPE_ID,
                                                   'index': GROUP_REQUEST_ANALYST_SELECTION}, multi=True)],
                          style={'display': 'flex'}),
                      html.Br(),
                      _status_check_list(),
                      html.Br(),
                      html.Br(),
                      html.Button('Submit', id=GROUP_REQUEST_SUBMIT_BUTTON_ID, className='submit-button')
                      ], className='list'
                     )
            ]

    dynamic_kids = [html.Div([html.Br(),
                              html.Div([], id=GROUP_REQUEST_OUTPUT_DIV_ID)],
                             className='page')
                    ]
    return kids, dynamic_kids


def create_group_request_layout():
    static_kids, dynamic_kids = _page_header()
    return static_kids, dynamic_kids


def on_group_restrictions_submit(selected_company, selected_analyst, selected_status):
    logging.info(f'Go Pvt Form: Group Request ID: Request By Company: {selected_company}, '
                 f'Analyst: {selected_analyst}, Status: {selected_status}')

    request_ids = data.get_request_id(selected_company, selected_analyst, selected_status)
    request_ids.sort()

    restrictions_df = data.get_compliance_restrictions_by_request(request_ids)
    request_event_id_map_df = data.get_request_event_matrix(request_ids)

    historical_request_event_id_map_df = data.get_event_id_map_tbl(request_ids)

    request_id_dropdown_options = [{'label': 'ALL', 'value': request_ids}]
    request_id_dropdown_options.extend([{'label': x, 'value': x} for x in request_ids])

    layout = [html.Div([html.Br(),
                        get_section_title('Restrictions'),
                        html.Br(),
                        html.Br(),
                        make_dash_table(restrictions_df,
                                        cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                        txt_col_width='250px', fixed_columns=1
                                        ),
                        ], className='list'
                       ),

              html.Div([html.Br(),
                        html.Br(),
                        get_section_title('Event ID Mapping - View by Request ID'),
                        html.Br(),
                        html.Br(),
                        make_dash_table(request_event_id_map_df,
                                        cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                        num_col_width='125px', fixed_columns=1
                                        ),
                        ], className='list'
                       ),

              html.Br(),
              html.Br(),

              html.Div([html.Br(),
                        html.Br(),
                        get_section_title('Event ID Mapping - View by Event ID'),
                        html.Br(),
                        html.Br(),
                        make_dash_table(historical_request_event_id_map_df,
                                        cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                        num_col_width='125px', fixed_columns=1
                                        ),
                        ], className='list'
                       ),

              html.Br(),
              html.Br(),
              html.Div([get_section_title('Select Request IDs to Group'),
                        get_dropdown_component(request_id_dropdown_options, {'type': COMPLIANCE_DROPDOWN_TYPE_ID,
                                                                             'index': GROUP_REQUEST_ID_SELECTION},
                                               multi=True)], className='list',
                       style={'display': 'flex'}
                       ),
              html.Br(),
              html.Br(),
              html.Div([html.Button('Assign Event ID', id=GROUP_REQUEST_INSERT_BUTTON, className='submit-button'),
                        html.Div([], id=GROUP_REQUEST_INSERT_STATUS)], className='list',
                       style={'display': 'flex'})
              ]

    return layout
