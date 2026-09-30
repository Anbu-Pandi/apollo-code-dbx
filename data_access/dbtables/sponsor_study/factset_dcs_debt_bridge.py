"""Exact instruments reported in a company's own FactSet capital structure."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class FactSetDebtSeed:
    portco_id: str
    factset_entity_id: str

    def __post_init__(self) -> None:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (self.portco_id, self.factset_entity_id)):
            raise ValueError("Invalid FactSet company seed identifier")


DCS_DEBT_RELATION = "reported_in_company_capital_structure"
INSTRUMENT_COLUMNS = (
    "instrument_id_uniq", "instrument_id", "isin", "bbg_id", "cusip",
    "fsym_id", "fsym_security_id", "bbg_id_fs", "bbg_id_index",
    "instrument_id_fs", "instrument_id_index",
)


def factset_dcs_debt_sql(
    seeds: tuple[FactSetDebtSeed, ...], *, table_name: str,
    factset_catalog: str, source_columns: tuple[str, ...],
    asof_cutoff: date | None,
) -> str:
    """Keep debt source rows intact; this relationship never asserts issuer identity."""
    for name in (table_name, factset_catalog):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", name):
            raise ValueError("Invalid source table identifier")
    if not seeds or not {"asof_date", "instrument_id_uniq", "instrument_type"}.issubset(source_columns):
        raise ValueError("DCS debt matching requires seeds and instrument/date columns")
    seed_values = ",\n".join(
        f"('{seed.portco_id}', '{seed.factset_entity_id}')"
        for seed in sorted(set(seeds), key=lambda item: (item.portco_id, item.factset_entity_id))
    )
    cutoff = f"WHERE CAST(asof_date AS DATE) <= DATE '{asof_cutoff.isoformat()}'" if asof_cutoff else ""
    debt_keys = "\nUNION ALL\n".join(
        f"SELECT instrument_id_uniq, '{column}' AS debt_column, CAST({column} AS STRING) AS identifier "
        f"FROM debt_base WHERE {column} IS NOT NULL"
        for column in INSTRUMENT_COLUMNS if column in source_columns
    )
    mapped_keys = "\nUNION ALL\n".join(
        f"SELECT d.*, '{kind}' AS identifier_type, CAST(s.{column} AS STRING) AS identifier "
        f"FROM dcs_instruments d JOIN {factset_catalog}.sym_v1.{table} s "
        f"ON s.fsym_id = d.factset_instrument_id WHERE s.{column} IS NOT NULL"
        for kind, table, column in (("figi", "sym_bbg", "bbg_id"), ("isin", "sym_isin", "isin"), ("cusip", "sym_cusip", "cusip"))
    )
    return f"""
WITH seeds(portco_id, factset_entity_id) AS (VALUES {seed_values}),
latest_date AS (
    SELECT MAX(CAST(asof_date AS DATE)) AS asof_date FROM {table_name} {cutoff}
), debt_base AS (
    SELECT * FROM {table_name}
    WHERE CAST(asof_date AS DATE) = (SELECT asof_date FROM latest_date)
      AND LOWER(CAST(instrument_type AS STRING)) IN ('bond', 'loan')
      AND instrument_id_uniq IS NOT NULL
    QUALIFY COUNT(*) OVER (PARTITION BY instrument_id_uniq) = 1
), latest_filings AS (
    SELECT * FROM {factset_catalog}.dcs_v2.dcs_filings
    WHERE factset_entity_id IN (SELECT factset_entity_id FROM seeds)
      AND CAST(report_date AS DATE) <= (SELECT asof_date FROM latest_date)
      AND CAST(COALESCE(publication_date, report_date) AS DATE) <= (SELECT asof_date FROM latest_date)
    QUALIFY DENSE_RANK() OVER (
        PARTITION BY factset_entity_id ORDER BY report_date DESC
    ) = 1
), dcs_instruments AS (
    SELECT s.portco_id, s.factset_entity_id, dd.instrument_id AS factset_instrument_id,
           CAST(dd.report_date AS DATE) AS dcs_report_date,
           CAST(df.publication_date AS DATE) AS dcs_publication_date
    FROM seeds s
    JOIN {factset_catalog}.dcs_v2.dcs_details dd ON dd.factset_entity_id = s.factset_entity_id
    JOIN latest_filings df
      ON df.factset_entity_id = dd.factset_entity_id AND df.report_date = dd.report_date AND df.currency = dd.currency
    WHERE dd.active = TRUE AND dd.instrument_id IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY s.portco_id, s.factset_entity_id, dd.instrument_id
        ORDER BY dd.report_date DESC, df.publication_date DESC NULLS LAST
    ) = 1
), dcs_identifiers AS (
    SELECT d.*, 'factset_instrument' AS identifier_type, CAST(factset_instrument_id AS STRING) AS identifier
    FROM dcs_instruments d
    UNION ALL
    {mapped_keys}
), debt_identifiers AS (
    {debt_keys}
), matches AS (
    SELECT d.*, i.instrument_id_uniq, i.debt_column
    FROM dcs_identifiers d JOIN debt_identifiers i
      ON UPPER(TRIM(d.identifier)) = UPPER(TRIM(i.identifier))
    WHERE TRIM(d.identifier) <> ''
)
SELECT b.*, m.portco_id, m.factset_entity_id AS portco_factset_entity_id,
       CAST(NULL AS STRING) AS debt_factset_entity_id,
       CAST(NULL AS STRING) AS debt_parent_factset_entity_id,
       CAST(NULL AS STRING) AS debt_ultimate_factset_entity_id,
       CAST(b.instrument_id_uniq AS STRING) AS debt_feature_key,
       m.factset_instrument_id, m.dcs_report_date, m.dcs_publication_date,
       'factset_dcs_instrument_exact' AS debt_bridge_match_method,
       'high' AS debt_bridge_confidence,
       '{DCS_DEBT_RELATION}' AS debt_bridge_relation,
       'factset_dcs_instrument' AS debt_bridge_source_system,
       CONCAT_WS('->', m.factset_entity_id, m.factset_instrument_id,
           CONCAT_WS(':', m.identifier_type, m.identifier), CONCAT_WS(':', 'debt_features', m.debt_column)) AS debt_bridge_source
FROM matches m JOIN debt_base b ON b.instrument_id_uniq = m.instrument_id_uniq
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY m.portco_id, b.instrument_id_uniq
    ORDER BY m.dcs_report_date DESC, m.factset_entity_id, m.factset_instrument_id, m.identifier_type, m.identifier, m.debt_column
) = 1
"""
