import dash_core_components as dcc
import dash_html_components as html

import dash_reports.report_common.data.utils as rdu
from dash_reports.report_common.data.config import FUNDS_FOR_SANKEY
from dash_reports.report_common.data.data_model import datastore
from dash_reports.risk_report.data.sankey_charts import generate_issuer_pnl_sankey, \
    generate_tot_alpha_sankey, generate_tot_pnl_sankey


def create_layout(app, fund):
    issuer_pnl_chart = datastore.read_or_create_cache_for_df(__file__ + '_1',
                                                             lambda: generate_issuer_pnl_sankey(FUNDS_FOR_SANKEY))
    alpha_pnl_chart = datastore.read_or_create_cache_for_df(__file__ + '_2',
                                                            lambda: generate_tot_alpha_sankey(FUNDS_FOR_SANKEY))
    total_pnl_chart = datastore.read_or_create_cache_for_df(__file__ + '_3',
                                                            lambda: generate_tot_pnl_sankey(FUNDS_FOR_SANKEY))

    def generate_fund_charts(fundname):
        return html.Div(
            [
                html.Div([html.H6([fundname], className="subtitle")]),
                html.Div(
                    [dcc.Graph(figure=issuer_pnl_chart[fundname])],
                    className="", style={'display': 'inline-block'}
                ),
                html.Div(
                    [dcc.Graph(figure=total_pnl_chart[fundname])],
                    className="", style={'display': 'inline-block'}
                ),
                html.Div(
                    [dcc.Graph(figure=alpha_pnl_chart[fundname])],
                    className="", style={'display': 'inline-block'}
                ),
            ],
            style={'width': '100%', 'display': 'inline-block'},
            className=''
        )

    divchildren = []
    for f in rdu.parsefunds(fund, FUNDS_FOR_SANKEY):
        divchildren += [generate_fund_charts(f)]

    return html.Div(id='sankey_charts',
                    children=
                    [
                        # page 5
                        html.Div(divchildren),
                    ],
                    className="sankey_page",
                    )
