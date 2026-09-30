"""Delta-backed run manifests for website crawl and external refresh."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from common.utils.sqlutil import insertdf_batch
from data_access.dbtables.sponsor_study.website_delta_io import insert_website_rows
from data_access.dbtables.sponsor_study.persistence import (
    _delta_insert_batch_size,
    _escape_delta_insert_strings,
    _qualified_name,
    delta_defaults_for_env,
    ensure_delta_tables,
)
from data_access.dbtables.sponsor_study.query import DEFAULT_TABLE_PREFIX
from data_importer.sponsor_study.audit.delta_registry import (
    json_dumps,
    resolve_source_machine,
    utc_now_naive,
)


META_RUN_MANIFEST_TABLE = "meta_run_manifest"
META_RUN_STAGE_TABLE = "meta_run_stage"
RUN_KIND_WEBSITE_CRAWL = "website_crawl"
RUN_KIND_EXTERNAL_REFRESH = "external_refresh"

META_RUN_MANIFEST_COLUMNS: dict[str, str] = {
    "event_ts": "TIMESTAMP",
    "run_kind": "STRING",
    "run_id": "STRING",
    "status": "STRING",
    "environment": "STRING",
    "catalog": "STRING",
    "schema": "STRING",
    "error_message": "STRING",
    "manifest_json": "STRING",
    "source_cutoff_date": "TIMESTAMP",
    "related_run_id": "STRING",
    "source_machine": "STRING",
}

META_RUN_STAGE_COLUMNS: dict[str, str] = {
    "event_ts": "TIMESTAMP",
    "run_id": "STRING",
    "stage_name": "STRING",
    "batch_id": "STRING",
    "attempt": "INT",
    "status": "STRING",
    "started_at": "TIMESTAMP",
    "completed_at": "TIMESTAMP",
    "input_rows": "BIGINT",
    "output_rows": "BIGINT",
    "error_message": "STRING",
    "manifest_json": "STRING",
}


def meta_run_manifest_contract() -> pd.DataFrame:
    out = pd.DataFrame(columns=list(META_RUN_MANIFEST_COLUMNS))
    out.attrs["delta_schema_types"] = {
        "event_ts": "TIMESTAMP",
        "source_cutoff_date": "TIMESTAMP",
    }
    return out


def meta_run_stage_contract() -> pd.DataFrame:
    out = pd.DataFrame(columns=list(META_RUN_STAGE_COLUMNS))
    out.attrs["delta_schema_types"] = {
        "event_ts": "TIMESTAMP",
        "started_at": "TIMESTAMP",
        "completed_at": "TIMESTAMP",
    }
    return out


def ensure_meta_run_manifest_table(
    *,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    defaults = delta_defaults_for_env(environment)
    ensure_delta_tables(
        {META_RUN_MANIFEST_TABLE: meta_run_manifest_contract()},
        catalog=catalog,
        schema=schema,
        storage_account=defaults["storage_account"],
        blob_container=defaults["blob_container"],
        blob_dir=defaults["blob_dir"],
        app=app,
        table_prefix=table_prefix,
        cleantable=False,
    )
    return _qualified_name(catalog, schema, META_RUN_MANIFEST_TABLE, table_prefix)


def persist_run_manifest(
    *,
    run_kind: str,
    run_id: str,
    status: str,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    error_message: str | None = None,
    manifest: dict[str, Any] | None = None,
    source_cutoff_date: datetime | pd.Timestamp | str | None = None,
    related_run_id: str | None = None,
) -> str:
    qualified = ensure_meta_run_manifest_table(
        environment=environment,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    cutoff = None
    if source_cutoff_date not in {None, ""}:
        cutoff = pd.Timestamp(source_cutoff_date)
        if getattr(cutoff, "tzinfo", None) is not None:
            cutoff = cutoff.tz_localize(None)
    row = {
        "event_ts": utc_now_naive(),
        "run_kind": str(run_kind or "").strip(),
        "run_id": str(run_id or "").strip(),
        "status": str(status or "").strip(),
        "environment": str(environment or "").strip(),
        "catalog": str(catalog or "").strip(),
        "schema": str(schema or "").strip(),
        "error_message": (str(error_message)[:4000] if error_message else None),
        "manifest_json": json_dumps(manifest or {}),
        "source_cutoff_date": cutoff,
        "related_run_id": str(related_run_id or "").strip() or None,
        "source_machine": resolve_source_machine(),
    }
    insert_df = pd.DataFrame([row], columns=list(META_RUN_MANIFEST_COLUMNS))
    writer = insert_website_rows if run_kind == RUN_KIND_WEBSITE_CRAWL else insertdf_batch
    if run_kind != RUN_KIND_WEBSITE_CRAWL:
        insert_df = _escape_delta_insert_strings(insert_df)
    writer(
        qualified,
        insert_df,
        app=app,
        cleantable=False,
        msg=f"append sponsor study run manifest {run_kind} {run_id}",
        maxrow_batch=_delta_insert_batch_size(META_RUN_MANIFEST_TABLE, insert_df),
    )
    return qualified


def ensure_meta_run_stage_table(
    *, environment: str, catalog: str, schema: str, app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    defaults = delta_defaults_for_env(environment)
    ensure_delta_tables(
        {META_RUN_STAGE_TABLE: meta_run_stage_contract()},
        catalog=catalog, schema=schema, storage_account=defaults["storage_account"],
        blob_container=defaults["blob_container"], blob_dir=defaults["blob_dir"],
        app=app, table_prefix=table_prefix, cleantable=False,
    )
    return _qualified_name(catalog, schema, META_RUN_STAGE_TABLE, table_prefix)


def persist_run_stage(
    *, run_id: str, stage_name: str, batch_id: str, attempt: int, status: str,
    environment: str, catalog: str, schema: str, app: str,
    started_at: datetime | pd.Timestamp | str | None = None,
    completed_at: datetime | pd.Timestamp | str | None = None,
    input_rows: int | None = None, output_rows: int | None = None,
    error_message: str | None = None, manifest: dict[str, Any] | None = None,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    qualified = ensure_meta_run_stage_table(
        environment=environment, catalog=catalog, schema=schema, app=app,
        table_prefix=table_prefix,
    )

    def timestamp(value: datetime | pd.Timestamp | str | None) -> pd.Timestamp | None:
        if value in {None, ""}:
            return None
        stamp = pd.Timestamp(value)
        return stamp.tz_localize(None) if stamp.tzinfo is not None else stamp

    row = {
        "event_ts": utc_now_naive(), "run_id": str(run_id),
        "stage_name": str(stage_name), "batch_id": str(batch_id),
        "attempt": int(attempt), "status": str(status),
        "started_at": timestamp(started_at), "completed_at": timestamp(completed_at),
        "input_rows": input_rows, "output_rows": output_rows,
        "error_message": str(error_message)[:4000] if error_message else None,
        "manifest_json": json_dumps(manifest or {}),
    }
    frame = _escape_delta_insert_strings(pd.DataFrame([row], columns=list(META_RUN_STAGE_COLUMNS)))
    insertdf_batch(
        qualified, frame, app=app, cleantable=False,
        msg=f"append sponsor study stage {run_id} {stage_name} {batch_id}",
        maxrow_batch=_delta_insert_batch_size(META_RUN_STAGE_TABLE, frame),
    )
    return qualified
