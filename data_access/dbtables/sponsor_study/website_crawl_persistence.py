from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from common.utils.sqlutil import execsql
from data_access.dbtables.sponsor_study.website_delta_io import insert_website_rows
from data_access.dbtables.sponsor_study.website_quality import SponsorCaptureQuality
from data_access.dbtables.sponsor_study.persistence import (
    _delta_insert_batch_size,
    _qualified_name,
    delta_defaults_for_env,
    ensure_delta_tables,
)
from data_access.dbtables.sponsor_study.query import DEFAULT_TABLE_PREFIX
from data_access.dbtables.sponsor_study.website_sources import (
    WEBSITE_BRIDGE_COLUMNS,
    WEBSITE_HOLDING_COLUMNS,
    WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
    WEBSITE_RECONCILIATION_COLUMNS,
    WEBSITE_XREF_COLUMNS,
    clean_site_snapshots,
)
from data_importer.sponsor_study.audit.delta_registry import (
    ARTIFACT_COLUMNS,
    ARTIFACT_TABLE,
    AUDIT_JSON_COLUMNS,
    AUDIT_JSON_TABLE,
    MANIFEST_COLUMNS,
    MANIFEST_TABLE,
    SOURCE_OBSERVATION_COLUMNS,
    SOURCE_OBSERVATION_TABLE,
)
from data_access.dbtables.sponsor_study.website_evidence import HOLDING_CANDIDATE_COLUMNS


SPONSOR_WEBSITE_SEED_COLUMNS = [
    "sponsor_website_id",
    "sponsor",
    "website",
    "portfolio_page",
    "active_flag",
    "crawl_priority",
    "max_pages_override",
    "extraction_profile",
    "browser_profile_key",
    "crawl_mode",
    "source_completeness",
    "effective_from",
    "effective_to",
    "updated_at",
    "updated_by",
    "source_excel_row",
]
RUN_CONTROL_COLUMNS = [
    "crawl_run_id",
    "logical_crawl_run_id",
    "run_started_at",
    "run_completed_at",
    "status",
    "environment",
    "catalog",
    "schema",
    "code_version",
    "config_json",
    "requested_sponsor",
    "sponsor_count",
    "attempted_sponsor_count",
    "resumed_sponsor_count",
    "snapshot_count",
    "candidate_count",
    "holding_count",
    "blocked_sponsor_count",
    "error_sponsor_count",
    "error_message",
]
RUN_SPONSOR_CONTROL_COLUMNS = [
    "crawl_run_id",
    "logical_crawl_run_id",
    "configuration_fingerprint",
    "publication_verified_at",
    "verification_manifest_json",
    "previous_company_count",
    "lost_company_count",
    "lost_company_names_json",
    "sponsor_website_id",
    "sponsor",
    "crawl_mode",
    "source_completeness",
    "run_started_at",
    "run_completed_at",
    "status",
    "attempt_count",
    "snapshot_count",
    "candidate_count",
    "holding_count",
    "blocked_snapshot_count",
    "accepted_candidate_count",
    "non_company_candidate_count",
    "review_candidate_count",
    "discovered_company_count",
    "extracted_company_count",
    "missing_company_count",
    "missing_company_names_json",
    "error_snapshot_count",
    "page_limit_snapshot_count",
    "llm_failure_count",
    "last_error",
    "retry_eligible",
]
SPONSOR_SUMMARY_COLUMNS = [
    "crawl_run_id",
    "crawl_run_started_at",
    "sponsor_website_id",
    "sponsor",
    "website",
    "portfolio_page",
    "crawl_mode",
    "source_completeness",
    "snapshot_count",
    "captured_snapshot_count",
    "blocked_snapshot_count",
    "error_snapshot_count",
    "page_limit_snapshot_count",
    "llm_failure_count",
    "portfolio_index_snapshot_count",
    "detail_snapshot_count",
    "holding_candidate_count",
    "accepted_candidate_count",
    "non_company_candidate_count",
    "review_candidate_count",
    "discovered_company_count",
    "extracted_company_count",
    "missing_company_count",
    "missing_company_names_json",
    "distinct_candidate_company_count",
    "website_holding_count",
    "distinct_website_holding_company_count",
    "portfolio_bridge_count",
    "xref_match_count",
    "xref_needs_review_count",
    "xref_unmatched_count",
]

WEBSITE_CRAWL_DERIVED_TABLES = (
    "source_sponsor_site_snapshot",
    "source_sponsor_site_holding_candidate",
    "xref_website_holding_to_portco",
    "source_sponsor_site_portfolio_bridge",
    "source_sponsor_website_holding",
    "validation_website_bridge_reconciliation",
    "validation_website_crawl_sponsor_summary",
)
WEBSITE_CRAWL_CONTROL_TABLES = (
    "control_website_crawl_run",
    "control_website_crawl_run_sponsor",
)
WEBSITE_CRAWL_AUDIT_TABLES = (
    MANIFEST_TABLE,
    SOURCE_OBSERVATION_TABLE,
    ARTIFACT_TABLE,
    AUDIT_JSON_TABLE,
)
WEBSITE_CRAWL_TABLES = (
    "source_sponsor_website_list",
    *WEBSITE_CRAWL_CONTROL_TABLES,
    *WEBSITE_CRAWL_DERIVED_TABLES,
)


def _empty(columns: list[str], schema_types: dict[str, str] | None = None) -> pd.DataFrame:
    out = pd.DataFrame(columns=columns)
    if schema_types:
        out.attrs["delta_schema_types"] = schema_types
    return out


def website_crawl_audit_contract_dataset() -> dict[str, pd.DataFrame]:
    return {
        MANIFEST_TABLE: _empty(MANIFEST_COLUMNS),
        SOURCE_OBSERVATION_TABLE: _empty(
            SOURCE_OBSERVATION_COLUMNS,
            {
                "scan_started_at": "TIMESTAMP",
                "scan_finished_at": "TIMESTAMP",
                "scanned_count": "BIGINT",
                "candidate_count": "BIGINT",
            },
        ),
        ARTIFACT_TABLE: _empty(
            ARTIFACT_COLUMNS,
            {"size_bytes": "BIGINT"},
        ),
        AUDIT_JSON_TABLE: _empty(
            AUDIT_JSON_COLUMNS,
            {
                "file_modified_at": "TIMESTAMP",
                "size_bytes": "BIGINT",
            },
        ),
    }


def website_crawl_contract_dataset() -> dict[str, pd.DataFrame]:
    candidate_types = {
        "employee_count": "BIGINT",
        "investment_year": "BIGINT",
        "investment_date": "TIMESTAMP",
        "exit_date": "TIMESTAMP",
        "extracted_at": "TIMESTAMP",
        "knowledge_datetime": "TIMESTAMP",
    }
    bridge_types = {
        "knowledge_datetime": "TIMESTAMP",
        "investment_date": "TIMESTAMP",
        "investment_year": "BIGINT",
        "active_flag": "BOOLEAN",
        "exit_date": "TIMESTAMP",
    }
    return {
        "source_sponsor_website_list": _empty(
            SPONSOR_WEBSITE_SEED_COLUMNS,
            {
                "active_flag": "BOOLEAN",
                "crawl_priority": "BIGINT",
                "max_pages_override": "BIGINT",
                "effective_from": "TIMESTAMP",
                "effective_to": "TIMESTAMP",
                "updated_at": "TIMESTAMP",
                "source_excel_row": "BIGINT",
            },
        ),
        "control_website_crawl_run": _empty(
            RUN_CONTROL_COLUMNS,
            {
                "run_started_at": "TIMESTAMP",
                "run_completed_at": "TIMESTAMP",
                "sponsor_count": "BIGINT",
                "attempted_sponsor_count": "BIGINT",
                "resumed_sponsor_count": "BIGINT",
                "snapshot_count": "BIGINT",
                "candidate_count": "BIGINT",
                "holding_count": "BIGINT",
                "blocked_sponsor_count": "BIGINT",
                "error_sponsor_count": "BIGINT",
            },
        ),
        "control_website_crawl_run_sponsor": _empty(
            RUN_SPONSOR_CONTROL_COLUMNS,
            {
                "run_started_at": "TIMESTAMP",
                "run_completed_at": "TIMESTAMP",
                "publication_verified_at": "TIMESTAMP",
                "previous_company_count": "BIGINT",
                "lost_company_count": "BIGINT",
                "attempt_count": "BIGINT",
                "snapshot_count": "BIGINT",
                "candidate_count": "BIGINT",
                "holding_count": "BIGINT",
                "blocked_snapshot_count": "BIGINT",
                "accepted_candidate_count": "BIGINT",
                "non_company_candidate_count": "BIGINT",
                "review_candidate_count": "BIGINT",
                "discovered_company_count": "BIGINT",
                "extracted_company_count": "BIGINT",
                "missing_company_count": "BIGINT",
                "error_snapshot_count": "BIGINT",
                "page_limit_snapshot_count": "BIGINT",
                "llm_failure_count": "BIGINT",
                "retry_eligible": "BOOLEAN",
            },
        ),
        "source_sponsor_site_snapshot": clean_site_snapshots(None),
        "source_sponsor_site_holding_candidate": _empty(HOLDING_CANDIDATE_COLUMNS, candidate_types),
        "xref_website_holding_to_portco": _empty(WEBSITE_XREF_COLUMNS),
        "source_sponsor_site_portfolio_bridge": _empty(WEBSITE_BRIDGE_COLUMNS, bridge_types),
        "source_sponsor_website_holding": _empty(
            WEBSITE_HOLDING_COLUMNS,
            WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
        ),
        "validation_website_bridge_reconciliation": _empty(
            WEBSITE_RECONCILIATION_COLUMNS,
            {
                "knowledge_datetime": "TIMESTAMP",
                "seed_ownership_count": "BIGINT",
                "website_bridge_count": "BIGINT",
                "matched_seed_ownership_count": "BIGINT",
                "website_unmatched_count": "BIGINT",
                "matched_seed_pct": "DOUBLE",
            },
        ),
        "validation_website_crawl_sponsor_summary": _empty(
            SPONSOR_SUMMARY_COLUMNS,
            {
                "crawl_run_started_at": "TIMESTAMP",
                **{column: "BIGINT" for column in SPONSOR_SUMMARY_COLUMNS if column.endswith("_count")},
            },
        ),
    }


def ensure_website_crawl_audit_tables(
    *,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    defaults = delta_defaults_for_env(environment)
    ensure_delta_tables(
        website_crawl_audit_contract_dataset(),
        catalog=catalog,
        schema=schema,
        storage_account=defaults["storage_account"],
        blob_container=defaults["blob_container"],
        blob_dir=defaults["blob_dir"],
        app=app,
        table_prefix=table_prefix,
        cleantable=False,
    )


def _dedupe_audit_rows(
    table_name: str,
    rows: list[dict],
    *,
    existing_manifest_keys: set[tuple[str, str]] | None = None,
    existing_source_hashes: set[str] | None = None,
    existing_artifact_ids: set[str] | None = None,
    existing_audit_ids: set[str] | None = None,
) -> list[dict]:
    if not rows:
        return []
    if table_name == MANIFEST_TABLE:
        seen = set(existing_manifest_keys or ())
        deduped = []
        for row in rows:
            key = (
                str(row.get("registry_key") or "").strip(),
                str(row.get("event_hash") or "").strip(),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped
    if table_name == SOURCE_OBSERVATION_TABLE:
        seen = set(existing_source_hashes or ())
        deduped = []
        for row in rows:
            event_hash = str(row.get("event_hash") or "").strip()
            if not event_hash or event_hash in seen:
                continue
            seen.add(event_hash)
            deduped.append(row)
        return deduped
    if table_name == ARTIFACT_TABLE:
        seen = set(existing_artifact_ids or ())
        deduped = []
        for row in rows:
            artifact_id = str(row.get("artifact_id") or "").strip()
            if not artifact_id or artifact_id in seen:
                continue
            seen.add(artifact_id)
            deduped.append(row)
        return deduped
    if table_name == AUDIT_JSON_TABLE:
        seen = set(existing_audit_ids or ())
        deduped = []
        for row in rows:
            audit_id = str(row.get("audit_id") or "").strip()
            if not audit_id or audit_id in seen:
                continue
            seen.add(audit_id)
            deduped.append(row)
        return deduped
    raise ValueError(f"Unsupported website crawl audit table: {table_name}")


def open_delta_crawl_audit_store(
    *,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
):
    ensure_website_crawl_audit_tables(
        environment=environment,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    from data_importer.sponsor_study.audit.delta_registry import DeltaCrawlAuditStore

    qualified_tables = {
        table_name: _qualified_name(catalog, schema, table_name, table_prefix)
        for table_name in WEBSITE_CRAWL_AUDIT_TABLES
    }
    return DeltaCrawlAuditStore(qualified_tables=qualified_tables, app=app)


def persist_website_crawl_audit_events(
    events: dict[str, list[dict] | pd.DataFrame],
    *,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, int]:
    ensure_website_crawl_audit_tables(
        environment=environment,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    from data_importer.sponsor_study.audit.delta_registry import DeltaCrawlAuditStore

    qualified_tables = {
        table_name: _qualified_name(catalog, schema, table_name, table_prefix)
        for table_name in WEBSITE_CRAWL_AUDIT_TABLES
    }
    store = DeltaCrawlAuditStore(qualified_tables=qualified_tables, app=app)
    existing_manifest_keys = store.existing_manifest_keys()
    existing_source_hashes = store.existing_source_hashes()
    existing_artifact_ids = store.existing_artifact_ids()
    existing_audit_ids = store.existing_audit_ids()
    written: dict[str, int] = {}
    for table_name in WEBSITE_CRAWL_AUDIT_TABLES:
        payload = events.get(table_name)
        if payload is None:
            written[table_name] = 0
            continue
        rows = payload.to_dict("records") if isinstance(payload, pd.DataFrame) else list(payload)
        deduped = _dedupe_audit_rows(
            table_name,
            rows,
            existing_manifest_keys=existing_manifest_keys,
            existing_source_hashes=existing_source_hashes,
            existing_artifact_ids=existing_artifact_ids,
            existing_audit_ids=existing_audit_ids,
        )
        if not deduped:
            written[table_name] = 0
            continue
        qualified = qualified_tables[table_name]
        insert_df = pd.DataFrame(deduped)
        insert_website_rows(
            qualified,
            insert_df,
            app=app,
            cleantable=False,
            msg=f"append sponsor website crawl audit rows {table_name}",
            maxrow_batch=_delta_insert_batch_size(table_name, insert_df),
        )
        written[table_name] = len(deduped)
        if table_name == MANIFEST_TABLE:
            existing_manifest_keys.update(
                (
                    str(row.get("registry_key") or "").strip(),
                    str(row.get("event_hash") or "").strip(),
                )
                for row in deduped
            )
        elif table_name == SOURCE_OBSERVATION_TABLE:
            existing_source_hashes.update(
                str(row.get("event_hash") or "").strip()
                for row in deduped
                if str(row.get("event_hash") or "").strip()
            )
        elif table_name == ARTIFACT_TABLE:
            existing_artifact_ids.update(
                str(row.get("artifact_id") or "").strip()
                for row in deduped
                if str(row.get("artifact_id") or "").strip()
            )
        elif table_name == AUDIT_JSON_TABLE:
            existing_audit_ids.update(
                str(row.get("audit_id") or "").strip()
                for row in deduped
                if str(row.get("audit_id") or "").strip()
            )
    return written


def ensure_website_crawl_tables(
    *,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    defaults = delta_defaults_for_env(environment)
    ensure_delta_tables(
        website_crawl_contract_dataset(),
        catalog=catalog,
        schema=schema,
        storage_account=defaults["storage_account"],
        blob_container=defaults["blob_container"],
        blob_dir=defaults["blob_dir"],
        app=app,
        table_prefix=table_prefix,
        cleantable=False,
    )


def _run_id_literal(crawl_run_id: str) -> str:
    return "'" + str(crawl_run_id).replace("'", "''") + "'"


def _require_legacy_run(crawl_run_id: str) -> None:
    import re

    if re.search(r"__[0-9a-f]{32}$", crawl_run_id):
        raise ValueError("Immutable crawl attempts cannot use legacy replacement writers")


def _replace_run_rows(
    name: str,
    df: pd.DataFrame,
    *,
    crawl_run_id: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str,
) -> None:
    _require_legacy_run(crawl_run_id)
    _validate_run_rows(name, df, crawl_run_id=crawl_run_id)
    qualified = _qualified_name(catalog, schema, name, table_prefix)
    execsql(
        f"DELETE FROM {qualified} WHERE crawl_run_id = {_run_id_literal(crawl_run_id)}",
        app=app,
        msg=f"clear sponsor website crawl run rows {qualified}",
    )
    if df is None or df.empty:
        logging.info("No rows to persist for %s run=%s", name, crawl_run_id)
        return
    insert_website_rows(
        qualified,
        df,
        app=app,
        cleantable=False,
        msg=f"insert sponsor website crawl run rows {name}",
        maxrow_batch=_delta_insert_batch_size(name, df),
    )


def _validate_run_rows(name: str, df: pd.DataFrame, *, crawl_run_id: str) -> None:
    if df.empty:
        return
    if "crawl_run_id" not in df or df["crawl_run_id"].isna().any() or not df["crawl_run_id"].eq(crawl_run_id).all():
        raise ValueError(f"{name} contains missing or unexpected crawl_run_id values")
    identity = {
        "source_sponsor_site_holding_candidate": "website_holding_candidate_id",
        "source_sponsor_website_holding": "website_holding_id",
        "control_website_crawl_run": "crawl_run_id",
        "control_website_crawl_run_sponsor": "sponsor_website_id",
    }.get(name)
    if identity and (df[identity].isna().any() or df[identity].duplicated().any()):
        raise ValueError(f"{name} requires unique non-null {identity} values per run")


def persist_website_crawl_capture(
    snapshots: pd.DataFrame,
    *,
    crawl_run_id: str,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    _require_legacy_run(crawl_run_id)
    ensure_website_crawl_tables(
        environment=environment, catalog=catalog, schema=schema, app=app, table_prefix=table_prefix,
    )
    _replace_run_rows(
        "source_sponsor_site_snapshot", clean_site_snapshots(snapshots),
        crawl_run_id=crawl_run_id, catalog=catalog, schema=schema, app=app, table_prefix=table_prefix,
    )
    execsql(
        f"UPDATE {_qualified_name(catalog, schema, 'control_website_crawl_run', table_prefix)} "
        f"SET status = 'captured' WHERE crawl_run_id = {_run_id_literal(crawl_run_id)}",
        app=app, msg="checkpoint website crawl capture before extraction",
    )


def persist_website_crawl_run(
    dataset: dict[str, pd.DataFrame],
    *,
    crawl_run_id: str,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    _require_legacy_run(crawl_run_id)
    # Validate before any DELETE, and publish the run marker only after every
    # derived table and sponsor control has been written successfully.
    names = (*WEBSITE_CRAWL_DERIVED_TABLES, "control_website_crawl_run_sponsor", "control_website_crawl_run")
    for name in names:
        _validate_run_rows(name, dataset.get(name, pd.DataFrame()), crawl_run_id=crawl_run_id)
    missing = set(names) - set(dataset)
    if missing:
        raise ValueError(f"Incomplete website crawl dataset; missing tables: {', '.join(sorted(missing))}")
    if len(dataset["control_website_crawl_run"]) != 1:
        raise ValueError("Website crawl publication requires exactly one run-control row")
    ensure_website_crawl_tables(
        environment=environment,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    execsql(
        f"UPDATE {_qualified_name(catalog, schema, 'control_website_crawl_run', table_prefix)} "
        f"SET status = 'transformed' WHERE crawl_run_id = {_run_id_literal(crawl_run_id)}",
        app=app,
        msg="withhold website crawl publication while replacing run outputs",
    )
    for name in names:
        _replace_run_rows(
            name,
            dataset[name],
            crawl_run_id=crawl_run_id,
            catalog=catalog,
            schema=schema,
            app=app,
            table_prefix=table_prefix,
        )


def persist_website_crawl_control(
    dataset: dict[str, pd.DataFrame],
    *,
    crawl_run_id: str,
    environment: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    ensure_website_crawl_tables(
        environment=environment,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    for name in WEBSITE_CRAWL_CONTROL_TABLES:
        if name not in dataset:
            continue
        _replace_run_rows(
            name,
            dataset[name],
            crawl_run_id=crawl_run_id,
            catalog=catalog,
            schema=schema,
            app=app,
            table_prefix=table_prefix,
        )


def run_control_frame(
    *,
    crawl_run_id: str,
    run_started_at: datetime,
    status: str,
    environment: str,
    catalog: str,
    schema: str,
    config_json: str,
    requested_sponsor: str | None = None,
    run_completed_at: datetime | None = None,
    dataset: dict[str, pd.DataFrame] | None = None,
    error_message: str | None = None,
    code_version: str = "",
    logical_crawl_run_id: str | None = None,
) -> pd.DataFrame:
    source = dataset or {}
    summary = source.get("validation_website_crawl_sponsor_summary", pd.DataFrame())
    current = summary.loc[summary.crawl_run_id.eq(crawl_run_id)] if not summary.empty else summary
    row = {
        "crawl_run_id": crawl_run_id,
        "logical_crawl_run_id": logical_crawl_run_id,
        "run_started_at": run_started_at,
        "run_completed_at": run_completed_at,
        "status": status,
        "environment": environment,
        "catalog": catalog,
        "schema": schema,
        "code_version": code_version,
        "config_json": config_json,
        "requested_sponsor": requested_sponsor,
        "sponsor_count": int(len(summary)),
        "attempted_sponsor_count": int(len(current)),
        "resumed_sponsor_count": int(len(summary) - len(current)),
        "snapshot_count": int(current.get("snapshot_count", pd.Series(dtype="int64")).sum()),
        "candidate_count": int(current.get("holding_candidate_count", pd.Series(dtype="int64")).sum()),
        "holding_count": int(current.get("website_holding_count", pd.Series(dtype="int64")).sum()),
        "blocked_sponsor_count": int((summary.get("blocked_snapshot_count", pd.Series(dtype="int64")) > 0).sum()),
        "error_sponsor_count": int((summary.get("error_snapshot_count", pd.Series(dtype="int64")) > 0).sum()),
        "error_message": error_message,
    }
    out = pd.DataFrame([row], columns=RUN_CONTROL_COLUMNS)
    out.attrs["delta_schema_types"] = website_crawl_contract_dataset()["control_website_crawl_run"].attrs[
        "delta_schema_types"
    ]
    return out


def sponsor_control_frame(
    summary: pd.DataFrame,
    *,
    run_started_at: datetime,
    run_completed_at: datetime,
) -> pd.DataFrame:
    if summary is None or summary.empty:
        return website_crawl_contract_dataset()["control_website_crawl_run_sponsor"]
    rows = []
    for row in summary.to_dict("records"):
        holdings = int(row.get("website_holding_count") or 0)
        blocked = int(row.get("blocked_snapshot_count") or 0)
        errors = int(row.get("error_snapshot_count") or 0)
        limited = int(row.get("page_limit_snapshot_count") or 0)
        llm_failures = int(row.get("llm_failure_count") or 0)
        mode = row.get("crawl_mode", "normal")
        quality = SponsorCaptureQuality(
            holdings, blocked, errors, limited, llm_failures,
            review_candidates=int(row.get("review_candidate_count") or 0),
            missing_companies=int(row.get("missing_company_count") or 0),
            vendor_only=mode == "vendor_only",
        )
        rows.append({
            "crawl_run_id": row.get("crawl_run_id"),
            "sponsor_website_id": row.get("sponsor_website_id"),
            "sponsor": row.get("sponsor"),
            "crawl_mode": mode,
            "source_completeness": row.get("source_completeness", "full_portfolio"),
            "run_started_at": run_started_at,
            "run_completed_at": run_completed_at,
            "status": quality.status.value,
            "attempt_count": 1,
            "snapshot_count": int(row.get("snapshot_count") or 0),
            "candidate_count": int(row.get("holding_candidate_count") or 0),
            "accepted_candidate_count": int(row.get("accepted_candidate_count") or 0),
            "non_company_candidate_count": int(row.get("non_company_candidate_count") or 0),
            "review_candidate_count": int(row.get("review_candidate_count") or 0),
            "discovered_company_count": int(row.get("discovered_company_count") or 0),
            "extracted_company_count": int(row.get("extracted_company_count") or 0),
            "missing_company_count": int(row.get("missing_company_count") or 0),
            "missing_company_names_json": row.get("missing_company_names_json", "[]"),
            "holding_count": holdings,
            "blocked_snapshot_count": blocked,
            "error_snapshot_count": errors,
            "page_limit_snapshot_count": limited,
            "llm_failure_count": llm_failures,
            "last_error": "; ".join(quality.problems) or None,
            "retry_eligible": not quality.publishable,
        })
    out = pd.DataFrame(rows, columns=RUN_SPONSOR_CONTROL_COLUMNS)
    out.attrs["delta_schema_types"] = website_crawl_contract_dataset()[
        "control_website_crawl_run_sponsor"
    ].attrs["delta_schema_types"]
    return out
