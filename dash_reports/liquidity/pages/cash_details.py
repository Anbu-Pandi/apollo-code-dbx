import logging
import dash_html_components as html

from dash_reports.liquidity.data.cash_summary import get_cash_summary
from dash_reports.report_common.utils.components import make_dash_table, make_tabulator_table


def make_bold_rows(df, title, filter_col, cols_to_bold_dict):
    if title in cols_to_bold_dict:
        row_identifier = cols_to_bold_dict[title]
    else:
        return []
    conditional_styling = []

    for key in row_identifier:
        idx = df.loc[df[filter_col] == key].index.values[0]
        conditional_styling.append({"if": {"row_index": idx}, "fontWeight": "bold", })

    return conditional_styling


bold_cols_dict = {'Cash Summary': ['Dry Powder'],
                  'Details': ['Total Capital Commitment', 'Total Fund Commitment', 'Adjusted Trade Date Cash',
                              'Trade Date Cash excluding FX']}


def cash_summary_layout():
    logging.info(f'Liquidity Report: Designing Cash Summary Layout ')

    cash_summary_dict = get_cash_summary()
    val = []
    for key, value in cash_summary_dict.items():
        title = html.H6([key], className="subtitle padded")
        conditional_style = make_bold_rows(value, key, 'Metric', bold_cols_dict)
        if key == 'Liquid Cash Details':
            tbl = make_tabulator_table(value, ["Security Sub Type", "Issuer Ult"])
        else:
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


if __name__ == '__main__':
    cash_summary_layout()
