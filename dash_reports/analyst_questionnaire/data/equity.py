from dash_reports.analyst_questionnaire.data.utils import *
from dash_reports.analyst_questionnaire.data.utils import get_industry_wide_financials
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX
from dash_reports.analyst_questionnaire.data.utils import get_growth_lines
from data_access.dbtables.factset import estimates
from common.utils import dtutil

FORMULA_METRICS = ('IQ_COMMON_DIV_CF', 'IQ_COMMON_REP', 'IQ_COMMON_ISSUED', 'IQ_NI')

FINANCIAL_ITEMS = {
    'net_equity': {'title': 'Net Equity',
                   'main': ('', 'Net Equity'),
                   'component': [('IQ_COMMON_DIV_CF', 'Common Dividends Paid'),
                                 ('IQ_COMMON_REP', 'Repurchase of Common Stock'),
                                 ('IQ_COMMON_ISSUED', 'Issuance of Common Stock'),
                                 ('IQ_NI', 'Net Income - (IS)')]
                   },
}


def _add_equity_friendly_indicator(df, metric, fig):
    metric = tolist(metric)
    df = df.loc[df.index.get_level_values('metrics').isin(metric)]
    condition = (df > 1).any()
    df = df.loc[:, condition]
    df = df + 0.18

    fig.add_trace(
        go.Scatter(x=df.iloc[0].index.values.tolist(), y=df.iloc[0].values.tolist(),
                   name='Equity Friendly Qtr',
                   mode='markers',
                   marker=dict(color='green', symbol='triangle-up', size=10),
                   showlegend=True,
                   hoverinfo='skip'))

    return fig


def get_analysis_results_dict(company_df, industry_df, company_name, industry, tbl_styling_dict):
    if company_df.empty:
        result_dict = {'company_stats': None,
                       'industry_stats': None,
                       'fig': None}
        return result_dict
    else:
        result_dict = {}

    for key, metric in FINANCIAL_ITEMS.items():
        temp_company_df, temp_industry_df, main_metric = filter_financials_using_metric_config(company_df, industry_df,
                                                                                               metric)
        edit_table_flag = True
        fig = create_metric_percentile_chart(temp_company_df, temp_industry_df, company_name, main_metric, industry)
        fig = _add_equity_friendly_indicator(temp_company_df, main_metric, fig)

        result_dict[metric['title']] = {'company_stats': {'df': temp_company_df.reset_index(),
                                                          'dash_props': {'editable': {'editable': edit_table_flag,
                                                                                      'non_editable_cols': NON_EDITABLE_COLS},
                                                                         'conditional_formatting':
                                                                             {
                                                                                 'cell_highlight': tbl_styling_dict
                                                                             },
                                                                         'fixed_columns': 3,
                                                                         },
                                                          },
                                        'industry_stats': temp_industry_df.reset_index(),
                                        'fig': fig}

        del temp_company_df, temp_industry_df

    return result_dict


def _compute_equity_issuance_ratio(df):
    logging.info('Analyst Questionnaire: Computing Net Equity Ratio')
    if len(df) == 0:
        return pd.DataFrame()

    df = df.fillna(0)
    id_metrics_df = df[['formula_metric', 'metrics']].drop_duplicates()
    id_metrics_map = id_metrics_df.drop_duplicates(subset=['formula_metric']) \
        .set_index('formula_metric')[['metrics']].to_dict()['metrics']
    df_pivot = df.pivot_table(index=['company_id', 'company_name', 'industry', 'qtr', 'period_end_date'],
                              columns=['metrics'], values=['value'],
                              aggfunc={'value': 'sum'})
    df_pivot.columns = df_pivot.columns.droplevel(0)
    df_pivot = df_pivot.fillna(0)
    df_pivot['temp_col'] = 0
    df_pivot['Net Equity'] = (df_pivot[id_metrics_map.get('IQ_COMMON_DIV_CF', 'temp_col')].mul(-1) +
                              df_pivot[id_metrics_map.get('IQ_COMMON_REP', 'temp_col')].mul(-1) +
                              df_pivot[id_metrics_map.get('IQ_COMMON_ISSUED', 'temp_col')].mul(-1)) / \
                             df_pivot[id_metrics_map.get('IQ_NI', 'temp_col')]
    df_pivot.drop(columns=['temp_col'], inplace=True)
    df_pivot = df_pivot.stack('metrics')
    df_pivot.name = 'value'

    df_pivot = pd.merge(df_pivot.reset_index(), id_metrics_df, on='metrics', how='left')
    df_pivot['formula_metric'] = df_pivot['formula_metric'].fillna('')
    return df_pivot


def _get_net_equity_stats(industry_metrics_df, company_metrics_df, company_name, industry, tbl_styling_dict):
    result_dict = get_analysis_results_dict(company_metrics_df, industry_metrics_df, company_name,
                                            industry, tbl_styling_dict)

    result_dict['Dividends'] = {'company_stats': {'df': None},
                                'industry_stats': None,
                                'fig': get_dividends_fig(company_metrics_df)}

    return result_dict


def get_dividends_fig(df):
    if df.empty:
        return None

    df = df[df.index.get_level_values('formula_metric') == 'IQ_COMMON_DIV_CF'].stack('qtr')

    if df.empty:
        return None

    df = df.to_frame(name='dividend').abs()
    df.sort_index(level='qtr', inplace=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    x_axis = df.index.get_level_values('qtr').values.tolist()

    fig.add_trace(go.Bar(name='Dividend', x=x_axis, y=df['dividend'].values.tolist(), showlegend=True))

    if len(df) > 0:
        rets = get_growth_lines(df['dividend'])

        for k, v in rets.items():
            fig.add_trace(go.Line(name=k, x=x_axis, y=v.values.tolist()), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      xaxis=dict(tickangle=270, dtick=1, tick0=2, showline=True, linewidth=1, linecolor='black',
                                 zeroline=True, zerolinewidth=2, zerolinecolor='LightPink'),
                      yaxis=dict(showgrid=True, mirror=True, showline=True, linewidth=1, linecolor='black'),
                      bargap=0.3,
                      bargroupgap=0.1
                      )

    return fig


def get_outstanding_shares_fig(df):
    if df.empty:
        return None
    df = df.set_index(['company_name', 'industry', 'qtr'])
    df.sort_index(level='qtr', inplace=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    x_axis = df.index.get_level_values('qtr').values.tolist()

    fig.add_trace(go.Bar(name='Outstanding Shares', x=x_axis, y=df['shares_outstanding'].values.tolist(),
                         showlegend=True))

    if len(df) > 0:
        rets = get_growth_lines(df['shares_outstanding'])

        for k, v in rets.items():
            fig.add_trace(go.Line(name=k, x=x_axis, y=v.values.tolist()), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1500,
                      height=500,
                      xaxis=dict(tickangle=270, dtick=1, tick0=2, showline=True, linewidth=1, linecolor='black',
                                 zeroline=True, zerolinewidth=2, zerolinecolor='LightPink'),
                      yaxis=dict(showgrid=True, mirror=True, showline=True, linewidth=1, linecolor='black'),
                      bargap=0.3,
                      bargroupgap=0.1
                      )

    return fig


def _get_outstanding_shares(df):
    if df.empty:
        return None
    filter_cols = ['company_name', 'industry_level_4', 'qtr', 'sharesOutstanding']

    df = df.loc[:, filter_cols]
    df = df.rename(columns={'industry_level_4': 'industry', 'sharesOutstanding': 'shares_outstanding'})
    fig = get_outstanding_shares_fig(df)

    df['year'], df['qtr'] = list(zip(*df['qtr'].apply(split_qtr_year)))

    df = df.pivot_table(index=['company_name', 'industry', 'year'],
                        columns=['qtr'], values=['shares_outstanding'],
                        aggfunc={'shares_outstanding': 'sum'}).reset_index()
    df.columns = [x[1] if x[1] != '' else x[0] for x in df.columns]
    result_dict = {'company_stats': {'df': df,
                                     'dash_props': {'editable': {'editable': False,
                                                                 'non_editable_cols': ['company_name', 'industry',
                                                                                       'metrics']}}
                                     },
                   'industry_stats': None,
                   'fig': fig}

    return result_dict


def get_forecasts(fin_company_ids, dataitem, tenor):
    df = estimates.get_estimates(dtutil.dateadd(dtutil.today(), '-3m'), dtutil.dateadd(dtutil.today(), tenor),
                                 dataitem, entity_ids=fin_company_ids, rel_per=[1, 2])
    df = df.sort_values(['FACTSET_ENTITY_ID', 'FE_ITEM', 'FE_FP_END', 'CONS_START_DATE']) \
        .drop_duplicates(subset=['FACTSET_ENTITY_ID', 'FE_ITEM', 'FE_FP_END'], keep='last')
    dps_forecast = df.groupby(['FACTSET_ENTITY_ID', 'FE_ITEM'])['FE_MEAN'].sum()

    return dps_forecast


def get_ques_base_value(company_financials_df, pricing_df, fin_company_id, company_name):
    data_item = ['IQ_COMMON_DIV_CF', 'sharesOutstanding']

    logging.info(f'Get questions base value: company id: {fin_company_id} Company Name: {company_name} '
                 f'data_item: {data_item}')

    base_value_dict = {}

    for item in data_item:
        absolute_base_value = np.nan
        pct_change_value = np.nan
        if item == 'IQ_COMMON_DIV_CF':
            data_item_df = pd.DataFrame()
            if len(company_financials_df) > 0:
                data_item_df = company_financials_df[
                    company_financials_df.index.get_level_values('formula_metric') == item]
            if not data_item_df.empty:
                absolute_base_value = data_item_df.iloc[:, -1].iloc[-1]
                absolute_base_value = absolute_base_value / 2
                pct_change_data_item_df = data_item_df.stack().sort_index(level='qtr', ascending=True).rolling(
                    window=3).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1) * 100
                pct_change_value = round(pct_change_data_item_df.iloc[-1], 2)
                nominal_change = round(absolute_base_value * pct_change_value * 0.01, 2)
        elif item == 'sharesOutstanding':
            if not pricing_df.empty:
                shares_df = pricing_df[['qtr', item]].set_index('qtr')
                absolute_base_value = round(shares_df.loc[shares_df.index.max()][item], 0)
                pct_change_shares_df = shares_df.sort_index(ascending=True).rolling(window=3).apply(
                    lambda x: x.iloc[-1] / x.iloc[0] - 1) * 100
                pct_change_value = round(pct_change_shares_df.iloc[-1]['sharesOutstanding'], 2)
                nominal_change = round(absolute_base_value * pct_change_value * 0.01, 2)

        base_value_dict[item] = {'absolute': absolute_base_value, 'pct_change': pct_change_value,
                                 'nominal_change': nominal_change}

    curr_div, curr_shares = np.nan, np.nan
    if 'IQ_COMMON_DIV_CF' in base_value_dict:
        curr_div = base_value_dict['IQ_COMMON_DIV_CF']['absolute']
    if 'sharesOutstanding' in base_value_dict:
        curr_shares = base_value_dict['sharesOutstanding']['absolute']
    dps_forecast = get_forecasts(fin_company_id, 'DPS', '6m')
    div_forecast = dps_forecast.iloc[0] * curr_shares if len(dps_forecast) > 0 else np.nan

    base_value_dict['DIVIDEND_FORECAST'] = {'absolute': round(div_forecast, 2),
                                            'pct_change': round(100 * (div_forecast / curr_div - 1), 2)}

    return base_value_dict


def get_equity_issuance_analysis(id_bb_company, from_date, to_date, period_type):
    logging.info(f'Analyst Questionnaire: Generating Equity Issuance Analysis for {id_bb_company}')

    issuer_df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', id_bb_company)
    fin_company_id = issuer_df['factset_entity_id'].iloc[0]

    company_name, industry, financials_df, = get_industry_wide_financials(id_bb_company, 'analyst_ques_equity')

    updated_financials_df, tbl_styling_dict = override_company_financials(financials_df, company_name, id_bb_company,
                                                                          industry)
    financials_df_net_equity = _compute_equity_issuance_ratio(updated_financials_df)
    company_metrics_df = filter_company_financials_by_id(financials_df_net_equity, id_bb_company)
    industry_metrics_df = get_industry_percentiles(financials_df_net_equity)

    pricing_df = default_es.search_exact('analyst_ques_acquisition', 'company_id', fin_company_id)
    pricing_df = pricing_df.drop_duplicates(subset=['company_id', 'qtr'])
    result_dict = _get_net_equity_stats(industry_metrics_df, company_metrics_df, company_name, industry,
                                        tbl_styling_dict)

    result_dict['Outstanding Shares'] = _get_outstanding_shares(pricing_df)

    base_value_dict = get_ques_base_value(company_metrics_df, pricing_df, fin_company_id, company_name)

    logging.info(f'Analyst Questionnaire: Equity Issuance Analysis Generated for {company_name}')

    return result_dict, company_name, industry, base_value_dict


if __name__ == '__main__':
    from common.utils.dtutil import today, boyear, dateadd
    from apo_config.config import config

    config.init('dev')

    company_name = 'Foot Locker, Inc.'  # "Macy's, Inc."
    company_id = 315394
    to_dt = today().date()
    from_dt = dateadd(boyear(to_dt, False), '-5y')
    period_type = 'LTM'

    get_equity_issuance_analysis(company_id, from_dt, to_dt, period_type)
