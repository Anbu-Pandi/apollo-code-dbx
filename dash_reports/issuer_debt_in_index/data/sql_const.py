ICE_INDICES = ['C0A0', 'H0A0', 'ER00', 'HE00', 'UR00']

ASOF_DATE_EQUAL_CLAUSE = '''
cast(asof_date as date) = '{}'
'''

ASOF_DATE_IN_CLAUSE = '''
cast(asof_date as date) IN {}
'''

ASOF_DATE_BETWEEN_CLAUSE = '''
cast(asof_date as date) between '{}' and '{}'
'''

BOND_SQL = '''
    select * from (
    select {}
    , rank() over(partition by ice_index_ticker, isin_number order by 
                            case when frequency='DAILY' then 1 
                            when frequency='MEND' then 2
                            when frequency='PROJ' then 3
                            else 4
                            end
                            ) as rk 
    from dbo.ice_index_all
    where ice_index_ticker in {}
    and {}
    ) t
    where rk=1
'''

BBG_BOND_SQL = '''
select {} from dbo.f_get_bond_security_master('{}')
where id_isin in {}
'''

BBG_BOND_TICKER_SQL = '''
select {0} from dbo.f_get_bond_security_master('{1}') bsm
where ticker in {2}
and COALESCE(bsm.update_type, 0) <> -1
            AND COALESCE(bsm.maturity, COALESCE(null, '{1}')) >= COALESCE(null, '{1}')
            AND COALESCE(bsm.called, FALSE) <> TRUE
            AND COALESCE(bsm.defaulted, FALSE) <> TRUE
            AND COALESCE(bsm.structured_note, FALSE) <> TRUE
            AND COALESCE(bsm.contrib_data_indicator, FALSE) <> TRUE
            AND COALESCE(bsm.called_dt, COALESCE(null, '{1}')) >= COALESCE(null, '{1}')
            AND bsm.amt_outstanding > 100000000
'''

SP_LOAN_SELECT = '''
select {}
from dbo.sp_loan_index li
where {}
'''

JPM_LOAN_SELECT = '''
select {}
from dbo.jpm_loan_index li
where {}
'''

SP_LOAN_SQL = '''
select 
	distinct li.*,
	'SPBDAL' as index,
    wli2.value as loanx_id,
    wli3.value as id_bb_global,
	icr.id_bb_company,
	seniority as seniority,
	wlf.lien_type as lien_type,
	concat(coalesce(wlf.stated_spread_type, ''), '+', li.libor_spread*1e4) as coupon,
	jpm.beg_cpn_rate *100 as current_coupon,
	coalesce(bim.company_corp_ticker, bim.ult_parent_corp_ticker, wlis.markit_ticker) as ticker
from
	dbo.sp_loan_index li
left join dbo.wso_loan_identifiers wli on
	li.pricing_id = wli.value
	and wli.type = 'LIN'
left join dbo.wso_loan_identifiers wli2 on
	wli.wso_data_facility_id = wli2.wso_data_facility_id
	and wli2.type = 'LoanX'
left join dbo.wso_loan_identifiers wli3 on
	wli.wso_data_facility_id = wli3.wso_data_facility_id
	and wli3.type = 'FIGI'
left join dbo.wso_loan_facility wlf on
	wli.wso_data_facility_id = wlf .wso_data_facility_id
left join dbo.wso_loan_bank_deal wlb on
	wlf.wso_data_bank_deal_id = wlb.wso_data_bank_deal_id
left join dbo.wso_loan_issuers wlis on
	wlis.wso_data_issuer_id = wlb.wso_data_issuer_id	
left join dbo.f_get_issuer_cross_ref(NULL) icr on
	wlis.wso_data_issuer_id = icr.wso_data_issuer_id
left join dbo.f_get_bond_issuer_master(NULL) bim on
	icr.id_bb_company = bim.id_bb_company
left join dbo.jpm_loan_index jpm on
	wli2.value = jpm.loanx 
	and jpm.asof_date = (SELECT MAX(asof_date) FROM dbo.jpm_loan_index)
where {}
'''

JPM_LOAN_SQL1 = '''
with jpm_loans as (
    select *
    from dbo.jpm_loan_index li
    where {}
),
relevant_tickers as (
    select distinct upper(trim(ticker)) as ticker
    from jpm_loans
    where ticker is not null
),
issuer_counts as (
    select
        upper(trim(company_corp_ticker)) as ticker,
        id_bb_company
    from dbo.v_bond_issuer_master bim
    inner join relevant_tickers rt
        on upper(trim(bim.company_corp_ticker)) = rt.ticker
    where bim.is_latest = true
        and bim.id_bb_company is not null
    group by upper(trim(company_corp_ticker)), id_bb_company
),
unique_issuers as (
    select ticker, max(id_bb_company) as id_bb_company
    from issuer_counts
    group by ticker
    having count(*) = 1
),
history_counts as (
    select
        upper(trim(bim.company_corp_ticker)) as ticker,
        bim.id_bb_company,
        count(*) as use_count,
        max(bim.knowledge_date) as knowledge_date,
        max(bim.load_date) as load_date
    from dbo.bond_issuer_master bim
    inner join relevant_tickers rt
        on upper(trim(bim.company_corp_ticker)) = rt.ticker
    where bim.id_bb_company is not null
    group by upper(trim(bim.company_corp_ticker)), bim.id_bb_company
),
history_ranked as (
    select
        *,
        row_number() over (
            partition by ticker
            order by use_count desc, knowledge_date desc, load_date desc, id_bb_company
        ) as rn
    from history_counts
),
bbg_issuer_by_ticker as (
    select
        rt.ticker,
        coalesce(ui.id_bb_company, hr.id_bb_company) as id_bb_company
    from relevant_tickers rt
    left join unique_issuers ui
        on rt.ticker = ui.ticker
    left join history_ranked hr
        on rt.ticker = hr.ticker and hr.rn = 1
)
select 
    distinct 
    li.loan_id as `id`,
    li.asof_date as asof_date,
    li.issuer_name as issuer_name,
    li.asset_name as facility_name,
    li.maturity_date as maturity_date,
    li.original_commitment as original_amount,
    li.calc_current_outstanding as current_outstanding,
    li.offer_spread/1e4 as libor_spread,
    NULL as original_libor_spread,
    li.offer_date as launch_date,
    NULL as initial_rating,
    li.consolidated_rating as current_rating,
    li.industry as sector,
    li.industry as lcd_broad_industry,
    li.ind_segment as narrow_industry,
    case
        when li.leveraged_mkt_wt > 0 then 'JPM_LEVERAGED'
        when li.sec_lien_mkt_wt > 0 then 'JPM_SEC_LIEN'
        when li.liquid_mkt_wt > 0 then 'JPM_LIQUID'
        when li.bb_b_mkt_wt > 0 then 'JPM_BB_B'
        when li.euro_mkt_wt > 0 then 'JPM_EURO'
        else null end as `index`,
    li.leveraged_mkt_wt+li.sec_lien_mkt_wt+li.liquid_mkt_wt+li.bb_b_mkt_wt+li.euro_mkt_wt as weights,
    li.prn_ret as market_return,
    li.int_ret as interest_return,
    li.tot_ret as total_return,
    li.currency as currency,
    li.country as country,
    li.country_tier as region,
    li.facility_id as facility_id,
    li.lin as pricing_id,
    li.loan_only_main_issuer,
    NULL as ccr_rating,
    li.libor_floor as libor_floor,
    NULL as fl_sl,
    li.covlite as covenant_light,
    li.load_date as load_date,
    li.knowledge_date as knowledge_date,
    li.cusip as cusip,
    (1.0/li.frequency) as effective_duration,
    li.loanx as loanx_id,
    li.bbg_id as bbg_id,
    bim.id_bb_company,
    li.seniority as seniority,
    case
        when lower(li.loan_segment) like '%second lien%' then 'Second Lien'
        when lower(li.loan_segment) like '%third lien%' then 'Third Lien'
        when lower(li.seniority) like '%senior secured%' then 'First Lien'
        else null end as lien_type,
    concat(coalesce(li.benchmark, ''), '+', li.offer_spread * 100) as coupon,
    li.beg_cpn_rate * 100 as current_coupon,
    li.ticker
from
	jpm_loans li
left join bbg_issuer_by_ticker bim
    on upper(trim(li.ticker)) = bim.ticker
'''

LOAN_PRICE_SQL = '''
select asof_date, loanx_id, evaluated_price from markit_pricing_data_loan
where {}
'''

CDS_SQL = '''
with bond_markit_tickers as
(
select distinct isin, ticker
from markit_pricing_data
where {}
)
select
    mcds.asof_date,
	mcds.ticker,
	mpd.isin,
	mcds.short_name as issuer_name,
	mcds.red_code,
	concat(mcds.red_code, '_', mcds.tenor) as instrument_id,
	mcds.sector as sector,
	mcds.implied_rating as rating,
	mcds.tier as seniority,
	mcds.tier,
	mcds.ccy as currency,
	mcds.doc_clause,
	mcds.tenor,
	mcds.conv_spread * 100 as yield_to_worst_conv,
	mcds.conv_spread * 100 as yield_to_maturity_conv,
	mcds.conv_spread * 10000 as oas,
	(100-mcds.upfront * 100) as price,
	mcds.running_coupon * 100 * 100 as coupon,
	mcds.running_coupon * 100 * 100 as current_coupon,
	cast(mcds.credit_dv01 as float)/ 1000 as spread_duration,
	cast(mcds.credit_dv01 as float)/ 1000 as effective_duration,
	mcds.idx_member,
	mcds.wkly_gn_sec4,
	mcds.wkly_contrct_sec4,
	mcds.gnx_trd,
	mcds.sec4_last_update,
	CAST(mcds.wkly_gn_sec4_1m_ma AS float) * 21 / 5 as gross_volume_1m,
    abs(CAST(mcds.wkly_net_notional AS float) - CAST(mcds.wkly_net_notional_1m AS float)) as volume_1m,
    cast(mcds.wkly_net_notional as float) as amt_outstanding_loc,
	mcds.wkly_gross_notional,
	mcds.tbl6_last_update,
	mcds.curve_liquidity_score_1m_ma as liquidity_score,
	mcds.dp as default_prob
from
	markit_cds mcds
left join bond_markit_tickers mpd on
mcds.ticker = mpd.ticker
where tenor in {}
and primary_curve = 'Y'
and primary_coupon = 'Y'
and ccy in {}
and curve_liquidity_score_1m_ma <= 4
and {}
'''

CDS_LIQUIDITY_5Y_SQL = '''
select
    mcds.asof_date,
    mcds.ticker,
    mcds.red_code,
    mcds.tier,
    mcds.ccy,
    mcds.doc_clause,
    mcds.tenor,
    mcds.primary_curve,
    mcds.primary_coupon,
    cast(mcds.curve_liquidity_score_1m_ma as double) as curve_liquidity_score_1m_ma
from
    markit_cds mcds
where
    tenor = '5Y'
    and primary_coupon = 'Y'
    and ccy in {}
    and {}
'''

CDS_PRICE_SQL = '''
select
    mcds.asof_date,
	mcds.ticker,
	mcds.red_code,
	mcds.tier,
	mcds.ccy as currency,
	mcds.doc_clause,
	mcds.tenor,
	(100-mcds.upfront*100) as price
from
	markit_cds mcds
where
	tenor in {}
	and doc_clause in ('XR14', 'MR14')
	and tier = 'SNRFOR'
	and primary_curve = 'Y'
	and primary_coupon = 'Y'
	and ccy in {}
	and curve_liquidity_score_1m_ma <= 4
	and {}
'''

BOND_OAS_PCTILE_SQL = '''
SELECT
	*
FROM
	(
	SELECT
		asof_date,
		isin_number,
		min(avg(oas)) OVER(PARTITION BY isin_number) AS oas_min,
		avg(oas) AS oas,
		max(avg(oas)) OVER(PARTITION BY isin_number) AS oas_max,
		100 * percent_rank() OVER(PARTITION BY isin_number
	ORDER BY
		avg(oas)) AS oas_pctile,
		RANK() OVER(PARTITION BY isin_number
	ORDER BY
		asof_date DESC) AS rk
	FROM
		ice_index_all
	WHERE
		asof_date BETWEEN '{}' AND '{}'
		AND ice_index_ticker IN {}
		AND frequency = 'DAILY'
	GROUP BY
		asof_date,
		isin_number
) t1
WHERE
	t1.rk = 1
ORDER BY
	asof_date DESC
'''

LOAN_OAS_PCTILE_SQL = '''
SELECT
	*
FROM
	(
	SELECT
		asof_date,
		loanx,
		min(avg(fl_mat_dm)) OVER(PARTITION BY loanx) AS oas_min,
		avg(fl_mat_dm) AS oas,
		max(avg(fl_mat_dm)) OVER(PARTITION BY loanx) AS oas_max,
		100 * percent_rank() OVER(PARTITION BY loanx
	ORDER BY
		avg(fl_mat_dm)) AS oas_pctile,
		RANK() OVER(PARTITION BY loanx
	ORDER BY
		asof_date DESC) AS rk
	FROM
		jpm_loan_index
	WHERE
		asof_date BETWEEN '{}' AND '{}'
		AND jpm_index_ticker IN ('USD', 'Non-USD')
	GROUP BY
		asof_date,
		loanx
) t1
WHERE
	t1.rk = 1
ORDER BY
	asof_date DESC
'''

LOAN_RISK_SQL = '''
SELECT
	loanx,
	avg(end_price) AS price,
	avg(end_cpn_rate) AS cpn_rate,
	avg(end_curr_yld*100) AS curr_yld,
	avg(fl_mat_dm) AS fl_mat_dm,
	avg(fw_mat_dm) AS fw_mat_dm,	
	avg(fl_3y_dm) AS fl_3y_dm,
	avg(fw_3y_dm) AS fw_3y_dm,
	avg(fl_mat_yld*100) AS fl_mat_yld,
	avg(fw_mat_yld*100) AS fw_mat_yld,	
	avg(fl_3y_yld*100) AS fl_3y_yld,
	avg(fw_3y_yld*100) AS fw_3y_yld,
	avg(fl_mat_moddur) AS fl_mat_moddur,
	avg(fw_mat_moddur) AS fw_mat_moddur,	
	avg(fl_3y_moddur) AS fl_3y_moddur,
	avg(fw_3y_moddur) AS fw_3y_moddur,
	avg(fl_mat_spd) AS fl_mat_spd,
	avg(fw_mat_spd) AS fw_mat_spd,	
	avg(fl_3y_spd) AS fl_3y_spd,
	avg(fw_3y_spd) AS fw_3y_spd
FROM
	jpm_loan_index
WHERE
	asof_date = '{}'
GROUP BY
	loanx
'''

CDS_PCTILE_SQL = '''
SELECT
	*
FROM
	(
	SELECT
		asof_date,
		ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor,
		min(avg(conv_spread) * 10000) OVER(PARTITION BY ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor) AS oas_min,
		avg(conv_spread) * 10000 AS oas,
		max(avg(conv_spread) * 10000) OVER(PARTITION BY ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor) AS oas_max,
		100 * percent_rank() OVER(PARTITION BY ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor
	ORDER BY
		avg(conv_spread)) AS oas_pctile,
		RANK() OVER(PARTITION BY ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor
	ORDER BY
		asof_date DESC) AS rk
	FROM
		markit_cds
	WHERE
		asof_date BETWEEN '{}' AND '{}'
		AND primary_curve = 'Y'
		AND primary_coupon = 'Y'
		and curve_liquidity_score_1m_ma <= 4
	GROUP BY
		asof_date,
		ticker,
		red_code,
		tier,
		ccy,
		doc_clause,
		tenor
) t1
WHERE
	t1.rk = 1
	AND doc_clause IN ('XR14', 'MR14')
	AND tier = 'SNRFOR'
	AND ccy IN ('USD', 'EUR' , 'GBP')
ORDER BY
	asof_date DESC
'''

ISSUER_BONDS = '''
select cast(asof_date as date) as asof_date, isin_number as instrument_id, 'Bond' as instrument_type,
ticker,
industry_lvl3_desc,
industry_lvl4_desc,
min(iso_currency_code) as currency,
min(subordination_type) as seniority,
avg(par_wtd_coupon) as coupon,
min(maturity_date) as maturity,
avg(current_period_coupon) as current_coupon,
avg(years_since_issue) as years_since_issue,
avg(face_value_usd) as amt_outstanding_usd, 
avg(yield_to_worst_conv) as yield_to_worst_conv, 
avg(oas) as oas, 
avg(price) as price
from ice_index_all
where
asof_date between '{}' and '{}'
and ice_index_ticker in {}
{}
group by asof_date, isin_number, ticker, industry_lvl3_desc, industry_lvl4_desc
'''

BOND_VOLUME = '''
SELECT
    effective_date,
	isin,
	sum(quantity)/1e6 AS gross_volume,
	sum(CASE WHEN side = 'B' THEN 1 ELSE 0 END * quantity)/1e6 AS buy_volume,
	sum(CASE WHEN side = 'S' THEN 1 ELSE 0 END * quantity)/1e6 AS sell_volume
FROM
	dbo.trace th
WHERE
	th.effective_date >= '2000-01-01'
	AND th.cancel_flag <> 'Y'
	AND th.asof_flag <> 'R'
	AND (effective_date between '{}' and '{}')
	{}	
GROUP BY
	effective_date, isin
'''

ISSUER_ID_SQL = """
select 
    company_corp_ticker, ult_parent_corp_ticker, 
    id_bb_company, id_bb_global_company, 
    id_bb_parent_co, id_bb_global_parent_co, 
    id_bb_ultimate_parent_co, id_bb_global_ultimate_parent_co
from f_get_bond_issuer_master(null)
where
    {}
"""

APO_PRICE_SQL = """
select asof_date, instrument_id, price, oas, yield_to_worst as yield_to_worst_conv
from gold.v_apo_price
where cast(asof_date as date) between '{}' and '{}'
"""
