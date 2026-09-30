from datetime import date

from apo_config.config import config
from dash_reports.etf_apo_funds_report.data.utils import *
from common.utils.dtutil import dateadd
from dash_reports.etf_apo_funds_report.pages.components import etf_funds, ice_funds

key_to_grp_by_col_map = {'overlap-by-issuer': 'SnP_company_name',
                         'overlap-by-rating': ['SnP_rating', 'APO_risk_direction', 'SnP_company_name', 'APO_security'],
                         'overlap-by-industry': ['SnP_industry', 'APO_risk_direction', 'SnP_company_name',
                                                 'APO_security']}


def combine_benchmark_apo_dataset(benchmark_df, apo_df, benchmark_identifiers, apo_identifiers):
    benchmark_apo_df = join_benchmark_apo_df(benchmark_df, apo_df, benchmark_identifiers, apo_identifiers)
    return benchmark_apo_df


def combine_ice_apo_snp_dataset(benchmark_df, apo_df, benchmark_identifiers, apo_identifiers):
    ice_apo_df = join_benchmark_apo_df(benchmark_df, apo_df, benchmark_identifiers, apo_identifiers)

    identifiers = list(set(list(ice_apo_df['isin'].values) + list(ice_apo_df['cusip'].values)))

    snp_df = get_snp_meta_data(identifiers)

    ice_apo_snp_df = join_other_dataset(ice_apo_df, snp_df, {'identifier_1': 'cusip', 'identifier_2': 'isin'},
                                        {'identifier_1': 'SnP_cusip', 'identifier_2': 'SnP_isin'})

    return ice_apo_snp_df


def combine_all_dataset(etf, apo):
    etf_apo_df = combine_benchmark_apo_dataset(etf, apo, {'identifier_1': 'ETF_cusip', 'identifier_2': 'ETF_isin'},
                                               {'identifier_1': 'APO_cusip', 'identifier_2': 'APO_isin'})

    identifiers = list(set(list(etf_apo_df['isin'].values) + list(etf_apo_df['cusip'].values)))

    snp_df = get_snp_meta_data(identifiers)

    etf_apo_snp_df = join_other_dataset(etf_apo_df, snp_df, {'identifier_1': 'cusip', 'identifier_2': 'isin'},
                                        {'identifier_1': 'SnP_cusip', 'identifier_2': 'SnP_isin'})

    return etf_apo_snp_df


def get_data(dt, bmk_fund, apo_fund, lookback, ice_or_etf='etf'):
    lookback_dt = dateadd(dt, lookback)
    apo = get_apo_risk_data(lookback_dt, dt, apo_fund)

    if ice_or_etf == 'etf':
        etf = get_etf_data(lookback_dt, dt, bmk_fund)
        return etf, apo

    elif ice_or_etf == 'ice':
        ice_data = get_ice_data(lookback_dt, dt, bmk_fund)
        return apo, ice_data


def get_overlap_by(df, pivot_index, pivot_values, pivot_agg_func):
    overlap_df = df.pivot_table(index=pivot_index, values=pivot_values, aggfunc=pivot_agg_func)
    overlap_df.loc['Total', :] = overlap_df.sum(numeric_only=True).values
    # overlap_df.loc['Total', :] = overlap_df.loc['Total'].replace(np.nan, '')
    return overlap_df.reset_index()


def _check_instance_list(val):
    if not isinstance(val, list):
        val = [val]
    return val


def get_issuer_overlap(df, apo_fund, grp_by_col, etf_or_ice):
    pivot_params_dict = get_pivot_params(apo_fund, etf_or_ice)

    overlap_df = get_overlap_by(df, grp_by_col, pivot_params_dict['pivot_values'],
                                pivot_params_dict['pivot_aggfunc'])
    overlap_df = overlap_df.sort_values('Diff_%_NAV_Net', ascending=False)

    return overlap_df


def get_etf_fund_comparison(dt, etf_df, apo_df, etf_fund, apo_fund, key):
    result_dict = {}

    logging.info(f'Generating analysis for {apo_fund} vs {etf_fund} for date: {dt}')

    apo = apo_df[apo_df['APO_asof_date'].isin([dt]) & apo_df['APO_fund_entity_code'].isin([apo_fund])]
    etf = etf_df[etf_df['ETF_asof_date'].isin([dt]) & etf_df['ETF_composite_ticker'].isin([etf_fund])]

    if etf.empty or apo.empty:
        logging.info(f'Issue: {etf_fund} or {apo_fund} data for date {dt} not available.')
        result_dict[f'{etf_fund}_{apo_fund}'] = {
            'issue': f'{etf_fund} or {apo_fund} data for date {dt} not available.'}
        return result_dict

    df = combine_all_dataset(etf, apo)
    df['Diff_%_NAV_Net'] = df['APO_%_NAV_net'].sub(df['ETF_%_NAV'])

    df.loc[(df['is_apo'] == 1) & (df['is_etf'] == 1), 'flag'] = apo_fund + '_' + etf_fund
    df.loc[(pd.isna(df['is_apo']) & (df['is_etf'] == 1)), 'flag'] = etf_fund
    df.loc[(df['is_apo'] == 1) & (pd.isna(df['is_etf'])), 'flag'] = apo_fund

    # common_df = df.loc[(df['is_apo'] == 1) & (df['is_etf'] == 1)]
    #
    # if common_df.empty:
    #     logging.info(f'Issue: No Common Data Found between {etf_fund} and {apo_fund} for date {dt}.')
    #     result_dict[f'{etf_fund}_{apo_fund}'] = {
    #         'issue': f'No Common Data Found between {etf_fund} and {apo_fund} for date {dt}.'}
    #     return result_dict

    if key != 'overlap-by-issuer':
        overlap_result_dict = get_industry_rating_overlap(df, apo_fund, etf_fund, key, 'etf')
        result_dict[f'{etf_fund}_{apo_fund}'] = {key: overlap_result_dict}
    else:
        grp_keys = [key_to_grp_by_col_map[key], 'flag']  # 'is_same_direction',
        overlap_df = get_issuer_overlap(df, apo_fund, grp_keys, 'etf')
        result_dict[f'{etf_fund}_{apo_fund}'] = {key: overlap_df}

    return result_dict


def get_distribution_charts(apo_distribution_df, benchmark_distribution_df, long_intersection_chart_df,
                            short_intersection_chart_df, apo_fund, benchmark_fund, x_axis_col, bnchmrk_nav_col, title,
                            key=None):
    if key:
        apo_distribution_df = apo_distribution_df.drop(
            columns=[('APO_%_NAV_gross', 'Long'), ('APO_%_NAV_gross', 'Short')])
    apo_distribution_df.columns = apo_distribution_df.columns.droplevel(0)
    apo_distribution_df = apo_distribution_df.rename(columns={'': x_axis_col})

    apo_fig = get_histogram(apo_distribution_df, x_axis_col, ['Long', 'Short'], title=apo_fund + title,
                            xaxis_title=x_axis_col, yaxis_title='% NAV', bar_mode='relative')

    apo_fig.add_scatter(x=apo_distribution_df[x_axis_col], y=apo_distribution_df['Net'], mode='markers', name='Net',
                        marker=dict(
                            color='Black',
                            size=8,
                            line=dict(
                                color='MediumPurple',
                                width=1
                            )
                        ))

    bnchmrk_fig = get_histogram(benchmark_distribution_df, x_axis_col, bnchmrk_nav_col, title=benchmark_fund + title,
                                xaxis_title=x_axis_col, yaxis_title='% NAV', )

    long_intersection_fig = get_histogram(long_intersection_chart_df, x_axis_col,
                                          ['APO_%_NAV_net', bnchmrk_nav_col],
                                          title=benchmark_fund + ' vs ' + apo_fund + ' Overlap - Long Position',
                                          xaxis_title=x_axis_col, yaxis_title='% NAV')

    short_intersection_fig = get_histogram(short_intersection_chart_df, x_axis_col,
                                           ['APO_%_NAV_net', bnchmrk_nav_col],
                                           title=benchmark_fund + ' vs ' + apo_fund + ' Overlap - Short Position',
                                           xaxis_title=x_axis_col, yaxis_title='% NAV')

    return apo_fig, bnchmrk_fig, long_intersection_fig, short_intersection_fig


def get_pivot_params(apo_fund, etf_or_ice):
    if etf_or_ice == 'etf':
        bnchmrk_nav_col = 'ETF_%_NAV'
    elif etf_or_ice == 'ice':
        bnchmrk_nav_col = 'ICE_percent_mkt_value'

    if apo_fund == 'CSTRAT':
        values = ['APO_quantity', 'APO_mv_usd', 'APO_%_NAV_gross', 'APO_%_NAV_net', bnchmrk_nav_col, 'Diff_%_NAV_Net']
        overlap_agg_func = {'APO_quantity': 'sum', 'APO_%_NAV_gross': 'sum', 'APO_%_NAV_net': 'sum',
                            bnchmrk_nav_col: 'sum',
                            'APO_mv_usd': "sum", 'Diff_%_NAV_Net': 'sum'}
    else:
        values = ['APO_quantity', 'APO_mv_usd', 'APO_%_NAV_net', bnchmrk_nav_col, 'Diff_%_NAV_Net']
        overlap_agg_func = {'APO_%_NAV_net': 'sum', bnchmrk_nav_col: 'sum', 'APO_mv_usd': "sum", 'APO_quantity': 'sum',
                            'Diff_%_NAV_Net': 'sum'}

    pivot_params_dict = {'pivot_values': values, 'pivot_aggfunc': overlap_agg_func}

    return pivot_params_dict


def get_industry_rating_overlap(df, apo_fund, benchmark_fund, key, etf_or_ice):
    if etf_or_ice == 'etf':
        bnchmrk_nav_col = 'ETF_%_NAV'
    elif etf_or_ice == 'ice':
        bnchmrk_nav_col = 'ICE_percent_mkt_value'

    df['rating_order'] = df['SnP_rating'].apply(lambda x: rating_order[x] if x in rating_order else 99)
    intersection_df = df.loc[(df['is_apo'] == 1) & (df['is_' + etf_or_ice] == 1)]

    if key == 'overlap-by-rating':
        pivot_index = key_to_grp_by_col_map[key] + ['rating_order']
        distribution_pivot_index = ['SnP_rating', 'rating_order']
        chart_x_axis_col = 'SnP_rating'
        chart_title = ' Composition by Rating'
        sort_col = 'rating_order'

    else:
        pivot_index = key_to_grp_by_col_map[key]
        distribution_pivot_index = ['SnP_industry']
        chart_x_axis_col = 'SnP_industry'
        chart_title = ' Composition by Industry'
        sort_col = 'SnP_industry'

    benchmark_distribution_df = df.pivot_table(index=distribution_pivot_index,
                                               values=[bnchmrk_nav_col],
                                               aggfunc='sum').reset_index().sort_values(sort_col)

    apo_distribution_df = df.pivot_table(index=distribution_pivot_index,
                                         columns=['APO_risk_direction'],
                                         values=['APO_%_NAV_net', 'APO_%_NAV_gross'],
                                         aggfunc='sum').reset_index().sort_values(sort_col).fillna(0)

    apo_distribution_df[('', 'Net')] = apo_distribution_df[('APO_%_NAV_net', 'Long')] + apo_distribution_df[
        ('APO_%_NAV_net', 'Short')]

    pivot_params_dict = get_pivot_params(apo_fund, etf_or_ice)

    intersection_chart_df = intersection_df.pivot_table(index=pivot_index, values=pivot_params_dict['pivot_values'],
                                                        aggfunc=pivot_params_dict[
                                                            'pivot_aggfunc']).reset_index().sort_values(sort_col)

    long_intersection_chart_df = intersection_chart_df.loc[intersection_chart_df['APO_risk_direction'].isin(['Long'])]
    short_intersection_chart_df = intersection_chart_df.loc[intersection_chart_df['APO_risk_direction'].isin(['Short'])]

    if key == 'overlap-by-rating':
        apo_distribution_df = apo_distribution_df.drop(('rating_order', ''), axis=1)
        long_intersection_chart_df = long_intersection_chart_df.drop('rating_order', axis=1)
        short_intersection_chart_df = short_intersection_chart_df.drop('rating_order', axis=1)

    apo_fig, bnchmrk_fig, long_intersection_fig, short_intersection_fig = get_distribution_charts(apo_distribution_df,
                                                                                                  benchmark_distribution_df,
                                                                                                  long_intersection_chart_df,
                                                                                                  short_intersection_chart_df,
                                                                                                  apo_fund,
                                                                                                  benchmark_fund,
                                                                                                  chart_x_axis_col,
                                                                                                  bnchmrk_nav_col,
                                                                                                  chart_title,
                                                                                                  key='overlap-by-rating')

    long_intersection_chart_df = long_intersection_chart_df.sort_values('Diff_%_NAV_Net', ascending=False)
    short_intersection_chart_df = short_intersection_chart_df.sort_values('Diff_%_NAV_Net', ascending=True)

    result_dict = {'long_overlap_df': long_intersection_chart_df, 'short_overlap_df': short_intersection_chart_df,
                   'apo_fig': apo_fig,
                   'bnchmrk_fig': bnchmrk_fig, 'long_intersection_fig': long_intersection_fig,
                   'short_intersection_fig': short_intersection_fig}

    return result_dict


def get_etf_ts_overlap_count(etf_df, apo, etf_fund, apo_fund):
    result_dict = {}

    logging.info(f'Generating ts analysis for {apo_fund} vs {etf_fund}')
    overlap_count_dict = {}

    apo = apo[apo['APO_fund_entity_code'] == apo_fund]
    etf = etf_df[etf_df['ETF_composite_ticker'] == etf_fund]

    grp_by = etf.groupby('ETF_asof_date')
    for dt, etf_df in grp_by:
        apo_df = apo[apo['APO_asof_date'] == dt]

        if etf_df.empty or apo_df.empty:
            overlap_count_dict[dt] = {'APO % Gross NAV': np.nan, 'ETF % Gross NAV': np.nan, 'APO % Net NAV': np.nan}
            continue

        etf_apo_df = combine_benchmark_apo_dataset(etf_df, apo_df,
                                                   {'identifier_1': 'ETF_cusip', 'identifier_2': 'ETF_isin'},
                                                   {'identifier_1': 'APO_cusip', 'identifier_2': 'APO_isin'})
        common_df = etf_apo_df.loc[(etf_apo_df['is_apo'] == 1) & (etf_apo_df['is_etf'] == 1)]

        if common_df.empty:
            overlap_count_dict[dt.date()] = {'APO % Gross NAV': 0, 'ETF % Gross NAV': 0, 'APO % Net NAV': 0}
            continue

        overlap_count_dict[dt] = {'APO % Gross NAV': common_df['APO_%_NAV_gross'].sum(),
                                  'ETF % Gross NAV': abs(common_df['ETF_%_NAV']).sum(),
                                  'APO % Net NAV': common_df['APO_%_NAV_net'].sum()}

        del common_df, etf_apo_df, apo_df

    df = pd.DataFrame.from_dict(overlap_count_dict, orient='index')
    df.index.name = 'Date'
    fig = get_ts_chart(df)

    result_dict[f'{etf_fund}_{apo_fund}_chart'] = fig
    return result_dict


def get_ice_ts_overlap_count(ice_df, apo, ice_fund, apo_fund):
    result_dict = {}

    logging.info(f'Generating ts analysis for {apo_fund} vs {ice_fund}')
    overlap_count_dict = {}

    apo = apo[apo['APO_fund_entity_code'] == apo_fund]
    ice = ice_df[ice_df['ICE_index_ticker'] == ice_fund]

    grp_by = ice.groupby('ICE_asof_date')
    for dt, ice_df in grp_by:
        apo_df = apo[apo['APO_asof_date'] == dt]

        if ice_df.empty or apo_df.empty:
            overlap_count_dict[dt] = {'APO % Gross NAV': np.nan, 'ICE % Gross NAV': np.nan, 'APO % Net NAV': np.nan}
            continue

        ice_apo_df = combine_benchmark_apo_dataset(ice_df, apo_df,
                                                   {'identifier_1': 'ICE_cusip', 'identifier_2': 'ICE_isin_number'},
                                                   {'identifier_1': 'APO_cusip', 'identifier_2': 'APO_isin'})
        common_df = ice_apo_df.loc[(ice_apo_df['is_apo'] == 1) & (ice_apo_df['is_ice'] == 1)]

        if common_df.empty:
            overlap_count_dict[dt.date()] = {'APO % Gross NAV': 0, 'ICE % Gross NAV': 0, 'APO % Net NAV': 0}
            continue

        overlap_count_dict[dt] = {'APO % Gross NAV': common_df['APO_%_NAV_gross'].sum(),
                                  'ICE % Gross NAV': abs(common_df['ICE_percent_mkt_value']).sum(),
                                  'APO % Net NAV': common_df['APO_%_NAV_net'].sum()}

        del common_df, ice_apo_df, apo_df

    df = pd.DataFrame.from_dict(overlap_count_dict, orient='index')
    df.index.name = 'Date'
    fig = get_ts_chart(df)

    result_dict[f'{ice_fund}_{apo_fund}_chart'] = fig
    return result_dict


def get_ice_apo_comparison(dt, ice_df, apo_df, ice_fund, apo_fund, key):
    result_dict = {}

    apo = apo_df[apo_df['APO_asof_date'].isin([dt]) & apo_df['APO_fund_entity_code'].isin([apo_fund])]
    ice = ice_df[ice_df['ICE_asof_date'].isin([dt]) & ice_df['ICE_index_ticker'].isin([ice_fund])]

    logging.info(f'Generating analysis for {apo_fund} vs {ice_fund} for date: {dt}')

    if ice.empty or apo.empty:
        logging.info(f'Issue: {ice_fund} or {apo_fund} data for date {dt} not available.')
        result_dict[f'{ice_fund}_{apo_fund}'] = {
            'issue': f'{ice_fund} or {apo_fund} data for date {dt} not available.'}
        return result_dict

    ice_apo_df = combine_ice_apo_snp_dataset(ice, apo,
                                             {'identifier_1': 'ICE_cusip', 'identifier_2': 'ICE_isin_number'},
                                             {'identifier_1': 'APO_cusip', 'identifier_2': 'APO_isin'})

    ice_apo_df['Diff_%_NAV_Net'] = ice_apo_df['APO_%_NAV_net'].sub(ice_apo_df['ICE_percent_mkt_value'])

    ice_apo_df.loc[(ice_apo_df['is_apo'] == 1) & (ice_apo_df['is_ice'] == 1), 'flag'] = apo_fund + '_' + ice_fund
    ice_apo_df.loc[(pd.isna(ice_apo_df['is_apo']) & (ice_apo_df['is_ice'] == 1)), 'flag'] = ice_fund
    ice_apo_df.loc[(ice_apo_df['is_apo'] == 1) & (pd.isna(ice_apo_df['is_ice'])), 'flag'] = apo_fund

    # common_df = ice_apo_df.loc[(ice_apo_df['is_apo'] == 1) & (ice_apo_df['is_ice'] == 1)]
    #
    # if common_df.empty:
    #     logging.info(f'Issue: No Common Data Found between {ice_fund} and {apo_fund} for date {dt}.')
    #     result_dict[f'{ice_fund}_{apo_fund}'] = {
    #         'issue': f'No Common Data Found between {ice_fund} and {apo_fund} for date {dt}.'}
    #     return result_dict

    if key != 'overlap-by-issuer':
        overlap_result_dict = get_industry_rating_overlap(ice_apo_df, apo_fund, ice_fund, key, 'ice')
        result_dict[f'{ice_fund}_{apo_fund}'] = {key: overlap_result_dict}
    else:
        grp_keys = [key_to_grp_by_col_map[key], 'flag']  # 'is_same_direction',
        overlap_df = get_issuer_overlap(ice_apo_df, apo_fund, grp_keys, 'ice')
        result_dict[f'{ice_fund}_{apo_fund}'] = {key: overlap_df}

    return result_dict


def main(dt, benchmark_fund, apo_funds, key='overlap-by-issuer'):
    apo_funds = _check_instance_list(apo_funds)

    result_dict = {}

    if benchmark_fund in etf_funds:
        etf, apo = get_data(dt, benchmark_fund, apo_funds, lookback='-1y', ice_or_etf='etf')

        etf_funds_list = _check_instance_list(benchmark_fund)
        for etf_fund in etf_funds_list:
            for apo_fund in apo_funds:
                comparison_dict = get_etf_fund_comparison(dt, etf, apo, etf_fund, apo_fund, key)

                if key == 'overlap-by-issuer':
                    ts_overlap_count_dict = get_etf_ts_overlap_count(etf, apo, etf_fund, apo_fund)
                    result_dict.update(ts_overlap_count_dict)

                result_dict.update(comparison_dict)

    elif benchmark_fund in ice_funds:
        apo, ice_data = get_data(dt, benchmark_fund, apo_funds, lookback='-1y', ice_or_etf='ice')
        ice_funds_list = _check_instance_list(benchmark_fund)
        for ice_fund in ice_funds_list:
            for apo_fund in apo_funds:
                comparison_dict = get_ice_apo_comparison(dt, ice_data, apo, ice_fund, apo_fund, key)

                if key == 'overlap-by-issuer':
                    ts_overlap_count_dict = get_ice_ts_overlap_count(ice_data, apo, ice_fund, apo_fund)
                    result_dict.update(ts_overlap_count_dict)

                result_dict.update(comparison_dict)

    return result_dict


if __name__ == '__main__':
    config.init('prod')

    dt = date(2022, 2, 2)
    fund = ['CSTRAT']
    benchmark = 'HYG'
    path = "Z:\\Hedge Fund\\MicroStrategy Exports\\Daily\\PnL_Daily_lite_" + dt.strftime(
        '%Y') + dt.strftime('%m') + dt.strftime('%d') + ".csv"

    key = 'overlap-by-rating'  # ['overlap-by-issuer', 'overlap-by-rating', 'overlap-by-industry']

    result = main(dt, benchmark, fund, key)

    pass
