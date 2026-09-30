import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from data_access.dbtables import SNOWFLAKE_CATALOG_APPS
from dash_reports.data_catalog.data import catalog_reader
from dash_reports.data_catalog.data.broker_research import broker_research_assets
from dash_reports.data_catalog.data.categorization import (
    categorize,
    category_description,
    describe_asset,
    provider_for,
)
from dash_reports.data_catalog.data.market_data_tiers import (
    marketplace_universe,
    parse_badges,
    parse_governance_assets,
)
from dash_reports.data_catalog.data.models import ASSET_COLUMNS


log = logging.getLogger(__name__)


def _empty(columns):
    return pd.DataFrame(columns=columns)


def _databricks_columns():
    return catalog_reader._read_databricks(
        f"""
        WITH tags AS (
            SELECT catalog_name, schema_name, table_name, column_name,
                   CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(CONCAT(tag_name, '=', tag_value)))) AS tags
            FROM system.information_schema.column_tags
            WHERE {catalog_reader.PROD_FILTER.replace("table_catalog", "catalog_name")}
            GROUP BY catalog_name, schema_name, table_name, column_name
        )
        SELECT c.table_catalog AS catalog, c.table_schema AS schema, c.table_name AS name,
               c.ordinal_position, c.column_name, c.data_type, c.is_nullable,
               COALESCE(c.comment, '') AS comment, COALESCE(t.tags, '') AS tags
        FROM system.information_schema.columns c
        LEFT JOIN tags t
          ON t.catalog_name = c.table_catalog AND t.schema_name = c.table_schema
         AND t.table_name = c.table_name AND t.column_name = c.column_name
        WHERE {catalog_reader.PROD_FILTER.replace("table_catalog", "c.table_catalog")}
          AND LOWER(c.table_schema) <> 'information_schema'
        """
    )


def _snowflake_columns(app):
    database = catalog_reader._identifier(catalog_reader.config.getconfig(app, "database_name"))
    return catalog_reader._read_snowflake(
        app,
        f"""
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               ordinal_position, column_name, data_type, is_nullable,
               COALESCE(comment, '') AS comment, '' AS tags
        FROM {database}.information_schema.columns
        WHERE table_schema <> 'INFORMATION_SCHEMA'
        """,
    )


def _databricks_table_lineage():
    return catalog_reader._read_databricks(
        """
        SELECT source_table_full_name AS source, target_table_full_name AS target,
               COUNT(*) AS events, MAX(event_time) AS last_seen,
               MAX(CASE WHEN direct_access THEN 1 ELSE 0 END) AS direct_access
        FROM system.access.table_lineage
        WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
        GROUP BY source_table_full_name, target_table_full_name
        """
    )


def _databricks_column_lineage():
    return catalog_reader._read_databricks(
        """
        SELECT source_table_full_name AS source_table, source_column_name AS source_column,
               target_table_full_name AS target_table, target_column_name AS target_column,
               COUNT(*) AS events, MAX(event_time) AS last_seen
        FROM system.access.column_lineage
        WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
        GROUP BY source_table_full_name, source_column_name,
                 target_table_full_name, target_column_name
        LIMIT 250000
        """
    )


def _databricks_usage():
    return catalog_reader._read_databricks(
        """
        WITH refs AS (
            SELECT source_table_full_name AS full_name, statement_id, event_time
            FROM system.access.table_lineage
            WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
            UNION ALL
            SELECT target_table_full_name AS full_name, statement_id, event_time
            FROM system.access.table_lineage
            WHERE event_date >= DATEADD(day, -90, CURRENT_DATE())
        )
        SELECT full_name, COUNT(DISTINCT r.statement_id) AS queries_90d,
               COUNT(DISTINCT CASE WHEN r.event_time >= DATEADD(day, -30, CURRENT_TIMESTAMP())
                                   THEN r.statement_id END) AS queries_30d,
               MAX(r.event_time) AS last_accessed,
               ROUND(AVG(q.total_duration_ms), 0) AS avg_duration_ms,
               SUM(COALESCE(q.read_bytes, 0)) AS read_bytes,
               COUNT(DISTINCT q.client_application) AS client_applications
        FROM refs r
        LEFT JOIN system.query.history q ON q.statement_id = r.statement_id
        WHERE full_name IS NOT NULL
        GROUP BY full_name
        """
    )


def _databricks_access():
    return catalog_reader._read_databricks(
        f"""
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               grantee, privilege_type, is_grantable, inherited_from,
               grantee = current_user() OR is_account_group_member(grantee) AS effective_grantee
        FROM system.information_schema.table_privileges
        WHERE {catalog_reader.PROD_FILTER}
        """
    )


def _snowflake_access(app):
    database = catalog_reader._identifier(catalog_reader.config.getconfig(app, "database_name"))
    return catalog_reader._read_snowflake(
        app,
        f"""
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               grantee, privilege_type, is_grantable, grantor
        FROM {database}.information_schema.table_privileges
        WHERE table_schema <> 'INFORMATION_SCHEMA'
        """,
    )


def _quality_profile():
    return catalog_reader._read_databricks(
        """
        SELECT window, column_name, count, percent_null, distinct_count
        FROM apollo_market_data_prod.quality.ice_index_all_profile_metrics
        ORDER BY window DESC
        LIMIT 100
        """
    )


def _quality_drift():
    return catalog_reader._read_databricks(
        """
        SELECT window, column_name, drift_type, percent_null_delta,
               js_distance, population_stability_index
        FROM apollo_market_data_prod.quality.ice_index_all_drift_metrics
        ORDER BY window DESC
        LIMIT 100
        """
    )


def _asset_lookup(assets, source=None, connection=None):
    frame = assets
    if source:
        frame = frame[frame["source"] == source]
    if connection:
        frame = frame[frame["connection"] == connection]
    return {
        str(row.full_name).lower(): row.asset_id
        for row in frame[["asset_id", "full_name"]].itertuples(index=False)
    }


def _attach_by_full_name(frame, assets, source, connection=None):
    if frame.empty:
        return frame
    result = frame.copy()
    result["full_name"] = (
        result["catalog"].astype(str) + "." + result["schema"].astype(str) + "." + result["name"].astype(str)
    )
    lookup = _asset_lookup(assets, source, connection)
    result["asset_id"] = result["full_name"].str.lower().map(lookup)
    return result.dropna(subset=["asset_id"]).drop(columns=["full_name"])


def _attach_edges(frame, assets, source_column, target_column):
    columns = ["asset_id", *frame.columns] if not frame.empty else ["asset_id"]
    if frame.empty:
        return _empty(columns)
    lookup = _asset_lookup(assets, "Databricks")
    rows = []
    for row in frame.to_dict("records"):
        asset_ids = {
            lookup.get(str(row.get(source_column, "")).lower()),
            lookup.get(str(row.get(target_column, "")).lower()),
        }
        for asset_id in asset_ids - {None}:
            rows.append({"asset_id": asset_id, **row})
    return pd.DataFrame(rows, columns=columns)


def _safe_future(future, name, columns):
    try:
        return future.result()
    except Exception as exc:
        log.warning("Data Catalog snapshot %s unavailable: %s", name, exc)
        return _empty(columns)


def _read_details():
    jobs = {
        "databricks_columns": (_databricks_columns, ["catalog", "schema", "name"]),
        "table_lineage": (_databricks_table_lineage, ["source", "target"]),
        "column_lineage": (_databricks_column_lineage, ["source_table", "target_table"]),
        "usage": (_databricks_usage, ["full_name"]),
        "databricks_access": (_databricks_access, ["catalog", "schema", "name"]),
        "quality_profile": (_quality_profile, []),
        "quality_drift": (_quality_drift, []),
    }
    for app in SNOWFLAKE_CATALOG_APPS:
        jobs[f"columns:{app}"] = (lambda app=app: _snowflake_columns(app), ["catalog", "schema", "name"])
        jobs[f"access:{app}"] = (lambda app=app: _snowflake_access(app), ["catalog", "schema", "name"])
    results = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(function): (name, columns)
            for name, (function, columns) in jobs.items()
        }
        for future in as_completed(futures):
            name, columns = futures[future]
            results[name] = _safe_future(future, name, columns)
    return results


def enrich_asset_descriptions(assets, columns):
    result = assets.copy()
    records = result.to_dict("records")
    result["data_category"] = [
        categorize(row["name"], row["schema"], row["catalog"])
        for row in records
    ]
    result["domain"] = result["data_category"]
    result["vendor"] = [
        provider_for(
            row["catalog"],
            row["schema"],
            row["name"],
            row.get("comment", ""),
            row.get("tags", ""),
            connection=row.get("connection", ""),
        )
        for row in records
    ]
    result["provider"] = result["vendor"]
    fields = {}
    if not columns.empty:
        ordered = columns[["asset_id", "ordinal_position", "column_name"]].copy()
        ordered["ordinal_position"] = pd.to_numeric(ordered["ordinal_position"], errors="coerce")
        ordered = ordered.sort_values(["asset_id", "ordinal_position"])
        fields = ordered.groupby("asset_id")["column_name"].apply(list).to_dict()
    result["category_description"] = result["data_category"].map(category_description)
    result["example_fields"] = result["asset_id"].map(
        lambda asset_id: ", ".join(map(str, fields.get(asset_id, [])[:8]))
    )
    result["business_description"] = [
        describe_asset(record, fields.get(record["asset_id"], []))
        for record in result.to_dict("records")
    ]
    return result


def enrich_asset_ranking(assets, usage, access):
    result = assets.copy()
    usage_summary = usage.copy()
    for column in ("queries_30d", "queries_90d"):
        if column not in usage_summary:
            usage_summary[column] = 0
        usage_summary[column] = pd.to_numeric(usage_summary[column], errors="coerce").fillna(0)
    if "last_accessed" not in usage_summary:
        usage_summary["last_accessed"] = ""
    if usage_summary.empty:
        query_30d = {}
        query_90d = {}
        last_accessed = {}
    else:
        usage_summary = usage_summary.groupby("asset_id", as_index=False).agg(
            queries_30d=("queries_30d", "max"),
            queries_90d=("queries_90d", "max"),
            last_accessed=("last_accessed", "max"),
        )
        query_30d = usage_summary.set_index("asset_id")["queries_30d"].to_dict()
        query_90d = usage_summary.set_index("asset_id")["queries_90d"].to_dict()
        last_accessed = usage_summary.set_index("asset_id")["last_accessed"].to_dict()
    result["queries_30d"] = result["asset_id"].map(query_30d).fillna(0).astype(int)
    result["queries_90d"] = result["asset_id"].map(query_90d).fillna(0).astype(int)
    accessed = pd.to_datetime(result["asset_id"].map(last_accessed), errors="coerce", utc=True)
    result["last_accessed"] = accessed.dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")

    if access.empty or "effective_grantee" not in access:
        selectable = set()
    else:
        effective = access["effective_grantee"].astype(str).str.lower().isin({"true", "1"})
        select_grant = access["privilege_type"].astype(str).str.upper().isin(
            {"SELECT", "ALL PRIVILEGES"}
        )
        selectable = set(access.loc[effective & select_grant, "asset_id"].astype(str))
    databricks = result["source"].astype(str).str.lower().eq("databricks")
    result["service_can_select"] = databricks & result["asset_id"].astype(str).isin(selectable)
    observed_usage = result["queries_90d"] > 0
    result["ranking_tier"] = np.select(
        [result["service_can_select"] & observed_usage, result["service_can_select"]],
        [0, 1],
        default=2,
    ).astype(int)

    recent = np.maximum(result["queries_90d"] - result["queries_30d"], 0)
    usage_score = 4 * np.log1p(result["queries_30d"]) + np.log1p(recent)
    altered = pd.to_datetime(result["last_altered"], errors="coerce", utc=True)
    created = pd.to_datetime(result["created"], errors="coerce", utc=True)
    age_days = (pd.Timestamp.now(tz="UTC") - altered.fillna(created)).dt.days.clip(0, 365)
    recency = (365 - age_days.fillna(365)) / 365 * 9
    fallback = (
        result["certified"].fillna(False).astype(bool).astype(int) * 40
        + result["documented"].fillna(False).astype(bool).astype(int) * 20
        + result["asset_kind"].eq("Ready-to-use view").astype(int) * 10
        + recency
    )
    result["ranking_score"] = (usage_score * 100 + fallback).round(6)
    return result


def _normalize_timestamps(assets):
    for column in ("created", "last_altered"):
        assets[column] = pd.to_datetime(assets[column], errors="coerce", utc=True).dt.tz_localize(None)
    return assets


def _append_broker_research(assets):
    research = _safe_future_call(broker_research_assets, "broker research")
    if research.empty:
        return _normalize_timestamps(assets)
    catalog_reader._SOURCE_STATUS["Broker Research"] = (
        f"{research['vendor'].nunique():,} providers · {int(research['row_count'].sum()):,} documents"
    )
    return _normalize_timestamps(pd.concat([assets, research], ignore_index=True))


def _safe_future_call(function, name):
    try:
        return function()
    except Exception as exc:
        log.warning("Data Catalog %s unavailable: %s", name, exc)
        return _empty(ASSET_COLUMNS)


def _merge_tag(existing, tag):
    tags = [item.strip() for item in str(existing or "").split(",") if item.strip()]
    if tag not in tags:
        tags.append(tag)
    return ", ".join(tags)


FEDERATION_HINT = "snowflake"
FEDERATED_SOURCE = "Snowflake (Federated)"


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _db_matches_catalog(database, catalog):
    db, cat = _norm(database), _norm(catalog)
    if db.endswith("db"):
        db = db[:-2]
    return bool(db) and bool(cat) and (db in cat or cat in db)


def _badges_by_schema_table(tiers):
    index = {}
    for (database, schema, table), badge in tiers.items():
        index.setdefault((schema, table), []).append((database, badge))
    return index


def _federation_map(catalogs):
    metadata = catalog_reader.federation_snowflake_catalogs(catalogs)
    heuristic = {name.lower(): "" for name in catalogs if FEDERATION_HINT in str(name).lower()}
    fallback = {name: database for name, database in heuristic.items() if name not in metadata}
    if metadata and fallback:
        return {**fallback, **metadata}, "catalog metadata + name fallback"
    if metadata:
        return metadata, "catalog metadata"
    return fallback, "name heuristic"


def _federated_badge(tiers, by_schema_table, mirrored_db, catalog, schema, name):
    if mirrored_db:
        return tiers.get((mirrored_db, schema, name))
    for database, badge in by_schema_table.get((schema, name), ()):
        if _db_matches_catalog(database, catalog):
            return badge
    return None


def _governance_record(records, by_schema_table, mirrored_db, catalog, schema, name):
    if mirrored_db:
        return records.get((mirrored_db, schema, name))
    matches = [
        (database, record)
        for database, record in by_schema_table.get((schema, name), ())
        if _db_matches_catalog(database, catalog)
    ]
    return matches[0][1] if len(matches) == 1 else None


def _apply_governance_metadata(result, governance, fed_map):
    for column in ("comment", "business_description", "owner", "vendor", "provider"):
        if column not in result:
            result[column] = pd.Series("", index=result.index, dtype="object")
    if "documented" not in result:
        result["documented"] = False
    records = {}
    by_schema_table = {}
    for record in governance.to_dict("records"):
        key = (
            str(record["catalog"]).lower(),
            str(record["schema"]).lower(),
            str(record["name"]).lower(),
        )
        records[key] = record
        by_schema_table.setdefault(key[1:], []).append((key[0], record))
    matched = 0
    for index in result.index:
        source = str(result.at[index, "source"]).lower()
        catalog = str(result.at[index, "catalog"]).lower()
        if source == "snowflake":
            mirrored = catalog
        elif source == "databricks" and catalog in fed_map:
            mirrored = fed_map[catalog]
        else:
            continue
        schema = str(result.at[index, "schema"]).lower()
        name = str(result.at[index, "name"]).lower()
        record = _governance_record(records, by_schema_table, mirrored, catalog, schema, name)
        if not record:
            continue
        matched += 1
        for column in ("comment", "business_description", "owner", "vendor", "provider"):
            if record.get(column):
                result.at[index, column] = record[column]
        if record.get("documented"):
            result.at[index, "documented"] = True
    return result, matched


def _append_snowflake_tiers(assets, fed_map, fed_mode):
    universe = marketplace_universe()
    tiers = parse_badges(universe)
    result = assets.copy()
    source = result["source"].astype(str).str.lower()
    catalog = result["catalog"].astype(str).str.lower()
    schema = result["schema"].astype(str).str.lower()
    name = result["name"].astype(str).str.lower()

    snowflake = source.eq("snowflake")
    native_badges = [tiers.get(key) for key in zip(catalog, schema, name)]
    tagged = 0
    for index, badge, is_snowflake in zip(result.index, native_badges, snowflake):
        if is_snowflake and badge:
            result.at[index, "tags"] = _merge_tag(result.at[index, "tags"], f"tier={badge.lower()}")
            tagged += 1

    databricks = source.eq("databricks")
    by_schema_table = _badges_by_schema_table(tiers)
    federated_tagged = 0
    federated = databricks & catalog.isin(fed_map)
    for index in result.index[databricks]:
        cat = catalog.at[index]
        if cat not in fed_map:
            continue
        badge = _federated_badge(tiers, by_schema_table, fed_map[cat], cat, schema.at[index], name.at[index])
        if badge:
            result.at[index, "tags"] = _merge_tag(result.at[index, "tags"], f"tier={badge.lower()}")
            federated_tagged += 1

    governance_all = pd.DataFrame(parse_governance_assets(universe), columns=ASSET_COLUMNS)
    result, governance_matched = _apply_governance_metadata(result, governance_all, fed_map)
    existing = set(zip(catalog[snowflake], schema[snowflake], name[snowflake]))
    existing.update(
        (fed_map[catalog.at[index]], schema.at[index], name.at[index])
        for index in result.index[federated]
        if fed_map[catalog.at[index]]
    )
    governance = governance_all
    if not governance.empty:
        keys = list(zip(
            governance["catalog"].astype(str).str.lower(),
            governance["schema"].astype(str).str.lower(),
            governance["name"].astype(str).str.lower(),
        ))
        governance = governance[[key not in existing for key in keys]]
        result = pd.concat([result, governance], ignore_index=True)
    catalog_reader._SOURCE_STATUS["Snowflake tiers"] = (
        f"{tagged:,} native badged · {federated_tagged:,}/{int(federated.sum()):,} "
        f"federated badged ({len(fed_map):,} catalogs, {fed_mode}) · "
        f"{governance_matched:,} Delphi assets enriched · {len(governance):,} governance assets added"
    )
    return result


def _relabel_federated_source(assets, fed_map):
    if not fed_map or assets.empty:
        return assets
    result = assets.copy()
    federated = result["source"].astype(str).str.lower().eq("databricks") & result[
        "catalog"
    ].astype(str).str.lower().isin(set(fed_map))
    result.loc[federated, "source"] = FEDERATED_SOURCE
    return result


def build_snapshot_frames():
    catalog_reader._assets_cached.cache_clear()
    assets = catalog_reader._assets_cached(catalog_reader._bucket())
    details = _read_details()

    column_frames = [
        _attach_by_full_name(details["databricks_columns"], assets, "Databricks", "databricks")
    ]
    access_frames = [
        _attach_by_full_name(details["databricks_access"], assets, "Databricks", "databricks")
    ]
    for app in SNOWFLAKE_CATALOG_APPS:
        column_frames.append(_attach_by_full_name(details[f"columns:{app}"], assets, "Snowflake", app))
        access_frames.append(_attach_by_full_name(details[f"access:{app}"], assets, "Snowflake", app))

    usage = details["usage"].copy()
    if not usage.empty:
        usage["asset_id"] = usage["full_name"].str.lower().map(_asset_lookup(assets, "Databricks"))
        usage = usage.dropna(subset=["asset_id"]).drop(columns=["full_name"])

    quality_assets = assets[
        (assets["source"] == "Databricks") & (assets["name"].str.lower() == "ice_index_all")
    ]["asset_id"].tolist()
    profiles = [
        frame.assign(asset_id=asset_id)
        for asset_id in quality_assets
        for frame in [details["quality_profile"]]
        if not frame.empty
    ]
    drifts = [
        frame.assign(asset_id=asset_id)
        for asset_id in quality_assets
        for frame in [details["quality_drift"]]
        if not frame.empty
    ]

    columns = pd.concat(column_frames, ignore_index=True) if column_frames else pd.DataFrame()
    access = pd.concat(access_frames, ignore_index=True) if access_frames else pd.DataFrame()
    databricks_catalogs = assets.loc[
        assets["source"].astype(str).str.lower().eq("databricks"), "catalog"
    ].astype(str).unique()
    fed_map, fed_mode = _federation_map(databricks_catalogs)
    assets = _append_snowflake_tiers(assets, fed_map, fed_mode)
    assets = enrich_asset_descriptions(assets, columns)
    assets = enrich_asset_ranking(assets, usage, access)
    assets = _append_broker_research(assets)
    frames = {
        "assets": _relabel_federated_source(assets, fed_map),
        "columns": columns,
        "table_lineage": _attach_edges(details["table_lineage"], assets, "source", "target"),
        "column_lineage": _attach_edges(
            details["column_lineage"], assets, "source_table", "target_table"
        ),
        "usage": usage,
        "access": access,
        "quality_profile": pd.concat(profiles, ignore_index=True) if profiles else pd.DataFrame(),
        "quality_drift": pd.concat(drifts, ignore_index=True) if drifts else pd.DataFrame(),
    }
    return frames, {"source_status": dict(catalog_reader._SOURCE_STATUS)}
