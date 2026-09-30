import dash_html_components as html

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.rolling_pnl_data import generate_rolling_pnl_data


def create_layout(app, fund):
    wtd_df, mtd_df, ytd_df = datastore.read_or_create_cache_for_df(__file__, lambda: generate_rolling_pnl_data())

    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        temp_wtd = wtd_df
        temp_mtd = mtd_df
        temp_ytd = ytd_df
    else:
        temp_wtd = wtd_df.loc[wtd_df['Fund Entity Code'].isin(fund)]
        temp_mtd = mtd_df.loc[mtd_df['Fund Entity Code'].isin(fund)]
        temp_ytd = ytd_df.loc[ytd_df['Fund Entity Code'].isin(fund)]

    return html.Div(
        [
            # get_base_header_menu(app),
            # page 6
            html.Div(
                [
                    # Row
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Rolling PnL by WTD"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_wtd, sortby_cols=['Fund Entity Code', '1 Week Rolling PnL']),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ", style={'width': '33%', 'display': 'inline-block'}
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Rolling PnL by MTD"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_mtd, sortby_cols=['Fund Entity Code', 'MTD PnL']),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ", style={'width': '33%', 'display': 'inline-block'}
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Rolling PnL by YTD"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_ytd, sortby_cols=['Fund Entity Code', 'YTD PnL']),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ", style={'width': '33%', 'display': 'inline-block'}
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
