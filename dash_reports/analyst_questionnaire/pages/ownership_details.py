import logging
import dash_html_components as html
import dash_core_components as dcc

from dash_reports.analyst_questionnaire.pages.utils import get_company_name_dropdown, \
    snp_financials_page, get_questions_html, get_data_button


def create_ownership_details_layout():
    return html.Div(
        children=[
            dcc.Store(id="diff-store", storage_type='memory'),
            get_company_name_dropdown(),
            html.Br(),
            get_data_button(),
            html.Div(id='questionnaire-result-container'),
            html.Div(id='push-to-db-result-container2')

        ]
    )


def result_ownership_details_layout(id_bb_company, from_date, to_date, period_type, path):
    logging.info(f'Analyst Questionnaire: Designing Ownership Details Page Layout ')

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [html.Div([snp_financials_page(id_bb_company, path)], className="subtitle padded"),
                         html.Br(),
                         get_questions_html('ownership', id_bb_company),
                         # val
                         ],
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
