import logging

import pandas as pd

from common.utils import sqlutil, dtutil, dfutil
from common.utils.dfutil import tolist
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.const import CUSTOM_FF_FIELDS, DEFAULT_FF_METRIC_COLS, NON_BS_ITEMS, \
    NON_RESTATED_ITEMS, CAPIQ_FACTSET_MAP
from data_access.dbtables.factset.utils import get_ff_table_for_period_type, get_metric_cols_csv, \
    normalize_factset_fin_df, \
    get_metric_cols, get_non_bs_metric_cols, add_custom_ff_fields, get_metrics_reqd_by_custom_ff, reverse_map_columns, \
    normalize_dcs_df, get_non_restated_cols
from data_access.dbtables.factset.utils import normalize_df
from data_access.utils import tosqllist, join_where_clauses, get_where_clause

FINANCIALS_QUERY = '''
SELECT 'original' AS filing_type, tr.ticker_region, f1.FSYM_ID, ec.factset_entity_id, ec.entity_proper_name, 
f1.DATE, GREATEST(f1.FF_SOURCE_BS_DATE, f1.FF_SOURCE_CF_DATE, f1.FF_SOURCE_IS_DATE) AS filing_date, f1.CURRENCY, f1.FF_REPORT_FREQ_CODE
{4}
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
     JOIN FACTSET_DB.ff_v3.ff_sec_map AS sm ON sm.fsym_id=tr.fsym_id
     JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc ON sc.fsym_id=sc.fsym_primary_equity_id AND sm.fsym_id=sc.fsym_primary_listing_id
     JOIN FACTSET_DB.SYM_V1.SYM_SEC_ENTITY sse ON sc.fsym_id=sse.fsym_id
     JOIN FACTSET_DB.ENT_V1.ENT_ENTITY_COVERAGE ec ON sse.factset_entity_id=ec.factset_entity_id
     JOIN FACTSET_DB.FF_V3.FF_BASIC_{3} AS f1 ON f1.fsym_id=sm.fsym_id
     JOIN FACTSET_DB.ff_v3.FF_ADVANCED_{3} AS f2 ON f2.fsym_id=f1.fsym_id AND f2.date=f1.date AND f2.ff_restate_ind=f1.ff_restate_ind
     JOIN FACTSET_DB.ff_v3.FF_BASIC_DER_{3} AS f3 ON f3.fsym_id=f1.fsym_id AND f3.date=f1.date
     JOIN FACTSET_DB.ff_v3.FF_ADVANCED_DER_{3} AS f4 ON f4.fsym_id=f1.fsym_id AND f4.date=f1.date
WHERE 
    f1.ff_restate_ind = 0 AND f1.DATE BETWEEN '{1}' AND '{2}'
    AND {0}
'''

RESTATED_FINANCIALS_QUERY = FINANCIALS_QUERY.replace('_{3}', '_R_{3}') \
    .replace("'original' AS filing_type", "'restated' AS filing_type") \
    .replace('f1.ff_restate_ind = 0 AND ', '') \
    .replace(' AND f2.ff_restate_ind=f1.ff_restate_ind', '')

NON_LTM_COLUMNS_REPLACE = {'GREATEST(f1.FF_SOURCE_BS_DATE, f1.FF_SOURCE_CF_DATE, f1.FF_SOURCE_IS_DATE)': 'f1.DATE',
                           ', f1.FF_REPORT_FREQ_CODE': '',
                           ' AND f2.ff_restate_ind=f1.ff_restate_ind': '',
                           'f1.ff_restate_ind = 0 AND ': '',
                           ' AND f2.ff_restate_ind=f1.ff_restate_ind': ''}

FILING_DATE_QUERY = '''
SELECT ec.factset_entity_id,
max(GREATEST(f1.FF_SOURCE_BS_DATE, f1.FF_SOURCE_CF_DATE, f1.FF_SOURCE_IS_DATE)) AS filingDate,
max(f1.DATE) AS periodEndDate
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
     JOIN FACTSET_DB.ff_v3.ff_sec_map AS sm ON sm.fsym_id=tr.fsym_id
     JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc ON sc.fsym_id=sc.fsym_primary_equity_id AND sm.fsym_id=sc.fsym_primary_listing_id
     JOIN FACTSET_DB.SYM_V1.SYM_SEC_ENTITY sse ON sc.fsym_id=sse.fsym_id
     JOIN FACTSET_DB.ENT_V1.ENT_ENTITY_COVERAGE ec ON sse.factset_entity_id=ec.factset_entity_id
     JOIN FACTSET_DB.FF_V3.FF_BASIC_QF AS f1 ON f1.fsym_id=sm.fsym_id
WHERE 
    ec.factset_entity_id IN {}
GROUP BY 
	ec.factset_entity_id, tr.ticker_region
'''


def _combine_multi_period_dfs(dfs):
    completed_ids = set()
    ret_dfs = []
    for _df in dfs:
        ret_dfs.append(_df[~_df['company_id'].isin(completed_ids)])
        completed_ids |= set(_df['company_id'].unique().tolist())

    ret_df = pd.concat(ret_dfs)
    return ret_df


def get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, dataitems=None):
    from_dt = dtutil.todate(from_dt)
    to_dt = dtutil.todate(to_dt)
    dataitems = dataitems or 'NULL'
    metric_cols = get_metric_cols(dataitems, metric_map=CAPIQ_FACTSET_MAP,
                                  all_metrics=DEFAULT_FF_METRIC_COLS + NON_BS_ITEMS + NON_RESTATED_ITEMS)
    if period_type.lower()[0] == 'l':
        non_bs_metric_cols = set(get_non_bs_metric_cols())
        non_bs_metric_cols = set(metric_cols) & non_bs_metric_cols

        non_restated_cols = set(get_non_restated_cols())
        non_restated_cols = set(metric_cols) & non_restated_cols

        bs_metric_cols = set(metric_cols) - non_bs_metric_cols - non_restated_cols

        df_bs = pd.DataFrame()
        df_non_bs = pd.DataFrame()
        df_non_restated = pd.DataFrame()

        if (len(bs_metric_cols) > 0):
            df_bs_qf = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'qf', list(bs_metric_cols))
            df_bs_saf = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'saf', list(bs_metric_cols))
            df_bs_af = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'af', list(bs_metric_cols))
            df_bs = _combine_multi_period_dfs([df_bs_qf, df_bs_saf, df_bs_af])

        if (len(non_restated_cols) > 0):
            df_non_restated_qf = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'qf',
                                                         list(non_restated_cols),
                                                         non_restated=True)
            df_non_restated_saf = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'saf',
                                                          list(non_restated_cols),
                                                          non_restated=True)
            df_non_restated_af = _get_company_financials(company_id_or_ticker, from_dt, to_dt, 'af',
                                                         list(non_restated_cols),
                                                         non_restated=True)
            df_non_restated = _combine_multi_period_dfs([df_non_restated_qf, df_non_restated_saf, df_non_restated_af])

        if (len(non_bs_metric_cols) > 0):
            df_non_bs = _get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type,
                                                list(non_bs_metric_cols))

        df = pd.concat([df_bs, df_non_bs, df_non_restated], ignore_index=True)
    else:
        non_restated_cols = set(get_non_restated_cols())
        non_restated_cols = set(metric_cols) & non_restated_cols

        restated_metric_cols = set(metric_cols) - non_restated_cols

        df_non_restated = pd.DataFrame()
        df_restated = pd.DataFrame()

        if (len(restated_metric_cols) > 0):
            df_restated = _get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type,
                                                  restated_metric_cols)
        if (len(non_restated_cols) > 0):
            df_non_restated = _get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type,
                                                      non_restated_cols, non_restated=True)

        df = pd.concat([df_restated, df_non_restated], ignore_index=True)

    if (len(df) > 0) and (dataitems != 'NULL'):
        df = df[df['dataitem_id'].isin(tolist(dataitems)) | df['factset_id'].isin(tolist(dataitems))]

    return df


def _get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, metric_cols, non_restated=False):
    logging.info(f'fetching company_financials for company id: {company_id_or_ticker}')
    custom_ff_fields = {k: v for k, v in CUSTOM_FF_FIELDS.items() if k in metric_cols}
    metric_cols = [x for x in metric_cols if x not in custom_ff_fields]
    metrics_reqd_by_custom_ff = get_metrics_reqd_by_custom_ff([v[0] for _, v in custom_ff_fields.items()],
                                                              CUSTOM_FF_FIELDS)

    formula_metric_csv = get_metric_cols_csv(metric_cols + metrics_reqd_by_custom_ff, CAPIQ_FACTSET_MAP,
                                             DEFAULT_FF_METRIC_COLS + NON_BS_ITEMS + NON_RESTATED_ITEMS,
                                             start_char=',')
    fs_ff_table = get_ff_table_for_period_type(period_type)

    company_id_clause = get_where_clause('ec.factset_entity_id', company_id_or_ticker)
    ticker_region_clause = get_where_clause('tr.ticker_region', company_id_or_ticker)
    ticker_clause = get_where_clause("SPLIT_PART(tr.ticker_region, '-', 1)", company_id_or_ticker)
    where_clause = join_where_clauses(' OR ', [company_id_clause, ticker_region_clause, ticker_clause])

    financials_sql = FINANCIALS_QUERY
    if (period_type.lower()[0] != 'l') and not non_restated:
        financials_sql = '\nUNION\n'.join([financials_sql, RESTATED_FINANCIALS_QUERY])
    else:
        for x, r in NON_LTM_COLUMNS_REPLACE.items():
            financials_sql = financials_sql.replace(x, r)

    if fs_ff_table is None:
        return pd.DataFrame()

    df = sqlutil.readdfsql(financials_sql.format(where_clause, from_dt, to_dt, fs_ff_table, formula_metric_csv),
                           app=APP)
    id_vars = [x for x in df.columns if x.upper() not in formula_metric_csv.upper().split(',')]

    df = normalize_factset_fin_df(df, id_vars=id_vars)
    df['period_type'] = fs_ff_table

    if len(custom_ff_fields) > 0:
        df = add_custom_ff_fields(df, custom_ff_fields, 'dataitem_id',
                                  ['metrics', 'dataitem_name'],
                                  'value',
                                  ['company_id'],
                                  ['period_end_date', 'qtr', 'filing_date'],
                                  other_field_name_cols=['formula_metric'],
                                  other_field_value_cols=['dataitem_value'])
        df = reverse_map_columns(df, metric_map=CAPIQ_FACTSET_MAP, metric_col1='capiq_id', metric_col2='factset_id')

    return df.reset_index(drop=True)


DCS_DETAIL_SQL = '''
SELECT
	dd.DETAIL_ID,
	dd.CURRENCY,
	dd.FACTSET_ENTITY_ID,
	dd.FACTSET_ISSUER_ENTITY_ID,
	ec.ENTITY_PROPER_NAME,
	tr.ticker_region,
	dd.REPORT_DATE,
	df.PUBLICATION_DATE,
	df.FILING_TYPE,
	df.REPORTING_PERIOD_CODE,
	dd.INSTRUMENT_ID,
	isn.ISIN,
	bbg.BBG_ID,
	bbg.BBG_TICKER,
	dd.INSTRUMENT_DESCRIPTION,
	dd.ISSUE_DATE,
	dd.MATURITY_YEAR_DAY,
	imap.ISSUE_TYPE_DESC,
	dmap.DEBT_DESC,
	dsmap.SENIORITY_DESC,
	dd.OS_AMOUNT,
	dd.AVAIL_AMOUNT,
	dd.BORROWING_BASE,
	dmap.ISSUE_TYPE,
	dd.DEBT_CODE,
	dmap.SENIORITY_CODE
FROM
	FACTSET_DB.DCS_V2.DCS_DETAILS AS dd
JOIN FACTSET_DB.DCS_V2.DCS_FILINGS df ON
	dd.FACTSET_ENTITY_ID = df.FACTSET_ENTITY_ID
	AND dd.REPORT_DATE = df.REPORT_DATE
	AND dd.CURRENCY = df.CURRENCY
LEFT JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc1 ON
	dd.instrument_id = sc1.FSYM_ID
LEFT JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc2 ON 
	sc1.fsym_primary_equity_id = sc2.fsym_id
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region AS tr ON
	tr.fsym_id = sc2.fsym_regional_id
LEFT JOIN FACTSET_DB.ff_v3.ff_sec_map AS sm 
     	ON
	sm.fsym_id = tr.fsym_id
LEFT JOIN FACTSET_DB.SYM_V1.SYM_ISIN isn ON
	isn.fsym_id = dd.instrument_id
LEFT JOIN FACTSET_DB.SYM_V1.SYM_BBG bbg ON
	bbg.fsym_id = dd.instrument_id
LEFT JOIN FACTSET_DB.ENT_V1.ENT_ENTITY_COVERAGE ec ON
	dd.FACTSET_ENTITY_ID = ec.FACTSET_ENTITY_ID
LEFT JOIN FACTSET_DB.REF_V2.DCS_DEBT_MAP AS dmap
  ON
	dd.debt_code = dmap.debt_code
LEFT JOIN FACTSET_DB.REF_V2.DCS_SENIORITY_MAP AS dsmap
  ON
	dsmap.SENIORITY_CODE = dmap.SENIORITY_CODE
LEFT JOIN FACTSET_DB.ref_v2.issue_type_map AS imap ON
	imap.issue_type_code = dmap.ISSUE_TYPE
WHERE
	dd.ACTIVE = TRUE
	AND dd.FACTSET_ENTITY_ID IN {}
	AND dd.REPORT_DATE BETWEEN '{}' AND '{}'
ORDER BY
	dd.REPORT_DATE DESC,
	dmap.ISSUE_TYPE,
	dmap.DEBT_DESC,
	dd.INSTRUMENT_DESCRIPTION DESC
'''


def get_company_dcs_detail(company_id, from_dt, to_dt):
    logging.info(f'fetching company dcs for company id: {company_id}')
    sql = DCS_DETAIL_SQL.format(tosqllist(company_id), from_dt, to_dt)
    df = sqlutil.readdfsql(sql, app=APP)
    df = _map_apo_debt_group(df)
    df = normalize_dcs_df(df)
    return df


DCS_SUMMARY_SQL = '''
SELECT
	ds.FACTSET_ENTITY_ID,
	ec.ENTITY_PROPER_NAME,
	tr.ticker_region,
	ds.REPORT_DATE,
	df.PUBLICATION_DATE,
	df.FILING_TYPE,
	df.REPORTING_PERIOD_CODE,
	ds.SUMMARY_CODE,
	dc.SUMMARY_DESC,
	dm.CATEGORY_DESC,
	ds.CURRENCY,
	ds.VALUE
FROM
	FACTSET_DB.DCS_V2.DCS_SUMMARY_VALUES AS ds
JOIN FACTSET_DB.ref_v2.DCS_SUMMARY_MAP AS dc ON
	ds.summary_code = dc.summary_code
JOIN FACTSET_DB.ref_v2.DCS_CATEGORY_MAP dm ON
	dm.CATEGORY_CODE = dc.CATEGORY_CODE
JOIN FACTSET_DB.DCS_V2.DCS_FILINGS df ON
	ds.FACTSET_ENTITY_ID = df.FACTSET_ENTITY_ID
	AND ds.REPORT_DATE = df.REPORT_DATE
	AND ds.CURRENCY = df.CURRENCY
JOIN FACTSET_DB.ENT_V1.ENT_ENTITY_COVERAGE ec ON
	ds.FACTSET_ENTITY_ID = ec.FACTSET_ENTITY_ID
JOIN FACTSET_DB.SYM_V1.SYM_SEC_ENTITY se ON
	ds.FACTSET_ENTITY_ID = se.FACTSET_ENTITY_ID
JOIN FACTSET_DB.SYM_V1.SYM_COVERAGE sc ON
	se.FSYM_ID = sc.FSYM_ID
	AND (sc.FSYM_ID = sc.FSYM_PRIMARY_EQUITY_ID
		OR sc.FSYM_PRIMARY_EQUITY_ID IS NULL)
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region AS tr ON
	tr.fsym_id = sc.fsym_regional_id
WHERE
	ds.FACTSET_ENTITY_ID IN {}
	AND ds.REPORT_DATE BETWEEN '{}' AND '{}'
ORDER BY
	ds.REPORT_DATE DESC,
	dm.CATEGORY_DESC,
	dc.SUMMARY_DESC
'''


def get_company_dcs_summary(company_id, from_dt, to_dt, totals=None):
    logging.info(f'fetching company dcs for company id: {company_id}')
    sql = DCS_SUMMARY_SQL.format(tosqllist(company_id), from_dt, to_dt)
    df = sqlutil.readdfsql(sql, app=APP)
    if totals is not None:
        total_flag = df['SUMMARY_DESC'].str.lower().str.contains('total')
        if totals:
            df = df[total_flag]
        else:
            df = df[~total_flag]
    df = normalize_dcs_df(df)
    return df


def get_latest_filing_date(company_ids):
    query = FILING_DATE_QUERY.format(tosqllist(company_ids))
    df = sqlutil.readdfsql(query, app=APP)

    df.columns = df.columns.map(lambda x: x.lower())

    return df


def _map_apo_debt_group(df):
    df['apo_debt_group'] = 'Other'

    SUB_CODES = ['CB', 'CU', 'CX', 'JS', 'SB', 'SO']
    df.loc[df[df['SENIORITY_CODE'].isin(SUB_CODES)].index, 'apo_debt_group'] = 'SUB'

    SR_UNSEC_CODES = ['SU', 'CS']
    df.loc[df[df['SENIORITY_CODE'].isin(SR_UNSEC_CODES)].index, 'apo_debt_group'] = 'SR_UNSEC'

    SR_SEC_CODES = ['SS', 'CL']
    df.loc[df[df['SENIORITY_CODE'].isin(SR_SEC_CODES)].index, 'apo_debt_group'] = 'SR_SEC'

    FIRST_LIEN_GROUPS = [{'DEBT_CODE': 'BD_S1', 'ISSUE_TYPE': 'BD', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'ST_FA', 'ISSUE_TYPE': 'CA', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'ST_FC', 'ISSUE_TYPE': 'CA', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'ST_FF', 'ISSUE_TYPE': 'CA', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'ST_FR', 'ISSUE_TYPE': 'CA', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'ST_RP', 'ISSUE_TYPE': 'CA', 'SENIORITY_CODE': 'CL'},
                         {'DEBT_CODE': 'BD_FM', 'ISSUE_TYPE': 'FM', 'SENIORITY_CODE': 'SS'},
                         {'DEBT_CODE': 'TL_S1', 'ISSUE_TYPE': 'LN', 'SENIORITY_CODE': 'SS'}]
    df_1l = pd.DataFrame(FIRST_LIEN_GROUPS)
    merged = df.merge(df_1l, on=df_1l.columns.tolist(), how='left', indicator=True)
    df.loc[df[merged['_merge'] == 'both'].index, 'apo_debt_group'] = '1L'

    return df


REPORT_DATE_SQL = '''
WITH FsymRegionMapping AS (
    SELECT
        sm.FSYM_ID,
        tr.TICKER_REGION,
        rank() OVER(PARTITION BY tr.ticker_region ORDER BY sm.FIRST_PRICE_DATE) AS rk
    FROM 
        FACTSET_DB.sym_v1.sym_ticker_region tr
    JOIN 
        FGP_V1.FGP_SEC_COVERAGE AS sm_regional ON tr.fsym_id = sm_regional.FSYM_ID
    JOIN 
        FGP_V1.FGP_SEC_COVERAGE AS sm 
        ON sm.FSYM_SECURITY_ID = sm_regional.FSYM_SECURITY_ID 
        AND sm.FREF_EXCHANGE_CODE = sm_regional.FREF_EXCHANGE_CODE
        AND sm.PRICE_FLAG 
    WHERE 
        tr.ticker_region IN {0}
)
SELECT frm.ticker_region, MIN(e.REPORT_DATE) AS next_report_date, min(e.PUBLICATION_DATE) AS report_date_published_date FROM factset_db.fe_v4.fe_basic_act_qf e
JOIN FsymRegionMapping frm ON frm.fsym_id=e.fsym_id AND frm.rk=1
WHERE fe_item='SALES'
AND ACTUAL_VALUE IS null
AND e.REPORT_DATE > GETDATE()
GROUP BY frm.ticker_region
'''


def get_company_report_date(ticker_regions):
    ticker_regions = [str(x).upper() for x in dfutil.tolist(ticker_regions)]
    dfs = []
    for _t in dfutil.chunks_list(set(ticker_regions), 10_000):
        dfs.append(sqlutil.readdfsql(REPORT_DATE_SQL.format(tosqllist(ticker_regions)), app=APP))
    df = pd.concat(dfs)
    df = normalize_df(df)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    company_id = '000C7F-E'

    # df = get_latest_filing_date(company_id)
    # df = get_company_dcs_detail(company_id, '2021-01-01', '2022-12-31')
    ids = 'FF_MKT_VAL, FF_EBITDA_OPER, FF_CAPEX, FF_INT_EXP_NET,FF_ENTRPR_VAL,FF_DEBT_ASSETS,FF_DEBT,FF_MKT_VAL,FF_EBITDA_OPER,FF_DEBT_EBITDA_OPER,FF_NET_DEBT,FF_CAPEX,FF_INT_EXP_NET,FF_COM_SHS_OUT_EPS_DIL'.replace(
        ' ', '').split(',')
    df = get_company_financials('SNOW-US', '2023-06-30', '2026-02-11', 'af', ids)
    logging.info(df.to_string())

    ccydf = df.groupby(['ticker_region', 'currency'])['period_end_date'].count() \
        .reset_index().sort_values('period_end_date').drop_duplicates(keep='last').drop(columns='period_end_date')

    logging.info(ccydf.to_string())
