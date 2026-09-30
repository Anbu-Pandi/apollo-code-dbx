import pandas as pd
from data_access.dbtables.sponsor_study.website_company_classification import CLASSIFICATION_VERSION
from data_access.dbtables.sponsor_study.website_attempts import published_sponsor_predicate

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study import DEFAULT_SCHEMA, SPONSOR_STUDY_APP
from data_access.dbtables.sponsor_study.enrichment_serving import identity_enrichment_ctes_sql
from data_access.dbtables.sponsor_study.financial_serving import financial_selector_sql
from data_access.dbtables.sponsor_study.external_prod_contracts import (
    EXTERNAL_PROD_BENCHMARK_DENYLIST as _EXTERNAL_PROD_BENCHMARK_DENYLIST,
    EXTERNAL_PROD_TABLE_ALLOWLIST as _EXTERNAL_PROD_TABLE_ALLOWLIST,
    external_prod_physical_table_name,
)
from data_access.dbtables.sponsor_study.findox_coverage import COVERAGE_COLUMNS
from data_access.dbtables.sponsor_study.screen_rules import RULE_VERSION, SCREEN_RULES
from data_access.dbtables.sponsor_study.source_priority import (
    holding_source_priority,
    holding_source_priority_sql,
)


DEFAULT_TABLE_PREFIX = "pe_sponsor_"
OUTPUT_NAMESPACE = "output_"
DEFAULT_PREQIN_CATALOG = "snowflake_preqin_prod"
DEFAULT_PREQIN_SCHEMA = "PREQIN_SHARED_DATA"
EXTERNAL_PROD_TABLES = frozenset(_EXTERNAL_PROD_TABLE_ALLOWLIST)
EXTERNAL_BENCHMARK_DENYLIST = frozenset(_EXTERNAL_PROD_BENCHMARK_DENYLIST)
OUTPUT_VIEW_NAMES = {
    "vw_company_analysis_snapshot": "company_analysis_snapshot",
    "fact_opportunity_screen": "opportunity_screen",
    "vw_sponsor_summary": "sponsor_summary",
    "dim_opportunity_screen_rule": "opportunity_screen_rule",
    "validation_reconciliation": "validation_coverage",
}


def read_table(
    table_name: str,
    schema: str = DEFAULT_SCHEMA,
    app: str = SPONSOR_STUDY_APP,
    limit: int | None = None,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> pd.DataFrame:
    sql = f"SELECT * FROM {schema}.{prefixed_name(table_name, table_prefix)}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return readdfsql(sql, app=app)


def prefixed_name(name: str, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    clean_name = str(name or "").strip()
    clean_prefix = str(table_prefix or "").strip()
    if not clean_prefix or clean_name.startswith(clean_prefix):
        return clean_name
    return f"{clean_prefix}{clean_name}"


def output_view_name(name: str, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    clean_name = str(name or "").strip()
    suffix = OUTPUT_VIEW_NAMES.get(clean_name, clean_name)
    return prefixed_name(f"{OUTPUT_NAMESPACE}{suffix}", table_prefix)


def _qualified(name: str, catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    physical_name = external_prod_physical_table_name(name, catalog=catalog)
    table_name = prefixed_name(physical_name, table_prefix)
    return f"{catalog}.{schema}.{table_name}" if catalog else f"{schema}.{table_name}"


def _output_qualified(name: str, catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    table_name = output_view_name(name, table_prefix)
    return f"{catalog}.{schema}.{table_name}" if catalog else f"{schema}.{table_name}"


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _values_view_sql(
    name: str,
    rows: list[dict],
    columns: list[str],
    *,
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    values = ",\n    ".join(
        "(" + ", ".join(_sql_literal(row.get(col)) for col in columns) + ")"
        for row in rows
    )
    return f"""
CREATE OR REPLACE VIEW {_qualified(name, catalog, schema, table_prefix)} AS
SELECT *
FROM VALUES
    {values}
AS t({", ".join(columns)})
""".strip()


def company_analysis_snapshot_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    analysis = _output_qualified("vw_company_analysis_snapshot", catalog, schema, table_prefix)
    stage = _qualified("stage_consolidated_company_snapshot", catalog, schema, table_prefix)
    internal_financial = _qualified("source_internal_financial_snapshot", catalog, schema, table_prefix)
    market = _qualified("fact_market_trading_snapshot", catalog, schema, table_prefix)
    internal_market = _qualified("source_internal_market_trading_snapshot", catalog, schema, table_prefix)
    debt = _qualified("fact_debt_instrument_snapshot", catalog, schema, table_prefix)
    internal_debt = _qualified("source_internal_debt_instrument_snapshot", catalog, schema, table_prefix)
    compliance = _qualified("fact_company_compliance_snapshot", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {analysis} AS
WITH internal_financial_latest AS (
    SELECT *
    FROM (
SELECT
            f.*,
            ROW_NUMBER() OVER (
                PARTITION BY f.portco_id
                ORDER BY f.period_end_date DESC NULLS LAST, f.asof_date DESC NULLS LAST
            ) AS rn
        FROM {internal_financial} f
    ) x
    WHERE rn = 1
),
market_latest AS (
    SELECT *
    FROM (
        SELECT
            m.*,
            ROW_NUMBER() OVER (
                PARTITION BY m.portco_id, m.tranche_type
                ORDER BY m.study_asof_date DESC
            ) AS rn
        FROM {market} m
    ) x
    WHERE rn = 1
),
market_wide AS (
    SELECT
        portco_id,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN price END) AS first_lien_price,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN ytw END) AS first_lien_ytw,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN ytm END) AS first_lien_ytm,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN prior_price END) AS prior_first_lien_price,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN prior_ytm END) AS prior_first_lien_ytm,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN price END) AS second_lien_unsecured_price,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN ytw END) AS second_lien_unsecured_ytw,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN ytm END) AS second_lien_unsecured_ytm,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN prior_price END) AS prior_second_lien_unsecured_price,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN prior_ytm END) AS prior_second_lien_unsecured_ytm
    FROM market_latest
    GROUP BY portco_id
),
internal_market_latest AS (
    SELECT *
    FROM (
        SELECT
            m.*,
            ROW_NUMBER() OVER (
                PARTITION BY m.portco_id, m.tranche_type
                ORDER BY m.asof_date DESC NULLS LAST
            ) AS rn
        FROM {internal_market} m
    ) x
    WHERE rn = 1
),
internal_market_wide AS (
    SELECT
        portco_id,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN price END) AS first_lien_price,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN ytw END) AS first_lien_ytw,
        MAX(CASE WHEN tranche_type LIKE '1L%' THEN ytm END) AS first_lien_ytm,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN price END) AS second_lien_unsecured_price,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN ytw END) AS second_lien_unsecured_ytw,
        MAX(CASE WHEN tranche_type NOT LIKE '1L%' THEN ytm END) AS second_lien_unsecured_ytm,
        MAX(source_system) AS source_system
    FROM internal_market_latest
    GROUP BY portco_id
),
debt_latest AS (
    SELECT *
    FROM (
        SELECT
            d.*,
            ROW_NUMBER() OVER (
                PARTITION BY d.ownership_id, d.portco_id
                ORDER BY d.nearest_maturity_date ASC NULLS LAST, d.study_asof_date DESC
            ) AS rn
        FROM {debt} d
    ) x
    WHERE rn = 1
),
internal_debt_latest AS (
    SELECT *
    FROM (
        SELECT
            d.*,
            ROW_NUMBER() OVER (
                PARTITION BY d.portco_id
                ORDER BY d.nearest_maturity_date ASC NULLS LAST, d.asof_date DESC NULLS LAST
            ) AS rn
        FROM {internal_debt} d
    ) x
    WHERE rn = 1
)
SELECT
    b.*,
    COALESCE(c.private_data_flag, false) AS private_data_flag,
    COALESCE(c.direct_lending_only_flag, false) AS direct_lending_only_flag,
    COALESCE(c.pe_investment_team_restricted_flag, false) AS pe_investment_team_restricted_flag,
    COALESCE(c.disqualified_lender_blocked_flag, false) AS disqualified_lender_blocked_flag,
    COALESCE(c.visible_to_all_placeholder_flag, true) AS visible_to_all_placeholder_flag,
    c.allowed_group_names,
    c.blocked_group_names,
    c.restriction_reason,
    c.restriction_source,
    c.effective_start_date,
    c.effective_end_date,
    fi.period_end_date AS financial_period_end_date,
    fi.company_corp_ticker AS financial_company_corp_ticker,
    fi.financial_identifier,
    fi.financial_identifier_type,
    fi.financial_provenance_json,
    fi.current_total_debt,
    fi.current_total_cash,
    fi.current_net_debt,
    fi.current_ltm_revenue,
    fi.current_ltm_ebitda,
    fi.current_ebitda_margin,
    fi.current_gross_leverage,
    fi.current_net_leverage,
    fi.yoy_revenue_growth,
    fi.yoy_ebitda_growth,
    fi.ltv,
    fi.source_system AS financial_metric_source_system,
    COALESCE(mi.first_lien_price, m.first_lien_price) AS first_lien_price,
    COALESCE(mi.first_lien_ytw, m.first_lien_ytw) AS first_lien_ytw,
    COALESCE(mi.first_lien_ytm, m.first_lien_ytm) AS first_lien_ytm,
    m.prior_first_lien_price,
    m.prior_first_lien_ytm,
    COALESCE(mi.second_lien_unsecured_price, m.second_lien_unsecured_price) AS second_lien_unsecured_price,
    COALESCE(mi.second_lien_unsecured_ytw, m.second_lien_unsecured_ytw) AS second_lien_unsecured_ytw,
    COALESCE(mi.second_lien_unsecured_ytm, m.second_lien_unsecured_ytm) AS second_lien_unsecured_ytm,
    m.prior_second_lien_unsecured_price,
    m.prior_second_lien_unsecured_ytm,
    COALESCE(mi.first_lien_ytm, m.first_lien_ytm) - m.prior_first_lien_ytm AS yoy_first_lien_ytm_change,
    COALESCE(mi.second_lien_unsecured_ytm, m.second_lien_unsecured_ytm) - m.prior_second_lien_unsecured_ytm AS yoy_second_lien_unsecured_ytm_change,
    COALESCE(mi.source_system, 'workbook') AS market_metric_source_system,
    COALESCE(di.nearest_maturity_date, d.nearest_maturity_date) AS nearest_maturity_date,
    COALESCE(di.nearest_maturity_type, d.nearest_maturity_type) AS nearest_maturity_type,
    COALESCE(di.source_system, 'workbook') AS debt_metric_source_system,
    CASE
        WHEN fi.current_total_debt IS NULL OR fi.current_total_debt <= 500 THEN 'Performing / Other'
        WHEN COALESCE(mi.first_lien_ytm, m.first_lien_ytm) > 0.14 OR COALESCE(mi.second_lien_unsecured_ytm, m.second_lien_unsecured_ytm) > 0.20 THEN 'Distressed'
        WHEN (COALESCE(mi.first_lien_ytm, m.first_lien_ytm) > 0.10 AND COALESCE(mi.first_lien_ytm, m.first_lien_ytm) < 0.14)
            OR (COALESCE(mi.second_lien_unsecured_ytm, m.second_lien_unsecured_ytm) > 0.13 AND COALESCE(mi.second_lien_unsecured_ytm, m.second_lien_unsecured_ytm) < 0.20)
            THEN 'Stressed'
        ELSE 'Performing / Other'
    END AS health_status
FROM {stage} b
LEFT JOIN internal_financial_latest fi
    ON fi.portco_id = b.portco_id
LEFT JOIN market_wide m
    ON m.portco_id = b.portco_id
LEFT JOIN internal_market_wide mi
    ON mi.portco_id = b.portco_id
LEFT JOIN debt_latest d
    ON d.ownership_id = b.ownership_id
    AND d.portco_id = b.portco_id
LEFT JOIN internal_debt_latest di
    ON di.portco_id = b.portco_id
LEFT JOIN {compliance} c
    ON c.portco_id = b.portco_id
WHERE COALESCE(c.disqualified_lender_blocked_flag, false) = false
""".strip()


def opportunity_screen_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("fact_opportunity_screen", catalog, schema, table_prefix)
    analysis = _output_qualified("vw_company_analysis_snapshot", catalog, schema, table_prefix)
    rule_version = _sql_literal(RULE_VERSION)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH candidates AS (
    SELECT 'distressed' AS screen_name, * FROM {analysis}
    WHERE health_status = 'Distressed'
    UNION ALL
    SELECT 'stressed' AS screen_name, * FROM {analysis}
    WHERE health_status = 'Stressed'
    UNION ALL
    SELECT 'deleveraging' AS screen_name, * FROM {analysis}
    WHERE current_total_debt > 750
      AND current_net_leverage > 3.0
      AND nearest_maturity_date <= DATE '2028-12-31'
    UNION ALL
    SELECT 'equity_partnership' AS screen_name, * FROM {analysis}
    WHERE current_total_debt > 500
      AND current_net_leverage > 3.0
      AND investment_date IS NOT NULL
      AND DATEDIFF(COALESCE(CAST(study_asof_date AS DATE), CURRENT_DATE()), CAST(investment_date AS DATE)) / 365.25 > 3.0
    UNION ALL
    SELECT 'declining_credit' AS screen_name, * FROM {analysis}
    WHERE current_total_debt > 500
      AND (yoy_first_lien_ytm_change >= 0.05 OR yoy_second_lien_unsecured_ytm_change >= 0.10)
    UNION ALL
    SELECT 'improving_credit' AS screen_name, * FROM {analysis}
    WHERE current_total_debt > 500
      AND (yoy_first_lien_ytm_change <= -0.05 OR yoy_second_lien_unsecured_ytm_change <= -0.10)
    UNION ALL
    SELECT 's3' AS screen_name, * FROM {analysis}
    WHERE LOWER(COALESCE(CAST(workbook_s3_candidate AS STRING), '')) IN ('1', 'x', 'y', 'yes', 'true', 'candidate')
    UNION ALL
    SELECT 'amend_extend' AS screen_name, * FROM {analysis}
    WHERE LOWER(COALESCE(CAST(workbook_amend_extend_candidate AS STRING), '')) IN ('1', 'x', 'y', 'yes', 'true', 'candidate')
    UNION ALL
    SELECT 'debt_down' AS screen_name, * FROM {analysis}
    WHERE LOWER(COALESCE(CAST(workbook_debt_down_candidate AS STRING), '')) IN ('1', 'x', 'y', 'yes', 'true', 'candidate')
    UNION ALL
    SELECT 'debt_up' AS screen_name, * FROM {analysis}
    WHERE LOWER(COALESCE(CAST(workbook_debt_up_candidate AS STRING), '')) IN ('1', 'x', 'y', 'yes', 'true', 'candidate')
)
SELECT
    SHA2(CONCAT_WS('|', ownership_id, screen_name, {rule_version}), 256) AS opportunity_screen_id,
    {rule_version} AS rule_version,
    screen_name,
    ownership_id,
    portco_id,
    sponsor_id,
    company,
    sponsor,
    COALESCE(fund_used, fund) AS fund,
    industry,
    health_status,
    current_total_debt AS total_debt,
    current_net_leverage AS net_leverage,
    first_lien_price,
    first_lien_ytm,
    second_lien_unsecured_price,
    second_lien_unsecured_ytm,
    nearest_maturity_date,
    investment_date,
    DATEDIFF(COALESCE(CAST(study_asof_date AS DATE), CURRENT_DATE()), CAST(investment_date AS DATE)) / 365.25 AS years_held,
    source_excel_row,
    ROW_NUMBER() OVER (
        PARTITION BY screen_name
        ORDER BY current_total_debt DESC NULLS LAST, current_net_leverage DESC NULLS LAST
    ) AS screen_rank
FROM candidates
""".strip()


def sponsor_summary_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("vw_sponsor_summary", catalog, schema, table_prefix)
    analysis = _output_qualified("vw_company_analysis_snapshot", catalog, schema, table_prefix)
    screens = _output_qualified("fact_opportunity_screen", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH screen_counts AS (
    SELECT
        sponsor,
        COUNT(DISTINCT CASE WHEN health_status = 'Stressed' THEN portco_id END) AS stressed_count,
        COUNT(DISTINCT CASE WHEN health_status = 'Distressed' THEN portco_id END) AS distressed_count
    FROM {screens}
    GROUP BY sponsor
)
SELECT
    a.sponsor,
    COUNT(DISTINCT a.portco_id) AS active_investments,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY a.current_net_leverage) AS median_net_leverage,
    MIN(a.nearest_maturity_date) AS nearest_maturity_date,
    COUNT(a.first_lien_ytm) AS debt_trading_coverage,
    COALESCE(MAX(sc.stressed_count), 0) AS stressed_count,
    COALESCE(MAX(sc.distressed_count), 0) AS distressed_count,
    COALESCE(MAX(sc.stressed_count), 0) + COALESCE(MAX(sc.distressed_count), 0) AS at_risk_count
FROM {analysis} a
LEFT JOIN screen_counts sc
    ON sc.sponsor = a.sponsor
WHERE LOWER(COALESCE(a.current_status, '')) = 'active'
GROUP BY a.sponsor
""".strip()


def screen_rule_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    return _values_view_sql(
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['dim_opportunity_screen_rule']}",
        SCREEN_RULES,
        ["screen_name", "rule_version", "definition"],
        catalog=catalog,
        schema=schema,
        table_prefix=table_prefix,
    )


def validation_reconciliation_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("validation_reconciliation", catalog, schema, table_prefix)
    stage = _qualified("stage_consolidated_company_snapshot", catalog, schema, table_prefix)
    bridge = _qualified("bridge_fund_portco", catalog, schema, table_prefix)
    financial = _qualified("fact_financial_snapshot", catalog, schema, table_prefix)
    internal_financial = _qualified("source_internal_financial_snapshot", catalog, schema, table_prefix)
    market = _qualified("fact_market_trading_snapshot", catalog, schema, table_prefix)
    internal_market = _qualified("source_internal_market_trading_snapshot", catalog, schema, table_prefix)
    debt = _qualified("fact_debt_instrument_snapshot", catalog, schema, table_prefix)
    internal_debt = _qualified("source_internal_debt_instrument_snapshot", catalog, schema, table_prefix)
    xref = _qualified("xref_portco_internal_ids", catalog, schema, table_prefix)
    debt_features = _qualified("fact_internal_debt_feature_snapshot", catalog, schema, table_prefix)
    transaction = _qualified("fact_transaction_entry", catalog, schema, table_prefix)
    screens = _output_qualified("fact_opportunity_screen", catalog, schema, table_prefix)
    compliance = _qualified("fact_company_compliance_snapshot", catalog, schema, table_prefix)
    structured = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH base AS (
    SELECT COUNT(DISTINCT portco_id) AS total_portcos FROM {stage}
),
metrics AS (
    SELECT 'portfolio_companies_total' AS metric, CAST(COUNT(DISTINCT portco_id) AS DOUBLE) AS actual, CAST(1839 AS DOUBLE) AS target FROM {stage}
    UNION ALL
    SELECT 'portfolio_companies_active', CAST(SUM(CASE WHEN LOWER(COALESCE(current_status, '')) = 'active' THEN 1 ELSE 0 END) AS DOUBLE), CAST(1339 AS DOUBLE) FROM {bridge}
    UNION ALL
    SELECT 'debt_trading_coverage', CAST(COUNT(DISTINCT portco_id) AS DOUBLE), CAST(645 AS DOUBLE) FROM {market}
    UNION ALL
    SELECT 'leverage_coverage', CAST(COUNT(DISTINCT CASE WHEN current_net_leverage IS NOT NULL THEN portco_id END) AS DOUBLE), CAST(512 AS DOUBLE) FROM {financial}
    UNION ALL
    SELECT 'maturity_coverage', CAST(COUNT(DISTINCT CASE WHEN nearest_maturity_date IS NOT NULL THEN portco_id END) AS DOUBLE), CAST(796 AS DOUBLE) FROM {debt}
    UNION ALL
    SELECT 'equity_commitment_coverage', CAST(COUNT(DISTINCT CASE WHEN equity_commitment IS NOT NULL THEN ownership_id END) AS DOUBLE), CAST(861 AS DOUBLE) FROM {transaction}
    UNION ALL
    SELECT 'yoy_financials_coverage', CAST(COUNT(DISTINCT CASE WHEN yoy_revenue_growth IS NOT NULL OR yoy_ebitda_growth IS NOT NULL THEN portco_id END) AS DOUBLE), CAST(221 AS DOUBLE) FROM {financial}
    UNION ALL
    SELECT 'internal_id_match_coverage', CAST(COUNT(DISTINCT CASE WHEN match_confidence <> 'unmatched' THEN portco_id END) AS DOUBLE), NULL FROM {xref}
    UNION ALL
    SELECT 'internal_debt_feature_coverage', CAST(COUNT(DISTINCT portco_id) AS DOUBLE), NULL FROM {debt_features}
    UNION ALL
    SELECT 'internal_financial_coverage', CAST(COUNT(DISTINCT portco_id) AS DOUBLE), NULL FROM {internal_financial}
    UNION ALL
    SELECT 'internal_market_price_coverage', CAST(COUNT(DISTINCT CASE WHEN price IS NOT NULL THEN portco_id END) AS DOUBLE), NULL FROM {internal_market}
    UNION ALL
    SELECT 'internal_maturity_coverage', CAST(COUNT(DISTINCT CASE WHEN nearest_maturity_date IS NOT NULL THEN portco_id END) AS DOUBLE), NULL FROM {internal_debt}
    UNION ALL
    SELECT CONCAT('screen_count_', screen_name), CAST(COUNT(DISTINCT portco_id) AS DOUBLE), NULL FROM {screens} GROUP BY screen_name
    UNION ALL
    SELECT 'internal_id_match_coverage_pct', CAST(COUNT(DISTINCT CASE WHEN match_confidence <> 'unmatched' THEN portco_id END) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {xref} CROSS JOIN base
    UNION ALL
    SELECT 'internal_debt_feature_coverage_pct', CAST(COUNT(DISTINCT portco_id) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {debt_features} CROSS JOIN base
    UNION ALL
    SELECT 'internal_financial_coverage_pct', CAST(COUNT(DISTINCT portco_id) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {internal_financial} CROSS JOIN base
    UNION ALL
    SELECT 'internal_market_price_coverage_pct', CAST(COUNT(DISTINCT CASE WHEN price IS NOT NULL THEN portco_id END) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {internal_market} CROSS JOIN base
    UNION ALL
    SELECT 'internal_maturity_coverage_pct', CAST(COUNT(DISTINCT CASE WHEN nearest_maturity_date IS NOT NULL THEN portco_id END) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {internal_debt} CROSS JOIN base
    UNION ALL
    SELECT 'compliance_private_data_count', CAST(COUNT(DISTINCT CASE WHEN private_data_flag THEN portco_id END) AS DOUBLE), NULL FROM {compliance}
    UNION ALL
    SELECT 'compliance_direct_lending_only_count', CAST(COUNT(DISTINCT CASE WHEN direct_lending_only_flag THEN portco_id END) AS DOUBLE), NULL FROM {compliance}
    UNION ALL
    SELECT 'compliance_pe_restricted_count', CAST(COUNT(DISTINCT CASE WHEN pe_investment_team_restricted_flag THEN portco_id END) AS DOUBLE), NULL FROM {compliance}
    UNION ALL
    SELECT 'compliance_disqualified_lender_blocked_count', CAST(COUNT(DISTINCT CASE WHEN disqualified_lender_blocked_flag THEN portco_id END) AS DOUBLE), NULL FROM {compliance}
    UNION ALL
    SELECT 'compliance_disqualified_lender_blocked_pct', CAST(COUNT(DISTINCT CASE WHEN disqualified_lender_blocked_flag THEN portco_id END) AS DOUBLE) / NULLIF(MAX(base.total_portcos), 0), NULL FROM {compliance} CROSS JOIN base
    UNION ALL
    SELECT 'structured_investment_count', CAST(COUNT(DISTINCT structured_investment_id) AS DOUBLE), NULL FROM {structured}
    UNION ALL
    SELECT 'structured_fund_count', CAST(COUNT(DISTINCT fund_id) AS DOUBLE), NULL FROM {structured}
    UNION ALL
    SELECT 'structured_linked_benchmark_portco_count', CAST(COUNT(DISTINCT CASE WHEN benchmark_portco_id IS NOT NULL THEN structured_portco_id END) AS DOUBLE), NULL FROM {structured}
)
SELECT
    metric,
    actual,
    target,
    CASE WHEN target IS NULL THEN NULL ELSE actual - target END AS difference,
    CASE
        WHEN target IS NULL THEN 'reference_only'
        WHEN actual = target THEN 'match'
        ELSE 'review'
    END AS status
FROM metrics
""".strip()


def structured_company_detail_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("company_detail_structured_universe", catalog, schema, table_prefix)
    investments = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    internal_financial = _qualified("source_internal_financial_snapshot", catalog, schema, table_prefix)
    internal_market = _qualified("source_internal_market_trading_snapshot", catalog, schema, table_prefix)
    internal_debt = _qualified("source_internal_debt_instrument_snapshot", catalog, schema, table_prefix)
    debt_features = _qualified("fact_internal_debt_feature_snapshot", catalog, schema, table_prefix)
    fund_perf = _qualified("fact_fund_performance_snapshot", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH internal_financial_latest AS (
    SELECT *
    FROM (
        SELECT
            f.*,
            ROW_NUMBER() OVER (
                PARTITION BY f.portco_id
                ORDER BY f.period_end_date DESC NULLS LAST, f.asof_date DESC NULLS LAST
            ) AS rn
        FROM {internal_financial} f
    ) x
    WHERE rn = 1
),
market_instruments AS (
    SELECT
        d.portco_id,
        d.instrument_id_uniq,
        d.asof_date,
        d.tranche_type,
        d.tranche_size,
        d.rate,
        d.maturity_date,
        d.nearest_maturity_date,
        d.nearest_maturity_type,
        m.price,
        m.ytw,
        m.ytm,
        m.spread,
        m.duration,
        m.price_source
    FROM {internal_debt} d
    LEFT JOIN {internal_market} m
        ON m.portco_id = d.portco_id
        AND m.instrument_id_uniq = d.instrument_id_uniq
),
ranked_1l AS (
    SELECT *
    FROM market_instruments
    WHERE LOWER(COALESCE(tranche_type, '')) LIKE '%1l%'
       OR LOWER(COALESCE(tranche_type, '')) LIKE '%first%'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY portco_id
        ORDER BY tranche_size DESC NULLS LAST, price DESC NULLS LAST
    ) = 1
),
ranked_2l AS (
    SELECT *
    FROM market_instruments
    WHERE LOWER(COALESCE(tranche_type, '')) LIKE '%2l%'
       OR LOWER(COALESCE(tranche_type, '')) LIKE '%second%'
       OR LOWER(COALESCE(tranche_type, '')) LIKE '%unsecured%'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY portco_id
        ORDER BY tranche_size DESC NULLS LAST, price DESC NULLS LAST
    ) = 1
),
credit_features AS (
    SELECT
        portco_id,
        MAX(CASE WHEN LOWER(feature_name) LIKE '%rating%' OR LOWER(feature_name) LIKE '%rtg%' THEN feature_value END) AS credit_rating,
        MAX(CASE WHEN feature_name = 'broad_industry' THEN feature_value END) AS debt_feature_broad_industry,
        MAX(CASE WHEN feature_name = 'narrow_industry' THEN feature_value END) AS debt_feature_narrow_industry
    FROM {debt_features}
    GROUP BY portco_id
),
latest_fund_performance AS (
    SELECT *
    FROM (
        SELECT
            p.*,
            ROW_NUMBER() OVER (
                PARTITION BY p.fund_id, p.source_system
                ORDER BY p.as_of_date DESC NULLS LAST
            ) AS rn
        FROM {fund_perf} p
    ) x
    WHERE rn = 1
)
SELECT
    i.structured_investment_id,
    i.sponsor_id,
    i.sponsor_name AS sponsor,
    i.fund_id,
    i.fund_name AS fund,
    i.vintage,
    i.structured_portco_id,
    i.benchmark_portco_id,
    i.factset_gp_entity_id,
    i.factset_fund_entity_id,
    i.factset_portco_entity_id,
    i.company_name AS company,
    i.company_country,
    COALESCE(cf.debt_feature_broad_industry, i.industry) AS industry,
    cf.debt_feature_narrow_industry,
    i.investment_date,
    i.exit_date,
    CASE WHEN i.exit_date IS NULL THEN 'Active' ELSE 'Realized' END AS active_realized_status,
    DATEDIFF(COALESCE(CAST(i.exit_date AS DATE), CURRENT_DATE()), CAST(i.investment_date AS DATE)) / 365.25 AS holding_years,
    fi.current_total_debt,
    fi.current_total_cash,
    fi.current_net_debt,
    fi.current_ltm_revenue,
    fi.current_ltm_ebitda,
    fi.current_ebitda_margin,
    fi.current_gross_leverage,
    fi.current_net_leverage,
    fi.ltv,
    fl.instrument_id_uniq AS first_lien_instrument_id,
    fl.tranche_size AS first_lien_tranche_size,
    fl.price AS first_lien_price,
    fl.ytw AS first_lien_ytw,
    fl.ytm AS first_lien_ytm,
    sl.instrument_id_uniq AS second_lien_instrument_id,
    sl.tranche_size AS second_lien_tranche_size,
    sl.price AS second_lien_unsecured_price,
    sl.ytw AS second_lien_unsecured_ytw,
    sl.ytm AS second_lien_unsecured_ytm,
    COALESCE(fl.nearest_maturity_date, sl.nearest_maturity_date) AS nearest_maturity_date,
    COALESCE(fl.nearest_maturity_type, sl.nearest_maturity_type) AS nearest_maturity_type,
    cf.credit_rating,
    i.total_investment_amount AS deal_size_tev,
    i.amount_invested_by_sponsor AS equity_commitment,
    CAST(NULL AS DOUBLE) AS entry_multiple,
    CAST(NULL AS DOUBLE) AS entry_total_leverage,
    CAST(NULL AS DOUBLE) AS entry_net_leverage,
    i.entry_type,
    i.transaction_close_date,
    i.round_of_funding,
    i.investment_description,
    p.preqin_asset_class,
    p.commitment_usd_m AS fund_commitment_usd_m,
    p.pct_called AS fund_pct_called,
    p.dpi AS fund_dpi,
    p.rvpi AS fund_rvpi,
    p.net_moic AS fund_net_moic,
    p.net_irr AS fund_net_irr,
    CASE
        WHEN fi.current_total_debt IS NULL OR fi.current_total_debt <= 500 THEN 'Performing / Other'
        WHEN fl.ytm > 0.14 OR sl.ytm > 0.20 THEN 'Distressed'
        WHEN (fl.ytm > 0.10 AND fl.ytm < 0.14) OR (sl.ytm > 0.13 AND sl.ytm < 0.20) THEN 'Stressed'
        ELSE 'Performing / Other'
    END AS health_status,
    CASE
        WHEN fi.current_net_leverage > 6 OR fi.ltv > 0.70 THEN '>6x net leverage / >70% LTV'
        WHEN fi.current_net_leverage >= 4 OR fi.ltv >= 0.50 THEN '4-6x net leverage / 50-70% LTV'
        WHEN fi.current_net_leverage IS NULL AND fi.ltv IS NULL THEN 'Insufficient Data'
        ELSE '<4x net leverage / <50% LTV'
    END AS leverage_ltv_bucket,
    i.source_system,
    i.source_confidence,
    i.match_method
FROM {investments} i
LEFT JOIN internal_financial_latest fi
    ON fi.portco_id = i.benchmark_portco_id
LEFT JOIN ranked_1l fl
    ON fl.portco_id = i.benchmark_portco_id
LEFT JOIN ranked_2l sl
    ON sl.portco_id = i.benchmark_portco_id
LEFT JOIN credit_features cf
    ON cf.portco_id = i.benchmark_portco_id
LEFT JOIN latest_fund_performance p
    ON p.fund_id = i.fund_id
""".strip()


def structured_sponsor_exits_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("sponsor_exits_structured_universe", catalog, schema, table_prefix)
    investments = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    structured_investment_id AS exit_event_id,
    structured_portco_id,
    benchmark_portco_id,
    sponsor_id,
    sponsor_name AS sponsor,
    fund_id,
    fund_name AS fund,
    vintage,
    company_name AS company,
    industry,
    investment_date,
    exit_date,
    COALESCE(investment_description, entry_type, 'Unknown') AS raw_exit_type,
    CASE
        WHEN LOWER(COALESCE(investment_description, entry_type, '')) LIKE '%strategic%' THEN 'Sponsor-to-Strategic'
        WHEN LOWER(COALESCE(investment_description, entry_type, '')) LIKE '%sponsor%' THEN 'Sponsor-to-Sponsor'
        WHEN LOWER(COALESCE(investment_description, entry_type, '')) LIKE '%ipo%' THEN 'IPO'
        WHEN LOWER(COALESCE(investment_description, entry_type, '')) LIKE '%secondary%' THEN 'Secondary'
        ELSE 'Other'
    END AS normalized_exit_type,
    DATEDIFF(CAST(exit_date AS DATE), CAST(investment_date AS DATE)) / 365.25 AS holding_years,
    exit_date >= ADD_MONTHS(CURRENT_DATE(), -12) AS exited_since_one_year_ago,
    source_system,
    source_confidence
FROM {investments}
WHERE exit_date IS NOT NULL
""".strip()


def fund_dry_powder_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("fund_dry_powder", catalog, schema, table_prefix)
    fund_perf = _qualified("fact_fund_performance_snapshot", catalog, schema, table_prefix)
    investments = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH latest_fund_perf AS (
    SELECT *
    FROM {fund_perf}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY fund_id, source_system, source_fund_id, source_fund_source_table
        ORDER BY as_of_date DESC NULLS LAST
    ) = 1
),
fund_company_counts AS (
    SELECT
        fund_id,
        COUNT(DISTINCT structured_portco_id) AS company_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NULL THEN structured_portco_id END) AS active_company_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NOT NULL THEN structured_portco_id END) AS realized_company_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY DATEDIFF(COALESCE(CAST(exit_date AS DATE), CURRENT_DATE()), CAST(investment_date AS DATE)) / 365.25
        ) AS median_holding_period_years
    FROM {investments}
    WHERE investment_date IS NOT NULL
    GROUP BY fund_id
)
SELECT
    p.fund_id,
    p.source_system,
    p.source_fund_id,
    p.source_fund_source_table,
    p.preqin_asset_class,
    p.sponsor_name,
    p.fund_name,
    p.vintage,
    p.fund_strategy,
    p.fund_status,
    p.as_of_date,
    p.commitment_usd_m,
    p.pct_called,
    1.0 - p.pct_called AS pct_not_called,
    p.commitment_usd_m * (1.0 - p.pct_called) AS estimated_dry_powder_usd_m,
    p.dpi,
    p.rvpi,
    p.net_moic,
    p.net_irr,
    CASE
        WHEN YEAR(CURRENT_DATE()) - p.vintage <= 3 THEN '3 years or less'
        WHEN YEAR(CURRENT_DATE()) - p.vintage BETWEEN 4 AND 5 THEN '4-5 years'
        WHEN YEAR(CURRENT_DATE()) - p.vintage > 5 THEN 'More than 5 years'
        ELSE 'Unknown'
    END AS vintage_tenor_bucket,
    c.company_count,
    c.active_company_count,
    c.realized_company_count,
    c.median_holding_period_years
FROM latest_fund_perf p
LEFT JOIN fund_company_counts c
    ON c.fund_id = p.fund_id
""".strip()


def preqin_dry_powder_macro_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    *,
    preqin_catalog: str = DEFAULT_PREQIN_CATALOG,
    preqin_schema: str = DEFAULT_PREQIN_SCHEMA,
) -> str:
    target = _output_qualified("preqin_dry_powder_macro", catalog, schema, table_prefix)
    drypowder_pe = f"{preqin_catalog}.{preqin_schema}.DRYPOWDER_PE"
    drypowder_inf = f"{preqin_catalog}.{preqin_schema}.DRYPOWDER_INF"
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    TO_DATE(CAST(TRY_CAST(DATE AS BIGINT) AS STRING), 'yyyyMMdd') AS as_of_date,
    'private_equity' AS asset_class,
    stack(
        4,
        'Buyout', TRY_CAST(`"BUYOUT (USD BN)"` AS DOUBLE),
        'Growth', TRY_CAST(`"GROWTH (USD BN)"` AS DOUBLE),
        'Balanced', TRY_CAST(`"BALANCED (USD BN)"` AS DOUBLE),
        'Secondaries', TRY_CAST(`"SECONDARIES (USD BN)"` AS DOUBLE)
    ) AS (strategy, dry_powder_usd_bn)
FROM {drypowder_pe}
UNION ALL
SELECT
    TO_DATE(CAST(TRY_CAST(DATE AS BIGINT) AS STRING), 'yyyyMMdd') AS as_of_date,
    'infrastructure' AS asset_class,
    stack(
        4,
        'Infrastructure Core', TRY_CAST(`"INFRASTRUCTURE CORE (USD BN)"` AS DOUBLE),
        'Infrastructure Core Plus', TRY_CAST(`"INFRASTRUCTURE CORE PLUS (USD BN)"` AS DOUBLE),
        'Infrastructure Value Added', TRY_CAST(`"INFRASTRUCTURE VALUE ADDED (USD BN)"` AS DOUBLE),
        'Infrastructure Opportunistic', TRY_CAST(`"INFRASTRUCTURE OPPORTUNISTIC (USD BN)"` AS DOUBLE)
    ) AS (strategy, dry_powder_usd_bn)
FROM {drypowder_inf}
""".strip()


def preqin_buyout_deal_flow_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    *,
    preqin_catalog: str = DEFAULT_PREQIN_CATALOG,
    preqin_schema: str = DEFAULT_PREQIN_SCHEMA,
) -> str:
    target = _output_qualified("preqin_buyout_deal_flow", catalog, schema, table_prefix)
    buyout_deals = (
        f"{preqin_catalog}.{preqin_schema}."
        "DEAL_ASSET_COMPANY_FUNDAMENTAL_BUYOUT"
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    CAST(`"DEAL ID"` AS STRING) AS deal_id,
    `"TARGET COMPANY"` AS target_company,
    `"TARGET ID"` AS target_id,
    TO_DATE(CAST(TRY_CAST(`"DEAL DATE"` AS BIGINT) AS STRING), 'yyyyMMdd') AS deal_date,
    YEAR(TO_DATE(CAST(TRY_CAST(`"DEAL DATE"` AS BIGINT) AS STRING), 'yyyyMMdd')) AS deal_year,
    `"DEAL TYPE"` AS deal_type,
    `"DEAL STATUS"` AS deal_status,
    CAST(EXIT AS BOOLEAN) AS exit_flag,
    `"PORTFOLIO COMPANY REGION"` AS portfolio_company_region,
    `"PORTFOLIO COMPANY COUNTRY"` AS portfolio_company_country,
    `"PRIMARY INDUSTRY"` AS primary_industry,
    `"INDUSTRY VERTICALS"` AS industry_verticals,
    TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) AS deal_size_usd_m,
    TRY_CAST(`"ENTERPRISE VALUE USD (MN)"` AS DOUBLE) AS enterprise_value_usd_m,
    TRY_CAST(`"EQUITY USD (MN)"` AS DOUBLE) AS equity_usd_m,
    TRY_CAST(`"DEBT USD (MN)"` AS DOUBLE) AS debt_usd_m,
    CASE
        WHEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) < 100 THEN '<$0.1B'
        WHEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) < 1000 THEN '$0.1B-$1B'
        WHEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) < 2500 THEN '$1B-$2.5B'
        WHEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) < 5000 THEN '$2.5B-$5B'
        WHEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) < 10000 THEN '$5B-$10B'
        ELSE '>$10B'
    END AS deal_size_bucket,
    CASE WHEN CAST(EXIT AS BOOLEAN) THEN TRY_CAST(`"DEAL SIZE USD (MN)"` AS DOUBLE) ELSE 0 END AS exit_value_usd_m
FROM {buyout_deals}
WHERE TO_DATE(CAST(TRY_CAST(`"DEAL DATE"` AS BIGINT) AS STRING), 'yyyyMMdd') IS NOT NULL
""".strip()


def universe_reconciliation_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("universe_reconciliation", catalog, schema, table_prefix)
    structured = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    bridge = _qualified("bridge_fund_portco", catalog, schema, table_prefix)
    funds = _qualified("dim_fund", catalog, schema, table_prefix)
    sponsors = _qualified("dim_sponsor", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH benchmark AS (
    SELECT
        b.fund_id,
        b.sponsor_id,
    s.sponsor_name,
        f.fund_name,
        f.vintage,
        b.portco_id,
        b.current_status,
        b.investment_date,
        b.exit_date,
        b.exit_type
    FROM {bridge} b
    LEFT JOIN {funds} f
        ON f.fund_id = b.fund_id
    LEFT JOIN {sponsors} s
        ON s.sponsor_id = b.sponsor_id
),
structured AS (
    SELECT
        fund_id,
        sponsor_id,
        sponsor_name,
        fund_name,
        vintage,
        benchmark_portco_id,
        structured_portco_id,
        investment_date,
        exit_date,
        CASE WHEN exit_date IS NULL THEN 'active' ELSE 'realized' END AS structured_status
    FROM {structured}
),
fund_scope AS (
    SELECT DISTINCT
        COALESCE(b.fund_id, s.fund_id) AS fund_id,
        COALESCE(b.sponsor_id, s.sponsor_id) AS sponsor_id,
        COALESCE(b.sponsor_name, s.sponsor_name) AS sponsor_name,
        COALESCE(b.fund_name, s.fund_name) AS fund_name,
        COALESCE(b.vintage, s.vintage) AS vintage
    FROM benchmark b
    FULL OUTER JOIN structured s
        ON s.fund_id = b.fund_id
),
benchmark_counts AS (
    SELECT
        fund_id,
        COUNT(DISTINCT portco_id) AS benchmark_investment_count,
        COUNT(DISTINCT CASE WHEN LOWER(COALESCE(current_status, '')) = 'active' THEN portco_id END) AS benchmark_active_count,
        COUNT(DISTINCT CASE WHEN LOWER(COALESCE(current_status, '')) <> 'active' THEN portco_id END) AS benchmark_realized_count
    FROM benchmark
    GROUP BY fund_id
),
structured_counts AS (
    SELECT
        fund_id,
        COUNT(DISTINCT structured_portco_id) AS structured_investment_count,
        COUNT(DISTINCT CASE WHEN structured_status = 'active' THEN structured_portco_id END) AS structured_active_count,
        COUNT(DISTINCT CASE WHEN structured_status = 'realized' THEN structured_portco_id END) AS structured_realized_count,
        COUNT(DISTINCT CASE WHEN benchmark_portco_id IS NOT NULL THEN structured_portco_id END) AS structured_linked_to_benchmark_count
    FROM structured
    GROUP BY fund_id
),
matched_counts AS (
    SELECT
        b.fund_id,
        COUNT(DISTINCT b.portco_id) AS matched_investment_count,
        COUNT(DISTINCT CASE WHEN CAST(b.investment_date AS DATE) = CAST(s.investment_date AS DATE) THEN b.portco_id END) AS investment_date_match_count,
        COUNT(DISTINCT CASE WHEN CAST(b.exit_date AS DATE) = CAST(s.exit_date AS DATE) THEN b.portco_id END) AS exit_date_match_count,
        COUNT(DISTINCT CASE
            WHEN LOWER(COALESCE(b.current_status, '')) = s.structured_status THEN b.portco_id
        END) AS active_realized_match_count
    FROM benchmark b
    JOIN structured s
        ON s.fund_id = b.fund_id
        AND s.benchmark_portco_id = b.portco_id
    GROUP BY b.fund_id
)
SELECT
    fs.sponsor_id,
    fs.sponsor_name,
    fs.fund_id,
    fs.fund_name,
    fs.vintage,
    COALESCE(bc.benchmark_investment_count, 0) AS benchmark_investment_count,
    COALESCE(sc.structured_investment_count, 0) AS structured_investment_count,
    COALESCE(mc.matched_investment_count, 0) AS matched_investment_count,
    COALESCE(bc.benchmark_investment_count, 0) - COALESCE(mc.matched_investment_count, 0) AS benchmark_only_count,
    COALESCE(sc.structured_investment_count, 0) - COALESCE(mc.matched_investment_count, 0) AS structured_extra_count,
    COALESCE(bc.benchmark_active_count, 0) AS benchmark_active_count,
    COALESCE(sc.structured_active_count, 0) AS structured_active_count,
    COALESCE(bc.benchmark_realized_count, 0) AS benchmark_realized_count,
    COALESCE(sc.structured_realized_count, 0) AS structured_realized_count,
    COALESCE(sc.structured_linked_to_benchmark_count, 0) AS structured_linked_to_benchmark_count,
    COALESCE(mc.investment_date_match_count, 0) AS investment_date_match_count,
    COALESCE(mc.exit_date_match_count, 0) AS exit_date_match_count,
    COALESCE(mc.active_realized_match_count, 0) AS active_realized_match_count,
    COALESCE(mc.matched_investment_count, 0) / NULLIF(COALESCE(bc.benchmark_investment_count, 0), 0) AS benchmark_replication_pct
FROM fund_scope fs
LEFT JOIN benchmark_counts bc
    ON bc.fund_id = fs.fund_id
LEFT JOIN structured_counts sc
    ON sc.fund_id = fs.fund_id
LEFT JOIN matched_counts mc
    ON mc.fund_id = fs.fund_id
""".strip()


def website_portfolio_latest_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("website_portfolio_latest", catalog, schema, table_prefix)
    website = _qualified("source_sponsor_website_holding", catalog, schema, table_prefix)
    candidates = _qualified("source_sponsor_site_holding_candidate", catalog, schema, table_prefix)
    sponsor_runs = _qualified("control_website_crawl_run_sponsor", catalog, schema, table_prefix)
    runs = _qualified("control_website_crawl_run", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH latest_success AS (
    SELECT s.sponsor, s.crawl_run_id
    FROM {sponsor_runs} s JOIN {runs} r ON r.crawl_run_id = s.crawl_run_id
    WHERE {published_sponsor_predicate('s', 'r')}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY LOWER(TRIM(s.sponsor))
        ORDER BY s.run_started_at DESC NULLS LAST, s.crawl_run_id DESC
    ) = 1
),
ranked AS (
    SELECT
        w.*,
        LOWER(TRIM(REGEXP_REPLACE(w.company_name, '[^a-zA-Z0-9]+', ' '))) AS company_key,
        ROW_NUMBER() OVER (
            PARTITION BY sponsor_id, COALESCE(fund_id, fund_name_raw, ''), LOWER(TRIM(REGEXP_REPLACE(w.company_name, '[^a-zA-Z0-9]+', ' ')))
            ORDER BY
                CASE WHEN fund_match_confidence = 'high' THEN 0 ELSE 1 END,
                CASE WHEN source_confidence = 'high' THEN 0 WHEN source_confidence = 'medium' THEN 1 ELSE 2 END,
                CAST(w.knowledge_datetime AS TIMESTAMP) DESC NULLS LAST
        ) AS rn
    FROM {website} w
    JOIN {candidates} c
        ON c.crawl_run_id = w.crawl_run_id
        AND c.website_holding_candidate_id = w.website_holding_candidate_id
        AND c.company_classification = 'company'
        AND c.company_classification_version = '{CLASSIFICATION_VERSION}'
    JOIN latest_success r
        ON r.crawl_run_id = w.crawl_run_id
        AND LOWER(TRIM(r.sponsor)) = LOWER(TRIM(w.sponsor_name))
    WHERE w.company_name IS NOT NULL
)
SELECT *
FROM ranked
WHERE rn = 1
""".strip()


def website_crawl_health_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("website_crawl_health", catalog, schema, table_prefix)
    sponsor_runs = _qualified("control_website_crawl_run_sponsor", catalog, schema, table_prefix)
    runs = _qualified("control_website_crawl_run", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH ranked AS (
    SELECT
        s.*,
        r.status AS run_status,
        r.environment,
        r.catalog,
        r.schema,
        r.code_version,
        r.config_json,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(s.sponsor))
            ORDER BY s.run_started_at DESC NULLS LAST, s.crawl_run_id DESC
        ) AS rn
    FROM {sponsor_runs} s
    JOIN {runs} r
        ON r.crawl_run_id = s.crawl_run_id
)
SELECT *
FROM ranked
WHERE rn = 1
""".strip()


def website_bridge_reconciliation_output_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("website_bridge_reconciliation", catalog, schema, table_prefix)
    reconciliation = _qualified("validation_website_bridge_reconciliation", catalog, schema, table_prefix)
    sponsor_runs = _qualified("control_website_crawl_run_sponsor", catalog, schema, table_prefix)
    runs = _qualified("control_website_crawl_run", catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH latest_success AS (
    SELECT s.sponsor, s.crawl_run_id
    FROM {sponsor_runs} s JOIN {runs} r ON r.crawl_run_id = s.crawl_run_id
    WHERE {published_sponsor_predicate('s', 'r')}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY LOWER(TRIM(s.sponsor))
        ORDER BY s.run_started_at DESC NULLS LAST, s.crawl_run_id DESC
    ) = 1
)
SELECT
    r.crawl_run_id,
    r.sponsor,
    CAST(r.seed_ownership_count AS BIGINT) AS benchmark_ownership_count,
    CAST(r.website_bridge_count AS BIGINT) AS website_company_count,
    CAST(r.matched_seed_ownership_count AS BIGINT) AS website_matched_benchmark_count,
    CAST(r.website_unmatched_count AS BIGINT) AS website_only_count,
    GREATEST(
        CAST(r.seed_ownership_count AS BIGINT) - CAST(r.matched_seed_ownership_count AS BIGINT),
        0
    ) AS benchmark_only_count,
    CAST(r.matched_seed_pct AS DOUBLE) AS website_to_benchmark_match_pct,
    r.knowledge_datetime
FROM {reconciliation} r
JOIN latest_success s
    ON s.crawl_run_id = r.crawl_run_id
    AND LOWER(TRIM(s.sponsor)) = LOWER(TRIM(r.sponsor))
""".strip()


def website_crawl_view_scripts(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    return {
        "output_website_portfolio_latest": website_portfolio_latest_view_sql(catalog, schema, table_prefix),
        "output_website_crawl_health": website_crawl_health_view_sql(catalog, schema, table_prefix),
        "output_website_bridge_reconciliation": website_bridge_reconciliation_output_view_sql(
            catalog,
            schema,
            table_prefix,
        ),
    }


def gold_website_crawl_view_scripts(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    source_latest = _output_qualified("website_portfolio_latest", catalog, source_schema, table_prefix)
    source_health = _output_qualified("website_crawl_health", catalog, source_schema, table_prefix)
    source_reconciliation = _output_qualified(
        "website_bridge_reconciliation",
        catalog,
        source_schema,
        table_prefix,
    )
    return {
        "v_pe_sponsor_website_portfolio_latest": (
            f"CREATE OR REPLACE VIEW {catalog}.{gold_schema}.v_pe_sponsor_website_portfolio_latest "
            f"AS SELECT * FROM {source_latest}"
        ),
        "v_pe_sponsor_website_crawl_coverage": (
            f"CREATE OR REPLACE VIEW {catalog}.{gold_schema}.v_pe_sponsor_website_crawl_coverage "
            f"AS SELECT * FROM {source_health}"
        ),
        "v_pe_sponsor_website_bridge_reconciliation": (
            f"CREATE OR REPLACE VIEW {catalog}.{gold_schema}.v_pe_sponsor_website_bridge_reconciliation "
            f"AS SELECT * FROM {source_reconciliation}"
        ),
    }


def external_holdings_merged_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_holdings_merged", catalog, schema, table_prefix)
    structured = _qualified("structured_fund_portco_investment", catalog, schema, table_prefix)
    website_latest = _output_qualified("website_portfolio_latest", catalog, schema, table_prefix)
    factset_priority = holding_source_priority("factset_pevc")
    website_priority = holding_source_priority("sponsor_website")
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH factset AS (
    SELECT
        'factset_pevc' AS holding_source_system,
        structured_investment_id AS source_holding_id,
        source_confidence,
        sponsor_id,
        sponsor_name,
        fund_id,
        fund_name,
        CAST(vintage AS STRING) AS vintage,
        structured_portco_id,
        factset_portco_entity_id AS source_portco_entity_id,
        benchmark_portco_id,
        company_name,
        LOWER(TRIM(REGEXP_REPLACE(company_name, '[^a-zA-Z0-9]+', ' '))) AS company_key,
        company_country,
        industry,
        CAST(investment_date AS DATE) AS investment_date,
        CAST(exit_date AS DATE) AS exit_date,
        entry_type,
        transaction_close_date,
        round_of_funding,
        total_investment_amount,
        investment_description,
        amount_invested_by_sponsor,
        transaction_fund_entity_id,
        transaction_fund_name,
        match_method,
        'auto_mapped' AS holding_review_status,
        CAST(NULL AS STRING) AS external_company_url,
        CAST(NULL AS STRING) AS company_description,
        CAST(NULL AS STRING) AS evidence_text,
        CAST(NULL AS STRING) AS source_url,
        CAST(NULL AS STRING) AS final_url,
        CAST(NULL AS STRING) AS crawl_run_id,
        CAST(NULL AS TIMESTAMP) AS knowledge_datetime,
        {factset_priority} AS source_priority
    FROM {structured}
    WHERE source_system = 'factset_pevc'
),
website AS (
    SELECT
        'sponsor_website' AS holding_source_system,
        website_holding_id AS source_holding_id,
        source_confidence,
        sponsor_id,
        sponsor_name,
        fund_id,
        fund_name,
        CAST(vintage AS STRING) AS vintage,
        structured_portco_id,
        CAST(NULL AS STRING) AS source_portco_entity_id,
        benchmark_portco_id,
        company_name,
        LOWER(TRIM(REGEXP_REPLACE(company_name, '[^a-zA-Z0-9]+', ' '))) AS company_key,
        company_country,
        industry,
        CAST(investment_date AS DATE) AS investment_date,
        CAST(exit_date AS DATE) AS exit_date,
        entry_type,
        transaction_close_date,
        round_of_funding,
        total_investment_amount,
        investment_description,
        amount_invested_by_sponsor,
        transaction_fund_entity_id,
        transaction_fund_name,
        fund_match_method AS match_method,
        holding_review_status,
        external_company_url,
        company_description,
        evidence_text,
        source_url,
        final_url,
        crawl_run_id,
        CAST(knowledge_datetime AS TIMESTAMP) AS knowledge_datetime,
        {website_priority} AS source_priority
    FROM {website_latest}
),
unioned AS (
    SELECT * FROM factset
    UNION ALL
    SELECT * FROM website
),
source_sets AS (
    SELECT
        sponsor_id,
        COALESCE(fund_id, fund_name, '') AS fund_scope_key,
        company_key,
        ARRAY_SORT(COLLECT_SET(holding_source_system)) AS contributing_source_systems
    FROM unioned
    WHERE company_key IS NOT NULL AND company_key <> ''
    GROUP BY sponsor_id, COALESCE(fund_id, fund_name, ''), company_key
),
ranked AS (
    SELECT
        u.*,
        s.contributing_source_systems,
        ROW_NUMBER() OVER (
            PARTITION BY u.sponsor_id, COALESCE(u.fund_id, u.fund_name, ''), u.company_key
            ORDER BY source_priority, investment_date ASC NULLS LAST, knowledge_datetime DESC NULLS LAST
        ) AS canonical_rank
    FROM unioned u
    LEFT JOIN source_sets s
        ON s.sponsor_id = u.sponsor_id
        AND s.fund_scope_key = COALESCE(u.fund_id, u.fund_name, '')
        AND s.company_key = u.company_key
    WHERE u.company_key IS NOT NULL AND u.company_key <> ''
)
SELECT
    *,
    canonical_rank = 1 AS canonical_holding_flag
FROM ranked
""".strip()


def external_fund_performance_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_fund_performance", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    external_fund_id,
    external_sponsor_id,
    sponsor_name,
    fund_name,
    vintage,
    fund_strategy,
    fund_status,
    commitment_usd_m,
    as_of_date,
    pct_called,
    dpi,
    rvpi,
    net_moic,
    net_irr,
    flagship_flag,
    flagship_selection_rationale,
    preqin_fund_id,
    factset_fund_entity_id,
    source_system
FROM {funds}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def _latest_external_refresh_id_sql(
    catalog: str,
    schema: str,
    table_prefix: str,
) -> str:
    control = _qualified(
        "meta_external_refresh_run",
        catalog,
        schema,
        table_prefix,
    )
    return (
        f"(SELECT refresh_run_id FROM {control} "
        "WHERE status = 'succeeded' "
        "ORDER BY run_completed_at DESC NULLS LAST, refresh_run_id DESC LIMIT 1)"
    )


def external_holdings_canonical_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_holdings_canonical", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {holdings}
WHERE canonical_holding_flag = true
  AND refresh_run_id = {latest_refresh}
""".strip()


def external_company_analysis_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_company_analysis", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    financial = _qualified("source_external_financial_snapshot", catalog, schema, table_prefix)
    market = _qualified("source_external_market_trading_snapshot", catalog, schema, table_prefix)
    debt = _qualified("source_external_debt_instrument_snapshot", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH base AS (
    SELECT *
    FROM {holdings}
    WHERE canonical_holding_flag = true
      AND refresh_run_id = {latest_refresh}
),
fund_metrics AS (
    SELECT
        external_fund_id,
        commitment_usd_m,
        pct_called,
        dpi,
        rvpi,
        net_moic,
        net_irr,
        as_of_date AS fund_as_of_date
    FROM {funds}
    WHERE refresh_run_id = {latest_refresh}
),
fin AS (
    SELECT *
    FROM {financial}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY period_end_date DESC NULLS LAST, asof_date DESC NULLS LAST
    ) = 1
),
mkt AS (
    SELECT *
    FROM {market}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY asof_date DESC NULLS LAST
    ) = 1
),
debt_inst AS (
    SELECT *
    FROM {debt}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY nearest_maturity_date ASC NULLS LAST, asof_date DESC NULLS LAST
    ) = 1
)
SELECT
    b.external_holding_id,
    b.external_sponsor_id,
    b.external_fund_id,
    b.external_portco_id,
    b.sponsor_name,
    b.fund_name,
    b.vintage,
    b.company_name,
    b.company_country,
    b.industry,
    b.investment_date,
    b.exit_date,
    b.holding_source_system,
    b.fund_assignment_method,
    b.fund_assignment_confidence,
    b.fund_assignment_is_assumed,
    b.fund_assignment_rationale,
    b.match_method,
    b.factset_portco_entity_id,
    b.contributing_source_systems,
    fm.commitment_usd_m,
    fm.pct_called,
    fm.dpi,
    fm.rvpi,
    fm.net_moic,
    fm.net_irr,
    fm.fund_as_of_date,
    fin.current_total_debt,
    fin.current_total_cash,
    fin.current_net_debt,
    fin.current_ltm_revenue,
    fin.current_ltm_ebitda,
    fin.current_ebitda_margin,
    fin.current_gross_leverage,
    fin.current_net_leverage,
    fin.yoy_revenue_growth,
    fin.yoy_ebitda_growth,
    fin.ltv,
    fin.financial_identifier,
    fin.financial_identifier_type,
    fin.financial_provenance_json,
    mkt.price,
    mkt.ytw,
    mkt.ytm,
    mkt.spread,
    mkt.duration,
    debt_inst.nearest_maturity_date,
    debt_inst.nearest_maturity_type,
    debt_inst.tranche_type,
    debt_inst.tranche_size
FROM base b
LEFT JOIN fund_metrics fm
    ON fm.external_fund_id = b.external_fund_id
LEFT JOIN fin
    ON fin.external_portco_id = b.external_portco_id
LEFT JOIN mkt
    ON mkt.external_portco_id = b.external_portco_id
LEFT JOIN debt_inst
    ON debt_inst.external_portco_id = b.external_portco_id
""".strip()


def external_fund_summary_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_fund_summary", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    financial = _qualified("source_external_financial_snapshot", catalog, schema, table_prefix)
    market = _qualified("source_external_market_trading_snapshot", catalog, schema, table_prefix)
    debt = _qualified("source_external_debt_instrument_snapshot", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH financial_coverage AS (
    SELECT DISTINCT external_portco_id
    FROM {financial}
    WHERE refresh_run_id = {latest_refresh}
      AND current_net_leverage IS NOT NULL
),
market_coverage AS (
    SELECT DISTINCT external_portco_id
    FROM {market}
    WHERE refresh_run_id = {latest_refresh}
      AND price IS NOT NULL
),
maturity_coverage AS (
    SELECT DISTINCT external_portco_id
    FROM {debt}
    WHERE refresh_run_id = {latest_refresh}
      AND nearest_maturity_date IS NOT NULL
)
SELECT
    h.external_sponsor_id,
    h.sponsor_name,
    h.external_fund_id,
    h.fund_name,
    h.vintage,
    COUNT(DISTINCT h.external_portco_id) AS holding_count,
    COUNT(DISTINCT CASE WHEN h.exit_date IS NULL THEN h.external_portco_id END) AS active_holding_count,
    MAX(f.commitment_usd_m) AS commitment_usd_m,
    MAX(f.pct_called) AS pct_called,
    MAX(f.dpi) AS dpi,
    MAX(f.rvpi) AS rvpi,
    MAX(f.net_moic) AS net_moic,
    MAX(f.net_irr) AS net_irr,
    COUNT(DISTINCT fc.external_portco_id) AS leverage_coverage_count,
    COUNT(DISTINCT mc.external_portco_id) AS debt_trading_coverage_count,
    COUNT(DISTINCT dc.external_portco_id) AS maturity_coverage_count,
    COUNT(DISTINCT CASE WHEN h.fund_assignment_is_assumed THEN h.external_holding_id END) AS assumed_fund_assignment_count
FROM {holdings} h
LEFT JOIN {funds} f
    ON f.external_fund_id = h.external_fund_id
    AND f.refresh_run_id = {latest_refresh}
LEFT JOIN financial_coverage fc
    ON fc.external_portco_id = h.external_portco_id
LEFT JOIN market_coverage mc
    ON mc.external_portco_id = h.external_portco_id
LEFT JOIN maturity_coverage dc
    ON dc.external_portco_id = h.external_portco_id
WHERE h.canonical_holding_flag = true
  AND h.refresh_run_id = {latest_refresh}
GROUP BY h.external_sponsor_id, h.sponsor_name, h.external_fund_id, h.fund_name, h.vintage
""".strip()


def external_sponsor_exits_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_sponsor_exits", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    external_sponsor_id,
    sponsor_name,
    external_fund_id,
    fund_name,
    vintage,
    external_portco_id,
    company_name,
    investment_date,
    exit_date,
    holding_source_system,
    fund_assignment_method,
    fund_assignment_is_assumed,
    match_method,
    factset_portco_entity_id
FROM {holdings}
WHERE exit_date IS NOT NULL
  AND canonical_holding_flag = true
  AND refresh_run_id = {latest_refresh}
""".strip()


def external_source_coverage_view_sql(catalog: str = "", schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    target = _output_qualified("external_source_coverage", catalog, schema, table_prefix)
    validation = _qualified("validation_external_source_reconciliation", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {validation}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_enrichment_coverage_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified(
        "external_enrichment_coverage", catalog, schema, table_prefix
    )
    validation = _qualified(
        "validation_external_enrichment", catalog, schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, schema, table_prefix
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {validation}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_holdings_lineage_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_holdings_lineage", catalog, schema, table_prefix)
    holdings = _qualified("xref_external_holding_source", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {holdings}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_holdings_merged_current_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified(
        "external_holdings_merged", catalog, schema, table_prefix
    )
    lineage = _output_qualified(
        "external_holdings_lineage", catalog, schema, table_prefix
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {lineage}
""".strip()


def external_companies_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_companies", catalog, schema, table_prefix)
    companies = _qualified("dim_external_portco", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {companies}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_funds_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_funds", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {funds}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_sponsor_summary_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_sponsor_summary", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH holding_counts AS (
    SELECT
        external_sponsor_id,
        COUNT(DISTINCT external_portco_id) AS holding_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NULL THEN external_portco_id END) AS active_holding_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NOT NULL THEN external_portco_id END) AS realized_holding_count,
        COUNT(DISTINCT CASE WHEN fund_assignment_is_assumed THEN external_holding_id END) AS assumed_fund_assignment_count
    FROM {holdings}
    WHERE canonical_holding_flag = true
      AND refresh_run_id = {latest_refresh}
    GROUP BY external_sponsor_id
)
SELECT
    f.external_sponsor_id,
    MAX(f.sponsor_name) AS sponsor_name,
    COUNT(DISTINCT f.external_fund_id) AS fund_count,
    COUNT(DISTINCT CASE WHEN f.flagship_flag THEN f.external_fund_id END) AS flagship_fund_count,
    MAX(f.commitment_usd_m) AS largest_fund_commitment_usd_m,
    COALESCE(MAX(h.holding_count), 0) AS holding_count,
    COALESCE(MAX(h.active_holding_count), 0) AS active_holding_count,
    COALESCE(MAX(h.realized_holding_count), 0) AS realized_holding_count,
    COALESCE(MAX(h.assumed_fund_assignment_count), 0) AS assumed_fund_assignment_count
FROM {funds} f
LEFT JOIN holding_counts h
    ON h.external_sponsor_id = f.external_sponsor_id
WHERE f.refresh_run_id = {latest_refresh}
GROUP BY f.external_sponsor_id
""".strip()


def external_review_queue_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_review_queue", catalog, schema, table_prefix)
    fund_xref = _qualified("xref_external_fund_source", catalog, schema, table_prefix)
    portco_xref = _qualified("xref_external_company_source", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
    'fund_source_match' AS review_type,
    external_fund_xref_id AS review_id,
    external_sponsor_id,
    external_fund_id,
    CAST(NULL AS STRING) AS external_portco_id,
    source_system,
    match_method,
    match_confidence,
    review_status,
    match_score
FROM {fund_xref}
WHERE refresh_run_id = {latest_refresh}
  AND COALESCE(review_status, '') NOT IN ('auto_matched', 'accepted')
UNION ALL
SELECT
    'company_source_match' AS review_type,
    external_company_source_xref_id AS review_id,
    CAST(NULL AS STRING) AS external_sponsor_id,
    CAST(NULL AS STRING) AS external_fund_id,
    external_portco_id,
    source_system,
    match_method,
    match_confidence,
    review_status,
    CAST(NULL AS DOUBLE) AS match_score
FROM {portco_xref}
WHERE refresh_run_id = {latest_refresh}
  AND COALESCE(review_status, '') NOT IN ('auto_matched', 'accepted')
UNION ALL
SELECT
    'holding_assignment' AS review_type,
    external_holding_id AS review_id,
    external_sponsor_id,
    external_fund_id,
    external_portco_id,
    holding_source_system AS source_system,
    fund_assignment_method AS match_method,
    fund_assignment_confidence AS match_confidence,
    holding_review_status AS review_status,
    fund_assignment_score AS match_score
FROM {holdings}
WHERE refresh_run_id = {latest_refresh}
  AND (
      fund_assignment_is_assumed = true
      OR fund_assignment_method IN (
          'assumed_latest_flagship',
          'missing_fund_assignment'
      )
      OR COALESCE(holding_review_status, '') NOT IN ('auto_mapped', 'accepted')
  )
""".strip()


def external_debt_profile_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_debt_profile", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    financial = _qualified("source_external_financial_snapshot", catalog, schema, table_prefix)
    market = _qualified("source_external_market_trading_snapshot", catalog, schema, table_prefix)
    debt = _qualified("source_external_debt_instrument_snapshot", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH canonical AS (
    SELECT *
    FROM {holdings}
    WHERE canonical_holding_flag = true
      AND refresh_run_id = {latest_refresh}
),
fin AS (
    SELECT *
    FROM {financial}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY period_end_date DESC NULLS LAST, asof_date DESC NULLS LAST
    ) = 1
),
mkt AS (
    SELECT *
    FROM {market}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY asof_date DESC NULLS LAST
    ) = 1
),
debt_inst AS (
    SELECT *
    FROM {debt}
    WHERE refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY external_portco_id
        ORDER BY nearest_maturity_date ASC NULLS LAST, asof_date DESC NULLS LAST
    ) = 1
)
SELECT
    h.external_portco_id,
    h.company_name,
    h.external_sponsor_id,
    h.sponsor_name,
    h.external_fund_id,
    h.fund_name,
    fin.current_total_debt,
    fin.current_total_cash,
    fin.current_net_debt,
    fin.current_ltm_revenue,
    fin.current_ltm_ebitda,
    fin.current_gross_leverage,
    fin.current_net_leverage,
    fin.ltv,
    mkt.price,
    mkt.ytw,
    mkt.ytm,
    mkt.spread,
    mkt.duration,
    debt_inst.tranche_type,
    debt_inst.tranche_size,
    debt_inst.nearest_maturity_date,
    debt_inst.nearest_maturity_type
FROM canonical h
LEFT JOIN fin
    ON fin.external_portco_id = h.external_portco_id
LEFT JOIN mkt
    ON mkt.external_portco_id = h.external_portco_id
LEFT JOIN debt_inst
    ON debt_inst.external_portco_id = h.external_portco_id
""".strip()


def external_fund_dry_powder_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("external_fund_dry_powder", catalog, schema, table_prefix)
    funds = _qualified("dim_external_fund", catalog, schema, table_prefix)
    holdings = _qualified("fact_external_holding", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH holding_counts AS (
    SELECT
        external_fund_id,
        COUNT(DISTINCT external_portco_id) AS holding_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NULL THEN external_portco_id END) AS active_holding_count,
        COUNT(DISTINCT CASE WHEN exit_date IS NOT NULL THEN external_portco_id END) AS realized_holding_count
    FROM {holdings}
    WHERE canonical_holding_flag = true
      AND refresh_run_id = {latest_refresh}
    GROUP BY external_fund_id
)
SELECT
    f.external_fund_id,
    f.external_sponsor_id,
    f.sponsor_name,
    f.fund_name,
    f.vintage,
    f.fund_strategy,
    f.fund_status,
    f.as_of_date,
    f.commitment_usd_m,
    f.pct_called,
    1.0 - f.pct_called AS pct_not_called,
    f.commitment_usd_m * (1.0 - f.pct_called) AS estimated_dry_powder_usd_m,
    f.dpi,
    f.rvpi,
    f.net_moic,
    f.net_irr,
    f.flagship_flag,
    COALESCE(h.holding_count, 0) AS holding_count,
    COALESCE(h.active_holding_count, 0) AS active_holding_count,
    COALESCE(h.realized_holding_count, 0) AS realized_holding_count
FROM {funds} f
LEFT JOIN holding_counts h
    ON h.external_fund_id = f.external_fund_id
WHERE f.refresh_run_id = {latest_refresh}
""".strip()


def external_preqin_dry_powder_macro_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("preqin_dry_powder_macro", catalog, schema, table_prefix)
    source = _qualified("source_preqin_dry_powder_macro", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {source}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def external_preqin_buyout_deal_flow_view_sql(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = _output_qualified("preqin_buyout_deal_flow", catalog, schema, table_prefix)
    source = _qualified("source_preqin_buyout_deal_flow", catalog, schema, table_prefix)
    latest_refresh = _latest_external_refresh_id_sql(catalog, schema, table_prefix)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT *
FROM {source}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def benchmark_v31_sponsor_resolved_cte_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    cte_name: str = "benchmark_v31_resolved",
) -> str:
    portco = _qualified(
        "source_benchmark_v31_portco_row", catalog, source_schema, table_prefix
    )
    xref = _qualified(
        "source_benchmark_v31_sponsor_xref", catalog, source_schema, table_prefix
    )
    sponsors = _qualified(
        "dim_external_sponsor", catalog, source_schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, source_schema, table_prefix
    )
    return f"""
{cte_name} AS (
    SELECT
        p.*,
        x.external_sponsor_id,
        x.prod_sponsor_name,
        s.sponsor_name AS prod_sponsor_display_name
    FROM {portco} p
    INNER JOIN {xref} x
        ON x.v31_sponsor_name = p.v31_sponsor_name
       AND x.refresh_run_id = {latest_refresh}
    INNER JOIN {sponsors} s
        ON s.external_sponsor_id = x.external_sponsor_id
       AND s.refresh_run_id = {latest_refresh}
)""".strip()


def v31_sponsor_xref_validation_sql(
    *,
    catalog: str,
    schema: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    portco = _qualified(
        "source_benchmark_v31_portco_row", catalog, schema, table_prefix
    )
    xref = _qualified(
        "source_benchmark_v31_sponsor_xref", catalog, schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, schema, table_prefix
    )
    return f"""
WITH v31 AS (
    SELECT DISTINCT v31_sponsor_name
    FROM {portco}
    WHERE refresh_run_id = {latest_refresh}
),
xref AS (
    SELECT
        v31_sponsor_name,
        COUNT(*) AS xref_count,
        COUNT(DISTINCT external_sponsor_id) AS resolved_sponsor_count
    FROM {xref}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY v31_sponsor_name
)
SELECT
    v.v31_sponsor_name,
    COALESCE(x.xref_count, 0) AS xref_count,
    COALESCE(x.resolved_sponsor_count, 0) AS resolved_sponsor_count
FROM v31 v
LEFT JOIN xref x USING (v31_sponsor_name)
WHERE COALESCE(x.xref_count, 0) <> 1
   OR COALESCE(x.resolved_sponsor_count, 0) <> 1
""".strip()


def v31_sponsor_xref_coverage_sql(
    *,
    catalog: str,
    schema: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    portco = _qualified(
        "source_benchmark_v31_portco_row", catalog, schema, table_prefix
    )
    xref = _qualified(
        "source_benchmark_v31_sponsor_xref", catalog, schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, schema, table_prefix
    )
    return f"""
SELECT
    COUNT(DISTINCT p.v31_sponsor_name) AS benchmark_sponsor_count,
    COUNT(DISTINCT CASE
        WHEN x.external_sponsor_id IS NOT NULL THEN p.v31_sponsor_name
    END) AS resolved_sponsor_count
FROM {portco} p
LEFT JOIN {xref} x
    ON x.v31_sponsor_name = p.v31_sponsor_name
   AND x.refresh_run_id = p.refresh_run_id
WHERE p.refresh_run_id = {latest_refresh}
""".strip()


def pe_sponsor_holding_identity_resolved_view_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    audit: bool = False,
) -> str:
    target = _qualified(
        "holding_dedup_audit" if audit else "holding_identity_resolved",
        catalog,
        source_schema,
        table_prefix,
    )
    holdings = _qualified(
        "fact_external_holding",
        catalog,
        source_schema,
        table_prefix,
    )
    decisions = _qualified(
        "xref_external_company_name_decision",
        catalog,
        source_schema,
        table_prefix,
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog,
        source_schema,
        table_prefix,
    )
    source_priority_sql = holding_source_priority_sql(
        "holding_source_system"
    )
    website_candidates = _qualified("source_sponsor_site_holding_candidate", catalog, source_schema, table_prefix)
    curated_website = _qualified("source_external_sponsor_website_holding", catalog, source_schema, table_prefix)
    script = f"""
CREATE OR REPLACE VIEW {target} AS
WITH website_company_decisions AS (
    SELECT
        w.website_holding_id,
        MAX(CASE
            WHEN c.company_classification = 'company'
             AND c.company_classification_version = '{CLASSIFICATION_VERSION}' THEN NULL
            ELSE CONCAT('website_company_', COALESCE(c.company_classification, 'unclassified'), ':',
                        COALESCE(c.company_classification_reason, 'missing_current_classification'))
        END) AS exclusion_reason
    FROM {curated_website} w
    LEFT JOIN {website_candidates} c
      ON c.crawl_run_id = w.crawl_run_id
     AND c.website_holding_candidate_id = w.website_holding_candidate_id
    GROUP BY w.website_holding_id
),
accepted_decisions AS (
    SELECT
        external_company_name_decision_id,
        external_portco_id,
        external_sponsor_id,
        source_system,
        source_company_id,
        source_external_portco_id,
        decision_method,
        match_score,
        rule_version,
        approved_at
    FROM {decisions}
    WHERE refresh_run_id = {latest_refresh}
      AND decision_status = 'accepted'
      AND is_active = true
),
source_decisions AS (
    SELECT *
    FROM accepted_decisions
    WHERE source_external_portco_id IS NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            external_sponsor_id,
            source_system,
            source_company_id
        ORDER BY approved_at DESC NULLS LAST,
                 external_company_name_decision_id
    ) = 1
),
portco_decisions AS (
    SELECT *
    FROM accepted_decisions
    WHERE source_external_portco_id IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            external_sponsor_id,
            source_system,
            source_external_portco_id
        ORDER BY approved_at DESC NULLS LAST,
                 external_company_name_decision_id
    ) = 1
),
mapped AS (
    SELECT
        h.*,
        CASE WHEN h.holding_source_system = 'sponsor_website'
            THEN CASE WHEN wc.website_holding_id IS NULL
                THEN 'website_company_unclassified:missing_source_lineage'
                ELSE wc.exclusion_reason END
        END AS _serving_exclusion_reason,
        COALESCE(
            pd.external_portco_id,
            sd.external_portco_id,
            h.external_portco_id
        )
            AS _resolved_external_portco_id,
        COALESCE(
            pd.external_company_name_decision_id,
            sd.external_company_name_decision_id
        ) AS _identity_decision_id,
        COALESCE(pd.decision_method, sd.decision_method)
            AS _identity_decision_method,
        COALESCE(pd.match_score, sd.match_score) AS _identity_match_score,
        COALESCE(pd.rule_version, sd.rule_version) AS _identity_rule_version
    FROM {holdings} h
    LEFT JOIN website_company_decisions wc
        ON wc.website_holding_id = h.source_holding_id
       AND h.holding_source_system = 'sponsor_website'
    LEFT JOIN portco_decisions pd
        ON (
            pd.external_sponsor_id = h.external_sponsor_id
            OR pd.external_sponsor_id IS NULL
        )
       AND pd.source_system = h.holding_source_system
       AND pd.source_external_portco_id = h.external_portco_id
    LEFT JOIN source_decisions sd
        ON (
            sd.external_sponsor_id = h.external_sponsor_id
            OR sd.external_sponsor_id IS NULL
        )
       AND sd.source_system = h.holding_source_system
       AND sd.source_company_id = h.source_company_id
    WHERE h.refresh_run_id = {latest_refresh}
),
group_stats AS (
    SELECT
        external_sponsor_id,
        external_fund_id,
        vintage,
        _resolved_external_portco_id,
        COUNT(*) AS _identity_member_count,
        COUNT(DISTINCT holding_source_system) AS _identity_source_count,
        MAX(CASE WHEN COALESCE(fund_assignment_is_assumed, false)
            THEN 1 ELSE 0 END) AS _assumed_fund_count,
        MAX(_identity_decision_id) AS _group_decision_id,
        MAX(_identity_decision_method) AS _group_decision_method,
        MAX(_identity_match_score) AS _group_match_score,
        MAX(_identity_rule_version) AS _group_rule_version,
        MAX(CASE WHEN _identity_decision_method = 'manual_company_identity_only_v1'
            THEN 1 ELSE 0 END) AS _manual_episode_preservation_count,
        MAX(CASE WHEN _identity_decision_method = 'manual_company_assumed_cohort_review_v1'
            THEN 1 ELSE 0 END) AS _manual_assumed_cohort_approval,
        MAX(investment_date) > MIN(investment_date) + INTERVAL '10' YEAR AS _long_investment_gap,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(_identity_decision_id))) AS _decision_ids,
        CONCAT_WS(
            '|',
            SORT_ARRAY(COLLECT_SET(holding_source_system))
        ) AS _identity_source_systems
    FROM mapped
    WHERE _serving_exclusion_reason IS NULL
    GROUP BY
        external_sponsor_id,
        external_fund_id,
        vintage,
        _resolved_external_portco_id
),
grouped AS (
    SELECT
        m.*,
        g._identity_member_count,
        g._identity_source_systems,
        (
            m._serving_exclusion_reason IS NULL
            AND g._identity_source_count >= 2
            AND g._resolved_external_portco_id IS NOT NULL
            AND (g._assumed_fund_count = 0 OR g._manual_assumed_cohort_approval = 1)
            AND g.external_fund_id IS NOT NULL
            AND g.vintage IS NOT NULL
            AND g._manual_episode_preservation_count = 0
            AND NOT COALESCE(g._long_investment_gap, false)
        ) AS _identity_resolution_applied,
        g._group_decision_id,
        g._group_decision_method,
        g._group_match_score,
        g._group_rule_version
    FROM mapped m
    LEFT JOIN group_stats g
        ON g.external_sponsor_id <=> m.external_sponsor_id
       AND g.external_fund_id <=> m.external_fund_id
       AND g.vintage <=> m.vintage
       AND g._resolved_external_portco_id
            <=> m._resolved_external_portco_id
),
source_ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY
                external_sponsor_id,
                external_fund_id,
                vintage,
                _resolved_external_portco_id,
                holding_source_system
            ORDER BY
                CASE WHEN _serving_exclusion_reason IS NULL THEN 0 ELSE 1 END,
                CASE WHEN exit_is_full = true AND YEAR(exit_date) < TRY_CAST(vintage AS INT)
                    THEN 1 ELSE 0 END,
                canonical_holding_flag DESC NULLS LAST,
                investment_date ASC NULLS LAST,
                knowledge_datetime DESC NULLS LAST,
                external_holding_id
        ) AS _identity_source_representative_rank
    FROM grouped
),
eligible_rows AS (
    SELECT
        *,
        CASE
            WHEN _identity_resolution_applied THEN SHA2(
                CONCAT_WS(
                    '|',
                    'sponsor_holding_identity_v1',
                    external_sponsor_id,
                    external_fund_id,
                    vintage,
                    _resolved_external_portco_id
                ),
                256
            )
            ELSE external_holding_id
        END AS _identity_resolution_group_id
    FROM source_ranked
    WHERE _serving_exclusion_reason IS NULL AND ((
        _identity_resolution_applied
        AND _identity_source_representative_rank = 1
    )
       OR (
           NOT _identity_resolution_applied
           AND canonical_holding_flag = true
       ))
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY _identity_resolution_group_id
            ORDER BY
                {source_priority_sql},
                knowledge_datetime DESC NULLS LAST,
                external_holding_id
        ) AS _identity_resolution_rank
    FROM eligible_rows
)
SELECT
    * EXCEPT (
        external_portco_id,
        _serving_exclusion_reason,
        _resolved_external_portco_id,
        _identity_decision_id,
        _identity_decision_method,
        _identity_match_score,
        _identity_rule_version,
        _group_decision_id,
        _group_decision_method,
        _group_match_score,
        _group_rule_version,
        _identity_resolution_group_id,
        _identity_resolution_rank,
        _identity_resolution_applied,
        _identity_member_count,
        _identity_source_systems,
        _identity_source_representative_rank
    ),
    _resolved_external_portco_id AS external_portco_id,
    _identity_resolution_group_id AS identity_resolution_group_id,
    _identity_resolution_rank AS identity_resolution_rank,
    _identity_resolution_applied AS identity_resolution_applied,
    _group_decision_id AS identity_decision_id,
    _group_decision_method AS identity_decision_method,
    _group_match_score AS identity_match_score,
    _group_rule_version AS identity_rule_version,
    CASE WHEN _identity_resolution_applied
        THEN _identity_member_count ELSE 1 END AS identity_source_holding_count,
    CASE WHEN _identity_resolution_applied
        THEN _identity_source_systems
        ELSE holding_source_system
    END AS identity_contributing_source_systems
FROM ranked
""".strip()
    if not audit:
        return script
    # Use the serving CTEs verbatim: audit and Gold share eligibility and ranking.
    ctes = script.rsplit("\nSELECT\n    * EXCEPT (", 1)[0]
    return ctes + """
SELECT
    s.refresh_run_id,
    s.external_holding_id,
    s.external_sponsor_id,
    s.sponsor_name,
    s.external_fund_id,
    s.fund_name,
    s.vintage,
    s.company_name,
    s.holding_source_system,
    s.source_company_id,
    s.external_portco_id AS original_portco_id,
    s._resolved_external_portco_id AS resolved_portco_id,
    s.investment_date,
    s.exit_date,
    s._identity_decision_id AS applied_decision_id,
    g._decision_ids AS contributing_decision_ids,
    s._group_rule_version AS rule_version,
    s._identity_source_representative_rank AS within_source_rank,
    w.external_holding_id AS winning_holding_id,
    CASE
        WHEN s._serving_exclusion_reason IS NOT NULL THEN 'excluded_website_non_company'
        WHEN w.external_holding_id = s.external_holding_id THEN 'retained'
        WHEN s._identity_resolution_applied AND s._identity_source_representative_rank > 1
            THEN 'suppressed_within_source'
        WHEN s._identity_resolution_applied THEN 'suppressed_source_priority'
        WHEN s.canonical_holding_flag = true THEN 'retained'
        ELSE 'preexisting_canonical_exclusion'
    END AS outcome,
    CASE
        WHEN s._serving_exclusion_reason IS NOT NULL THEN s._serving_exclusion_reason
        WHEN g._manual_episode_preservation_count > 0 THEN 'manual_identity_only_ownership_unresolved'
        WHEN g._long_investment_gap THEN 'distinct_or_unresolved_investment_episodes'
        WHEN g._assumed_fund_count > 0 AND g._manual_assumed_cohort_approval = 1
            THEN 'manual_collapse_assumed_fund_assignment_unchanged'
        WHEN g._assumed_fund_count > 0 THEN 'assumed_fund'
        WHEN s.external_fund_id IS NULL OR s.vintage IS NULL THEN 'missing_fund_vintage'
        WHEN s._identity_resolution_applied AND s.exit_is_full = true
             AND YEAR(s.exit_date) < TRY_CAST(s.vintage AS INT)
            THEN 'full_exit_before_fund_vintage'
        WHEN s._identity_resolution_applied THEN 'whole_row_source_priority'
        ELSE 'existing_canonical_holding_flag'
    END AS reason
FROM source_ranked s
LEFT JOIN group_stats g
    ON g.external_sponsor_id <=> s.external_sponsor_id
   AND g.external_fund_id <=> s.external_fund_id
   AND g.vintage <=> s.vintage
   AND g._resolved_external_portco_id <=> s._resolved_external_portco_id
LEFT JOIN ranked w
    ON w._identity_resolution_rank = 1
   AND ((s._identity_resolution_applied
         AND w.external_sponsor_id <=> s.external_sponsor_id
         AND w.external_fund_id <=> s.external_fund_id
         AND w.vintage <=> s.vintage
         AND w._resolved_external_portco_id <=> s._resolved_external_portco_id)
        OR (NOT s._identity_resolution_applied AND w.external_holding_id = s.external_holding_id))
"""


def pe_sponsor_investment_360_view_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = f"{catalog}.{gold_schema}.v_pe_sponsor_investment_360"
    holdings = _qualified(
        "holding_identity_resolved", catalog, source_schema, table_prefix
    )
    sponsors = _qualified(
        "dim_external_sponsor", catalog, source_schema, table_prefix
    )
    funds = _qualified(
        "dim_external_fund", catalog, source_schema, table_prefix
    )
    portcos = _qualified(
        "dim_external_portco", catalog, source_schema, table_prefix
    )
    financial = _qualified(
        "source_external_financial_snapshot",
        catalog,
        source_schema,
        table_prefix,
    )
    market = _qualified(
        "source_external_market_trading_snapshot",
        catalog,
        source_schema,
        table_prefix,
    )
    debt = _qualified(
        "source_external_debt_instrument_snapshot",
        catalog,
        source_schema,
        table_prefix,
    )
    debt_features = _qualified(
        "fact_external_debt_feature_snapshot",
        catalog,
        source_schema,
        table_prefix,
    )
    dcs = _qualified(
        "source_external_factset_dcs_debt_detail",
        catalog,
        source_schema,
        table_prefix,
    )
    documents = _qualified(
        "source_external_findox_document_index",
        catalog,
        source_schema,
        table_prefix,
    )
    lineage = _qualified(
        "xref_external_holding_source",
        catalog,
        source_schema,
        table_prefix,
    )
    capital_stack = _qualified(
        "xref_external_company_capital_stack",
        catalog,
        source_schema,
        table_prefix,
    )
    identifiers = _qualified(
        "xref_external_company_internal_id",
        catalog,
        source_schema,
        table_prefix,
    )
    findox_matches = _qualified(
        "xref_external_company_findox",
        catalog,
        source_schema,
        table_prefix,
    )
    decisions = _qualified(
        "xref_external_company_name_decision",
        catalog,
        source_schema,
        table_prefix,
    )
    v31_holding_xref = _qualified(
        "source_benchmark_v31_holding_xref",
        catalog,
        source_schema,
        table_prefix,
    )
    v31_portco = _qualified(
        "source_benchmark_v31_portco_row",
        catalog,
        source_schema,
        table_prefix,
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, source_schema, table_prefix
    )
    identity_enrichment = identity_enrichment_ctes_sql(
        audit=_qualified("holding_dedup_audit", catalog, source_schema, table_prefix),
        financial=financial, market=market, debt=debt, latest_refresh=latest_refresh,
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH {identity_enrichment},
base AS (
    SELECT *
    FROM {holdings}
    WHERE identity_resolution_rank = 1
      AND refresh_run_id = {latest_refresh}
),
{financial_selector_sql()},
market_latest AS (
    SELECT *
    FROM market_members
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY enrichment_sponsor_id, enrichment_portco_id
        ORDER BY asof_date DESC NULLS LAST,
                 CASE WHEN price IS NOT NULL OR ytw IS NOT NULL OR ytm IS NOT NULL THEN 0 ELSE 1 END,
                 instrument_id_uniq
    ) = 1
),
market_counts AS (
    SELECT
        enrichment_sponsor_id, enrichment_portco_id,
        COUNT(DISTINCT CASE WHEN price IS NOT NULL THEN instrument_id_uniq END) AS priced_instrument_count,
        COUNT(DISTINCT CASE WHEN price IS NOT NULL AND source_system = 'v_securities_data'
            THEN instrument_id_uniq END) AS securities_data_priced_instrument_count,
        COUNT(DISTINCT CASE WHEN price IS NOT NULL AND source_system LIKE 'debt_features%'
            THEN instrument_id_uniq END) AS debt_features_priced_instrument_count
    FROM market_members
    GROUP BY enrichment_sponsor_id, enrichment_portco_id
),
debt_summary AS (
    SELECT
        enrichment_sponsor_id, enrichment_portco_id,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(external_portco_id))) AS debt_source_portco_ids,
        COUNT(DISTINCT instrument_id_uniq) AS debt_instrument_count,
        SUM(tranche_size) AS aggregate_tranche_size,
        COUNT(DISTINCT CASE WHEN debt_attribution_scope = 'consolidated_group'
            THEN instrument_id_uniq END) AS consolidated_group_debt_instrument_count,
        SUM(CASE WHEN debt_attribution_scope = 'consolidated_group'
            THEN tranche_size END) AS consolidated_group_debt_amount,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(CASE WHEN debt_attribution_scope = 'consolidated_group'
            THEN debt_group_name END))) AS consolidated_group_names,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(CASE WHEN debt_attribution_scope = 'consolidated_group'
            THEN debt_borrower_name END))) AS consolidated_group_borrowers,
        MIN(nearest_maturity_date) AS nearest_maturity_date,
        MIN_BY(nearest_maturity_type, nearest_maturity_date)
            AS nearest_maturity_type,
        MIN_BY(tranche_type, nearest_maturity_date)
            AS nearest_maturity_tranche_type
    FROM debt_members
    GROUP BY enrichment_sponsor_id, enrichment_portco_id
),
feature_summary AS (
    SELECT
        m.external_sponsor_id AS enrichment_sponsor_id,
        m.resolved_portco_id AS enrichment_portco_id,
        COUNT(DISTINCT STRUCT(s.instrument_id_uniq, s.asof_date, s.feature_name)) AS debt_feature_count,
        COUNT(DISTINCT s.instrument_id_uniq) AS debt_feature_instrument_count,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(s.external_portco_id))) AS debt_feature_source_portco_ids,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(s.source_system)))
            AS debt_feature_source_systems,
        COUNT(DISTINCT CASE WHEN s.source_system IN (
            'debt_features', 'debt_features_factset_dcs_instrument', 'debt_features_factset_loan_security',
            'debt_features_reported_loan_deal', 'debt_features_findox_core_identifier')
            THEN STRUCT(s.instrument_id_uniq, s.asof_date, s.feature_name) END) AS direct_debt_feature_count,
        COUNT(DISTINCT CASE WHEN s.source_system = 'debt_features_factset_entity_tree'
            THEN STRUCT(s.instrument_id_uniq, s.asof_date, s.feature_name) END) AS entity_tree_debt_feature_count
    FROM {debt_features} s
    INNER JOIN identity_members m ON m.original_portco_id = s.external_portco_id
    WHERE s.refresh_run_id = {latest_refresh}
    GROUP BY m.external_sponsor_id, m.resolved_portco_id
),
dcs_summary AS (
    SELECT
        external_portco_id,
        COUNT(DISTINCT dcs_detail_id) AS dcs_debt_line_count
    FROM {dcs}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_portco_id
),
document_summary AS (
    SELECT
        external_portco_id,
        COUNT(DISTINCT document_id) AS findox_public_document_count,
        MAX(document_date) AS latest_findox_document_date
    FROM {documents}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_portco_id
),
lineage_summary AS (
    SELECT
        external_holding_id,
        COUNT(*) AS source_holding_count,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(source_system)))
            AS source_systems
    FROM {lineage}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_holding_id
),
capital_summary AS (
    SELECT
        external_portco_id,
        COUNT(*) AS capital_stack_entity_count
    FROM {capital_stack}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_portco_id
),
identifier_summary AS (
    SELECT
        external_portco_id,
        COUNT(*) AS internal_identifier_count
    FROM {identifiers}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_portco_id
),
decision_summary AS (
    SELECT
        external_portco_id,
        COUNT(*) AS accepted_name_decision_count
    FROM {decisions}
    WHERE refresh_run_id = {latest_refresh}
      AND decision_status = 'accepted'
      AND is_active = true
    GROUP BY external_portco_id
),
findox_match AS (
    SELECT
        external_portco_id,
        findox_issuer_id,
        matched_input_name,
        matched_findox_name,
        matched_name_field,
        match_score,
        token_overlap_score,
        exact_match_flag,
        {", ".join(COVERAGE_COLUMNS)}
    FROM {findox_matches}
    WHERE refresh_run_id = {latest_refresh}
      AND selected = true
),
v31_entry_rows AS (
    SELECT DISTINCT
        x.external_holding_id,
        p.v31_holding_id,
        p.deal_size_tev AS v31_deal_size_tev,
        p.equity_commitment AS v31_equity_commitment,
        p.entry_multiple AS v31_entry_multiple
    FROM {v31_holding_xref} x
    INNER JOIN {v31_portco} p
        ON p.v31_holding_id = x.v31_holding_id
       AND p.refresh_run_id = {latest_refresh}
    WHERE x.refresh_run_id = {latest_refresh}
      AND x.match_status = 'matched'
      AND x.external_holding_id IS NOT NULL
),
v31_entry_metrics AS (
    SELECT
        external_holding_id,
        COUNT(*) AS v31_entry_match_count,
        -- Multiple benchmark rows can describe different fund investments.
        -- Hydrate all entry metrics only from one unambiguous source row.
        CASE WHEN COUNT(*) = 1 THEN MAX(v31_deal_size_tev) END AS v31_deal_size_tev,
        CASE WHEN COUNT(*) = 1 THEN MAX(v31_equity_commitment) END AS v31_equity_commitment,
        CASE WHEN COUNT(*) = 1 THEN MAX(v31_entry_multiple) END AS v31_entry_multiple
    FROM v31_entry_rows
    GROUP BY external_holding_id
)
SELECT
    b.*,
    s.website AS sponsor_website,
    s.portfolio_page AS sponsor_portfolio_page,
    f.fund_strategy,
    f.fund_status,
    f.commitment_usd_m,
    f.as_of_date AS fund_as_of_date,
    f.pct_called,
    f.dpi,
    f.rvpi,
    f.net_moic,
    f.net_irr,
    f.flagship_flag,
    p.preferred_source_system AS company_preferred_source_system,
    p.is_active AS company_is_active,
    fin.source_system AS financial_source_system,
    fin.external_portco_id AS financial_source_portco_id,
    fin.financial_identifier,
    fin.financial_identifier_type,
    fin.financial_provenance_json,
    fin.financial_comparability_json,
    fin.financial_alias_selection_json,
    fin.findox_source_portco_id AS financial_findox_source_portco_id,
    fin.asof_date AS financial_asof_date,
    fin.period_end_date AS financial_period_end_date,
    CASE
        WHEN COALESCE(fin.has_findox_financials, false) AND fx.exact_match_flag = true
            THEN 'findox_exact_name'
        WHEN COALESCE(fin.has_findox_financials, false)
             AND COALESCE(feature_summary.debt_feature_count, 0) > 0
            THEN 'findox_fuzzy_with_debt_feature_coverage'
        WHEN COALESCE(fin.has_findox_financials, false)
            THEN 'findox_fuzzy_unverified'
        WHEN fin.source_system = 'debt_features_factset_entity_tree'
            THEN 'debt_features_via_factset_entity_tree'
        WHEN fin.source_system IN ('debt_features_factset_dcs_instrument', 'debt_features_factset_loan_security')
            THEN 'debt_features_exact_instrument_identifier'
        WHEN fin.source_system = 'debt_features_reported_loan_deal'
            THEN 'debt_features_exact_loan_agreement'
        WHEN fin.source_system = 'debt_features'
            THEN 'debt_features_direct_identifier'
        ELSE fin.source_system
    END AS financial_provenance_status,
    CASE
        WHEN COALESCE(fin.financial_review_required, false) THEN true
        WHEN COALESCE(fin.has_findox_financials, false)
             AND COALESCE(fx.exact_match_flag, false) = false
             AND COALESCE(feature_summary.debt_feature_count, 0) = 0
            THEN true
        ELSE false
    END AS financial_review_required,
    COALESCE(fin.has_findox_financials, false) AS has_findox_financials,
    COALESCE(fin.has_findox_financials, false)
        AND COALESCE(feature_summary.debt_feature_count, 0) > 0
        AS findox_has_debt_feature_coverage,
    fx.findox_issuer_id,
    fx.matched_input_name AS findox_matched_input_name,
    fx.matched_findox_name,
    fx.matched_name_field AS findox_matched_name_field,
    fx.match_score AS findox_match_score,
    fx.token_overlap_score AS findox_token_overlap_score,
    fx.exact_match_flag AS findox_exact_match_flag,
    {", ".join("fx." + column for column in COVERAGE_COLUMNS)},
    fin.current_total_debt,
    fin.current_total_cash,
    fin.current_net_debt,
    fin.current_ltm_revenue,
    fin.current_ltm_ebitda,
    fin.current_ebitda_margin,
    fin.current_gross_leverage,
    fin.current_net_leverage,
    fin.yoy_revenue_growth,
    fin.yoy_ebitda_growth,
    fin.ltv,
    market_latest.asof_date AS market_asof_date,
    market_latest.external_portco_id AS representative_market_source_portco_id,
    market_latest.tranche_type AS representative_tranche_type,
    market_latest.debt_attribution_scope AS representative_market_debt_attribution_scope,
    market_latest.debt_group_name AS representative_market_debt_group_name,
    market_latest.debt_borrower_name AS representative_market_debt_borrower_name,
    market_latest.debt_borrower_bbg_company_id AS representative_market_debt_borrower_id,
    market_latest.debt_relationship_path AS representative_market_debt_relationship_path,
    market_latest.price,
    market_latest.ytw,
    market_latest.ytm,
    market_latest.spread,
    market_latest.duration,
    market_latest.bridge_provenance AS debt_price_bridge_provenance,
    COALESCE(market_counts.debt_features_priced_instrument_count, 0) AS debt_features_priced_instrument_count,
    COALESCE(market_counts.securities_data_priced_instrument_count, 0) AS securities_data_priced_instrument_count,
    COALESCE(market_counts.priced_instrument_count, 0)
        AS priced_instrument_count,
    COALESCE(debt_summary.debt_instrument_count, 0)
        AS debt_instrument_count,
    debt_summary.aggregate_tranche_size,
    CASE WHEN debt_summary.consolidated_group_debt_instrument_count > 0
        THEN 'Includes consolidated group debt' END AS debt_attribution_label,
    COALESCE(debt_summary.consolidated_group_debt_instrument_count, 0)
        AS consolidated_group_debt_instrument_count,
    debt_summary.consolidated_group_debt_amount,
    debt_summary.consolidated_group_names,
    debt_summary.consolidated_group_borrowers,
    debt_summary.debt_source_portco_ids,
    debt_summary.nearest_maturity_date,
    debt_summary.nearest_maturity_type,
    debt_summary.nearest_maturity_tranche_type,
    COALESCE(feature_summary.debt_feature_count, 0)
        AS debt_feature_count,
    COALESCE(feature_summary.debt_feature_instrument_count, 0)
        AS debt_feature_instrument_count,
    feature_summary.debt_feature_source_systems,
    feature_summary.debt_feature_source_portco_ids,
    COALESCE(feature_summary.direct_debt_feature_count, 0)
        AS direct_debt_feature_count,
    COALESCE(feature_summary.entity_tree_debt_feature_count, 0)
        AS entity_tree_debt_feature_count,
    COALESCE(dcs_summary.dcs_debt_line_count, 0) AS dcs_debt_line_count,
    COALESCE(document_summary.findox_public_document_count, 0)
        AS findox_public_document_count,
    document_summary.latest_findox_document_date,
    GREATEST(
        COALESCE(b.identity_source_holding_count, 0),
        COALESCE(lineage_summary.source_holding_count, 0)
    )
        AS source_holding_count,
    CASE
        WHEN v31.v31_deal_size_tev IS NOT NULL
          OR v31.v31_equity_commitment IS NOT NULL
          OR v31.v31_entry_multiple IS NOT NULL
        THEN CONCAT_WS(
            '|',
            COALESCE(
                b.identity_contributing_source_systems,
                lineage_summary.source_systems
            ),
            'pe_sponsor_v31_study'
        )
        ELSE COALESCE(
            b.identity_contributing_source_systems,
            lineage_summary.source_systems
        )
    END AS source_systems,
    COALESCE(capital_summary.capital_stack_entity_count, 0)
        AS capital_stack_entity_count,
    COALESCE(identifier_summary.internal_identifier_count, 0)
        AS internal_identifier_count,
    COALESCE(decision_summary.accepted_name_decision_count, 0)
        AS accepted_name_decision_count,
    fin.external_portco_id IS NOT NULL AS has_financials,
    market_latest.external_portco_id IS NOT NULL AS has_market_pricing,
    debt_summary.enrichment_portco_id IS NOT NULL AS has_debt_instruments,
    document_summary.external_portco_id IS NOT NULL AS has_findox_documents,
    COALESCE(b.total_investment_amount, v31.v31_deal_size_tev) AS deal_size_tev,
    COALESCE(b.amount_invested_by_sponsor, v31.v31_equity_commitment)
        AS equity_commitment,
    v31.v31_entry_multiple AS entry_multiple,
    COALESCE(v31.v31_entry_match_count, 0) AS v31_entry_match_count,
    CASE
        WHEN v31.v31_entry_match_count = 1 THEN 'unambiguous'
        WHEN v31.v31_entry_match_count > 1 THEN 'ambiguous'
        ELSE 'unmatched'
    END AS v31_entry_metrics_status
FROM base b
LEFT JOIN {sponsors} s
    ON s.external_sponsor_id = b.external_sponsor_id
   AND s.refresh_run_id = {latest_refresh}
LEFT JOIN {funds} f
    ON f.external_fund_id = b.external_fund_id
   AND f.refresh_run_id = {latest_refresh}
LEFT JOIN {portcos} p
    ON p.external_portco_id = b.external_portco_id
   AND p.refresh_run_id = {latest_refresh}
LEFT JOIN fin ON fin.enrichment_portco_id = b.external_portco_id
    AND fin.enrichment_sponsor_id = b.external_sponsor_id
LEFT JOIN market_latest
    ON market_latest.enrichment_portco_id = b.external_portco_id
   AND market_latest.enrichment_sponsor_id = b.external_sponsor_id
LEFT JOIN market_counts
    ON market_counts.enrichment_portco_id = b.external_portco_id
   AND market_counts.enrichment_sponsor_id = b.external_sponsor_id
LEFT JOIN debt_summary
    ON debt_summary.enrichment_portco_id = b.external_portco_id
   AND debt_summary.enrichment_sponsor_id = b.external_sponsor_id
LEFT JOIN feature_summary
    ON feature_summary.enrichment_portco_id = b.external_portco_id
   AND feature_summary.enrichment_sponsor_id = b.external_sponsor_id
LEFT JOIN dcs_summary ON dcs_summary.external_portco_id = b.external_portco_id
LEFT JOIN document_summary
    ON document_summary.external_portco_id = b.external_portco_id
LEFT JOIN lineage_summary
    ON lineage_summary.external_holding_id = b.external_holding_id
LEFT JOIN capital_summary
    ON capital_summary.external_portco_id = b.external_portco_id
LEFT JOIN identifier_summary
    ON identifier_summary.external_portco_id = b.external_portco_id
LEFT JOIN decision_summary
    ON decision_summary.external_portco_id = b.external_portco_id
LEFT JOIN findox_match fx
    ON fx.external_portco_id = COALESCE(fin.findox_source_portco_id, fin.external_portco_id, b.external_portco_id)
LEFT JOIN v31_entry_metrics v31
    ON v31.external_holding_id = b.external_holding_id
""".strip()


def pe_sponsor_fund_360_view_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = f"{catalog}.{gold_schema}.v_pe_sponsor_fund_360"
    funds = _qualified(
        "dim_external_fund", catalog, source_schema, table_prefix
    )
    sponsors = _qualified(
        "dim_external_sponsor", catalog, source_schema, table_prefix
    )
    holdings = _qualified(
        "holding_identity_resolved", catalog, source_schema, table_prefix
    )
    investments = f"{catalog}.{gold_schema}.v_pe_sponsor_investment_360"
    fund_source = _qualified(
        "xref_external_fund_source", catalog, source_schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, source_schema, table_prefix
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH canonical_holdings AS (
    SELECT *
    FROM {holdings}
    WHERE identity_resolution_rank = 1
      AND refresh_run_id = {latest_refresh}
),
holding_rollup AS (
    SELECT
        h.external_fund_id,
        COUNT(DISTINCT h.external_portco_id) AS holding_count,
        COUNT(DISTINCT CASE WHEN h.exit_date IS NULL
            THEN h.external_portco_id END) AS active_holding_count,
        COUNT(DISTINCT CASE WHEN h.exit_date IS NOT NULL
            THEN h.external_portco_id END) AS realized_holding_count,
        COUNT(DISTINCT CASE WHEN h.fund_assignment_is_assumed
            THEN h.external_holding_id END) AS assumed_assignment_count,
        COUNT(DISTINCT CASE WHEN coverage.has_financials
            THEN h.external_portco_id END) AS financial_coverage_count,
        COUNT(DISTINCT CASE WHEN coverage.has_market_pricing
            THEN h.external_portco_id END) AS market_coverage_count,
        COUNT(DISTINCT CASE WHEN coverage.has_debt_instruments
            THEN h.external_portco_id END) AS debt_coverage_count,
        COUNT(DISTINCT CASE WHEN coverage.findox_allowed = false
            THEN coverage.findox_issuer_id END) AS findox_not_allowed_issuer_count,
        COUNT(DISTINCT CASE WHEN coverage.findox_denied_model_count > 0
            THEN coverage.findox_issuer_id END) AS findox_issuer_with_denied_model_count,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(h.industry))) AS industries,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(h.company_country)))
            AS company_countries,
        MIN(h.investment_date) AS earliest_investment_date,
        MAX(h.investment_date) AS latest_investment_date,
        MAX(h.exit_date) AS latest_exit_date
    FROM canonical_holdings h
    LEFT JOIN {investments} coverage
        ON coverage.external_holding_id = h.external_holding_id
       AND coverage.refresh_run_id = h.refresh_run_id
    GROUP BY h.external_fund_id
),
source_rollup AS (
    SELECT
        external_fund_id,
        COUNT(*) AS fund_source_count,
        CONCAT_WS('|', SORT_ARRAY(COLLECT_SET(source_system)))
            AS fund_source_systems,
        MIN(match_confidence) AS minimum_source_match_confidence,
        SUM(CASE WHEN review_status NOT IN ('auto_matched', 'accepted')
            THEN 1 ELSE 0 END) AS fund_source_review_count
    FROM {fund_source}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY external_fund_id
)
SELECT
    f.*,
    s.website AS sponsor_website,
    s.portfolio_page AS sponsor_portfolio_page,
    1.0 - f.pct_called AS pct_not_called,
    f.commitment_usd_m * (1.0 - f.pct_called)
        AS estimated_dry_powder_usd_m,
    COALESCE(h.holding_count, 0) AS holding_count,
    COALESCE(h.active_holding_count, 0) AS active_holding_count,
    COALESCE(h.realized_holding_count, 0) AS realized_holding_count,
    COALESCE(h.assumed_assignment_count, 0) AS assumed_assignment_count,
    COALESCE(h.financial_coverage_count, 0) AS financial_coverage_count,
    COALESCE(h.market_coverage_count, 0) AS market_coverage_count,
    COALESCE(h.debt_coverage_count, 0) AS debt_coverage_count,
    COALESCE(h.findox_not_allowed_issuer_count, 0) AS findox_not_allowed_issuer_count,
    COALESCE(h.findox_issuer_with_denied_model_count, 0) AS findox_issuer_with_denied_model_count,
    h.industries,
    h.company_countries,
    h.earliest_investment_date,
    h.latest_investment_date,
    h.latest_exit_date,
    COALESCE(x.fund_source_count, 0) AS fund_source_count,
    x.fund_source_systems,
    x.minimum_source_match_confidence,
    COALESCE(x.fund_source_review_count, 0) AS fund_source_review_count
FROM {funds} f
LEFT JOIN {sponsors} s
    ON s.external_sponsor_id = f.external_sponsor_id
   AND s.refresh_run_id = {latest_refresh}
LEFT JOIN holding_rollup h ON h.external_fund_id = f.external_fund_id
LEFT JOIN source_rollup x ON x.external_fund_id = f.external_fund_id
WHERE f.refresh_run_id = {latest_refresh}
""".strip()


def pe_sponsor_market_monthly_360_view_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = f"{catalog}.{gold_schema}.v_pe_sponsor_market_monthly_360"
    deals = _qualified(
        "source_preqin_buyout_deal_flow",
        catalog,
        source_schema,
        table_prefix,
    )
    dry_powder = _qualified(
        "source_preqin_dry_powder_macro",
        catalog,
        source_schema,
        table_prefix,
    )
    holdings = _qualified(
        "holding_identity_resolved", catalog, source_schema, table_prefix
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, source_schema, table_prefix
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH deal_monthly AS (
    SELECT
        DATE_TRUNC('month', deal_date) AS activity_month,
        COALESCE(portfolio_company_region, 'Unknown') AS geography,
        COUNT(DISTINCT deal_id) AS buyout_deal_count,
        SUM(CASE WHEN exit_flag THEN 1 ELSE 0 END) AS preqin_exit_count,
        SUM(deal_size_usd_m) AS aggregate_deal_size_usd_m,
        PERCENTILE_APPROX(deal_size_usd_m, 0.5)
            AS median_deal_size_usd_m,
        SUM(enterprise_value_usd_m) AS aggregate_enterprise_value_usd_m,
        SUM(equity_usd_m) AS aggregate_equity_usd_m,
        SUM(debt_usd_m) AS aggregate_debt_usd_m
    FROM {deals}
    WHERE refresh_run_id = {latest_refresh}
      AND deal_date IS NOT NULL
    GROUP BY
        DATE_TRUNC('month', deal_date),
        COALESCE(portfolio_company_region, 'Unknown')
),
sponsor_exit_monthly AS (
    SELECT
        DATE_TRUNC('month', exit_date) AS activity_month,
        COALESCE(company_country, 'Unknown') AS geography,
        COUNT(DISTINCT external_holding_id) AS sponsor_portfolio_exit_count
    FROM {holdings}
    WHERE refresh_run_id = {latest_refresh}
      AND identity_resolution_rank = 1
      AND exit_date IS NOT NULL
    GROUP BY
        DATE_TRUNC('month', exit_date),
        COALESCE(company_country, 'Unknown')
),
activity_keys AS (
    SELECT activity_month, geography FROM deal_monthly
    UNION
    SELECT activity_month, geography FROM sponsor_exit_monthly
),
dry_powder_monthly AS (
    SELECT
        DATE_TRUNC('month', as_of_date) AS activity_month,
        MAX(CASE WHEN LOWER(strategy) = 'buyout'
            THEN dry_powder_usd_bn END) AS buyout_dry_powder_usd_bn,
        MAX(CASE WHEN LOWER(strategy) = 'growth'
            THEN dry_powder_usd_bn END) AS growth_dry_powder_usd_bn,
        MAX(CASE WHEN LOWER(strategy) = 'balanced'
            THEN dry_powder_usd_bn END) AS balanced_dry_powder_usd_bn,
        MAX(CASE WHEN LOWER(strategy) = 'secondaries'
            THEN dry_powder_usd_bn END) AS secondaries_dry_powder_usd_bn,
        MAX(CASE WHEN LOWER(strategy) LIKE 'infrastructure%'
            THEN dry_powder_usd_bn END) AS infrastructure_dry_powder_usd_bn
    FROM {dry_powder}
    WHERE refresh_run_id = {latest_refresh}
    GROUP BY DATE_TRUNC('month', as_of_date)
)
SELECT
    k.activity_month,
    k.geography,
    COALESCE(d.buyout_deal_count, 0) AS buyout_deal_count,
    COALESCE(d.preqin_exit_count, 0) AS preqin_exit_count,
    COALESCE(e.sponsor_portfolio_exit_count, 0)
        AS sponsor_portfolio_exit_count,
    d.aggregate_deal_size_usd_m,
    d.median_deal_size_usd_m,
    d.aggregate_enterprise_value_usd_m,
    d.aggregate_equity_usd_m,
    d.aggregate_debt_usd_m,
    p.buyout_dry_powder_usd_bn,
    p.growth_dry_powder_usd_bn,
    p.balanced_dry_powder_usd_bn,
    p.secondaries_dry_powder_usd_bn,
    p.infrastructure_dry_powder_usd_bn,
    {latest_refresh} AS refresh_run_id
FROM activity_keys k
LEFT JOIN deal_monthly d
    ON d.activity_month = k.activity_month
   AND d.geography = k.geography
LEFT JOIN sponsor_exit_monthly e
    ON e.activity_month = k.activity_month
   AND e.geography = k.geography
LEFT JOIN dry_powder_monthly p
    ON p.activity_month = k.activity_month
""".strip()


def pe_sponsor_data_quality_view_sql(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    target = f"{catalog}.{gold_schema}.v_pe_sponsor_data_quality"
    source_validation = _qualified(
        "validation_external_source_reconciliation",
        catalog,
        source_schema,
        table_prefix,
    )
    enrichment_validation = _qualified(
        "validation_external_enrichment",
        catalog,
        source_schema,
        table_prefix,
    )
    refresh_runs = _qualified(
        "meta_external_refresh_run", catalog, source_schema, table_prefix
    )
    crawl_runs = _qualified(
        "control_website_crawl_run", catalog, source_schema, table_prefix
    )
    crawl_summary = _qualified(
        "validation_website_crawl_sponsor_summary",
        catalog,
        source_schema,
        table_prefix,
    )
    latest_refresh = _latest_external_refresh_id_sql(
        catalog, source_schema, table_prefix
    )
    benchmark_resolved = benchmark_v31_sponsor_resolved_cte_sql(
        catalog=catalog,
        source_schema=source_schema,
        table_prefix=table_prefix,
    )
    v31_holding_xref = _qualified(
        "source_benchmark_v31_holding_xref",
        catalog,
        source_schema,
        table_prefix,
    )
    v31_portco = _qualified(
        "source_benchmark_v31_portco_row",
        catalog,
        source_schema,
        table_prefix,
    )
    v31_comment = _qualified(
        "source_benchmark_v31_comment",
        catalog,
        source_schema,
        table_prefix,
    )
    holdings = _qualified(
        "fact_external_holding",
        catalog,
        source_schema,
        table_prefix,
    )
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH {benchmark_resolved},
benchmark_sponsor_summary AS (
    SELECT
        external_sponsor_id,
        prod_sponsor_display_name,
        v31_sponsor_name,
        COUNT(DISTINCT v31_holding_id) AS holding_count,
        MAX(ingested_at) AS observed_at,
        MAX(refresh_run_id) AS refresh_run_id
    FROM benchmark_v31_resolved
    GROUP BY
        external_sponsor_id,
        prod_sponsor_display_name,
        v31_sponsor_name
),
entry_metric_hydration AS (
    SELECT
        x.match_status,
        p.v31_holding_id,
        p.source_sheet,
        p.source_excel_row,
        p.deal_size_tev AS v31_deal_size_tev,
        p.equity_commitment AS v31_equity_commitment,
        p.entry_multiple AS v31_entry_multiple,
        h.total_investment_amount AS external_deal_size_tev,
        h.amount_invested_by_sponsor AS external_equity_commitment,
        c.field_name,
        c.comment_text
    FROM {v31_holding_xref} x
    INNER JOIN {v31_portco} p
        ON p.v31_holding_id = x.v31_holding_id
       AND p.refresh_run_id = {latest_refresh}
    LEFT JOIN {holdings} h
        ON h.external_holding_id = x.external_holding_id
       AND h.refresh_run_id = {latest_refresh}
    LEFT JOIN {v31_comment} c
        ON c.source_sheet = p.source_sheet
       AND c.source_excel_row = p.source_excel_row
       AND c.refresh_run_id = {latest_refresh}
    WHERE x.refresh_run_id = {latest_refresh}
)
SELECT
    'source_reconciliation' AS quality_scope,
    CONCAT(source_system, ':', source_table) AS quality_id,
    CAST(NULL AS STRING) AS sponsor_name,
    source_system,
    source_table,
    CAST(NULL AS STRING) AS metric_name,
    status,
    CAST(NULL AS DOUBLE) AS actual_value,
    CAST(NULL AS DOUBLE) AS target_value,
    source_row_count,
    normalized_row_count,
    canonical_row_count,
    CAST(NULL AS BIGINT) AS matched_count,
    unmatched_row_count,
    duplicate_row_count AS review_count,
    CAST(NULL AS BIGINT) AS blocked_count,
    CAST(NULL AS BIGINT) AS error_count,
    ingested_at AS observed_at,
    refresh_run_id,
    CAST(NULL AS STRING) AS crawl_run_id,
    detail
FROM {source_validation}
WHERE refresh_run_id = {latest_refresh}
UNION ALL
SELECT
    'validation_metric',
    CONCAT(scope, ':', metric),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    metric,
    status,
    actual,
    target,
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    ingested_at,
    refresh_run_id,
    CAST(NULL AS STRING),
    CAST(NULL AS STRING)
FROM {enrichment_validation}
WHERE refresh_run_id = {latest_refresh}
UNION ALL
SELECT
    'website_sponsor',
    CONCAT(c.crawl_run_id, ':', c.sponsor),
    c.sponsor,
    'sponsor_website',
    'validation_website_crawl_sponsor_summary',
    'website_crawl_status',
    CASE
        WHEN c.blocked_snapshot_count > 0 THEN 'blocked'
        WHEN c.error_snapshot_count > 0 OR c.review_candidate_count > 0 THEN 'review'
        WHEN c.website_holding_count = 0 THEN 'empty'
        ELSE 'pass'
    END,
    CAST(c.website_holding_count AS DOUBLE),
    CAST(NULL AS DOUBLE),
    c.snapshot_count,
    c.holding_candidate_count,
    c.website_holding_count,
    c.xref_match_count,
    c.xref_unmatched_count,
    c.xref_needs_review_count,
    c.blocked_snapshot_count,
    c.error_snapshot_count,
    c.crawl_run_started_at,
    {latest_refresh},
    c.crawl_run_id,
    CONCAT('portfolio_bridge_count=', c.portfolio_bridge_count,
           '; accepted_candidates=', c.accepted_candidate_count,
           '; non_company_candidates=', c.non_company_candidate_count,
           '; review_candidates=', c.review_candidate_count,
           '; company_classifier={CLASSIFICATION_VERSION}')
FROM {crawl_summary} c
JOIN {crawl_runs} r
  ON r.crawl_run_id = c.crawl_run_id
WHERE r.status IN ('succeeded', 'succeeded_with_warnings')
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY LOWER(TRIM(c.sponsor))
    ORDER BY c.crawl_run_started_at DESC, c.crawl_run_id DESC
) = 1
UNION ALL
SELECT
    'benchmark_v31_sponsor',
    external_sponsor_id,
    prod_sponsor_display_name,
    'benchmark_v31',
    'source_benchmark_v31_portco_row',
    'holding_count',
    'pass',
    CAST(holding_count AS DOUBLE),
    CAST(NULL AS DOUBLE),
    holding_count,
    holding_count,
    holding_count,
    holding_count,
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    observed_at,
    refresh_run_id,
    CAST(NULL AS STRING),
    CONCAT('v31_sponsor_name=', v31_sponsor_name)
FROM benchmark_sponsor_summary
UNION ALL
SELECT
    'entry_metric_hydration',
    metric_name,
    CAST(NULL AS STRING),
    'benchmark_v31',
    'source_benchmark_v31_portco_row',
    metric_name,
    'pass',
    CAST(metric_value AS DOUBLE),
    CAST(NULL AS DOUBLE),
    CAST(metric_value AS BIGINT),
    CAST(metric_value AS BIGINT),
    CAST(metric_value AS BIGINT),
    CAST(metric_value AS BIGINT),
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    CAST(0 AS BIGINT),
    CURRENT_TIMESTAMP(),
    {latest_refresh},
    CAST(NULL AS STRING),
    detail
FROM (
    SELECT 'v31_source_deal_size_tev' AS metric_name,
           COUNT(v31_deal_size_tev) AS metric_value,
           'non_null_v31_deal_size_tev' AS detail
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'v31_source_equity_commitment',
           COUNT(v31_equity_commitment),
           'non_null_v31_equity_commitment'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'v31_source_entry_multiple',
           COUNT(v31_entry_multiple),
           'non_null_v31_entry_multiple'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'external_retained_deal_size_tev',
           COUNT(CASE WHEN external_deal_size_tev IS NOT NULL THEN 1 END),
           'external_deal_size_tev_retained_by_waterfall'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'external_retained_equity_commitment',
           COUNT(CASE WHEN external_equity_commitment IS NOT NULL THEN 1 END),
           'external_equity_commitment_retained_by_waterfall'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'v31_fill_deal_size_tev',
           COUNT(CASE
               WHEN external_deal_size_tev IS NULL
                AND v31_deal_size_tev IS NOT NULL
               THEN 1 END),
           'v31_fill_for_missing_external_deal_size_tev'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'v31_fill_equity_commitment',
           COUNT(CASE
               WHEN external_equity_commitment IS NULL
                AND v31_equity_commitment IS NOT NULL
               THEN 1 END),
           'v31_fill_for_missing_external_equity_commitment'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'comment_backed_entry_metric_fills',
           COUNT(DISTINCT CONCAT(
               CAST(source_sheet AS STRING), ':',
               CAST(source_excel_row AS STRING), ':',
               field_name
           )),
           'comment_evidence_for_entry_metrics'
    FROM entry_metric_hydration
    WHERE comment_text IS NOT NULL
      AND field_name IN (
          'deal_size_tev', 'equity_commitment', 'entry_multiple'
      )
    UNION ALL
    SELECT 'v31_holding_unmatched',
           COUNT(CASE WHEN match_status = 'benchmark_only' THEN 1 END),
           'benchmark_only_holdings_without_external_match'
    FROM entry_metric_hydration
    UNION ALL
    SELECT 'v31_holding_ambiguous',
           COUNT(CASE WHEN match_status = 'ambiguous' THEN 1 END),
           'ambiguous_v31_to_external_matches'
    FROM entry_metric_hydration
) hydration_metrics
UNION ALL
SELECT
    'refresh_run',
    refresh_run_id,
    CAST(NULL AS STRING),
    'sponsor_study',
    'meta_refresh_run',
    'refresh_status',
    status,
    CAST(NULL AS DOUBLE),
    CAST(NULL AS DOUBLE),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    run_completed_at,
    refresh_run_id,
    website_crawl_run_id,
    error_message
FROM {refresh_runs}
WHERE refresh_run_id = {latest_refresh}
""".strip()


def gold_pe_sponsor_dashboard_view_scripts(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    return {
        "v_pe_sponsor_investment_360": pe_sponsor_investment_360_view_sql(
            catalog=catalog,
            source_schema=source_schema,
            gold_schema=gold_schema,
            table_prefix=table_prefix,
        ),
        "v_pe_sponsor_fund_360": pe_sponsor_fund_360_view_sql(
            catalog=catalog,
            source_schema=source_schema,
            gold_schema=gold_schema,
            table_prefix=table_prefix,
        ),
        "v_pe_sponsor_market_monthly_360":
            pe_sponsor_market_monthly_360_view_sql(
                catalog=catalog,
                source_schema=source_schema,
                gold_schema=gold_schema,
                table_prefix=table_prefix,
            ),
        "v_pe_sponsor_data_quality": pe_sponsor_data_quality_view_sql(
            catalog=catalog,
            source_schema=source_schema,
            gold_schema=gold_schema,
            table_prefix=table_prefix,
        ),
    }


def benchmark_delta_view_scripts(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    return {
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['vw_company_analysis_snapshot']}": company_analysis_snapshot_view_sql(catalog, schema, table_prefix),
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['fact_opportunity_screen']}": opportunity_screen_view_sql(catalog, schema, table_prefix),
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['vw_sponsor_summary']}": sponsor_summary_view_sql(catalog, schema, table_prefix),
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['dim_opportunity_screen_rule']}": screen_rule_view_sql(catalog, schema, table_prefix),
        f"{OUTPUT_NAMESPACE}{OUTPUT_VIEW_NAMES['validation_reconciliation']}": validation_reconciliation_view_sql(catalog, schema, table_prefix),
        "output_company_detail_structured_universe": structured_company_detail_view_sql(catalog, schema, table_prefix),
        "output_sponsor_exits_structured_universe": structured_sponsor_exits_view_sql(catalog, schema, table_prefix),
        "output_fund_dry_powder": fund_dry_powder_view_sql(catalog, schema, table_prefix),
        "output_universe_reconciliation": universe_reconciliation_view_sql(catalog, schema, table_prefix),
        "output_website_portfolio_latest": website_portfolio_latest_view_sql(catalog, schema, table_prefix),
        "output_website_crawl_health": website_crawl_health_view_sql(catalog, schema, table_prefix),
        "output_website_bridge_reconciliation": website_bridge_reconciliation_output_view_sql(catalog, schema, table_prefix),
    }


def external_delta_view_scripts(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    *,
    preqin_catalog: str = DEFAULT_PREQIN_CATALOG,
    preqin_schema: str = DEFAULT_PREQIN_SCHEMA,
) -> dict[str, str]:
    return {
        "output_external_holdings_lineage": external_holdings_lineage_view_sql(catalog, schema, table_prefix),
        "output_external_holdings_merged": external_holdings_merged_current_view_sql(catalog, schema, table_prefix),
        "output_external_holdings_canonical": external_holdings_canonical_view_sql(catalog, schema, table_prefix),
        "output_external_companies": external_companies_view_sql(catalog, schema, table_prefix),
        "output_external_company_analysis": external_company_analysis_view_sql(catalog, schema, table_prefix),
        "output_external_funds": external_funds_view_sql(catalog, schema, table_prefix),
        "output_external_fund_performance": external_fund_performance_view_sql(catalog, schema, table_prefix),
        "output_external_fund_summary": external_fund_summary_view_sql(catalog, schema, table_prefix),
        "output_external_sponsor_summary": external_sponsor_summary_view_sql(catalog, schema, table_prefix),
        "output_external_source_coverage": external_source_coverage_view_sql(catalog, schema, table_prefix),
        "output_external_enrichment_coverage": external_enrichment_coverage_view_sql(catalog, schema, table_prefix),
        "output_external_review_queue": external_review_queue_view_sql(catalog, schema, table_prefix),
        "output_external_debt_profile": external_debt_profile_view_sql(catalog, schema, table_prefix),
        "output_external_sponsor_exits": external_sponsor_exits_view_sql(catalog, schema, table_prefix),
        "output_external_fund_dry_powder": external_fund_dry_powder_view_sql(catalog, schema, table_prefix),
        "output_preqin_dry_powder_macro": external_preqin_dry_powder_macro_view_sql(catalog, schema, table_prefix),
        "output_preqin_buyout_deal_flow": external_preqin_buyout_deal_flow_view_sql(catalog, schema, table_prefix),
    }


def gold_external_view_scripts(
    *,
    catalog: str,
    source_schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    preqin_catalog: str = DEFAULT_PREQIN_CATALOG,
    preqin_schema: str = DEFAULT_PREQIN_SCHEMA,
) -> dict[str, str]:
    outputs = external_delta_view_scripts(
        catalog=catalog,
        schema=source_schema,
        table_prefix=table_prefix,
        preqin_catalog=preqin_catalog,
        preqin_schema=preqin_schema,
    )
    scripts = {}
    for output_name in outputs:
        suffix = output_name.removeprefix(OUTPUT_NAMESPACE)
        gold_name = f"v_pe_sponsor_{suffix}"
        source = _qualified(output_name, catalog, source_schema, table_prefix)
        scripts[gold_name] = (
            f"CREATE OR REPLACE VIEW {catalog}.{gold_schema}.{gold_name} "
            f"AS SELECT * FROM {source}"
        )
    return scripts


def delta_view_scripts(
    catalog: str = "",
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    *,
    preqin_catalog: str = DEFAULT_PREQIN_CATALOG,
    preqin_schema: str = DEFAULT_PREQIN_SCHEMA,
) -> dict[str, str]:
    return {
        **benchmark_delta_view_scripts(catalog, schema, table_prefix),
        **external_delta_view_scripts(
            catalog,
            schema,
            table_prefix,
            preqin_catalog=preqin_catalog,
            preqin_schema=preqin_schema,
        ),
    }


def sponsor_summary_sql(schema: str = DEFAULT_SCHEMA, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    return f"SELECT * FROM {schema}.{output_view_name('vw_sponsor_summary', table_prefix)}"


def opportunity_screen_sql(
    schema: str = DEFAULT_SCHEMA,
    screen_name: str | None = None,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    clean_screen_name = None if screen_name is None else screen_name.replace("'", "''")
    where = "" if not clean_screen_name else f"WHERE screen_name = '{clean_screen_name}'"
    return f"""
SELECT *
FROM {schema}.{output_view_name('fact_opportunity_screen', table_prefix)}
{where}
ORDER BY screen_name, screen_rank
"""
