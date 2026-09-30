from collections import OrderedDict

import dash_html_components as html

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.sector_rating_pnl_data import generate_rating_attribution


def create_layout(app, fund):
    rating_df, _ = datastore.read_or_create_cache_for_df(__file__, lambda: (generate_rating_attribution(), None))
    attributes_order = list(OrderedDict.fromkeys(rating_df[rating_df.columns[1]].values))
    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        temp_rating = rating_df
    else:
        temp_rating = rating_df.loc[rating_df.loc[:, 'Fund Entity Code'].isin(fund)]

    return html.Div(
        [
            # get_base_header_menu(app),
            # page 2
            html.Div(
                [
                    # Row
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Attribution by Rating"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_rating,
                                                    sortby_cols=rating_df.columns[:2],
                                                    ordered_row_values=[[], attributes_order]),
                                ],
                                className="six columns",
                            ),

                        ],
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
