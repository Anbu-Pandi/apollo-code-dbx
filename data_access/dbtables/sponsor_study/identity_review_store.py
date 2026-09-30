"""Independent append-only review ledger and pinned-refresh report inputs."""

from __future__ import annotations

import re

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed

from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables.sponsor_study.external_prod_contracts import external_prod_physical_table_name
from data_access.dbtables.sponsor_study.identity_review import EVENT_COLUMNS, EVENT_TABLE


def qualified(catalog: str, schema: str, name: str, prefix: str = "pe_sponsor_") -> str:
    parts = (catalog, schema, prefix + external_prod_physical_table_name(name, catalog=catalog))
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
        raise ValueError("Invalid review SQL identifier")
    return ".".join(parts)


def literal(value: str) -> str:
    # Databricks parses adjacent quoted strings and backslash escapes; hex preserves exact text.
    return "decode(unhex('" + value.encode("utf-8").hex() + "'), 'UTF-8')"


@checkpointed
def read_events(*, catalog: str, schema: str, app: str, prefix: str = "pe_sponsor_") -> pd.DataFrame:
    table = qualified(catalog, schema, EVENT_TABLE, prefix)
    exists = readdfsql(
        f"SELECT table_name FROM {catalog}.information_schema.tables "
        f"WHERE table_schema = {literal(schema)} AND table_name = {literal(table.rsplit('.', 1)[1])}", app=app,
    )
    if exists.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    # The SQL dataframe adapter truncates timestamp fractions; text transport preserves the audit clock.
    events = readdfsql(f"SELECT * EXCEPT (reviewed_at), CAST(reviewed_at AS STRING) AS reviewed_at FROM {table}", app=app)
    events["reviewed_at"] = pd.to_datetime(events.reviewed_at, utc=True, format="mixed").dt.tz_localize(None)
    return events


def current_refresh(*, catalog: str, schema: str, app: str, prefix: str = "pe_sponsor_") -> str:
    table = qualified(catalog, schema, "meta_external_refresh_run", prefix)
    result = readdfsql(f"SELECT refresh_run_id FROM {table} WHERE status = 'succeeded' "
                      "ORDER BY run_completed_at DESC NULLS LAST, refresh_run_id DESC LIMIT 1", app=app)
    if result.empty:
        raise ValueError("No succeeded sponsor refresh is available")
    return str(result.iloc[0].refresh_run_id)


def load_review_dataset(*, catalog: str, schema: str, app: str,
                        prefix: str = "pe_sponsor_") -> tuple[str, dict[str, pd.DataFrame]]:
    run_id = current_refresh(catalog=catalog, schema=schema, app=app, prefix=prefix)
    projections = {
        "fact_external_holding": "*",
        "xref_external_company_name_decision": "*",
        "xref_external_company_capital_stack": (
            "DISTINCT external_portco_id, original_seed_bbg_company_id, seed_bbg_company_id, "
            "legacy_seed_promoted, capital_stack_bbg_company_id, stack_eligibility"
        ),
        "source_external_debt_issuer_bridge": "DISTINCT external_portco_id, portco_factset_entity_id, debt_factset_entity_id, debt_bridge_relation",
    }
    dataset = {name: readdfsql(
        f"SELECT {projection} FROM {qualified(catalog, schema, name, prefix)} "
        f"WHERE refresh_run_id = {literal(run_id)}", app=app,
    ) for name, projection in projections.items()}
    dataset["source_factset_company_profile"] = read_factset_company_profiles(
        run_id=run_id, catalog=catalog, schema=schema, app=app, prefix=prefix)
    return run_id, dataset


def read_factset_company_profiles(*, run_id: str, catalog: str, schema: str,
                                  app: str, prefix: str = "pe_sponsor_") -> pd.DataFrame:
    holdings = qualified(catalog, schema, "fact_external_holding", prefix)
    return readdfsql(f"""
SELECT e.factset_entity_id, NULLIF(TRIM(e.web_site), '') AS company_url,
       COALESCE(NULLIF(TRIM(p.entity_profile), ''), NULLIF(TRIM(s.entity_profile), '')) AS company_description,
       TO_JSON(NAMED_STRUCT('entity', STRUCT(e.*), 'description_profile_type',
           CASE WHEN NULLIF(TRIM(p.entity_profile), '') IS NOT NULL THEN 'PRO'
                WHEN NULLIF(TRIM(s.entity_profile), '') IS NOT NULL THEN 'PRD' END)) AS company_profile_json,
       CURRENT_TIMESTAMP() AS profile_observed_at
FROM dbrx_factset_prod.ent_v1.ent_entity_coverage e
LEFT JOIN dbrx_factset_prod.ffp_v1.ffp_entity_profiles p
  ON p.factset_entity_id = e.factset_entity_id AND p.entity_profile_type = 'PRO'
LEFT JOIN dbrx_factset_prod.ffp_v1.ffp_entity_profiles s
  ON s.factset_entity_id = e.factset_entity_id AND s.entity_profile_type = 'PRD'
WHERE EXISTS (
    SELECT 1 FROM {holdings} h
    WHERE h.refresh_run_id = {literal(run_id)}
      AND h.holding_source_system = 'factset_pevc'
      AND h.source_company_id = e.factset_entity_id
)
""", app=app)


def append_review_event(event: pd.DataFrame, *, catalog: str, schema: str,
                        app: str, prefix: str = "pe_sponsor_") -> None:
    if len(event) != 1:
        raise ValueError("Submit one review event at a time")
    table = qualified(catalog, schema, EVENT_TABLE, prefix)
    columns = ", ".join(f"{name} {'TIMESTAMP' if name == 'reviewed_at' else 'STRING'}"
                        for name in EVENT_COLUMNS)
    execsql(f"CREATE TABLE IF NOT EXISTS {table} ({columns}) USING DELTA", app=app)
    row = event.iloc[0]
    values = ", ".join(
        f"CAST({literal(str(row[name]))} AS TIMESTAMP) AS {name}" if name == "reviewed_at"
        else f"{literal(str(row[name]))} AS {name}" for name in EVENT_COLUMNS)
    control = qualified(catalog, schema, "meta_external_refresh_run", prefix)
    # Snapshot/version checks are inside the statement, not just in the CLI preview.
    sql = f"""
MERGE INTO {table} t
USING (
    SELECT * FROM (SELECT {values}) submitted
    WHERE COALESCE((SELECT event_id FROM {table}
        WHERE pair_id = {literal(row.pair_id)}
        ORDER BY reviewed_at DESC, event_id DESC LIMIT 1), '') = {literal(row.expected_event_id)}
      AND (SELECT refresh_run_id FROM {control} WHERE status = 'succeeded'
        ORDER BY run_completed_at DESC NULLS LAST, refresh_run_id DESC LIMIT 1)
        = {literal(row.base_refresh_run_id)}
) s
ON t.event_id = s.event_id
WHEN NOT MATCHED THEN INSERT *
"""
    execsql(sql, app=app)
    stored = readdfsql(f"SELECT * EXCEPT (reviewed_at), CAST(reviewed_at AS STRING) AS reviewed_at "
                       f"FROM {table} WHERE event_id = {literal(row.event_id)}", app=app)
    if len(stored) != 1:
        raise ValueError("Review not recorded: refresh or review version changed")
    expected = event[EVENT_COLUMNS].reset_index(drop=True).copy()
    stored = stored[EVENT_COLUMNS].reset_index(drop=True).copy()
    for frame in (expected, stored):
        frame["reviewed_at"] = pd.to_datetime(frame.reviewed_at, utc=True, format="mixed")
    pd.testing.assert_frame_equal(expected.astype("string"), stored.astype("string"))


def verify_published_audit(audit: pd.DataFrame, *, run_id: str, catalog: str,
                           schema: str, app: str) -> None:
    from data_access.dbtables.sponsor_study.query import pe_sponsor_holding_identity_resolved_view_sql

    sql = pe_sponsor_holding_identity_resolved_view_sql(catalog=catalog, source_schema=schema, audit=True)
    readdfsql("EXPLAIN " + sql.split(" AS\n", 1)[1], app=app)
    table = qualified(catalog, schema, "holding_identity_resolved")
    published = readdfsql(f"SELECT external_holding_id FROM {table} "
                         f"WHERE refresh_run_id = {literal(run_id)} AND identity_resolution_rank = 1", app=app)
    retained = set(audit.loc[audit.outcome.eq("retained"), "external_holding_id"])
    if retained != set(published.external_holding_id):
        raise ValueError("Local audit retained IDs differ from the published serving layer")
