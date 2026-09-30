import dash_core_components as dcc
import dash_html_components as html

from dash_reports.report_common.data.config import *
from dash_reports.report_common.data.data_model import datastore
from dash_reports.risk_report.data.beta_charts_data import generate_beta_charts, \
    generate_flagship_beta_chart


def get_beta_by_component_chart(fundList, beta_charts):
    all_charts = []
    for fund in fundList:
        title = html.Div([html.H6([beta_charts[fund]['key']], className="subtitle padded")])
        figure = html.Div([dcc.Graph(figure=beta_charts[fund]['chart'])], className="six columns",
                          style={'display': 'inline-block'})
        all_charts.append(title)
        all_charts.append(figure)
        del title, figure

    return all_charts


def create_layout(app, fund):
    beta_charts = datastore.read_or_create_cache_for_df(__file__,
                                                        lambda: generate_beta_charts(FUND_CODE_LIST, BETA_CHART_COLOR))

    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        flagship_funds_beta_chart = generate_flagship_beta_chart(
            ['CSTRAT', 'ANCF', 'ATLAS', 'CENTRE', 'CF', 'MOULTRIE', 'SPNTACT', 'TRENHANCE', 'TRF'])
        beta_bycomponent_chart = get_beta_by_component_chart(['CSTRAT'], beta_charts)

    else:

        flagship_funds_beta_chart = generate_flagship_beta_chart(fund)
        beta_bycomponent_chart = get_beta_by_component_chart(fund, beta_charts)

    return html.Div(id='fund_beta_chart', children=
    [
        # page 5
        html.Div(
            [  # Row
                html.Div(
                    [html.Div([html.H6(["Flagship Funds Beta"], className="subtitle padded")]),
                     html.Div([dcc.Graph(figure=flagship_funds_beta_chart)])
                     ], className="six columns", style={'display': 'inline-block'}
                ),
                html.Div([html.H6(["Funds Beta by Components"], className="subtitle padded")]),
                html.Div(
                    beta_bycomponent_chart,
                    className="row ", style={'width': '100%', 'display': 'inline-block'}
                ),

            ],
            className="sub_page",
        ),
    ],
                    className="page",
                    )
