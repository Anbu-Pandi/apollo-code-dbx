import dash_core_components as dcc
import dash_html_components as html

from dash_reports.report_common.data.config import BETA_DIFF_RULES, BETA_CHART_COLOR
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.data.utils import parsefunds
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.beta_charts_data import generate_beta_charts
from dash_reports.risk_report.data.beta_diff_by_index import generate_beta_diff


def get_fundlist():
    fundlist = BETA_DIFF_RULES['funds']
    return fundlist


def get_charts(fundlist, trf_funds_beta_diff, oth_beta_dff):
    data_dict = {}
    for fund in BETA_DIFF_RULES['funds']:
        if fund == 'TRF':
            data_dict[fund] = {'key': fund, 'data': trf_funds_beta_diff}
        else:
            temp_df = oth_beta_dff.loc[oth_beta_dff['Fund Entity Code'] == fund]
            data_dict[fund] = {'key': fund, 'data': temp_df}

    beta_charts = generate_beta_charts(fundlist, color_code=BETA_CHART_COLOR)

    all_charts = {}
    for fund in fundlist:
        figure = html.Div([dcc.Graph(figure=beta_charts[fund]['chart'])], className="six columns",
                          style={'display': 'inline-block'})
        all_charts[fund] = figure
        del figure
    return data_dict, beta_charts, all_charts


def create_layout(app, fund):
    trf_funds_beta_diff, oth_beta_dff = datastore.read_or_create_cache_for_df(__file__ + '_0',
                                                                              lambda: generate_beta_diff())
    fundlist = datastore.read_or_create_cache_for_df(__file__ + '_1', lambda: get_fundlist())
    data_dict, beta_charts, all_charts = datastore.read_or_create_cache_for_df(__file__ + '_12',
                                                                               lambda: get_charts(fundlist,
                                                                                                  trf_funds_beta_diff,
                                                                                                  oth_beta_dff))

    def get_layout_for_fund(fund):
        return html.Div(
            [
                html.Div(
                    [
                        html.H6([data_dict[fund]['key']], className="subtitle padded"),
                        all_charts[fund],
                        make_dash_table(data_dict[fund]['data']),
                        html.P(),
                    ],
                    className="six columns",
                ),

            ],
            className="row "
        )

    divchildren = []
    for f in parsefunds(fund, fundlist):
        divchildren += [get_layout_for_fund(f), html.Br()]

    return html.Div(id='beta_by_diff',
                    children=
                    [
                        # page 5
                        html.Div(
                            divchildren,
                            className="sub_page",
                        )
                    ],
                    className="page",
                    )
