from data_access.dbtables.flow_data.trace import get_trace_trades_summary
from data_access.dbtables.index_data.ice_index import get_ice_index_summary_data
from collections import OrderedDict
import common.utils.dtutil as dtutil
import pandas as pd

spread_bins_labels = ['(0, 75]', '(75, 150]', '(150, 250]', '(250, 500]', '(500, 800]', '(800, 1200]', '1200+']

duration_buckets = [0, 3.5, 7, 10, 15, 20]
duration_bins_labels = ['(0-3.5]', '(3.5,7]', '(7,10]', '(10, 15]', '15+']

rating_label = ['Aaa', 'Aa1', 'Aa2', 'Aa3', 'A1', 'A2', 'A3', 'Baa1', 'Baa2', 'Baa3', 'Ba1', 'Ba2', 'Ba3', 'B1',
                'B2', 'B3', 'Caa1', 'Caa2', 'Caa3', 'Ca', 'C']

rating_group = {'Aaa': 'AAA', 'Aa1': 'AA', 'Aa2': 'AA', 'Aa3': 'AA', 'A1': 'A', 'A2': 'A', 'A3': 'A', 'Baa1': 'BAA',
                'Baa2': 'BAA', 'Baa3': 'BAA', 'Ba1': 'BA', 'Ba2': 'BA', 'Ba3': 'BA', 'B1': 'B',
                'B2': 'B', 'B3': 'B', 'extra': 'CCC and Below'}


def add_spd_dur_bins(df):
    min_spread_bin_value = int(df['apo_oas'].min() - 100)
    max_spread_bin_value = int(df['apo_oas'].max() - 100)

    spread_custom_bins = [0, 75, 150, 250, 500, 800, 1200, max_spread_bin_value]

    df['spread_bins'] = pd.cut(df['apo_oas'].abs(), spread_custom_bins, labels=spread_bins_labels)
    df['duration_bins'] = pd.cut(df['apo_spd_dur'], duration_buckets, labels=duration_bins_labels)

    df['spread_bins'] = df['spread_bins'].astype(str)
    df['duration_bins'] = df['duration_bins'].astype(str)

    return df


def get_trade_summary(start_date, end_date, is_portfolio_trade=True):
    df = get_trace_trades_summary(start_date, end_date, is_portfolio_trade=is_portfolio_trade)
    df['effective_date'] = pd.to_datetime(df['effective_date']).dt.date
    df = add_spd_dur_bins(df)
    return df


def get_ice_index_summary(start_date, end_date, ticker=['H0A0', 'C0A0']):
    df = get_ice_index_summary_data(ticker, start_date, end_date)
    risk_stats_df = df[['asof_date', 'ice_index_ticker', 'oas', 'yield_to_maturity_sa']]
    return risk_stats_df


col_rename_dict = {'spread_bins': 'spread', 'duration_bins': 'duration', 'apo_dv01': 'DV01', 'dirty_mv': 'MV',
                   'apo_tcost': 'Transaction Cost'}


def add_total_col_pivot(df):
    df['Total'] = df.sum(axis=1)

    col_total = df.sum(axis=0)
    col_total = col_total.to_frame('Total').T

    df = pd.concat([df, col_total], sort=False)
    return df


def get_pivot_by_spd_dur(df, agg_col, scale=1e6, add_total=True):
    df = df.rename(columns=col_rename_dict)
    metric = col_rename_dict.get(agg_col, agg_col)

    df_aggregated = df.dropna(subset=['spread', 'duration'],
                              how='any').groupby(['spread', 'duration'],
                                                 dropna=False, as_index=False).agg({metric: 'sum'})

    df_aggregated[metric] = df_aggregated[metric].fillna(0).div(scale)

    df_pivot = df_aggregated.pivot(index='spread', columns='duration',
                                   values=metric).reindex(index=spread_bins_labels, columns=duration_bins_labels)

    if add_total:
        df_pivot = add_total_col_pivot(df_pivot)

    return df_pivot


def get_pivot_by_rating_dur(df, agg_col, scale=1e6, add_total=True):
    df = df.rename(columns=col_rename_dict)
    df['rating'] = df['apo_warf_rating'].map(lambda x: rating_group.get(x, 'CCC and Below'))
    metric = col_rename_dict.get(agg_col, agg_col)

    df_aggregated = df.dropna(subset=['rating', 'duration'],
                              how='any').groupby(['rating', 'duration'],
                                                 dropna=False, as_index=False).agg({metric: 'sum'})

    df_aggregated[metric] = df_aggregated[metric].fillna(0).div(scale)

    df_pivot = df_aggregated.pivot(index='rating', columns='duration',
                                   values=metric).reindex(index=list(OrderedDict.fromkeys(rating_group.values())),
                                                          columns=duration_bins_labels)

    if add_total:
        df_pivot = add_total_col_pivot(df_pivot)

    return df_pivot


def get_trxn_cost_by_gross_notional(df, add_total=True):
    tcost_pivot_by_spread = get_pivot_by_spd_dur(df, 'apo_tcost', scale=1e6, add_total=False)
    gross_mv_pivot_by_spread = get_pivot_by_spd_dur(df, 'qty_model_est', scale=1e6, add_total=False)

    tcost_pivot_by_spread = tcost_pivot_by_spread.div(gross_mv_pivot_by_spread).mul(1e2)

    tcost_pivot_by_rating = get_pivot_by_rating_dur(df, 'apo_tcost', scale=1e6, add_total=False)
    gross_mv_pivot_by_rating = get_pivot_by_rating_dur(df, 'qty_model_est', scale=1e6, add_total=False)

    tcost_pivot_by_rating = tcost_pivot_by_rating.div(gross_mv_pivot_by_rating).mul(1e2)

    if add_total:
        tcost_pivot_by_spread = add_total_col_pivot(tcost_pivot_by_spread)
        tcost_pivot_by_rating = add_total_col_pivot(tcost_pivot_by_rating)

    return tcost_pivot_by_spread, tcost_pivot_by_rating


def get_spd_dur_chg_df(df, agg_col, t_dt, prev_dt):
    t_df = df.loc[df['effective_date'] == t_dt, :]
    prev_df = df.loc[df['effective_date'] == prev_dt, :]

    t_df_pivot = get_pivot_by_spd_dur(t_df, agg_col, add_total=False)
    prev_df_pivot = get_pivot_by_spd_dur(prev_df, agg_col, add_total=False)

    chg_df = t_df_pivot.fillna(0).sub(prev_df_pivot.fillna(0))
    chg_df = add_total_col_pivot(chg_df)
    return chg_df


def get_rating_dur_chg_df(df, agg_col, t_dt, prev_dt):
    t_df = df.loc[df['effective_date'] == t_dt, :]
    prev_df = df.loc[df['effective_date'] == prev_dt, :]

    t_df_pivot = get_pivot_by_rating_dur(t_df, agg_col, add_total=False)
    prev_df_pivot = get_pivot_by_rating_dur(prev_df, agg_col, add_total=False)

    chg_df = t_df_pivot.fillna(0).sub(prev_df_pivot.fillna(0))
    chg_df = add_total_col_pivot(chg_df)

    return chg_df


def get_trxn_cost_by_gross_notional_chg(df, t_dt, prev_dt):
    t_df = df.loc[df['effective_date'] == t_dt, :]
    prev_df = df.loc[df['effective_date'] == prev_dt, :]

    tcost_pivot_by_spread, tcost_pivot_by_rating = get_trxn_cost_by_gross_notional(t_df, add_total=False)
    prev_tcost_pivot_by_spread, prev_tcost_pivot_by_rating = get_trxn_cost_by_gross_notional(prev_df, add_total=False)

    sprd_chg_df = tcost_pivot_by_spread.fillna(0).sub(prev_tcost_pivot_by_spread.fillna(0))
    sprd_chg_df = add_total_col_pivot(sprd_chg_df)

    rating_chg_df = tcost_pivot_by_rating.fillna(0).sub(prev_tcost_pivot_by_rating.fillna(0))
    rating_chg_df = add_total_col_pivot(rating_chg_df)

    return sprd_chg_df, rating_chg_df
