import logging
import dash_html_components as html

from dash_reports.liquidity.data.fund_commitment import fund_commitment_detail
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.liquidity.pages.utils import make_bold_rows

bold_cols_dict = {'Drawn Commitments': ['Total'], 'Undrawn Commitments': ['Total'], 'Total Fund Commitments': ['Total']}


def fund_commitment_layout():
    logging.info(f'Liquidity Report: Designing Fund Commitment Layout ')

    fund_commit_dict = fund_commitment_detail()
    val = []
    for key, value in fund_commit_dict.items():
        title = html.H6([key], className="subtitle padded")
        value = value.reset_index()
        conditional_style = make_bold_rows(value, key, 'APO Funds', bold_cols_dict)
        tbl = make_dash_table(value, txt_col_width='225px', num_col_width='80px', precision_2_format=True,
                              data_styling=conditional_style, fixed_columns=1)
        val += [title, html.Br(), tbl, html.Br()]

    return html.Div(
        [
            html.Div(
                [
                    html.Div(val, className="row "),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
