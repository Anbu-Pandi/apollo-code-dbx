import logging

import databricks.sql
import pandas as pd

from apo_config.config import config
from common.utils.jobutil import retrycode
from common.utils.sqlutil import fetch_data_with_arrow
from data_access.dbtables.bdc import BDC_APP

FACTSET_DATABRICKS_CATALOG = "dbrx_factset_prod"
BDC_DATABRICKS_APP = BDC_APP


def get_databricks_warehouse_connection(app: str):
    connection_config = {
        "server_hostname": config.getconfig(app, "databricks_host"),
        "http_path": config.getconfig(app, "databricks_http_path"),
        "access_token": config.getconfig(app, "databricks_pat"),
        "catalog": config.getconfig(app, "databricks_catalog"),
        "schema": config.getconfig(app, "schema") or "dbo",
    }
    missing = [key for key in ("server_hostname", "http_path", "access_token") if not connection_config[key]]
    if missing:
        raise RuntimeError(f"Databricks SQL warehouse config is missing: {missing}")
    return databricks.sql.connect(**connection_config)


def _read_databricks_sql(sql: str, *, msg: str, chunk_size: int = 10_000) -> pd.DataFrame:
    def _fetch() -> pd.DataFrame:
        with get_databricks_warehouse_connection(BDC_DATABRICKS_APP) as conn:
            return fetch_data_with_arrow(sql, conn, chunk_size)

    logging.info("Executing Databricks SQL warehouse query %s", msg)
    df = retrycode(_fetch, retrycount=3, wait_on_error_seconds=5)
    logging.info("Retrieved Databricks SQL warehouse df %s shape=%s", msg, df.shape)
    return df


def set_factset_databricks_catalog(catalog: str) -> None:
    catalog_s = str(catalog or "").strip()
    if not catalog_s:
        raise ValueError("FactSet Databricks catalog cannot be blank.")
    global FACTSET_DATABRICKS_CATALOG
    FACTSET_DATABRICKS_CATALOG = catalog_s


def set_bdc_databricks_app(app: str) -> None:
    app_s = str(app or "").strip()
    if not app_s:
        raise ValueError("BDC Databricks app cannot be blank.")
    global BDC_DATABRICKS_APP
    BDC_DATABRICKS_APP = app_s


FACTSET_BDC_SQL = """
WITH selected_reports AS (
    SELECT *
    FROM {factset_catalog}.bdc_v1.bdc_reports r
    WHERE {date_filter}
),
selected_holdings AS (
    SELECT h.*
    FROM {factset_catalog}.bdc_v1.bdc_holdings h
    INNER JOIN selected_reports r ON r.report_id = h.report_id
    WHERE COALESCE(CAST(h.terminated_flag AS BOOLEAN), FALSE) = FALSE
),
holding_fsym AS (
    SELECT DISTINCT fsym_id
    FROM selected_holdings
    WHERE fsym_id IS NOT NULL
),
sym_cov_one AS (
    SELECT sc.fsym_id, sc.fsym_security_id
    FROM {factset_catalog}.SYM_V1.SYM_COVERAGE sc
    INNER JOIN holding_fsym hf ON hf.fsym_id = sc.fsym_id
    QUALIFY ROW_NUMBER() OVER (PARTITION BY sc.fsym_id ORDER BY sc.fsym_security_id) = 1
),
lookup_fsym AS (
    SELECT fsym_id FROM holding_fsym
    UNION
    SELECT fsym_security_id FROM sym_cov_one WHERE fsym_security_id IS NOT NULL
),
sym_isin_hist_one AS (
    SELECT s.fsym_id, s.isin
    FROM {factset_catalog}.SYM_V1.SYM_ISIN_HIST s
    INNER JOIN lookup_fsym lf ON lf.fsym_id = s.fsym_id
    WHERE s.most_recent = TRUE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.fsym_id ORDER BY s.isin) = 1
),
sym_bbg_one AS (
    SELECT s.fsym_id, s.bbg_id, s.bbg_ticker
    FROM {factset_catalog}.SYM_V1.SYM_BBG s
    INNER JOIN lookup_fsym lf ON lf.fsym_id = s.fsym_id
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.fsym_id ORDER BY s.bbg_id, s.bbg_ticker) = 1
),
sym_cusip_loan_one AS (
    SELECT s.fsym_id, s.cusip, s.ppn_cusip, s.pcl_cusip
    FROM {factset_catalog}.SYM_V1.SYM_CUSIP_LOAN s
    INNER JOIN lookup_fsym lf ON lf.fsym_id = s.fsym_id
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.fsym_id ORDER BY s.cusip, s.ppn_cusip, s.pcl_cusip) = 1
),
sym_cusip_hist_one AS (
    SELECT s.fsym_id, s.cusip
    FROM {factset_catalog}.SYM_V1.SYM_CUSIP_HIST s
    INNER JOIN lookup_fsym lf ON lf.fsym_id = s.fsym_id
    WHERE s.most_recent = TRUE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.fsym_id ORDER BY s.cusip) = 1
),
dcs_latest AS (
    SELECT
        dd.instrument_id AS dcs_fsym_id,
        dd.detail_id AS dcs_detail_id,
        CAST(dd.report_date AS DATE) AS dcs_report_date,
        dd.instrument_description AS dcs_instrument_description,
        dd.debt_code AS dcs_debt_code,
        dmap.debt_desc AS dcs_debt_desc,
        dmap.issue_type AS dcs_issue_type_code,
        imap.issue_type_desc AS dcs_issue_type_desc,
        dmap.seniority_code AS dcs_seniority_code,
        dsmap.seniority_desc AS dcs_seniority_desc,
        dd.coupon_code AS dcs_coupon_code,
        dd.coupon_index AS dcs_coupon_index,
        dd.coupon_rate_min AS dcs_coupon_rate_min,
        dd.coupon_margin_min AS dcs_coupon_margin_min,
        CAST(dd.issue_date AS DATE) AS dcs_issue_date,
        TRY_CAST(
            CONCAT(
                CAST(dd.maturity_year AS STRING), '-',
                LPAD(CAST(dd.maturity_year_month AS STRING), 2, '0'), '-',
                LPAD(CAST(dd.maturity_year_day AS STRING), 2, '0')
            ) AS DATE
        ) AS dcs_maturity_date,
        dd.issuance_currency AS dcs_issuance_currency,
        dd.currency AS dcs_currency,
        dd.issue_amount AS dcs_issue_amount,
        dd.os_amount AS dcs_os_amount,
        dd.avail_amount AS dcs_avail_amount,
        dd.borrowing_base AS dcs_borrowing_base,
        i1.isin AS dcs_isin,
        b1.bbg_id AS dcs_bbg_id,
        b1.bbg_ticker AS dcs_bbg_ticker
    FROM {factset_catalog}.DCS_V2.DCS_DETAILS dd
    INNER JOIN lookup_fsym lf ON lf.fsym_id = dd.instrument_id
    LEFT JOIN {factset_catalog}.REF_V2.DCS_DEBT_MAP dmap ON dmap.debt_code = dd.debt_code
    LEFT JOIN {factset_catalog}.REF_V2.DCS_SENIORITY_MAP dsmap ON dsmap.seniority_code = dmap.seniority_code
    LEFT JOIN {factset_catalog}.REF_V2.ISSUE_TYPE_MAP imap ON imap.issue_type_code = dmap.issue_type
    LEFT JOIN sym_isin_hist_one i1 ON i1.fsym_id = dd.instrument_id
    LEFT JOIN sym_bbg_one b1 ON b1.fsym_id = dd.instrument_id
    WHERE dd.active = TRUE
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dd.instrument_id
        ORDER BY dd.report_date DESC, dd.os_amount DESC NULLS LAST, dd.detail_id DESC
    ) = 1
)
SELECT
    CAST(r.report_date AS DATE) AS report_date,
    CAST(r.report_date AS DATE) AS asof_date,
    CAST(r.publication_date AS DATE) AS publication_date,
    CAST(r.collection_date AS TIMESTAMP) AS collection_date,
    CAST(r.last_update AS TIMESTAMP) AS last_update,
    r.period_type_code, rm.reporting_period_desc,
    r.report_currency, r.sec_accession_id,
    r.factset_entity_id AS bdc_factset_entity_id,
    bdc_ent.entity_proper_name AS bdc_name_factset,
    r.report_id AS factset_report_id,
    CAST(h.record_id AS DOUBLE) AS factset_bdc_holding_id,
    h.factset_portfolio_entity_id,
    port_ent.entity_proper_name AS portfolio_name_factset,
    h.investment_name,
    h.reported_industry,
    h.fsym_id,
    COALESCE(sc.fsym_security_id, h.fsym_id) AS dcs_lookup_fsym_id,
    scl.cusip AS factset_sym_cusip_loan_cusip,
    scl.ppn_cusip AS factset_sym_cusip_loan_ppn_cusip,
    scl.pcl_cusip AS factset_sym_cusip_loan_pcl_cusip,
    sch.cusip AS factset_sym_cusip_hist_cusip,
    sih.isin AS factset_sym_isin_hist_isin,
    sbbg.bbg_id AS factset_sym_bbg_bbg_id,
    h.bdc_security_desc,
    h.investment_type_code,
    h.debt_code,
    h.seniority_code,
    h.coupon_code,
    im.investment_group, im.investment_desc, dm.debt_code_description,
    port_ent_cov.industry_code AS portfolio_industry_code,
    ref_ind.factset_industry_desc AS portfolio_industry_desc,
    ref_ind.factset_sector_code AS portfolio_sector_code,
    sm.seniority_desc, cm.coupon_desc,
    h.coupon_index, h.min_coupon_rate,
    TRY_CAST(REGEXP_REPLACE(TRIM(CAST(h.min_coupon_margin AS STRING)), '[^0-9.-]', '') AS DOUBLE) / 100.0 AS min_coupon_margin,
    h.pay_in_kind_coupon_rate,
    h.instrument_currency,
    CAST(h.issue_date AS DATE) AS issue_date,
    CAST(h.acquisition_date AS DATE) AS acquisition_date,
    CAST(h.maturity_date AS DATE) AS maturity_date,
    TRY_CAST(REGEXP_REPLACE(TRIM(CAST(h.amount_issued AS STRING)), '[^0-9.-]', '') AS DOUBLE) * 1e6 AS amount_issued,
    h.par_value, h.fair_value, h.cost,
    h.net_assets_pct,
    TRY_CAST(REGEXP_REPLACE(TRIM(CAST(h.fair_to_par_value_ratio AS STRING)), '[^0-9.-]', '') AS DOUBLE) * 100 AS fair_to_par_value_ratio,
    h.convert_par_value_flag, h.exchange_rate,
    h.non_accrual_status, h.defaulted_flag, h.perpetual_flag,
    h.convertible_flag, h.pay_in_kind_flag,
    COALESCE(CAST(h.terminated_flag AS BOOLEAN), FALSE) AS terminated_flag,
    dcs.dcs_detail_id, dcs.dcs_report_date, dcs.dcs_instrument_description,
    dcs.dcs_debt_code, dcs.dcs_debt_desc,
    dcs.dcs_issue_type_code, dcs.dcs_issue_type_desc,
    dcs.dcs_seniority_code, dcs.dcs_seniority_desc,
    dcs.dcs_coupon_code, dcs.dcs_coupon_index,
    dcs.dcs_coupon_rate_min, dcs.dcs_coupon_margin_min,
    dcs.dcs_issue_date, dcs.dcs_maturity_date,
    dcs.dcs_issuance_currency, dcs.dcs_currency,
    dcs.dcs_issue_amount, dcs.dcs_os_amount,
    dcs.dcs_avail_amount, dcs.dcs_borrowing_base,
    dcs.dcs_isin, dcs.dcs_bbg_id, dcs.dcs_bbg_ticker,
    dcs.dcs_fsym_id AS dcs_joined_fsym_id,
    CASE WHEN dcs.dcs_detail_id IS NOT NULL THEN 'fsym_id_security_map' ELSE 'unmatched' END AS dcs_join_source
FROM selected_holdings h
INNER JOIN selected_reports r ON h.report_id = r.report_id
LEFT JOIN {factset_catalog}.bdc_v1.bdc_report_period_map rm ON rm.reporting_period_code = r.period_type_code
LEFT JOIN {factset_catalog}.bdc_v1.bdc_investment_map im ON im.investment_type_code = h.investment_type_code
LEFT JOIN {factset_catalog}.bdc_v1.bdc_debt_map dm ON dm.debt_code = h.debt_code
LEFT JOIN {factset_catalog}.bdc_v1.bdc_coupon_map cm ON cm.coupon_code = h.coupon_code
LEFT JOIN {factset_catalog}.bdc_v1.bdc_seniority_map sm ON sm.seniority_code = h.seniority_code
LEFT JOIN {factset_catalog}.sym_v1.sym_entity bdc_ent ON bdc_ent.factset_entity_id = r.factset_entity_id
LEFT JOIN {factset_catalog}.sym_v1.sym_entity port_ent ON port_ent.factset_entity_id = h.factset_portfolio_entity_id
LEFT JOIN {factset_catalog}.ENT_V1.ENT_ENTITY_COVERAGE port_ent_cov ON port_ent_cov.factset_entity_id = h.factset_portfolio_entity_id
LEFT JOIN {factset_catalog}.REF_V2.FACTSET_INDUSTRY_MAP ref_ind ON ref_ind.factset_industry_code = port_ent_cov.industry_code
LEFT JOIN sym_cov_one sc ON sc.fsym_id = h.fsym_id
LEFT JOIN sym_cusip_loan_one scl ON scl.fsym_id = h.fsym_id
LEFT JOIN sym_cusip_hist_one sch ON sch.fsym_id = h.fsym_id
LEFT JOIN sym_isin_hist_one sih ON sih.fsym_id = h.fsym_id
LEFT JOIN sym_bbg_one sbbg ON sbbg.fsym_id = h.fsym_id
LEFT JOIN dcs_latest dcs ON dcs.dcs_fsym_id = COALESCE(sc.fsym_security_id, h.fsym_id)
"""

SP_INDEX_SQL = """
SELECT
    NULLIF(UPPER(TRIM(CAST(figi AS STRING))), '') AS figi,
    NULLIF(SUBSTRING(UPPER(TRIM(CAST(cusip AS STRING))), 1, 9), '') AS cusip,
    NULLIF(UPPER(TRIM(CAST(linid AS STRING))), '') AS linid,
    NULLIF(UPPER(TRIM(CAST(loan_xid AS STRING))), '') AS loan_xid,
    NULLIF(UPPER(TRIM(CAST(id AS STRING))), '') AS id,
    issuer, facility_name, narrow_industry,
    CAST(asof_date AS DATE) AS asof_date
FROM dbo.sp_loan_index
WHERE ({date_filter})
"""

JPM_INDEX_SQL = """
SELECT
    NULLIF(SUBSTRING(UPPER(TRIM(CAST(cusip AS STRING))), 1, 9), '') AS cusip,
    NULLIF(UPPER(TRIM(CAST(loan_id AS STRING))), '') AS loan_id,
    NULLIF(UPPER(TRIM(CAST(bbg_id AS STRING))), '') AS bbg_id,
    NULLIF(UPPER(TRIM(CAST(loanx AS STRING))), '') AS loanx,
    NULLIF(UPPER(TRIM(CAST(facility_id AS STRING))), '') AS facility_id,
    issuer_name, asset_name, ind_segment,
    CAST(asof_date AS DATE) AS asof_date
FROM dbo.jpm_loan_index
WHERE ({date_filter})
"""

ICE_INDEX_SQL = """
SELECT
    COALESCE(
        NULLIF(UPPER(TRIM(CAST(cusip9 AS STRING))), ''),
        SUBSTRING(NULLIF(UPPER(TRIM(CAST(cusip AS STRING))), ''), 1, 9)
    ) AS cusip,
    COALESCE(
        NULLIF(UPPER(TRIM(CAST(cusip9 AS STRING))), ''),
        SUBSTRING(NULLIF(UPPER(TRIM(CAST(cusip AS STRING))), ''), 1, 9)
    ) AS cusip9,
    NULLIF(UPPER(TRIM(CAST(isin_number AS STRING))), '') AS isin_number,
    NULLIF(UPPER(TRIM(CAST(ice_unique_id AS STRING))), '') AS ice_unique_id,
    ice_index_ticker, description, ticker, industry_lvl4_desc,
    price,
    COALESCE(spread_to_worst, oas, credit_spread, asset_swap_spread) AS spread,
    COALESCE(effective_yield, yield_to_worst_sa, yield_to_maturity_sa) AS yield,
    rating,
    face_value_usd AS amount_outstanding,
    CAST(asof_date AS DATE) AS asof_date
FROM dbo.ice_index_all
WHERE frequency = 'PROJ'
  AND ({date_filter})
"""

BBG_BRIDGE_SQL = """
SELECT
    UPPER(TRIM(CAST(linked_instrument_id_bb AS STRING))) AS database_bbg_id,
    UPPER(TRIM(CAST(instrument_id_bb AS STRING))) AS api_id_bb
FROM dbo.related_bbg_instruments
WHERE linked_instrument_id_bb IS NOT NULL
  AND instrument_id_bb IS NOT NULL
"""

DIRECT_INDEX_SQL = """
WITH direct_sources AS (
    SELECT
        CAST(asof_date AS DATE) AS asof_date,
        CONCAT('JPM_LOAN ', COALESCE(jpm_index_ticker, benchmark)) AS index_name,
        COALESCE(jpm_index_ticker, benchmark, 'JPM_LOAN') AS index_desc,
        COALESCE(loanx, bbg_id, cusip, CAST(facility_id AS STRING)) AS constituent_id,
        COALESCE(issuer_name, ticker) AS constituent_name,
        cusip, CAST(NULL AS STRING) AS isin, loanx AS loanx_id, ticker,
        'JPM' AS source, CAST(end_price AS DOUBLE) AS price, ind_segment AS industry,
        CAST(fw_mat_moddur AS DOUBLE) AS duration,
        CAST(tot_ret AS DOUBLE) AS period_total_return,
        CAST(fw_mat_spd AS DOUBLE) AS spread,
        CAST(offer_spread AS DOUBLE) AS current_spread_coup,
        ROUND(CAST(end_cpn_rate AS DOUBLE) * 100, 5) AS coupon,
        CAST(current_outstanding AS DOUBLE) AS amt_out,
        ROUND(CAST(fw_mat_yld AS DOUBLE) * 100, 5) AS ytm,
        CAST(maturity_date AS DATE) AS maturity_date,
        CAST(prn_ret AS DOUBLE) AS period_principal_return,
        bbg_id AS _bridge_key_1,
        loanx AS _bridge_key_2,
        cusip AS _bridge_key_3,
        CAST(NULL AS STRING) AS _bridge_key_4
    FROM dbo.jpm_loan_index
    WHERE asof_date >= '{from_date}' AND asof_date <= '{to_date}'

    UNION ALL

    SELECT
        CAST(asof_date AS DATE) AS asof_date,
        'SP_LOAN_INDEX' AS index_name,
        portfolio_name AS index_desc,
        COALESCE(loan_xid, pricing_id, cusip, figi, CAST(facility_id AS STRING), CAST(id AS STRING)) AS constituent_id,
        issuer AS constituent_name,
        cusip, CAST(NULL AS STRING) AS isin, loan_xid AS loanx_id,
        CAST(NULL AS STRING) AS ticker,
        'SP' AS source, CAST(index_price AS DOUBLE) AS price, narrow_industry AS industry,
        CAST(NULL AS DOUBLE) AS duration,
        CAST(total_return AS DOUBLE) AS period_total_return,
        CAST(NULL AS DOUBLE) AS spread,
        CAST(current_spread AS DOUBLE) * 100 AS current_spread_coup,
        ROUND(CAST(coupon AS DOUBLE), 3) AS coupon,
        CAST(amount_outstanding AS DOUBLE) AS amt_out,
        ROUND(CAST(yieldto_maturity AS DOUBLE), 5) AS ytm,
        CAST(maturity_date AS DATE) AS maturity_date,
        CAST(market_return AS DOUBLE) AS period_principal_return,
        figi AS _bridge_key_1,
        loan_xid AS _bridge_key_2,
        cusip AS _bridge_key_3,
        pricing_id AS _bridge_key_4
    FROM dbo.sp_loan_index
    WHERE asof_date >= '{from_date}' AND asof_date <= '{to_date}'

    UNION ALL

    SELECT
        CAST(asof_date AS DATE) AS asof_date,
        ice_index_ticker AS index_name,
        ice_index_ticker AS index_desc,
        COALESCE(isin_number, cusip, ticker) AS constituent_id,
        COALESCE(description, ticker) AS constituent_name,
        cusip, isin_number AS isin, CAST(NULL AS STRING) AS loanx_id, ticker,
        'ICE' AS source, CAST(price AS DOUBLE) AS price, industry_lvl4_desc AS industry,
        CAST(effective_duration AS DOUBLE) AS duration,
        CAST(trr_pct_1day_loc / 100 AS DOUBLE) AS period_total_return,
        CAST(oas AS DOUBLE) AS spread,
        CAST(NULL AS DOUBLE) AS current_spread_coup,
        CAST(current_period_coupon AS DOUBLE) AS coupon,
        CAST(face_value_loc AS DOUBLE) * 1000000 AS amt_out,
        ROUND(CAST(yield_to_maturity_conv AS DOUBLE), 5) AS ytm,
        CAST(maturity_date AS DATE) AS maturity_date,
        CAST(prr_pct_1day_local AS DOUBLE) AS period_principal_return,
        isin_number AS _bridge_key_1,
        cusip AS _bridge_key_2,
        ticker AS _bridge_key_3,
        CAST(NULL AS STRING) AS _bridge_key_4
    FROM dbo.ice_index_all
    WHERE frequency = 'PROJ'
      AND ice_index_ticker IN (
        'C0A0', 'C0J0', 'CABS', 'CMBS', 'COCO', 'DGOV', 'DXEM', 'EB00', 'EMCB',
        'EMCL', 'EMHB', 'EMIB', 'EMIC', 'ER00', 'G0BL', 'H0A0', 'HE00', 'IP00',
        'M0A0', 'R0A0', 'R0C0', 'R0E0', 'R0FS', 'R0O0', 'R0U0', 'UR00'
      )
      AND asof_date >= '{from_date}' AND asof_date <= '{to_date}'
)
SELECT *
FROM direct_sources
WHERE constituent_id IS NOT NULL
QUALIFY asof_date = MAX(asof_date) OVER (PARTITION BY source)
"""


def _lower_cols(df):
    df.columns = df.columns.str.lower()
    return df


def _strip_co_prefix(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip().str.upper()
    s = s.where(~s.isin(["", "NAN", "NONE", "NULL"]), pd.NA)
    out = s.str[2:].where(s.str.startswith("CO", na=False))
    return out.where(out.str.len() > 0)


def get_factset_bdc_holdings(from_date, to_date):
    date_filter = (
        f"CAST(r.report_date AS DATE) >= '{from_date}'\n"
        f"  AND CAST(r.report_date AS DATE) <= '{to_date}'"
    )
    sql = FACTSET_BDC_SQL.format(
        date_filter=date_filter,
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(sql, msg='FactSet BDC + DCS (Databricks)')
    return _lower_cols(df)


def get_factset_bdc_holdings_all():
    sql = FACTSET_BDC_SQL.format(
        date_filter="1 = 1",
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(sql, msg='FactSet BDC + DCS all holdings (Databricks)')
    return _lower_cols(df)


def get_factset_bdc_holdings_by_collection_date(from_date, to_date):
    date_filter = (
        f"CAST(r.collection_date AS DATE) >= '{from_date}'\n"
        f"  AND CAST(r.collection_date AS DATE) <= '{to_date}'"
    )
    sql = FACTSET_BDC_SQL.format(
        date_filter=date_filter,
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(sql, msg=f'FactSet BDC + DCS collection_date {from_date}..{to_date}')
    return _lower_cols(df)


def get_factset_bdc_holdings_knowledge_updates(from_date, to_date, asof_date=None):
    report_filter = f"""
(
    (CAST(collection_date AS DATE) >= '{from_date}' AND CAST(collection_date AS DATE) <= '{to_date}')
    OR
    (CAST(last_update AS DATE) >= '{from_date}' AND CAST(last_update AS DATE) <= '{to_date}')
)
"""
    if asof_date is not None:
        report_filter = f"""
CAST(report_date AS DATE) <= '{asof_date}'
  AND CAST(collection_date AS DATE) <= '{asof_date}'
  AND {report_filter}
"""
    report_ids_sql = f"""
SELECT report_id
FROM {FACTSET_DATABRICKS_CATALOG}.bdc_v1.bdc_reports
WHERE {report_filter}
"""
    report_ids_df = _read_databricks_sql(
        report_ids_sql,
        msg=f'FactSet BDC report metadata updates {from_date}..{to_date}',
    )
    if report_ids_df is None or report_ids_df.empty:
        logging.info('No FactSet BDC report updates for %s..%s', from_date, to_date)
        return pd.DataFrame()

    report_ids = pd.to_numeric(report_ids_df['report_id'], errors='coerce').dropna().astype('int64').unique()
    if not len(report_ids):
        return pd.DataFrame()
    date_filter = f"r.report_id IN ({', '.join(map(str, sorted(report_ids)))})"
    sql = FACTSET_BDC_SQL.format(
        date_filter=date_filter,
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(
        sql,
        msg=f'FactSet BDC + DCS knowledge updates {from_date}..{to_date}',
    )
    return _lower_cols(df)


def get_factset_bdc_holdings_updated_since(last_update_from, last_update_to=None):
    date_filter = f"CAST(r.last_update AS TIMESTAMP) > CAST('{last_update_from}' AS TIMESTAMP)"
    if last_update_to is not None:
        date_filter += f"\n  AND CAST(r.last_update AS TIMESTAMP) <= CAST('{last_update_to}' AS TIMESTAMP)"
    sql = FACTSET_BDC_SQL.format(
        date_filter=date_filter,
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(sql, msg=f'FactSet BDC + DCS last_update > {last_update_from}')
    return _lower_cols(df)


def get_factset_bdc_holdings_asof(asof_date, factset_entity_ids=None):
    entity_filter = ""
    if factset_entity_ids is not None:
        if isinstance(factset_entity_ids, str):
            factset_entity_ids = [factset_entity_ids]
        entity_values = sorted(
            {
                str(value).strip()
                for value in factset_entity_ids
                if value is not None and str(value).strip()
            }
        )
        if entity_values:
            quoted_values = ", ".join(
                f"'{value.replace(chr(39), chr(39) * 2)}'" for value in entity_values
            )
            entity_filter = f"\n          AND factset_entity_id IN ({quoted_values})"
    latest_report_filter = f"""
r.report_id IN (
    SELECT report_id
    FROM (
        SELECT
            report_id,
            factset_entity_id,
            ROW_NUMBER() OVER (
                PARTITION BY factset_entity_id
                ORDER BY CAST(report_date AS DATE) DESC,
                         CAST(collection_date AS DATE) DESC,
                         CAST(publication_date AS DATE) DESC,
                         report_id DESC
            ) AS rn
        FROM {FACTSET_DATABRICKS_CATALOG}.bdc_v1.bdc_reports
        WHERE CAST(report_date AS DATE) <= '{asof_date}'
          AND CAST(collection_date AS DATE) <= '{asof_date}'
          {entity_filter}
    ) lr
    WHERE rn = 1
)
"""
    sql = FACTSET_BDC_SQL.format(
        date_filter=latest_report_filter,
        factset_catalog=FACTSET_DATABRICKS_CATALOG,
    )
    df = _read_databricks_sql(sql, msg=f'FactSet BDC + DCS (asof={asof_date})')
    return _lower_cols(df)


def get_sp_index(date_filter):
    sql = SP_INDEX_SQL.format(date_filter=date_filter)
    return _lower_cols(_read_databricks_sql(sql, msg='S&P loan index'))


def get_jpm_index(date_filter):
    sql = JPM_INDEX_SQL.format(date_filter=date_filter)
    return _lower_cols(_read_databricks_sql(sql, msg='JPM loan index'))


def get_ice_index(date_filter):
    sql = ICE_INDEX_SQL.format(date_filter=date_filter)
    return _lower_cols(_read_databricks_sql(sql, msg='ICE index'))


def get_direct_index_constituent_stats(from_date: str, to_date: str) -> pd.DataFrame:
    sql = DIRECT_INDEX_SQL.format(from_date=from_date, to_date=to_date)
    return _lower_cols(
        _read_databricks_sql(
            sql,
            msg=f'direct index sources {from_date}..{to_date}',
        )
    )


def get_direct_index_bond_security_map(
    from_date: str,
    to_date: str,
    factset_bbg_ids,
) -> dict[str, str]:
    values = sorted(
        {
            str(value).strip().upper()
            for value in factset_bbg_ids
            if value is not None and str(value).strip()
        }
    )
    if not values:
        return {}
    values_sql = ",\n        ".join(
        f"('{value.replace(chr(39), chr(39) * 2)}')" for value in values
    )
    sql = f"""
WITH holding_bbg(id_bb_global) AS (
    VALUES {values_sql}
),
ice_isins AS (
    SELECT DISTINCT isin_number AS id_isin
    FROM dbo.ice_index_all
    WHERE frequency = 'PROJ'
      AND asof_date >= '{from_date}' AND asof_date <= '{to_date}'
      AND isin_number IS NOT NULL
)
SELECT b.id_isin, b.id_bb_global
FROM dbo.f_get_bond_security_master(NULL) b
INNER JOIN ice_isins i ON i.id_isin = b.id_isin
INNER JOIN holding_bbg h
    ON h.id_bb_global = b.id_bb_global
    OR (
        LENGTH(h.id_bb_global) = 8
        AND h.id_bb_global = SUBSTRING(b.id_bb_global, 1, 8)
    )
WHERE b.id_bb_global IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY b.id_isin
    ORDER BY COALESCE(b.is_latest, FALSE) DESC, b.knowledge_date DESC, b.load_date DESC
) = 1
"""
    frame = _lower_cols(
        _read_databricks_sql(
            sql,
            msg=f'direct ICE bond map {from_date}..{to_date}',
        )
    )
    if frame.empty:
        return {}
    return dict(frame[["id_isin", "id_bb_global"]].itertuples(index=False, name=None))


def get_bbg_bridge(enable_key_variants: bool = True):
    df = _read_databricks_sql(BBG_BRIDGE_SQL, msg='BBG bridge map', chunk_size=5000)
    df = _lower_cols(df)
    from data_access.dbtables.bdc.matching import _clean_bbg
    df['database_bbg_id'] = _clean_bbg(df['database_bbg_id'])
    df['api_id_bb'] = _clean_bbg(df['api_id_bb'])
    df = df.dropna(subset=['database_bbg_id', 'api_id_bb']).drop_duplicates(subset=['database_bbg_id'], keep='last')

    if enable_key_variants and not df.empty:
        variant_df = df.copy()
        variant_df["database_bbg_id"] = _strip_co_prefix(variant_df["database_bbg_id"])
        variant_df = variant_df.dropna(subset=["database_bbg_id"])
        if not variant_df.empty:
            # Keep exact/raw keys when both raw and variant produce the same key.
            df["_key_priority"] = 0
            variant_df["_key_priority"] = 1
            df = pd.concat([df, variant_df], ignore_index=True)
            df = (
                df.sort_values(["database_bbg_id", "_key_priority"])
                .drop_duplicates(subset=["database_bbg_id"], keep="first")
                .drop(columns=["_key_priority"])
                .reset_index(drop=True)
            )

    return dict(df.set_index('database_bbg_id')['api_id_bb'])


def build_index_date_filter(factset_df, lookback_days=7):
    report_dates = pd.to_datetime(factset_df['report_date']).unique()
    parts = []
    for rd in report_dates:
        from_dt = (rd - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        to_dt = rd.strftime('%Y-%m-%d')
        parts.append(f"asof_date BETWEEN '{from_dt}' AND '{to_dt}'")
    return ' OR '.join(parts)
