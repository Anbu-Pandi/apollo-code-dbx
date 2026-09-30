import logging
from dash_reports.go_private_form.pages.utils import *
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.compliance.data.status import get_status_df, get_upcoming_alerts_df


def create_stats_layout():
    logging.info('Go-Pvt-Form: Creating Layout for Status Page ')
    status_df = get_status_df('pending')
    upcoming_alert_df = get_upcoming_alerts_df()

    layout = [html.Br(),
              html.Div([get_section_title('Pending Requests'),
                        html.Br(),
                        html.Div(make_dash_table(status_df,
                                                 cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                 txt_col_width='250px',
                                                 num_col_width='125px'
                                                 ),
                                 className='list'),
                        ]
                       ),
              html.Br(),
              html.Div([get_section_title('Alerts in next 15 days'),
                        html.Br(),
                        html.Div(make_dash_table(upcoming_alert_df,
                                                 cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                 txt_col_width='200px',
                                                 num_col_width='125px'
                                                 ),
                                 className='list'),
                        ],
                       ),
              ]
    return layout
