import dash_html_components as html

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.pnl_attribution_data import generate_top_beta_contrib


def create_layout(app, fund):
    top_beta_contib_df, bottom_beta_contrib_df = datastore.read_or_create_cache_for_df(__file__,
                                                                                       lambda: generate_top_beta_contrib())
    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        temp_top = top_beta_contib_df
        temp_down = bottom_beta_contrib_df
    else:
        temp_top = top_beta_contib_df.loc[top_beta_contib_df['Fund Entity Code'].isin(fund)]
        temp_down = bottom_beta_contrib_df.loc[top_beta_contib_df['Fund Entity Code'].isin(fund)]

    return html.Div(id='beta_contributors',
                    children=[html.Div(
                        [html.Div([html.Div([
                            html.Div(
                                [html.H6(["Top Beta Contributors"], className="subtitle padded"),
                                 make_dash_table(temp_top,
                                                 sortby_cols=['Fund Entity Code', 'Beta Contribution'], ), ],
                                className="six columns", ), ],
                            className="row ", style={'width': '50%', 'display': 'inline-block'}),
                            html.Div(
                                [html.Div([
                                    html.H6(["Bottom Beta Contributors"], className="subtitle padded"),
                                    make_dash_table(temp_down,
                                                    sortby_cols=['Fund Entity Code', 'Beta Contribution'], ), ],
                                    className="six columns", ), ],
                                className="row ", style={'width': '50%', 'display': 'inline-block'}), ],
                            className="row "), ],
                        className="sub_page", ), ],
                    className="page",
                    )
