from dash_reports.risk_data_center.pages.utils import *


def create_external_data_layout(risk_metric):
    val = [html.Br(),
           get_fund_dropdown(risk_metric),
           ]

    layout = html.Div(val +
                      [html.Br(),
                       get_data_button(),
                       html.Br(),
                       html.Div(id='metric-result-container'),
                       html.Br(),
                       dcc.Download(id="download-dataframe-csv"),
                       get_grpby_dropdown('none'),
                       html.Br(),
                       get_risk_metrics(risk_metric, 'none'),

                       ])

    return layout
