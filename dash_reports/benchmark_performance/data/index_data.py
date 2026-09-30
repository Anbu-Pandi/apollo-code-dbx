import logging

import numpy as np
import pandas as pd

from common.utils import sqlutil, dtutil
from data_access.dbtables import JPM_DQ_APP, ICE_INDEX_APP
from data_access.utils import tosqllist


def get_index_desc():
    logging.info(f'getting benchmark index desc')
    sql = '''
    SELECT parent_bi.benchmark_ticker AS main_index_name, bi.benchmark_ticker AS index_name, bi.col_name AS index_desc, bi.root_name AS main_index_desc, bi.order_rank, bi.source
    FROM benchmark_info bi
    JOIN benchmark_info parent_bi ON
    parent_bi.root_name = bi.root_name
    AND parent_bi.order_rank = 0
    ORDER BY bi.order_rank, bi.benchmark_ticker
    '''
    index_desc = sqlutil.readdfsql(sql, app=JPM_DQ_APP).set_index('index_name')
    return index_desc


def get_jpm_data(fromdt, todt):
    logging.info(f'getting benchmark jpm data: {fromdt} and {todt}')
    sql = '''
    SELECT bi.benchmark_ticker AS index_name, asof_date, value, metric
    FROM dbo.apo_jpm_data ad
    JOIN dbo.apo_jpm_dq_query aq ON
    ad.id = aq.id
    JOIN benchmark_info bi ON 
    aq.col_name = bi.col_name
    WHERE 
    metric in ('excess_return', 'spread', 'duration')
    AND aq.group_name = 'APO_CREDIT'
    AND asof_date BETWEEN '{}' AND '{}'

    '''
    df = sqlutil.readdfsql(sql.format(fromdt, todt), app=JPM_DQ_APP)
    dfp = pd.pivot_table(df, values='value', index=['asof_date', 'metric'], columns='index_name').reset_index()

    return dfp


def get_ice_data(fromdt, todt, ice_index_ticker):
    logging.info(f'getting benchmark ice data: {fromdt} and {todt}')
    ice_col_rename = {'effective_duration': 'duration', 'effective_yield': 'yield',
                      'yield_to_worst_conv': 'yield_to_worst',
                      'spread_to_worst': 'spread', 'num_issues': 'num_of_issues', 'trr_pct_1day_loc': 'total_return',
                      'trr_pct_1day_usd_unhedged': 'total_return_unhedged_usd',
                      'excess_return_pct_1day_usd': 'excess_return'}

    def get_ice_index_data(tickers, freq='daily'):
        select_cols = ['asof_date', 'ice_index_ticker', 'num_issues', 'effective_duration',
                       'trr_pct_1day_loc / 100 as trr_pct_1day_loc',
                       'trr_pct_1day_usd_unhedged / 100 as trr_pct_1day_usd_unhedged',
                       'effective_yield / 100 as effective_yield',
                       '(trr_pct_1day_usd_unhedged / 100 - trr_pct_1day_loc / 100) / (1 + trr_pct_1day_loc / 100) as fx_return',
                       'excess_return_pct_1day / 100 as excess_return_pct_1day',
                       'excess_return_pct_1day / 100 + (trr_pct_1day_usd_unhedged / 100 - trr_pct_1day_loc / 100) / (1 + trr_pct_1day_loc / 100) as excess_return_pct_1day_usd',
                       'yield_to_worst_conv / 100 as yield_to_worst_conv', 'spread_to_worst']

        query = f'''
                    select distinct {', '.join(select_cols)}
                    from ice_index_summary
                    where ice_index_ticker in {tosqllist(tickers)}
                    and frequency = '{freq.upper()}'
                    and asof_date between '{fromdt}' and '{todt}'
                '''

        df = sqlutil.readdfsql(query, app=ICE_INDEX_APP)
        df = df.rename(columns=ice_col_rename)
        return df

    ice_df = get_ice_index_data(ice_index_ticker)
    _get_metric_df = lambda x: pd.pivot_table(ice_df, values=x, index='asof_date',
                                              columns='ice_index_ticker') \
        .assign(metric=x).rename(columns={x: 'value'}).reset_index()

    er_dfp = _get_metric_df('excess_return')
    spd_dfp = _get_metric_df('spread')
    dur_dfp = _get_metric_df('duration')

    ice_dfp = pd.concat([er_dfp, spd_dfp, dur_dfp], ignore_index=True)

    return ice_dfp


def get_index_data(todt, index_desc):
    logging.info(f'getting index data for dt: {todt}')
    fromdt = dtutil.dateadd(todt, '-5y')
    df_jpm = get_jpm_data(fromdt, todt)

    ice_tickers = index_desc[index_desc['source'] == 'ice'].index.tolist()
    df_ice = get_ice_data(fromdt, todt, ice_tickers)
    df = pd.merge(df_jpm, df_ice, on=['asof_date', 'metric'], how='outer') \
        .sort_values(['asof_date', 'metric']) \
        .set_index(['asof_date', 'metric'])

    dfs = {}
    for metric, df_metric in df.groupby('metric'):
        df_metric = df_metric.droplevel('metric')
        last_date = df_metric.index.max()
        _agg_func = lambda x: x.dropna().iloc[-1] if len(x.dropna()) > 0 else np.nan
        if metric == 'excess_return':
            _agg_func = lambda x: (1 + x.fillna(0)).prod() - 1
        df_metric = df_metric.resample('W-FRI').apply(_agg_func)

        dts = df_metric.index.tolist()
        dts[-1] = last_date
        df_metric.index = dts
        dfs[metric] = df_metric

    return dfs


def linearmodel(X, Y, add_const=False):
    if add_const:
        X = np.concatenate([np.zeros(X), X], axis=1)
        X = np.vstack([np.ones_like(X), X]).T
    b = np.linalg.pinv(X.T.dot(X)).dot(X.T).dot(Y)
    return b[0]


def vol_beta(X, Y):
    return Y.std() / X.std()


def ref_beta(df, idx_col, ref_idx_cols):
    ds = df[idx_col]
    betas = {}
    for ref_col in ref_idx_cols:
        try:
            Y = ds.values
            X = df.loc[:, [ref_col]].values
            b = vol_beta(X, Y)
            betas[ref_col] = b
        except:
            betas[ref_col] = np.nan

    return betas


def get_beta_dfs(dfp_full, index_desc):
    logging.info(f'getting benchmark beta df')
    todt = dfp_full.index.max()

    def f(x, col, ref_col):
        return ref_beta(x, col, [ref_col])[ref_col]

    beta_pairs = {}
    rolling_beta_pairs = {}
    main_index_cols = [x for x in get_main_indexes(index_desc) if x in dfp_full.columns]
    ref_cols = [x for x in index_desc.index.tolist() if x in dfp_full.columns]
    for col in main_index_cols:
        betas = ref_beta(dfp_full[dtutil.dateadd(todt, '-1y'):], col, ref_cols)
        beta_pairs[col] = betas
        beta_by_ref_col = {}
        for ref_col in ref_cols:
            beta_by_ref_col[ref_col] = dfp_full[ref_col].rolling(window=52) \
                .apply(lambda x: f(dfp_full.loc[x.index, :], col, ref_col)) \
                .rolling(window=78).mean()
            beta_pairs[col].update({ref_col: [beta_by_ref_col[ref_col].iloc[-1]]})
            beta_by_ref_col[ref_col].name = None
        rolling_beta_pairs[col] = beta_by_ref_col

    beta_df = pd.DataFrame.from_dict(beta_pairs)

    beta_ts_dfs = {}
    for col in index_desc.index.tolist():
        for ref_col in ref_cols:
            if col in rolling_beta_pairs and ref_col in rolling_beta_pairs[col]:
                beta_ts_dfs[(col, ref_col)] = pd.DataFrame(rolling_beta_pairs[col][ref_col]) \
                    .rename(columns={0: f'{col}/{ref_col} beta'})

    beta_ts_df = pd.concat(beta_ts_dfs, axis=1)

    return {'df': beta_df, 'ts_df': beta_ts_df}


def get_plot_dfs(dfp_full, fromdt, index_desc, beta_df=None, is_index=True):
    logging.info(f'getting benchmark plot df')
    fromdt = dtutil.todate(fromdt)
    plot_dfs = {}
    main_index_cols = [x for x in get_main_indexes(index_desc) if x in dfp_full.columns]
    ref_cols = [x for x in index_desc.index.tolist() if x in dfp_full.columns]
    for ref_col in ref_cols:
        for col in main_index_cols:
            col_ts = dfp_full.loc[:, col]
            ref_col_ts = dfp_full.loc[:, ref_col]

            beta = 1
            col_desc_suffix = ''
            if beta_df is not None:
                beta = beta_df.loc[ref_col, col]
                col_desc_suffix = f' beta adj({round(beta[0], 2)})'

            col_ts_calc = (ref_col_ts * beta)

            def chart_col(col):
                return index_desc.loc[col, 'index_desc']

            pair_df = pd.DataFrame({chart_col(col): col_ts,
                                    f'{chart_col(ref_col)}{col_desc_suffix}': col_ts_calc})

            pair_df_index = pair_df.loc[fromdt:]
            if is_index:
                pair_df_index = (pair_df_index + 1)
                pair_df_index.iloc[0] = 1
                pair_df_index = pair_df_index.cumprod()

            pair = (col, ref_col)
            plot_dfs[pair] = pair_df_index

    return plot_dfs


def get_beta_chart_df(dfs, beta_ts_dfs, pair):
    logging.info(f'getting benchmark beta chart df')
    if pair in beta_ts_dfs and pair in dfs:
        beta_plot_df = beta_ts_dfs[pair]
        beta_plot_df.columns = ['Beta']
        plot_df = dfs[pair].join(beta_plot_df)
        return plot_df


def get_rel_chart_df(dfs, index_desc, plot_col, plot_ref_cols):
    logging.info(f'getting benchmark relative chart df')
    plot_col_desc = index_desc.loc[plot_col, 'index_desc']
    if len(plot_ref_cols) > 0 and (plot_col, plot_ref_cols[0]) in dfs:
        rel_chart_dfs = dfs[(plot_col, plot_ref_cols[0])]
        for prc in plot_ref_cols[1:]:
            pair = (plot_col, prc)
            if pair in dfs:
                rel_chart_dfs = rel_chart_dfs.join(dfs[pair].drop(columns=[plot_col_desc]))
        return rel_chart_dfs


def get_main_indexes(index_desc):
    return index_desc['main_index_name'].unique().tolist()


def get_sub_indexes(index_desc, main_index_name):
    return index_desc[index_desc['main_index_name'] == main_index_name].index.tolist()


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')
    index_desc = get_index_desc()
    dfs = get_index_data('2022-05-31', index_desc)
    logging.info(dfs)

    beta_df, beta_ts_df = get_beta_dfs(dfs['excess_return'], index_desc)
    logging.info(beta_df)
    logging.info(beta_ts_df)
