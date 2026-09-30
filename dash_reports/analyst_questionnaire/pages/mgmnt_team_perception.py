import logging
import dash_html_components as html
import dash_core_components as dcc

from dash_reports.analyst_questionnaire.pages.utils import get_company_name_dropdown, \
    snp_financials_page, get_data_button, unpack_result_html, get_questions_html
from dash_reports.analyst_questionnaire.data.management_team import get_management_perspective_analysis


def create_management_perspective_layout():
    return html.Div(
        children=[
            dcc.Store(id="diff-store", storage_type='memory'),
            get_company_name_dropdown(),
            get_data_button(),
            html.Div(id='questionnaire-result-container'),
            html.Div(id='push-to-db-result-container2')

        ]
    )


def result_management_perspective_layout(id_bb_company, from_date, to_date, period_type, path, rating_type):
    logging.info(f'Analyst Questionnaire: Designing Management Perspective Page Layout ')

    result_dict, company_name, industry = get_management_perspective_analysis(id_bb_company, from_date, to_date,
                                                                              period_type,
                                                                              rating_type)
    heading = f'{company_name}  | {period_type} Financials  | {from_date} to {to_date} | Industry: {industry}'
    val = unpack_result_html(result_dict, heading)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [html.Div([snp_financials_page(id_bb_company, path)], className="subtitle padded"),
                         html.Br(),
                         get_questions_html('mgmt', id_bb_company),
                         val,
                         ],
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )


if __name__ == '__main__':
    from common.utils.dtutil import today, boyear, dateadd
    from apo_config.config import config

    config.init('dev')

    company_name = 'Foot Locker, Inc.'
    to_dt = today().date()
    from_dt = dateadd(boyear(to_dt, False), '-5y')
    period_type = 'Quarterly'

    result_management_perspective_layout(company_name, from_dt, to_dt, period_type, 'path_abc')
