import dash_html_components as html

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils.dates import reportdates
from dash_reports.risk_report.data.index_attribution import generate_index_beta_corr, \
    generate_index_alpha_excess, generate_snp_attribution, generate_bnchmrk_attribution

"""Attribution dates"""
(start_date, end_date, _, _, _) = \
    reportdates.get_report_folder_dates(datastore.reportdt)
rating_order = ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC OR LOWER', 'D']


def get_hyindex_data():
    bond_attrib_df, top_bottom_contribs, hoao_mtd_return = generate_bnchmrk_attribution('H0A0', start_date, end_date)
    alpha_excess_ret_df, CDX_HY_df = generate_index_alpha_excess('H0A0', start_date, end_date)
    beta_corr_df = generate_index_beta_corr('H0A0', start_date, end_date)
    return bond_attrib_df, top_bottom_contribs, hoao_mtd_return, alpha_excess_ret_df, CDX_HY_df, beta_corr_df


def get_igindex_data():
    coao_bond_attrib_df, coao_top_bottom_contribs, coao_mtd_return = \
        generate_bnchmrk_attribution('C0A0', start_date, end_date)
    return coao_bond_attrib_df, coao_top_bottom_contribs, coao_mtd_return


def get_loan_index_data():
    loan_attrib_df, snp_mtd_return = generate_snp_attribution(start_date, end_date)
    return loan_attrib_df, snp_mtd_return


def create_layout(app, fund):
    bond_attrib_df, top_bottom_contribs, hoao_mtd_return, alpha_excess_ret_df, CDX_HY_df, beta_corr_df = \
        datastore.read_or_create_cache_for_df(__file__ + '_0', lambda: get_hyindex_data())
    coao_bond_attrib_df, coao_top_bottom_contribs, coao_mtd_return = \
        datastore.read_or_create_cache_for_df(__file__ + '_1', lambda: get_igindex_data())
    loan_attrib_df, snp_mtd_return = \
        datastore.read_or_create_cache_for_df(__file__ + '_2', lambda: get_loan_index_data())

    return html.Div(id='index_attribution', children=
    [
        # page 5
        html.Div(
            [  # Row
                html.Div(
                    [
                        html.Div(
                            [
                                html.H6(["S&P LL Index - MTD Return: {}%".format(snp_mtd_return)],
                                        className="subtitle padded"),
                                # html.Br([]),
                                # html.H3(["MTD Return: {}%".format(snp_mtd_return)]),
                                # html.Br([]),
                                make_dash_table(loan_attrib_df,
                                                ordered_column_values=[rating_order],
                                                sortby_column_levels=['RATING'],
                                                ),
                            ],
                            className="six columns"
                        ),

                    ],
                    className="row ",
                ),
                html.P(),
                html.Div(
                    [
                        # Row
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(["HY CDX Returns"], className="subtitle padded"),
                                        make_dash_table(CDX_HY_df)
                                    ],
                                    className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '20%'}
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(["H0A0 Attribution - MTD Return: {}%".format(hoao_mtd_return)],
                                                className="subtitle padded"),
                                        make_dash_table(bond_attrib_df,
                                                        ordered_column_values=[rating_order],
                                                        sortby_column_levels=['Rating_L1'], ),
                                    ],
                                    # className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '80%'}
                        ),
                    ],
                    className="row ", style={'display': 'flex'}
                ),
                html.P(),
                # Row 2
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(
                                            ["H0A0 Top Bottom Contributors"], className="subtitle padded"
                                        ),
                                        make_dash_table(top_bottom_contribs),
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
                                            ["H0A0 Correlation"], className="subtitle padded"
                                        ),
                                        make_dash_table(beta_corr_df,
                                                        ordered_column_values=[rating_order],
                                                        sortby_column_levels=['Rating_L1'],
                                                        ),
                                    ],
                                    className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '50%', 'display': 'inline-block'}
                        ),
                    ],
                    className="row "
                ),
                html.P(),
                html.Div(
                    [
                        html.Div(
                            [
                                html.H6(
                                    ["H0A0 Excess Returns"], className="subtitle padded"
                                ),
                                make_dash_table(alpha_excess_ret_df,
                                                ordered_column_values=[rating_order],
                                                sortby_column_levels=['Rating_L1'],
                                                )
                            ],
                            className="six columns",
                        ),

                    ],
                    className="row ", style={'width': '50%', 'display': 'inline-block'}
                ),
                # # Row 3
                html.P(),
                html.Div(
                    [
                        # Row
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(["HY CDX Returns"], className="subtitle padded"),
                                        make_dash_table(CDX_HY_df)
                                    ],
                                    className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '20%'}
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(["C0A0 Attribution - MTD Return: {}%".format(coao_mtd_return)],
                                                className="subtitle padded"),
                                        make_dash_table(coao_bond_attrib_df,
                                                        ordered_column_values=[rating_order],
                                                        sortby_column_levels=['Rating_L1'],
                                                        )
                                    ],
                                    # className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '80%'}
                        ),
                    ],
                    className="row ", style={'display': 'flex'}
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H6(
                                            ["C0A0 Top Bottom Contributors"], className="subtitle padded"
                                        ),
                                        make_dash_table(coao_top_bottom_contribs),
                                    ],
                                    className="six columns",
                                ),

                            ],
                            className="row ", style={'width': '50%', 'display': 'inline-block'}
                        ),
                    ],
                    className="row "
                ),
            ],
            className="sub_page",
        ),
    ],
                    className="page",
                    )
