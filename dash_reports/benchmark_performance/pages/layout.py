from dash import html, dcc

from common.utils import dtutil

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.risk_report.pages.utils import get_datepicker_control

BM_DATE_CONTROL_ID = 'bm-date-control'
BM_FROM_DATE_CONTROL_ID = 'bm-from-date-control'
BM_SUBMIT_ID = 'bm-submit'
BM_RESULT_ID = 'bm-results'
BM_METRIC_DD_CONTROL_ID = 'bm-metric-dd-control'

BM_METRIC_DD_CONTROL_DICT = {'excess_return': 'Excess Return', 'spread': 'Spread', 'duration': 'Duration'}


def get_bm_header():
    return html.Div([get_page_title("Benchmark Performance"),
                     html.Br(), ])


def create_layout():
    dt = dtutil.dateadd(dtutil.today(), '0b')
    fromdt = dtutil.dateadd(dtutil.boyear(dt), '-1b')
    from_date_control = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("From Date:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([get_datepicker_control(BM_FROM_DATE_CONTROL_ID, dt=fromdt,
                                                            disabled=False)],
                                    style=dict(width='50%', height='35px'))])

    to_date_control = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("To Date:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([get_datepicker_control(BM_DATE_CONTROL_ID, dt=dt,
                                                            disabled=False)],
                                    style=dict(width='50%', height='35px'))])

    metric_dd = html.Div(style=dict(display='flex'),
                         children=[html.Div([html.Label("{:<25}".format("Metric:"), className='subtitle')],
                                            style=dict(width='12.5%')),
                                   html.Div([dcc.Dropdown(id=BM_METRIC_DD_CONTROL_ID, options=BM_METRIC_DD_CONTROL_DICT,
                                                          value=list(BM_METRIC_DD_CONTROL_DICT.keys())[0], multi=False,
                                                          style=dict(width='300%'))])
                                   ])

    submit_btn = html.Div([html.Button('Go!', id=BM_SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=BM_RESULT_ID, children=[], className='page')

    return (html.Div([html.Div([from_date_control,
                                to_date_control,
                                metric_dd,
                                submit_btn,
                                ], className='fund_filter'),
                      ]), search_results)
