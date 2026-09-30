import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from apo_config.config import config
from common.utils.sqlutil import execsql, insertdf_batch, readdfsql
from data_access.dbtables.sponsor_study import (
    DEFAULT_DELTA_BLOB_CONTAINER,
    DEFAULT_DELTA_BLOB_DIR,
    DEFAULT_SCHEMA,
    SPONSOR_STUDY_APP,
)
from data_access.dbtables.sponsor_study.external_prod_contracts import (
    EXTERNAL_PROD_CATALOG,
    EXTERNAL_PROD_PHYSICAL_TABLE_NAMES,
    EXTERNAL_REFRESH_RUN_COLUMNS,
    external_prod_physical_table_name,
    external_prod_table_contracts,
)
from data_access.dbtables.sponsor_study.metadata_baseline import (
    gold_view_select_scripts,
    preflight_alter_view_permissions,
)
from data_access.dbtables.sponsor_study.query import (
    DEFAULT_TABLE_PREFIX,
    EXTERNAL_BENCHMARK_DENYLIST,
    EXTERNAL_PROD_TABLES,
    OUTPUT_NAMESPACE,
    OUTPUT_VIEW_NAMES,
    benchmark_delta_view_scripts,
    delta_view_scripts,
    external_delta_view_scripts,
    gold_external_view_scripts,
    gold_website_crawl_view_scripts,
    pe_sponsor_holding_identity_resolved_view_sql,
    prefixed_name,
    website_crawl_view_scripts,
)


DEFAULT_EXTERNAL_DELTA_SCHEMA = DEFAULT_SCHEMA
DEFAULT_EXTERNAL_DELTA_BLOB_DIR = DEFAULT_DELTA_BLOB_DIR
EXTERNAL_REFRESH_CONTROL_TABLE = "meta_external_refresh_run"
EXTERNAL_REFRESH_CONTROL_COLUMNS = EXTERNAL_REFRESH_RUN_COLUMNS
EXTERNAL_BULK_WRITE_MIN_ROWS = 5_000
DEFAULT_DELTA_INSERT_EXCLUDE_TABLES = {
    "source_annual_2026_consolidated",
    "source_annual_2026_formula_map",
    "source_factset_pevc_fund_summary",
    "source_factset_pevc_portco_investment",
}
DEFAULT_BENCHMARK_DELTA_VIEW_OUTPUTS = set(benchmark_delta_view_scripts())
DEFAULT_EXTERNAL_DELTA_VIEW_OUTPUTS = set(external_delta_view_scripts())
DEFAULT_DELTA_VIEW_OUTPUTS = {
    "dim_opportunity_screen_rule",
    "fact_opportunity_screen",
    "validation_reconciliation",
    "output_external_fund_performance",
    "output_external_holdings_merged",
    *(f"{OUTPUT_NAMESPACE}{suffix}" for suffix in OUTPUT_VIEW_NAMES.values()),
    *DEFAULT_BENCHMARK_DELTA_VIEW_OUTPUTS,
    *DEFAULT_EXTERNAL_DELTA_VIEW_OUTPUTS,
}
LEGACY_DELTA_VIEW_OUTPUTS = set(OUTPUT_VIEW_NAMES)
def write_dataset_files(dataset: dict[str, pd.DataFrame], output_dir: str | Path, file_format: str = "csv") -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, df in dataset.items():
        if file_format == "parquet":
            path = output_path / f"{name}.parquet"
            df.to_parquet(path, index=False)
        elif file_format == "csv":
            path = output_path / f"{name}.csv"
            df.to_csv(path, index=False)
        else:
            raise ValueError(f"Unsupported file format: {file_format}")
        paths[name] = str(path)
        logging.info("Wrote %s rows=%s path=%s", name, len(df), path)
    manifest_path = output_path / "manifest.json"
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "file_format": file_format,
        "tables": {name: {"path": path, "rows": int(len(dataset[name]))} for name, path in paths.items()},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    paths["manifest"] = str(manifest_path)
    return paths


def read_dataset_files(input_dir: str | Path, file_format: str = "csv") -> dict[str, pd.DataFrame]:
    input_path = Path(input_dir)
    dataset = {}
    pattern = f"*.{file_format}"
    for path in input_path.glob(pattern):
        if path.name == "manifest.json":
            continue
        name = path.stem
        if file_format == "parquet":
            dataset[name] = pd.read_parquet(path)
        elif file_format == "csv":
            dataset[name] = pd.read_csv(path)
        else:
            raise ValueError(f"Unsupported file format: {file_format}")
    return dataset


def create_table_scripts(
    dataset: dict[str, pd.DataFrame],
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    scripts = {}
    for name, df in dataset.items():
        target_name = prefixed_name(name, table_prefix)
        if df.empty:
            scripts[name] = f"-- {schema}.{target_name}: empty dataframe; create manually when schema is populated"
            continue
        column_defs = []
        for col, dtype in df.dtypes.items():
            sql_type = _sql_type_for_dataframe_column(df, str(col), str(dtype))
            column_defs.append(f"    {col} {sql_type}")
        scripts[name] = f"CREATE TABLE IF NOT EXISTS {schema}.{target_name} (\n" + ",\n".join(column_defs) + "\n);"
    return scripts


def delta_defaults_for_env(env: str) -> dict[str, str]:
    key = str(env or "dev").strip().lower()
    configured_environment = str(config.getenv() or key).strip().lower()
    if configured_environment != key:
        raise ValueError(
            f"Sponsor-study Delta configuration is initialized for {configured_environment!r}, "
            f"not {key!r}"
        )

    def configured(*apps: str, key_name: str) -> str:
        for app in apps:
            value = config.getconfig(app, key_name)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise RuntimeError(
            f"Missing sponsor-study Delta configuration: {SPONSOR_STUDY_APP}.{key_name}"
        )

    storage_account = configured(
        SPONSOR_STUDY_APP,
        "apo_data_access.spark",
        "",
        key_name="blob_storage_name",
    ).split(".", 1)[0]
    return {
        "catalog": configured(SPONSOR_STUDY_APP, key_name="databricks_catalog"),
        "storage_account": f"{storage_account}.dfs.core.windows.net",
        "blob_container": configured(
            SPONSOR_STUDY_APP,
            "apo_data_access.spark",
            "",
            key_name="blob_container_name",
        ).split(".", 1)[0],
        "blob_dir": config.getconfig(SPONSOR_STUDY_APP, "delta_blob_dir")
        or config.getconfig("apo_data_access.spark", "azuredb_remote_dir")
        or DEFAULT_DELTA_BLOB_DIR,
    }


def _qualified_name(catalog: str, schema: str, table_name: str, table_prefix: str = DEFAULT_TABLE_PREFIX) -> str:
    catalog = str(catalog or "").strip()
    schema = str(schema or DEFAULT_SCHEMA).strip() or DEFAULT_SCHEMA
    logical_name = str(table_name or "").strip()
    physical_name = external_prod_physical_table_name(
        logical_name,
        catalog=catalog,
    )
    table = prefixed_name(physical_name, table_prefix)
    return f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"


def _delta_location(
    table_name: str,
    *,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> str:
    table_leaf = prefixed_name(str(table_name or "").split(".")[-1], table_prefix)
    return f"abfss://{blob_container}@{storage_account}/{blob_dir.strip('/')}/{table_leaf}"


def _column_defs_for_delta(df: pd.DataFrame) -> list[str]:
    return [
        f"    `{col}` {_sql_type_for_dataframe_column(df, str(col), str(dtype))}"
        for col, dtype in df.dtypes.items()
    ]


def _existing_delta_columns(qualified: str, *, app: str = "databricks") -> set[str]:
    desc = readdfsql(f"DESCRIBE TABLE {qualified}", app=app)
    if desc.empty or "col_name" not in desc.columns:
        return set()
    col_names = desc["col_name"].dropna().astype(str).str.strip()
    return {col for col in col_names if col and not col.startswith("#")}


def _existing_delta_schema_types(
    qualified: str,
    *,
    app: str = "databricks",
) -> dict[str, str]:
    desc = readdfsql(f"DESCRIBE TABLE {qualified}", app=app)
    if desc.empty or not {"col_name", "data_type"}.issubset(desc.columns):
        return {}
    valid = desc["col_name"].notna() & ~desc["col_name"].astype(str).str.startswith(
        "#"
    )
    return dict(
        zip(
            desc.loc[valid, "col_name"].astype(str),
            desc.loc[valid, "data_type"].astype(str),
        )
    )


def _add_missing_delta_columns(qualified: str, df: pd.DataFrame, *, app: str = "databricks") -> None:
    existing = _existing_delta_columns(qualified, app=app)
    missing_defs = [
        f"    `{col}` {_sql_type_for_dataframe_column(df, str(col), str(dtype))}"
        for col, dtype in df.dtypes.items()
        if str(col) not in existing
    ]
    if not missing_defs:
        return
    execsql(
        f"ALTER TABLE {qualified} ADD COLUMNS (\n" + ",\n".join(missing_defs) + "\n)",
        app=app,
        msg=f"add missing sponsor_study Delta columns {qualified}",
    )


def _escape_delta_insert_strings(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.select_dtypes(include=["object", "string"]).columns:
        out[col] = out[col].map(lambda value: value.replace("\\", "/").replace("'", "''") if isinstance(value, str) else value)
    return out


def _delta_insert_batch_size(name: str, df: pd.DataFrame) -> int:
    if name == "source_factset_pevc_portco_investment":
        return 250
    if len(df.columns) > 50:
        return 100
    text_cols = df.select_dtypes(include=["object", "string"]).columns
    if len(text_cols) and df[text_cols].stack().dropna().astype(str).str.len().max() > 1000:
        return 100
    return 1000


def create_delta_table_scripts(
    dataset: dict[str, pd.DataFrame],
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    exclude_tables: set[str] | None = None,
) -> dict[str, str]:
    excluded = (
        DEFAULT_DELTA_INSERT_EXCLUDE_TABLES
        if exclude_tables is None
        else set(exclude_tables)
    )
    scripts = {}
    schema_name = f"{catalog}.{schema}" if catalog else schema
    schema_sql = f"CREATE SCHEMA IF NOT EXISTS {schema_name};"
    for name, df in dataset.items():
        if name in DEFAULT_DELTA_VIEW_OUTPUTS or name in excluded:
            continue
        qualified = _qualified_name(catalog, schema, name, table_prefix)
        location = _delta_location(
            name,
            storage_account=storage_account,
            blob_container=blob_container,
            blob_dir=blob_dir,
            table_prefix=table_prefix,
        )
        column_defs = _column_defs_for_delta(df)
        if not column_defs:
            scripts[name] = f"-- {qualified}: empty dataframe with no columns; cannot infer Delta schema"
            continue
        scripts[name] = (
            f"{schema_sql}\n\n"
            f"CREATE TABLE IF NOT EXISTS {qualified} (\n"
            + ",\n".join(column_defs)
            + "\n)\n"
            "USING DELTA\n"
            f"LOCATION '{location}';\n"
        )
    return scripts


def _uses_string_delta_type(column: str) -> bool:
    col = str(column or "").strip().lower()
    if not col:
        return False
    if col.endswith("_id"):
        return True
    if col.endswith("_table") or col.endswith("_slug"):
        return True
    return col in {
        "source_entity_id",
        "source_fund_id",
        "source_sponsor_id",
        "source_system",
        "instrument_id_uniq",
        "factset_entity_id",
        "findox_issuer_id",
        "findox_common_id",
        "ciq_identifier",
        "loanx_id",
        "lxid",
        "bbg_company_id",
        "dealogic_borrower_id",
        "first_lien_debt_identifier",
        "second_lien_unsecured_debt_identifier",
        "feature_value",
        "preqin_asset_class",
        "preqin_fund_source_table",
        "source_entity_table",
        "source_fund_source_table",
        "review_status",
        "match_method",
        "alias_type",
        "evidence_source",
        "preferred_metric_source_system",
        "allowed_group_names",
        "blocked_group_names",
        "restriction_reason",
        "restriction_source",
    }


def _sql_type_for_dataframe_column(df: pd.DataFrame, column: str, dtype: str) -> str:
    schema_types = getattr(df, "attrs", {}).get("delta_schema_types", {})
    override = schema_types.get(column) if isinstance(schema_types, dict) else None
    if override:
        return str(override).upper()
    return _sql_type_for_series(df[column], dtype, column=column)


def _sql_type_for_series(series: pd.Series, dtype: str, column: str = "") -> str:
    if _uses_string_delta_type(column):
        return "STRING"
    if "datetime" in dtype:
        return "TIMESTAMP"
    if dtype.startswith("int"):
        return "BIGINT"
    if dtype.startswith("float"):
        return "DOUBLE"
    if dtype in {"bool", "boolean"}:
        return "BOOLEAN"
    return "STRING"


def write_table_scripts(
    dataset: dict[str, pd.DataFrame],
    output_dir: str | Path,
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, script in create_table_scripts(dataset, schema=schema, table_prefix=table_prefix).items():
        path = output_path / f"{prefixed_name(name, table_prefix)}.sql"
        path.write_text(str(script), encoding="utf-8")
        paths[name] = str(path)
    return paths


def write_delta_table_scripts(
    dataset: dict[str, pd.DataFrame],
    output_dir: str | Path,
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    scripts = create_delta_table_scripts(
        dataset,
        catalog=catalog,
        schema=schema,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_dir=blob_dir,
        table_prefix=table_prefix,
    )
    for name, script in scripts.items():
        path = output_path / f"{prefixed_name(name, table_prefix)}.sql"
        path.write_text(str(script), encoding="utf-8")
        paths[name] = str(path)
    return paths


def write_delta_view_scripts(
    output_dir: str | Path,
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, script in delta_view_scripts(catalog=catalog, schema=schema, table_prefix=table_prefix).items():
        path = output_path / f"{prefixed_name(name, table_prefix)}.sql"
        path.write_text(str(script), encoding="utf-8")
        paths[name] = str(path)
    return paths


def ensure_delta_tables(
    dataset: dict[str, pd.DataFrame],
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    cleantable: bool = False,
    exclude_tables: set[str] | None = None,
) -> None:
    scripts = create_delta_table_scripts(
        dataset,
        catalog=catalog,
        schema=schema,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_dir=blob_dir,
        table_prefix=table_prefix,
        exclude_tables=exclude_tables,
    )
    for name, script in scripts.items():
        if script.lstrip().startswith("--"):
            logging.info("Skipping Delta table with no inferred schema: %s", name)
            continue
        schema_name = f"{catalog}.{schema}" if catalog else schema
        execsql(
            f"CREATE SCHEMA IF NOT EXISTS {schema_name}",
            app=app,
            msg=f"ensure sponsor_study Delta schema {schema_name}",
        )
        qualified = _qualified_name(catalog, schema, name, table_prefix)
        if cleantable:
            execsql(f"DROP TABLE IF EXISTS {qualified}", app=app, msg=f"drop sponsor_study Delta table {name} for schema refresh")
        table_sql = script.split("\n\n", 1)[1].rstrip(";\n")
        try:
            execsql(table_sql, app=app, msg=f"ensure sponsor_study Delta table {name}")
            _add_missing_delta_columns(qualified, dataset[name], app=app)
        except Exception as exc:
            if "DELTA_CREATE_TABLE_SCHEME_MISMATCH" not in str(exc):
                raise
            location = _delta_location(
                name,
                storage_account=storage_account,
                blob_container=blob_container,
                blob_dir=blob_dir,
                table_prefix=table_prefix,
            )
            logging.info("Registering existing Delta location for %s before schema alignment.", qualified)
            execsql(
                f"CREATE TABLE IF NOT EXISTS {qualified} USING DELTA LOCATION '{location}'",
                app=app,
                msg=f"register existing sponsor_study Delta table {name}",
            )
            _add_missing_delta_columns(qualified, dataset[name], app=app)


def _drop_delta_table_or_view(qualified: str, name: str, *, app: str = "databricks") -> None:
    try:
        execsql(f"DROP TABLE IF EXISTS {qualified}", app=app, msg=f"drop old sponsor_study Delta table {name}")
    except Exception as exc:
        logging.info("Could not drop %s as table before view creation: %s", qualified, exc)
    try:
        execsql(f"DROP VIEW IF EXISTS {qualified}", app=app, msg=f"drop old sponsor_study Delta view {name}")
    except Exception as exc:
        logging.info("Could not drop %s as view before view creation: %s", qualified, exc)


def ensure_delta_views(
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    for name in LEGACY_DELTA_VIEW_OUTPUTS:
        qualified = _qualified_name(catalog, schema, name, table_prefix)
        _drop_delta_table_or_view(qualified, name, app=app)
    for name, script in delta_view_scripts(catalog=catalog, schema=schema, table_prefix=table_prefix).items():
        qualified = _qualified_name(catalog, schema, name, table_prefix)
        _drop_delta_table_or_view(qualified, name, app=app)
        execsql(script.rstrip(";\n"), app=app, msg=f"ensure sponsor_study Delta view {name}")


def external_prod_table_rename_scripts(
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> dict[str, str]:
    scripts = {}
    for logical_name, physical_name in (
        EXTERNAL_PROD_PHYSICAL_TABLE_NAMES.items()
    ):
        if logical_name == physical_name:
            continue
        old_name = (
            f"{catalog}.{schema}.{prefixed_name(logical_name, table_prefix)}"
        )
        new_name = (
            f"{catalog}.{schema}.{prefixed_name(physical_name, table_prefix)}"
        )
        scripts[logical_name] = (
            f"ALTER TABLE {old_name} RENAME TO {new_name}"
        )
    return scripts


def migrate_external_prod_object_names(
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    current_dbo_views = {
        *external_delta_view_scripts(
            catalog=catalog,
            schema=schema,
            table_prefix=table_prefix,
        ),
        *website_crawl_view_scripts(
            catalog=catalog,
            schema=schema,
            table_prefix=table_prefix,
        ),
    }
    current_gold_views = {
        *gold_external_view_scripts(
            catalog=catalog,
            source_schema=schema,
            gold_schema=gold_schema,
            table_prefix=table_prefix,
        ),
        *gold_website_crawl_view_scripts(
            catalog=catalog,
            source_schema=schema,
            gold_schema=gold_schema,
            table_prefix=table_prefix,
        ),
    }
    for logical_name in current_dbo_views:
        qualified = (
            f"{catalog}.{schema}."
            f"{prefixed_name(logical_name, table_prefix)}"
        )
        execsql(
            f"DROP VIEW IF EXISTS {qualified}",
            app=app,
            msg=f"drop obsolete sponsor study dbo view {logical_name}",
        )
    for view_name in current_gold_views:
        execsql(
            f"DROP VIEW IF EXISTS {catalog}.{gold_schema}.{view_name}",
            app=app,
            msg=f"drop obsolete sponsor study gold view {view_name}",
        )

    existing = readdfsql(
        f"""
SELECT table_name
FROM {catalog}.information_schema.tables
WHERE table_schema = '{schema}'
""".strip(),
        app=app,
    )
    existing_names = set(existing["table_name"].astype(str))
    for logical_name, script in external_prod_table_rename_scripts(
        catalog=catalog,
        schema=schema,
        table_prefix=table_prefix,
    ).items():
        old_name = prefixed_name(logical_name, table_prefix)
        new_name = prefixed_name(
            EXTERNAL_PROD_PHYSICAL_TABLE_NAMES[logical_name],
            table_prefix,
        )
        old_exists = old_name in existing_names
        new_exists = new_name in existing_names
        if old_exists and new_exists:
            raise ValueError(
                f"Cannot rename {old_name}; target {new_name} already exists"
            )
        if not old_exists and new_exists:
            continue
        if not old_exists:
            raise ValueError(f"Expected production table is missing: {old_name}")
        execsql(
            script,
            app=app,
            msg=f"rename sponsor study table {old_name} to {new_name}",
        )
        existing_names.remove(old_name)
        existing_names.add(new_name)


def _view_exists(qualified: str, *, app: str = "databricks") -> bool:
    catalog, schema, view_name = qualified.split(".", 2)
    exists = readdfsql(
        f"""
SELECT table_name
FROM {catalog}.information_schema.views
WHERE table_schema = '{schema}'
  AND table_name = '{view_name}'
""".strip(),
        app=app,
    )
    return not exists.empty


def _deploy_view_definition(
    qualified: str,
    select_sql: str,
    *,
    app: str = "databricks",
    msg: str,
) -> None:
    if _view_exists(qualified, app=app):
        execsql(
            f"ALTER VIEW {qualified} AS {select_sql}",
            app=app,
            msg=msg,
        )
        return
    execsql(
        f"CREATE VIEW {qualified} AS {select_sql}",
        app=app,
        msg=msg,
    )


def ensure_external_delta_views(
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    preqin_catalog: str,
    preqin_schema: str = "PREQIN_SHARED_DATA",
    require_existing_views: bool = False,
) -> None:
    if require_existing_views:
        preflight_alter_view_permissions(
            catalog=catalog,
            gold_schema=gold_schema,
            app=app,
        )
    execsql(
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{gold_schema}",
        app=app,
        msg=f"ensure sponsor study gold schema {catalog}.{gold_schema}",
    )
    resolved_name = prefixed_name(
        "holding_identity_resolved",
        table_prefix,
    )
    resolved_script = pe_sponsor_holding_identity_resolved_view_sql(
        catalog=catalog,
        source_schema=schema,
        table_prefix=table_prefix,
    )
    _, resolved_body = resolved_script.split(" AS\n", 1)
    _deploy_view_definition(
        f"{catalog}.{schema}.{resolved_name}",
        resolved_body,
        app=app,
        msg="ensure sponsor study holding identity resolved view",
    )
    audit_script = pe_sponsor_holding_identity_resolved_view_sql(
        catalog=catalog, source_schema=schema, table_prefix=table_prefix, audit=True,
    )
    _deploy_view_definition(
        f"{catalog}.{schema}.{table_prefix}holding_dedup_audit",
        audit_script.split(" AS\n", 1)[1], app=app,
        msg="ensure sponsor study holding dedup audit view",
    )
    scripts = gold_view_select_scripts(
        catalog=catalog,
        source_schema=schema,
        gold_schema=gold_schema,
        table_prefix=table_prefix,
    )
    for name, select_sql in scripts.items():
        qualified = f"{catalog}.{gold_schema}.{name}"
        _deploy_view_definition(
            qualified,
            select_sql,
            app=app,
            msg=f"ensure sponsor study gold view {name}",
        )


def ensure_website_crawl_views(
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    gold_schema: str = "gold",
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    if str(catalog).strip().lower() == EXTERNAL_PROD_CATALOG:
        logging.info(
            "Production website crawl publishes through the four gold marts; "
            "no dbo website views are created."
        )
        return
    from data_access.dbtables.sponsor_study.query import (
        gold_website_crawl_view_scripts,
        website_crawl_view_scripts,
    )

    for name, script in website_crawl_view_scripts(
        catalog=catalog,
        schema=schema,
        table_prefix=table_prefix,
    ).items():
        qualified = _qualified_name(catalog, schema, name, table_prefix)
        execsql(
            f"DROP VIEW IF EXISTS {qualified}",
            app=app,
            msg=f"drop old sponsor website crawl view {name}",
        )
        execsql(script.rstrip(";\n"), app=app, msg=f"ensure sponsor website crawl view {name}")

    execsql(
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{gold_schema}",
        app=app,
        msg=f"ensure sponsor website crawl gold schema {catalog}.{gold_schema}",
    )
    for name, script in gold_website_crawl_view_scripts(
        catalog=catalog,
        source_schema=schema,
        gold_schema=gold_schema,
        table_prefix=table_prefix,
    ).items():
        qualified = f"{catalog}.{gold_schema}.{name}"
        execsql(
            f"DROP VIEW IF EXISTS {qualified}",
            app=app,
            msg=f"drop old sponsor website crawl gold view {name}",
        )
        execsql(script.rstrip(";\n"), app=app, msg=f"ensure sponsor website crawl gold view {name}")


def _external_refresh_control_contract() -> pd.DataFrame:
    return external_prod_table_contracts()[EXTERNAL_REFRESH_CONTROL_TABLE].copy()


def _validated_refresh_run_id(refresh_run_id: str) -> str:
    clean = str(refresh_run_id or "").strip()
    if not clean:
        raise ValueError("refresh_run_id is required for external sponsor study persistence")
    return clean


def _stamp_external_refresh_run_id(
    name: str,
    df: pd.DataFrame,
    *,
    refresh_run_id: str,
) -> pd.DataFrame:
    out = df.copy()
    out.attrs.update(getattr(df, "attrs", {}))
    if "refresh_run_id" in out.columns and not out.empty:
        values = set(out["refresh_run_id"].dropna().astype(str))
        has_null = out["refresh_run_id"].isna().any()
        if has_null or values != {refresh_run_id}:
            raise ValueError(
                f"{name} contains unexpected refresh_run_id values: "
                f"{sorted(values)}"
            )
    out["refresh_run_id"] = refresh_run_id
    if name != EXTERNAL_REFRESH_CONTROL_TABLE:
        if "ingested_at" not in out:
            out["ingested_at"] = datetime.now()
        else:
            out["ingested_at"] = out["ingested_at"].fillna(datetime.now())
    return out


def external_curation_dataset(
    dataset: dict[str, pd.DataFrame],
    *,
    refresh_run_id: str,
) -> dict[str, pd.DataFrame]:
    run_id = _validated_refresh_run_id(refresh_run_id)
    forbidden = sorted(set(dataset) & set(EXTERNAL_BENCHMARK_DENYLIST))
    if forbidden:
        logging.info(
            "Ignoring benchmark-owned tables during external deploy: %s",
            ", ".join(forbidden),
        )
    return {
        name: _stamp_external_refresh_run_id(
            name,
            dataset[name],
            refresh_run_id=run_id,
        )
        for name in sorted(
            (set(dataset) & set(EXTERNAL_PROD_TABLES))
            - {EXTERNAL_REFRESH_CONTROL_TABLE}
        )
    }


def _external_run_lineage(
    dataset: dict[str, pd.DataFrame],
) -> tuple[object, object]:
    cutoff = None
    crawl_run_id = None
    meta = dataset.get("meta_external_refresh_run", pd.DataFrame())
    if meta is not None and not meta.empty:
        if "source_cutoff_date" in meta.columns:
            value = meta["source_cutoff_date"].iloc[0]
            if pd.notna(value):
                cutoff = value
        if "website_crawl_run_id" in meta.columns:
            value = meta["website_crawl_run_id"].iloc[0]
            if pd.notna(value) and str(value).strip():
                crawl_run_id = str(value).strip()
    if cutoff is None:
        study = dataset.get("study_asof_date", pd.DataFrame())
        if (
            study is not None
            and not study.empty
            and "study_asof_date" in study.columns
        ):
            value = study["study_asof_date"].iloc[0]
            if pd.notna(value):
                cutoff = value
    return cutoff, crawl_run_id


def _external_run_config(
    dataset: dict[str, pd.DataFrame],
) -> dict[str, object]:
    meta = dataset.get("meta_external_refresh_run", pd.DataFrame())
    if (
        meta is None
        or meta.empty
        or "config_json" not in meta.columns
        or pd.isna(meta["config_json"].iloc[0])
    ):
        return {}
    value = json.loads(str(meta["config_json"].iloc[0]))
    if not isinstance(value, dict):
        raise ValueError("External refresh config_json must contain an object")
    return value


def _external_run_control_frame(
    *,
    refresh_run_id: str,
    run_started_at: datetime,
    status: str,
    environment: str,
    catalog: str,
    schema: str,
    dataset: dict[str, pd.DataFrame],
    run_completed_at: datetime | None = None,
    error_message: str | None = None,
    source_cutoff_date: object = None,
    website_crawl_run_id: object = None,
    source_config: dict[str, object] | None = None,
) -> pd.DataFrame:
    row_count = sum(len(frame) for frame in dataset.values())
    config = dict(source_config or {})
    config.update({
        "tables": sorted(dataset),
        "row_count": row_count,
    })
    validation_status = {
        "running": "pending",
        "succeeded": "passed",
        "failed": "failed",
    }[status]
    out = pd.DataFrame(
        [
            {
                "refresh_run_id": refresh_run_id,
                "run_started_at": run_started_at,
                "run_completed_at": run_completed_at,
                "status": status,
                "environment": environment,
                "catalog": catalog,
                "schema": schema,
                "code_version": "",
                "source_cutoff_date": source_cutoff_date,
                "website_crawl_run_id": website_crawl_run_id,
                "config_json": json.dumps(config, default=str),
                "validation_status": validation_status,
                "published_at": run_completed_at if status == "succeeded" else None,
                "supersedes_refresh_run_id": None,
                "error_message": error_message,
            }
        ],
        columns=EXTERNAL_REFRESH_CONTROL_COLUMNS,
    )
    out.attrs.update(_external_refresh_control_contract().attrs)
    return out


def _replace_external_refresh_rows(
    name: str,
    df: pd.DataFrame,
    *,
    refresh_run_id: str,
    catalog: str,
    schema: str,
    app: str,
    table_prefix: str,
) -> None:
    stamped = _stamp_external_refresh_run_id(
        name,
        df,
        refresh_run_id=refresh_run_id,
    )
    qualified = _qualified_name(catalog, schema, name, table_prefix)
    run_id_literal = "'" + refresh_run_id.replace("'", "''") + "'"
    execsql(
        f"DELETE FROM {qualified} WHERE refresh_run_id = {run_id_literal}",
        app=app,
        msg=f"clear external sponsor study refresh rows {qualified}",
    )
    if stamped.empty:
        logging.info("No rows to persist for %s refresh=%s", name, refresh_run_id)
        return
    insert_df = _escape_delta_insert_strings(stamped)
    insertdf_batch(
        qualified,
        insert_df,
        app=app,
        cleantable=False,
        msg=f"external sponsor study refresh {name}",
        maxrow_batch=_delta_insert_batch_size(name, insert_df),
    )


def _bulk_replace_external_refresh_rows(
    name: str,
    df: pd.DataFrame,
    *,
    refresh_run_id: str,
    catalog: str,
    schema: str,
    storage_account: str,
    blob_container: str,
    blob_dir: str,
    app: str,
    table_prefix: str,
) -> None:
    from data_access.spark.azureblob import BlobDownloader

    stamped = _stamp_external_refresh_run_id(
        name,
        df,
        refresh_run_id=refresh_run_id,
    )
    qualified = _qualified_name(catalog, schema, name, table_prefix)
    run_id_literal = "'" + refresh_run_id.replace("'", "''") + "'"
    execsql(
        f"DELETE FROM {qualified} WHERE refresh_run_id = {run_id_literal}",
        app=app,
        msg=f"clear external sponsor study refresh rows {qualified}",
    )
    if stamped.empty:
        return

    target_account = storage_account.split(".", 1)[0]
    uploader = BlobDownloader(container=blob_container)
    if uploader.container_client.account_name != target_account:
        raise ValueError(
            "Bulk staging account does not match external Delta target: "
            f"{uploader.container_client.account_name} != {target_account}"
        )
    blob_name = (
        f"{blob_dir.strip('/')}/_staging/external_refresh/"
        f"{refresh_run_id}/{prefixed_name(name, table_prefix)}.parquet"
    )
    uploader.upload_parquet_dataframe(
        _escape_delta_insert_strings(stamped),
        blob_name,
    )
    source = (
        f"abfss://{blob_container}@{target_account}.dfs.core.windows.net/"
        f"{blob_name}"
    )
    contract = external_prod_table_contracts()[name]
    schema_types = _existing_delta_schema_types(qualified, app=app)
    contract_types = contract.attrs.get("delta_schema_types", {})
    select_list = ",\n    ".join(
        f"CAST(`{column}` AS "
        f"{schema_types.get(column, contract_types.get(column, 'STRING'))}) "
        f"AS `{column}`"
        for column in contract.columns
    )
    execsql(
        f"""
COPY INTO {qualified}
FROM (
    SELECT
    {select_list}
    FROM '{source}'
)
FILEFORMAT = PARQUET
COPY_OPTIONS ('force' = 'true')
""".strip(),
        app=app,
        msg=f"bulk external sponsor study refresh {name}",
    )


def persist_external_dataset_to_delta(
    dataset: dict[str, pd.DataFrame],
    *,
    refresh_run_id: str,
    environment: str,
    catalog: str,
    schema: str = DEFAULT_EXTERNAL_DELTA_SCHEMA,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_EXTERNAL_DELTA_BLOB_DIR,
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
    cleantable: bool = False,
) -> None:
    if "_diagnostic_scope" in dataset or _external_run_config(dataset).get("diagnostic_sponsor"):
        raise ValueError("Sponsor-scoped diagnostic datasets cannot be published")
    run_id = _validated_refresh_run_id(refresh_run_id)
    env = str(environment or "").strip().lower()
    if env == "prod" and cleantable:
        raise ValueError("--clean-tables is prohibited for external production deploys")

    source_cutoff_date, website_crawl_run_id = _external_run_lineage(dataset)
    source_config = _external_run_config(dataset)
    curation = external_curation_dataset(dataset, refresh_run_id=run_id)
    control_contract = _external_refresh_control_contract()
    table_contracts = {
        **curation,
        EXTERNAL_REFRESH_CONTROL_TABLE: control_contract,
    }
    ensure_delta_tables(
        table_contracts,
        catalog=catalog,
        schema=schema,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_dir=blob_dir,
        app=app,
        table_prefix=table_prefix,
        cleantable=bool(cleantable),
        exclude_tables=set(),
    )

    run_started_at = datetime.now()
    started = _external_run_control_frame(
        refresh_run_id=run_id,
        run_started_at=run_started_at,
        status="running",
        environment=env,
        catalog=catalog,
        schema=schema,
        dataset=curation,
        source_cutoff_date=source_cutoff_date,
        website_crawl_run_id=website_crawl_run_id,
        source_config=source_config,
    )
    _replace_external_refresh_rows(
        EXTERNAL_REFRESH_CONTROL_TABLE,
        started,
        refresh_run_id=run_id,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    try:
        for name, frame in curation.items():
            if len(frame) >= EXTERNAL_BULK_WRITE_MIN_ROWS:
                _bulk_replace_external_refresh_rows(
                    name,
                    frame,
                    refresh_run_id=run_id,
                    catalog=catalog,
                    schema=schema,
                    storage_account=storage_account,
                    blob_container=blob_container,
                    blob_dir=blob_dir,
                    app=app,
                    table_prefix=table_prefix,
                )
            else:
                _replace_external_refresh_rows(
                    name,
                    frame,
                    refresh_run_id=run_id,
                    catalog=catalog,
                    schema=schema,
                    app=app,
                    table_prefix=table_prefix,
                )
    except Exception as exc:
        failed = _external_run_control_frame(
            refresh_run_id=run_id,
            run_started_at=run_started_at,
            run_completed_at=datetime.now(),
            status="failed",
            environment=env,
            catalog=catalog,
            schema=schema,
            dataset=curation,
            error_message=str(exc),
            source_cutoff_date=source_cutoff_date,
            website_crawl_run_id=website_crawl_run_id,
            source_config=source_config,
        )
        _replace_external_refresh_rows(
            EXTERNAL_REFRESH_CONTROL_TABLE,
            failed,
            refresh_run_id=run_id,
            catalog=catalog,
            schema=schema,
            app=app,
            table_prefix=table_prefix,
        )
        raise

    succeeded = _external_run_control_frame(
        refresh_run_id=run_id,
        run_started_at=run_started_at,
        run_completed_at=datetime.now(),
        status="succeeded",
        environment=env,
        catalog=catalog,
        schema=schema,
        dataset=curation,
        source_cutoff_date=source_cutoff_date,
        website_crawl_run_id=website_crawl_run_id,
        source_config=source_config,
    )
    _replace_external_refresh_rows(
        EXTERNAL_REFRESH_CONTROL_TABLE,
        succeeded,
        refresh_run_id=run_id,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )


def persist_external_dataset_files_to_delta(
    input_dir: str | Path,
    *,
    refresh_run_id: str,
    environment: str,
    catalog: str,
    storage_account: str,
    schema: str = DEFAULT_EXTERNAL_DELTA_SCHEMA,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_EXTERNAL_DELTA_BLOB_DIR,
    app: str = "databricks",
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    """Resume a validated external publish from local run-scoped CSV outputs."""
    run_id = _validated_refresh_run_id(refresh_run_id)
    source_dir = Path(input_dir)
    contracts = external_prod_table_contracts()
    validation = pd.read_csv(
        source_dir / "validation_external_enrichment.csv",
        low_memory=False,
    )
    gates = validation[
        validation["scope"].astype(str).eq("release_gate")
    ]
    if gates.empty or not gates["status"].astype(str).eq("pass").all():
        blocked = gates.loc[
            ~gates["status"].astype(str).eq("pass"), "metric"
        ].astype(str)
        raise ValueError(
            "External file publish release gates blocked: "
            + ", ".join(blocked)
        )

    ensure_delta_tables(
        contracts,
        catalog=catalog,
        schema=schema,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_dir=blob_dir,
        app=app,
        table_prefix=table_prefix,
        cleantable=False,
        exclude_tables=set(),
    )
    table_names = sorted(set(contracts) - {EXTERNAL_REFRESH_CONTROL_TABLE})
    run_started_at = datetime.now()
    started = _external_run_control_frame(
        refresh_run_id=run_id,
        run_started_at=run_started_at,
        status="running",
        environment=environment,
        catalog=catalog,
        schema=schema,
        dataset={name: contracts[name] for name in table_names},
    )
    _replace_external_refresh_rows(
        EXTERNAL_REFRESH_CONTROL_TABLE,
        started,
        refresh_run_id=run_id,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )
    try:
        for name in table_names:
            path = source_dir / f"{name}.csv"
            if not path.exists():
                raise ValueError(f"External publish input is missing {path}")
            frame = pd.read_csv(path, low_memory=False)
            frame = frame.reindex(columns=contracts[name].columns)
            frame.attrs.update(contracts[name].attrs)
            _bulk_replace_external_refresh_rows(
                name,
                frame,
                refresh_run_id=run_id,
                catalog=catalog,
                schema=schema,
                storage_account=storage_account,
                blob_container=blob_container,
                blob_dir=blob_dir,
                app=app,
                table_prefix=table_prefix,
            )
            del frame
    except Exception as exc:
        failed = _external_run_control_frame(
            refresh_run_id=run_id,
            run_started_at=run_started_at,
            run_completed_at=datetime.now(),
            status="failed",
            environment=environment,
            catalog=catalog,
            schema=schema,
            dataset={},
            error_message=str(exc),
        )
        _replace_external_refresh_rows(
            EXTERNAL_REFRESH_CONTROL_TABLE,
            failed,
            refresh_run_id=run_id,
            catalog=catalog,
            schema=schema,
            app=app,
            table_prefix=table_prefix,
        )
        raise

    succeeded = _external_run_control_frame(
        refresh_run_id=run_id,
        run_started_at=run_started_at,
        run_completed_at=datetime.now(),
        status="succeeded",
        environment=environment,
        catalog=catalog,
        schema=schema,
        dataset={name: contracts[name] for name in table_names},
    )
    _replace_external_refresh_rows(
        EXTERNAL_REFRESH_CONTROL_TABLE,
        succeeded,
        refresh_run_id=run_id,
        catalog=catalog,
        schema=schema,
        app=app,
        table_prefix=table_prefix,
    )


def persist_dataset_to_delta(
    dataset: dict[str, pd.DataFrame],
    *,
    catalog: str,
    schema: str = DEFAULT_SCHEMA,
    storage_account: str,
    blob_container: str = DEFAULT_DELTA_BLOB_CONTAINER,
    blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    app: str = "databricks",
    cleantable: bool = False,
    exclude_tables: set[str] | None = None,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    excluded = set(DEFAULT_DELTA_INSERT_EXCLUDE_TABLES if exclude_tables is None else exclude_tables)
    excluded.update(DEFAULT_DELTA_VIEW_OUTPUTS)
    ensure_delta_tables(
        dataset,
        catalog=catalog,
        schema=schema,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_dir=blob_dir,
        app=app,
        table_prefix=table_prefix,
        cleantable=cleantable,
    )
    for name, df in dataset.items():
        if name in excluded:
            logging.info("Skipping Delta insert for excluded/view-only output %s.", name)
            continue
        if df.empty:
            logging.info("Skipping empty Delta insert %s.%s.%s", catalog, schema, name)
            continue
        insert_df = _escape_delta_insert_strings(df)
        insertdf_batch(
            _qualified_name(catalog, schema, name, table_prefix),
            insert_df,
            app=app,
            cleantable=cleantable,
            msg=f"sponsor study Delta {name}",
            maxrow_batch=_delta_insert_batch_size(name, insert_df),
        )
    ensure_delta_views(catalog=catalog, schema=schema, app=app, table_prefix=table_prefix)


def persist_dataset_to_sql(
    dataset: dict[str, pd.DataFrame],
    schema: str = DEFAULT_SCHEMA,
    app: str = SPONSOR_STUDY_APP,
    cleantable: bool = False,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> None:
    for name, df in dataset.items():
        if df.empty:
            logging.info("Skipping empty table %s.%s", schema, name)
            continue
        target_name = prefixed_name(name, table_prefix)
        insertdf_batch(f"{schema}.{target_name}", df, app=app, cleantable=cleantable, msg=f"sponsor study {target_name}")
