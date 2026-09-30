"""Point-in-time sponsor-study adapter for the persisted FinDox aggregate feed."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AggregateMetric:
    name: str
    line_ids: tuple[int, ...]
    flow: bool


# Aggregate standard IDs are a different namespace from legacy model line IDs.
METRICS = (
    AggregateMetric("revenue", (101,), True),
    AggregateMetric("ebitda_fda", (304, 311, 301), True),
    AggregateMetric("total_debt", (10017, 10043), False),
    AggregateMetric("cash_and_equivs", (10155, 617, 10018), False),
)


def aggregate_financials_sql(issuer_ids: list[str], asof: str) -> str:
    identifiers = ",".join(str(int(value)) for value in issuer_ids)
    metric_rows = ",".join(
        f"({line_id}, '{metric.name}', {rank}, {str(metric.flow).lower()})"
        for metric in METRICS for rank, line_id in enumerate(metric.line_ids)
    )
    return f"""
WITH universe AS (
    SELECT *, CAST(findox_id AS STRING) issuer_id
    FROM dbo.findox_agg_universe
    WHERE financial_data_updated < DATE '{asof}' + INTERVAL 1 DAY
    QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY financial_data_updated DESC)=1
), selected_models AS (
    SELECT * FROM universe
    WHERE TRY_CAST(findox_id AS BIGINT) IN ({identifiers})
), metric_map(line_id, line_name, preference, is_flow) AS (
    VALUES {metric_rows}
), actual AS (
    SELECT a.*, u.issuer_id, u.financial_data_updated, u.access_type, u.allowed, u.model_type_label,
           m.line_name, m.preference, m.is_flow
    FROM dbo.findox_agg_standard_data_actual a
    JOIN selected_models u ON a.company_id = u.id
    JOIN metric_map m ON TRY_CAST(a.line_id AS INT) = m.line_id
    WHERE a.asof_date <= DATE '{asof}'
      AND a.asof_date_time <= u.financial_data_updated
      AND a.load_date < DATE '{asof}' + INTERVAL 1 DAY
), periods AS (
    SELECT p.* FROM dbo.findox_agg_periods p
    JOIN selected_models u ON p.company_id=u.id
    WHERE p.asof_date <= DATE '{asof}' AND p.asof_date_time <= u.financial_data_updated
      AND p.load_date < DATE '{asof}' + INTERVAL 1 DAY
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY p.company_id, p.asof_date_time, p.id ORDER BY p.load_date DESC
    )=1
), candidates AS (
    SELECT a.issuer_id, a.company_id AS aggregate_company_id, a.line_name,
           TRY_CAST(a.line_id AS INT) source_line_id, a.report_id,
           a.period_id, p.period AS period_name,
           TRY_CAST(p.end_of_reporting_period AS DATE) period_end_date,
           p.report_issue_date, upper(trim(p.reporting_currency)) currency,
           a.asof_date_time AS source_snapshot_at, GREATEST(a.load_date, p.load_date) AS source_loaded_at,
           a.id AS source_record_id,
           CONCAT(CAST(a.asof_date_time AS STRING), '|', CAST(a.load_date AS STRING), '|',
                  CAST(p.load_date AS STRING)) AS source_version,
           a.financial_data_updated AS committed_watermark, a.access_type,
           a.allowed AS findox_allowed, p.is_allowed AS findox_period_allowed,
           a.model_type_label AS findox_model_type, a.access_type AS findox_access_type,
           'stored_aggregate_value_access_flags_retained' AS eligibility_basis,
           a.preference,
           CASE WHEN a.is_flow THEN a.data_ltm
                ELSE COALESCE(a.data_qrt, a.data_fy, a.data_ltm, a.data_ytd, a.data_hy) END
                * CASE WHEN a.line_id='10018' THEN -1 ELSE 1 END AS value,
           CASE WHEN a.line_id='10018' THEN -1 ELSE 1 END source_sign_multiplier
    FROM actual a JOIN periods p
      ON p.company_id=a.company_id AND p.id=a.period_id
     AND p.asof_date_time=a.asof_date_time
    -- Access metadata is preserved for downstream disclosure, not used to discard stored values.
    WHERE TRY_CAST(p.end_of_reporting_period AS DATE) <= DATE '{asof}'
      AND p.report_issue_date <= DATE '{asof}'
)
SELECT DISTINCT *, 'dbo.findox_agg_standard_data_actual' AS source_table,
       'currency_millions' AS source_unit
FROM candidates WHERE value IS NOT NULL
"""
