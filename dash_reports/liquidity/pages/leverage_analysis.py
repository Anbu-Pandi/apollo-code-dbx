import logging
import dash_html_components as html
import dash_core_components as dcc

from dash_reports.liquidity.data.leverage import leverage_analysis
from dash_reports.report_common.utils.components import make_dash_table


def leverage_analysis_layout():
    logging.info(f'Liquidity Report: Designing Leverage Analysis Layout ')

    leverage_df = leverage_analysis()
    leverage_df.columns = leverage_df.columns.droplevel('Fund Entity Code')
    title = html.H6(['Leverage Facility'], className="subtitle padded")
    tbl = make_dash_table(leverage_df, precision_2_format=True, fixed_columns=1)
    val = [title, html.Br(), tbl, html.Br()]

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


if __name__ == '__main__':
    leverage_analysis_layout()
