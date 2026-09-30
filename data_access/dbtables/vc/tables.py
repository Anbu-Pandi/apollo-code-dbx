"""Create / verify / read / write the ``vc_*`` EXTERNAL Delta tables.

House convention (identical to ``data_access.dbtables.sponsor_study.persistence``):

    CREATE TABLE IF NOT EXISTS <catalog>.dbo.vc_deals (<cols>)
    USING DELTA
    LOCATION 'abfss://marketanalytics@<storage_account>/tables/dbo/vc_deals';

Env split comes from ``sponsor_study.persistence.delta_defaults_for_env`` (reused, DRY):
dev -> apollo_market_data_dev @ due2systradingpocrgsa; prod -> apollo_market_data_prod @
pue2azdatafactoryrgsa. DDL runs via ``execsql(app='databricks')``.

Run once (per env) to create the tables:
    python -m data_access.dbtables.vc.tables --env dev
"""

import logging
from datetime import datetime

import pandas as pd

from common.utils.sqlutil import execsql, insertdf_batch, readdfsql
from data_access.dbtables.sponsor_study.persistence import delta_defaults_for_env
from data_access.dbtables.vc import ALL_TABLES, DEFAULT_SCHEMA, VC_APP
from data_access.dbtables.vc.contracts import all_contracts


def _sql_type(df: pd.DataFrame, col: str) -> str:
    override = df.attrs.get("delta_schema_types", {}).get(col)
    if override:
        return str(override).upper()
    dtype = str(df.dtypes[col])
    if "datetime" in dtype:
        return "TIMESTAMP"
    if dtype.startswith("int"):
        return "BIGINT"
    if dtype.startswith("float"):
        return "DOUBLE"
    if dtype in {"bool", "boolean"}:
        return "BOOLEAN"
    return "STRING"


def _column_defs(df: pd.DataFrame) -> list:
    return [f"    `{c}` {_sql_type(df, c)}" for c in df.columns]


def location(table: str, defaults: dict) -> str:
    sa = defaults["storage_account"]
    container = defaults["blob_container"]
    blob_dir = defaults["blob_dir"].strip("/")
    return f"abfss://{container}@{sa}/{blob_dir}/{table}"


def qualified(table: str, defaults: dict, schema: str = DEFAULT_SCHEMA) -> str:
    catalog = defaults["catalog"]
    return f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"


def create_table_sql(table: str, df: pd.DataFrame, defaults: dict,
                     schema: str = DEFAULT_SCHEMA) -> str:
    qn = qualified(table, defaults, schema)
    loc = location(table, defaults)
    cols = ",\n".join(_column_defs(df))
    return (
        f"CREATE TABLE IF NOT EXISTS {qn} (\n{cols}\n)\n"
        f"USING DELTA\nLOCATION '{loc}'"
    )


def ensure_vc_tables(env: str = None, app: str = VC_APP,
                     schema: str = DEFAULT_SCHEMA) -> None:
    """Create every ``vc_*`` external Delta table if it does not exist."""
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    catalog = defaults["catalog"]
    execsql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}", app=app,
            msg=f"ensure vc schema {catalog}.{schema}")
    contracts = all_contracts()
    for table in ALL_TABLES:
        sql = create_table_sql(table, contracts[table], defaults, schema)
        execsql(sql, app=app, msg=f"ensure vc table {table}")
        if table == "vc_deals":
            existing = readdfsql(f"SHOW COLUMNS IN {qualified(table, defaults, schema)}", app=app)
            missing = contracts[table].columns.difference(existing.iloc[:, 0])
            if len(missing):
                columns = ", ".join(_column_defs(contracts[table][missing]))
                execsql(f"ALTER TABLE {qualified(table, defaults, schema)} ADD COLUMNS ({columns})",
                        app=app, msg="VC deal detail columns")
        logging.info("ensured %s at %s", qualified(table, defaults, schema),
                     location(table, defaults))


def verify_external(table: str, env: str = None, app: str = VC_APP,
                    schema: str = DEFAULT_SCHEMA) -> bool:
    """Confirm a table is EXTERNAL + DELTA at the expected location."""
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    catalog = defaults["catalog"]
    rows = readdfsql(
        "SELECT table_type, data_source_format, storage_path "
        f"FROM {catalog}.information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}'",
        app=app,
    )
    if rows is None or rows.empty:
        return False
    row = rows.iloc[0]
    return (
        str(row["table_type"]).upper() == "EXTERNAL"
        and str(row["data_source_format"]).upper() == "DELTA"
        and str(row["storage_path"]).rstrip("/") == location(table, defaults)
    )


# ── read / write helpers ────────────────────────────────────────────────────

def read_table_databricks(table: str, where: str = None, cols: str = "*",
                          env: str = None, app: str = VC_APP,
                          schema: str = DEFAULT_SCHEMA) -> pd.DataFrame:
    """Read straight from the Databricks external Delta table (source of truth)."""
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    qn = qualified(table, defaults, schema)
    sql = f"SELECT {cols} FROM {qn}"
    if where:
        sql += f" WHERE {where}"
    return readdfsql(sql, app=app)


def read_table(table: str, where: str = None, cols: str = "*",
               env: str = None, app: str = VC_APP,
               schema: str = DEFAULT_SCHEMA) -> pd.DataFrame:
    """Cache-aware read. Full-table reads (no where, all cols, default app) are served
    from the per-host local replica when present; filtered/CRUD reads and any override
    go straight to Databricks. Falls back to Databricks on any cache miss."""
    from apo_config.config import config
    env = env or config.getenv()
    if where is None and cols == "*" and app == VC_APP:
        try:
            from data_access.dbtables.vc import local_cache
            cached = local_cache.read(table, env=env)
            if cached is not None:
                return cached
        except Exception:
            logging.debug("vc local_cache unavailable; using databricks", exc_info=True)
    return read_table_databricks(table, where=where, cols=cols, env=env, app=app,
                                 schema=schema)


def append_rows(table: str, df: pd.DataFrame, env: str = None, app: str = VC_APP,
                schema: str = DEFAULT_SCHEMA) -> None:
    """Insert rows (Delta append), escaping strings the sponsor_study way."""
    if df is None or df.empty:
        return
    from apo_config.config import config
    from data_access.dbtables.sponsor_study.persistence import _escape_delta_insert_strings
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    qn = qualified(table, defaults, schema)
    insertdf_batch(qn, _escape_delta_insert_strings(df), app=app, cleantable=False,
                   msg=f"vc append {table}", maxrow_batch=1000)


def replace_run_rows(table: str, df: pd.DataFrame, refresh_run_id: str,
                     env: str = None, app: str = VC_APP,
                     schema: str = DEFAULT_SCHEMA) -> None:
    """Idempotent per-run replace: DELETE this run's rows then append."""
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    qn = qualified(table, defaults, schema)
    rid = "'" + str(refresh_run_id).replace("'", "''") + "'"
    execsql(f"DELETE FROM {qn} WHERE refresh_run_id = {rid}", app=app,
            msg=f"clear vc run rows {table}")
    append_rows(table, df, env=env, app=app, schema=schema)


def upsert_by_key(table: str, df: pd.DataFrame, key: str, env: str = None,
                  app: str = VC_APP, schema: str = DEFAULT_SCHEMA,
                  preserve_existing: bool = False, only_newer: bool = False) -> None:
    if df is None or df.empty:
        return
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    qn = qualified(table, defaults, schema)
    from common.utils.dbinsertutil import enlist
    if df[key].isna().any() or df[key].astype(str).str.strip().eq("").any() or df[key].duplicated().any():
        raise ValueError(f"{table} requires unique, nonblank {key}")
    columns = ", ".join(f"`{c}`" for c in df.columns)
    updates = []
    for column in df.columns:
        if column == key:
            continue
        value = f"s.`{column}`"
        if preserve_existing and column not in {"ingested_at", "refresh_run_id"}:
            if df[column].dtype == object:
                value = f"CASE WHEN {value} IN ('', 'Unknown', 'Unclassified', 'Undisclosed') THEN NULL ELSE {value} END"
            value = f"COALESCE({value}, t.`{column}`)"
            if column == "is_apollo_relevant":
                value = f"COALESCE(s.`{column}`, false) OR COALESCE(t.`{column}`, false)"
        updates.append(f"t.`{column}` = {value}")
    rows = df.astype(object).where(pd.notna(df), None).values.tolist()
    condition = " AND (t.ingested_at IS NULL OR s.ingested_at >= t.ingested_at)" if only_newer else ""
    for batch in enlist(rows, 100, is_databricks_app=True):
        execsql(f"MERGE INTO {qn} t USING (SELECT * FROM ({batch}) AS incoming ({columns})) s ON t.`{key}` = s.`{key}` "
                f"WHEN MATCHED{condition} THEN UPDATE SET {', '.join(updates)} "
                f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({', '.join('s.`'+c+'`' for c in df.columns)})",
                app=app, msg=f"vc merge {table}")


def delete_by_key(table: str, key: str, value: str, env: str = None,
                  app: str = VC_APP, schema: str = DEFAULT_SCHEMA) -> None:
    from apo_config.config import config
    env = env or config.getenv()
    defaults = delta_defaults_for_env(env)
    qn = qualified(table, defaults, schema)
    v = "'" + str(value).replace("'", "''") + "'"
    execsql(f"DELETE FROM {qn} WHERE {key} = {v}", app=app,
            msg=f"vc delete {table}")


def write_refresh_run(job: str, refresh_run_id: str, status: str, env: str,
                      row_count: int = 0, config_json: str = "{}",
                      error_message: str = None,
                      run_started_at: datetime = None,
                      run_completed_at: datetime = None,
                      app: str = VC_APP, schema: str = DEFAULT_SCHEMA) -> None:
    from apo_config.config import config as _cfg
    from data_access.dbtables.vc import VC_META_REFRESH_RUN
    defaults = delta_defaults_for_env(env or _cfg.getenv())
    frame = pd.DataFrame([{
        "refresh_run_id": refresh_run_id,
        "job": job,
        "run_started_at": run_started_at or datetime.now(),
        "run_completed_at": run_completed_at,
        "status": status,
        "environment": env,
        "catalog": defaults["catalog"],
        "schema": schema,
        "row_count": int(row_count),
        "config_json": config_json,
        "error_message": error_message,
    }])
    replace_run_rows(VC_META_REFRESH_RUN, frame, refresh_run_id, env=env,
                     app=app, schema=schema)


def get_parser():
    from common.utils.envsetup import get_base_parser
    parser = get_base_parser("vc_ensure_tables")
    parser.add_argument("--verify", action="store_true")
    return parser


def main():
    from common.utils.envsetup import init_app_using_parser
    args = init_app_using_parser(get_parser())
    ensure_vc_tables(env=args.env)
    if args.verify:
        for table in ALL_TABLES:
            ok = verify_external(table, env=args.env)
            logging.info("verify %s external+delta=%s", table, ok)


if __name__ == "__main__":
    main()
