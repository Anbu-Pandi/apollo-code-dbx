import logging

import pandas as pd

from common.utils import dtutil, sqlutil, dfutil
from data_access.dbtables import MARKIT_CDS_INDEX_APP
from data_access.dbtables.security_data.utils import _clean_tables_by_ids, processdf_for_insert
from data_access.spark.api import sparkmanager
from data_access.utils import get_where_clause, join_where_clauses, tosqllist


def get_curve_points(fromdt, todt):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    sql = '''
    SELECT * FROM markit_curve WHERE asof_date BETWEEN '{}' and '{}'
    '''
    df = sqlutil.readdfsql(sql.format(fromdt, todt), app=MARKIT_CDS_INDEX_APP)
    return df


def load_dfs(dfs, usespark=False):
    for table, df in dfs.items():
        logging.info(f'starting data load for table: {table}, df: {df.shape}')
        df = processdf_for_insert(df)
        for id_column in ['asof_date_time', 'asof_date']:
            ids = list(pd.to_datetime(df[id_column], errors='coerce').dt.date)
            if id_column in df.columns:
                _clean_tables_by_ids(id_column, ids, {table: id_column}, cast_as_date=True, app=MARKIT_CDS_INDEX_APP)
        logging.info(f'insert data for table: {table}, df: {df.shape}')
        if usespark:
            sparkmanager.pandastosql(df, table, app=MARKIT_CDS_INDEX_APP)
        else:
            sqlutil.insertdf_batch(table, df, app=MARKIT_CDS_INDEX_APP)


def _get_tkr_clause(tickers, tbl_name=None):
    prefix = ''
    if tbl_name:
        prefix = f'{tbl_name}.'
    tkr_only_clause = get_where_clause(f'{prefix}ticker', tickers)
    tkr_series_clause = get_where_clause(f"concat({prefix}ticker,'_',{prefix}series)", tickers)
    tkr_series_version_clause = get_where_clause(f"concat({prefix}ticker,'_',{prefix}series,'_',{prefix}version)",
                                                 tickers)
    tkr_clause = join_where_clauses(' OR ', [tkr_only_clause, tkr_series_clause, tkr_series_version_clause])
    return tkr_clause


CURVE_SQL = '''
select * from (
SELECT mcp.asof_date, mcp.asof_date_time, mcp.red9, mcp.family, {}, mcp.version, mcp.series, mcp.mnemonic, mcp.on_the_run, 
mcp.batch_id, mcp.batch_run_id, 
mcp.running_coupon, mcp.tenor, mcp.maturity_date, mct.recovery_rate, mct.factor, mct.currency,
mcp.model_price, mcp.model_spread, mcp.price, mcp.conventional_spread, 
mcp.default_probability, mcp.risky_pv01, mcp.credit_dv01, mcp.ir_dv01, mcp.skew_price, mcp.skew_spread,
row_number() over (partition by mcp.asof_date, mcp.ticker, mcp.tenor, mcp.series, mcp.version order by mct.asof_date desc)  as rw,
(mcp.series*1e6+mcp.version) as ser_ver,
max(mcp.series*1e6+mcp.version) over (partition by mcp.asof_date, mcp.ticker) as max_ser_ver
FROM markit_curve_points mcp
    LEFT JOIN markit_constituents mct ON DATEDIFF(DAY, mct.asof_date, mcp.asof_date) <= 5 AND mct.red9=mcp.red9
WHERE mcp.batch_id='{}' and {}
) t
WHERE rw=1 {}
'''


def get_index_curve_sql(fromdt, todt, tickers, tenor=None, on_the_run=None, batch_id='EOD',
                        use_full_ticker=False, max_series_version_only=True):
    ticker_col = 'mcp.ticker'
    if use_full_ticker:
        ticker_col = "mcp.ticker as index_ticker, concat(mcp.ticker, '_', mcp.series, '_', mcp.version) as ticker"
    asof_clause = '''cast(mcp.asof_date as date) BETWEEN '{}' AND '{}' '''.format(dtutil.todate(fromdt),
                                                                                  dtutil.todate(todt))
    tkr_clause = _get_tkr_clause(tickers, tbl_name='mcp')
    tenor_clause = get_where_clause('upper(mcp.tenor)', tenor)
    on_the_run_clause = get_where_clause('mcp.on_the_run', on_the_run)
    where_clause = join_where_clauses(' AND ', [asof_clause, tkr_clause, tenor_clause, on_the_run_clause])
    max_series_version_clause = ''
    if max_series_version_only: max_series_version_clause = 'and ser_ver=max_ser_ver'

    sql = CURVE_SQL.format(ticker_col, batch_id, where_clause, max_series_version_clause)
    return sql


def get_index_curve(fromdt, todt, tickers, tenor=None, on_the_run=None, batch_id='EOD',
                    use_full_ticker=False, max_series_version_only=True):
    sql = get_index_curve_sql(fromdt, todt, tickers, tenor=tenor, on_the_run=on_the_run, batch_id=batch_id,
                              use_full_ticker=use_full_ticker, max_series_version_only=max_series_version_only)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    df.sort_values(['asof_date', 'ticker', 'series', 'version'])

    off_the_runs = df['on_the_run'].fillna(0) != 1
    df.loc[off_the_runs, 'conventional_spread'] = df.loc[off_the_runs, 'conventional_spread'] \
        .combine_first(df.loc[off_the_runs, 'model_spread'])
    df.loc[off_the_runs, 'price'] = df.loc[off_the_runs, 'price'] \
        .combine_first(df.loc[off_the_runs, 'model_price'])
    return df


OCONSTITUENT_SQL = '''
select * from (
SELECT moc.asof_date, moc.asof_date_time, moc.red9, moc.ticker, moc.family, moc.sub_family, moc.version, moc.series, moc.active_version, moc.on_the_run, mc.currency,
constituent_role, constituent_reference_entity, constituent_ticker,constituent_red9, constituent_red_code, constituent_tier, constituent_doc_clause, constituent_weight,
row_number() over (partition by moc.asof_date, moc.ticker, moc.constituent_ticker, moc.constituent_red9, moc.constituent_red_code, moc.series, moc.version order by moc.series desc, moc.version desc) as rw,
(moc.series*1e6+moc.version) as ser_ver,
max(moc.series*1e6+moc.version) over (partition by moc.asof_date, moc.ticker) as max_ser_ver
FROM markit_original_constituents moc
JOIN markit_constituents mc
    ON DATEDIFF(DAY, mc.asof_date, moc.asof_date) <= 5 
    AND moc.red9=mc.red9
WHERE {}
) t
where rw=1 {}
'''


def get_index_original_constituents(fromdt, todt, tickers, on_the_run=None, max_series_version_only=True):
    asof_clause = '''moc.asof_date BETWEEN '{}' AND '{}' '''.format(dtutil.todate(fromdt),
                                                                    dtutil.todate(todt))
    tkr_clause = _get_tkr_clause(tickers, 'moc')
    on_the_run_clause = get_where_clause('moc.on_the_run', on_the_run)
    where_clause = join_where_clauses(' AND ', [asof_clause, tkr_clause, on_the_run_clause])
    max_series_version_clause = ''
    if max_series_version_only: max_series_version_clause = 'and ser_ver=max_ser_ver'
    sql = OCONSTITUENT_SQL.format(where_clause, max_series_version_clause)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


LCONSTITUENT_SQL = '''
select * from (
SELECT moc.asof_date, moc.asof_date_time, moc.red9, moc.ticker, moc.family, moc.sub_family, moc.version, moc.series, moc.active_version, moc.on_the_run, mc.currency, 
constituent_reference_entity, constituent_ticker, constituent_red_code, constituent_weight,
original_constituent_role, original_constituent_reference_entity, original_constituent_ticker, original_constituent_red9, original_constituent_red_code, original_constituent_tier, 
original_constituent_doc_clause, original_constituent_weight,
row_number() over (partition by moc.asof_date, moc.ticker, moc.original_constituent_ticker, moc.original_constituent_red9, moc.constituent_red_code, moc.series, moc.version  order by moc.series desc, moc.version desc) as rw,
(moc.series*1e6+moc.version) as ser_ver,
max(moc.series*1e6+moc.version) over (partition by moc.asof_date, moc.ticker) as max_ser_ver
FROM v_markit_latest_constituents moc
JOIN markit_constituents mc
    ON DATEDIFF(DAY, mc.asof_date, moc.asof_date) <= 5 
    AND moc.red9=mc.red9
WHERE {}
) t
where rw=1 {}
'''


def get_index_latest_constituents(fromdt, todt, tickers, on_the_run=None, max_series_version_only=True):
    asof_clause = '''moc.asof_date BETWEEN '{}' AND '{}' '''.format(dtutil.todate(fromdt),
                                                                    dtutil.todate(todt))
    tkr_clause = _get_tkr_clause(tickers, 'moc')
    on_the_run_clause = get_where_clause('moc.on_the_run', on_the_run)
    where_clause = join_where_clauses(' AND ', [asof_clause, tkr_clause, on_the_run_clause])
    max_series_version_clause = ''
    if max_series_version_only: max_series_version_clause = 'and ser_ver=max_ser_ver'
    sql = LCONSTITUENT_SQL.format(where_clause, max_series_version_clause)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


ACTIVE_DATE_USING_PRICE_SQL = '''
select 
red9, ticker, series, version, min(asof_date) as active_date
from dbo.markit_curve_points
where price is not null
group by all
'''


def get_active_date_using_price():
    df = sqlutil.readdfsql(ACTIVE_DATE_USING_PRICE_SQL, app=MARKIT_CDS_INDEX_APP)
    return df


INDEX_INFO_SQL = '''
WITH red9_maxdt AS (
SELECT red9, max(asof_date) as max_dt
from markit_constituents
WHERE asof_date <= '{0}'
GROUP BY red9
), red9_maxdt_mc AS (
SELECT red9, max(asof_date) as max_dt
from markit_curve
WHERE asof_date <= '{0}'
GROUP BY red9
), 
index_data AS (
SELECT mc.asof_date, mc.asof_date_time, 
mc.red9, COALESCE(mc.ticker, mct.ticker) AS ticker, mc.family, mct.sub_family, mc.series,mc.version,  
coalesce(mc. mnemonic, concat(COALESCE(mc.ticker, mct.ticker), '\', mct.series, '\', mct.version)) AS mnemonic,
concat(COALESCE(mc.ticker, mct.ticker), '_', mct.series, '_', mct.version) as product,
mct.active_version, mc.on_the_run, mct.currency, 
mct.factor, mct.effective_date, mct.annex_date, mct.active_date, mct.recovery_rate, mct.term, 
mc.batch_id, mc.batch_run_id, is_eod,
ROW_NUMBER () OVER (PARTITION BY mc.red9 ORDER BY coalesce(mc.asof_date_time, CAST((CAST(mc.asof_date AS string) + '23:59') as timestamp)) desc) AS rw
FROM markit_curve mc 
JOIN red9_maxdt_mc rdm
    ON mc.red9=rdm.red9
    AND mc.asof_date=rdm.max_dt
JOIN markit_constituents mct 
	ON mc.red9=mct.red9
JOIN red9_maxdt rd
    ON mct.red9=rd.red9
    AND mct.asof_date=rd.max_dt
{1}
)
SELECT * FROM index_data WHERE rw=1
ORDER BY ticker, series, version, asof_date
'''


def get_index_info_sql(tickers=None, batch_id='EOD', knowledge_date=None):
    tkr_clause = _get_tkr_clause(tickers, tbl_name='mct') or ''
    eod_clause = get_where_clause('batch_id', batch_id)

    where_clause = join_where_clauses(' AND ', [tkr_clause, eod_clause])
    if len(where_clause or '') > 0: where_clause = f'WHERE {where_clause}'
    sql = INDEX_INFO_SQL.format(knowledge_date or dtutil.dateadd(dtutil.today(), '-1b'), where_clause)
    return sql


def get_index_info(tickers=None, batch_id='EOD', knowledge_date=None):
    sql = get_index_info_sql(tickers, batch_id=batch_id, knowledge_date=knowledge_date)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)

    active_dt_df = get_active_date_using_price()
    df = df.merge(active_dt_df, on='red9', how='left', suffixes=('', '_2'))
    df['active_date'] = df['active_date'].fillna(df['active_date_2'])
    df.drop(columns=[x for x in df.columns if x.endswith('_2')], inplace=True)

    return df


MARKIT_INDEX_PRICING_SQL = '''
with latest_markit_constituents as 
(
select * from markit_constituents
where asof_date = (select max(asof_date) from markit_constituents)
)
select
    mcp.ticker, 
    mcp.series,
    mcp.version,
    mcp.mnemonic,
    mcp.asof_date,
    mc.sub_family,
    mcp.index_id as index_id2,
    round(running_coupon*10000,0) as coupon,
    mcp.tenor,
    mcp.maturity_date,
    round(mc.factor*100000,0)/100000 as factor,
    -- add original constituent names
    -- add realized losses
    round(mc.recovery_rate*100000,0)/100000 as recovery_rate,
    (model_price*100) as model_px,
    (price*100) as mkt_px,
    ((price - model_price)*100) as price_skew,
    (model_spread*10000) as model_spd_bps,
    (conventional_spread*10000) as conv_spd_bps,
    ((conventional_spread - model_spread)*10000) as spd_skew_bps,
    (credit_dv01/1000) as credit_dv01,
    (risky_pv01) as risky_pv01,
    (ir_dv01/1000) as ir_dv01
from
    markit_curve_points mcp
left join latest_markit_constituents mc on
    mcp.red9 = mc.red9
--    and mcp.asof_date = mc.asof_date
where
    mcp.asof_date between '{0}' and '{1}'
--  and mcp.series in (39,38)
    and mcp.tenor = '5Y'
    and mcp.ticker in {2}
    and cast(mc.active_version as float) = 1
    and mcp.batch_id = 'EOD'
order by
    mcp.index_id,
    mcp.batch_run_id'''


def get_markit_index_pricing(start_date, end_date, ticker):
    sql = MARKIT_INDEX_PRICING_SQL.format(start_date, end_date, tosqllist(ticker))
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


MARKIT_INDEX_VOLUME_SQL = '''
    select
        DATEADD(day,-1, markit_serv_update_date) as asof_date,
        malcp.ticker,
        malcp.mnemonic,
        mcp.maturity_date,
        {3}
        avg(malcp.weekly_gross_notional) as daily_gross_notional,
        avg(malcp.gnxtrd) as gross_transaction,
        avg(malcp.weekly_contracts) as weekly_contracts
    from
        markit_average_liquidity_curve_points malcp
    left join markit_curve_points mcp on
        mcp.red9 = malcp.red9 
        and mcp.tenor=malcp.tenor
        and mcp.batch_id = 'EOD'
        and malcp.asof_date = mcp.asof_date
    where
        malcp.markit_serv_update_date is not null
        and DATEADD(day,-1, markit_serv_update_date) between '{0}' and '{1}'
        and mcp.tenor = '5Y'
        {2}
    group by all
'''


def get_markit_index_volume(start_date, end_date, tickers=None, additional_cols=None):
    ticker_clause = get_where_clause('malcp.ticker', tickers)
    if len(tickers or '') > 0:
        ticker_clause = f'and {ticker_clause}'

    additional_cols = ",".join([f"malcp.{x}" for x in dfutil.tolist(additional_cols, replacenone=True)])
    if len(additional_cols or '') > 0:
        additional_cols = f'{additional_cols},'
    sql = MARKIT_INDEX_VOLUME_SQL.format(start_date, end_date, ticker_clause or '', additional_cols)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    df['asof_date'] = df['asof_date'].dt.date
    return df


OTR_INDEX_SQL = '''
with mkt_curve as (
	select
		asof_date,
		id,
		ticker,
		series,
		version,
		recovery,
		is_eod
	from
		markit_curve
	where id like '%EOD%'
),
curve_points as (
	select
		asof_date,
		id,
		ticker,
		series,
		version,
		tenor,
		maturity_date,
		running_coupon,
        model_price,
        price,
        model_spread,
        conventional_spread,
        credit_dv01,
        on_the_run,
        row_number() over (partition by asof_date, ticker, tenor, series, version order by series desc, version desc) as rw,
        (series*1e6+version) as ser_ver,
        max(series*1e6+version) over (partition by asof_date, ticker, series) as max_ser_ver
	from
		markit_curve_points
	where id like '%EOD%' and price is not null
),
factor_data as (
    select 
        asof_date,
        ticker,
        series,
        version,
        factor,
        row_number() over (partition by asof_date, ticker, series, version order by active_version desc)  as factor_data_rw
    from
        markit_constituents    
),
factor_data_new as (
select * from factor_data where factor_data_rw = 1
),
volume_data as (
select
        DATEADD(day,-1, markit_serv_update_date) as asof_date,
        malcp.ticker,
        malcp.series, 
        malcp.version,
        avg(malcp.weekly_gross_notional) as daily_gross_notional,
        avg(malcp.gnxtrd) as gross_transaction,
        avg(malcp.weekly_contracts) as weekly_contracts
    from
        markit_average_liquidity_curve_points malcp
    left join markit_curve_points mcp on
        mcp.red9 = malcp.red9
    where
        malcp.markit_serv_update_date is not null       
        and mcp.batch_id = 'EOD'
    group by
        DATEADD(day,-1, markit_serv_update_date),
        malcp.ticker,
        malcp.series, 
        malcp.version
)
select
	mc.asof_date,
	mc.id,
	mc.ticker,
	mc.series,
	mc.version,
	fd.factor,
    concat(mc.ticker, '-', mc.series) as index_ticker,
	cp.tenor,
	mc.recovery,
	cp.maturity_date,
	cp.running_coupon,
    cp.model_price * 100 as model_px,
    cp.price * 100 as mkt_px,
    ((cp.price - cp.model_price)*100) as price_skew,
    cp.model_spread * 10000 as model_spd_bps,
    cp.conventional_spread * 10000 as conv_spd_bps,
    ((cp.conventional_spread - cp.model_spread)*10000) as spd_skew_bps,
    vd.daily_gross_notional,
    vd.gross_transaction,
    vd.weekly_contracts,
    cp.credit_dv01,
    cp.on_the_run,
    cp.rw,
    cp.ser_ver,
    cp.max_ser_ver
from
	mkt_curve as mc
left join curve_points as cp on
	mc.id = cp.id
	and mc.asof_date = cp.asof_date
	and mc.ticker = cp.ticker
	and mc.series = cp.series
	and mc.version = cp.version 
left join factor_data_new as fd on
    mc.asof_date = fd.asof_date
    and mc.ticker = fd.ticker
    and mc.series = fd.series
    and mc.version = fd.version
left join volume_data as vd on
    mc.asof_date = vd.asof_date
    and mc.ticker = vd.ticker
    and mc.series = vd.series
    and mc.version = vd.version 
where mc.id like '%EOD%'
    and {0}
    and cp.rw=1 and cp.ser_ver = cp.max_ser_ver
order by asof_date asc
'''


def get_otr_index_data(tickers, tenor, start_date, end_date, on_the_run='True'):
    asof_date_clause = f"mc.asof_date between '{start_date}' and '{end_date}'"
    tickers_clause = f"mc.ticker in {tosqllist(tickers)}"
    tenor_clause = f"cp.tenor = '{tenor}'"

    on_the_run_clause = get_where_clause('cp.on_the_run', on_the_run)
    where_clause = join_where_clauses(' AND ', [asof_date_clause, tickers_clause, tenor_clause, on_the_run_clause])

    sql = OTR_INDEX_SQL.format(where_clause)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


def get_cdx_ex_default_data(ticker):
    sql = f"select * from cdx_ex_default_valuation where ticker in {tosqllist(ticker)}"
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)
    return df


DEFAULTS_SQL = '''
with succ_events as (
select me.asof_date, event_date as succ_date, name, short_name,
coalesce(valid_from, cast('1900-01-01' as date)) as valid_from,
coalesce(valid_to, cast('2200-01-01' as date)) as valid_to,
red_code, cds_successor, cds_predecessor
from markit_entity me
left join markit_events_cds_succession mcs on me.asof_date=mcs.asof_date and me.id=mcs.id
where cast(me.asof_date as date)='{0}'
), credit_events as (
select me.asof_date, me.id, me.red_code, me.short_name, default_date, auction_date, cerrd, event_type, avg(auction_price) as avg_auction_price, count(*) as ct
from markit_events_credit_event mce
join markit_entity me on mce.asof_date=me.asof_date and mce.id=me.id
where mce.asof_date='{0}'
group by all
), defaulted as (
select coalesce(ce.auction_date, ce.default_date) as active_date_lag1,
moc.constituent_red_code, se.succ_date, coalesce(se.cds_successor, se.red_code) as red_code_1, coalesce(se.cds_predecessor, se.red_code) as red_code_2,
se.valid_from, se.valid_to,
moc.asof_date, moc.red9, moc.ticker, moc.series, moc.version, mocv1.constituent_weight, ce.short_name, default_date, auction_date, avg_auction_price, cerrd, event_type
from markit_original_constituents moc
left join succ_events se on moc.asof_date=se.asof_date and (moc.constituent_red_code=coalesce(se.cds_successor, se.red_code)
or moc.constituent_red_code=coalesce(se.cds_predecessor, se.red_code))
left join credit_events ce on moc.asof_date=ce.asof_date and(ce.red_code=coalesce(se.cds_successor, se.red_code)
or ce.red_code=coalesce(se.cds_predecessor, se.red_code))
join markit_original_constituents mocv1 on moc.asof_date=mocv1.asof_date and
    moc.constituent_red_code=mocv1.constituent_red_code and moc.ticker=mocv1.ticker
    and moc.series=mocv1.series and mocv1.version=1
where
cast(moc.asof_date as date)='{0}'
and moc.constituent_weight=0
{1}
)
select active_date_lag1, short_name, constituent_red_code, constituent_weight,
asof_date, ticker, series, version, red9, default_date, auction_date, avg_auction_price, cerrd, event_type,
collect_list(succ_date) as succ_dates,
collect_list(red_code_1) as red_codes_1,
collect_list(red_code_2) as red_codes_2
from defaulted
group by all
order by ticker, series desc, version desc, active_date_lag1 desc
'''


def get_defaults_by_ticker(tickers=None, dt=None, max_version_only=False):
    where_clause = ''
    if dt:
        dt = dtutil.todate(dt)
        where_clause = f"where asof_date<='{dt}'"
    dt_df = sqlutil.readdfsql(f'select max(asof_date) as asof_date from markit_original_constituents {where_clause}',
                              app=MARKIT_CDS_INDEX_APP)
    dt = dt_df['asof_date'].iloc[0]
    dt = dtutil.todate(dt)

    tkr_clause = _get_tkr_clause(tickers, tbl_name='moc')
    if tkr_clause:
        tkr_clause = f'and {tkr_clause}'

    sql = DEFAULTS_SQL.format(dt, tkr_clause)
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_INDEX_APP)

    if max_version_only:
        df['max_version'] = df.groupby(['ticker', 'series'])['version'].transform('max')
        df = df[df['version'] == df['max_version']]

    return df


def get_default_pv_by_ticker(tickers=None, dt=None):
    index_info_df = get_index_info(tickers, knowledge_date=dt, batch_id='EOD')
    df = get_defaults_by_ticker(tickers, dt, max_version_only=False)

    def _get_group_metrics(group):
        default_pv = (group['constituent_weight'] * (100 - group['avg_auction_price'])).sum()
        default_wt = group['constituent_weight'].sum()
        default_ct = len(group)

        latest_date_mask = pd.to_datetime(group['default_date']) == pd.to_datetime(group['default_date']).max()
        group_vals = {}
        for x in ['auction_date', 'default_date', 'cerrd', 'avg_auction_price', 'short_name', 'constituent_red_code',
                  'event_type']:
            group_vals[x] = group.loc[latest_date_mask, x].iloc[0] if latest_date_mask.any() else None

        return pd.Series({
                             'default_pv': default_pv,
                             'default_wt': default_wt,
                             'default_ct': default_ct} | group_vals
                         )

    df = df.groupby(['ticker', 'series', 'version']).apply(_get_group_metrics).reset_index()

    df = df.merge(index_info_df, on=['ticker', 'series', 'version'], how='left')
    df['ticker_series'] = df['ticker'] + '_' + pd.to_numeric(df['series'], downcast='integer').astype(str)

    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init('prod')

    # df = get_index_info(['CDX-NAHY'])
    # df = get_index_curve('2024-08-20', '2024-08-28', ['CDX-NAHY'])
    # df = get_index_original_constituents('2024-08-20', '2024-08-28', ['CDX-NAHY'])
    # df = get_markit_index_volume('2024-08-20', '2024-08-28', ['CDX-NAHY'])
    # df = get_markit_index_volume('2024-08-20', '2024-08-28',
    #                              additional_cols=["red9", "series", "version", "weekly_gross_notional"])
    df = get_defaults_by_ticker(['CDX-NAHY'], dt='2026-01-16', max_version_only=True)
    # df = get_default_pv_by_ticker(dt='2025-01-25')
    logging.info(df)
    pass
