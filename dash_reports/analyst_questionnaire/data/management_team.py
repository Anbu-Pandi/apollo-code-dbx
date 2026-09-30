import datetime

import numpy as np
import pandas as pd

from dash_reports.analyst_questionnaire.data.utils import *
from dash_reports.analyst_questionnaire.data.utils import get_industry_wide_financials
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
import dash_reports.analyst_questionnaire.data.debt as debt_data

FORMULA_METRICS = ('IQ_CAPEX', 'IQ_EBITDA_CAPEX_INT', 'IQ_CAPEX_PCT_REV', 'IQ_TOTAL_REV', 'IQ_EBITDA_LEASE_ADJUSTED',
                   'IQ_INTEREST_EXP')

FINANCIAL_ITEMS = {
    'capex_pct_rev': {'title': 'Capex as % of Revenues',
                      'main': ('IQ_CAPEX_PCT_REV', 'Capex as % of Revenues'),
                      'component': [('IQ_CAPEX', 'Capital Expenditure'),
                                    ('IQ_TOTAL_REV', 'Total Revenues')]
                      },
    'ebitda_capex_int': {'title': '(EBITDA - Capex) / Interest Expense',
                         'main': ('IQ_EBITDA_CAPEX_INT', '(EBITDA - Capex) / Interest Expense'),
                         'component': [('IQ_EBITDA_LEASE_ADJUSTED', 'EBITDA, Lease Adjusted'),
                                       ('IQ_CAPEX', 'Capital Expenditure'),
                                       ('IQ_INTEREST_EXP', 'Interest Expense, Total')]
                         }
}

RATING_ORDER = ['NR', 'D', 'C', 'CC', 'CCC-', 'CCC', 'CCC+', 'B-', 'B', 'B+', 'BB-', 'BB', 'BB+', 'BBB-', 'BBB', 'BBB+',
                'A-', 'A', 'A+', 'AA-', 'AA', 'AA+', 'AAA']

rating_value = {'AAA': 23, 'AA+': 22, 'AA': 21, 'AA-': 20, 'A+': 19, 'A': 18, 'A-': 17, 'BBB+': 16, 'BBB': 15,
                'BBB-': 14, 'BB+': 13, 'BB': 12, 'BB-': 11, 'B+': 10, 'B': 9, 'B-': 8, 'CCC+': 7, 'CCC': 6, 'CCC-': 5,
                'CC': 4, 'C': 3, 'D': 2, 'NR': 1, 'SD': 0
                }


def get_leverage_charts(company_id):
    leverage_fig_dict = {}
    result_dict, _, _, _ = debt_data.get_debt_issuance_analysis(company_id, None, None, None)

    if 'Total Debt / EBITDA' in leverage_fig_dict:
        leverage_fig_dict['Total Debt / EBITDA'] = {'company_stats': {'df': None},
                                                    'fig': result_dict['Total Debt / EBITDA']['fig']}

    if 'Net Debt / EBITDA' in leverage_fig_dict:
        leverage_fig_dict['Net Debt / EBITDA'] = {'company_stats': {'df': None},
                                                  'fig': result_dict['Net Debt / EBITDA']['fig']}

    return leverage_fig_dict


def get_analysis_results_dict(company_df, industry_df, ratings_df, company_id, company_name, industry, rating_type,
                              tbl_styling_dict):
    if company_df.empty:
        result_dict = {'company_stats': None,
                       'industry_stats': None,
                       'fig': None}
        return result_dict
    else:
        result_dict = {}

    result_dict['Company vs Industry Rating Chart'] = get_rating_ts_chart(ratings_df, company_id, rating_type,
                                                                          company_name)

    result_dict.update(get_leverage_charts(company_id))

    for key, metric in FINANCIAL_ITEMS.items():
        temp_company_df, temp_industry_df, main_metric = filter_financials_using_metric_config(company_df, industry_df,
                                                                                               metric)
        edit_table_flag = True
        fig = create_metric_percentile_chart(temp_company_df, temp_industry_df, company_name, main_metric, industry)

        result_dict[metric['title']] = {'company_stats': {'df': temp_company_df.reset_index(),
                                                          'dash_props': {'editable': {'editable': edit_table_flag,
                                                                                      'non_editable_cols': NON_EDITABLE_COLS},
                                                                         'conditional_formatting':
                                                                             {
                                                                                 'cell_highlight': tbl_styling_dict
                                                                             },
                                                                         'fixed_columns': 3
                                                                         }
                                                          },
                                        'industry_stats': temp_industry_df.reset_index(),
                                        'fig': fig}

        del temp_company_df, temp_industry_df

    return result_dict


def create_rating_chart(company_df, industry_df, company_name):
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    xaxis_order = list(set(company_df[company_df['qtr'].notnull()]['qtr'].unique().tolist() +
                           industry_df[industry_df['qtr'].notnull()]['qtr'].unique().tolist()
                           )
                       )
    xaxis_order.sort(reverse=False)

    fig.add_trace(
        go.Scatter(x=company_df['qtr'], y=company_df['rating'],
                   name=f'{company_name} - Rating',
                   line=dict(shape='hv', color='black'),
                   mode='lines',
                   showlegend=True,
                   connectgaps=True
                   ))

    fig.add_trace(
        go.Scatter(x=industry_df['qtr'], y=industry_df['rating'],
                   name=f'Industry - Rating',
                   line=dict(shape='hv', color='green'),
                   mode='lines',
                   showlegend=True,
                   connectgaps=True
                   ))

    fig.add_trace(go.Scatter(x=industry_df['qtr'], y=['BB+'] * industry_df['qtr'].count(), fill='tozeroy',
                             mode='none', fillcolor='rgba(255, 0, 0, 0.1)',
                             showlegend=False  # override default markers+lines
                             ))

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      # title=f"Ratings Chart - {company_name}",
                      xaxis=dict(tickformat="%e %b %Y", tickangle=270, title_text='Rating Date', tick0=1,
                                 showgrid=True, linecolor='black', categoryorder='array', categoryarray=xaxis_order),
                      yaxis=dict(title_text='Ratings', categoryorder='array', showdividers=True, dtick=1,
                                 categoryarray=RATING_ORDER, showgrid=True, linecolor='black'),
                      clickmode='event+select',
                      )

    return fig


def _add_missing_ratings(df):
    ratings_in_df = set(df['rating'])
    missing_rating = list(set(RATING_ORDER) - ratings_in_df)
    rating_df = pd.DataFrame({'rating': missing_rating})
    rating_df = pd.concat([df, rating_df])
    return rating_df


def _get_target_company_ratings(df, id_bb_company):
    if 'id_bb_company' in df.columns:
        df = df[df['id_bb_company'] == id_bb_company]
        df = _add_missing_ratings(df)
        return df
    return df[df['company_id'] == -1]


def _get_dict_keys(value):
    for k, v in rating_value.items():
        if float(v) == float(value):
            return k
    return 'NR'


def _get_industry_rating_df(df):
    df['rating_value'] = df['rating'].map(
        lambda x: rating_value[x] if x in rating_value.keys() else 0)

    df = np.floor(df.groupby('qtr')['rating_value'].mean())
    df = df.reset_index()
    df['rating'] = df.rating_value.map(lambda x: _get_dict_keys(x))
    df = _add_missing_ratings(df)

    return df


def get_rating_ts_chart(df, company_id, rating_type, company_name):
    df = df.sort_values(['company_name', 'ratingdate', 'qtr'], ascending=False)
    df = df.drop('ratingdate', axis=1)
    df = df.drop_duplicates(['company_id', 'company_name', 'ratingtypecode', 'qtr'], keep='first')

    curr_year = datetime.datetime.now().year
    transformed_rating_df = _get_transformed_rating_df(df, curr_year - 5, curr_year)

    target_company_rating_df = _get_target_company_ratings(transformed_rating_df, company_id)
    industry_rating_df = _get_industry_rating_df(transformed_rating_df)

    fig = create_rating_chart(target_company_rating_df, industry_rating_df, company_name)

    rating_dict = {'company_stats': {'df': None,
                                     'dash_props': {'editable': {'editable': False,
                                                                 'non_editable_cols': ['company_name', 'rating',
                                                                                       'ratingdate']},
                                                    'fixed_columns': 3,
                                                    }
                                     },
                   'industry_stats': None,
                   'fig': fig}

    return rating_dict


def _get_transformed_rating_df(df, from_dt_year, to_dt_year):
    qtr_list = []
    for year in range(from_dt_year, to_dt_year + 1):
        for qtr in range(1, 5):
            qtr_list.append(f'{year} {qtr}Q')

    missing_qtr = list(set(qtr_list) - set(df['qtr']))
    df = pd.concat([df, pd.DataFrame({'qtr': missing_qtr})])
    df = df[['company_id', 'company_name', 'rating', 'qtr']]

    index_list = df.columns.tolist()
    index_list.remove('rating')

    df = df.set_index(index_list)
    df = df.unstack('qtr')
    df = df.sort_index(ascending=False, axis=1)
    df = df.fillna(method='bfill', axis=1)
    df = df.stack('qtr').reset_index()
    df = df[df['qtr'] >= f'{from_dt_year} 1Q']

    return df


def get_management_perspective_analysis(id_bb_company, from_date, to_date, period_type, rating_type):
    logging.info(f'Analyst Questionnaire: Generating Management Perspective Analysis for {id_bb_company}')

    company_name, industry, financials_df = get_industry_wide_financials(id_bb_company, 'analyst_ques_mgmt')
    ratings_df = default_es.search_exact('analyst_ques_ratings', 'industry', industry)

    updated_financials_df, tbl_styling_dict = override_company_financials(financials_df, company_name, id_bb_company,
                                                                          industry)

    company_metrics_df = filter_company_financials_by_id(updated_financials_df, id_bb_company)
    industry_metrics_df = get_industry_percentiles(updated_financials_df)
    result_dict = get_analysis_results_dict(company_metrics_df, industry_metrics_df, ratings_df, id_bb_company,
                                            company_name, industry,
                                            rating_type, tbl_styling_dict)

    logging.info(f'Analyst Questionnaire: Management Perspective Analysis Generated for {company_name}')

    return result_dict, company_name, industry


if __name__ == '__main__':
    from common.utils.dtutil import today, boyear, dateadd
    from apo_config.config import config

    config.init('dev')

    company_name = "KB Home"  # 'Foot Locker, Inc.'
    company_id = 315394
    to_dt = today().date()
    from_dt = dateadd(boyear(to_dt, False), '-5y')
    period_type = 'LTM'
    rating_type = 'STDLONG'

    get_management_perspective_analysis(company_id, from_dt, to_dt, period_type, rating_type)
