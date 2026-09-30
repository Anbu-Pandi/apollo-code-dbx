import logging

import numpy as np
import pandas as pd

from common.utils import dtutil, sqlutil
from data_access.dbtables import CDX_VOL_APP
from data_access.spark.azuredb import AzureDB
from data_access.utils import get_where_clause, join_where_clauses
from sdr.dtccpy.credit_utils import _is_px_index

VOL_MARK_SQL = '''
select 
    *,
    rank() over(partition by asof_date_time, product, vol_mark_type 
        order by case when source='jpm' then 2 else 1 end, min_quote_count_threshold desc) rk
from dbo.cds_index_vol_data
where 
    cast(asof_date as date) between '{0}' and '{1}'
    {2}
'''


def _create_cdx_where_clause(ticker, product=None, vol_mark_type=None, is_extrapolated=None,
                             product_col='product', min_quote_count_threshold=None):
    ticker_clause = get_where_clause('ticker', ticker)
    product_clause = get_where_clause(product_col, product)
    vol_mark_type_clause = get_where_clause(f"coalesce(vol_mark_type, '{vol_mark_type}')", vol_mark_type)
    is_extrapolated_clause = get_where_clause(f"coalesce(is_extrapolated, {is_extrapolated})", is_extrapolated)
    min_quote_count_threshold_clause = get_where_clause(f'coalesce(min_quote_count_threshold, {min_quote_count_threshold})', min_quote_count_threshold)

    where_clause = join_where_clauses(' AND ',
                                      [ticker_clause, vol_mark_type_clause, product_clause, is_extrapolated_clause,
                                       min_quote_count_threshold_clause])
    return where_clause


def get_vol_mark(fromdt, todt, ticker=None, product=None,
                 vol_mark_type='median',
                 min_quote_count_threshold=None,
                 is_extrapolated=None, use_live=False):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    dt_col = 'asof_date'
    where_clause = _create_cdx_where_clause(ticker, product, vol_mark_type, is_extrapolated,
                                            min_quote_count_threshold=min_quote_count_threshold)
    if len(where_clause) > 0:
        where_clause = ' AND ' + where_clause

    live_df = pd.DataFrame()
    if use_live:
        localdb = AzureDB()
        live_df = localdb.read_sql(VOL_MARK_SQL.format(fromdt, todt, where_clause))

    if (len(live_df) > 0) or ((fromdt == todt) and (use_live)):
        todt = min(todt, dtutil.dateadd(dtutil.today(), '-1d'))

    hist_df = pd.DataFrame()
    if fromdt <= todt:
        hist_df = sqlutil.readdfsql(VOL_MARK_SQL.format(fromdt, todt, where_clause), app=CDX_VOL_APP)
        if len(live_df) > 0:
            hist_dt = dtutil.todate(hist_df[dt_col].max())
            live_df = live_df[pd.to_datetime(live_df[dt_col]).dt.date > hist_dt]

    df = pd.concat([hist_df, live_df], ignore_index=True)
    if len(df) > 0:
        df = df[df['rk'] == 1]

        df['asof_date_time'] = dtutil.ds_tz_convert(df['asof_date_time'], from_tz='UTC',
                                                    to_tz='UTC')
        df['asof_date_time_utc'] = dtutil.ds_tz_convert(df['asof_date_time'], from_tz='America/New_York',
                                                        to_tz='UTC')

        df['asof_date'] = pd.to_datetime(df['asof_date'], format='mixed').dt.date
        df['option_end_date'] = pd.to_datetime(df['option_end_date'], format='mixed').dt.date

        df['vol_bid_ann'] = df['vol_bid_bpd'] * (252 ** 0.5)
        df['vol_ask_ann'] = df['vol_ask_bpd'] * (252 ** 0.5)
        df['vol_mid_ann'] = df['vol_mid_bpd'] * (252 ** 0.5)

        _, df['series'], df['version'] = zip(*df['product'].str.split('_'))
        df['series'] = df['series'].astype(int)
        df['version'] = df['version'].astype(int)

    return df


LIVE_QUOTE_SQL = '''
select 
    asset_class,
    strike as strike,
    spread_strike as spread_strike,
    bid_payer as payer_bid,
    ask_payer as payer_ask,
    delta_payer as payer_delta,
    bid_receiver as receiver_bid,
    ask_receiver as receiver_ask,
    delta_receiver as receiver_delta,
    mid_spread_volatility as spread_vol,
    mid_volatility as price_vol,
    reference as spot,
    reference as spread_spot,
    forward_price as forward,
    forward_spread as spread_forward,
    cast(concat(cast(expiry_year as int), '-' , cast(expiry_month as int), '-', cast(expiry_day as int)) as date) as expiry,
    mpq.internal_instrument_identifier as internal_instrument_identifier,
    concat(mpq.ticker, '_', cast(mpq.series as int), '_', cast(mpq.version as int)) as product, 
    message_sender_email as sender,
    mpq.source_full_name as sender_bank,
    mpq.received_date_time as asof_date_time,
    asof_date as asof_date,
    ticker as ticker
from markit_parsing_quote mpq
where 
    mpq.asset_class = 'IO' 
    and mpq.asof_date between '{0}' and '{1}'
    and expiry_year is not null
    {2}
'''


def get_quotes(fromdt, todt, ticker=None, product=None, use_live=False):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    dt_col = 'asof_date'
    where_clause = _create_cdx_where_clause(ticker, product,
                                            product_col="concat(mpq.ticker, '_', cast(mpq.series as int), '_', cast(mpq.version as int))")
    if len(where_clause) > 0:
        where_clause = ' AND ' + where_clause

    live_df = pd.DataFrame()
    if use_live:
        localdb = AzureDB()
        live_df = localdb.read_sql(LIVE_QUOTE_SQL.format(fromdt, todt, where_clause))

    if (len(live_df) > 0) or ((fromdt == todt) and (use_live)):
        todt = min(todt, dtutil.dateadd(dtutil.today(), '-1d'))

    hist_df = pd.DataFrame()
    if fromdt <= todt:
        hist_df = sqlutil.readdfsql(LIVE_QUOTE_SQL.format(fromdt, todt, where_clause), app=CDX_VOL_APP)
        if len(live_df) > 0:
            hist_dt = dtutil.todate(hist_df[dt_col].max())
            live_df = live_df[pd.to_datetime(live_df[dt_col]).dt.date > hist_dt]

    df = pd.concat([hist_df, live_df], ignore_index=True)
    for col in ['ticker', 'product', 'asof_date', 'asof_date_time']:
        if col not in df.columns:
            df[col] = pd.Series(dtype='object')
    if df.empty:
        df['asof_date_time_utc'] = pd.Series(dtype='datetime64[ns]')
        df['asof_date_time_nyc'] = pd.Series(dtype='datetime64[ns]')
        return df
    if 'asof_date_time' not in df.columns:
        if 'asof_date' in df.columns:
            df['asof_date_time'] = pd.to_datetime(df['asof_date'], errors='coerce')
        else:
            df['asof_date_time'] = pd.NaT

    df['asof_date_time_utc'] = dtutil.ds_tz_convert(df['asof_date_time'], from_tz='UTC',
                                                    to_tz='UTC')
    df['asof_date_time_nyc'] = dtutil.ds_tz_convert(df['asof_date_time_utc'], from_tz='UTC',
                                                    to_tz='America/New_York')

    if 'ticker' in df.columns and 'spot' in df.columns and 'spread_spot' in df.columns:
        is_px = np.vectorize(_is_px_index)(df['ticker'].values)
        df['spot'] = np.where(is_px, df['spot'], np.nan)
        df['spread_spot'] = np.where(is_px, np.nan, df['spread_spot'])

    return df


def upsert_live_vol_df(df, overwrite=False):
    AzureDB.writedf_local(df, 'cds_index_vol_data', overwrite=overwrite)



CORRELATION_MARKS_SQL = '''
with cdx_vol_data as
(
select
	case
		when source = 'jpm' then '2_jpm'
		when source = 'apo' then '1_apo'
	end as source,
	coalesce(vol_mark_type,
	'median_interp') as vol_mark_type,
	case
		when vol_mark_id = 'NYAM' then '3_NYAM'
		when vol_mark_id = 'NYMD' then '2_NYMD'
		when vol_mark_id = 'NYPM' then '1_NYPM'
		else '1_NYPM'
	end vol_mark_id,
	case
		when index_name = 'HY-5Y' then 'CDX-NAHY'
		when index_name = 'IG-5Y' then 'CDX-NAIG'
		else index_name
	end as index_name,
	case
		when index_name = 'HY-5Y' then 'CDX-NAHY'
		when index_name = 'IG-5Y' then 'CDX-NAIG'
		else product
	end as product,
	strike_type,
	asof_date,
	strike,
	rel_strike,
	tenor,
	option_end_date,
	atm_forward_price,
	atm_forward_spread,
	spot_price,
	spot_spread,
	implied_vol,
	duration,
	implied_normal_vol,
	apo_atm_fwd_spread
from
	cds_index_vol_data
where
	cds_index_tenor = '5Y'
	and strike_type <> 'delta'
	and coalesce(vol_mark_type,
	'median_interp')= 'median_interp'
	and asof_date between '{0}' and '{1}'
	and (index_name like '%HY%'
		or index_name like '%IG%' )
),
ranked_cdx_vol_data as (
select
	*,
	rank() over(partition by asof_date,
	index_name
order by
	source,
	product desc,
	vol_mark_id) as rk
from
	cdx_vol_data
)
select
	*
from
	ranked_cdx_vol_data
where
	rk = 1
order by
	asof_date desc
'''


def get_correlation_marks(fromdt, todt):
    fromdt, todt = dtutil.todate(fromdt), dtutil.todate(todt)
    return sqlutil.readdfsql(CORRELATION_MARKS_SQL.format(fromdt, todt), app='databricks')

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    # df = get_quotes('20240422', '20240422', product='CDX-NAHY_42_1', use_live=True)

    vol_df = get_vol_mark('20240101', '20240131', use_live=False, ticker='CDX-NAHY', min_quote_count_threshold=1)

    logging.info(vol_df)
