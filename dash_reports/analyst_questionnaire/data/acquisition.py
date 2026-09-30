import logging
import pandas as pd

from common.utils.dtutil import today, dateadd, boyear
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.utils import get_industry_wide_financials
from dash_reports.analyst_questionnaire.data.utils import get_industry_wide_financials
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX

FORMULA_METRICS = ('IQ_EBITDA_CAPEX_INT',)

FINANCIAL_ITEMS = {
    'ebitda_capex_int': {'title': 'Total Enterprise Value v/s Interest Coverage Ratio v/s 1yr Returns',
                         'main': ('IQ_EBITDA_CAPEX_INT', '(EBITDA - Capex) / Interest Expense')}
}


def _create_bubble_chart(df, target_company_id, company_name, qtr):
    import plotly.express as px
    import plotly.graph_objects as go

    df['TEV'] = df['TEV'].fillna(0)
    target_company_df = df.loc[df['company_id'] == target_company_id]
    industry_df = df.loc[df['company_id'] != target_company_id]
    left_x = df['dataitem_value'].quantile(0.25)
    left_x -= abs(left_x * 0.10)
    right_x = df['dataitem_value'].quantile(0.95)
    right_x += abs(right_x * 0.10)
    industry_df.loc[industry_df['TEV'] < 0, 'TEV'] = 0
    fig = px.scatter(industry_df, x="dataitem_value", y="totalReturn1Y",
                     size="TEV", color='company_name',
                     hover_name="company_name", size_max=60,
                     labels={'dataitem_value': 'Interest Coverage Ratio',
                             'company_name': 'Company',
                             'totalReturn1Y': '1yr Equity Return in %',
                             'TEV': 'Total Enterprise Value'})

    fig.add_trace(go.Scatter(x=target_company_df["dataitem_value"], y=target_company_df["totalReturn1Y"],
                             mode='markers', name=company_name,
                             marker=dict(color='black', symbol='star', size=25),
                             showlegend=True)
                  )

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      xaxis_range=[left_x, right_x],
                      width=1500,
                      height=500,
                      xaxis=dict(zeroline=True, zerolinecolor='black',
                                 showgrid=True, gridwidth=0.5, gridcolor='lightgrey', ),
                      yaxis=dict(zeroline=True, zerolinecolor='black',
                                 showgrid=True, gridwidth=0.5, gridcolor='lightgrey'), )
    return fig


def get_bubble_chart(df, id_bb_company, company_name):
    issuer_df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', id_bb_company)
    company_id = issuer_df.iloc[0]['factset_entity_id']

    filter_condition = df['formula_metric'].isin(FORMULA_METRICS)
    df = df[filter_condition]
    max_target_qtr = df.loc[df['company_id'] == company_id]['qtr'].max()

    fig = _create_bubble_chart(df.loc[df['qtr'] == max_target_qtr], company_id, company_name, max_target_qtr)

    df = df[['company_id', 'company_name', 'industry_level_4', 'period_end_date',
             'qtr', 'dataitem_value', 'totalReturn1Y', 'TEV']]
    df = df.rename(columns={'industry_level_4': 'industry',
                            'dataitem_value': '(EBITDA - Capex) / Interest Expense',
                            'totalReturn1Y': 'Total 1yr Return',
                            'TEV': 'Total Enterprise Value'})
    df = df.melt(id_vars=df.columns[:5], var_name='metric', value_name='value')
    df = df.pivot_table(index=['company_id', 'company_name', 'industry', 'metric'],
                        columns=['qtr'], values=['value'],
                        aggfunc={'value': 'sum'}).reset_index()
    df.columns = [x[1] if x[1] != '' else x[0] for x in df.columns]
    df = pd.concat([df[df['company_id'] == company_id], df[df['company_id'] != company_id]]).drop(columns='company_id')
    bubble_dict = {'company_stats': {'df': df,
                                     'dash_props': {'editable': {'editable': False,
                                                                 'non_editable_cols': ['company_name', 'qtr',
                                                                                       'industry']},
                                                    'fixed_columns': 1,
                                                    },
                                     },
                   'industry_stats': None,
                   'fig': fig}

    return bubble_dict


def get_analysis_results_dict(df, id_bb_company, company_name):
    if df.empty:
        result_dict = {'company_stats': None,
                       'industry_stats': None,
                       'fig': None}
        return result_dict
    else:
        result_dict = {}

    for key, metric in FINANCIAL_ITEMS.items():
        result_dict[metric['title']] = get_bubble_chart(df, id_bb_company, company_name)

    return result_dict


def get_acquisition_analysis(id_bb_company, from_date, to_date, period_type):
    logging.info(f'Analyst Questionnaire: Generating Acquisition Analysis for {id_bb_company}')

    company_name, industry, industry_pricing_df = get_industry_wide_financials(id_bb_company,
                                                                                     'analyst_ques_acquisition')
    result_dict = get_analysis_results_dict(industry_pricing_df, id_bb_company, company_name)
    logging.info(f'Analyst Questionnaire: Acquisition Analysis Generated for {company_name}')

    return result_dict, company_name, industry


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')

    target_company_id = 315394
    to_dt = today().date()
    from_dt = dateadd(boyear(to_dt, False), '-5y')
    period_type = 'LTM'
    df = get_acquisition_analysis(target_company_id, from_dt, to_dt, period_type)
    pass
