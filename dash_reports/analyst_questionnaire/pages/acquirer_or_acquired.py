import logging
import dash_html_components as html

from dash_reports.analyst_questionnaire.pages.utils import snp_financials_page, get_questions_html, unpack_result_html
from dash_reports.analyst_questionnaire.data.acquisition import get_acquisition_analysis


def result_acquisition_layout(id_bb_company, from_date, to_date, period_type, path):
    logging.info(f'Analyst Questionnaire: Designing Acquisition Page Layout ')
    result_dict, company_name, industry = get_acquisition_analysis(id_bb_company, from_date, to_date, period_type)
    heading = f'{company_name}  | {period_type} Financials  | {from_date} to {to_date} | Industry: {industry}'
    val = unpack_result_html(result_dict, heading)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [html.Div([snp_financials_page(id_bb_company, path)], className="subtitle padded"),
                         html.Br(),
                         get_questions_html('acquisition', id_bb_company),
                         val
                         ],
                        className="row ",
                    ),
                ],
                className="sub_page",
            ),
        ],
        className="page",
    )
