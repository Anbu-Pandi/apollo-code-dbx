"""Unity Catalog metadata capture and reconciliation for metadata-preserving DDL."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.external_prod_contracts import (
    BENCHMARK_V31_TABLES,
    EXTERNAL_PROD_PHYSICAL_TABLE_NAMES,
)
from data_access.dbtables.sponsor_study.query import (
    DEFAULT_TABLE_PREFIX,
    gold_pe_sponsor_dashboard_view_scripts,
    prefixed_name,
)

ENTRY_METRIC_COLUMNS = (
    "deal_size_tev",
    "equity_commitment",
    "entry_multiple",
)
GOLD_DASHBOARD_VIEWS = (
    "v_pe_sponsor_investment_360",
    "v_pe_sponsor_fund_360",
    "v_pe_sponsor_market_monthly_360",
    "v_pe_sponsor_data_quality",
)


def _qualified_table(
    catalog: str,
    schema: str,
    logical_name: str,
    *,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    physical = EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.get(logical_name, logical_name)
    table = prefixed_name(physical, table_prefix)
    return f"{catalog}.{schema}.{table}"


def entry_metrics_affected_objects(
    *,
    catalog: str,
    dbo_schema: str = "dbo",
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, list[str]]:
    """Return dbo tables and gold views touched by entry-metrics hydration."""
    dbo_tables = [
        _qualified_table(
            catalog,
            dbo_schema,
            name,
            table_prefix=table_prefix,
        )
        for name in BENCHMARK_V31_TABLES
    ]
    dbo_tables.extend([
        _qualified_table(
            catalog,
            dbo_schema,
            "source_benchmark_v31_comment",
            table_prefix=table_prefix,
        ),
        _qualified_table(
            catalog,
            dbo_schema,
            "source_benchmark_v31_holding_xref",
            table_prefix=table_prefix,
        ),
    ])
    gold_views = [
        f"{catalog}.{gold_schema}.{name}"
        for name in GOLD_DASHBOARD_VIEWS
    ]
    return {"dbo_tables": sorted(set(dbo_tables)), "gold_views": gold_views}


def _table_metadata_row(
    catalog: str,
    schema: str,
    table_name: str,
    *,
    app: str,
) -> dict[str, Any]:
    qualified = f"{catalog}.{schema}.{table_name}"
    info = readdfsql(
        f"""
SELECT table_catalog, table_schema, table_name, table_type
FROM {catalog}.information_schema.tables
WHERE table_schema = '{schema}'
  AND table_name = '{table_name}'
""".strip(),
        app=app,
    )
    if info.empty:
        raise ValueError(f"Missing metadata object: {qualified}")
    row = info.iloc[0]
    describe = readdfsql(f"DESCRIBE TABLE EXTENDED {qualified}", app=app)
    properties: dict[str, str] = {}
    owner = ""
    if not describe.empty and "col_name" in describe.columns:
        info_rows = describe[
            describe["col_name"].astype(str).str.startswith("#")
        ]
        for info_row in info_rows.itertuples(index=False):
            key = str(info_row.col_name).lstrip("#").strip().lower()
            value = str(getattr(info_row, "data_type", "") or "").strip()
            if key == "owner":
                owner = value
            elif key in {"table properties", "view properties"} and value:
                for part in value.split(","):
                    if "=" in part:
                        prop_key, prop_value = part.split("=", 1)
                        properties[prop_key.strip()] = prop_value.strip()
    columns = readdfsql(
        f"""
SELECT column_name, data_type, ordinal_position, comment
FROM {catalog}.information_schema.columns
WHERE table_schema = '{schema}'
  AND table_name = '{table_name}'
ORDER BY ordinal_position
""".strip(),
        app=app,
    )
    column_comments = {
        str(row.column_name): str(row.comment or "")
        for row in columns.itertuples(index=False)
    }
    ordered_schema = [
        {"column_name": str(row.column_name), "data_type": str(row.data_type)}
        for row in columns.itertuples(index=False)
    ]
    grants = readdfsql(f"SHOW GRANTS ON {qualified}", app=app)
    grant_records = (
        grants.fillna("").astype(str).to_dict(orient="records")
        if not grants.empty
        else []
    )
    tags = readdfsql(f"SHOW TAGS ON {qualified}", app=app)
    tag_records = (
        tags.fillna("").astype(str).to_dict(orient="records")
        if not tags.empty
        else []
    )
    return {
        "qualified_name": qualified,
        "table_catalog": str(row.table_catalog),
        "table_schema": str(row.table_schema),
        "table_name": str(row.table_name),
        "table_type": str(row.table_type),
        "owner": owner,
        "properties": properties,
        "ordered_schema": ordered_schema,
        "column_comments": column_comments,
        "grants": grant_records,
        "tags": tag_records,
    }


def capture_metadata_snapshot(
    *,
    catalog: str,
    dbo_schema: str = "dbo",
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    app: str = "databricks",
) -> dict[str, Any]:
    """Capture owners, grants, tags, properties, comments, and schemas."""
    affected = entry_metrics_affected_objects(
        catalog=catalog,
        dbo_schema=dbo_schema,
        gold_schema=gold_schema,
        table_prefix=table_prefix,
    )
    objects: dict[str, Any] = {}
    for qualified in affected["dbo_tables"]:
        _, schema_name, table_name = qualified.rsplit(".", 2)
        objects[qualified] = _table_metadata_row(
            catalog,
            schema_name,
            table_name,
            app=app,
        )
    for qualified in affected["gold_views"]:
        _, schema_name, view_name = qualified.rsplit(".", 2)
        objects[qualified] = _table_metadata_row(
            catalog,
            schema_name,
            view_name,
            app=app,
        )
    investment = f"{catalog}.{gold_schema}.v_pe_sponsor_investment_360"
    entry_metric_counts = {
        column: None for column in ENTRY_METRIC_COLUMNS
    }
    if investment in objects:
        select_parts = [
            f"COUNT({column}) AS {column}_non_null"
            for column in ENTRY_METRIC_COLUMNS
            if column in objects[investment]["column_comments"]
            or any(
                row["column_name"] == column
                for row in objects[investment]["ordered_schema"]
            )
        ]
        if select_parts:
            counts = readdfsql(
                f"SELECT {', '.join(select_parts)} FROM {investment}",
                app=app,
            )
            if not counts.empty:
                entry_metric_counts = {
                    column: int(counts.iloc[0][f"{column}_non_null"])
                    for column in ENTRY_METRIC_COLUMNS
                    if f"{column}_non_null" in counts.columns
                }
    return {
        "catalog": catalog,
        "dbo_schema": dbo_schema,
        "gold_schema": gold_schema,
        "objects": objects,
        "entry_metric_counts": entry_metric_counts,
    }


def reconcile_metadata_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    """Return metadata regressions that should block publication."""
    issues: list[str] = []
    before_objects = before.get("objects", {})
    after_objects = after.get("objects", {})
    missing = sorted(set(before_objects) - set(after_objects))
    if missing:
        issues.append(f"missing objects after migration: {missing}")
    for qualified, prior in before_objects.items():
        current = after_objects.get(qualified)
        if current is None:
            continue
        if prior.get("owner") and prior.get("owner") != current.get("owner"):
            issues.append(
                f"owner changed for {qualified}: "
                f"{prior.get('owner')} -> {current.get('owner')}"
            )
        if prior.get("grants") != current.get("grants"):
            issues.append(f"grants changed for {qualified}")
        if prior.get("tags") != current.get("tags"):
            issues.append(f"tags changed for {qualified}")
        if prior.get("properties") != current.get("properties"):
            issues.append(f"properties changed for {qualified}")
        prior_comments = prior.get("column_comments", {})
        current_comments = current.get("column_comments", {})
        shared_columns = set(prior_comments) & set(current_comments)
        for column in sorted(shared_columns):
            if prior_comments[column] != current_comments[column]:
                issues.append(
                    f"column comment changed for {qualified}.{column}"
                )
        if qualified.endswith(".v_pe_sponsor_investment_360"):
            prior_schema = [
                row["column_name"]
                for row in prior.get("ordered_schema", [])
            ]
            current_schema = [
                row["column_name"]
                for row in current.get("ordered_schema", [])
            ]
            if prior_schema and current_schema[: len(prior_schema)] != prior_schema:
                issues.append(
                    f"investment view column order changed for {qualified}"
                )
            appended = current_schema[len(prior_schema):]
            if appended != list(ENTRY_METRIC_COLUMNS):
                issues.append(
                    f"unexpected appended investment columns: {appended}"
                )
    return issues


def preflight_alter_view_permissions(
    *,
    catalog: str,
    gold_schema: str = "gold",
    app: str = "databricks",
) -> None:
    """Fail before mutation when gold views cannot be altered in place."""
    missing = []
    for view_name in GOLD_DASHBOARD_VIEWS:
        qualified = f"{catalog}.{gold_schema}.{view_name}"
        exists = readdfsql(
            f"""
SELECT table_name
FROM {catalog}.information_schema.views
WHERE table_schema = '{gold_schema}'
  AND table_name = '{view_name}'
""".strip(),
            app=app,
        )
        if exists.empty:
            missing.append(qualified)
    if missing:
        raise ValueError(
            "ALTER VIEW preflight requires existing gold views: "
            + ", ".join(sorted(missing))
        )


def metadata_snapshot_to_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True, default=str)


def gold_view_select_scripts(
    *,
    catalog: str,
    source_schema: str = "dbo",
    gold_schema: str = "gold",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    """Return gold view bodies without CREATE/ALTER DDL wrappers."""
    wrapped = gold_pe_sponsor_dashboard_view_scripts(
        catalog=catalog,
        source_schema=source_schema,
        gold_schema=gold_schema,
        table_prefix=table_prefix,
    )
    bodies: dict[str, str] = {}
    for name, script in wrapped.items():
        marker = " AS\n"
        if marker not in script:
            marker = " AS "
        _, body = script.split(marker, 1)
        bodies[name] = body.strip().rstrip(";")
    return bodies
