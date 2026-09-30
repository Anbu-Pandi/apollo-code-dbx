import numpy as np
from colour import Color

from dash_reports.analyst_questionnaire.data.utils import *
from dash_reports.analyst_questionnaire.data.utils import get_industry_wide_financials
from dash_reports.analyst_questionnaire.data.utils import get_growth_lines

FORMULA_METRICS = ['IQ_NET_DEBT_EBITDA', 'IQ_TOTAL_DEBT_EBITDA', 'IQ_EBITDA_LEASE_ADJUSTED',
                   'IQ_NET_DEBT', 'IQ_TOTAL_DEBT', 'IQ_CASH_EQUIV', ]

FINANCIAL_ITEMS = {
    'total_debt_ebitda': {'title': 'Total Debt / EBITDA',
                          'main': ('IQ_TOTAL_DEBT_EBITDA', 'Total Debt / EBITDA'),
                          'component': [('IQ_TOTAL_DEBT', 'Total Debt'),
                                        ('IQ_EBITDA_LEASE_ADJUSTED', 'EBITDA, Lease Adjusted')]},

    'net_debt_ebitda': {'title': 'Net Debt / EBITDA',
                        'main': ('IQ_NET_DEBT_EBITDA', 'Net Debt / EBITDA'),
                        'component': [('IQ_NET_DEBT', 'Net Debt'),
                                      ('IQ_EBITDA_LEASE_ADJUSTED', 'EBITDA, Lease Adjusted')]},
}


def seasonal_debt_change(df):
    if len(df) == 0:
        return pd.DataFrame(), []

    df = df.stack(dropna=False)
    df.index.names = df.index.names[:-1] + ['qtr']
    df = df.fillna(0).diff()
    df.name = 'Total'
    df = df.reset_index()
    df['year'], df['qtr_num'] = list(zip(*df['qtr'].apply(split_qtr_year)))

    df_pivot = df.pivot_table(index=['year'], columns=['qtr_num'], values=['Total'], aggfunc={'Total': 'sum'})
    df_pivot.columns = df_pivot.columns.droplevel(0)
    df_pivot = df_pivot.fillna(0).cumsum(axis=1)

    charting_df = df_pivot.copy()
    charting_df.insert(0, '0Q', 0)

    fig = _create_seasonal_debt_chart(charting_df)

    return df_pivot, fig


def get_color_shades(N):
    start_color = '#ADD8E6'
    end_color = '#00008b'
    colorscale = [x.hex for x in list(Color(start_color).range_to(Color(end_color), N))]
    for i in range(N):
        yield colorscale[i]


def _create_seasonal_debt_chart(df):
    if df.empty:
        return []

    df = df.T
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    for shade, col in zip(get_color_shades(len(df.columns)), df.columns):
        fig.add_trace(
            go.Scatter(x=df[col].index.values.tolist(), y=df[col].values.tolist(),
                       name=col,
                       mode='lines',
                       line=dict(color=shade),
                       showlegend=True))

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      xaxis=dict(tickangle=270, dtick=1, tick0=2, showline=True, linewidth=1,
                                 zeroline=True, zerolinewidth=2, zerolinecolor='LightPink', linecolor='black'),
                      yaxis=dict(showgrid=True, tick0=2, mirror=True, showline=True, linewidth=1, linecolor='black'),
                      yaxis2=dict(overlaying='y', side='right', tick0=2, mirror=True, showline=True, linecolor='black',
                                  linewidth=1),
                      clickmode='event+select'
                      )

    return fig


def update_df_with_others_or_total(df, metric_desc_map={}):
    if (len(df) == 0):
        return pd.DataFrame()
    total_debt_metric_desc = metric_desc_map.get('IQ_TOTAL_DEBT', None)
    mask = df.index.get_level_values('metrics') == total_debt_metric_desc
    total_debt_df = df[mask]
    if len(total_debt_df) == 0:
        total_debt_df = df.sum().to_frame().T
        total_debt_df.index = df.iloc[[0]].index
        total_debt_df = total_debt_df.rename(
            index={df.iloc[[0]].index.get_level_values('metrics')[0]: total_debt_metric_desc, },
            level='metrics')
        total_debt_df = total_debt_df.rename(
            index={df.iloc[[0]].index.get_level_values('formula_metric')[0]: 'IQ_TOTAL_DEBT', },
            level='formula_metric')
        df = pd.concat([df, total_debt_df])
    else:
        categorized_debt_df = df[df.index.get_level_values('metrics') != total_debt_metric_desc].sum()
        others_df = total_debt_df - categorized_debt_df
        others_df = others_df.rename(index={total_debt_metric_desc: 'Others/Adj'}, level='metrics')
        others_df = others_df.rename(index={'IQ_TOTAL_DEBT': 'Others/Adj', }, level='formula_metric')

        df = pd.concat([df, others_df])
    return df


def metric_desc_map_f(metric_dict):
    lambda metric: {x[0]: x[1] for x in (metric if isinstance(metric, list) else [metric])}

    metric_desc_map = metric_desc_map_f(metric_dict.get('main', []))
    metric_desc_map.update(metric_desc_map_f(metric_dict.get('component', [])))

    return metric_desc_map


def create_result_config(temp_company_df, temp_industry_df, fig, edit_table_flag, tbl_styling_dict):
    return {'company_stats': {'df': temp_company_df.reset_index(),
                              'dash_props': {'editable': {'editable': edit_table_flag,
                                                          'non_editable_cols': NON_EDITABLE_COLS},
                                             'conditional_formatting':
                                                 {
                                                     'cell_highlight': tbl_styling_dict
                                                 },
                                             'fixed_columns': 3,
                                             }
                              },
            'industry_stats': temp_industry_df.reset_index(),
            'fig': fig}


def get_analysis_results_dict(company_df, industry_df, company_name, industry, tbl_styling_dict):
    if company_df.empty:
        return {'company_stats': None, 'industry_stats': None, 'fig': None}

    result_dict = {}
    for item, metric_dict in FINANCIAL_ITEMS.items():
        temp_company_df, temp_industry_df, main_metric = filter_financials_using_metric_config(company_df, industry_df,
                                                                                               metric_dict)
        edit_table_flag = True

        fig = create_metric_percentile_chart(temp_company_df, temp_industry_df, company_name, main_metric, industry)

        result_dict[metric_dict['title']] = create_result_config(temp_company_df, temp_industry_df, fig,
                                                                 edit_table_flag, tbl_styling_dict)

    return result_dict


def get_debt_results_dict(co_debt_df, industry_debt_df, tbl_styling_dict):
    result_dict = {}
    edit_table_flag = True

    metrics_desc_map = co_debt_df.reset_index()[['formula_metric', 'metrics']] \
        .set_index('formula_metric')['metrics'].to_dict()
    if 'IQ_TOTAL_DEBT' not in metrics_desc_map:
        metrics_desc_map['IQ_TOTAL_DEBT'] = 'Total Debt'

    co_debt_df = update_df_with_others_or_total(co_debt_df, metrics_desc_map)
    mask = co_debt_df.index != co_debt_df.index
    if len(co_debt_df) > 0:
        mask = co_debt_df.index.get_level_values('metrics') == metrics_desc_map['IQ_TOTAL_DEBT']
    non_total_debt_df = co_debt_df[~mask]
    total_debt_df = co_debt_df[mask]

    fig = _get_debt_stack_fig(total_debt_df, non_total_debt_df)
    result_dict['Debt Structure'] = create_result_config(non_total_debt_df, industry_debt_df,
                                                         fig, edit_table_flag, tbl_styling_dict)
    total_debt_df, fig = seasonal_debt_change(total_debt_df)

    edit_table_flag = False
    result_dict['Seasonal Debt'] = create_result_config(total_debt_df, industry_debt_df,
                                                        fig, edit_table_flag, tbl_styling_dict)
    return result_dict


def _get_debt_stack_fig(total_df, category_debt_df):
    if category_debt_df.empty:
        return []
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    category_debt_df = category_debt_df.T
    x_axis = category_debt_df.index.tolist()

    for metric_col in category_debt_df.columns:
        fig.add_trace(go.Bar(name=metric_col[-1], x=x_axis, y=category_debt_df[metric_col].values.tolist(),
                             showlegend=True))
    if len(total_df) > 0:
        total_df = total_df.dropna(axis=1).T
        rets = get_growth_lines(total_df[total_df.columns[0]])

        for k, v in rets.items():
            fig.add_trace(go.Line(name=k, x=v.index.tolist(), y=v.values.tolist()), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      xaxis=dict(tickangle=270, dtick=1, tick0=2, showline=True, linewidth=1, linecolor='black',
                                 zeroline=True, zerolinewidth=2, zerolinecolor='LightPink'),
                      yaxis=dict(showgrid=True, mirror=True, showline=True, linewidth=1, linecolor='black'),
                      bargap=0.3,
                      bargroupgap=0.1,
                      barmode='relative')

    return fig


def get_ques_base_value(company_fin_df, id_bb_company, company_name):
    data_item = 'IQ_TOTAL_DEBT'

    logging.info(f'Get questions base value: bbg_company_id: {id_bb_company} Company Name: {company_name} '
                 f'data_item: {data_item}')

    data_item_df = pd.DataFrame()
    if len(company_fin_df) > 0:
        data_item_df = company_fin_df[company_fin_df.index.get_level_values('formula_metric') == data_item]

    absolute_base_value = None
    pct_change_value = None
    nominal_change = None

    if not data_item_df.empty:
        absolute_base_value = data_item_df.iloc[:, -1].iloc[-1]
        absolute_base_value = round(absolute_base_value, 2)

        pct_change_data_item_df = data_item_df.stack().sort_index(level='qtr', ascending=True).rolling(window=3) \
                                      .apply(lambda x: x.iloc[-1] / x.iloc[0] - 1) * 100

        pct_change_value = round(pct_change_data_item_df.iloc[-1], 2)
        nominal_change = round(absolute_base_value*pct_change_value*0.01,2)

    return {data_item: {'absolute': absolute_base_value, 'pct_change': pct_change_value, 'nominal_change': nominal_change}}


def get_debt_issuance_analysis(id_bb_company, from_date, to_date, period_type):
    logging.info(f'Analyst Questionnaire: Generating Debt Issuance Analysis for {id_bb_company}')

    company_name, industry, industry_wide_financials = get_industry_wide_financials(id_bb_company,
                                                                                    'analyst_ques_debt')
    industry_wide_financials_updated, overriden_styling_dict = override_company_financials(industry_wide_financials,
                                                                                           company_name, id_bb_company,
                                                                                           industry)
    company_financials = filter_company_financials_by_id(industry_wide_financials_updated, id_bb_company)
    industry_metrics_df = get_industry_percentiles(industry_wide_financials_updated)

    result_dict = get_analysis_results_dict(company_financials, industry_metrics_df, company_name,
                                            industry, overriden_styling_dict)

    _, _, industry_debt_df = get_industry_wide_financials(id_bb_company, 'analyst_ques_dcs')
    industry_debt_df, overriden_styling_dict = override_company_financials(industry_debt_df,
                                                                           company_name, id_bb_company,
                                                                           industry)
    co_debt_df = filter_company_financials_by_id(industry_debt_df, id_bb_company)
    total_debt_df = company_financials[company_financials.index.get_level_values('formula_metric') == 'IQ_TOTAL_DEBT']
    co_debt_df = pd.concat([co_debt_df, total_debt_df])
    co_debt_df = co_debt_df[sorted(co_debt_df.columns)]

    result_dict.update(get_debt_results_dict(co_debt_df, industry_debt_df, overriden_styling_dict))

    base_value_dict = get_ques_base_value(company_financials, id_bb_company, company_name)

    logging.info(f'Analyst Questionnaire: Debt Issuance Analysis Generated for {company_name}')

    return result_dict, company_name, industry, base_value_dict


if __name__ == '__main__':
    from common.utils.dtutil import today, boyear, dateadd
    from apo_config.config import config

    config.init('dev')

    company_name = "Foot Locker, Inc."  # "Macy's, Inc."
    company_id = 315394
    to_dt = today().date()
    from_dt = dateadd(boyear(to_dt, False), '-5y')
    period_type = 'Quarterly'

    analyst_response = get_debt_issuance_analysis(company_id, from_dt, to_dt, period_type)

    pass

    # get_debt_issuance_analysis(company_id, from_dt, to_dt, period_type)
