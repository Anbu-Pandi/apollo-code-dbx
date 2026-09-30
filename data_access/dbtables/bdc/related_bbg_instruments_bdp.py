import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from apo_config.config import config
from common.utils import sqlutil
from data_access.dbtables.bdc import FACTSET_APP, BDC_APP
from data_access.dbtables.bdc.related_bbg_instruments_spglobal import (
    DEFAULT_SCHEMA_NAME,
    DEFAULT_TABLE_NAME,
    FINAL_COLS,
    get_candidates,
    persist_bridge,
)


def _clean_bbg(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip().str.upper()
    return cleaned.where(~cleaned.isin(["", "NAN", "NONE", "NULL"]), np.nan)


def _normalize_security_to_input(sec):
    if pd.isna(sec):
        return np.nan
    sec = str(sec).strip().upper()
    if sec.endswith(" CORP"):
        sec = sec[:-5].strip()
    return sec


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
):
    for app_name in [factset_app, databricks_app]:
        _validate_app_connection(app_name)


def _is_limit_error(exc: Exception) -> bool:
    txt = str(exc).lower()
    return any(x in txt for x in ["limit", "too many", "exceeded", "throttle"])


def _to_rowwise_bdp_df(raw) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame()

    if isinstance(raw, pd.Series):
        df = raw.to_frame().T
    elif isinstance(raw, pd.DataFrame):
        df = raw.copy()
    elif isinstance(raw, dict):
        df = pd.DataFrame.from_dict(raw, orient="index")
    else:
        return pd.DataFrame()

    up_cols = {str(c).strip().upper(): c for c in df.columns}
    up_index = [str(i).strip().upper() for i in df.index]

    # Case 1: already row-wise with SECURITY column.
    if "SECURITY" in up_cols:
        out = df.copy()
        out = out.rename(columns={up_cols["SECURITY"]: "SECURITY"})
        return out

    # Case 2: row-wise fields with security on index.
    if "ID_BB" in up_cols or "SECURITY_DES" in up_cols:
        out = df.copy()
        if "SECURITY" not in out.columns:
            out = out.reset_index().rename(columns={"index": "SECURITY"})
        return out

    # Case 3: Pybbg style: index is fields, columns are securities.
    if "ID_BB" in up_index or "SECURITY_DES" in up_index or "CPN" in up_index:
        out = df.T.reset_index().rename(columns={"index": "SECURITY"})
        return out

    # Fallback: try using index as security.
    out = df.reset_index().rename(columns={"index": "SECURITY"})
    return out


def _coerce_lookup_output(raw, tickers_to_input: dict) -> pd.DataFrame:
    out = _to_rowwise_bdp_df(raw)
    if out.empty:
        return pd.DataFrame(
            columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"]
        )

    out.columns = [str(c).strip().upper() for c in out.columns]
    if "SECURITY" not in out.columns:
        return pd.DataFrame(
            columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"]
        )

    sec_clean = _clean_bbg(out["SECURITY"].map(_normalize_security_to_input))
    linked = sec_clean.map(lambda s: tickers_to_input.get(str(s), s if pd.notna(s) else np.nan))

    id_bb = _clean_bbg(out["ID_BB"]) if "ID_BB" in out.columns else np.nan
    security_des = out["SECURITY_DES"] if "SECURITY_DES" in out.columns else np.nan
    series = out["SERIES"] if "SERIES" in out.columns else np.nan
    mty_typ = out["MTY_TYP"] if "MTY_TYP" in out.columns else np.nan
    cpn = out["CPN"] if "CPN" in out.columns else np.nan

    df = pd.DataFrame(
        {
            "linked_instrument_id_bb": linked,
            "instrument_id_bb": id_bb,
            "security_des": security_des,
            "series": series,
            "mty_typ": mty_typ,
            "cpn": cpn,
        }
    )
    df["linked_instrument_id_bb"] = _clean_bbg(df["linked_instrument_id_bb"])
    df["instrument_id_bb"] = _clean_bbg(df["instrument_id_bb"])
    df = df.dropna(subset=["linked_instrument_id_bb"]).drop_duplicates(subset=["linked_instrument_id_bb"], keep="first")
    return df


def _lookup_via_bbgutil(tickers, fields):
    from common.utils.bbgutil import bbg_ref_request

    return bbg_ref_request(tickers, fields)


def _lookup_via_pybbg(tickers, fields):
    from common.pybbg import Pybbg

    bbg = Pybbg()
    return bbg.bdp(tickers, fields)


def _lookup_via_notebook_bdp(tickers, fields, notebook_bdp_func):
    if notebook_bdp_func is None:
        return pd.DataFrame()
    return notebook_bdp_func(tickers, fields)


def _lookup_via_blpapi(tickers, fields):
    import blpapi

    options = blpapi.SessionOptions()
    options.setServerHost("localhost")
    options.setServerPort(8194)
    session = blpapi.Session(options)
    if not session.start():
        return pd.DataFrame()
    if not session.openService("//blp/refdata"):
        session.stop()
        return pd.DataFrame()

    try:
        service = session.getService("//blp/refdata")
        req = service.createRequest("ReferenceDataRequest")
        for t in tickers:
            req.getElement("securities").appendValue(t)
        for f in fields:
            req.getElement("fields").appendValue(f)
        session.sendRequest(req)

        rows = []
        while True:
            ev = session.nextEvent(2000)
            for msg in ev:
                if not msg.hasElement("securityData"):
                    continue
                sec_data = msg.getElement("securityData")
                for i in range(sec_data.numValues()):
                    item = sec_data.getValueAsElement(i)
                    sec = item.getElementAsString("security") if item.hasElement("security") else None
                    field_data = item.getElement("fieldData") if item.hasElement("fieldData") else None
                    if field_data is None:
                        continue
                    row = {"SECURITY": sec}
                    for f in fields:
                        row[f] = field_data.getElement(f).getValue() if field_data.hasElement(f) else np.nan
                    rows.append(row)
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
        return pd.DataFrame(rows)
    finally:
        session.stop()


def bdp_lookup_batch(
    base_ids,
    corp_only: bool = True,
    strict_notebook_only: bool = False,
    notebook_bdp_func=None,
    fields: list | None = None,
):
    fields = fields or ["ID_BB", "SECURITY_DES", "SERIES", "MTY_TYP", "CPN"]
    cleaned_ids = (
        _clean_bbg(pd.Series(base_ids))
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    if not cleaned_ids:
        return pd.DataFrame(
            columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"]
        ), False

    resolved = pd.DataFrame(
        columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"]
    )
    limit_hit = False
    attempt_order = ["corp"] if corp_only else ["raw", "corp"]

    for attempt_name in attempt_order:
        pending_ids = [x for x in cleaned_ids if x not in set(resolved["linked_instrument_id_bb"].dropna().astype(str))]
        if not pending_ids or limit_hit:
            break

        tickers = [f"{x} CORP" if attempt_name == "corp" else x for x in pending_ids]
        tickers_to_input = {str(t).strip().upper(): i for i, t in zip(pending_ids, tickers)}
        for i in pending_ids:
            tickers_to_input[_normalize_security_to_input(i)] = i
            tickers_to_input[_normalize_security_to_input(f"{i} CORP")] = i

        if strict_notebook_only:
            providers = [("notebook_bdp", lambda: _lookup_via_notebook_bdp(tickers, fields, notebook_bdp_func))]
        else:
            providers = [
                ("bbgutil", lambda: _lookup_via_bbgutil(tickers, fields)),
                ("notebook_bdp", lambda: _lookup_via_notebook_bdp(tickers, fields, notebook_bdp_func)),
                ("pybbg", lambda: _lookup_via_pybbg(tickers, fields)),
                ("blpapi", lambda: _lookup_via_blpapi(tickers, fields)),
            ]

        for provider_name, provider_func in providers:
            pending_ids = [x for x in cleaned_ids if x not in set(resolved["linked_instrument_id_bb"].dropna().astype(str))]
            if not pending_ids or limit_hit:
                break
            try:
                raw = provider_func()
                out = _coerce_lookup_output(raw, tickers_to_input)
                if out.empty:
                    continue
                out = out[out["linked_instrument_id_bb"].isin(pending_ids)].copy()
                if out.empty:
                    continue
                resolved = pd.concat([resolved, out], ignore_index=True)
                resolved = resolved.drop_duplicates(subset=["linked_instrument_id_bb"], keep="first")
                logging.info("BDP provider=%s attempt=%s mapped=%s", provider_name, attempt_name, len(out))
            except Exception as exc:
                logging.warning("BDP provider=%s attempt=%s failed: %s", provider_name, attempt_name, exc)
                if _is_limit_error(exc):
                    limit_hit = True
                    break

    return resolved, limit_hit


def get_bdp_lookup_df(
    lookup_ids,
    bdp_batch_size: int = 200,
    corp_only: bool = True,
    strict_notebook_only: bool = False,
    notebook_bdp_func=None,
    enable_parallel_lookup: bool = True,
    max_parallel_workers: int = 4,
):
    chunk_list = [lookup_ids[x : x + bdp_batch_size] for x in range(0, len(lookup_ids), bdp_batch_size) if lookup_ids[x : x + bdp_batch_size]]
    total_chunks = len(chunk_list)
    if not chunk_list:
        return pd.DataFrame(
            columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"]
        ), False, 0

    frames = []
    any_limit_hit = False
    query_count = total_chunks

    if enable_parallel_lookup and total_chunks > 1:
        max_workers = max(1, min(int(max_parallel_workers or 1), total_chunks))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    bdp_lookup_batch,
                    chunk,
                    corp_only,
                    strict_notebook_only,
                    notebook_bdp_func,
                ): i
                for i, chunk in enumerate(chunk_list, start=1)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    out, limit_hit = future.result()
                    any_limit_hit = any_limit_hit or bool(limit_hit)
                    if isinstance(out, pd.DataFrame) and not out.empty:
                        frames.append(out)
                    logging.info("BDP chunk %s/%s complete | mapped_rows=%s", i, total_chunks, 0 if out is None else len(out))
                except Exception as exc:
                    logging.error("BDP chunk %s/%s failed: %s", i, total_chunks, exc)
    else:
        for i, chunk in enumerate(chunk_list, start=1):
            logging.info("BDP chunk %s/%s | ids=%s", i, total_chunks, len(chunk))
            out, limit_hit = bdp_lookup_batch(
                chunk,
                corp_only=corp_only,
                strict_notebook_only=strict_notebook_only,
                notebook_bdp_func=notebook_bdp_func,
            )
            any_limit_hit = any_limit_hit or bool(limit_hit)
            frames.append(out)
            logging.info("BDP chunk %s/%s complete | mapped_rows=%s", i, total_chunks, 0 if out is None else len(out))
            if any_limit_hit:
                logging.warning("BDP limit detected; stopping remaining sequential chunks.")
                break

    bdp_lookup_df = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["linked_instrument_id_bb", "instrument_id_bb", "security_des", "series", "mty_typ", "cpn"])
    )
    if not bdp_lookup_df.empty:
        bdp_lookup_df["linked_instrument_id_bb"] = _clean_bbg(bdp_lookup_df["linked_instrument_id_bb"])
        bdp_lookup_df["instrument_id_bb"] = _clean_bbg(bdp_lookup_df["instrument_id_bb"])
        bdp_lookup_df = bdp_lookup_df.drop_duplicates(subset=["linked_instrument_id_bb"], keep="first")
    return bdp_lookup_df, any_limit_hit, query_count


def _sample_lookup_ids(candidates: pd.DataFrame, unresolved_ids: list[str], max_ids: int) -> list[str]:
    unresolved_df = candidates[candidates["linked_instrument_id_bb"].isin(unresolved_ids)].copy()
    selected = []
    for src in ["factset", "jpm_loan_index", "sp_loan_index"]:
        src_ids = (
            unresolved_df.loc[unresolved_df["source_system"] == src, "linked_instrument_id_bb"]
            .dropna()
            .astype(str)
            .tolist()
        )
        selected.extend(src_ids)
    if len(selected) < max_ids:
        selected.extend([x for x in unresolved_ids if x not in set(selected)])
    return selected[:max_ids]


def run_pipeline(
    window_from_date: str = "2025-01-01",
    schema_name: str = DEFAULT_SCHEMA_NAME,
    table_name: str = DEFAULT_TABLE_NAME,
    databricks_app: str = BDC_APP,
    factset_app: str = FACTSET_APP,
    bdp_batch_size: int = 200,
    bdp_test_mode: bool = False,
    bdp_test_max_ids: int = 300,
    bdp_corp_only: bool = True,
    bdp_strict_notebook_only: bool = False,
    notebook_bdp_func=None,
    enable_parallel_lookup: bool = True,
    max_parallel_workers: int = 4,
):
    validate_required_apps(factset_app=factset_app, databricks_app=databricks_app)
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

    existing_key_set = set(bridge_existing.get("linked_instrument_id_bb", pd.Series(dtype="object")).dropna().astype(str))
    unresolved_ids = sorted(set(candidates["linked_instrument_id_bb"].astype(str)) - existing_key_set)
    lookup_ids = unresolved_ids
    if bdp_test_mode:
        lookup_ids = _sample_lookup_ids(candidates, unresolved_ids, int(bdp_test_max_ids))

    bdp_lookup_df, limit_hit, query_count = get_bdp_lookup_df(
        lookup_ids=lookup_ids,
        bdp_batch_size=bdp_batch_size,
        corp_only=bdp_corp_only,
        strict_notebook_only=bdp_strict_notebook_only,
        notebook_bdp_func=notebook_bdp_func,
        enable_parallel_lookup=enable_parallel_lookup,
        max_parallel_workers=max_parallel_workers,
    )

    unresolved_meta = candidates[candidates["linked_instrument_id_bb"].isin(lookup_ids)].copy()
    new_rows = unresolved_meta.merge(bdp_lookup_df, on="linked_instrument_id_bb", how="left")
    new_rows["id_bb8"] = new_rows["instrument_id_bb"].astype(str).str[:8]
    new_rows.loc[new_rows["instrument_id_bb"].isna(), "id_bb8"] = np.nan
    new_rows.loc[new_rows["id_bb8"].str.len() != 8, "id_bb8"] = np.nan
    new_rows["mapping_provider"] = "bbg"
    if limit_hit:
        new_rows["bdp_status"] = np.where(new_rows["instrument_id_bb"].notna(), "FOUND", "SKIPPED_LIMIT")
    else:
        new_rows["bdp_status"] = np.where(new_rows["instrument_id_bb"].notna(), "FOUND", "NOT_FOUND")
    now_ts = pd.Timestamp.now(tz="UTC")
    new_rows["created_at"] = now_ts
    new_rows["updated_at"] = now_ts
    new_rows["last_seen_at"] = now_ts
    new_rows["is_active"] = new_rows["instrument_id_bb"].notna()
    new_rows["parent_security_id"] = np.nan
    new_rows["related_identifier_active_flag"] = np.nan

    for c in FINAL_COLS:
        if c not in bridge_existing.columns:
            bridge_existing[c] = np.nan
        if c not in new_rows.columns:
            new_rows[c] = np.nan
    bridge_existing = bridge_existing[FINAL_COLS].copy()
    new_rows = new_rows[FINAL_COLS].copy()

    for df in [bridge_existing, new_rows]:
        for ts_col in ["created_at", "updated_at", "last_seen_at"]:
            if ts_col in df.columns:
                df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce", utc=True)

    if not bridge_existing.empty and not new_rows.empty:
        bridge_existing = bridge_existing[
            ~bridge_existing["linked_instrument_id_bb"].isin(new_rows["linked_instrument_id_bb"])
        ].copy()

    bridge_final = pd.concat([bridge_existing, new_rows], ignore_index=True)
    bridge_final = bridge_final.drop_duplicates(
        subset=["linked_instrument_id_bb", "instrument_id_bb"],
        keep="last",
    )
    seen_ids = set(candidates["linked_instrument_id_bb"].dropna().astype(str))
    if seen_ids and not bridge_final.empty:
        bridge_final.loc[
            bridge_final["linked_instrument_id_bb"].isin(seen_ids),
            "last_seen_at",
        ] = now_ts

    qa_summary = pd.DataFrame(
        [{
            "total_rows": int(len(bridge_final)),
            "matched_rows": int(bridge_final["instrument_id_bb"].notna().sum()),
            "unmatched_rows": int(bridge_final["instrument_id_bb"].isna().sum()),
            "distinct_linked_ids": int(bridge_final["linked_instrument_id_bb"].nunique(dropna=True)),
            "distinct_parent_security_ids": int(bridge_final["parent_security_id"].nunique(dropna=True))
            if "parent_security_id" in bridge_final.columns
            else np.nan,
        }]
    )

    return {
        "candidates": candidates,
        "bridge_existing": bridge_existing,
        "lookup_ids": lookup_ids,
        "bdp_lookup_df": bdp_lookup_df,
        "new_rows": new_rows,
        "bridge_final": bridge_final,
        "qa_summary": qa_summary,
        "metrics": {
            "existing_linked_ids": len(existing_key_set),
            "unresolved_ids": len(unresolved_ids),
            "lookup_ids": len(lookup_ids),
            "bdp_queries": int(query_count),
            "rows_in_lookup": len(bdp_lookup_df),
            "rows_in_result": len(bridge_final),
            "rows_with_matches": int(bridge_final["instrument_id_bb"].notna().sum()),
            "limit_hit": bool(limit_hit),
        },
    }


__all__ = [
    "run_pipeline",
    "persist_bridge",
    "bdp_lookup_batch",
    "get_bdp_lookup_df",
]
