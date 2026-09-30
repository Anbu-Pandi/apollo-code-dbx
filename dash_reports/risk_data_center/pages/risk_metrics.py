import dash_html_components as html
import dash_core_components as dcc

from dash_reports.risk_data_center.pages.utils import get_risk_metrics, \
    get_data_button, get_fund_dropdown, get_grpby_dropdown


def create_risk_metric_layout():
    return html.Div(
        children=[
            get_fund_dropdown(),
            html.Br(),
            get_grpby_dropdown(),
            html.Br(),
            get_risk_metrics(),
            html.Br(),
            get_data_button(),
            html.Br(),
            html.Div(id='metric-result-container'),
            dcc.Download(id="download-dataframe-csv")
        ]
    )


def create_other_layout(risk_metric):
    return html.Div(
        children=[
            get_data_button(),
            html.Br(),
            html.Div(id='metric-result-container'),
            dcc.Download(id="download-dataframe-csv"),
            get_grpby_dropdown('none'),
            html.Br(),
            get_risk_metrics(risk_metric, 'none'),
            get_fund_dropdown(data=None, display='none')
        ]
    )
