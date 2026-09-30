"""Sponsor-scoped enrichment through identities already resolved by the holding audit."""
from __future__ import annotations


def identity_enrichment_ctes_sql(*, audit: str, financial: str, market: str, debt: str,
                                 latest_refresh: str) -> str:
    """Keep complete financial/instrument rows; never infer a new company relationship."""
    identity_rows = f"""
    SELECT DISTINCT external_sponsor_id, resolved_portco_id, original_portco_id
    FROM {audit}
    WHERE refresh_run_id = {latest_refresh}
      AND outcome <> 'excluded_website_non_company'
      AND original_portco_id IS NOT NULL AND resolved_portco_id IS NOT NULL
""".strip()
    return f"""
identity_members_raw AS (
    {identity_rows}
),
identity_members AS (
    SELECT * FROM identity_members_raw
    UNION
    SELECT external_sponsor_id, resolved_portco_id, resolved_portco_id AS original_portco_id
    FROM identity_members_raw
),
financial_members AS (
    SELECT m.external_sponsor_id AS enrichment_sponsor_id,
           m.resolved_portco_id AS enrichment_portco_id, f.*
    FROM {financial} f
    INNER JOIN identity_members m ON m.original_portco_id = f.external_portco_id
    WHERE f.refresh_run_id = {latest_refresh}
),
market_members AS (
    SELECT m.external_sponsor_id AS enrichment_sponsor_id,
           m.resolved_portco_id AS enrichment_portco_id, s.*
    FROM {market} s
    INNER JOIN identity_members m ON m.original_portco_id = s.external_portco_id
    WHERE s.refresh_run_id = {latest_refresh}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY m.external_sponsor_id, m.resolved_portco_id, s.instrument_id_uniq
        ORDER BY s.asof_date DESC NULLS LAST,
                 CASE WHEN s.price IS NOT NULL THEN 0 ELSE 1 END,
                 CASE WHEN s.source_system LIKE 'debt_features%' THEN 0 ELSE 1 END,
                 CASE WHEN s.external_portco_id = m.resolved_portco_id THEN 0 ELSE 1 END,
                 s.external_portco_id, s.source_system
    ) = 1
),
debt_members AS (
    SELECT m.external_sponsor_id AS enrichment_sponsor_id,
           m.resolved_portco_id AS enrichment_portco_id, s.*
    FROM {debt} s
    INNER JOIN identity_members m ON m.original_portco_id = s.external_portco_id
    WHERE s.refresh_run_id = {latest_refresh}
    QUALIFY CASE WHEN s.instrument_id_uniq IS NOT NULL THEN
        ROW_NUMBER() OVER (
            PARTITION BY m.external_sponsor_id, m.resolved_portco_id, s.instrument_id_uniq
            ORDER BY s.asof_date DESC NULLS LAST,
                     CASE WHEN s.external_portco_id = m.resolved_portco_id THEN 0 ELSE 1 END,
                     s.external_portco_id, s.source_system
        ) = 1
    ELSE
        -- Unidentified lines cannot be matched individually. Retain one source's
        -- complete unidentified stack, rather than summing it again through aliases.
        s.external_portco_id = FIRST_VALUE(s.external_portco_id) OVER (
            PARTITION BY m.external_sponsor_id, m.resolved_portco_id, s.instrument_id_uniq
            ORDER BY CASE WHEN s.external_portco_id = m.resolved_portco_id THEN 0 ELSE 1 END,
                     s.external_portco_id
        )
    END
)
""".strip()
