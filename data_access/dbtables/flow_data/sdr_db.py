import datetime
import logging

import duckdb
import numpy as np
import pandas as pd

from common.utils import dtutil, sqlutil, dfutil
from data_access.dbtables import SDR_APP
from data_access.dbtables.security_data.utils import processdf_for_insert
from data_access.spark.api import sparkmanager
from data_access.spark.azuredb import AzureDB
from data_access.utils import get_where_clause, join_where_clauses

LATEST_TRADES_SQL = """
                    select *
                    from sdr_transactions st \
                    """

SWITCH_DATE = '20221202'

SWITCH_MAP = {'sdr_transactions': 'sdr_transactions_pre_20220212',
              'asset_class': 'primary_asset_class',
              'underlier_idleg1': 'underlying_asset_id',
              'dissemination_identifier': 'dissemination_id',
              'original_dissemination_identifier': 'original_dissemination_id'}

TICKER_ESTIMATED_NOTIONAL_MULT_MAP = {'CDX-NAIG': 5.0,
                                      'CDX-NAHY': 1.5,
                                      'ITRAXX-Xover': 1.5,
                                      'ITRAXX-Europe': 1.5,
                                      'ITRAXX-FinSen': 1.5,
                                      'DEFAULT': 1.0}


def update_estimated_notional(df):
    if df is None or df.empty:
        return df
    if 'notional' not in df.columns:
        return df
    if 'ticker' not in df.columns:
        df['ticker'] = ''
    if 'notional_estimated_flag' not in df.columns:
        df['notional_estimated_flag'] = ''
    is_estimated_trade = (df['notional_estimated_flag'] == 'estimated')
    default_mult = TICKER_ESTIMATED_NOTIONAL_MULT_MAP['DEFAULT']
    df['estimated_notional_mult'] = df['ticker'].map(lambda x: TICKER_ESTIMATED_NOTIONAL_MULT_MAP.get(x, default_mult))
    df['estimated_notional'] = (df['notional'] *
                                np.where(is_estimated_trade, df['estimated_notional_mult'], 1.0))
    return df


def _resolve_names(dt, x):
    if dtutil.todate(dt) <= dtutil.todate(SWITCH_DATE):
        return SWITCH_MAP.get(x, x)
    return x


def _resolve_sql(dt, sql):
    for k, v in SWITCH_MAP.items():
        sql = sql.replace(k, _resolve_names(dt, k))
    return sql


def _create_where_clause(dt, primary_asset_class=None, source=None, product_id=None, underlying_asset=None):
    dt_clause = get_where_clause(_resolve_names(dt, 'cast(execution_timestamp as date)'), dt)
    asset_class_clause = get_where_clause(_resolve_names(dt, 'asset_class'), primary_asset_class)
    source_clause = get_where_clause(_resolve_names(dt, 'source'), source)
    product_id_clause = get_where_clause(_resolve_names(dt, 'product_id'), product_id)
    underlying_asset_id_clause = get_where_clause(_resolve_names(dt, 'underlier_idleg1'), underlying_asset)
    underlying_asset_name_clause = get_where_clause(_resolve_names(dt, 'underlying_asset_name'), underlying_asset)
    underlying_asset_clause = join_where_clauses(' OR ', [underlying_asset_id_clause, underlying_asset_name_clause])

    where_clause = join_where_clauses(' AND ',
                                      [dt_clause, asset_class_clause, source_clause, product_id_clause,
                                       underlying_asset_clause])
    return where_clause


def get_sdr_transactions(dt, primary_asset_class=None, source=None, product_id=None, underlying_asset=None):
    sql = _resolve_sql(dt, LATEST_TRADES_SQL)
    SQL = f"""
    {sql} 
    WHERE 
    {_create_where_clause(dt, primary_asset_class, source, product_id, underlying_asset)}
    """

    remotedb = AzureDB(local_dir=None)
    df = remotedb.read_sql(SQL)
    return df


def get_sdr_live_transactions(dt, primary_asset_class=None, source=None, product_id=None, underlying_asset=None,
                              additional_where=None):
    sql = _resolve_sql(dt, LATEST_TRADES_SQL)
    additional_where = additional_where or ''
    if len(additional_where) > 0:
        additional_where = f'AND {additional_where}'
    SQL = f"""
        {sql} 
        WHERE 
        {_create_where_clause(dt, primary_asset_class, source, product_id, underlying_asset)}
        {additional_where}
        """

    localdb = AzureDB()
    df = localdb.read_sql(SQL)
    return df


def query_sdr_live_transactions(query):
    localdb = AzureDB()

    try:
        df = localdb.read_sql(query)
        return df
    except:
        pass


def get_credit_upi_df():
    return sqlutil.readdfsql('''
                             select *
                             from dbo.v_sdr_upi
                             where (header_asset_class = 'Credit' and header_instrument_type = 'Swap' and
                                    header_use_case = 'Index')
                                or (header_asset_class = 'Credit' and header_instrument_type = 'Option' and
                                    header_use_case = 'Index_Swaption')
                             ''', app=SDR_APP)


def _process_sdr_df(df, dt):
    df = pd.DataFrame({
        col: df[col] if df[col].dtype in ('int64', 'float64') else processdf_for_insert(df[col].to_frame())[col]
        for col in df.columns
    }, index=df.index)
    df['asof_date'] = dt
    df['knowledge_date'] = dtutil.dateadd(dt, "1d")
    df['load_date'] = datetime.datetime.now()
    df['rk'] = 1
    return df


def upsert_sdr_transactions(dt, df, overwrite):
    df = _process_sdr_df(df, dt)

    tbl = _resolve_names(dt, 'sdr_transactions')
    sparkmanager.pandastodelta(df, f'{tbl}', 'tables/dbo', 'asof_date', overwrite)


def upsert_sdr_live_transactions(dt, df, overwrite):
    df = _process_sdr_df(df, dt)
    tbl = _resolve_names(dt, 'sdr_transactions')
    AzureDB.writedf_local(df, tbl, overwrite)


def upsert_upi_df(df, overwrite):
    sparkmanager.pandastodelta(df, 'sdr_upi', 'tables/dbo', 'asof_date', overwrite)


def upsert_upi_batches(batches, overwrite):
    return sparkmanager.pandastodelta_batches(batches, 'sdr_upi', 'tables/dbo', 'asof_date', overwrite)


def upsert_cdx_trades_df(df, overwrite):
    sparkmanager.pandastodelta(df, 'sdr_cdx_trades', 'tables/dbo', 'asof_date', overwrite)


def upsert_cdx_trades_live_df(df, overwrite):
    AzureDB.writedf_local(df, 'sdr_cdx_trades', overwrite)


def upsert_cdx_option_trades_df(df, overwrite):
    sparkmanager.pandastodelta(df, 'sdr_cdx_option_trades', 'tables/dbo', 'asof_date', overwrite)


def upsert_cdx_option_trades_live_df(df, overwrite):
    AzureDB.writedf_local(df, 'sdr_cdx_option_trades', overwrite)


def upsert_cdx_option_pos_live_df(df, overwrite):
    AzureDB.writedf_local(df, 'sdr_cdx_option_pos', overwrite)


def _create_cdx_where_clause(ticker, product, red9, prefix=''):
    ticker_clause = get_where_clause(f'{prefix}ticker', ticker)
    red9_clause = get_where_clause(f'{prefix}red9', red9)
    product_clause = get_where_clause(
        f"concat({prefix}ticker, '_', cast({prefix}series as int), '_', cast({prefix}version as int))", product)

    where_clause = join_where_clauses(' AND ', [ticker_clause, red9_clause, product_clause])
    return where_clause


def _get_merged_cdx_trades(sql, fromdt, todt, dt_col, ticker=None, product=None, red9=None, use_live=False, prefix='',
                           db_cols=None):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    db_cols = dfutil.tolist(db_cols, replacenone=True)

    where_clause = _create_cdx_where_clause(ticker, product, red9, prefix)
    if len(where_clause) > 0:
        where_clause = ' AND ' + where_clause

    live_df = pd.DataFrame()
    if use_live:
        localdb = AzureDB()
        if len(db_cols) > 0:
            live_df = localdb.read_sql(sql.format(fromdt, todt, where_clause, ''))
        else:
            live_df = localdb.read_sql(sql.format(fromdt, todt, where_clause))

    if (len(live_df) > 0) or ((fromdt == todt) and (use_live)):
        todt = min(todt, dtutil.dateadd(dtutil.today(), '-1d'))

    hist_df = pd.DataFrame()
    if fromdt <= todt:
        if len(db_cols) > 0:
            db_cols_string = "," + ",".join([f'{prefix}{x}' for x in db_cols])
            hist_df = sqlutil.readdfsql(sql.format(fromdt, todt, where_clause, db_cols_string), app=SDR_APP)
        else:
            hist_df = sqlutil.readdfsql(sql.format(fromdt, todt, where_clause), app=SDR_APP)

        if len(live_df) > 0:
            hist_dt = dtutil.todate(hist_df[dt_col].max())
            live_df = live_df[pd.to_datetime(live_df[dt_col]).dt.date > hist_dt]

    df = pd.concat([hist_df.assign(source='db'), live_df.assign(source='live')], ignore_index=True)
    if dt_col in df.columns:
        df[dt_col] = pd.to_datetime(df[dt_col], format='mixed').dt.date
    return df


def drop_dupe_trades(df):
    if len(df) > 0:
        df['original_dissemination_identifier'] = df['original_dissemination_identifier'] \
            .combine_first(df['dissemination_identifier'])
        df['max_event_timestamp'] = df.groupby(['original_dissemination_identifier'])['event_timestamp'] \
            .transform(max)
        df = df[df['max_event_timestamp'] == df['event_timestamp']]
        df = df[df['action_type'] != 'EROR']
        df = df.drop_duplicates(subset=['original_dissemination_identifier'])

    return df


def drop_roll_trades(df, drop_rolls=False):
    df['is_roll'] = 0
    df['package_indicator'] = pd.to_numeric(df['package_indicator'], errors='coerce').fillna(0)

    for (ticker, timestamp), group in df.groupby(['ticker', 'execution_timestamp']):
        package_trades = group[group['package_indicator'] == 1]
        if len(package_trades) >= 2 and package_trades['product'].nunique() > 1:
            df.loc[package_trades.index, 'is_roll'] = 1

    if drop_rolls: return df[(df['is_roll'].fillna(0) != 1)]

    return df


CDX_SQL = '''
          select cast(ct.asof_date as date)                                                   as asof_date,
                 cast(ct.execution_timestamp as date)                                         as trade_date,
                 ct.execution_timestamp,
                 ct.event_timestamp,
                 ct.red9,
                 concat(ct.ticker, '_', cast(ct.series as int), '_', cast(ct.version as int)) as product,
                 ct.cds_index,
                 ct.ticker,
                 ct.traded_price_or_spread,
                 ct.traded_price_or_spread_type,
                 ct.currency,
                 cast(ct.expiration_date as date)                                             as expiration_date,
                 ct.dissemination_identifier,
                 ct.original_dissemination_identifier,
                 t.action_type,
                 t.event_type,
                 ct.package_indicator,
                 ct.notional,
                 ct.notional_estimated_flag,
                 ct.notional_ccy{3}
          from dbo.sdr_cdx_trades ct
              join dbo.sdr_transactions t
          on t.dissemination_identifier=ct.dissemination_identifier
          where ct.traded_price_or_spread is not null
            and cast (ct.execution_timestamp as date) between '{0}'
            and '{1}'
              {2}
          order by ct.execution_timestamp desc \
          '''


def get_cdx_trades(fromdt, todt, ticker=None, product=None, red9=None, use_live=False, drop_rolls=False):
    dt_col = 'asof_date'
    df = _get_merged_cdx_trades(CDX_SQL, fromdt, todt, dt_col, ticker, product, red9, use_live, prefix='ct.',
                                db_cols=['original_notional', 'trade_direction'])
    df = drop_dupe_trades(df)
    df = drop_roll_trades(df, drop_rolls)

    return df


CDX_OPT_SQL = '''
              select cast(ot.execution_timestamp as date) as trade_date,
                     ot.*,
                     t.action_type,
                     t.event_type
              from dbo.sdr_cdx_option_trades ot
                       join dbo.sdr_transactions t on t.dissemination_identifier = ot.dissemination_identifier
              where cast(ot.execution_timestamp as date) between '{0}' and '{1}'
                and coalesce(ot.guess_num, 1) = 1 {2}
              order by ot.execution_timestamp desc \
              '''


def get_cdx_opt_trades(fromdt, todt, ticker=None, product=None, red9=None, use_live=False):
    dt_col = 'trade_date'
    df = _get_merged_cdx_trades(CDX_OPT_SQL, fromdt, todt, dt_col, ticker, product, red9, use_live, prefix='ot.')

    df = drop_dupe_trades(df)

    df = update_estimated_notional(df)

    for col in ['gamma', 'delta', 'vega_ann', 'forward_price']:
        if col not in df.columns:
            df[col] = np.nan
    df['gamma_usd'] = df['gamma'] * df['forward_price'] * 0.01 * 100_000_000
    df['delta_usd'] = df['delta'] * df['forward_price'] * 0.01 * 100_000_000
    df['vega_ann_usd'] = df['vega_ann'] * 100_000_000

    return df


CDX_OPT_POS_USING_TRADES_SQL = '''
                               select cast(t.execution_timestamp as date)                                     as trade_date,
                                      t.red9,
                                      t.product,
                                      t.ticker,
                                      t.series,
                                      t.version,
                                      cast(t.expiration_date as date)                                         as expiration_date,
                                      t.strike_price,
                                      t.option_type,
                                      t.currency,
                                      sum(case when t.buy_prob > t.sell_prob then 1 else -1 end * t.notional) as notional,
                                      sum((t.buy_prob - t.sell_prob) * t.notional)                            as prob_wt_notional,
                                      sum(case when t.buy_prob > t.sell_prob then 1 else -1 end *
                                          t.estimated_notional)                                               as estimated_notional,
                                      sum((t.buy_prob - t.sell_prob) * t.estimated_notional)                  as prob_wt_estimated_notional,
                                      count(*)                                                                as num_trades,
                                      min(t.execution_timestamp)                                              as first_trade_date,
                                      max(t.execution_timestamp)                                              as last_trade_date
                               from {3} t
                               where coalesce (t.guess_num
                                   , 1) = 1
                                 and cast (t.expiration_date as date) >= '{0}'
                                 and cast (t.execution_timestamp as date) <= '{1}'
                                   {2}
                               group by all
                               order by t.product, cast (t.expiration_date as date), t.strike_price, cast (t.execution_timestamp as date)
                                   desc \
                               '''


def _agg_trades(df):
    df = df.set_index('trade_date')

    sum_cols = [x for x in df.columns if x.endswith('notional')] + ['num_trades']
    min_cols = ['first_trade_date']
    max_cols = ['last_trade_date']
    other_cols = [x for x in df.columns if x not in (sum_cols + min_cols + max_cols)]

    df[sum_cols] = df[sum_cols].fillna(0).cumsum()
    df[min_cols] = df[min_cols].ffill().bfill().cummin()
    df[max_cols] = df[max_cols].ffill().bfill().cummax()
    df[other_cols] = df[other_cols].ffill().bfill()

    return df


def combine_daily_pos_df(agg_df):
    key_cols = ['trade_date', 'product', 'expiration_date', 'strike_price', 'option_type']
    daily_dfs = []
    for dt, _df in agg_df.dropna(subset=key_cols).set_index(key_cols).groupby('trade_date', sort=True):
        _df.reset_index('trade_date', inplace=True)
        if len(daily_dfs) != 0:
            prev_daily_df = daily_dfs[-1]
            prev_daily_df = prev_daily_df[pd.to_datetime(prev_daily_df.index.get_level_values('expiration_date'))
                                          > pd.to_datetime(dt)]

            combined_idx = set(prev_daily_df.reset_index('trade_date', drop=True).index.tolist() +
                               _df.index.tolist())
            _df = _df.reindex(combined_idx)
            _df['trade_date'] = dt

        _df.set_index('trade_date', append=True, inplace=True)
        daily_dfs.append(_df)

    dailydf = pd.concat(daily_dfs)

    levels = list(range(len(dailydf.index.names) - 1))
    netposdf = dailydf.sort_index().reset_index('trade_date').groupby(level=levels).apply(_agg_trades).reset_index()

    return netposdf


def agg_trades_to_pos(daily_trades_df, ticker=None, product=None, red9=None):
    base_cols = [
        'trade_date', 'red9', 'product', 'ticker', 'series', 'version',
        'expiration_date', 'strike_price', 'option_type', 'currency',
        'notional', 'prob_wt_notional', 'estimated_notional',
        'prob_wt_estimated_notional', 'num_trades', 'first_trade_date',
        'last_trade_date', 'asof_date',
    ]
    if daily_trades_df is None or daily_trades_df.empty:
        return pd.DataFrame(columns=base_cols)
    req_cols = {'trade_date', 'expiration_date'}
    if not req_cols.issubset(daily_trades_df.columns):
        return pd.DataFrame(columns=base_cols)
    daily_trades_df['trade_date'] = pd.to_datetime(daily_trades_df['trade_date']).dt.date
    daily_trades_df['expiration_date'] = pd.to_datetime(daily_trades_df['expiration_date'], format='mixed').dt.date

    where_clause = _create_cdx_where_clause(ticker, product, red9)
    if len(where_clause) > 0:
        where_clause = ' AND ' + where_clause

    daily_pos_df = duckdb.sql(
        CDX_OPT_POS_USING_TRADES_SQL.format('1900-01-01', '9999-12-31', where_clause, "daily_trades_df")) \
        .df()
    if daily_pos_df.empty:
        return pd.DataFrame(columns=base_cols)

    daily_pos_df = combine_daily_pos_df(daily_pos_df)
    if daily_pos_df.empty:
        return pd.DataFrame(columns=base_cols)

    daily_pos_df['trade_date'] = pd.to_datetime(daily_pos_df['trade_date']).dt.date
    daily_pos_df['asof_date'] = daily_pos_df['trade_date']
    daily_pos_df['expiration_date'] = pd.to_datetime(daily_pos_df['expiration_date']).dt.date

    return daily_pos_df


def get_cdx_opt_pos_using_trades(fromdt, todt, ticker=None, product=None, red9=None, use_live=False,
                                 df_type='date_pos'):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    query_fromdt = fromdt
    if df_type == 'full_pos':
        query_fromdt = dtutil.dateadd(todt, '-7m')

    daily_trades_df = get_cdx_opt_trades(query_fromdt, todt, ticker=ticker, product=product, red9=red9,
                                         use_live=use_live)

    daily_pos_df = agg_trades_to_pos(daily_trades_df)

    daily_pos_df = daily_pos_df[(daily_pos_df['trade_date'] >= fromdt) &
                                (daily_pos_df['expiration_date'] >= fromdt)]

    return daily_pos_df


CDX_OPT_POS_SQL = '''
                  select *
                  from dbo.sdr_cdx_option_pos op
                  where cast(op.asof_date as date) between '{0}' and '{1}' {2}
                  order by cast (op.asof_date as date) desc \
                  '''


def get_cdx_opt_pos(fromdt, todt, ticker=None, product=None, red9=None, use_live=False):
    dt_col = 'asof_date'
    df = _get_merged_cdx_trades(CDX_OPT_POS_SQL, fromdt, todt, dt_col, ticker, product, red9, use_live, prefix='op.')

    df['maturity_5y'] = pd.to_datetime(df['maturity_5y']).dt.date
    df['expiration_date'] = pd.to_datetime(df['expiration_date']).dt.date

    df['gamma_usd'] = df['gamma'] * df['forward_price'] * 0.01 * 100_000_000
    df['delta_usd'] = df['delta'] * df['forward_price'] * 0.01 * 100_000_000
    df['vega_ann_usd'] = df['vega_ann'] * 100_000_000

    return df


def get_cdx_imbalance(fromdt, todt, days=7, drop_rolls=True):
    query_fromdt = dtutil.dateadd(fromdt, f'-{days}d')
    df = get_cdx_trades(query_fromdt, todt, use_live=False, drop_rolls=drop_rolls)
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
    df['net_notional'] = df['notional'] * df['trade_direction'].map({'BUY': 1, 'SELL': -1}).fillna(0)
    df.sort_values(['trade_date', 'product'], inplace=True)
    df = df.groupby(['trade_date', 'product'], as_index=False).agg(
        daily_net_notional=('net_notional', 'sum'),
        daily_gross_notional=('notional', 'sum'),
    )

    date_range = dtutil.daterange(query_fromdt, todt, step='1d', use_bday=False)
    all_products = df['product'].unique()

    date_product_combos = pd.MultiIndex.from_product([date_range, all_products],
                                                     names=['trade_date', 'product'])
    date_product_df = pd.DataFrame(index=date_product_combos).reset_index()

    df = pd.merge(date_product_df, df, on=['trade_date', 'product'], how='left')
    df['daily_net_notional'] = df['daily_net_notional'].fillna(0)
    df['daily_gross_notional'] = df['daily_gross_notional'].fillna(0)

    df.sort_values(['product', 'trade_date'], inplace=True)

    df['rolling_net_notional'] = df.groupby('product')['daily_net_notional'].transform(
        lambda x: x.rolling(days, min_periods=1).sum())
    df['rolling_gross_notional'] = df.groupby('product')['daily_gross_notional'].transform(
        lambda x: x.rolling(days, min_periods=1).sum())

    df = df[(pd.to_datetime(df['trade_date']) >= pd.to_datetime(fromdt)) &
            (pd.to_datetime(df['trade_date']) <= pd.to_datetime(todt))]

    return df


CUM_IMBALANCE_SQL = '''
                    WITH base_trades_raw AS (SELECT ct.execution_timestamp,
                                                    ct.event_timestamp,
                                                    CAST(ct.execution_timestamp AS DATE)  AS trade_date,
                                                    CONCAT(ct.ticker, '_', CAST(ct.series AS INT), '_',
                                                           CAST(ct.version AS INT))       AS product,
                                                    ct.notional,
                                                    t.action_type,
                                                    ct.trade_direction,
                                                    COALESCE(ct.original_dissemination_identifier,
                                                             ct.dissemination_identifier) AS original_dissem_id
                                             FROM dbo.sdr_cdx_trades ct
                                                      JOIN dbo.sdr_transactions t
                                                           ON t.dissemination_identifier = ct.dissemination_identifier
                                             WHERE ct.traded_price_or_spread IS NOT NULL
                                               AND CAST(ct.execution_timestamp AS DATE)
                                                 BETWEEN DATE_SUB('{0}', {2}) AND '{1}'
                                               AND (ct.is_roll IS NULL OR ct.is_roll <> 1)),
                         latest_events AS (SELECT original_dissem_id,
                                                  MAX(event_timestamp) AS max_event_timestamp
                                           FROM base_trades_raw
                                           GROUP BY original_dissem_id),
                         deduped_trades AS (SELECT b.trade_date,
                                                   b.product,
                                                   b.notional,
                                                   CASE
                                                       WHEN b.trade_direction = 'BUY' THEN 1
                                                       WHEN b.trade_direction = 'SELL' THEN -1
                                                       ELSE 0 END AS dir_mult
                                            FROM base_trades_raw b
                                                     JOIN latest_events le
                                                          ON b.original_dissem_id = le.original_dissem_id
                                                              AND b.event_timestamp = le.max_event_timestamp
                                            WHERE b.action_type != 'EROR'
                        ), daily_agg AS (
                    SELECT
                        trade_date, product, SUM (notional * dir_mult) AS daily_net_notional, SUM (notional) AS daily_gross_notional
                    FROM deduped_trades
                    GROUP BY trade_date, product
                        ),
                        rolling_calcs AS (
                    SELECT
                        a.trade_date, a.product, a.daily_net_notional, a.daily_gross_notional, SUM (b.daily_net_notional) AS rolling_net_notional, SUM (b.daily_gross_notional) AS rolling_gross_notional
                    FROM daily_agg a
                        JOIN daily_agg b
                    ON a.product = b.product
                        AND b.trade_date BETWEEN DATE_SUB(a.trade_date, {2}) AND a.trade_date
                    GROUP BY a.trade_date, a.product,
                        a.daily_net_notional, a.daily_gross_notional
                        )
                    SELECT trade_date,
                           product,
                           daily_net_notional,
                           daily_gross_notional,
                           rolling_net_notional,
                           rolling_gross_notional
                    FROM rolling_calcs
                    ORDER BY trade_date, product \
                    '''


def get_cum_imbalance_using_query(fromdt, todt, days=7):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)

    sql = CUM_IMBALANCE_SQL.format(fromdt, todt, days)
    df = sqlutil.readdfsql(sql, app=SDR_APP)

    return df


if __name__ == '__main__':
    from common.utils import logutil

    logutil.setuplogger()
    from apo_config.config import config

    config.init('prod')

    df = get_cdx_imbalance('20250512', '20250516', days=7, drop_rolls=True)
    df = get_cum_imbalance_using_query('20250407', '20250411', days=7)

    pos_df = get_cdx_opt_pos_using_trades('20240101', '20241210', use_live=False, df_type='date_pos')

    cdx_df = get_cdx_trades('20240508', '20240508', use_live=True)

    hist_df = get_cdx_opt_trades('20240101', '20240422', use_live=True)

    live_df = get_cdx_opt_trades('20240423', '20240423', use_live=True)

    df = pd.concat([hist_df, live_df], ignore_index=True)

    df = drop_dupe_trades(df)

    cdx_opt_pos_df = combine_daily_pos_df(df)

    logging.info(df)
