import logging

import pandas as pd

from common.utils import dfutil
from common.utils import sqlutil, dtutil
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.financial_data import get_company_financials
from data_access.dbtables.factset.utils import normalize_df
from data_access.dbtables.factset.utils import normalize_factset_fin_df, get_ff_table_for_period_type
from data_access.utils import tosqllist

FS_PRICING_SQL = '''
WITH industry_peers AS (
SELECT 
	DISTINCT tr.ticker_region, fsc.FSYM_ID, ec.FACTSET_ENTITY_ID, ec.ENTITY_PROPER_NAME, i1.SIC_DESC , i2.FACTSET_INDUSTRY_DESC , i3.FACTSET_SECTOR_DESC
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
	JOIN FF_V3.FF_SEC_COVERAGE AS fsc ON fsc.FSYM_ID=tr.fsym_id
	JOIN SYM_V1.SYM_COVERAGE sc ON fsc.FSYM_ID = sc.FSYM_REGIONAL_ID 
	JOIN SYM_V1.SYM_SEC_ENTITY sse ON sc.FSYM_ID = sse.FSYM_ID 
	JOIN ENT_V1.ENT_ENTITY_COVERAGE ec ON ec.FACTSET_ENTITY_ID = sse.FACTSET_ENTITY_ID
	JOIN SYM_V1.SYM_ENTITY_SECTOR ses ON ec.FACTSET_ENTITY_ID = ses.FACTSET_ENTITY_ID 
	JOIN REF_V2.SIC_MAP i1 ON ses.PRIMARY_SIC_CODE = i1.SIC_CODE  
	JOIN REF_V2.FACTSET_INDUSTRY_MAP i2 ON ses.INDUSTRY_CODE  = i2.FACTSET_INDUSTRY_CODE  
	JOIN REF_V2.FACTSET_SECTOR_MAP i3 ON ses.SECTOR_CODE  = i3.FACTSET_SECTOR_CODE 
WHERE 
	ec.ISO_COUNTRY IN ('US', 'CA', 'DE', 'GB')
	AND fsc.FA_LATEST_ANN_UPDATE >= YEAR(getdate())-5
	AND ec.FACTSET_ENTITY_ID IN {0}
), pricing_data AS (
SELECT tr.TICKER_REGION, sm.PROPER_NAME, sm.CURRENCY, gp.PRICE_DATE, gp.ONE_YR_PCT, DATEADD(DAY, -1, DATE_TRUNC('QUARTER', gp.PRICE_DATE)) as PREV_QUARTER_END,
	ROW_NUMBER() OVER(PARTITION BY tr.TICKER_REGION, DATEADD(DAY, -1, DATE_TRUNC('QUARTER', gp.PRICE_DATE)) ORDER BY gp.PRICE_DATE DESC) as rn
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
     JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON tr.fsym_id=sm_regional.FSYM_ID
     JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm 
        ON sm.FSYM_SECURITY_ID=sm_regional.FSYM_SECURITY_ID 
            AND sm.FREF_EXCHANGE_CODE=sm_regional.FREF_EXCHANGE_CODE
            AND sm.FIRST_PRICE_DATE IS NOT NULL
     JOIN FACTSET_DB.FGP_V1.FGP_GLOBAL_PRICES gp ON gp.FSYM_ID=sm.FSYM_ID
     LEFT JOIN FACTSET_DB.FGP_V1.FGP_CA_ADJ_FACTORS cf ON cf.FSYM_ID = sm.FSYM_ID AND cf.EFFECTIVE_DATE = gp.PRICE_DATE 
     LEFT JOIN FACTSET_DB.FGP_V1.FGP_CA_EVENTS  ce ON ce.FSYM_ID = sm.FSYM_ID AND ce.EFFECTIVE_DATE = gp.PRICE_DATE 
     JOIN industry_peers ip ON ip.ticker_region=tr.TICKER_REGION
WHERE 
    gp.PRICE_DATE BETWEEN '{2}' AND '{3}'
), fin_data AS (
<fin_sql>
)
SELECT pd.PRICE_DATE, fd.DATE, fd.filing_date,  ip.ticker_region, ip.FACTSET_ENTITY_ID,  ip.ENTITY_PROPER_NAME, ip.SIC_DESC , ip.FACTSET_INDUSTRY_DESC , ip.FACTSET_SECTOR_DESC,
fd.FF_MKT_VAL AS marketCap,  FF_COM_SHS_OUT AS sharesOutstanding, tev AS TEV, ONE_YR_PCT AS totalReturn1Y
{1} 
FROM fin_data fd
    JOIN industry_peers ip ON fd.ticker_region=ip.ticker_region 
    JOIN pricing_data pd ON pd.ticker_region=fd.ticker_region AND pd.PREV_QUARTER_END=fd.DATE AND pd.rn=1
ORDER BY fd.DATE DESC, fd.filing_date DESC
    '''

PRICING_FINANCIALS_QUERY = '''
SELECT 'original' AS filing_type, tr.ticker_region, proper_name, f1.FSYM_ID, f1.DATE, 
GREATEST(f1.FF_SOURCE_BS_DATE, f1.FF_SOURCE_CF_DATE, f1.FF_SOURCE_IS_DATE) AS filing_date, f1.CURRENCY, f1.FF_REPORT_FREQ_CODE, 
FF_MKT_VAL, FF_MKT_VAL - FF_CASH_ST + FF_DEBT AS TEV, FF_COM_SHS_OUT
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
     JOIN FACTSET_DB.ff_v3.ff_sec_map AS sm ON sm.fsym_id=tr.fsym_id
     JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc ON sm.fsym_id=sc.fsym_regional_id
     JOIN FACTSET_DB.FF_V3.FF_BASIC_{4} AS f1 ON f1.fsym_id=sm.fsym_id
     JOIN FACTSET_DB.ff_v3.FF_ADVANCED_{4} AS f2 ON f2.fsym_id=f1.fsym_id AND f2.date=f1.date AND f2.ff_restate_ind=f1.ff_restate_ind
     JOIN FACTSET_DB.ff_v3.FF_BASIC_DER_{4} AS f3 ON f3.fsym_id=f1.fsym_id AND f3.date=f1.date
     JOIN FACTSET_DB.ff_v3.FF_ADVANCED_DER_{4} AS f4 ON f4.fsym_id=f1.fsym_id AND f4.date=f1.date
     JOIN industry_peers ip ON ip.ticker_region=tr.TICKER_REGION
WHERE 
    f1.ff_restate_ind = 0 AND f1.DATE BETWEEN '{2}' AND '{3}'
'''
RESTATED_PRICING_FINANCIALS_QUERY = PRICING_FINANCIALS_QUERY.replace('_{4}', '_R_{4}') \
    .replace("'original' AS filing_type", "'restated' AS filing_type") \
    .replace('f1.ff_restate_ind = 0 AND ', '') \
    .replace(' AND f2.ff_restate_ind=f1.ff_restate_ind', '')


def get_pricing(company_id, formula_metric, from_date, to_date, period_type):
    logging.info(f'fetching pricing data')

    fin_df = get_company_financials(company_id, from_date, to_date, period_type, formula_metric).reset_index(drop=True)
    fin_df['period_end_date'] = fin_df['period_end_date'].map(dtutil.todate)

    fs_ff_table = get_ff_table_for_period_type('qf')
    company_id = tosqllist(company_id)

    financials_sql = PRICING_FINANCIALS_QUERY
    financials_sql = '\nUNION\n'.join([financials_sql, RESTATED_PRICING_FINANCIALS_QUERY])

    pricing_sql = FS_PRICING_SQL.replace('<fin_sql>', financials_sql)

    df = sqlutil.readdfsql(pricing_sql.format(company_id, '', from_date, to_date, fs_ff_table),
                           app=APP)
    df['DATE'] = df['DATE'].map(dtutil.todate)

    df = pd.merge(df, fin_df.drop(columns=['filing_date']).rename(columns={'company_id': 'FACTSET_ENTITY_ID',
                                                                           'company_name': 'ENTITY_PROPER_NAME',
                                                                           'period_end_date': 'DATE',
                                                                           'ticker_region': 'TICKER_REGION'}),
                  on=['FACTSET_ENTITY_ID', 'DATE', 'TICKER_REGION', 'ENTITY_PROPER_NAME'], how='left')

    id_vars = df.columns.tolist()
    rename_dict = {'PRICE_DATE': 'pricingDate', 'MARKETCAP': 'marketCap',
                   'SHARESOUTSTANDING': 'sharesOutstanding',
                   'TOTALRETURN1Y': 'totalReturn1Y', 'TEV': 'TEV'}
    df = normalize_factset_fin_df(df, id_vars=id_vars, rename_dict=rename_dict)
    return df


EQ_SUBSET_SQL_TICKER_REGION = '''
SELECT
    sm.FSYM_ID,
    tr.TICKER_REGION,
    tr2.BBG_TICKER,
    row_number() OVER(PARTITION BY tr.ticker_region, tr2.bbg_ticker ORDER BY sm.FIRST_PRICE_DATE) AS rk
FROM 
    FACTSET_DB.sym_v1.sym_ticker_region tr
JOIN 
    FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON tr.fsym_id = sm_regional.FSYM_ID
LEFT JOIN
  FACTSET_DB.sym_v1.sym_bbg tr2 ON tr2.fsym_id = sm_regional.FSYM_ID
JOIN 
    FGP_V1.FGP_SEC_COVERAGE AS sm 
    ON sm.FSYM_SECURITY_ID = sm_regional.FSYM_SECURITY_ID 
    AND sm.FREF_EXCHANGE_CODE = sm_regional.FREF_EXCHANGE_CODE
    AND sm.PRICE_FLAG 
WHERE 
    tr.ticker_region IN {0}
    OR
    tr2.bbg_ticker IN {0}
'''

EQ_SUBSET_SQL_TICKER_CURRENCY = '''
SELECT
  sm.FSYM_ID,
  tr.TICKER_REGION,
  tr2.BBG_TICKER,
  row_number() OVER(PARTITION BY concat(SPLIT(tr.ticker_region, '-')[0],'-',sm.currency) ORDER BY sm.FIRST_PRICE_DATE) AS rk
FROM
  FACTSET_DB.sym_v1.sym_ticker_region tr
JOIN
  FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON tr.fsym_id = sm_regional.FSYM_ID
LEFT JOIN
  FACTSET_DB.sym_v1.sym_bbg tr2 ON tr2.fsym_id = sm_regional.FSYM_ID
JOIN
  FGP_V1.FGP_SEC_COVERAGE AS sm
  ON sm.FSYM_SECURITY_ID = sm_regional.FSYM_SECURITY_ID
  AND sm.FREF_EXCHANGE_CODE = sm_regional.FREF_EXCHANGE_CODE
  AND sm.PRICE_FLAG
WHERE
  concat(SPLIT(tr.ticker_region, '-')[0],'-',sm.currency) IN {0}
'''

EQ_PRICES_SQL = '''
WITH FsymRegionMapping AS (
    {0}
), _DailyReturns AS (
    SELECT
        gp.FSYM_ID,
        gp.PRICE_DATE,
        gp.PRICE,
        gp.ONE_DAY_PCT/100 - round(gp.PRICE/LEAD(case when gp.PRICE=0 then 1 else gp.PRICE end) OVER (PARTITION BY gp.FSYM_ID ORDER BY gp.PRICE_DATE DESC) - 1, 6) AS ONE_DAY_DIV,
        SQRT(252) * STDDEV(gp.ONE_DAY_PCT / 100) OVER (PARTITION BY gp.FSYM_ID ORDER BY gp.PRICE_DATE ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS ONE_YEAR_VOLATILITY,
        SQRT(252) * STDDEV(gp.ONE_DAY_PCT / 100) OVER (PARTITION BY gp.FSYM_ID ORDER BY gp.PRICE_DATE ROWS BETWEEN 62 PRECEDING AND CURRENT ROW) AS THREE_MONTH_VOLATILITY,
        EXP(SUM(LN(1 + ((case when gp.ONE_DAY_PCT <= -100 then 0 else gp.ONE_DAY_PCT end) / 100))) OVER (PARTITION BY gp.FSYM_ID ORDER BY gp.PRICE_DATE ROWS BETWEEN 4 PRECEDING AND CURRENT ROW))-1 AS ONE_WEEK_PCT
    FROM 
        FsymRegionMapping frm
    JOIN 
        FGP_V1.FGP_GLOBAL_PRICES gp ON frm.FSYM_ID = gp.FSYM_ID AND frm.rk=1
    WHERE 
        gp.PRICE_DATE BETWEEN DATEADD(day, -365, '{1}') AND '{2}'
),  DailyReturns AS (
SELECT EXP(sum(LN(1+CASE WHEN abs(ONE_DAY_DIV) >= 1 THEN 0 ELSE ONE_DAY_DIV END)) OVER(PARTITION BY fsym_id ORDER BY PRICE_DATE ROWS BETWEEN 251 PRECEDING AND CURRENT ROW))-1 AS ONE_YEAR_DIVIDEND,
* FROM _DailyReturns
),
price_sh AS (
SELECT
	gp.FSYM_ID,
	sm_regional.FSYM_SECURITY_ID,
    {3},
    gp.PRICE_DATE,
    gp.CURRENCY AS EQUITY_CURRENCY,
    gp.PRICE as EQUITY_PX,
    gp.ONE_DAY_PCT / 100 as ONE_DAY_PCT,
    gp.WTD_PCT / 100 AS WTD_PCT,
    gp.MTD_PCT / 100 AS MTD_PCT,
    gp.YTD_PCT / 100 AS YTD_PCT,
    gp.ONE_MTH_PCT / 100 AS ONE_MONTH_PCT,
    gp.THREE_MTH_PCT / 100 AS THREE_MONTH_PCT,
    gp.SIX_MTH_PCT / 100 AS SIX_MONTH_PCT,
    gp.ONE_YR_PCT / 100 AS ONE_YR_PCT,
    gp.VWAP,
    gp.VOLUME,
    gp.TURNOVER,
    ONE_WEEK_PCT,
    dr.ONE_YEAR_DIVIDEND,
    dr.ONE_YEAR_VOLATILITY,
    dr.THREE_MONTH_VOLATILITY,
    sh.adj_shares_outstanding,
    sh.adj_shares_outstanding * gp.PRICE AS eq_mkt_cap_mm,
    sh.report_date AS shares_report_date,
    row_number() over(PARTITION BY {3},gp.price_date ORDER BY sh.REPORT_DATE desc) AS sh_rk
FROM 
    FsymRegionMapping frm
JOIN 
    FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON frm.FSYM_ID = sm_regional.FSYM_ID AND frm.rk=1
JOIN 
    FGP_V1.FGP_SEC_COVERAGE AS sm 
    ON sm.FSYM_SECURITY_ID = sm_regional.FSYM_SECURITY_ID 
    AND sm.FREF_EXCHANGE_CODE = sm_regional.FREF_EXCHANGE_CODE
    AND sm.FIRST_PRICE_DATE IS NOT NULL
JOIN 
    FGP_V1.FGP_GLOBAL_PRICES gp ON gp.FSYM_ID = sm.FSYM_ID
JOIN
    DailyReturns dr ON gp.FSYM_ID = dr.FSYM_ID AND gp.PRICE_DATE = dr.PRICE_DATE
LEFT JOIN
	fgp_v1.fgp_shares_sec_hist sh ON sm_regional.fsym_security_id=sh.FSYM_SECURITY_ID
	AND sh.report_date BETWEEN dateadd(DAY, -366, gp.PRICE_DATE) AND gp.PRICE_DATE
WHERE 
    gp.PRICE_DATE BETWEEN '{1}' and '{2}'
)
SELECT * FROM price_sh
WHERE sh_rk=1
ORDER BY 
    PRICE_DATE DESC
'''


def get_ticker_prices(fromdt, todt, tickers, has_ccy=False):
    fromdt = dtutil.todate(fromdt)
    todt = dtutil.todate(todt)
    tickers = [str(x).upper() for x in dfutil.tolist(tickers)]
    dfs = []
    ticker_column = 'frm.TICKER_REGION, frm.BBG_TICKER'
    for _t in dfutil.chunks_list(set(tickers), 10_000):
        if has_ccy:
            tr_subset_sql = EQ_SUBSET_SQL_TICKER_CURRENCY.format(tosqllist(_t))
        else:
            tr_subset_sql = EQ_SUBSET_SQL_TICKER_REGION.format(tosqllist(_t))

        dfs.append(sqlutil.readdfsql(EQ_PRICES_SQL.format(tr_subset_sql, fromdt, todt, ticker_column), app=APP))

    df = pd.concat(dfs)
    df = normalize_df(df)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('dev')
    company_id = '000C7F-E'

    # temp_df = get_pricing(company_id, 'ff_sales', '2022-01-01', '2023-05-31', 'q')
    temp_df = get_ticker_prices('20250226', '20250428', ['aapl us'])
    # temp_df = get_ticker_prices('20240516', '20240516', ['aapl-usd', 'iwm-usd'], has_ccy=True)
    logging.info(temp_df.to_string())
