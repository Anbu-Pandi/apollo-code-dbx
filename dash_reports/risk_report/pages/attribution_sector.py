from collections import OrderedDict

import dash_html_components as html

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.data.sector_rating_pnl_data import generate_sector_attribution


def get_sector_df():
    df = generate_sector_attribution()
    return (df, 1)


def create_layout(app, fund):
    sector_df, _ = datastore.read_or_create_cache_for_df(__file__, lambda: get_sector_df())
    attributes_order = list(OrderedDict.fromkeys(sector_df[sector_df.columns[1]].values))
    if isinstance(fund, str):
        fund = [fund]
    else:
        fund = fund

    if 'ALL' in fund:
        temp_sector = sector_df
    else:
        temp_sector = sector_df.loc[sector_df['Fund Entity Code'].isin(fund)]

    return html.Div(
        [
            # get_base_header_menu(app),
            # page 3
            html.Div(
                [
                    # Row
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H6(
                                        ["Attribution by Sector"], className="subtitle padded"
                                    ),
                                    make_dash_table(temp_sector,
                                                    sortby_cols=sector_df.columns[:2],
                                                    ordered_row_values=[[], attributes_order]
                                                    ),
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
