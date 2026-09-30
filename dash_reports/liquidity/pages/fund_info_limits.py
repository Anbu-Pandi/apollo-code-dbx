import logging
import dash_html_components as html
import dash_core_components as dcc

from dash_reports.liquidity.data.fund_info import get_fund_info_tbl
from dash_reports.report_common.utils.components import make_dash_table


def fund_info_layout():
    logging.info(f'Liquidity Report: Designing Fund Information Layout ')

    fund_details_dict = get_fund_info_tbl()
    val = []
    for key, value in fund_details_dict.items():
        title = html.H6([key], className="subtitle padded")
        tbl = make_dash_table(value, fixed_columns=1)
        val += [title, html.Br(), tbl, html.Br()]

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        val,
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
