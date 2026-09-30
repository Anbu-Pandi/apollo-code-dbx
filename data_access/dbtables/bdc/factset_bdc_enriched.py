import ast
import logging
import os
import tempfile
import time
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb
import numpy as np
import pandas as pd

from common.utils.jobutil import retrycode
from common.utils.sqlutil import execsql, fetch_data_with_arrow, insertdf_batch
from data_access.dbtables.bdc import BDC_APP
from data_access.dbtables.bdc.holdings import (
    get_bbg_bridge,
    get_direct_index_bond_security_map,
    get_direct_index_constituent_stats,
    get_factset_bdc_holdings,
    get_factset_bdc_holdings_all,
    get_factset_bdc_holdings_asof,
    get_factset_bdc_holdings_by_collection_date,
    get_factset_bdc_holdings_knowledge_updates,
    get_factset_bdc_holdings_updated_since,
    get_databricks_warehouse_connection,
    set_bdc_databricks_app,
)
from data_access.dbtables.bdc.matching import enrich_factset_bbg
from data_access.spark.api import sparkmanager


DEFAULT_INDEX_VIEW = "dbo.v_index_constituent_stats"
DEFAULT_SQL_TABLE_NAME = "dbo.factset_bdc_collateral_index_enriched"
DEFAULT_DELTA_TABLE_NAME = "factset_bdc_collateral_index_enriched"
DEFAULT_DELTA_BLOB_DIR = "tables/dbo"
DEFAULT_RESULT_DEDUPE_KEYS = ["position_row_id", "asof_date"]
DEFAULT_HOLDINGS_SQL_TABLE_NAME = "dbo.factset_bdc_holdings_knowledge"
DEFAULT_HOLDINGS_DELTA_TABLE_NAME = "factset_bdc_holdings_knowledge"
DEFAULT_HOLDINGS_DELTA_BLOB_DIR = "tables/dbo"
DEFAULT_HOLDINGS_DEDUPE_KEYS = ["knowledge_date", "holding_row_key", "holding_row_hash"]
DEFAULT_SQL_INSERT_BATCH_SIZE = 500
DEFAULT_RESULT_PARTITION_COLUMN = "knowledge_date"
DEFAULT_INDEX_ROLL_FORWARD_DAYS = 3
HOLDINGS_SNAPSHOT_VERSION = 2


def _read_bdc_databricks_sql(
    sql: str,
    *,
    msg: str,
    databricks_app: str,
    chunk_size: int = 10_000,
    retrycount: int = 3,
) -> pd.DataFrame:
    app = str(databricks_app or "").strip()
    if not app:
        raise ValueError("databricks_app is required for BDC Databricks reads.")

    def _fetch_once() -> pd.DataFrame:
        with get_databricks_warehouse_connection(app) as conn:
            return fetch_data_with_arrow(sql, conn, chunk_size)

    logging.info("Executing Databricks sql %s with app=%s", msg, app)
    df = retrycode(_fetch_once, retrycount=retrycount, wait_on_error_seconds=5)
    logging.info("Retrieved df shape %s: %s", msg, df.shape)
    logging.info("Completed Databricks sql %s!", msg)
    return df


def _execute_bdc_databricks_sql(sql: str, *, msg: str, databricks_app: str) -> None:
    app = str(databricks_app or "").strip()
    if not app:
        raise ValueError("databricks_app is required for BDC Databricks statements.")
    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        sparkmanager.refresh_spark(is_databricks_env=True)
        if sparkmanager._spark is None:
            raise RuntimeError("No active Spark session is available in Databricks Runtime.")
        logging.info("Executing Spark sql %s", msg)
        sparkmanager._spark.sql(sql).collect()
        logging.info("Completed Spark sql %s!", msg)
        return
    execsql(sql, msg=msg, app=app)


def _resolve_use_spark_write(use_spark_write: bool | None) -> bool:
    if use_spark_write is not None:
        return bool(use_spark_write)
    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        return True
    return not getattr(sparkmanager, "isdriverlocal", True)


def _parse_delta_partition_columns(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, float) and pd.isna(value):
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw == "[]":
            return []
        try:
            parsed = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return [part.strip().strip("`'\"") for part in raw.strip("[]").split(",") if part.strip()]
        return _parse_delta_partition_columns(parsed)
    if isinstance(value, (list, tuple, set, np.ndarray, pd.Series)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _get_delta_partition_columns(sql_table_name: str, databricks_app: str = BDC_APP) -> list[str]:
    detail_df = _read_bdc_databricks_sql(
        f"DESCRIBE DETAIL {sql_table_name}",
        msg="Describe target Delta table partitioning",
        databricks_app=databricks_app,
    )
    if detail_df is None or detail_df.empty or "partitionColumns" not in detail_df.columns:
        return []
    return _parse_delta_partition_columns(detail_df.iloc[0]["partitionColumns"])


def _resolve_existing_delta_partitioncolumn(
    sql_table_name: str,
    requested_partition_column: str,
    databricks_app: str = BDC_APP,
) -> str | None:
    partition_columns = _get_delta_partition_columns(sql_table_name, databricks_app=databricks_app)
    if not partition_columns:
        logging.info(
            "Target table %s is unpartitioned; Spark append will not pass a partition column.",
            sql_table_name,
        )
        return None

    requested = requested_partition_column.strip().lower()
    normalized = [col.strip().lower() for col in partition_columns]
    if normalized == [requested]:
        return partition_columns[0]

    logging.warning(
        "Target table %s is partitioned by %s; requested Spark write partition was %s. "
        "Using existing Delta partition column %s for append.",
        sql_table_name,
        partition_columns,
        requested_partition_column,
        partition_columns[0],
    )
    return partition_columns[0]


def validate_parallel_result_target(
    sql_table_name: str,
    databricks_app: str = BDC_APP,
) -> None:
    partition_columns = _get_delta_partition_columns(
        sql_table_name,
        databricks_app=databricks_app,
    )
    if [column.lower() for column in partition_columns] != [DEFAULT_RESULT_PARTITION_COLUMN]:
        raise RuntimeError(
            f"Parallel date shards require {sql_table_name} to be partitioned by "
            f"{DEFAULT_RESULT_PARTITION_COLUMN}; found {partition_columns or 'unpartitioned'}."
        )


HOLDING_INDEX_COLS = [
    "report_date",
    "asof_date",
    "collection_date",
    "publication_date",
    "last_update",
    "bdc_factset_entity_id",
    "factset_report_id",
    "fsym_id",
]

HOLDING_SOURCE_ROW_ID_COL = "factset_bdc_holding_id"
HOLDING_ROW_KEY_COLS = [
    HOLDING_SOURCE_ROW_ID_COL,
]
HOLDING_ROW_FALLBACK_KEY_COLS = [
    "bdc_factset_entity_id",
    "report_date",
    "factset_report_id",
    "factset_portfolio_entity_id",
    "fsym_id",
    "investment_type_code",
    "debt_code",
    "seniority_code",
    "coupon_code",
    "maturity_date",
    "bdc_security_desc",
]

INDEX_IDENTIFIER_COLS = [
    "asof_date",
    "index_name",
    "index_desc",
    "constituent_id",
    "constituent_name",
    "cusip",
    "isin",
    "loanx_id",
    "ticker",
    "linked_instrument_id_bb",
    "bbg_match_source_key_type",
    "bbg_match_source_key_value",
    "instrument_id_bb",
    "id_bb8",
]
INDEX_METRIC_COLS = [
    "price",
    "industry",
    "duration",
    "period_total_return",
    "spread",
    "current_spread_coup",
    "coupon",
    "amt_out",
    "ytm",
    "maturity_date",
    "period_principal_return",
]
SOURCE_COL = "source"
DIRECT_INDEX_BRIDGE_KEY_TYPES = {
    "JPM": ("bbg_id", "loanx_id", "cusip", None),
    "SP": ("figi", "loanx_id", "cusip", "pricing_id"),
    "ICE": ("isin", "cusip", "ticker", None),
}
FACTSET_BBG_ID_COLUMNS = (
    "instrument_id_bb",
    "id_bb8",
    "bbg_id_new",
    "bbg_id_new8",
    "dcs_instrument_id_bb",
    "dcs_id_bb8",
    "sym_v1_instrument_id_bb",
    "sym_v1_id_bb8",
    "factset_sym_bbg_bbg_id",
    "dcs_bbg_id",
)

FACTSET_KEY_COLS = [
    "instrument_id_bb",
    "id_bb8",
    "bbg_id_new8",
    "bbg_id_new",
    "dcs_instrument_id_bb",
    "dcs_id_bb8",
    "dcs_id_bb",
    "sym_v1_instrument_id_bb",
    "sym_v1_id_bb8",
    "factset_sym_bbg_bbg_id",
    "dcs_bbg_id",
    "dcs_isin",
    "factset_sym_isin_hist_isin",
    "factset_sym_cusip_loan_cusip",
    "factset_sym_cusip_loan_ppn_cusip",
    "factset_sym_cusip_loan_pcl_cusip",
    "factset_sym_cusip_hist_cusip",
    "dcs_detail_id",
    "dcs_joined_fsym_id",
    "dcs_lookup_fsym_id",
    "fsym_id",
]
INDEX_KEY_COLS = [
    "index_instrument_id_bb",
    "index_id_bb8",
    "index_linked_instrument_id_bb",
    "index_bbg_match_source_key_value",
    "index_isin",
    "index_cusip",
    "index_loanx_id",
    "index_constituent_id",
    "index_ticker",
]


def _bytes_to_gb(num_bytes: int) -> float:
    return float(num_bytes) / (1024**3)


def _get_process_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return None


def _df_mem_bytes(df: pd.DataFrame) -> int:
    try:
        return int(df.memory_usage(deep=True).sum())
    except Exception:
        return 0


def _log_memory_checkpoint(stage: str, dfs: dict[str, pd.DataFrame] | None = None) -> None:
    rss_bytes = _get_process_rss_bytes()
    msg_parts = [f"mem_checkpoint stage={stage}"]
    if rss_bytes is not None:
        msg_parts.append(f"process_rss_gb={_bytes_to_gb(rss_bytes):.2f}")
    else:
        msg_parts.append("process_rss_gb=NA")

    if dfs:
        for name, df in dfs.items():
            if isinstance(df, pd.DataFrame):
                mem_bytes = _df_mem_bytes(df)
                msg_parts.append(
                    f"{name}_rows={len(df):,}"
                )
                msg_parts.append(
                    f"{name}_cols={df.shape[1]:,}"
                )
                msg_parts.append(
                    f"{name}_mem_gb={_bytes_to_gb(mem_bytes):.2f}"
                )

    logging.info(" | ".join(msg_parts))


def clean_key(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip().str.upper()
    return s.where(~s.isin(["", "NAN", "NONE", "NULL"]), pd.NA)


def clean_id_bb8(series: pd.Series) -> pd.Series:
    s = clean_key(series).str[:8]
    return s.where(s.str.len() == 8)


def clean_cusip9(series: pd.Series) -> pd.Series:
    s = clean_key(series).str.replace(r"[^A-Z0-9]", "", regex=True).str[:9]
    return s.where(s.str.len() == 9)


def schema_hint(df: pd.DataFrame) -> dict:
    hints = {}
    for col in df.columns:
        dt = df[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dt):
            hints[col] = "datetime64[ns]"
        elif pd.api.types.is_integer_dtype(dt):
            hints[col] = "int64"
        elif pd.api.types.is_float_dtype(dt):
            hints[col] = "float64"
        elif pd.api.types.is_bool_dtype(dt):
            hints[col] = "bool"
        else:
            hints[col] = "string"
    return hints


def _quote_databricks_identifier(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def _infer_databricks_sql_type(series: pd.Series) -> str:
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"

    sample = series.dropna().head(100)
    if not sample.empty and sample.map(lambda x: isinstance(x, Decimal)).all():
        return "DECIMAL(38, 10)"
    return "STRING"


def _create_delta_table_with_schema(
    df: pd.DataFrame,
    sql_table_name: str,
    target_path: str,
    databricks_app: str,
) -> None:
    table_parts = sql_table_name.split(".")
    if len(table_parts) > 1:
        schema_name = ".".join(table_parts[:-1])
        _execute_bdc_databricks_sql(
            f"CREATE SCHEMA IF NOT EXISTS {schema_name}",
            msg=f"Ensure schema exists for {schema_name}",
            databricks_app=databricks_app,
        )

    column_defs = [
        f"{_quote_databricks_identifier(col)} {_infer_databricks_sql_type(df[col])}"
        for col in df.columns
    ]
    create_sql = f"""
CREATE OR REPLACE TABLE {sql_table_name} (
  {', '.join(column_defs)}
)
USING DELTA
LOCATION '{target_path}'
"""
    _execute_bdc_databricks_sql(
        create_sql,
        msg=f"Create holdings target table with explicit schema: {sql_table_name}",
        databricks_app=databricks_app,
    )


def build_date_chunks(from_date: str, to_date: str, chunk_months: int = 3) -> list[tuple[str, str]]:
    start = pd.to_datetime(from_date).normalize()
    end = pd.to_datetime(to_date).normalize()
    chunk_months = max(int(chunk_months), 1)
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = (cur + pd.offsets.MonthEnd(chunk_months)).normalize()
        chunk_end = min(chunk_end, end)
        chunks.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cur = chunk_end + pd.Timedelta(days=1)
    return chunks


def _run_parallel_chunk_fetch(
    chunks: list[tuple[str, str]],
    fetch_fn,
    max_parallel_workers: int = 8,
    label: str = "chunk",
) -> list[pd.DataFrame]:
    if not chunks:
        return []
    max_workers = max(1, min(int(max_parallel_workers or 1), len(chunks)))
    parts = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_fn, chunk): i for i, chunk in enumerate(chunks, start=1)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                part = future.result()
                if isinstance(part, pd.DataFrame) and not part.empty:
                    parts.append((i, part))
                    logging.info("%s %s/%s rows=%s", label, i, len(chunks), len(part))
            except Exception as exc:
                logging.error("%s %s/%s failed: %s", label, i, len(chunks), exc)
    return [df for _, df in sorted(parts, key=lambda x: x[0])]


def fetch_factset_holdings_chunked(
    from_date: str,
    to_date: str,
    chunk_months: int = 3,
    enable_parallel: bool = True,
    max_parallel_workers: int = 8,
) -> pd.DataFrame:
    chunks = build_date_chunks(from_date, to_date, chunk_months=chunk_months)
    def _fetch(chunk):
        chunk_from, chunk_to = chunk
        part = get_factset_bdc_holdings(chunk_from, chunk_to)
        if part is None or part.empty:
            return pd.DataFrame()
        part.columns = part.columns.str.lower()
        return part

    if enable_parallel and len(chunks) > 1:
        parts = _run_parallel_chunk_fetch(
            chunks=chunks,
            fetch_fn=_fetch,
            max_parallel_workers=max_parallel_workers,
            label="FactSet chunk",
        )
    else:
        parts = []
        for i, chunk in enumerate(chunks, start=1):
            part = _fetch(chunk)
            if part.empty:
                logging.info("FactSet chunk %s/%s returned 0 rows", i, len(chunks))
                continue
            parts.append(part)
            logging.info("FactSet chunk %s/%s rows=%s", i, len(chunks), len(part))

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    return out.drop_duplicates().reset_index(drop=True)


def fetch_index_constituent_stats_chunked(
    index_view: str,
    from_date: str,
    to_date: str,
    chunk_months: int = 3,
    databricks_app: str = BDC_APP,
    enable_parallel: bool = True,
    max_parallel_workers: int = 8,
    latest_per_source: bool = False,
) -> pd.DataFrame:
    chunks = build_date_chunks(from_date, to_date, chunk_months=chunk_months)
    source_columns = list(dict.fromkeys(INDEX_IDENTIFIER_COLS + [SOURCE_COL] + INDEX_METRIC_COLS))
    select_columns = ",\n    ".join(source_columns)

    def _fetch(chunk):
        chunk_from, chunk_to = chunk
        latest_source_filter = ""
        if latest_per_source:
            latest_source_filter = """
QUALIFY CAST(asof_date AS DATE) = MAX(CAST(asof_date AS DATE)) OVER (PARTITION BY source)
"""
        index_sql = f"""
SELECT
    {select_columns}
FROM {index_view}
WHERE asof_date >= '{chunk_from}'
  AND asof_date <= '{chunk_to}'
{latest_source_filter}
"""
        part = _read_bdc_databricks_sql(
            index_sql,
            msg=f"v_index_constituent_stats {chunk_from}..{chunk_to}",
            databricks_app=databricks_app,
        )
        if part is None or part.empty:
            return pd.DataFrame()
        part.columns = part.columns.str.lower()
        return part

    if enable_parallel and len(chunks) > 1:
        parts = _run_parallel_chunk_fetch(
            chunks=chunks,
            fetch_fn=_fetch,
            max_parallel_workers=max_parallel_workers,
            label="Index chunk",
        )
    else:
        parts = []
        for i, chunk in enumerate(chunks, start=1):
            part = _fetch(chunk)
            if part.empty:
                logging.info("Index chunk %s/%s returned 0 rows", i, len(chunks))
                continue
            parts.append(part)
            logging.info("Index chunk %s/%s rows=%s", i, len(chunks), len(part))

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates().reset_index(drop=True)
    out["asof_date"] = _normalize_date_series(out["asof_date"])
    return out


def enrich_direct_index_bbg(
    index_df: pd.DataFrame,
    bbg_bridge: dict[str, str],
) -> pd.DataFrame:
    out = index_df.copy()
    out.columns = out.columns.str.lower()
    bridge_key_columns = [f"_bridge_key_{i}" for i in range(1, 5)]
    required = {SOURCE_COL, *bridge_key_columns}
    missing = required - set(out.columns)
    if missing:
        raise RuntimeError(f"Direct index data missing bridge columns: {sorted(missing)}")

    out["linked_instrument_id_bb"] = pd.NA
    out["bbg_match_source_key_type"] = pd.NA
    out["bbg_match_source_key_value"] = pd.NA
    out["instrument_id_bb"] = pd.NA
    for source, key_types in DIRECT_INDEX_BRIDGE_KEY_TYPES.items():
        source_index = out.index[out[SOURCE_COL].eq(source)]
        if source_index.empty:
            continue
        keys = [clean_key(out.loc[source_index, column]) for column in bridge_key_columns]
        mapped = [clean_key(key.map(bbg_bridge)) for key in keys]
        selected_key = pd.Series(pd.NA, index=source_index, dtype="string")
        selected_type = pd.Series(pd.NA, index=source_index, dtype="string")
        selected_bbg = pd.Series(pd.NA, index=source_index, dtype="string")

        for key, mapped_bbg, key_type in zip(keys, mapped, key_types):
            if key_type is None:
                continue
            use = selected_bbg.isna() & mapped_bbg.notna()
            selected_key.loc[use] = key.loc[use]
            selected_type.loc[use] = key_type
            selected_bbg.loc[use] = mapped_bbg.loc[use]
        for key, key_type in zip(keys, key_types):
            if key_type is None:
                continue
            use = selected_key.isna() & key.notna()
            selected_key.loc[use] = key.loc[use]
            selected_type.loc[use] = key_type

        out.loc[source_index, "linked_instrument_id_bb"] = selected_key
        out.loc[source_index, "bbg_match_source_key_type"] = selected_type
        out.loc[source_index, "bbg_match_source_key_value"] = selected_key
        out.loc[source_index, "instrument_id_bb"] = selected_bbg

    out["id_bb8"] = clean_id_bb8(out["instrument_id_bb"])
    return out.drop(columns=bridge_key_columns)


def fetch_index_constituent_stats_direct(
    from_date: str,
    to_date: str,
    bbg_bridge: dict[str, str],
    factset_bbg_ids=(),
) -> pd.DataFrame:
    index_df = get_direct_index_constituent_stats(from_date, to_date)
    if index_df is None or index_df.empty:
        return pd.DataFrame()
    out = enrich_direct_index_bbg(index_df, bbg_bridge)
    bond_map = get_direct_index_bond_security_map(
        from_date,
        to_date,
        factset_bbg_ids,
    )
    if bond_map:
        use_bond_map = out[SOURCE_COL].eq("ICE") & out["instrument_id_bb"].isna()
        out.loc[use_bond_map, "instrument_id_bb"] = clean_key(
            out.loc[use_bond_map, "isin"].map(bond_map)
        )
        out["id_bb8"] = clean_id_bb8(out["instrument_id_bb"])
    out["asof_date"] = _normalize_date_series(out["asof_date"])
    return out.drop_duplicates().reset_index(drop=True)


def collect_factset_bbg_ids(factset_df: pd.DataFrame) -> set[str]:
    columns = [column for column in FACTSET_BBG_ID_COLUMNS if column in factset_df.columns]
    if not columns:
        return set()
    values = pd.concat([clean_key(factset_df[column]) for column in columns], ignore_index=True)
    co_prefix_stripped = values.str[2:].where(values.str.startswith("CO", na=False))
    return set(pd.concat([values, co_prefix_stripped], ignore_index=True).dropna().astype(str))


def _normalize_date_value(date_value) -> pd.Timestamp:
    ts = pd.to_datetime(date_value, errors="coerce", utc=True)
    if pd.isna(ts):
        return pd.NaT
    return ts.tz_localize(None).normalize()


def _normalize_date_series(date_values: pd.Series) -> pd.Series:
    return pd.to_datetime(date_values, errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()


def roll_forward_index_constituent_stats(
    index_raw_df: pd.DataFrame,
    target_date: str,
) -> pd.DataFrame:
    if index_raw_df is None or index_raw_df.empty:
        return pd.DataFrame()

    out = index_raw_df.copy()
    out.columns = out.columns.str.lower()
    required_cols = {"asof_date", SOURCE_COL}
    missing_cols = required_cols - set(out.columns)
    if missing_cols:
        raise RuntimeError(f"Cannot roll forward index data; missing columns: {sorted(missing_cols)}")

    target_ts = _normalize_date_value(target_date)
    if pd.isna(target_ts):
        raise ValueError(f"Invalid roll-forward target_date={target_date!r}")

    out["asof_date"] = _normalize_date_series(out["asof_date"])
    out = out.loc[out["asof_date"].notna() & (out["asof_date"] <= target_ts)].copy()
    if out.empty:
        return out

    latest_source_dates = (
        out.groupby(SOURCE_COL, dropna=False)["asof_date"]
        .max()
        .reset_index(name="source_asof_date")
    )
    rolled = out.merge(latest_source_dates, on=SOURCE_COL, how="inner")
    rolled = rolled.loc[rolled["asof_date"] == rolled["source_asof_date"]].copy()
    rolled["source_asof_date"] = _normalize_date_series(rolled["source_asof_date"])
    rolled["asof_date"] = target_ts

    source_summary = (
        rolled[[SOURCE_COL, "source_asof_date"]]
        .drop_duplicates()
        .sort_values([SOURCE_COL, "source_asof_date"])
    )
    logging.info(
        "Index roll-forward target=%s sources=%s rows=%s source_dates=%s",
        target_ts.strftime("%Y-%m-%d"),
        rolled[SOURCE_COL].nunique(dropna=False),
        f"{len(rolled):,}",
        source_summary.to_dict("records"),
    )
    return rolled.reset_index(drop=True)


def fetch_factset_holdings_knowledge(
    update_from: str | None = None,
    update_to: str | None = None,
    asof_date: str | None = None,
    collection_from: str | None = None,
    collection_to: str | None = None,
    last_update_from: str | None = None,
    last_update_to: str | None = None,
    databricks_app: str = BDC_APP,
) -> pd.DataFrame:
    set_bdc_databricks_app(databricks_app)
    if update_from and update_to:
        factset_df = get_factset_bdc_holdings_knowledge_updates(
            from_date=update_from,
            to_date=update_to,
            asof_date=asof_date,
        )
        knowledge_date = update_to
    elif last_update_from:
        factset_df = get_factset_bdc_holdings_updated_since(
            last_update_from=last_update_from,
            last_update_to=last_update_to,
        )
        knowledge_date = last_update_to or asof_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    elif collection_from and collection_to:
        factset_df = get_factset_bdc_holdings_by_collection_date(collection_from, collection_to)
        knowledge_date = collection_to
    else:
        factset_df = get_factset_bdc_holdings_all()
        knowledge_date = asof_date or pd.Timestamp.today().strftime("%Y-%m-%d")

    if factset_df is None or factset_df.empty:
        return pd.DataFrame()
    factset_df.columns = factset_df.columns.str.lower()
    return add_holdings_change_tracking_columns(
        factset_df,
        knowledge_date=knowledge_date,
    )


_ENRICHMENT_ONLY_COLUMNS = {
    "position_row_id",
    "next_report_date",
    "index_asof_date",
    "index_row_id",
    "factset_join_key_col",
    "index_join_key_col",
    "matched_key_value",
    "window_start_report_date",
    "window_end_report_date_exclusive",
    "knowledge_date",
    "upload_date",
    "holdings_snapshot_version",
}
_ENRICHMENT_ONLY_PREFIXES = ("index_", "ice_", "jpm_", "sp_")


def extract_reusable_factset_snapshot(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    if snapshot_df is None or snapshot_df.empty:
        return pd.DataFrame()
    snapshot = snapshot_df.copy()
    snapshot.columns = snapshot.columns.str.lower()
    derived_columns = [
        column
        for column in snapshot.columns
        if column in _ENRICHMENT_ONLY_COLUMNS or column.startswith(_ENRICHMENT_ONLY_PREFIXES)
    ]
    reusable = snapshot.drop(columns=derived_columns).drop_duplicates().reset_index(drop=True)
    reusable.attrs.update(snapshot_df.attrs)
    return reusable


def fetch_previous_enriched_factset_snapshot(
    asof_date: str,
    sql_table_name: str = DEFAULT_SQL_TABLE_NAME,
    databricks_app: str = BDC_APP,
) -> pd.DataFrame:
    asof_s = _normalize_date_string(asof_date)
    sql = f"""
SELECT *
FROM {sql_table_name}
WHERE CAST(knowledge_date AS DATE) = (
    SELECT MAX(CAST(knowledge_date AS DATE))
    FROM {sql_table_name}
    WHERE CAST(knowledge_date AS DATE) < DATE('{asof_s}')
)
"""
    previous = _read_bdc_databricks_sql(
        sql,
        msg=f"Previous enriched holdings snapshot before {asof_s}",
        databricks_app=databricks_app,
    )
    if previous is None or previous.empty:
        return pd.DataFrame()

    previous.columns = previous.columns.str.lower()
    if "holdings_snapshot_version" not in previous.columns:
        logging.info("Prior enriched slice has no holdings snapshot version; full refresh required.")
        return pd.DataFrame()
    versions = pd.to_numeric(previous["holdings_snapshot_version"], errors="coerce").dropna().unique()
    if versions.tolist() != [HOLDINGS_SNAPSHOT_VERSION]:
        logging.info(
            "Prior enriched slice version=%s does not match required version=%s; full refresh required.",
            versions.tolist(),
            HOLDINGS_SNAPSHOT_VERSION,
        )
        return pd.DataFrame()
    prior_date = pd.to_datetime(previous.get("knowledge_date"), errors="coerce").max()
    snapshot = extract_reusable_factset_snapshot(previous)
    if pd.notna(prior_date):
        snapshot.attrs["knowledge_date"] = prior_date.normalize()
    logging.info(
        "Reusing prior enriched holdings snapshot | prior_date=%s | target_date=%s | rows=%s | cols=%s",
        prior_date.strftime("%Y-%m-%d") if pd.notna(prior_date) else None,
        asof_s,
        f"{len(snapshot):,}",
        len(snapshot.columns),
    )
    return snapshot


def merge_refreshed_bdc_holdings(
    previous_snapshot: pd.DataFrame,
    refreshed_snapshot: pd.DataFrame,
    refreshed_bdc_ids=None,
) -> pd.DataFrame:
    if previous_snapshot is None or previous_snapshot.empty:
        return refreshed_snapshot.copy()
    refreshed_snapshot = refreshed_snapshot if refreshed_snapshot is not None else pd.DataFrame()

    key_col = "bdc_factset_entity_id"
    if key_col not in previous_snapshot.columns:
        raise RuntimeError(f"Cannot merge refreshed BDC holdings without {key_col}.")
    if refreshed_bdc_ids is None:
        if key_col not in refreshed_snapshot.columns:
            raise RuntimeError(f"Cannot merge refreshed BDC holdings without {key_col}.")
        refreshed_bdc_ids = refreshed_snapshot[key_col]
    refreshed_ids = {str(value) for value in refreshed_bdc_ids if pd.notna(value)}
    keep_mask = ~previous_snapshot[key_col].astype("string").isin(refreshed_ids)
    if refreshed_snapshot.empty:
        return previous_snapshot.loc[keep_mask].reset_index(drop=True)
    return pd.concat(
        [previous_snapshot.loc[keep_mask], refreshed_snapshot],
        ignore_index=True,
        sort=False,
    )


def prepare_factset_df(factset_df: pd.DataFrame, bbg_bridge: dict | None = None) -> pd.DataFrame:
    if factset_df is None or factset_df.empty:
        raise RuntimeError("FactSet holdings query returned no rows.")
    factset_df = factset_df.copy()
    factset_df.columns = factset_df.columns.str.lower()
    bbg_bridge = bbg_bridge or get_bbg_bridge()
    enrich_factset_bbg(factset_df, bbg_bridge)

    factset_df["report_date"] = pd.to_datetime(factset_df["report_date"], errors="coerce").dt.normalize()
    factset_df["asof_date"] = factset_df["report_date"]
    report_schedule = (
        factset_df[["bdc_factset_entity_id", "report_date"]]
        .drop_duplicates()
        .sort_values(["bdc_factset_entity_id", "report_date"])
        .reset_index(drop=True)
    )
    report_schedule["next_report_date"] = report_schedule.groupby("bdc_factset_entity_id")["report_date"].shift(-1)
    factset_df = factset_df.merge(
        report_schedule,
        on=["bdc_factset_entity_id", "report_date"],
        how="left",
    )
    sort_cols = [
        c
        for c in [
            "bdc_factset_entity_id",
            "report_date",
            "factset_report_id",
            "fsym_id",
            HOLDING_SOURCE_ROW_ID_COL,
        ]
        if c in factset_df.columns
    ]
    factset_df = factset_df.sort_values(sort_cols).reset_index(drop=True)
    factset_df["position_row_id"] = np.arange(1, len(factset_df) + 1)
    industry_cov = (
        factset_df["portfolio_industry_desc"].notna().mean()
        if "portfolio_industry_desc" in factset_df.columns
        else 0.0
    )
    logging.info(
        "factset_df rows=%s, positions=%s, bdc_industry_coverage=%.2f%%",
        f"{len(factset_df):,}",
        f"{factset_df['position_row_id'].nunique():,}",
        industry_cov * 100.0,
    )
    return factset_df


def _normalize_date_string(date_value: str) -> str:
    ts = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Invalid date value: {date_value}")
    return ts.normalize().strftime("%Y-%m-%d")


def resolve_holdings_update_from(
    asof_date: str,
    previous_snapshot_df: pd.DataFrame,
) -> str:
    asof_s = _normalize_date_string(asof_date)
    previous_date = pd.to_datetime(
        previous_snapshot_df.attrs.get("knowledge_date"),
        errors="coerce",
    )
    if pd.isna(previous_date) or previous_date.normalize() >= pd.Timestamp(asof_s):
        return asof_s
    return (previous_date.normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def add_holdings_change_tracking_columns(
    factset_df: pd.DataFrame,
    knowledge_date: str,
) -> pd.DataFrame:
    if factset_df is None or factset_df.empty:
        return pd.DataFrame(columns=list(factset_df.columns) if isinstance(factset_df, pd.DataFrame) else None)

    knowledge_s = _normalize_date_string(knowledge_date)
    out = factset_df.copy()
    if "collection_date" in out.columns:
        out["knowledge_date"] = pd.to_datetime(out["collection_date"], errors="coerce").dt.normalize()
        out["knowledge_date"] = out["knowledge_date"].fillna(
            pd.to_datetime(knowledge_s, errors="coerce").normalize()
        )
    else:
        out["knowledge_date"] = pd.to_datetime(knowledge_s, errors="coerce").normalize()
    out["snapshot_date"] = pd.to_datetime(knowledge_s, errors="coerce").normalize()
    out["upload_date"] = pd.Timestamp.now(tz="UTC").tz_localize(None)

    key_cols = [
        c
        for c in HOLDING_ROW_KEY_COLS
        if c in out.columns and out[c].notna().any()
    ]
    if not key_cols:
        key_cols = [c for c in HOLDING_ROW_FALLBACK_KEY_COLS if c in out.columns]
    if not key_cols:
        raise RuntimeError(
            "No holding row-key columns available. Expected FactSet holding id or "
            f"fallback columns: {HOLDING_ROW_FALLBACK_KEY_COLS}"
        )

    key_df = out[key_cols].copy()
    for col in key_cols:
        key_df[col] = key_df[col].astype("string").fillna("<NA>").str.strip()
    out["holding_row_key"] = key_df[key_cols].agg("|".join, axis=1)
    if out["holding_row_key"].duplicated(keep=False).any():
        out["holding_row_key_seq"] = (
            out.groupby("holding_row_key", dropna=False).cumcount() + 1
        ).astype("Int64")
        out["holding_row_key"] = (
            out["holding_row_key"]
            + "|SEQ="
            + out["holding_row_key_seq"].astype("string")
        )
    elif "holding_row_key_seq" not in out.columns:
        out["holding_row_key_seq"] = pd.Series(pd.NA, index=out.index, dtype="Int64")

    # last_update is an entity/fund-level touch timestamp, so it is useful metadata
    # but not definitive row-level CDC. Excluding it avoids false change events.
    hash_exclude_cols = {
        "upload_date",
        "snapshot_date",
        "last_update",
        "holding_row_hash",
        "prev_holding_row_hash",
    }
    hash_cols = [c for c in out.columns if c not in hash_exclude_cols]
    hash_df = out[hash_cols].copy()
    for col in hash_cols:
        if pd.api.types.is_datetime64_any_dtype(hash_df[col]):
            hash_df[col] = pd.to_datetime(hash_df[col], errors="coerce").astype("string")
        else:
            hash_df[col] = hash_df[col].astype("string")
    hash_df = hash_df.fillna("<NA>")
    out["holding_row_hash"] = (
        pd.util.hash_pandas_object(hash_df, index=False).astype("uint64").astype("string")
    )
    return out


def resolve_bootstrap_holdings_rows(
    holdings_updates_df: pd.DataFrame,
    factset_snapshot_df: pd.DataFrame,
    knowledge_date: str,
    bootstrap_holdings: bool,
) -> pd.DataFrame:
    if not bootstrap_holdings or not holdings_updates_df.empty:
        return holdings_updates_df
    logging.info(
        "No holdings updates on bootstrap date=%s; initializing from full as-of snapshot rows=%s",
        knowledge_date,
        f"{len(factset_snapshot_df):,}",
    )
    return add_holdings_change_tracking_columns(
        factset_snapshot_df,
        knowledge_date=knowledge_date,
    )


def select_incremental_holdings_rows(
    holdings_snapshot_df: pd.DataFrame,
    knowledge_date: str,
    holdings_sql_table_name: str = DEFAULT_HOLDINGS_SQL_TABLE_NAME,
    databricks_app: str = BDC_APP,
) -> pd.DataFrame:
    if holdings_snapshot_df is None:
        return pd.DataFrame()
    if holdings_snapshot_df.empty:
        return holdings_snapshot_df.copy()

    required_cols = {"holding_row_key", "holding_row_hash"}
    missing_cols = [c for c in required_cols if c not in holdings_snapshot_df.columns]
    if missing_cols:
        raise RuntimeError(f"Holdings snapshot missing required change-tracking columns: {missing_cols}")

    knowledge_s = _normalize_date_string(knowledge_date)
    prev_sql = f"""
SELECT holding_row_key, holding_row_hash
FROM (
    SELECT
        holding_row_key,
        holding_row_hash,
        ROW_NUMBER() OVER (
            PARTITION BY holding_row_key
            ORDER BY
                CAST(upload_date AS TIMESTAMP) DESC,
                CAST(last_update AS TIMESTAMP) DESC,
                CAST(knowledge_date AS DATE) DESC
        ) AS rn
    FROM {holdings_sql_table_name}
    WHERE holding_row_key IS NOT NULL
      AND holding_row_hash IS NOT NULL
) latest
WHERE rn = 1
"""
    try:
        prev_df = _read_bdc_databricks_sql(
            prev_sql,
            msg=f"Load latest holdings hashes before candidate compare knowledge_date={knowledge_s}",
            databricks_app=databricks_app,
        )
    except Exception as exc:
        logging.info(
            "No prior holdings snapshot available for incremental compare (table may not exist yet): %s",
            exc,
        )
        return holdings_snapshot_df.copy()

    if prev_df is None or prev_df.empty:
        logging.info("No prior holdings snapshot rows found before knowledge_date=%s", knowledge_s)
        return holdings_snapshot_df.copy()

    prev_key_hash_df = (
        prev_df[["holding_row_key", "holding_row_hash"]]
        .dropna(subset=["holding_row_key"])
        .drop_duplicates(subset=["holding_row_key"], keep="last")
        .rename(columns={"holding_row_hash": "prev_holding_row_hash"})
    )
    compare_df = holdings_snapshot_df[["holding_row_key", "holding_row_hash"]].merge(
        prev_key_hash_df,
        on="holding_row_key",
        how="left",
    )
    changed_mask = compare_df["prev_holding_row_hash"].isna() | (
        compare_df["holding_row_hash"] != compare_df["prev_holding_row_hash"]
    )
    incremental_df = holdings_snapshot_df.loc[changed_mask.to_numpy()].copy()
    logging.info(
        "Incremental holdings selection knowledge_date=%s rows=%s->%s",
        knowledge_s,
        f"{len(holdings_snapshot_df):,}",
        f"{len(incremental_df):,}",
    )
    return incremental_df


def persist_holdings_snapshot(
    holdings_snapshot_df: pd.DataFrame,
    knowledge_date: str,
    sql_table_name: str = DEFAULT_HOLDINGS_SQL_TABLE_NAME,
    delta_table_name: str = DEFAULT_HOLDINGS_DELTA_TABLE_NAME,
    delta_blob_dir: str = DEFAULT_HOLDINGS_DELTA_BLOB_DIR,
    bootstrap: bool = False,
    run_write: bool = False,
    use_spark_write: bool | None = None,
    sql_insert_batch_size: int = DEFAULT_SQL_INSERT_BATCH_SIZE,
    databricks_app: str = BDC_APP,
) -> pd.DataFrame:
    if not run_write:
        logging.info("RUN_WRITE_HOLDINGS=False. Skipping holdings Delta write.")
        return holdings_snapshot_df
    if holdings_snapshot_df is None or holdings_snapshot_df.empty:
        logging.info("Holdings snapshot empty; skipping holdings Delta write.")
        return holdings_snapshot_df

    if not getattr(sparkmanager, "isinit", False) or not hasattr(sparkmanager, "blob_dloader"):
        sparkmanager.init()
    use_spark_write = _resolve_use_spark_write(use_spark_write)
    sql_insert_batch_size = max(int(sql_insert_batch_size or DEFAULT_SQL_INSERT_BATCH_SIZE), 1)

    knowledge_s = _normalize_date_string(knowledge_date)
    out = holdings_snapshot_df.copy()
    dedupe_keys = [c for c in DEFAULT_HOLDINGS_DEDUPE_KEYS if c in out.columns]
    if dedupe_keys:
        out = out.drop_duplicates(subset=dedupe_keys, keep="first").reset_index(drop=True)

    datetime_cols = [
        "report_date",
        "asof_date",
        "publication_date",
        "collection_date",
        "last_update",
        "issue_date",
        "acquisition_date",
        "maturity_date",
        "dcs_report_date",
        "dcs_issue_date",
        "dcs_maturity_date",
        "next_report_date",
        "knowledge_date",
        "snapshot_date",
        "upload_date",
    ]
    for dt_col in datetime_cols:
        if dt_col in out.columns:
            out[dt_col] = pd.to_datetime(out[dt_col], errors="coerce")

    ssl_markers = (
        "spark connect tls handshake failed",
        "tls handshake failed",
        "certificate_verify_failed",
        "certificate verification",
        "self signed certificate in certificate chain",
        "ssl handshake failed",
        "statuscode.unavailable",
    )

    if bootstrap:
        _execute_bdc_databricks_sql(
            f"DROP TABLE IF EXISTS {sql_table_name}",
            msg=f"Drop holdings target table before bootstrap: {sql_table_name}",
            databricks_app=databricks_app,
        )
        target_blob_path = f"{delta_blob_dir.rstrip('/')}/{delta_table_name}"
        try:
            if sparkmanager.fileexists(delta_table_name, delta_blob_dir):
                logging.info("Deleting existing holdings Delta path before bootstrap: %s", target_blob_path)
                sparkmanager.blob_dloader.delete_dir(target_blob_path)
                try:
                    sparkmanager.blob_dloader.container_client.delete_blob(target_blob_path)
                except Exception:
                    pass
        except Exception as exc:
            logging.warning(
                "Could not delete existing holdings Delta path before bootstrap (continuing): %s",
                exc,
            )

        if not use_spark_write:
            target_path = sparkmanager._getfilepath(delta_table_name, delta_blob_dir)
            logging.info(
                "USE_SPARK_WRITE=False. Creating holdings table schema and inserting via Databricks SQL: %s",
                sql_table_name,
            )
            _create_delta_table_with_schema(
                df=out,
                sql_table_name=sql_table_name,
                target_path=target_path,
                databricks_app=databricks_app,
            )
            insertdf_batch(
                tablename=sql_table_name,
                df=out,
                msg=f"SQL-only bootstrap holdings insert knowledge_date={knowledge_s}",
                app=databricks_app,
                maxrow_batch=sql_insert_batch_size,
                cleantable=False,
            )
            logging.info(
                "Holdings bootstrap write complete (SQL-only insert): %s (knowledge_date=%s)",
                sql_table_name,
                knowledge_s,
            )
            return out

        try:
            sparkmanager.pandastodelta(
                out,
                delta_table_name,
                delta_blob_dir,
                partitioncolumn="knowledge_date",
                overwrite=True,
                new_schema=schema_hint(out),
            )
            target_path = sparkmanager._getfilepath(delta_table_name, delta_blob_dir)
            _execute_bdc_databricks_sql(
                f"""
CREATE TABLE IF NOT EXISTS {sql_table_name}
USING DELTA
LOCATION '{target_path}'
""",
                msg="Register holdings target table after bootstrap write",
                databricks_app=databricks_app,
            )
            logging.info(
                "Holdings bootstrap write complete: %s (knowledge_date=%s)",
                sql_table_name,
                knowledge_s,
            )
        except Exception as exc:
            exc_parts = [str(exc)]
            if getattr(exc, "__cause__", None) is not None:
                exc_parts.append(str(exc.__cause__))
            if getattr(exc, "__context__", None) is not None:
                exc_parts.append(str(exc.__context__))
            exc_msg = " | ".join(exc_parts).lower()
            if not any(marker in exc_msg for marker in ssl_markers):
                raise
            logging.warning(
                "Holdings bootstrap Spark Connect write failed due to TLS/SSL handshake; "
                "falling back to Databricks SQL INSERT batches for table=%s",
                sql_table_name,
            )
            target_path = sparkmanager._getfilepath(delta_table_name, delta_blob_dir)
            _create_delta_table_with_schema(
                df=out,
                sql_table_name=sql_table_name,
                target_path=target_path,
                databricks_app=databricks_app,
            )
            insertdf_batch(
                tablename=sql_table_name,
                df=out,
                msg=f"Fallback bootstrap holdings insert knowledge_date={knowledge_s}",
                app=databricks_app,
                maxrow_batch=sql_insert_batch_size,
                cleantable=False,
            )
            logging.info(
                "Holdings bootstrap write complete (fallback SQL insert): %s (knowledge_date=%s)",
                sql_table_name,
                knowledge_s,
            )
        return out

    try:
        target_probe_df = _read_bdc_databricks_sql(
            f"SELECT * FROM {sql_table_name} LIMIT 1",
            msg="Check holdings target table",
            databricks_app=databricks_app,
        )
    except Exception as exc:
        exc_msg = str(exc).lower()
        recreate_markers = (
            "delta_read_table_without_columns",
            "does not have any columns",
            "unresolved_column",
            "cannot be resolved",
        )
        if any(marker in exc_msg for marker in recreate_markers):
            logging.warning(
                "Holdings table exists but is unreadable; forcing bootstrap recreate: %s",
                exc,
            )
            return persist_holdings_snapshot(
                holdings_snapshot_df=out,
                knowledge_date=knowledge_s,
                sql_table_name=sql_table_name,
                delta_table_name=delta_table_name,
                delta_blob_dir=delta_blob_dir,
                bootstrap=True,
                run_write=True,
                use_spark_write=use_spark_write,
                sql_insert_batch_size=sql_insert_batch_size,
                databricks_app=databricks_app,
            )
        raise RuntimeError(
            f"Holdings target table missing or inaccessible: {sql_table_name}. Set bootstrap_holdings=True for first run."
        ) from exc

    spark_partitioncolumn = None
    if use_spark_write:
        sparkmanager.refresh_spark()
        spark_partitioncolumn = _resolve_existing_delta_partitioncolumn(
            sql_table_name,
            requested_partition_column="knowledge_date",
            databricks_app=databricks_app,
        )

    table_cols = {c.lower() for c in target_probe_df.columns}
    metadata_cols = [
        ("knowledge_date", "TIMESTAMP"),
        ("snapshot_date", "TIMESTAMP"),
        ("upload_date", "TIMESTAMP"),
        ("holding_row_key", "STRING"),
        ("holding_row_hash", "STRING"),
        ("holding_row_key_seq", "BIGINT"),
        ("collection_date", "TIMESTAMP"),
        ("last_update", "TIMESTAMP"),
        ("investment_name", "STRING"),
        ("reported_industry", "STRING"),
    ]
    for col_name, col_type in metadata_cols:
        if col_name.lower() in table_cols:
            continue
        try:
            _execute_bdc_databricks_sql(
                f"ALTER TABLE {sql_table_name} ADD COLUMNS ({col_name} {col_type})",
                msg=f"Add missing holdings metadata column {col_name}",
                databricks_app=databricks_app,
            )
        except Exception as exc:
            logging.warning(
                "Could not add holdings metadata column %s to %s (continuing): %s",
                col_name,
                sql_table_name,
                exc,
            )

    delete_sql = f"""
DELETE FROM {sql_table_name}
WHERE CAST(snapshot_date AS DATE) = DATE('{knowledge_s}')
"""
    _execute_bdc_databricks_sql(
        delete_sql,
        msg="Delete holdings snapshot_date slice before load",
        databricks_app=databricks_app,
    )
    if not use_spark_write:
        logging.info(
            "USE_SPARK_WRITE=False. Inserting holdings snapshot via Databricks SQL: %s",
            sql_table_name,
        )
        insertdf_batch(
            tablename=sql_table_name,
            df=out,
            msg=f"SQL-only holdings insert knowledge_date={knowledge_s}",
            app=databricks_app,
            maxrow_batch=sql_insert_batch_size,
            cleantable=False,
        )
        logging.info(
            "Holdings write complete (SQL-only insert): %s (knowledge_date=%s)",
            sql_table_name,
            knowledge_s,
        )
        return out

    try:
        sparkmanager.pandastodelta(
            out,
            delta_table_name,
            delta_blob_dir,
            partitioncolumn=spark_partitioncolumn,
            overwrite=False,
        )
        logging.info("Holdings write complete: %s (knowledge_date=%s)", sql_table_name, knowledge_s)
    except Exception as exc:
        exc_parts = [str(exc)]
        if getattr(exc, "__cause__", None) is not None:
            exc_parts.append(str(exc.__cause__))
        if getattr(exc, "__context__", None) is not None:
            exc_parts.append(str(exc.__context__))
        exc_msg = " | ".join(exc_parts).lower()
        if not any(marker in exc_msg for marker in ssl_markers):
            raise
        logging.warning(
            "Holdings Spark Connect write failed due to TLS/SSL handshake; "
            "falling back to Databricks SQL INSERT batches for table=%s",
            sql_table_name,
        )
        insertdf_batch(
            tablename=sql_table_name,
            df=out,
            msg=f"Fallback holdings insert knowledge_date={knowledge_s}",
            app=databricks_app,
            maxrow_batch=sql_insert_batch_size,
            cleantable=False,
        )
        logging.info(
            "Holdings write complete (fallback SQL insert): %s (knowledge_date=%s)",
            sql_table_name,
            knowledge_s,
        )
    return out


def select_factset_snapshot_for_asof_date(factset_df: pd.DataFrame, asof_date: str) -> pd.DataFrame:
    asof_ts = pd.to_datetime(asof_date, errors="coerce")
    if pd.isna(asof_ts):
        raise ValueError(f"Invalid asof_date: {asof_date}")
    asof_ts = asof_ts.normalize()

    required_cols = {"report_date", "next_report_date"}
    missing = [c for c in required_cols if c not in factset_df.columns]
    if missing:
        raise RuntimeError(f"FactSet dataframe missing required columns for asof snapshot: {missing}")

    snap = factset_df.loc[
        (factset_df["report_date"] <= asof_ts)
        & (factset_df["next_report_date"].isna() | (asof_ts < factset_df["next_report_date"]))
    ].copy()
    if snap.empty:
        raise RuntimeError(f"No FactSet holdings snapshot found for asof_date={asof_ts:%Y-%m-%d}")

    snap["asof_date"] = asof_ts
    snap = snap.sort_values(
        ["bdc_factset_entity_id", "report_date", "factset_report_id", "fsym_id"]
    ).reset_index(drop=True)
    logging.info(
        "factset snapshot asof=%s rows=%s positions=%s report_dates=%s",
        asof_ts.strftime("%Y-%m-%d"),
        f"{len(snap):,}",
        f"{snap['position_row_id'].nunique():,}" if "position_row_id" in snap.columns else "NA",
        f"{snap['report_date'].nunique():,}",
    )
    return snap


def build_pivoted_index_df(index_raw_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if index_raw_df is None or index_raw_df.empty:
        raise RuntimeError("Index constituent query returned no rows.")
    index_raw_df = index_raw_df.copy()
    index_raw_df.columns = index_raw_df.columns.str.lower()
    index_raw_df["asof_date"] = pd.to_datetime(index_raw_df["asof_date"], errors="coerce").dt.normalize()
    index_identifier_cols = INDEX_IDENTIFIER_COLS
    index_metric_cols = list(INDEX_METRIC_COLS)
    if "source_asof_date" in index_raw_df.columns:
        index_raw_df["source_asof_date"] = pd.to_datetime(
            index_raw_df["source_asof_date"], errors="coerce"
        ).dt.normalize()
        index_metric_cols = [*index_metric_cols, "source_asof_date"]

    missing_id_cols = [c for c in index_identifier_cols if c not in index_raw_df.columns]
    missing_metric_cols = [c for c in INDEX_METRIC_COLS if c not in index_raw_df.columns]
    if missing_id_cols:
        raise RuntimeError(f"Missing expected index identifier columns: {missing_id_cols}")
    if missing_metric_cols:
        raise RuntimeError(f"Missing expected index metric columns: {missing_metric_cols}")
    if SOURCE_COL not in index_raw_df.columns:
        raise RuntimeError(f"Missing expected index source column: {SOURCE_COL}")

    dedupe_exclude_cols = {"index_name", "index_desc"}
    dedupe_subset_cols = [c for c in index_raw_df.columns if c not in dedupe_exclude_cols]
    stable_sort_cols = [
        c
        for c in [
            "asof_date",
            "source_asof_date",
            "constituent_id",
            "cusip",
            "isin",
            "loanx_id",
            "ticker",
            "linked_instrument_id_bb",
            "bbg_match_source_key_type",
            "bbg_match_source_key_value",
            "instrument_id_bb",
            "id_bb8",
            SOURCE_COL,
        ]
        if c in index_raw_df.columns
    ]
    if stable_sort_cols:
        index_raw_dedup_df = (
            index_raw_df.sort_values(stable_sort_cols)
            .drop_duplicates(subset=dedupe_subset_cols, keep="first")
            .reset_index(drop=True)
        )
    else:
        index_raw_dedup_df = index_raw_df.drop_duplicates(subset=dedupe_subset_cols, keep="first").reset_index(drop=True)

    idx_work = index_raw_dedup_df[index_identifier_cols + [SOURCE_COL] + index_metric_cols].copy()
    idx_work[SOURCE_COL] = clean_key(idx_work[SOURCE_COL]).fillna("UNKNOWN")
    idx_grouped = idx_work.groupby(index_identifier_cols + [SOURCE_COL], dropna=False, as_index=False).agg(
        {c: "first" for c in index_metric_cols}
    )

    index_df = idx_grouped[index_identifier_cols].drop_duplicates().reset_index(drop=True)
    source_map = (
        idx_grouped.groupby(index_identifier_cols, dropna=False)[SOURCE_COL]
        .agg(lambda s: ",".join(sorted(pd.Series(s).dropna().astype(str).unique())))
        .reset_index(name="source")
    )
    index_df = index_df.merge(source_map, on=index_identifier_cols, how="left")

    present_sources = sorted(pd.Series(idx_grouped[SOURCE_COL]).dropna().astype(str).unique())
    pivot_metric_cols = [f"{s}_{m}" for s in present_sources for m in index_metric_cols]
    for src in present_sources:
        src_df = idx_grouped.loc[idx_grouped[SOURCE_COL] == src, index_identifier_cols + index_metric_cols].copy()
        src_df = src_df.rename(columns={m: f"{src}_{m}" for m in index_metric_cols})
        index_df = index_df.merge(src_df, on=index_identifier_cols, how="left")

    rename_map = {c: f"index_{c}" for c in index_identifier_cols}
    rename_map["source"] = "index_source"
    index_df = index_df.rename(columns=rename_map)
    id_cols_prefixed = [f"index_{c}" for c in index_identifier_cols] + ["index_source"]
    metric_cols_prefixed = [c for c in pivot_metric_cols if c in index_df.columns]
    other_cols = [c for c in index_df.columns if c not in set(id_cols_prefixed + metric_cols_prefixed)]
    index_df = index_df[id_cols_prefixed + metric_cols_prefixed + other_cols]
    index_df = index_df.reset_index(drop=True)
    index_df["index_row_id"] = np.arange(1, len(index_df) + 1)
    logging.info(
        "index_raw_rows=%s, index_dedup_rows=%s, index_pivot_rows=%s, pivot_metric_cols=%s",
        f"{len(index_raw_df):,}",
        f"{len(index_raw_dedup_df):,}",
        f"{len(index_df):,}",
        f"{len(metric_cols_prefixed):,}",
    )
    return index_df, metric_cols_prefixed


def _variant_map(series: pd.Series, key_col_name: str | None = None) -> dict:
    base = clean_key(series)
    alnum = base.str.replace(r"[^A-Z0-9]", "", regex=True)
    alnum = alnum.where(alnum.str.len() > 0)

    key_col_l = str(key_col_name or "").strip().lower()
    bbg_like_col = (
        ("bbg" in key_col_l)
        or ("instrument_id_bb" in key_col_l)
        or ("linked_instrument_id_bb" in key_col_l)
        or key_col_l.endswith("id_bb8")
    )
    if bbg_like_col:
        # SPGLOBAL can return Bloomberg ids with a CO prefix (e.g. COEH3905286);
        # emit a variant without that prefix so it can match EH3905286-style keys.
        co_prefix_strip2 = alnum.str[2:].where(alnum.str.startswith("CO", na=False))
        co_prefix_strip2 = co_prefix_strip2.where(co_prefix_strip2.str.len() > 0)
    else:
        co_prefix_strip2 = pd.Series(pd.NA, index=series.index, dtype="string")
    has_prefix = alnum.str[:2].str.match(r"^[A-Z]{2}$", na=False)
    isin_body = alnum.str[2:].where(has_prefix)
    isin_to_cusip9 = isin_body.str[:9]
    isin_to_cusip9 = isin_to_cusip9.where(isin_to_cusip9.str.len() == 9)
    return {
        "raw": base,
        "co_prefix_strip2": co_prefix_strip2,
        "isin_to_cusip9": isin_to_cusip9,
    }


def _explode_key_variants_chunked(
    df: pd.DataFrame,
    row_id_cols: list[str],
    key_cols: list[str],
    key_col_name: str,
    key_priority_map: dict,
    variant_col_name: str,
    allowed_values: set | None = None,
    chunk_size: int = 500_000,
    max_parallel_workers: int = 8,
) -> pd.DataFrame:
    n = len(df)

    def _explode_one_col(col_name: str):
        local_parts = []
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            rows = df.iloc[start:end]
            variants = _variant_map(rows[col_name], key_col_name=col_name)
            for vname, vseries in variants.items():
                if allowed_values is not None:
                    vseries = vseries.where(vseries.isin(allowed_values))
                valid = vseries.notna()
                if not valid.any():
                    continue
                part = rows.loc[valid, row_id_cols].copy()
                part[key_col_name] = col_name
                part[f"{key_col_name}_priority"] = key_priority_map.get(col_name, 999)
                part[variant_col_name] = vname
                part[f"{variant_col_name}_priority"] = 0 if vname == "raw" else 1
                part["key_value"] = vseries.loc[valid].to_numpy()
                local_parts.append(part)
        return local_parts

    out = []
    if max_parallel_workers > 1 and len(key_cols) > 1:
        max_workers = max(1, min(int(max_parallel_workers or 1), len(key_cols)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_explode_one_col, col): col for col in key_cols}
            for future in as_completed(futures):
                try:
                    out.extend(future.result())
                except Exception as exc:
                    logging.error("Key explode failed for col=%s: %s", futures[future], exc)
    else:
        for col in key_cols:
            out.extend(_explode_one_col(col))

    if not out:
        cols = row_id_cols + [
            key_col_name,
            f"{key_col_name}_priority",
            variant_col_name,
            f"{variant_col_name}_priority",
            "key_value",
        ]
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True).drop_duplicates()


def match_factset_to_index(
    factset_df: pd.DataFrame,
    index_df: pd.DataFrame,
    pivot_metric_cols: list[str],
    max_parallel_key_workers: int = 8,
    asof_calendar_override: pd.DataFrame | None = None,
    duckdb_memory_limit: str = "40GB",
    duckdb_threads: int = 4,
    duckdb_temp_directory: str | None = None,
    waterfall: bool = False,
    active_variants: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    factset_key_cols = [c for c in FACTSET_KEY_COLS if c in factset_df.columns]
    index_key_cols = [c for c in INDEX_KEY_COLS if c in index_df.columns]
    if not factset_key_cols:
        raise RuntimeError("No usable FactSet key columns found for matching.")
    if not index_key_cols:
        raise RuntimeError("No usable pivoted index key columns found for matching.")

    factset_key_priority = {c: i for i, c in enumerate(factset_key_cols, start=1)}
    index_key_priority = {c: i for i, c in enumerate(index_key_cols, start=1)}
    factset_key_long = _explode_key_variants_chunked(
        factset_df,
        ["position_row_id", "report_date", "next_report_date"],
        factset_key_cols,
        "factset_join_key_col",
        factset_key_priority,
        "factset_variant",
        allowed_values=None,
        chunk_size=200_000,
        max_parallel_workers=max_parallel_key_workers,
    )
    if factset_key_long.empty:
        raise RuntimeError("FactSet key explosion returned zero rows.")
    if active_variants:
        factset_key_long = factset_key_long[factset_key_long["factset_variant"].isin(active_variants)]
        if factset_key_long.empty:
            raise RuntimeError(f"FactSet key explosion empty after active_variants filter: {active_variants}")
    factset_key_values = set(factset_key_long["key_value"].dropna().unique())

    index_key_long = _explode_key_variants_chunked(
        index_df,
        ["index_row_id", "index_asof_date", "index_index_name", "index_source"],
        index_key_cols,
        "index_join_key_col",
        index_key_priority,
        "index_variant",
        allowed_values=factset_key_values,
        chunk_size=500_000,
        max_parallel_workers=max_parallel_key_workers,
    )
    if index_key_long.empty:
        raise RuntimeError("Pivoted index key explosion returned zero rows after key-universe filter.")
    if active_variants:
        index_key_long = index_key_long[index_key_long["index_variant"].isin(active_variants)]
        if index_key_long.empty:
            raise RuntimeError(f"Index key explosion empty after active_variants filter: {active_variants}")

    # Pre-merge dedup: per (row_id, key_value) keep only best-priority variant
    factset_key_long = (
        factset_key_long
        .sort_values(["factset_join_key_col_priority", "factset_variant_priority"])
        .drop_duplicates(subset=["position_row_id", "key_value"], keep="first")
    )
    index_key_long = (
        index_key_long
        .sort_values(["index_join_key_col_priority", "index_variant_priority"])
        .drop_duplicates(subset=["index_row_id", "key_value"], keep="first")
    )
    logging.info(
        "post-dedup factset_key_long=%s, index_key_long=%s",
        f"{len(factset_key_long):,}",
        f"{len(index_key_long):,}",
    )
    _log_memory_checkpoint(
        "post_key_explode_dedup",
        {
            "factset_key_long": factset_key_long,
            "index_key_long": index_key_long,
        },
    )

    def _apply_report_window(matches_df: pd.DataFrame) -> pd.DataFrame:
        if matches_df.empty:
            return matches_df
        out = matches_df.copy()
        out["report_date"] = pd.to_datetime(out["report_date"], errors="coerce").dt.normalize()
        out["next_report_date"] = pd.to_datetime(out["next_report_date"], errors="coerce").dt.normalize()
        out["index_asof_date"] = pd.to_datetime(out["index_asof_date"], errors="coerce").dt.normalize()
        return out.loc[
            (out["index_asof_date"] >= out["report_date"])
            & (out["next_report_date"].isna() | (out["index_asof_date"] < out["next_report_date"]))
        ].copy()

    if waterfall:
        matched_source_days: set[tuple] = set()
        match_parts = []
        for fk_col in factset_key_cols:
            fk_sub = factset_key_long[factset_key_long["factset_join_key_col"] == fk_col]
            if fk_sub.empty:
                continue
            fk_values = set(fk_sub["key_value"].dropna().unique())
            idx_sub = index_key_long[index_key_long["key_value"].isin(fk_values)]
            if idx_sub.empty:
                continue
            m = fk_sub.merge(idx_sub, on="key_value", how="inner")
            m = _apply_report_window(m)
            if m.empty:
                continue
            source_day_keys = list(
                zip(
                    m["position_row_id"],
                    m["report_date"],
                    m["index_asof_date"],
                    m["index_source"].fillna("UNKNOWN").astype(str),
                )
            )
            keep_mask = [key not in matched_source_days for key in source_day_keys]
            m = m.loc[keep_mask].copy()
            if m.empty:
                continue
            new_source_day_keys = set(
                zip(
                    m["position_row_id"],
                    m["report_date"],
                    m["index_asof_date"],
                    m["index_source"].fillna("UNKNOWN").astype(str),
                )
            )
            matched_source_days.update(new_source_day_keys)
            match_parts.append(m)
            logging.info(
                "waterfall key=%s new_positions=%d new_source_days=%d matched_source_days=%d merge_rows=%d",
                fk_col,
                m["position_row_id"].nunique(),
                len(new_source_day_keys),
                len(matched_source_days),
                len(m),
            )
        matches_ts = pd.concat(match_parts, ignore_index=True) if match_parts else pd.DataFrame()
        logging.info(
            "waterfall complete | total_match_rows=%s | matched_source_days=%d",
            f"{len(matches_ts):,}" if not matches_ts.empty else "0",
            len(matched_source_days),
        )
    else:
        matches_ts = factset_key_long.merge(index_key_long, on="key_value", how="inner")
    if matches_ts.empty:
        raise RuntimeError("No key matches between FactSet and pivoted index key tables.")
    matches_ts = _apply_report_window(matches_ts)
    if matches_ts.empty:
        raise RuntimeError("No key matches after applying report_date/asof_date window constraints.")

    matches_ts = matches_ts.sort_values(
        [
            "position_row_id",
            "report_date",
            "index_asof_date",
            "factset_variant_priority",
            "index_variant_priority",
            "factset_join_key_col_priority",
            "index_join_key_col_priority",
            "index_index_name",
            "index_row_id",
            "key_value",
        ]
    )
    matches_ts = matches_ts.drop_duplicates(
        subset=[
            "position_row_id",
            "report_date",
            "index_asof_date",
            "index_row_id",
            "factset_join_key_col",
            "index_join_key_col",
            "factset_variant",
            "index_variant",
            "key_value",
        ]
    ).reset_index(drop=True)

    best_day = (
        matches_ts.drop_duplicates(subset=["position_row_id", "report_date", "index_asof_date"], keep="first")
        .rename(columns={"key_value": "matched_key_value"})
    )
    join_summary = (
        best_day.groupby(["factset_join_key_col", "index_join_key_col"])
        .size()
        .reset_index(name="matched_pairs")
        .sort_values("matched_pairs", ascending=False)
        .reset_index(drop=True)
    )

    source_metric_cols = [c for c in pivot_metric_cols if c in index_df.columns]
    match_rows = (
        matches_ts[["position_row_id", "report_date", "index_asof_date", "index_row_id"]]
        .drop_duplicates()
        .merge(index_df[["index_row_id"] + source_metric_cols + ["index_source"]], on="index_row_id", how="left")
    )
    metric_by_day = (
        match_rows.sort_values(["position_row_id", "report_date", "index_asof_date", "index_row_id"])
        .groupby(["position_row_id", "report_date", "index_asof_date"], dropna=False, as_index=False)[source_metric_cols]
        .first()
    )
    source_by_day = (
        match_rows.groupby(["position_row_id", "report_date", "index_asof_date"], dropna=False)["index_source"]
        .agg(lambda s: ",".join(sorted({x for v in s.dropna().astype(str) for x in str(v).split(",") if x})))
        .reset_index(name="index_source_matched")
    )

    index_id_cols = [
        c
        for c in index_df.columns
        if c.startswith("index_") and c not in source_metric_cols and c not in ["index_row_id", "index_asof_date"]
    ]
    best_index_ids = best_day[
        [
            "position_row_id",
            "report_date",
            "index_asof_date",
            "index_row_id",
            "factset_join_key_col",
            "index_join_key_col",
            "matched_key_value",
        ]
    ].merge(
        index_df[["index_row_id", "index_asof_date"] + index_id_cols],
        on=["index_row_id", "index_asof_date"],
        how="left",
    )

    if asof_calendar_override is not None and not asof_calendar_override.empty:
        asof_calendar = (
            asof_calendar_override.copy()
            .assign(asof_date=lambda d: pd.to_datetime(d["asof_date"], errors="coerce").dt.normalize())
            .dropna(subset=["asof_date"])
            .drop_duplicates(subset=["asof_date"])
            .sort_values("asof_date")
            .reset_index(drop=True)
        )
    else:
        asof_calendar = (
            pd.DataFrame({"asof_date": pd.to_datetime(index_df["index_asof_date"], errors="coerce").dt.normalize()})
            .dropna()
            .drop_duplicates()
            .sort_values("asof_date")
            .reset_index(drop=True)
        )
    window_df = factset_df[["position_row_id", "report_date", "next_report_date"]].copy()
    window_df["report_date"] = pd.to_datetime(window_df["report_date"], errors="coerce").dt.normalize()
    window_df["next_report_date"] = pd.to_datetime(window_df["next_report_date"], errors="coerce").dt.normalize()

    # --- Use DuckDB for the expansion + all merges to avoid pandas OOM on ~30M rows ---
    factset_join_df = factset_df.drop(columns=["asof_date"], errors="ignore")
    _log_memory_checkpoint(
        "pre_duckdb_join",
        {
            "factset_join_df": factset_join_df,
            "best_index_ids": best_index_ids,
            "metric_by_day": metric_by_day,
            "source_by_day": source_by_day,
            "asof_calendar": asof_calendar,
        },
    )

    con = duckdb.connect()
    # Keep large joins stable by reducing thread pressure and allowing spill.
    try:
        con.execute(f"SET threads={max(1, int(duckdb_threads))}")
        con.execute("SET preserve_insertion_order=false")
        if duckdb_memory_limit:
            con.execute(f"SET memory_limit='{duckdb_memory_limit}'")
        temp_directory = duckdb_temp_directory or os.path.join(
            "/local_disk0/tmp" if os.environ.get("DATABRICKS_RUNTIME_VERSION") else tempfile.gettempdir(),
            "bdc_duckdb",
        )
        if temp_directory:
            os.makedirs(temp_directory, exist_ok=True)
            safe_temp_dir = str(temp_directory).replace("'", "''")
            con.execute(f"SET temp_directory='{safe_temp_dir}'")
    except Exception as exc:
        logging.warning("DuckDB runtime tuning skipped: %s", exc)
    con.register("window_df", window_df)
    con.register("asof_calendar", asof_calendar)
    con.register("factset_join_df", factset_join_df)
    con.register("best_index_ids", best_index_ids)
    con.register("metric_by_day", metric_by_day)
    con.register("source_by_day", source_by_day)

    result = con.sql(
        """
WITH expanded AS (
    SELECT w.position_row_id, c.asof_date
    FROM window_df w
    JOIN asof_calendar c
      ON c.asof_date >= w.report_date
     AND (w.next_report_date IS NULL OR c.asof_date < w.next_report_date)
),
holding_day_base AS (
    SELECT e.position_row_id, e.asof_date, f.*  EXCLUDE (position_row_id)
    FROM expanded e
    LEFT JOIN factset_join_df f ON e.position_row_id = f.position_row_id
),
with_index_ids AS (
    SELECT h.*, b.*  EXCLUDE (position_row_id, report_date)
    FROM holding_day_base h
    LEFT JOIN best_index_ids b
      ON h.position_row_id = b.position_row_id
     AND h.report_date = b.report_date
     AND h.asof_date = b.index_asof_date
),
with_metrics AS (
    SELECT w.*, m.*  EXCLUDE (position_row_id, report_date, index_asof_date)
    FROM with_index_ids w
    LEFT JOIN metric_by_day m
      ON w.position_row_id = m.position_row_id
     AND w.report_date = m.report_date
     AND w.asof_date = m.index_asof_date
),
with_source AS (
    SELECT wm.*, s.index_source_matched
    FROM with_metrics wm
    LEFT JOIN source_by_day s
      ON wm.position_row_id = s.position_row_id
     AND wm.report_date = s.report_date
     AND wm.asof_date = s.index_asof_date
)
SELECT * FROM with_source
"""
    ).df()
    con.close()
    _log_memory_checkpoint("post_duckdb_df_materialization", {"result": result})

    # Apply index_source_matched overlay
    if "index_source_matched" in result.columns:
        src_valid = result["index_source_matched"].notna() & (result["index_source_matched"].astype(str).str.strip() != "")
        result["index_source"] = result["index_source_matched"].where(src_valid, result.get("index_source"))
        result = result.drop(columns=["index_source_matched"], errors="ignore")

    result = result.reset_index(drop=True)
    if "index_instrument_id_bb" in result.columns:
        result["instrument_id_bb"] = result["index_instrument_id_bb"].fillna(result.get("instrument_id_bb"))
    if "index_id_bb8" in result.columns:
        result["id_bb8"] = result["index_id_bb8"].fillna(result.get("id_bb8"))

    result["asof_date"] = pd.to_datetime(result["asof_date"], errors="coerce").dt.normalize()
    result["window_start_report_date"] = result["report_date"]
    result["window_end_report_date_exclusive"] = result["next_report_date"]
    result = result.sort_values(["bdc_factset_entity_id", "report_date", "asof_date", "position_row_id"]).reset_index(drop=True)
    return result, join_summary, matches_ts, best_day


def _build_factset_key_universe(factset_df: pd.DataFrame) -> set[str]:
    candidate_cols = list(
        dict.fromkeys(
            FACTSET_KEY_COLS
            + [
                "bbg_id_new8",
                "bbg_id_new",
                "dcs_id_bb8",
                "dcs_id_bb",
                "instrument_id_bb",
                "id_bb8",
                "fsym_id",
            ]
        )
    )
    candidate_cols = [c for c in candidate_cols if c in factset_df.columns]
    key_values: set[str] = set()
    for col in candidate_cols:
        variants = _variant_map(factset_df[col], key_col_name=col)
        for v in variants.values():
            if v is None:
                continue
            non_null = v.dropna()
            if non_null.empty:
                continue
            key_values.update(non_null.astype(str).tolist())
    return key_values


def filter_index_raw_df_by_factset_keys(
    index_raw_df: pd.DataFrame,
    factset_key_values: set[str],
    chunk_size: int = 1_000_000,
) -> pd.DataFrame:
    if index_raw_df is None or index_raw_df.empty or not factset_key_values:
        return index_raw_df

    index_key_cols = [
        "instrument_id_bb",
        "id_bb8",
        "linked_instrument_id_bb",
        "bbg_match_source_key_value",
        "isin",
        "cusip",
        "loanx_id",
        "constituent_id",
        "ticker",
    ]
    index_key_cols = [c for c in index_key_cols if c in index_raw_df.columns]
    if not index_key_cols:
        return index_raw_df

    n = len(index_raw_df)
    keep_mask = np.zeros(n, dtype=bool)

    for col in index_key_cols:
        series = index_raw_df[col]
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            chunk = series.iloc[start:end]
            variants = _variant_map(chunk, key_col_name=col)
            chunk_mask = np.zeros(end - start, dtype=bool)
            for v in variants.values():
                if v is None:
                    continue
                chunk_mask |= v.isin(factset_key_values).fillna(False).to_numpy()
            if chunk_mask.any():
                keep_mask[start:end] |= chunk_mask

    filtered = index_raw_df.loc[keep_mask].copy()
    logging.info(
        "index prefilter rows=%s->%s using factset key universe=%s",
        f"{len(index_raw_df):,}",
        f"{len(filtered):,}",
        f"{len(factset_key_values):,}",
    )
    return filtered


def run_pipeline(
    from_date: str,
    to_date: str,
    run_asof_date: str | None = None,
    index_view: str = DEFAULT_INDEX_VIEW,
    chunk_months: int = 3,
    use_chunked_reads: bool = True,
    databricks_app: str = BDC_APP,
    enable_parallel_io: bool = True,
    max_parallel_workers: int = 8,
    max_parallel_key_workers: int = 8,
    filter_index_to_factset_keys: bool = True,
    index_prefilter_chunk_size: int = 1_000_000,
    index_roll_forward_days: int = DEFAULT_INDEX_ROLL_FORWARD_DAYS,
    waterfall: bool = False,
    active_variants: set[str] | None = None,
    holdings_sql_table_name: str = DEFAULT_HOLDINGS_SQL_TABLE_NAME,
    holdings_delta_table_name: str = DEFAULT_HOLDINGS_DELTA_TABLE_NAME,
    holdings_delta_blob_dir: str = DEFAULT_HOLDINGS_DELTA_BLOB_DIR,
    prior_enriched_sql_table_name: str = DEFAULT_SQL_TABLE_NAME,
    previous_factset_snapshot_df: pd.DataFrame | None = None,
    reuse_previous_enriched_snapshot: bool = True,
    bootstrap_holdings: bool = False,
    run_write_holdings: bool = False,
    use_spark_write: bool | None = None,
    sql_insert_batch_size: int = DEFAULT_SQL_INSERT_BATCH_SIZE,
) -> dict:
    pipeline_started_at = time.perf_counter()
    stage_timings: dict[str, float] = {}
    set_bdc_databricks_app(databricks_app)
    persisted_holdings_df = None
    holdings_knowledge_df = None
    changed_holdings_df = None
    if run_asof_date:
        asof_s = _normalize_date_string(run_asof_date)
        logging.info(
            "run_pipeline daily incremental mode | asof=%s | factset=latest_report_per_bdc<=asof",
            asof_s,
        )
    else:
        effective_factset_from = from_date
        effective_factset_to = to_date
        effective_index_from = from_date
        effective_index_to = to_date

    if run_asof_date:
        if previous_factset_snapshot_df is not None:
            previous_snapshot_df = extract_reusable_factset_snapshot(previous_factset_snapshot_df)
            logging.info(
                "Using in-memory prior FactSet snapshot | rows=%s | cols=%s",
                f"{len(previous_snapshot_df):,}",
                len(previous_snapshot_df.columns),
            )
        elif reuse_previous_enriched_snapshot:
            previous_snapshot_df = fetch_previous_enriched_factset_snapshot(
                asof_date=asof_s,
                sql_table_name=prior_enriched_sql_table_name,
                databricks_app=databricks_app,
            )
        else:
            previous_snapshot_df = pd.DataFrame()

        update_from = resolve_holdings_update_from(asof_s, previous_snapshot_df)
        holdings_knowledge_df = fetch_factset_holdings_knowledge(
            update_from=update_from,
            update_to=asof_s,
            asof_date=asof_s,
            databricks_app=databricks_app,
        )
        changed_holdings_df = select_incremental_holdings_rows(
            holdings_snapshot_df=holdings_knowledge_df,
            knowledge_date=asof_s,
            holdings_sql_table_name=holdings_sql_table_name,
            databricks_app=databricks_app,
        )

        if previous_snapshot_df.empty:
            factset_raw_df = get_factset_bdc_holdings_asof(asof_s)
            holdings_source = "factset_full_snapshot"
        elif holdings_knowledge_df.empty:
            factset_raw_df = previous_snapshot_df
            holdings_source = "previous_enriched_snapshot"
        else:
            affected_bdc_ids = holdings_knowledge_df["bdc_factset_entity_id"].dropna().unique()
            refreshed_snapshot_df = get_factset_bdc_holdings_asof(
                asof_s,
                factset_entity_ids=affected_bdc_ids,
            )
            factset_raw_df = merge_refreshed_bdc_holdings(
                previous_snapshot=previous_snapshot_df,
                refreshed_snapshot=refreshed_snapshot_df,
                refreshed_bdc_ids=affected_bdc_ids,
            )
            holdings_source = "previous_snapshot_plus_refreshed_bdcs"

        bbg_bridge = get_bbg_bridge()
        factset_df = prepare_factset_df(factset_raw_df, bbg_bridge=bbg_bridge)
        holdings_knowledge_df = resolve_bootstrap_holdings_rows(
            holdings_updates_df=holdings_knowledge_df,
            factset_snapshot_df=factset_raw_df,
            knowledge_date=asof_s,
            bootstrap_holdings=bootstrap_holdings,
        )
        if bootstrap_holdings and changed_holdings_df.empty:
            changed_holdings_df = holdings_knowledge_df.copy()
        factset_df["holdings_snapshot_version"] = HOLDINGS_SNAPSHOT_VERSION
        factset_snapshot_df = factset_df.copy()
        logging.info(
            "Daily holdings snapshot ready | asof=%s | source=%s | rows=%s | cols=%s",
            asof_s,
            holdings_source,
            f"{len(factset_df):,}",
            len(factset_df.columns),
        )
        stage_timings["holdings_snapshot_s"] = round(
            time.perf_counter() - pipeline_started_at,
            3,
        )
        logging.info(
            "pipeline_timing stage=holdings_snapshot elapsed_s=%.3f rows=%s",
            stage_timings["holdings_snapshot_s"],
            len(factset_df),
        )
        if run_write_holdings:
            holdings_write_started_at = time.perf_counter()
            persisted_holdings_df = persist_holdings_snapshot(
                holdings_snapshot_df=holdings_knowledge_df,
                knowledge_date=asof_s,
                sql_table_name=holdings_sql_table_name,
                delta_table_name=holdings_delta_table_name,
                delta_blob_dir=holdings_delta_blob_dir,
                bootstrap=bootstrap_holdings,
                run_write=True,
                use_spark_write=use_spark_write,
                sql_insert_batch_size=sql_insert_batch_size,
                databricks_app=databricks_app,
            )
            stage_timings["holdings_write_s"] = round(
                time.perf_counter() - holdings_write_started_at,
                3,
            )
        if changed_holdings_df.empty:
            logging.info(
                "No holdings changes detected for knowledge_date=%s. Continuing daily enrichment with full latest FactSet snapshot.",
                asof_s,
            )
        # Daily mode writes one knowledge-date slice. The full latest FactSet
        # snapshot feeds enrichment, while holdings_knowledge_df handles CDC.
        index_lookback_days = max(int(index_roll_forward_days or 0), 0)
        effective_index_from = (
            pd.to_datetime(asof_s, errors="coerce").normalize()
            - pd.Timedelta(days=index_lookback_days)
        ).strftime("%Y-%m-%d")
        effective_index_to = asof_s
        logging.info(
            "Daily incremental index window | knowledge_date=%s | index_from=%s | index_to=%s | index_roll_forward_days=%s | holdings_candidates=%s | changed_holdings=%s",
            asof_s,
            effective_index_from,
            effective_index_to,
            index_lookback_days,
            f"{len(holdings_knowledge_df):,}",
            f"{len(changed_holdings_df):,}",
        )
        index_started_at = time.perf_counter()
        index_raw_df = fetch_index_constituent_stats_direct(
            from_date=effective_index_from,
            to_date=effective_index_to,
            bbg_bridge=bbg_bridge,
            factset_bbg_ids=collect_factset_bbg_ids(factset_df),
        )
        index_raw_df = roll_forward_index_constituent_stats(index_raw_df, target_date=asof_s)
        stage_timings["index_source_s"] = round(
            time.perf_counter() - index_started_at,
            3,
        )
        logging.info(
            "pipeline_timing stage=index_source elapsed_s=%.3f rows=%s",
            stage_timings["index_source_s"],
            len(index_raw_df),
        )
    elif use_chunked_reads:
        factset_df = fetch_factset_holdings_chunked(
            effective_factset_from,
            effective_factset_to,
            chunk_months=chunk_months,
            enable_parallel=enable_parallel_io,
            max_parallel_workers=max_parallel_workers,
        )
        index_raw_df = fetch_index_constituent_stats_chunked(
            index_view=index_view,
            from_date=effective_index_from,
            to_date=effective_index_to,
            chunk_months=chunk_months,
            databricks_app=databricks_app,
            enable_parallel=enable_parallel_io,
            max_parallel_workers=max_parallel_workers,
        )
    else:
        factset_df = get_factset_bdc_holdings(effective_factset_from, effective_factset_to)
        if factset_df is not None and not factset_df.empty:
            factset_df.columns = factset_df.columns.str.lower()
        index_sql = f"""
SELECT *
FROM {index_view}
WHERE asof_date >= '{effective_index_from}'
  AND asof_date <= '{effective_index_to}'
"""
        index_raw_df = _read_bdc_databricks_sql(
            index_sql,
            msg="v_index_constituent_stats",
            databricks_app=databricks_app,
        )
        if index_raw_df is not None and not index_raw_df.empty:
            index_raw_df.columns = index_raw_df.columns.str.lower()

    _log_memory_checkpoint(
        "post_source_fetch",
        {
            "factset_raw": factset_df if isinstance(factset_df, pd.DataFrame) else pd.DataFrame(),
            "index_raw": index_raw_df if isinstance(index_raw_df, pd.DataFrame) else pd.DataFrame(),
        },
    )
    if index_raw_df is None or index_raw_df.empty:
        if run_asof_date:
            raise RuntimeError(
                f"No index rows available for {effective_index_from}..{effective_index_to}; "
                "daily enrichment cannot publish an empty snapshot."
            )
        stage_timings["pipeline_total_s"] = round(
            time.perf_counter() - pipeline_started_at,
            3,
        )
        logging.info(
            "No index rows available for index window %s..%s. Returning empty enrichment bundle.",
            effective_index_from,
            effective_index_to,
        )
        empty_df = pd.DataFrame()
        factset_df = factset_df if isinstance(factset_df, pd.DataFrame) else empty_df
        if not run_asof_date:
            factset_snapshot_df = factset_df.copy()
        qa_summary = pd.DataFrame(
            [
                {
                    "factset_snapshot_rows": int(len(factset_snapshot_df)),
                    "holdings_knowledge_rows": int(len(holdings_knowledge_df)) if holdings_knowledge_df is not None else 0,
                    "changed_holdings_rows": int(len(changed_holdings_df)) if changed_holdings_df is not None else 0,
                    "factset_rows": int(len(factset_df)),
                    "index_rows": 0,
                    "result_rows": 0,
                    "matches_rows": 0,
                    "matched_positions": 0,
                    "matched_position_days": 0,
                    "total_position_days": 0,
                    "matched_position_days_pct": 0.0,
                }
            ]
        )
        return {
            "factset_df": factset_df,
            "factset_snapshot_df": factset_snapshot_df,
            "index_raw_df": empty_df,
            "index_raw_df_filtered": empty_df,
            "index_df": empty_df,
            "result": empty_df,
            "join_summary": empty_df,
            "matches_ts": empty_df,
            "best_day": empty_df,
            "pivot_metric_cols": [],
            "qa_summary": qa_summary,
            "holdings_knowledge_df": holdings_knowledge_df,
            "changed_holdings_df": changed_holdings_df,
            "persisted_holdings": persisted_holdings_df,
            "stage_timings": stage_timings,
        }

    if run_asof_date:
        factset_snapshot_df = factset_snapshot_df.sort_values(
            ["bdc_factset_entity_id", "report_date", "factset_report_id", "fsym_id"]
        ).reset_index(drop=True)
    else:
        factset_df = prepare_factset_df(factset_df)
        factset_snapshot_df = factset_df.copy()
    _log_memory_checkpoint("post_prepare_factset_df", {"factset_df": factset_df})

    full_asof_calendar = (
        pd.DataFrame({"asof_date": pd.to_datetime(index_raw_df["asof_date"], errors="coerce").dt.normalize()})
        .dropna()
        .drop_duplicates()
        .sort_values("asof_date")
        .reset_index(drop=True)
    )
    if full_asof_calendar.empty:
        raise RuntimeError("No index asof_date values available to build asof calendar.")
    if run_asof_date:
        asof_limit = pd.to_datetime(asof_s, errors="coerce").normalize()
        min_changed_report_date = pd.to_datetime(factset_df["report_date"], errors="coerce").dropna().min()
        if pd.isna(min_changed_report_date):
            raise RuntimeError(
                f"Could not derive changed holdings report_date lower bound for knowledge_date={asof_s}."
            )
        min_changed_report_date = min_changed_report_date.normalize()
        full_asof_calendar = (
            full_asof_calendar.loc[
                (full_asof_calendar["asof_date"] >= min_changed_report_date)
                & (full_asof_calendar["asof_date"] <= asof_limit)
            ]
            .drop_duplicates()
            .reset_index(drop=True)
        )
        if full_asof_calendar.empty:
            raise RuntimeError(
                "No index asof_date values available inside changed-holdings window "
                f"[{min_changed_report_date:%Y-%m-%d}, {asof_s}]."
            )

    index_raw_df_filtered = index_raw_df
    stage_started_at = time.perf_counter()
    if filter_index_to_factset_keys:
        factset_key_values = _build_factset_key_universe(factset_df)
        index_raw_df_filtered = filter_index_raw_df_by_factset_keys(
            index_raw_df=index_raw_df,
            factset_key_values=factset_key_values,
            chunk_size=index_prefilter_chunk_size,
        )
        if index_raw_df_filtered is None or index_raw_df_filtered.empty:
            logging.warning(
                "Index prefilter produced no rows; falling back to unfiltered index_raw_df for safety."
            )
            index_raw_df_filtered = index_raw_df
    stage_timings["index_prefilter_s"] = round(
        time.perf_counter() - stage_started_at,
        3,
    )
    logging.info(
        "pipeline_timing stage=index_prefilter elapsed_s=%.3f rows=%s",
        stage_timings["index_prefilter_s"],
        len(index_raw_df_filtered),
    )
    _log_memory_checkpoint("post_index_prefilter", {"index_raw_df_filtered": index_raw_df_filtered})

    stage_started_at = time.perf_counter()
    index_df, pivot_metric_cols = build_pivoted_index_df(index_raw_df_filtered)
    stage_timings["index_pivot_s"] = round(
        time.perf_counter() - stage_started_at,
        3,
    )
    logging.info(
        "pipeline_timing stage=index_pivot elapsed_s=%.3f rows=%s",
        stage_timings["index_pivot_s"],
        len(index_df),
    )
    _log_memory_checkpoint("post_build_pivoted_index_df", {"index_df": index_df})
    stage_started_at = time.perf_counter()
    result, join_summary, matches_ts, best_day = match_factset_to_index(
        factset_df=factset_df,
        index_df=index_df,
        pivot_metric_cols=pivot_metric_cols,
        max_parallel_key_workers=max_parallel_key_workers,
        asof_calendar_override=full_asof_calendar,
        waterfall=waterfall,
        active_variants=active_variants,
    )
    stage_timings["multivariant_match_s"] = round(
        time.perf_counter() - stage_started_at,
        3,
    )
    logging.info(
        "pipeline_timing stage=multivariant_match elapsed_s=%.3f result_rows=%s matched_positions=%s",
        stage_timings["multivariant_match_s"],
        len(result),
        best_day["position_row_id"].nunique(),
    )
    _log_memory_checkpoint(
        "post_match_factset_to_index",
        {
            "result": result,
            "matches_ts": matches_ts,
            "best_day": best_day,
        },
    )

    matched_position_days = best_day[["position_row_id", "report_date", "index_asof_date"]].drop_duplicates().shape[0]
    total_position_days = len(result[["position_row_id", "report_date", "asof_date"]].drop_duplicates())
    matched_positions = best_day["position_row_id"].nunique()
    qa_summary = pd.DataFrame(
        [
            {
                "factset_snapshot_rows": int(len(factset_snapshot_df)),
                "holdings_knowledge_rows": int(len(holdings_knowledge_df)) if holdings_knowledge_df is not None else 0,
                "changed_holdings_rows": int(len(changed_holdings_df)) if changed_holdings_df is not None else 0,
                "factset_rows": int(len(factset_df)),
                "index_rows": int(len(index_df)),
                "result_rows": int(len(result)),
                "matches_rows": int(len(matches_ts)),
                "matched_positions": int(matched_positions),
                "matched_position_days": int(matched_position_days),
                "total_position_days": int(total_position_days),
                "matched_position_days_pct": float(matched_position_days / max(total_position_days, 1)),
            }
        ]
    )
    stage_timings["pipeline_total_s"] = round(
        time.perf_counter() - pipeline_started_at,
        3,
    )
    logging.info(
        "pipeline_timing stage=pipeline_total elapsed_s=%.3f result_rows=%s",
        stage_timings["pipeline_total_s"],
        len(result),
    )
    return {
        "factset_df": factset_df,
        "factset_snapshot_df": factset_snapshot_df,
        "index_raw_df": index_raw_df,
        "index_raw_df_filtered": index_raw_df_filtered,
        "index_df": index_df,
        "result": result,
        "join_summary": join_summary,
        "matches_ts": matches_ts,
        "best_day": best_day,
        "pivot_metric_cols": pivot_metric_cols,
        "qa_summary": qa_summary,
        "holdings_knowledge_df": holdings_knowledge_df,
        "changed_holdings_df": changed_holdings_df,
        "persisted_holdings": persisted_holdings_df,
        "stage_timings": stage_timings,
    }


def _overwrite_bootstrap_result(
    result_df: pd.DataFrame,
    delta_table_name: str,
    delta_blob_dir: str,
) -> None:
    sparkmanager.refresh_spark()
    partition_overwrite_key = "spark.sql.sources.partitionOverwriteMode"
    previous_mode = sparkmanager._spark.conf.get(partition_overwrite_key, "dynamic")
    sparkmanager._spark.conf.set(partition_overwrite_key, "static")
    try:
        sparkmanager.pandastodelta(
            result_df,
            delta_table_name,
            delta_blob_dir,
            partitioncolumn=DEFAULT_RESULT_PARTITION_COLUMN,
            overwrite=True,
            new_schema=schema_hint(result_df),
        )
    finally:
        sparkmanager._spark.conf.set(partition_overwrite_key, previous_mode)


def _replace_spark_result_slice(
    out: pd.DataFrame,
    sql_table_name: str,
    predicate: str,
) -> None:
    """Commit a complete daily slice atomically, preserving it if the write fails."""
    schema = sparkmanager._spark.table(sql_table_name).schema
    spark_df = sparkmanager.pandastospark(out, schema=schema)
    (
        spark_df.write.format("delta")
        .mode("overwrite")
        .option("partitionOverwriteMode", "static")
        .option("replaceWhere", predicate)
        .saveAsTable(sql_table_name)
    )
    logging.info("Atomically replaced enriched slice | table=%s | %s", sql_table_name, predicate)


def persist_result(
    result: pd.DataFrame,
    from_date: str,
    to_date: str,
    sql_table_name: str = DEFAULT_SQL_TABLE_NAME,
    delta_table_name: str = DEFAULT_DELTA_TABLE_NAME,
    delta_blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    result_dedupe_keys: list[str] | None = None,
    bootstrap: bool = False,
    run_write: bool = False,
    databricks_app: str = BDC_APP,
    delete_by_asof_date: bool = False,
    asof_date_from: str | None = None,
    asof_date_to: str | None = None,
    delete_by_knowledge_date: bool = False,
    knowledge_date_from: str | None = None,
    knowledge_date_to: str | None = None,
    knowledge_date_override: str | None = None,
    use_spark_write: bool | None = None,
    sql_insert_batch_size: int = DEFAULT_SQL_INSERT_BATCH_SIZE,
) -> pd.DataFrame:
    if not run_write:
        logging.info("RUN_WRITE=False. Skipping Delta write.")
        return result

    if result.empty:
        raise RuntimeError("Refusing to replace an enriched snapshot with an empty result.")

    if not getattr(sparkmanager, "isinit", False) or not hasattr(sparkmanager, "blob_dloader"):
        sparkmanager.init()
    use_spark_write = _resolve_use_spark_write(use_spark_write)
    sql_insert_batch_size = max(int(sql_insert_batch_size or DEFAULT_SQL_INSERT_BATCH_SIZE), 1)

    out = result.copy()
    run_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
    if knowledge_date_override is not None:
        out["knowledge_date"] = pd.to_datetime(knowledge_date_override, errors="coerce").normalize()
    elif "asof_date" in out.columns:
        out["knowledge_date"] = pd.to_datetime(out["asof_date"], errors="coerce")
    else:
        out["knowledge_date"] = run_ts
    out["upload_date"] = run_ts

    dedupe_keys = [c for c in (result_dedupe_keys or DEFAULT_RESULT_DEDUPE_KEYS) if c in out.columns]
    if dedupe_keys:
        before_rows = len(out)
        out = out.drop_duplicates(subset=dedupe_keys, keep="first").reset_index(drop=True)
        logging.info("deduped result rows=%s->%s using keys=%s", f"{before_rows:,}", f"{len(out):,}", dedupe_keys)

    datetime_cols = [
        "report_date",
        "asof_date",
        "publication_date",
        "collection_date",
        "last_update",
        "issue_date",
        "acquisition_date",
        "maturity_date",
        "dcs_report_date",
        "dcs_issue_date",
        "dcs_maturity_date",
        "ICE_maturity_date",
        "JPM_maturity_date",
        "SP_maturity_date",
        "ICE_source_asof_date",
        "JPM_source_asof_date",
        "SP_source_asof_date",
        "next_report_date",
        "window_start_report_date",
        "window_end_report_date_exclusive",
        "index_asof_date",
        "knowledge_date",
        "upload_date",
    ]
    for dt_col in datetime_cols:
        if dt_col in out.columns:
            out[dt_col] = pd.to_datetime(out[dt_col], errors="coerce")
    for int_col in ["position_row_id", "index_row_id", "factset_report_id", "period_type_code"]:
        if int_col in out.columns:
            out[int_col] = pd.to_numeric(out[int_col], errors="coerce").astype("Int64")
    if "terminated_flag" in out.columns:
        out["terminated_flag"] = out["terminated_flag"].astype("boolean")
    if "min_coupon_margin" in out.columns:
        out["min_coupon_margin"] = pd.to_numeric(out["min_coupon_margin"], errors="coerce")

    if bootstrap:
        if not use_spark_write:
            target_path = sparkmanager._getfilepath(delta_table_name, delta_blob_dir)
            logging.info(
                "USE_SPARK_WRITE=False. Creating result table schema and inserting via Databricks SQL: %s",
                sql_table_name,
            )
            _create_delta_table_with_schema(
                df=out,
                sql_table_name=sql_table_name,
                target_path=target_path,
                databricks_app=databricks_app,
            )
            insertdf_batch(
                tablename=sql_table_name,
                df=out,
                msg="SQL-only bootstrap result insert",
                app=databricks_app,
                maxrow_batch=sql_insert_batch_size,
                cleantable=False,
            )
            logging.info("Bootstrap write complete (SQL-only insert): %s", sql_table_name)
            return out

        _overwrite_bootstrap_result(out, delta_table_name, delta_blob_dir)
        target_path = sparkmanager._getfilepath(delta_table_name, delta_blob_dir)
        create_sql = f"""
CREATE TABLE IF NOT EXISTS {sql_table_name}
USING DELTA
LOCATION '{target_path}'
"""
        _execute_bdc_databricks_sql(
            create_sql,
            msg="Register target table after bootstrap write",
            databricks_app=databricks_app,
        )
        logging.info("Bootstrap write complete: %s", sql_table_name)
        return out

    try:
        target_probe_df = _read_bdc_databricks_sql(
            f"SELECT * FROM {sql_table_name} LIMIT 1",
            msg="Check target table",
            databricks_app=databricks_app,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Target table missing or inaccessible: {sql_table_name}. Set bootstrap=True for first run."
        ) from exc

    spark_partitioncolumn = None
    if use_spark_write:
        sparkmanager.refresh_spark()
        spark_partitioncolumn = _resolve_existing_delta_partitioncolumn(
            sql_table_name,
            requested_partition_column=DEFAULT_RESULT_PARTITION_COLUMN,
            databricks_app=databricks_app,
        )

    table_cols = {c.lower() for c in target_probe_df.columns}
    metadata_cols = [
        ("collection_date", "TIMESTAMP"),
        ("last_update", "TIMESTAMP"),
        ("knowledge_date", "TIMESTAMP"),
        ("upload_date", "TIMESTAMP"),
        ("ICE_source_asof_date", "TIMESTAMP"),
        ("JPM_source_asof_date", "TIMESTAMP"),
        ("SP_source_asof_date", "TIMESTAMP"),
        ("holdings_snapshot_version", "BIGINT"),
        ("investment_name", "STRING"),
        ("reported_industry", "STRING"),
    ]
    for col_name, col_type in metadata_cols:
        if col_name.lower() in table_cols:
            continue
        alter_sql = f"ALTER TABLE {sql_table_name} ADD COLUMNS ({col_name} {col_type})"
        try:
            _execute_bdc_databricks_sql(
                alter_sql,
                msg=f"Add missing metadata column {col_name}",
                databricks_app=databricks_app,
            )
        except Exception as exc:
            logging.warning(
                "Could not add metadata column %s to %s (continuing): %s",
                col_name,
                sql_table_name,
                exc,
            )

    if delete_by_knowledge_date:
        delete_from = knowledge_date_from or knowledge_date_override or from_date
        delete_to = knowledge_date_to or knowledge_date_override or to_date
        if use_spark_write:
            _replace_spark_result_slice(
                out,
                sql_table_name,
                f"CAST(knowledge_date AS DATE) >= DATE('{delete_from}') "
                f"AND CAST(knowledge_date AS DATE) <= DATE('{delete_to}')",
            )
            return out
        delete_sql = f"""
DELETE FROM {sql_table_name}
WHERE CAST(knowledge_date AS DATE) >= DATE('{delete_from}')
  AND CAST(knowledge_date AS DATE) <= DATE('{delete_to}')
"""
        delete_mode = "knowledge_date"
    elif delete_by_asof_date:
        delete_from = asof_date_from or from_date
        delete_to = asof_date_to or to_date
        delete_sql = f"""
DELETE FROM {sql_table_name}
WHERE CAST(asof_date AS DATE) >= DATE('{delete_from}')
  AND CAST(asof_date AS DATE) <= DATE('{delete_to}')
"""
        delete_mode = "asof_date"
    else:
        delete_from = from_date
        delete_to = to_date
        delete_sql = f"""
DELETE FROM {sql_table_name}
WHERE CAST(report_date AS DATE) >= DATE('{from_date}')
  AND CAST(report_date AS DATE) <= DATE('{to_date}')
"""
        delete_mode = "report_date"
    logging.info(
        "Deleting existing target slice before load | mode=%s | from=%s | to=%s",
        delete_mode,
        delete_from,
        delete_to,
    )
    _execute_bdc_databricks_sql(
        delete_sql,
        msg="Delete target window before load",
        databricks_app=databricks_app,
    )
    if not use_spark_write:
        logging.info(
            "USE_SPARK_WRITE=False. Inserting result via Databricks SQL: %s",
            sql_table_name,
        )
        insertdf_batch(
            tablename=sql_table_name,
            df=out,
            msg="SQL-only insert after target slice delete",
            app=databricks_app,
            maxrow_batch=sql_insert_batch_size,
            cleantable=False,
        )
        logging.info("Write complete (SQL-only insert after window delete): %s", sql_table_name)
        return out

    try:
        sparkmanager.pandastodelta(
            out,
            delta_table_name,
            delta_blob_dir,
            partitioncolumn=spark_partitioncolumn,
            overwrite=False,
        )
        logging.info("Write complete (direct append after window delete): %s", sql_table_name)
    except Exception as exc:
        exc_parts = [str(exc)]
        if getattr(exc, "__cause__", None) is not None:
            exc_parts.append(str(exc.__cause__))
        if getattr(exc, "__context__", None) is not None:
            exc_parts.append(str(exc.__context__))
        exc_msg = " | ".join(exc_parts).lower()
        ssl_markers = (
            "spark connect tls handshake failed",
            "tls handshake failed",
            "certificate_verify_failed",
            "certificate verification",
            "self signed certificate in certificate chain",
            "ssl handshake failed",
            "statuscode.unavailable",
        )
        if not any(marker in exc_msg for marker in ssl_markers):
            raise

        logging.warning(
            "Spark Connect write failed due to TLS/SSL handshake; "
            "falling back to Databricks SQL INSERT batches for table=%s",
            sql_table_name,
        )
        insertdf_batch(
            tablename=sql_table_name,
            df=out,
            msg="Fallback insert after Spark Connect TLS failure",
            app=databricks_app,
            maxrow_batch=sql_insert_batch_size,
            cleantable=False,
        )
        logging.info("Write complete (fallback SQL insert after window delete): %s", sql_table_name)
    return out
