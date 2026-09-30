from collections import OrderedDict

import dash_html_components as html

from dash_reports.report_common.data.config import FUND_DISPLAY_ORDER
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.pnl_attribution_data import generate_dollar_pnl_data


def create_layout(app, fund):
    dollar_pnl_df, return_pnl_df = datastore.read_or_create_cache_for_df(__file__, lambda: generate_dollar_pnl_data())
    fostrategybroadcategory_order = list(OrderedDict.fromkeys(dollar_pnl_df['FO Strategy Broad Category'].values))

    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        temp_dollar = dollar_pnl_df
        temp_return = return_pnl_df
    else:
        temp_dollar = dollar_pnl_df.loc[dollar_pnl_df['Fund Entity Code'].isin(fund)]
        temp_return = return_pnl_df.loc[return_pnl_df['Fund Entity Code'].isin(fund)]

    return html.Div(
        [
            # page 4
            html.Div(
                [
                    # Row
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["$ PnL Attribution"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_dollar,
                                                    sortby_cols=[('Fund Entity Code', ''),
                                                                 ('FO Strategy Broad Category', '')],
                                                    ordered_row_values=[FUND_DISPLAY_ORDER,
                                                                        fostrategybroadcategory_order],
                                                    ordered_column_values=[['HY', 'LL', 'EQ', 'CR & EQ', 'IR']],
                                                    sortby_column_levels=[1]),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ", style={'width': '50%', 'display': 'inline-block'}
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Return Attribution"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_return,
                                                    sortby_cols=[('Fund Entity Code', ''),
                                                                 ('FO Strategy Broad Category', '')],
                                                    ordered_row_values=[FUND_DISPLAY_ORDER,
                                                                        fostrategybroadcategory_order],
                                                    ordered_column_values=[['HY', 'LL', 'EQ', 'CR & EQ', 'IR']],
                                                    sortby_column_levels=[1]),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ", style={'width': '50%', 'display': 'inline-block'}
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
