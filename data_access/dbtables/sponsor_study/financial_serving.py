"""Gold financial selection across already approved, sponsor-scoped identity members."""

METRICS = (
    'current_total_debt', 'current_total_cash', 'current_net_debt', 'current_ltm_revenue',
    'current_ltm_ebitda', 'current_ebitda_margin', 'current_gross_leverage',
    'current_net_leverage', 'yoy_revenue_growth', 'yoy_ebitda_growth', 'ltv',
)


def financial_selector_sql() -> str:
    """One scan of financial_members, expanded by a small constant metric relation.

    Retrieval routes do not identify financial providers. Unknown legacy provenance
    ranks last. Corporate/financial scope conflicts cannot be resolved by recency.
    """
    selection_metrics = (*METRICS, 'current_enterprise_value')
    metrics = ' UNION ALL '.join(f"SELECT '{metric}' AS metric" for metric in selection_metrics)
    amounts = ' '.join(f"WHEN '{metric}' THEN f.{metric}" for metric in METRICS)
    amounts += " WHEN 'current_enterprise_value' THEN TRY_CAST(GET_JSON_OBJECT(f.financial_provenance_json, '$.current_enterprise_value.value') AS DOUBLE)"
    leaves = ('current_total_debt', 'current_total_cash', 'current_ltm_ebitda', 'current_ltm_revenue', 'current_net_debt', 'current_enterprise_value')
    dependency_basis = ', '.join(
        f"MAX(CASE WHEN metric='{metric}' THEN GET_JSON_OBJECT(evidence, '$.candidate_id') END) AS id{i}"
        for i, metric in enumerate(leaves))
    dependency_checks = ' AND '.join(
        f"(GET_JSON_OBJECT(w.evidence, '$.leaf_candidate_ids.{metric}') IS NULL OR GET_JSON_OBJECT(w.evidence, '$.leaf_candidate_ids.{metric}')=basis.id{i})"
        for i, metric in enumerate(leaves))
    pivot = ',\n           '.join(f"MAX(CASE WHEN metric='{metric}' THEN value END) AS {metric}" for metric in METRICS)
    provenance = ", ',', ".join(
        f"'\"{metric}\":', COALESCE(MAX(CASE WHEN metric='{metric}' THEN COALESCE(evidence, TO_JSON(NAMED_STRUCT('source_portco_id', external_portco_id, 'provider', provider, 'period', CAST(metric_period AS STRING), 'selection_reason', 'legacy_provenance_unavailable'))) END), 'null')"
        for metric in METRICS)
    return f"""fin AS (
WITH expanded AS (
    SELECT f.*, m.metric, CASE m.metric {amounts} END AS value,
           GET_JSON_OBJECT(f.financial_provenance_json, CONCAT('$.', m.metric)) AS evidence
    FROM financial_members f CROSS JOIN ({metrics}) m
), parsed AS (
    SELECT *,
        COALESCE(GET_JSON_OBJECT(evidence, '$.provider'),
            CASE WHEN source_system='findox' THEN 'findox' ELSE 'unattributed' END) AS provider,
        COALESCE(GET_JSON_OBJECT(evidence, '$.reporting_entity_id'),
                 GET_JSON_OBJECT(evidence, '$.identifier'), financial_identifier, external_portco_id) AS reporting_entity,
        COALESCE(GET_JSON_OBJECT(evidence, '$.aggregate_company_id'), '') AS reporting_model,
        COALESCE(GET_JSON_OBJECT(evidence, '$.reporting_scope_id'), '') AS reporting_scope,
        COALESCE(GET_JSON_OBJECT(evidence, '$.scope_status'), 'unverified') AS scope_status,
        CASE WHEN evidence IS NULL THEN CAST(period_end_date AS TIMESTAMP)
             ELSE TRY_CAST(GET_JSON_OBJECT(evidence, '$.period') AS TIMESTAMP) END AS metric_period,
        CASE WHEN evidence IS NULL THEN CAST(asof_date AS TIMESTAMP)
             ELSE TRY_CAST(GET_JSON_OBJECT(evidence, '$.asof') AS TIMESTAMP) END AS metric_asof,
        TRY_CAST(GET_JSON_OBJECT(evidence, '$.source_loaded_at') AS TIMESTAMP) AS metric_loaded
    FROM expanded WHERE value IS NOT NULL
), keyed AS (
    SELECT *, CONCAT(LENGTH(reporting_entity), ':', reporting_entity,
                     LENGTH(reporting_model), ':', reporting_model,
                     LENGTH(reporting_scope), ':', reporting_scope) AS scope_key
    FROM parsed
), scopes AS (
    SELECT enrichment_sponsor_id, enrichment_portco_id, provider,
        COUNT(DISTINCT scope_key) AS scope_count,
        COUNT(DISTINCT CASE WHEN scope_status='confirmed' THEN scope_key END) AS confirmed_count,
        MAX(CASE WHEN scope_status='confirmed' THEN scope_key END) AS confirmed_scope
    FROM keyed WHERE scope_status <> 'rejected'
    GROUP BY enrichment_sponsor_id, enrichment_portco_id, provider
), scoped AS (
    SELECT k.*, CASE
        WHEN k.scope_status='rejected' THEN 'reporting_scope_rejected'
        WHEN s.confirmed_count > 1 OR (s.confirmed_count=0 AND s.scope_count > 1)
            THEN 'ambiguous_reporting_scope'
        WHEN s.confirmed_count=1 AND (k.scope_key<>s.confirmed_scope OR k.scope_status<>'confirmed')
            THEN 'unconfirmed_reporting_scope'
        ELSE 'eligible' END AS scope_reason
    FROM keyed k LEFT JOIN scopes s
      ON k.enrichment_sponsor_id=s.enrichment_sponsor_id
     AND k.enrichment_portco_id=s.enrichment_portco_id AND k.provider=s.provider
), provider_ranked AS (
    SELECT *, DENSE_RANK() OVER (
        PARTITION BY enrichment_sponsor_id, enrichment_portco_id, metric, provider
        ORDER BY metric_period DESC NULLS LAST, metric_asof DESC NULLS LAST,
                 metric_loaded DESC NULLS LAST
    ) AS version_rank
    FROM scoped WHERE scope_reason='eligible'
), checked AS (
    SELECT *, MIN(value) OVER provider_metric AS min_value, MAX(value) OVER provider_metric AS max_value
    FROM provider_ranked WHERE version_rank=1
    WINDOW provider_metric AS (PARTITION BY enrichment_sponsor_id, enrichment_portco_id, metric, provider)
), winners AS (
    SELECT * FROM checked WHERE min_value=max_value
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY enrichment_sponsor_id, enrichment_portco_id, metric
        ORDER BY CASE provider WHEN 'factset' THEN 0 WHEN 'findox' THEN 1 ELSE 2 END,
                 CASE WHEN external_portco_id=enrichment_portco_id THEN 0 ELSE 1 END,
                 external_portco_id
    )=1
), dependency_basis AS (
    SELECT enrichment_sponsor_id, enrichment_portco_id, {dependency_basis}
    FROM winners GROUP BY enrichment_sponsor_id, enrichment_portco_id
), compatible AS (
    SELECT w.* FROM winners w
    JOIN dependency_basis basis ON basis.enrichment_sponsor_id=w.enrichment_sponsor_id
      AND basis.enrichment_portco_id=w.enrichment_portco_id
    WHERE COALESCE(GET_JSON_OBJECT(w.evidence, '$.source'), '') <> 'derived'
       OR (GET_JSON_OBJECT(w.evidence, '$.leaf_candidate_ids') IS NOT NULL AND {dependency_checks})
), values_by_company AS (
    SELECT enrichment_sponsor_id, enrichment_portco_id,
           {pivot},
           CONCAT('{{', {provenance}, '}}') AS financial_provenance_json,
           TO_JSON(NAMED_STRUCT(
               'current_ltm_revenue', MAX(CASE WHEN metric='current_ltm_revenue' THEN GET_JSON_OBJECT(evidence, '$.yoy_comparability_status') END),
               'current_ltm_ebitda', MAX(CASE WHEN metric='current_ltm_ebitda' THEN GET_JSON_OBJECT(evidence, '$.yoy_comparability_status') END))) AS financial_comparability_json,
           COALESCE(MAX(CASE WHEN provider='findox' AND external_portco_id=enrichment_portco_id THEN external_portco_id END),
                    MIN(CASE WHEN provider='findox' THEN external_portco_id END)) AS findox_source_portco_id,
           MAX(CASE WHEN provider='findox' THEN 1 ELSE 0 END)=1 AS has_findox_financials
    FROM compatible WHERE metric<>'current_enterprise_value' GROUP BY enrichment_sponsor_id, enrichment_portco_id
), primary_value AS (
    SELECT * FROM compatible WHERE metric<>'current_enterprise_value'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY enrichment_sponsor_id, enrichment_portco_id
        ORDER BY CASE WHEN metric='current_ltm_ebitda' THEN 0 ELSE 1 END, metric)=1
), alias_audit AS (
    SELECT s.enrichment_sponsor_id, s.enrichment_portco_id,
        MAX(CASE WHEN s.scope_reason='ambiguous_reporting_scope' OR c.min_value<>c.max_value
                   OR (w.metric IS NOT NULL AND v.metric IS NULL) THEN 1 ELSE 0 END)=1 AS alias_review_required,
        TO_JSON(COLLECT_LIST(NAMED_STRUCT(
            'metric', s.metric, 'source_portco_id', s.external_portco_id, 'provider', s.provider,
            'reason', CASE WHEN s.scope_reason<>'eligible' THEN s.scope_reason
                          WHEN c.min_value<>c.max_value THEN 'conflicting_statement_values'
                          WHEN w.external_portco_id=s.external_portco_id AND v.metric IS NULL THEN 'derived_inputs_changed'
                          WHEN v.external_portco_id=s.external_portco_id THEN 'selected'
                          ELSE 'provider_or_period_precedence' END))) AS financial_alias_selection_json
    FROM scoped s
    LEFT JOIN checked c ON c.enrichment_sponsor_id=s.enrichment_sponsor_id
      AND c.enrichment_portco_id=s.enrichment_portco_id AND c.metric=s.metric
      AND c.external_portco_id=s.external_portco_id
    LEFT JOIN winners w ON w.enrichment_sponsor_id=s.enrichment_sponsor_id
      AND w.enrichment_portco_id=s.enrichment_portco_id AND w.metric=s.metric
    LEFT JOIN compatible v ON v.enrichment_sponsor_id=s.enrichment_sponsor_id
      AND v.enrichment_portco_id=s.enrichment_portco_id AND v.metric=s.metric
    GROUP BY s.enrichment_sponsor_id, s.enrichment_portco_id
), companies AS (
    SELECT enrichment_sponsor_id, enrichment_portco_id,
           MAX(CASE WHEN financial_review_required THEN 1 ELSE 0 END)=1 AS upstream_review_required
    FROM financial_members GROUP BY enrichment_sponsor_id, enrichment_portco_id
)
SELECT g.enrichment_sponsor_id, g.enrichment_portco_id,
       p.external_portco_id, p.source_system, p.financial_identifier, p.financial_identifier_type,
       p.metric_period AS period_end_date, p.metric_asof AS asof_date,
       v.financial_comparability_json,
       v.financial_provenance_json, v.has_findox_financials, v.findox_source_portco_id,
       a.financial_alias_selection_json,
       g.upstream_review_required OR COALESCE(a.alias_review_required, false) AS financial_review_required,
       {', '.join('v.' + metric for metric in METRICS)}
FROM companies g
LEFT JOIN values_by_company v ON v.enrichment_sponsor_id=g.enrichment_sponsor_id AND v.enrichment_portco_id=g.enrichment_portco_id
LEFT JOIN primary_value p ON p.enrichment_sponsor_id=g.enrichment_sponsor_id AND p.enrichment_portco_id=g.enrichment_portco_id
LEFT JOIN alias_audit a ON a.enrichment_sponsor_id=g.enrichment_sponsor_id AND a.enrichment_portco_id=g.enrichment_portco_id
)"""
