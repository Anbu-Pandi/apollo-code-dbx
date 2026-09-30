import logging

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from common.utils.dfutil import tolist
from dash_reports.report_common.elastic_search.api import default_es

from data_access.dbtables.analyst_questionnaire.override_data import get_latest_override_df
from data_access.dbtables.analyst_questionnaire.analyst_response import get_analyst_response_by_company, \
    get_fundamental_score
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX

PERCENTILE_CHART_DICT = {'25%': 'orange', '50%': 'green', '75%': 'blue'}

NON_EDITABLE_COLS = ['company_name', 'industry', 'metrics']


def create_metric_percentile_chart(company_df, industry_df, company_name, metric, industry):
    metric = tolist(metric)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    temp_company_df = company_df.loc[company_df.index.get_level_values('metrics').isin(metric)]
    if len(industry_df) > 0:
        temp_industry_df = industry_df.loc[industry_df.index.get_level_values('metrics').isin(metric)]
    else:
        temp_industry_df = industry_df

    xaxis_order = list(set(list(temp_company_df.columns) + list(temp_industry_df.columns)))
    xaxis_order.sort(reverse=True)

    fill_color_dict = {'25%': None, '50%': 'rgba(255, 99, 71, 0.1)', '75%': 'rgba(0, 255, 127, 0.1)'}

    fig.add_trace(
        go.Scatter(x=temp_company_df.iloc[0].index.values.tolist(),
                   y=pd.to_numeric(temp_company_df.iloc[0], errors='coerce').values.tolist(),
                   name=company_name,
                   mode='markers',
                   marker=dict(color='black'),
                   showlegend=True))

    if len(temp_industry_df) > 0:
        for key, value in PERCENTILE_CHART_DICT.items():
            assert len(metric) == 1, f'check config. {metric} not len==1'
            fig.add_trace(
                go.Scatter(x=temp_industry_df.loc[(metric[0], key)].index.values.tolist(),
                           y=pd.to_numeric(temp_industry_df.loc[(metric[0], key)], errors='coerce').values.tolist(),
                           name='industry ' + key + '-ile',
                           line=dict(color=value, width=1, dash='dot'), fill='tonexty',
                           fillcolor=fill_color_dict[key],
                           showlegend=True))

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      # title=f"Company: {company_name}. Industry: {industry}. Metric: {metric}",
                      xaxis=dict(tickangle=270, autorange="reversed", dtick=1, tick0=2, showline=True, linewidth=1,
                                 zeroline=True, zerolinewidth=2, zerolinecolor='LightPink', linecolor='black',
                                 categoryorder='array', categoryarray=xaxis_order),
                      yaxis=dict(showgrid=True, tick0=2, showline=True, linewidth=1, linecolor='black'),
                      yaxis2=dict(overlaying='y', side='right', tick0=2, showline=True,
                                  linewidth=1),
                      clickmode='event+select'
                      )

    del temp_company_df, temp_industry_df

    return fig


def split_qtr_year(x):
    val = x.split(' ')
    return int(val[0]), val[1]


def filter_financials_using_metric_config(co_df, industry_df, metric_config):
    METRIC_COL_NAME_MAP = {'metrics': 'metric_description'}
    metric_desc_map = {}

    def _parse_metrics(key, desc_map):
        tpls = metric_config.get(key, [])
        if not isinstance(tpls, list):
            tpls = [tpls]
        desc_map.update({x[0]: x[1] for x in tpls})
        ids_ids = [x[0] for x in tpls]
        return tpls, ids_ids

    main_tuples, main_ids = _parse_metrics('main', metric_desc_map)
    components_tuples, component_ids = _parse_metrics('component', metric_desc_map)

    metric_desc_map_df = pd.DataFrame(metric_desc_map, index=[0]).T.reset_index() \
        .rename(columns={'index': 'formula_metric', 0: 'metrics'})

    metric_id_list = component_ids + main_ids
    main_metric_desc_list = [x[1] for x in main_tuples]

    def _reindex_df(_df, metric_row_combos):
        _df = _df.reset_index(level='metrics')
        _df = _df.reindex(pd.MultiIndex.from_product(metric_row_combos, names=_df.index.names))
        _df = _df.set_index('metrics', append=True).reset_index(level='formula_metric')
        _df.rename_axis([METRIC_COL_NAME_MAP.get(x, x) for x in _df.index.names], inplace=True)
        index_names = _df.index.names
        _df = pd.merge(_df.reset_index(), metric_desc_map_df, on='formula_metric', how='left') \
            .set_index(index_names)
        _df = _df.set_index('metrics', append=True) \
            .reset_index(level='metric_description') \
            .drop(columns=['formula_metric', 'metric_description'])
        return _df

    co_df = _reindex_df(co_df, [co_df.index.levels[0], co_df.index.levels[1], metric_id_list])

    if len(industry_df) > 0:
        industry_df = _reindex_df(industry_df, [metric_id_list, industry_df.index.levels[2]]) \
            .swaplevel('quantile')

    return co_df, industry_df, main_metric_desc_list


def get_industry_percentiles(df):
    logging.info('Computing Industry Stats')

    if len(df) == 0:
        return pd.DataFrame()

    df = df.drop(['company_id'], axis=1)
    df['value'] = pd.to_numeric(df['value'], errors='coerce').replace([np.inf, -np.inf], np.nan)
    summary_df = df.groupby(['metrics', 'formula_metric', 'qtr'])[['value']].describe()
    summary_df.columns = summary_df.columns.droplevel(0)

    if np.any([x not in summary_df.columns for x in ['25%', '50%', '75%']]):
        return pd.DataFrame()

    summary_df = summary_df[['25%', '50%', '75%']]
    summary_df = summary_df.stack().swaplevel('qtr').unstack()
    summary_df.index.names = summary_df.index.names[:-1] + ['quantile']
    summary_df = summary_df.sort_index(ascending=True, axis=1)

    return summary_df


def filter_company_financials_by_id(df, id_bb_company):
    if len(df) == 0:
        return pd.DataFrame()
    issuer_df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', id_bb_company)
    fin_company_id = issuer_df.iloc[0]['factset_entity_id']

    company_financials_df = df[df['company_id'] == fin_company_id]
    company_financials_df = company_financials_df.pivot_table(
        index=['company_name', 'industry', 'metrics', 'formula_metric'],
        columns=['qtr'], values=['value'], aggfunc={'value': 'sum'})
    company_financials_df = company_financials_df.sort_index(ascending=True, axis=1)

    if len(company_financials_df) > 0:
        company_financials_df.columns = company_financials_df.columns.droplevel(0)

    return company_financials_df


def _get_consolidated_metric_list(data_dict):
    metric_list = []
    main_item = None
    for key, value in data_dict.items():
        if key == 'main':
            main_item = value

        if isinstance(value, str):
            metric_list.append(value)
        else:
            metric_list += list(value)

    return metric_list, main_item


def get_analyst_answers(company_id, qid):
    df = get_analyst_response_by_company(company_id, qid)

    response_dict = None

    filter_condition = (df['last_update_ts'] == df['last_update_ts'].max()) & (df['question'] == qid)
    temp_df = df.loc[filter_condition]

    if not temp_df.empty:
        response_dict = {'date': str(temp_df['asof_date'].iloc[0]),
                         'last_updated_by': temp_df['last_updated_by'].iloc[0],
                         'last_update_ts': temp_df['last_update_ts'].iloc[0].date(),
                         'value': temp_df['answer'].iloc[0]}

    if qid == 'ratings_mom_score':
        issuer_df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', company_id)
        ticker = issuer_df['company_corp_ticker'].iloc[-1]
        apo_fundamental_score = get_fundamental_score()
        apo_fundamental_score = apo_fundamental_score[apo_fundamental_score['corp_ticker'] == ticker]
        if not apo_fundamental_score.empty and not temp_df.empty:
            if temp_df['last_update_ts'].iloc[0].date() < apo_fundamental_score['start_date'].iloc[-1]:
                response_dict = {'date': str(apo_fundamental_score['start_date'].iloc[-1]),
                                 'last_updated_by': 'BBG API',
                                 'last_update_ts': apo_fundamental_score['load_date'].iloc[-1].date(),
                                 'value': apo_fundamental_score['score'].iloc[-1]}

    return response_dict


def override_company_financials(company_df, company_name, company_id, industry):
    logging.info(f'Analyst Questionnaire: Fetching analyst updates for {company_name}')

    latest_overrides = pd.DataFrame()
    if len(company_df) > 0:
        latest_overrides = get_latest_override_df(company_id, company_df['metrics'].values.tolist())

    overriden_styling_dict = {}
    if latest_overrides.empty:
        logging.info(f'Analyst Questionnaire: No Data Found for Override {company_name}')
        return company_df, overriden_styling_dict

    latest_overrides = latest_overrides.set_index(['metric', 'source_col'])

    id_bb_company = latest_overrides['company_id'].iloc[-1]

    overide_key_to_value_dict = latest_overrides.to_dict()['value']

    for (metric, col), col in overide_key_to_value_dict.items():
        filter_condition = (company_df['company_id'] == id_bb_company) & \
                           (company_df['industry'] == industry) & \
                           (company_df['qtr'] == col) & \
                           (company_df['metrics'] == metric)

        prev_value = company_df.loc[filter_condition, 'value'].iloc[0]
        overriden_value = float(overide_key_to_value_dict[(metric, col)])

        company_df.loc[filter_condition, 'value'] = overriden_value
        overriden_styling_dict[f'{company_id}_{col}_{metric}'] = {'snp': prev_value, 'analyst_value': overriden_value}

    company_df['value'] = pd.to_numeric(company_df['value'], errors='coerce').replace([np.inf, -np.inf], np.nan)
    return company_df, overriden_styling_dict


def get_industry_wide_financials(id_bb_company, segment_name):
    df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', id_bb_company)
    df = df[df['id_bb_company'] == id_bb_company]

    industry = df.iloc[0]['factset_company_industry']
    company_name = df.iloc[0]['id_bb_global_company_name']

    fin_df = default_es.search_exact(segment_name, 'industry', industry)
    if len(fin_df) > 0:
        fin_df = fin_df[fin_df['industry'] == industry]
        fin_df['value'] = pd.to_numeric(fin_df['value'], errors='coerce').replace([np.inf, -np.inf], np.nan)

    return company_name, industry, fin_df


def get_growth_lines(ds):
    ds = ds.dropna()
    def window_ret(x, m=1, n=1):
        if n > 0:
            return ((x.iloc[-1] / x.iloc[0]) ** (1 / n) - 1) * 100 * m

    rets = {}
    num_qtrs = min(len(ds), 21)
    num_yrs = (num_qtrs - 1) / 4.0

    rets['1y annualized (rhs)'] = ds.rolling(window=5).apply(window_ret)
    rets['6m annualized (rhs)'] = ds.rolling(window=3).apply(lambda x: window_ret(x, m=2))
    rets['qoq (rhs)'] = ds.rolling(window=2).apply(window_ret)
    rets['5y cagr (rhs)'] = pd.Series([window_ret(ds.iloc[-num_qtrs:], n=num_yrs)] * len(ds), index=ds.index)

    return rets
