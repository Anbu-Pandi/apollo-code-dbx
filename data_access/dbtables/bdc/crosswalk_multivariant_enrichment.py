import logging
import time

import numpy as np
import pandas as pd

from data_access.dbtables.bdc import BDC_APP, FACTSET_APP, SPGLOBAL_APP
from data_access.dbtables.bdc.factset_bdc_enriched import (
    DEFAULT_DELTA_BLOB_DIR,
    DEFAULT_DELTA_TABLE_NAME,
    DEFAULT_HOLDINGS_DELTA_BLOB_DIR,
    DEFAULT_HOLDINGS_DELTA_TABLE_NAME,
    DEFAULT_HOLDINGS_SQL_TABLE_NAME,
    DEFAULT_INDEX_VIEW,
    DEFAULT_INDEX_ROLL_FORWARD_DAYS,
    DEFAULT_SQL_TABLE_NAME,
    persist_result,
    run_pipeline as run_enrichment_pipeline,
)
from data_access.dbtables.bdc.related_bbg_instruments_bdp import (
    get_bdp_lookup_df,
    run_pipeline as run_bdp_crosswalk_pipeline,
)
from data_access.dbtables.bdc.related_bbg_instruments_spglobal import (
    DEFAULT_SCHEMA_NAME,
    DEFAULT_TABLE_NAME,
    get_xref_lookup_df,
    persist_bridge,
    run_pipeline as run_spglobal_crosswalk_pipeline,
)


def _clean_ids(ids) -> list[str]:
    cleaned = (
        pd.Series(ids, dtype="object")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"": np.nan, "NAN": np.nan, "NONE": np.nan, "NULL": np.nan})
        .dropna()
        .drop_duplicates()
        .tolist()
    )
    return cleaned


def get_crosswalk_ids_spglobal(
    linked_ids,
    spglobal_app: str = SPGLOBAL_APP,
    lookup_batch_size: int = 1000,
    enable_parallel_lookup: bool = True,
    parallel_backend: str = "thread",
    max_parallel_workers: int = 8,
    enable_security_like_fallback: bool = True,
    security_like_fallback_prefix: str = "BL",
) -> pd.DataFrame:
    clean_ids = _clean_ids(linked_ids)
    xref_df, _ = get_xref_lookup_df(
        lookup_ids=clean_ids,
        spglobal_app=spglobal_app,
        lookup_batch_size=lookup_batch_size,
        enable_parallel_lookup=enable_parallel_lookup,
        parallel_backend=parallel_backend,
        max_parallel_workers=max_parallel_workers,
        enable_security_like_fallback=enable_security_like_fallback,
        security_like_fallback_prefix=security_like_fallback_prefix,
    )
    return xref_df


def get_crosswalk_ids_bdp(
    linked_ids,
    bdp_batch_size: int = 200,
    bdp_corp_only: bool = True,
    bdp_strict_notebook_only: bool = False,
    notebook_bdp_func=None,
    enable_parallel_lookup: bool = True,
    max_parallel_workers: int = 4,
) -> pd.DataFrame:
    clean_ids = _clean_ids(linked_ids)
    bdp_df, _, _ = get_bdp_lookup_df(
        lookup_ids=clean_ids,
        bdp_batch_size=bdp_batch_size,
        corp_only=bdp_corp_only,
        strict_notebook_only=bdp_strict_notebook_only,
        notebook_bdp_func=notebook_bdp_func,
        enable_parallel_lookup=enable_parallel_lookup,
        max_parallel_workers=max_parallel_workers,
    )
    return bdp_df


def get_crosswalk_ids(
    linked_ids,
    provider: str = "spglobal",
    **kwargs,
) -> pd.DataFrame:
    provider = str(provider).strip().lower()
    if provider == "spglobal":
        return get_crosswalk_ids_spglobal(linked_ids, **kwargs)
    if provider == "bdp":
        return get_crosswalk_ids_bdp(linked_ids, **kwargs)
    raise ValueError(f"Unsupported provider='{provider}'. Expected one of: spglobal, bdp")


def run_crosswalk_pipeline(
    provider: str = "spglobal",
    window_from_date: str = "2025-01-01",
    schema_name: str = DEFAULT_SCHEMA_NAME,
    table_name: str = DEFAULT_TABLE_NAME,
    databricks_app: str = BDC_APP,
    factset_app: str = FACTSET_APP,
    spglobal_app: str = SPGLOBAL_APP,
    run_write: bool = False,
    table_location: str | None = None,
    **kwargs,
) -> dict:
    provider = str(provider).strip().lower()
    if provider == "spglobal":
        result = run_spglobal_crosswalk_pipeline(
            window_from_date=window_from_date,
            schema_name=schema_name,
            table_name=table_name,
            databricks_app=databricks_app,
            factset_app=factset_app,
            spglobal_app=spglobal_app,
            **kwargs,
        )
    elif provider == "bdp":
        result = run_bdp_crosswalk_pipeline(
            window_from_date=window_from_date,
            schema_name=schema_name,
            table_name=table_name,
            databricks_app=databricks_app,
            factset_app=factset_app,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported provider='{provider}'. Expected one of: spglobal, bdp")

    if run_write:
        if not table_location:
            raise ValueError("table_location is required when run_write=True for crosswalk persistence.")
        persist_bridge(
            bridge_final=result["bridge_final"],
            table_name=table_name,
            table_location=table_location,
            run_write=True,
        )
    return result


def run_multivariant_join_enrichment(
    from_date: str | None = None,
    to_date: str | None = None,
    run_asof_date: str | None = None,
    index_view: str = DEFAULT_INDEX_VIEW,
    chunk_months: int = 3,
    use_chunked_reads: bool = True,
    enable_parallel_io: bool = True,
    max_parallel_workers: int = 8,
    max_parallel_key_workers: int = 8,
    filter_index_to_factset_keys: bool = True,
    index_prefilter_chunk_size: int = 1_000_000,
    index_roll_forward_days: int = DEFAULT_INDEX_ROLL_FORWARD_DAYS,
    reuse_previous_enriched_snapshot: bool = True,
    previous_factset_snapshot_df: pd.DataFrame | None = None,
    databricks_app: str = BDC_APP,
    run_write: bool = False,
    bootstrap: bool = False,
    sql_table_name: str = DEFAULT_SQL_TABLE_NAME,
    delta_table_name: str = DEFAULT_DELTA_TABLE_NAME,
    delta_blob_dir: str = DEFAULT_DELTA_BLOB_DIR,
    holdings_sql_table_name: str = DEFAULT_HOLDINGS_SQL_TABLE_NAME,
    holdings_delta_table_name: str = DEFAULT_HOLDINGS_DELTA_TABLE_NAME,
    holdings_delta_blob_dir: str = DEFAULT_HOLDINGS_DELTA_BLOB_DIR,
    run_write_holdings: bool | None = None,
    bootstrap_holdings: bool | None = None,
    delete_by_asof_date: bool | None = None,
    delete_by_knowledge_date: bool | None = None,
    use_spark_write: bool | None = None,
    sql_insert_batch_size: int = 500,
    waterfall: bool = False,
    active_variants: set[str] | None = None,
) -> dict:
    if not run_asof_date:
        raise ValueError(
            "run_asof_date is required. "
            "multivariant enrichment runs in daily mode only (range mode removed due to dataframe/join size)."
        )

    if from_date or to_date:
        logging.warning(
            "from_date/to_date are ignored in daily-only mode. "
            "Using run_asof_date=%s for both effective_from/effective_to.",
            run_asof_date,
        )

    effective_from = run_asof_date
    effective_to = run_asof_date
    if run_write_holdings is None:
        run_write_holdings = bool(run_write)
    if bootstrap_holdings is None:
        bootstrap_holdings = bool(bootstrap and run_write_holdings)

    bundle = run_enrichment_pipeline(
        from_date=effective_from,
        to_date=effective_to,
        run_asof_date=run_asof_date,
        index_view=index_view,
        chunk_months=chunk_months,
        use_chunked_reads=use_chunked_reads,
        databricks_app=databricks_app,
        enable_parallel_io=enable_parallel_io,
        max_parallel_workers=max_parallel_workers,
        max_parallel_key_workers=max_parallel_key_workers,
        filter_index_to_factset_keys=filter_index_to_factset_keys,
        index_prefilter_chunk_size=index_prefilter_chunk_size,
        index_roll_forward_days=index_roll_forward_days,
        waterfall=waterfall,
        active_variants=active_variants,
        holdings_sql_table_name=holdings_sql_table_name,
        holdings_delta_table_name=holdings_delta_table_name,
        holdings_delta_blob_dir=holdings_delta_blob_dir,
        prior_enriched_sql_table_name=sql_table_name,
        previous_factset_snapshot_df=previous_factset_snapshot_df,
        reuse_previous_enriched_snapshot=bool(reuse_previous_enriched_snapshot and not bootstrap),
        bootstrap_holdings=bool(bootstrap_holdings),
        run_write_holdings=bool(run_write_holdings),
        use_spark_write=use_spark_write,
        sql_insert_batch_size=sql_insert_batch_size,
    )
    if run_write:
        write_started_at = time.perf_counter()
        if delete_by_asof_date is None:
            delete_by_asof_date = run_asof_date is not None
        if delete_by_knowledge_date is None:
            delete_by_knowledge_date = run_asof_date is not None

        if run_asof_date:
            asof_from = run_asof_date
            asof_to = run_asof_date
            knowledge_from = run_asof_date
            knowledge_to = run_asof_date
        else:
            asof_from = None
            asof_to = None
            knowledge_from = None
            knowledge_to = None

        persisted = persist_result(
            result=bundle["result"],
            from_date=effective_from,
            to_date=effective_to,
            sql_table_name=sql_table_name,
            delta_table_name=delta_table_name,
            delta_blob_dir=delta_blob_dir,
            bootstrap=bootstrap,
            run_write=True,
            databricks_app=databricks_app,
            delete_by_asof_date=bool(delete_by_asof_date),
            asof_date_from=asof_from,
            asof_date_to=asof_to,
            delete_by_knowledge_date=bool(delete_by_knowledge_date),
            knowledge_date_from=knowledge_from,
            knowledge_date_to=knowledge_to,
            knowledge_date_override=run_asof_date,
            use_spark_write=use_spark_write,
            sql_insert_batch_size=sql_insert_batch_size,
        )
        bundle["persisted_result"] = persisted
        bundle.setdefault("stage_timings", {})["result_write_s"] = round(
            time.perf_counter() - write_started_at,
            3,
        )
    logging.info(
        "multivariant_enrichment complete | rows=%s | run_write=%s",
        f"{len(bundle['result']):,}",
        run_write,
    )
    return bundle


__all__ = [
    "get_crosswalk_ids_spglobal",
    "get_crosswalk_ids_bdp",
    "get_crosswalk_ids",
    "run_crosswalk_pipeline",
    "run_multivariant_join_enrichment",
]
