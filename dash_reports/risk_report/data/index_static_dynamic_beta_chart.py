from datetime import datetime

from data_access.beta_attribution.security_ts_hist import get_benchmark_beta_cdxhy

INDEXES = ['SPY', 'LL TRS', '10Y TRSY']
LL_TRS_STATIC_BETA = 0.4
SPY_STATIC_BETA = 1.5
TRSY_STATIC_BETA = -0.4


def _get_benchmark_data():
    start_date = '2014-01-01'
    end_date = datetime.today().date().strftime('%Y-%m-%d')
    df = get_benchmark_beta_cdxhy(start_date, end_date, include_exp_beta=True)
    return df


def get_index_beta_data():
    data_df = _get_benchmark_data()
    df = data_df[['asof_date', 'BMK_IDX', 'Beta to CDX HY']].pivot_table(index='asof_date', columns='BMK_IDX',
                                                                         values='Beta to CDX HY')
    df = df.rename(columns={'SPY US EQUITY': 'SPY', 'iboxx_usloan_trs': 'LL TRS', 'tsy_10y': '10Y TRSY'})
    df = df.fillna(method='pad')

    roll_avg_df = df.rolling(78).mean()
    roll_avg_df = roll_avg_df.rename(columns={x: f'78 per. Mov. Avg. ({x})' for x in roll_avg_df.columns})

    df = df.join(roll_avg_df)
    df['LL TRS Prior Beta'], df['SPY Prior Beta'], df[
        '10Y TRSY Prior Beta'] = LL_TRS_STATIC_BETA, SPY_STATIC_BETA, TRSY_STATIC_BETA

    df_exp = data_df[['asof_date', 'BMK_IDX', 'Exp Beta to CDX HY']].pivot_table(index='asof_date', columns='BMK_IDX',
                                                                                 values='Exp Beta to CDX HY')
    df_exp = df_exp.rename(columns={'SPY US EQUITY': 'SPY (Exp)', 'iboxx_usloan_trs': 'LL TRS (Exp)',
                                    'tsy_10y': '10Y TRSY (Exp)'})
    df_exp = df_exp.fillna(method='pad')

    df = df.merge(df_exp, how='left', left_index=True, right_index=True)

    return df
