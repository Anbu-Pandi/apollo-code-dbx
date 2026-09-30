import logging
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import pandas as pd

from apo_config.config import config
from common.utils.sqlutil import readdfsql, sql_escape_literal
from data_access.data_catalog import ensure_snapshot, get_status, read_snapshot, request_refresh
from data_access.dbtables import SNOWFLAKE_CATALOG_APPS
from dash_reports.data_catalog.data.categorization import (
    asset_kind,
    categorize,
    category_description,
    provider_for,
    score_record,
)
from dash_reports.data_catalog.data.models import ASSET_COLUMNS, AssetKey, SourceCapabilities, asset_record


log = logging.getLogger(__name__)

CACHE_SECONDS = 900
PROD_FILTER = """
LOWER(table_catalog) RLIKE '(prod|prd)'
AND LOWER(table_catalog) NOT RLIKE '(dev|sandbox|sample|system)'
"""
_SOURCE_STATUS = {}


def _bucket():
    return int(time.time() // CACHE_SECONDS)


def _lower_columns(df):
    result = df.copy()
    result.columns = [str(column).lower() for column in result.columns]
    return result


def _clean(value, default=""):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return value


def _text(value):
    return str(_clean(value)).strip()


def _identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", str(value)):
        raise ValueError(f"Invalid database identifier: {value}")
    return str(value)


def _read_databricks(sql):
    return _read_sql("databricks", sql)


def _read_snowflake(app, sql):
    return _read_sql(app, sql)


def _read_sql(app, sql):
    return _lower_columns(
        readdfsql(sql, app=app, msg="Apollo Data Catalog", use_arrow=False)
    )


def _optional_result(future, columns, name):
    try:
        return future.result()
    except Exception as exc:
        log.warning("Data catalog %s enrichment unavailable: %s", name, exc)
        return pd.DataFrame(columns=columns)


SNOWFLAKE_CONNECTIONS_QUERY = (
    "SELECT LOWER(connection_name) AS name "
    "FROM system.information_schema.connections "
    "WHERE UPPER(connection_type) = 'SNOWFLAKE'"
)


def _snowflake_connection_names():
    try:
        df = _read_databricks(SNOWFLAKE_CONNECTIONS_QUERY)
    except Exception as exc:
        log.warning("Data catalog Snowflake connection metadata unavailable: %s", exc)
        return set()
    return {str(name).lower() for name in df.get("name", []) if str(name).strip()}


def _describe_catalog(catalog):
    safe = re.sub(r"[^A-Za-z0-9_$]", "", str(catalog))
    return _read_databricks(f"DESCRIBE CATALOG EXTENDED `{safe}`")


def _parse_catalog_metadata(rows, snowflake_connections):
    info = {}
    for row in rows.to_dict("records"):
        key = str(row.get("info_name") or "").strip().lower()
        if key:
            info[key] = str(row.get("info_value") or "").strip()
    connection = info.get("connection name") or info.get("connection") or ""
    values = {value.lower() for value in info.values() if value}
    is_snowflake = (
        info.get("connection type", "").upper() == "SNOWFLAKE"
        or connection.lower() in snowflake_connections
        or bool(values & snowflake_connections)
        or connection.lower().startswith("snowflake_")
    )
    is_foreign = "foreign" in info.get("catalog type", "").lower()
    if not (is_foreign and is_snowflake):
        return None
    for key in ("database", "remote database", "snowflake database", "source database"):
        if info.get(key):
            return info[key].lower()
    match = re.search(r"database\s*[=:]\s*['\"]?([A-Za-z0-9_$]+)", " ".join(info.values()), re.I)
    return match.group(1).lower() if match else ""


def federation_snowflake_catalogs(catalogs):
    candidates = sorted({str(name).strip() for name in catalogs if str(name).strip()})
    if not candidates:
        return {}
    connections = _snowflake_connection_names()
    result = {}
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
        futures = {executor.submit(_describe_catalog, name): name for name in candidates}
        for future in as_completed(futures):
            catalog = futures[future]
            try:
                rows = future.result()
            except Exception as exc:
                log.warning("Data catalog DESCRIBE CATALOG failed for %s: %s", catalog, exc)
                continue
            mirrored = _parse_catalog_metadata(rows, connections)
            if mirrored is not None:
                result[catalog.lower()] = mirrored
    return result


def _databricks_assets():
    queries = {
        "tables": f"""
            WITH storage AS (
                SELECT catalog_name, schema_name, table_name, active_bytes,
                       ROW_NUMBER() OVER (
                           PARTITION BY catalog_name, schema_name, table_name
                           ORDER BY snapshot_date DESC, record_generation_time DESC
                       ) AS rn
                FROM system.storage.table_metrics_history
            )
            SELECT t.table_catalog AS catalog, t.table_schema AS schema, t.table_name AS name,
                   t.table_type AS object_type, t.table_owner AS owner, t.comment,
                   t.created, t.last_altered, t.data_source_format AS data_format,
                   s.active_bytes AS bytes
            FROM system.information_schema.tables t
            LEFT JOIN storage s
              ON s.catalog_name = t.table_catalog
             AND s.schema_name = t.table_schema
             AND s.table_name = t.table_name
             AND s.rn = 1
            WHERE {PROD_FILTER}
              AND LOWER(t.table_schema) <> 'information_schema'
        """,
        "columns": """
            SELECT LOWER(table_name) AS join_key, COUNT(DISTINCT col_name) AS column_count,
                   CONCAT_WS(' ', COLLECT_LIST(CONCAT_WS(' ', col_name, COALESCE(comment, ''))))
                       AS column_search
            FROM apollo_market_data_prod.dbo.all_column_comments
            WHERE is_latest = true
            GROUP BY LOWER(table_name)
        """,
        "tags": """
            SELECT catalog_name AS catalog, schema_name AS schema, table_name AS name,
                   CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(CONCAT(tag_name, '=', tag_value)))) AS tags
            FROM system.information_schema.table_tags
            WHERE LOWER(catalog_name) RLIKE '(prod|prd)'
              AND LOWER(catalog_name) NOT RLIKE '(dev|sandbox|sample|system)'
            GROUP BY catalog_name, schema_name, table_name
        """,
        "lineage": """
            SELECT full_name, COUNT(*) AS lineage_events,
                   COUNT(DISTINCT statement_id) AS usage_events,
                   MAX(event_time) AS last_lineage_event
            FROM (
                SELECT source_table_full_name AS full_name, statement_id, event_time
                FROM system.access.table_lineage
                WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
                UNION ALL
                SELECT target_table_full_name AS full_name, statement_id, event_time
                FROM system.access.table_lineage
                WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
            )
            WHERE full_name IS NOT NULL
            GROUP BY full_name
        """,
    }
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        futures = {name: executor.submit(_read_databricks, sql) for name, sql in queries.items()}
        tables = futures["tables"].result()
        columns = _optional_result(
            futures["columns"],
            ["join_key", "column_count", "column_search"],
            "Databricks columns",
        )
        tags = _optional_result(
            futures["tags"],
            ["catalog", "schema", "name", "tags"],
            "Databricks tags",
        )
        lineage = _optional_result(
            futures["lineage"],
            ["full_name", "lineage_events", "usage_events", "last_lineage_event"],
            "Databricks lineage",
        )
    result = tables.copy()
    result["full_name"] = result["catalog"] + "." + result["schema"] + "." + result["name"]
    result["join_key"] = result["full_name"].str.lower()
    result = result.merge(columns, how="left", on="join_key").drop(columns=["join_key"])
    result = result.merge(tags, how="left", on=["catalog", "schema", "name"])
    result = result.merge(lineage, how="left", on="full_name")
    _SOURCE_STATUS["Databricks"] = f"{len(result):,} production objects"
    return result


def _snowflake_assets(app):
    database = _identifier(config.getconfig(app, "database_name"))
    sql = f"""
        SELECT t.table_catalog AS catalog, t.table_schema AS schema, t.table_name AS name,
               t.table_type AS object_type, t.table_owner AS owner, t.comment,
               t.created, t.last_altered, t.row_count, t.bytes
        FROM {database}.information_schema.tables t
        WHERE t.table_schema <> 'INFORMATION_SCHEMA'
    """
    return _read_snowflake(app, sql)


def _frame_to_records(df, source, connection, capabilities):
    records = []
    for row in df.to_dict("records"):
        key = AssetKey(
            source=source,
            connection=connection,
            catalog=_text(row.get("catalog")),
            schema=_text(row.get("schema")),
            name=_text(row.get("name")),
        )
        tags = _text(row.get("tags"))
        comment = _text(row.get("comment"))
        domain = categorize(key.name, key.schema, key.catalog)
        vendor = provider_for(
            key.catalog,
            key.schema,
            key.name,
            comment,
            tags,
            connection=key.connection,
        )
        object_type = _text(row.get("object_type")).replace("BASE TABLE", "TABLE")
        record = asset_record(
            key,
            capabilities,
            object_type=object_type,
            owner=_text(row.get("owner")),
            comment=comment,
            tags=tags,
            vendor=vendor,
            data_category=domain,
            category_description=category_description(domain),
            asset_kind=asset_kind(object_type),
            domain=domain,
            provider=vendor,
            created=_clean(row.get("created")),
            last_altered=_clean(row.get("last_altered")),
            data_format=_text(row.get("data_format")),
            row_count=_clean(row.get("row_count")),
            bytes=_clean(row.get("bytes")),
            column_count=int(_clean(row.get("column_count"), 0) or 0),
            column_search=_text(row.get("column_search")),
            documented=bool(comment),
            certified="certif" in tags.lower(),
            has_lineage=bool(_clean(row.get("lineage_events"), 0)),
            has_quality=key.name.lower() == "ice_index_all",
            has_usage=bool(_clean(row.get("usage_events"), 0)),
        )
        records.append(record)
    return records


@lru_cache(maxsize=2)
def _assets_cached(_time_bucket):
    records = []
    databricks_capabilities = SourceCapabilities(
        tags=True,
        lineage=True,
        quality=True,
        usage=True,
        access=True,
    )
    snowflake_capabilities = SourceCapabilities(columns=True, access=True)
    with ThreadPoolExecutor(max_workers=len(SNOWFLAKE_CATALOG_APPS) + 1) as executor:
        databricks_future = executor.submit(_databricks_assets)
        futures = {
            executor.submit(_snowflake_assets, app): app
            for app in SNOWFLAKE_CATALOG_APPS
        }
        try:
            databricks = databricks_future.result()
            records.extend(_frame_to_records(databricks, "Databricks", "databricks", databricks_capabilities))
        except Exception as exc:
            _SOURCE_STATUS["Databricks"] = f"Unavailable: {exc}"
            log.warning("Data catalog Databricks discovery failed: %s", exc)
        for future in as_completed(futures):
            app = futures[future]
            try:
                frame = future.result()
                records.extend(_frame_to_records(frame, "Snowflake", app, snowflake_capabilities))
                _SOURCE_STATUS[f"Snowflake · {app}"] = f"{len(frame):,} production objects"
            except Exception as exc:
                _SOURCE_STATUS[f"Snowflake · {app}"] = f"Unavailable: {exc}"
                log.warning("Data catalog Snowflake discovery failed for %s: %s", app, exc)

    frame = pd.DataFrame(records, columns=ASSET_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["source", "catalog", "schema", "name"]).reset_index(drop=True)


def _build_snapshot():
    from dash_reports.data_catalog.data.snapshot_builder import build_snapshot_frames

    return build_snapshot_frames()


def refresh_catalog():
    return request_refresh(_build_snapshot, reason="user")


def load_assets(refresh=False):
    status = ensure_snapshot(_build_snapshot)
    if refresh:
        refresh_catalog()
    snapshot_id = status.get("snapshot_id")
    if not snapshot_id:
        return pd.DataFrame(columns=ASSET_COLUMNS)
    frame = read_snapshot("assets", snapshot_id=snapshot_id)
    if frame.empty:
        return pd.DataFrame(columns=ASSET_COLUMNS)
    for column in ASSET_COLUMNS:
        if column not in frame:
            frame[column] = ""
    if "capabilities" in frame:
        frame["capabilities"] = frame["capabilities"].map(
            lambda value: json.loads(value) if isinstance(value, str) and value.startswith("{") else value
        )
    return frame


def clear_cache():
    return refresh_catalog()


def catalog_status():
    return get_status()


def source_status(status=None):
    status = status or catalog_status()
    value = status.get("source_status")
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def get_asset(asset_id):
    frame = load_assets()
    match = frame[frame["asset_id"] == asset_id]
    return None if match.empty else match.iloc[0].to_dict()


def _split_tags(value):
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _tag_conditions(value):
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {
        condition.strip().casefold()
        for item in values
        for condition in str(item or "").split(";")
        if condition.strip()
    }


def search_assets(query="", filters=None, limit=100, frame=None):
    frame = load_assets() if frame is None else frame.copy()
    if frame.empty:
        return frame
    filters = filters or {}
    for column in (
        "source",
        "catalog",
        "schema",
        "domain",
        "object_type",
        "vendor",
        "data_category",
        "asset_kind",
    ):
        value = filters.get(column)
        if value:
            values = value if isinstance(value, list) else [value]
            frame = frame[frame[column].isin(values)]
    tag_value = filters.get("tags")
    if tag_value:
        selected = _tag_conditions(tag_value)
        frame = frame[
            frame["tags"].apply(
                lambda value: bool(selected & {tag.casefold() for tag in _split_tags(value)})
            )
        ]
    for column in ("documented", "certified", "has_lineage", "has_quality", "has_usage"):
        if filters.get(column):
            frame = frame[frame[column].astype(bool)]
    tier = frame["ranking_tier"] if "ranking_tier" in frame else pd.Series(2, index=frame.index)
    score = frame["ranking_score"] if "ranking_score" in frame else pd.Series(0, index=frame.index)
    frame["_ranking_tier"] = pd.to_numeric(tier, errors="coerce").fillna(2)
    frame["_ranking_score"] = pd.to_numeric(score, errors="coerce").fillna(0)
    if query:
        frame["_score"] = [score_record(row, query) for row in frame.to_dict("records")]
        frame = frame[frame["_score"] > 0]
        sort_columns = ["_score", "_ranking_tier", "_ranking_score", "documented", "name", "asset_id"]
        ascending = [False, True, False, False, True, True]
    else:
        sort_columns = ["_ranking_tier", "_ranking_score", "documented", "name", "asset_id"]
        ascending = [True, False, False, True, True]
    frame = frame.sort_values(sort_columns, ascending=ascending)
    if limit is not None:
        frame = frame.head(limit)
    return frame.drop(
        columns=["_score", "_ranking_tier", "_ranking_score"],
        errors="ignore",
    ).reset_index(drop=True)


def get_facets(frame=None):
    frame = load_assets() if frame is None else frame
    columns = (
        "vendor",
        "data_category",
        "asset_kind",
        "source",
        "catalog",
        "schema",
        "domain",
        "object_type",
    )
    if frame.empty:
        return {**{column: [] for column in columns}, "tags": []}
    facets = {
        column: sorted(value for value in frame[column].dropna().astype(str).unique() if value)
        for column in columns
    }
    tags = set()
    for value in frame["tags"].dropna().astype(str):
        tags |= _split_tags(value)
    facets["tags"] = sorted(tags)
    return facets


def get_summary(frame=None):
    frame = load_assets() if frame is None else frame
    total = len(frame)
    return {
        "assets": total,
        "vendors": frame["vendor"].nunique() if total else 0,
        "categories": frame["data_category"].nunique() if total else 0,
        "source_tables": int((frame["asset_kind"] == "Source table").sum()) if total else 0,
        "views": int((frame["asset_kind"] == "Ready-to-use view").sum()) if total else 0,
        "documented": int(frame["documented"].sum()) if total else 0,
    }


def get_columns(asset_id):
    return read_snapshot(
        "columns",
        f"asset_id = '{sql_escape_literal(asset_id)}'",
    )


def get_lineage(asset_id):
    return read_snapshot(
        "table_lineage",
        f"asset_id = '{sql_escape_literal(asset_id)}'",
    )


def get_column_lineage(asset_id):
    return read_snapshot(
        "column_lineage",
        f"asset_id = '{sql_escape_literal(asset_id)}'",
    )


def get_usage(asset_id):
    return read_snapshot(
        "usage",
        f"asset_id = '{sql_escape_literal(asset_id)}'",
    )


def get_access(asset_id):
    return read_snapshot(
        "access",
        f"asset_id = '{sql_escape_literal(asset_id)}'",
    )


def get_quality(asset_id):
    asset = get_asset(asset_id)
    if not asset:
        return {}
    summary = pd.DataFrame(
        [
            ("Documentation", "Available" if asset["documented"] else "Missing"),
            ("Field metadata", f"{asset['column_count']:,} fields"),
            ("Last updated", _text(asset["last_altered"]) or "Unavailable"),
            ("Data health monitor", "Available" if asset["has_quality"] else "Not configured"),
        ],
        columns=["metric", "value"],
    )
    predicate = f"asset_id = '{sql_escape_literal(asset_id)}'"
    return {
        "summary": summary,
        "profile": read_snapshot("quality_profile", predicate),
        "drift": read_snapshot("quality_drift", predicate),
    }
