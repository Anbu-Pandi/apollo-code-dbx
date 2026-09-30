import logging
import dash_html_components as html
import dash_core_components as dcc

import pandas as pd
import json

import dash_reports.analyst_questionnaire.pages.utils as page_utils
from dash_reports.analyst_questionnaire.data.equity import get_equity_issuance_analysis
from dash_reports.analyst_questionnaire.data.questions import get_section_questions


def create_equity_issuance_layout():
    return html.Div(
        children=[
            dcc.Store(id="diff-store", storage_type='memory'),
            page_utils.get_company_name_dropdown(),
            page_utils.get_data_button(),
            html.Div(id='questionnaire-result-container'),
            html.Div(id='push-to-db-result-container2')

        ]
    )


def get_ques_assumptions_statement(base_value_dict):
    ltm_dvd_paid = base_value_dict['IQ_COMMON_DIV_CF']['absolute']
    dvd_6m_rate = base_value_dict['IQ_COMMON_DIV_CF']['pct_change']
    out_shares = base_value_dict['sharesOutstanding']['absolute']
    out_shares_rate = base_value_dict['sharesOutstanding']['pct_change']
    dvd_forecast = base_value_dict['DIVIDEND_FORECAST']['absolute']
    dvd_6m_pct_chg_forecast = base_value_dict['DIVIDEND_FORECAST']['pct_change']

    assumptions = [f"Assumptions:", html.Br(),
                   f"(a) {page_utils.assumption_title['IQ_COMMON_DIV_CF']['absolute']}: {ltm_dvd_paid}", html.Br(),
                   f"(b) {page_utils.assumption_title['IQ_COMMON_DIV_CF']['pct_change']}: {dvd_6m_rate}", html.Br(),
                   f"(c) {page_utils.assumption_title['sharesOutstanding']['absolute']}: {out_shares}", html.Br(),
                   f"(d) {page_utils.assumption_title['sharesOutstanding']['pct_change']}: {out_shares_rate}",
                   html.Br(),
                   f"(e) {page_utils.assumption_title['DIVIDEND_FORECAST']['absolute']}: {round(dvd_forecast, 2)}",
                   html.Br(),
                   f"(f) {page_utils.assumption_title['DIVIDEND_FORECAST']['pct_change']}: {round(dvd_6m_pct_chg_forecast, 2)}",
                   html.Br(),
                   ]
    return assumptions


def get_equity_section_questions_html(section, id_bb_company, assumptions_list=None, default_input_values={}):
    children = [html.Div([html.H2(['Questions: '], className="subtitle padded")], className="six columns")]

    df_ques = get_section_questions(section)
    base_value_div = page_utils.get_qid_base_value_html(section, assumptions_list)
    children.append(base_value_div)

    sub_child = [html.Br()]

    for qid in df_ques['qid'].values:
        input_properties = df_ques.loc[df_ques['qid'] == qid, 'input_properties'].iloc[0]
        input_type = df_ques.loc[df_ques['qid'] == qid, 'input_type'].iloc[0]
        ques = df_ques.loc[df_ques['qid'] == qid, 'description'].iloc[0]

        ques_div_display = 'inline-block'
        or_label = ''

        if pd.isnull(input_properties):
            input_properties = {}
        else:
            input_properties = json.loads(input_properties)

        if qid in default_input_values.keys():
            input_properties.update({'value': default_input_values[qid]})

        component = page_utils.get_component({'type': page_utils.ANALYST_RESPONSE, 'index': f'{section}|{qid}'},
                                             input_type, input_properties)

        if qid in ['shares_out_chg_est_million', 'div_paid_chg_est_million']:
            ques_div_display = 'none'

        if qid in ['div_paid_chg_est', 'shares_out_chg_est']:
            or_label = html.Label('OR', style={'margin-right': '20px', 'margin-left': '20px'})

        ques_div = html.Div([html.Label(ques, style={'font-size': '14px'},
                                        id={'type': page_utils.ANALYST_QUESTIONS, 'index': f'{section}|{qid}'})],
                            style={'display': ques_div_display, 'margin-right': '20px'})  # 'width': '33%',

        ques_calc_div = html.Div(dcc.Markdown([], style={'font-size': '14px', 'color': 'red'}, mathjax=True,
                                              id={'type': page_utils.QUES_CALC_DIV, 'index': f'{section}|{qid}'}))

        sub_child.append(html.Div([ques_div, html.Div([component, ques_calc_div],
                                                      style={'display': 'inline-block'}),
                                   or_label],
                                  style={'margin-right': '20px', 'display': 'inline-block'}
                                  )
                         )

    children = children + sub_child + [html.Br(), page_utils.get_analyst_response_button(section), html.Br()]
    return html.Div(children=children)


def result_equity_issuance_layout(id_bb_company, from_date, to_date, period_type, path):
    logging.info(f'Analyst Questionnaire: Designing Equity Page Layout ')

    result_dict, company_name, industry, base_value_dict = get_equity_issuance_analysis(id_bb_company, from_date,
                                                                                        to_date, period_type)
    heading = f'{company_name}  | {period_type} Financials  | {from_date} to {to_date} | Industry: {industry}'

    val = page_utils.unpack_result_html(result_dict, heading)
    assumptions_list = get_ques_assumptions_statement(base_value_dict)
    section = 'equity'

    default_ques_input_values = {'shares_out_chg_est': base_value_dict['sharesOutstanding']['pct_change'],
                                 'shares_out_chg_est_million': base_value_dict['sharesOutstanding']['nominal_change'],
                                 'div_paid_chg_est': base_value_dict['DIVIDEND_FORECAST']['pct_change'],
                                 'div_paid_chg_est_million': base_value_dict['DIVIDEND_FORECAST']['absolute']
                                 }
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [html.Div([page_utils.snp_financials_page(id_bb_company, path)], className="subtitle padded"),
                         html.Br(),
                         get_equity_section_questions_html(section, id_bb_company, assumptions_list,
                                                           default_ques_input_values),
                         val,
                         dcc.Store(id={'type': page_utils.QUES_ASSUMPTION_VALUE_STORE_TYPE_ID, 'index': section},
                                   storage_type='memory',
                                   data={section: base_value_dict})
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

    get_equity_issuance_analysis(company_name, from_dt, to_dt, period_type, 'path_abc')
