import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from apo_config.config import config
from common.utils import sqlutil
from data_access.dbtables.bdc import FACTSET_APP, BDC_APP, SPGLOBAL_APP
from data_access.spark.api import sparkmanager


DEFAULT_SCHEMA_NAME = "dbo"
DEFAULT_TABLE_NAME = "related_bbg_instruments"
FACTSET_DATABRICKS_CATALOG = "dbrx_factset_prod"
FINAL_COLS = [
    "instrument_id_bb",
    "linked_instrument_id_bb",
    "id_type",
    "security_des",
    "series",
    "mty_typ",
    "cpn",
    "id_bb8",
    "source_system",
    "mapping_provider",
    "bdp_status",
    "created_at",
    "updated_at",
    "last_seen_at",
    "is_active",
    "parent_security_id",
    "related_identifier_active_flag",
]


def _clean_bbg(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip().str.upper()
    return cleaned.where(~cleaned.isin(["", "NAN", "NONE", "NULL"]), np.nan)


def _normalize_sql_query(query: str) -> str:
    query = str(query or "").strip()
    if (query.startswith('"') and query.endswith('"')) or (query.startswith("'") and query.endswith("'")):
        query = query[1:-1]
    if "\\n" in query or "\\t" in query or "\\r" in query:
        query = query.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "")
    return query.strip()


def _chunked(seq, size):
    size = max(int(size or 0), 1)
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _sql_values_clause(values) -> str | None:
    cleaned = [
        str(v).replace("'", "''")
        for v in values
        if pd.notna(v) and str(v).strip()
    ]
    if not cleaned:
        return None
    return ", ".join(f"('{v}')" for v in cleaned)


def _validate_app_connection(app_name: str):
    driver = (config.getconfig(app_name, "driver") or "jdbc").lower()
    if driver.startswith("jdbc"):
        server = config.getconfig(app_name, "server")
        database_name = config.getconfig(app_name, "database_name")
        if not server or not database_name:
            raise RuntimeError(
                f"Invalid app config for app='{app_name}'. "
                f"driver resolves to '{driver}' but server/database_name is missing."
            )


def validate_required_apps(
    factset_app: str = FACTSET_APP,
    databricks_app: str = BDC_APP,
    spglobal_app: str = SPGLOBAL_APP,
):
    for app_name in [factset_app, databricks_app, spglobal_app]:
        _validate_app_connection(app_name)


def _build_candidate_sql(window_from_date: str):
    factset_ids_sql = f"""
WITH sym_bbg_one AS (
    SELECT fsym_id, bbg_id
    FROM {FACTSET_DATABRICKS_CATALOG}.SYM_V1.SYM_BBG
    QUALIFY ROW_NUMBER() OVER (PARTITION BY fsym_id ORDER BY bbg_id) = 1
),
sym_cov_one AS (
    SELECT fsym_id, fsym_security_id
    FROM {FACTSET_DATABRICKS_CATALOG}.SYM_V1.SYM_COVERAGE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY fsym_id ORDER BY fsym_security_id) = 1
),
dcs_latest AS (
    SELECT dd.instrument_id AS dcs_fsym_id, b1.bbg_id AS dcs_bbg_id
    FROM {FACTSET_DATABRICKS_CATALOG}.DCS_V2.DCS_DETAILS dd
    LEFT JOIN sym_bbg_one b1 ON b1.fsym_id = dd.instrument_id
    WHERE dd.active = TRUE
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dd.instrument_id
        ORDER BY dd.report_date DESC, dd.os_amount DESC NULLS LAST, dd.detail_id DESC
    ) = 1
),
latest_bdc_report_date AS (
    SELECT r.factset_entity_id, MAX(CAST(r.report_date AS DATE)) AS latest_report_date
    FROM {FACTSET_DATABRICKS_CATALOG}.BDC_V1.BDC_REPORTS r
    WHERE CAST(r.report_date AS DATE) >= '{window_from_date}'
    GROUP BY r.factset_entity_id
),
factset_base AS (
    SELECT h.fsym_id, COALESCE(sc.fsym_security_id, h.fsym_id) AS dcs_lookup_fsym_id
    FROM {FACTSET_DATABRICKS_CATALOG}.BDC_V1.BDC_HOLDINGS h
    JOIN {FACTSET_DATABRICKS_CATALOG}.BDC_V1.BDC_REPORTS r ON h.report_id = r.report_id
    JOIN latest_bdc_report_date lbr
      ON lbr.factset_entity_id = r.factset_entity_id
     AND CAST(r.report_date AS DATE) = lbr.latest_report_date
    LEFT JOIN sym_cov_one sc ON sc.fsym_id = h.fsym_id
    WHERE COALESCE(h.terminated_flag, FALSE) = FALSE
),
factset_bbg_union AS (
    SELECT b1.bbg_id AS raw_bbg_id
    FROM factset_base fb
    LEFT JOIN sym_bbg_one b1 ON b1.fsym_id = fb.fsym_id
    UNION ALL
    SELECT dcs.dcs_bbg_id AS raw_bbg_id
    FROM factset_base fb
    LEFT JOIN dcs_latest dcs ON dcs.dcs_fsym_id = fb.dcs_lookup_fsym_id
)
SELECT DISTINCT UPPER(TRIM(CAST(raw_bbg_id AS STRING))) AS linked_instrument_id_bb
FROM factset_bbg_union
WHERE NULLIF(UPPER(TRIM(CAST(raw_bbg_id AS STRING))), '') IS NOT NULL
"""

    sp_ids_sql = f"""
SELECT DISTINCT UPPER(TRIM(CAST(figi AS STRING))) AS linked_instrument_id_bb
FROM dbo.sp_loan_index
WHERE NULLIF(UPPER(TRIM(CAST(figi AS STRING))), '') IS NOT NULL
  AND CAST(asof_date AS DATE) >= '{window_from_date}'
  AND CAST(asof_date AS DATE) <= (SELECT MAX(CAST(asof_date AS DATE)) FROM dbo.sp_loan_index)
"""

    jpm_ids_sql = f"""
SELECT DISTINCT UPPER(TRIM(CAST(bbg_id AS STRING))) AS linked_instrument_id_bb
FROM dbo.jpm_loan_index
WHERE NULLIF(UPPER(TRIM(CAST(bbg_id AS STRING))), '') IS NOT NULL
  AND CAST(asof_date AS DATE) >= '{window_from_date}'
  AND CAST(asof_date AS DATE) <= (SELECT MAX(CAST(asof_date AS DATE)) FROM dbo.jpm_loan_index)
"""
    return factset_ids_sql, sp_ids_sql, jpm_ids_sql


def get_candidates(
    window_from_date: str,
    factset_app: str = FACTSET_APP,
    databricks_app: str = BDC_APP,
    priority: dict | None = None,
) -> pd.DataFrame:
    factset_ids_sql, sp_ids_sql, jpm_ids_sql = _build_candidate_sql(window_from_date)
    factset_ids = sqlutil.readdfsql(_normalize_sql_query(factset_ids_sql), app=databricks_app)
    sp_ids = sqlutil.readdfsql(_normalize_sql_query(sp_ids_sql), app=databricks_app)
    jpm_ids = sqlutil.readdfsql(_normalize_sql_query(jpm_ids_sql), app=databricks_app)

    for _df, _src, _itype in [
        (factset_ids, "factset", "factset_bbg_union"),
        (sp_ids, "sp_loan_index", "sp_figi"),
        (jpm_ids, "jpm_loan_index", "jpm_bbg_id"),
    ]:
        _df.columns = _df.columns.map(lambda c: str(c).lower())
        _df["linked_instrument_id_bb"] = _clean_bbg(_df["linked_instrument_id_bb"])
        _df["source_system"] = _src
        _df["id_type"] = _itype

    candidates = pd.concat([factset_ids, sp_ids, jpm_ids], ignore_index=True)
    candidates = candidates.dropna(subset=["linked_instrument_id_bb"]).drop_duplicates()

    priority = priority or {"factset": 0, "jpm_loan_index": 1, "sp_loan_index": 2}
    candidates["_p"] = candidates["source_system"].map(priority).fillna(99)
    candidates = (
        candidates.sort_values(["linked_instrument_id_bb", "_p"])
        .drop_duplicates(subset=["linked_instrument_id_bb"], keep="first")
        .drop(columns=["_p"])
        .reset_index(drop=True)
    )
    return candidates


def xpressfeed_cross_join_lookup(
    base_ids,
    spglobal_app: str = SPGLOBAL_APP,
    enable_security_like_fallback: bool = True,
    security_like_fallback_prefix: str = "BL",
) -> pd.DataFrame:
    cleaned_ids = (
        _clean_bbg(pd.Series(base_ids))
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    values_clause = _sql_values_clause(cleaned_ids)
    if not values_clause:
        return pd.DataFrame(
            columns=[
                "linked_instrument_id_bb",
                "instrument_id_bb",
                "parent_security_id",
                "related_identifier_active_flag",
            ]
        )

    fallback_filter_sql = "AND 1=0"
    if enable_security_like_fallback:
        prefix = str(security_like_fallback_prefix or "").strip().upper().replace("'", "''")
        fallback_filter_sql = f"AND inp.linked_instrument_id_bb LIKE '{prefix}%'" if prefix else ""

    sql = f"""
    WITH input_ids AS (
        SELECT COLUMN1 AS linked_instrument_id_bb
        FROM VALUES {values_clause}
    ),
    bloomberg_identifier_types AS (
        SELECT DISTINCT identifiertypeid, objecttypeid
        FROM SPGLOBAL.XPRESSFEED.ciqcrossrefidentifiertype
        WHERE identifiertypename ILIKE '%bloom%'
    ),
    parent_security_ids_trading AS (
        SELECT DISTINCT
            inp.linked_instrument_id_bb,
            c.securityid AS parent_security_id
        FROM input_ids inp
        JOIN SPGLOBAL.XPRESSFEED.ciqtradingitemidentifier a
            ON UPPER(TRIM(CAST(a.identifiervalue AS STRING))) = inp.linked_instrument_id_bb
        JOIN SPGLOBAL.XPRESSFEED.ciqfoundationtradingitem b
            ON a.tradingitemid = b.tradingitemid
        JOIN SPGLOBAL.XPRESSFEED.ciqfoundationsecurity c
            ON c.securityid = b.securityid
        JOIN bloomberg_identifier_types bit
            ON bit.identifiertypeid = a.identifiertypeid
           AND bit.objecttypeid = 22
    ),
    parent_security_ids_security_like AS (
        SELECT DISTINCT
            inp.linked_instrument_id_bb,
            si.securityid AS parent_security_id
        FROM input_ids inp
        JOIN SPGLOBAL.XPRESSFEED.ciqsecurityidentifier si
            ON UPPER(TRIM(CAST(si.identifiervalue AS STRING))) LIKE ('%' || inp.linked_instrument_id_bb)
        JOIN bloomberg_identifier_types bit
            ON bit.identifiertypeid = si.identifiertypeid
           AND bit.objecttypeid = 21
        {fallback_filter_sql}
        WHERE NOT EXISTS (
            SELECT 1
            FROM parent_security_ids_trading pit
            WHERE pit.linked_instrument_id_bb = inp.linked_instrument_id_bb
        )
    ),
    parent_security_ids AS (
        SELECT linked_instrument_id_bb, parent_security_id
        FROM parent_security_ids_trading
        UNION
        SELECT linked_instrument_id_bb, parent_security_id
        FROM parent_security_ids_security_like
    )
    SELECT DISTINCT
        psi.linked_instrument_id_bb,
        UPPER(TRIM(CAST(csi.identifiervalue AS STRING))) AS instrument_id_bb,
        psi.parent_security_id,
        csi.activeflag AS related_identifier_active_flag
    FROM parent_security_ids psi
    JOIN SPGLOBAL.XPRESSFEED.ciqsecurityidentifier csi
        ON csi.securityid = psi.parent_security_id
    JOIN bloomberg_identifier_types bit
        ON bit.identifiertypeid = csi.identifiertypeid
       AND bit.objecttypeid = 21
    WHERE NULLIF(UPPER(TRIM(CAST(csi.identifiervalue AS STRING))), '') IS NOT NULL
    """
    df = sqlutil.readdfsql(_normalize_sql_query(sql), app=spglobal_app)
    df.columns = df.columns.map(lambda c: str(c).lower())
    req = ["linked_instrument_id_bb", "instrument_id_bb", "parent_security_id", "related_identifier_active_flag"]
    for c in req:
        if c not in df.columns:
            df[c] = np.nan
    df = df[req].copy()
    if df.empty:
        return df
    df["linked_instrument_id_bb"] = _clean_bbg(df["linked_instrument_id_bb"])
    df["instrument_id_bb"] = _clean_bbg(df["instrument_id_bb"])
    return df.dropna(subset=["linked_instrument_id_bb", "instrument_id_bb"])


def _run_thread_parallel_lookup(
    chunk_list,
    spglobal_app: str,
    enable_security_like_fallback: bool,
    security_like_fallback_prefix: str,
    max_parallel_workers: int,
):
    frames = []
    error_count = 0
    max_workers = max(1, min(int(max_parallel_workers or 1), len(chunk_list)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                xpressfeed_cross_join_lookup,
                chunk,
                spglobal_app,
                enable_security_like_fallback,
                security_like_fallback_prefix,
            ): i
            for i, chunk in enumerate(chunk_list, start=1)
        }
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            try:
                chunk_df = future.result()
                if isinstance(chunk_df, pd.DataFrame) and not chunk_df.empty:
                    frames.append(chunk_df)
            except Exception as exc:
                error_count += 1
                logging.error("Threaded SPGLOBAL chunk %s/%s failed: %s", i, len(chunk_list), exc)
    return frames, error_count


def get_xref_lookup_df(
    lookup_ids,
    spglobal_app: str = SPGLOBAL_APP,
    lookup_batch_size: int = 1000,
    enable_parallel_lookup: bool = True,
    parallel_backend: str = "thread",
    parallel_params_per_task: int = 1,
    max_parallel_workers: int = 8,
    enable_security_like_fallback: bool = True,
    security_like_fallback_prefix: str = "BL",
):
    chunk_list = [chunk for chunk in _chunked(lookup_ids, lookup_batch_size) if chunk]
    xref_frames = []
    query_count = 0

    if enable_parallel_lookup and chunk_list:
        query_count = len(chunk_list)
        logging.info("Starting parallel SPGLOBAL lookup for %s chunks via %s", len(chunk_list), parallel_backend)
        if parallel_backend.lower() == "spark":
            try:
                if not getattr(sparkmanager, "isinit", False) or not hasattr(sparkmanager, "blob_dloader"):
                    sparkmanager.init()
                chunk_params = [(chunk,) for chunk in chunk_list]
                res = sparkmanager.submitjob(
                    _xpressfeed_cross_join_lookup_chunk,
                    chunk_params,
                    initconfig=True,
                    params_per_task=parallel_params_per_task,
                    spglobal_app=spglobal_app,
                    enable_security_like_fallback=enable_security_like_fallback,
                    security_like_fallback_prefix=security_like_fallback_prefix,
                )
                for i, r in enumerate(res, start=1):
                    if r.get("error"):
                        logging.error("SPGLOBAL spark chunk %s/%s failed: %s", i, len(chunk_list), r.get("error"))
                        continue
                    out = r.get("result")
                    if isinstance(out, pd.DataFrame) and not out.empty:
                        xref_frames.append(out)
            except Exception as exc:
                logging.warning("Spark parallel lookup failed; falling back to thread backend: %s", exc)
                xref_frames, _ = _run_thread_parallel_lookup(
                    chunk_list,
                    spglobal_app,
                    enable_security_like_fallback,
                    security_like_fallback_prefix,
                    max_parallel_workers,
                )
        else:
            xref_frames, _ = _run_thread_parallel_lookup(
                chunk_list,
                spglobal_app,
                enable_security_like_fallback,
                security_like_fallback_prefix,
                max_parallel_workers,
            )
    else:
        total_chunks = int(math.ceil(len(lookup_ids) / max(lookup_batch_size, 1))) if lookup_ids else 0
        for i, chunk in enumerate(chunk_list, start=1):
            query_count += 1
            logging.info("SPGLOBAL chunk %s/%s | ids=%s", i, total_chunks, len(chunk))
            xref_frames.append(
                xpressfeed_cross_join_lookup(
                    chunk,
                    spglobal_app=spglobal_app,
                    enable_security_like_fallback=enable_security_like_fallback,
                    security_like_fallback_prefix=security_like_fallback_prefix,
                )
            )

    xref_lookup_df = (
        pd.concat(xref_frames, ignore_index=True)
        if xref_frames
        else pd.DataFrame(columns=["linked_instrument_id_bb", "instrument_id_bb", "parent_security_id", "related_identifier_active_flag"])
    )
    xref_lookup_df = xref_lookup_df.drop_duplicates(
        subset=["linked_instrument_id_bb", "instrument_id_bb"],
        keep="first",
    )
    return xref_lookup_df, query_count


def _xpressfeed_cross_join_lookup_chunk(
    chunk_ids,
    spglobal_app: str = SPGLOBAL_APP,
    enable_security_like_fallback: bool = True,
    security_like_fallback_prefix: str = "BL",
) -> pd.DataFrame:
    return xpressfeed_cross_join_lookup(
        chunk_ids,
        spglobal_app=spglobal_app,
        enable_security_like_fallback=enable_security_like_fallback,
        security_like_fallback_prefix=security_like_fallback_prefix,
    )


def run_pipeline(
    window_from_date: str = "2025-01-01",
    schema_name: str = DEFAULT_SCHEMA_NAME,
    table_name: str = DEFAULT_TABLE_NAME,
    databricks_app: str = BDC_APP,
    factset_app: str = FACTSET_APP,
    spglobal_app: str = SPGLOBAL_APP,
    lookup_batch_size: int = 1000,
    test_max_ids: int | None = None,
    refresh_all_lookup_ids: bool = False,
    retry_existing_not_found: bool = True,
    enable_security_like_fallback: bool = True,
    security_like_fallback_prefix: str = "BL",
    enable_parallel_lookup: bool = True,
    parallel_backend: str = "thread",
    parallel_params_per_task: int = 1,
    max_parallel_workers: int = 8,
    purge_legacy_bdp_found_rows: bool = False,
):
    validate_required_apps(factset_app=factset_app, databricks_app=databricks_app, spglobal_app=spglobal_app)
    candidates = get_candidates(window_from_date, factset_app=factset_app, databricks_app=databricks_app)

    existing_sql = f"SELECT * FROM {schema_name}.{table_name}"
    try:
        bridge_existing = sqlutil.readdfsql(existing_sql, app=databricks_app)
    except Exception:
        bridge_existing = pd.DataFrame()

    if bridge_existing.empty:
        bridge_existing = pd.DataFrame(columns=FINAL_COLS)
    else:
        bridge_existing.columns = bridge_existing.columns.map(lambda c: str(c).lower())
        for c in ["linked_instrument_id_bb", "instrument_id_bb"]:
            if c in bridge_existing.columns:
                bridge_existing[c] = _clean_bbg(bridge_existing[c])
        if "mapping_provider" not in bridge_existing.columns:
            bridge_existing["mapping_provider"] = np.nan
        bridge_existing["mapping_provider"] = (
            bridge_existing["mapping_provider"].astype(str).str.strip().str.lower().replace(
                {"": np.nan, "nan": np.nan, "none": np.nan, "null": np.nan}
            )
        )
        if "bdp_status" not in bridge_existing.columns:
            bridge_existing["bdp_status"] = np.nan
        bdp_up = bridge_existing["bdp_status"].astype(str).str.strip().str.upper()
        bridge_existing.loc[
            bridge_existing["mapping_provider"].isna() & (bdp_up == "FOUND"),
            "mapping_provider",
        ] = "bbg"

    if purge_legacy_bdp_found_rows:
        bdp_up = bridge_existing["bdp_status"].astype(str).str.strip().str.upper()
        provider_lo = bridge_existing["mapping_provider"].astype(str).str.strip().str.lower()
        legacy_mask = (bdp_up == "FOUND") & (provider_lo != "spglobal")
        bridge_existing = bridge_existing.loc[~legacy_mask].copy()

    existing_key_set = set(bridge_existing.get("linked_instrument_id_bb", pd.Series(dtype="object")).dropna().astype(str))
    candidate_id_set = set(candidates["linked_instrument_id_bb"].dropna().astype(str))
    existing_unmatched_set = set(
        bridge_existing.loc[
            bridge_existing["instrument_id_bb"].isna(),
            "linked_instrument_id_bb",
        ]
        .dropna()
        .astype(str)
    ) if "instrument_id_bb" in bridge_existing.columns else set()

    if refresh_all_lookup_ids:
        lookup_ids = sorted(candidate_id_set)
    else:
        new_ids = candidate_id_set - existing_key_set
        retry_ids = (candidate_id_set & existing_unmatched_set) if retry_existing_not_found else set()
        lookup_ids = sorted(new_ids | retry_ids)
    if test_max_ids is not None:
        lookup_ids = lookup_ids[: int(test_max_ids)]

    xref_lookup_df, query_count = get_xref_lookup_df(
        lookup_ids,
        spglobal_app=spglobal_app,
        lookup_batch_size=lookup_batch_size,
        enable_parallel_lookup=enable_parallel_lookup,
        parallel_backend=parallel_backend,
        parallel_params_per_task=parallel_params_per_task,
        max_parallel_workers=max_parallel_workers,
        enable_security_like_fallback=enable_security_like_fallback,
        security_like_fallback_prefix=security_like_fallback_prefix,
    )

    lookup_meta = candidates[candidates["linked_instrument_id_bb"].isin(lookup_ids)].copy()
    new_rows = lookup_meta.merge(xref_lookup_df, on="linked_instrument_id_bb", how="left")
    new_rows = new_rows[new_rows["instrument_id_bb"].notna()].copy()

    now_ts = pd.Timestamp.now("UTC").tz_convert(None)
    new_rows["security_des"] = np.nan
    new_rows["series"] = np.nan
    new_rows["mty_typ"] = np.nan
    new_rows["cpn"] = np.nan
    new_rows["id_bb8"] = new_rows["instrument_id_bb"].astype(str).str[:8]
    new_rows.loc[new_rows["id_bb8"].str.len() != 8, "id_bb8"] = np.nan
    new_rows["mapping_provider"] = "spglobal"
    new_rows["bdp_status"] = "SPGLOBAL"
    new_rows["created_at"] = now_ts
    new_rows["updated_at"] = now_ts
    new_rows["last_seen_at"] = now_ts
    new_rows["is_active"] = new_rows["instrument_id_bb"].notna()

    for c in FINAL_COLS:
        if c not in bridge_existing.columns:
            bridge_existing[c] = np.nan
        if c not in new_rows.columns:
            new_rows[c] = np.nan
    bridge_existing = bridge_existing[FINAL_COLS].copy()
    new_rows = new_rows[FINAL_COLS].copy()

    # Normalize timestamp columns to string before concat to avoid pandas block shape conflicts
    for df in [bridge_existing, new_rows]:
        for ts_col in ["created_at", "updated_at", "last_seen_at"]:
            if ts_col in df.columns:
                df[ts_col] = df[ts_col].astype(str).replace({"NaT": np.nan, "nat": np.nan, "None": np.nan, "nan": np.nan})

    resolved_ids = set(new_rows["linked_instrument_id_bb"].dropna().astype(str))
    if not bridge_existing.empty and resolved_ids:
        bridge_existing = bridge_existing[~bridge_existing["linked_instrument_id_bb"].isin(resolved_ids)].copy()

    bridge_final = pd.concat([bridge_existing, new_rows], ignore_index=True)
    bridge_final = bridge_final.drop_duplicates(subset=["linked_instrument_id_bb", "instrument_id_bb"], keep="last")
    seen_ids = set(candidates["linked_instrument_id_bb"].dropna().astype(str))
    if seen_ids and not bridge_final.empty:
        bridge_final.loc[bridge_final["linked_instrument_id_bb"].isin(seen_ids), "last_seen_at"] = now_ts

    for ts_col in ["created_at", "updated_at", "last_seen_at"]:
        if ts_col in bridge_final.columns:
            bridge_final[ts_col] = pd.to_datetime(bridge_final[ts_col], errors="coerce", utc=True).dt.tz_convert(None)

    qa_summary = pd.DataFrame(
        [{
            "total_rows": int(len(bridge_final)),
            "matched_rows": int(bridge_final["instrument_id_bb"].notna().sum()),
            "unmatched_rows": int(bridge_final["instrument_id_bb"].isna().sum()),
            "distinct_linked_ids": int(bridge_final["linked_instrument_id_bb"].nunique(dropna=True)),
            "distinct_parent_security_ids": int(bridge_final["parent_security_id"].nunique(dropna=True)),
        }]
    )

    return {
        "candidates": candidates,
        "bridge_existing": bridge_existing,
        "lookup_ids": lookup_ids,
        "xref_lookup_df": xref_lookup_df,
        "new_rows": new_rows,
        "bridge_final": bridge_final,
        "qa_summary": qa_summary,
        "metrics": {
            "existing_linked_ids": len(existing_key_set),
            "existing_unmatched_ids": len(existing_unmatched_set),
            "lookup_ids": len(lookup_ids),
            "spglobal_queries": int(query_count),
            "xref_rows": len(xref_lookup_df),
            "rows_in_result": len(bridge_final),
            "rows_with_matches": int(bridge_final["instrument_id_bb"].notna().sum()),
        },
    }


def persist_bridge(
    bridge_final: pd.DataFrame,
    table_name: str,
    table_location: str,
    run_write: bool = False,
):
    if not run_write:
        logging.info("RUN_WRITE=False. Skipping Delta write.")
        return
    if not getattr(sparkmanager, "isinit", False) or not hasattr(sparkmanager, "blob_dloader"):
        sparkmanager.init()

    # Support both styles:
    # 1) table_location as parent dir (e.g., .../tables/dbo)
    # 2) table_location as full table path (e.g., .../tables/dbo/related_bbg_instruments)
    # SparkManager.pandastodelta always appends "/{table_name}", so strip trailing table segment when present.
    normalized_location = str(table_location or "").rstrip("/")
    if normalized_location.lower().endswith("/" + str(table_name).lower()):
        normalized_location = normalized_location[: -(len(table_name) + 1)]

    sparkmanager.pandastodelta(bridge_final, table_name, normalized_location, overwrite=True)
    logging.info("Delta write completed.")
