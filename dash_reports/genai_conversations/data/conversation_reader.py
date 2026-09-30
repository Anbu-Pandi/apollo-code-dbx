"""Data layer for the Conversation Explorer.

Reads the genai conversation logs (the real chat) and run audit (metrics) from
Databricks, and searches them. Search is pluggable:

  * SEMANTIC — Databricks Vector Search via the native ``vector_search()`` SQL
    function, when an index name is configured (GENAI_CONV_VECTOR_INDEX). No
    embedding engineering on our side; Databricks hosts the model + index.
  * KEYWORD  — SQL ILIKE fallback, always available (no infra), used until the
    vector index is provisioned or if a vector query errors.

Everything is read-only and defensive: a missing table / unprovisioned index
degrades to keyword or an empty result, never an exception into the page.
"""

import logging
import os
import time

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables import GENAI_APP

log = logging.getLogger(__name__)

CONV_TABLE = "genai_conversation_logs"
AUDIT_TABLE = "genai_run_audit"


def _vector_index() -> str:
    """Fully-qualified Databricks Vector Search index (catalog.schema.index).
    Configured via env or apo_config; empty => semantic disabled (keyword only)."""
    idx = os.environ.get("GENAI_CONV_VECTOR_INDEX")
    if idx:
        return idx
    try:
        from apo_config.config import config
        return config.getconfig("GENAI", "conv_vector_index") or ""
    except Exception:
        return ""


def _esc(s: str) -> str:
    return str(s or "").replace("'", "''")


_EXISTING_TABLES = None
_EXISTING_TABLES_AT = 0.0
_TABLE_CACHE_SECONDS = 300


def _existing_tables() -> set:
    """Cached set of table names in the genie schema (via SHOW TABLES, which does
    NOT error on a missing table — unlike SELECT). Used to gate optional tables
    so a missing one degrades quietly instead of spamming retried ERRORs."""
    global _EXISTING_TABLES, _EXISTING_TABLES_AT
    if (
        _EXISTING_TABLES is not None
        and time.monotonic() - _EXISTING_TABLES_AT < _TABLE_CACHE_SECONDS
    ):
        return _EXISTING_TABLES
    try:
        df = readdfsql("SHOW TABLES", app=GENAI_APP, retrycount=1)
    except Exception as exc:
        log.warning("conv store: SHOW TABLES failed: %s", exc)
        return set()
    col = "tableName" if "tableName" in df.columns else df.columns[-2]
    tables = {str(x).lower() for x in df[col]}
    if tables:
        _EXISTING_TABLES = tables
        _EXISTING_TABLES_AT = time.monotonic()
    return tables


def _has(table: str) -> bool:
    return table.lower() in _existing_tables()


# ── search ────────────────────────────────────────────────────────────────

def search_conversations(query: str, top_k: int = 25, source_system: str = None,
                         since_days: int = None) -> tuple:
    """Return (DataFrame, mode) where mode is 'semantic' or 'keyword'."""
    query = (query or "").strip()
    if not query:
        return pd.DataFrame(), "keyword"
    if _vector_index():
        try:
            df = _vector_search(query, top_k)
            if df is not None:
                return df, "semantic"
        except Exception as exc:
            log.warning("conv search: vector_search failed, falling back to keyword: %s", exc)
    return _keyword_search(query, top_k, source_system, since_days), "keyword"


def _vector_search(query: str, top_k: int) -> pd.DataFrame:
    idx = _vector_index()
    sql = (
        "SELECT log_id, session_id, source_system, timestamp, question, text_response, "
        "search_score FROM vector_search("
        f"index => '{_esc(idx)}', query_text => '{_esc(query)}', num_results => {int(top_k)})"
    )
    return readdfsql(sql, app=GENAI_APP)


def _keyword_search(query: str, top_k: int, source_system, since_days) -> pd.DataFrame:
    like = _esc(query)
    where = [f"(question ILIKE '%{like}%' OR text_response ILIKE '%{like}%')"]
    if source_system:
        where.append(f"source_system = '{_esc(source_system)}'")
    if since_days:
        where.append(f"timestamp >= current_timestamp() - INTERVAL {int(since_days)} DAYS")
    sql = (
        "SELECT log_id, session_id, source_system, timestamp, question, text_response "
        f"FROM {CONV_TABLE} WHERE {' AND '.join(where)} "
        f"ORDER BY timestamp DESC LIMIT {int(top_k)}"
    )
    try:
        return readdfsql(sql, app=GENAI_APP, retrycount=1)
    except Exception as exc:
        log.warning("conv search: keyword query failed: %s", exc)
        return pd.DataFrame()


# ── single conversation ─────────────────────────────────────────────────────

def resolve_session_id(id_value: str) -> str:
    """Map any id (session / conversation / run) to the key the transcript is
    stored under in CONV_TABLE.

    Note transcripts are keyed by the agent's *conversation_id*, which for teams
    routes is the Teams conversation reference — NOT the run's per-message
    session_id (``user_ai_<msgid>``). The run-audit table bridges
    run_id -> conversation_id (it stores session_id=conversation_id), so a run_id
    resolves cleanly; a per-message session_id only resolves by first finding its
    run_id in the observability issues store, then bridging through audit."""
    idv = (id_value or "").strip()
    if not idv:
        return ""
    # already a key with turns? (true for the conversation_id itself, and for
    # cli/excel routes where session_id == conversation_id)
    try:
        hit = readdfsql(f"SELECT 1 FROM {CONV_TABLE} WHERE session_id = '{_esc(idv)}' LIMIT 1",
                        app=GENAI_APP, retrycount=1)
        if hit is not None and not hit.empty:
            return idv
    except Exception:
        pass
    # a run_id -> conversation_id via the audit table
    sid = _session_from_audit(idv)
    if sid:
        return sid
    # a per-message session_id from a Radar deep-link: find its run_id in the
    # observability issues store, then bridge that through audit to the
    # conversation_id the transcript is keyed by.
    sid = _session_from_issues(idv)
    if sid:
        return sid
    return idv  # fall through: treat as-is


def _session_from_audit(run_id: str) -> str:
    """run_id -> conversation/session key via the run-audit table (which stores
    session_id=conversation_id). Returns '' if absent; never raises."""
    if not run_id or not _has(AUDIT_TABLE):
        return ""
    try:
        aud = readdfsql(f"SELECT session_id FROM {AUDIT_TABLE} WHERE run_id = '{_esc(run_id)}' LIMIT 1",
                        app=GENAI_APP, retrycount=1)
        if aud is not None and not aud.empty:
            return str(aud.iloc[0]["session_id"] or "").strip()
    except Exception:
        pass
    return ""


def _session_from_issues(id_value: str) -> str:
    """Resolve a Radar id (conversation_id, per-message session_id, or run_id) to
    the transcript key via the observability issues store. Each issue records the
    conversation_id the transcript is actually keyed by, so we match the id
    against any of those columns and return the conversation_id — audit-independent
    and retroactive. Falls back to bridging run_id through audit if an older row
    has no conversation_id. Returns '' if unavailable; never raises."""
    try:
        from genai.observability import reader
        df = reader.read_all_issues()
        if df is None or df.empty:
            return ""
        match = None
        for col in ("conversation_id", "session_id", "run_id"):
            if col in df.columns:
                m = df[col] == id_value
                match = m if match is None else (match | m)
        if match is None:
            return ""
        hits = df[match]
        if "conversation_id" in df.columns:
            for cid in hits["conversation_id"].dropna().tolist():
                cid = str(cid).strip()
                if cid:
                    return cid
        # older rows may predate conversation_id capture: bridge run_id via audit
        if "run_id" in df.columns:
            for rid in hits["run_id"].dropna().tolist():
                sid = _session_from_audit(str(rid).strip())
                if sid:
                    return sid
    except Exception as exc:
        log.debug("conv resolve: issues-store fallback failed for %s: %s", id_value, exc)
    return ""


def get_conversation(session_id: str) -> pd.DataFrame:
    """All turns for a session, oldest first."""
    sid = _esc(session_id)
    if not sid:
        return pd.DataFrame()
    sql = (
        "SELECT timestamp, source_system, question, sql_query, text_response, "
        "user_feedback, feedback_sentiment "
        f"FROM {CONV_TABLE} WHERE session_id = '{sid}' ORDER BY timestamp"
    )
    try:
        return readdfsql(sql, app=GENAI_APP, retrycount=1)
    except Exception as exc:
        log.warning("conv get: failed for %s: %s", session_id, exc)
        return pd.DataFrame()


def get_run_audit(session_id: str) -> pd.DataFrame:
    if not _has(AUDIT_TABLE):   # optional table — absent in some catalogs
        return pd.DataFrame()
    sid = _esc(session_id)
    sql = (
        "SELECT run_id, route, user_name, input_tokens, output_tokens, "
        "estimated_cost, latency_ms, timestamp "
        f"FROM {AUDIT_TABLE} WHERE session_id = '{sid}' ORDER BY timestamp"
    )
    try:
        return readdfsql(sql, app=GENAI_APP, retrycount=1)
    except Exception:
        return pd.DataFrame()


def get_issues_for_session(session_id: str) -> pd.DataFrame:
    """Any captured observability issues tied to this session (links the two)."""
    try:
        from genai.observability import reader
        df = reader.read_all_issues()
        if df is None or df.empty or "session_id" not in df.columns:
            return pd.DataFrame()
        return df[df["session_id"] == session_id]
    except Exception:
        return pd.DataFrame()


# ── stats: how things are going, by the categories we log ───────────────────

def _q(sql: str) -> pd.DataFrame:
    try:
        df = readdfsql(sql, app=GENAI_APP, retrycount=1)
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        log.warning("conv stats query failed: %s", exc)
        return pd.DataFrame()


def conversation_stats(since_days: int = 30) -> dict:
    """Aggregations for the Stats tab. Every piece degrades to empty on error."""
    win = f"WHERE timestamp >= current_timestamp() - INTERVAL {int(since_days)} DAYS"

    kpi = _q(f"SELECT COUNT(*) turns, COUNT(DISTINCT session_id) sessions, "
             f"COUNT(DISTINCT source_system) channels, "
             f"SUM(CASE WHEN user_feedback IS NOT NULL AND user_feedback <> '' THEN 1 ELSE 0 END) feedback "
             f"FROM {CONV_TABLE} {win}")

    by_source = _q(f"SELECT source_system, COUNT(*) turns, COUNT(DISTINCT session_id) sessions "
                   f"FROM {CONV_TABLE} {win} GROUP BY source_system ORDER BY turns DESC")

    daily = _q(f"SELECT CAST(timestamp AS DATE) day, COUNT(*) turns, "
               f"COUNT(DISTINCT session_id) sessions "
               f"FROM {CONV_TABLE} {win} GROUP BY CAST(timestamp AS DATE) ORDER BY day")

    feedback = _q(f"SELECT COALESCE(NULLIF(feedback_sentiment,''),'none') sentiment, COUNT(*) n "
                  f"FROM {CONV_TABLE} {win} GROUP BY COALESCE(NULLIF(feedback_sentiment,''),'none') "
                  f"ORDER BY n DESC")

    # run audit is richer (route/user/tokens/cost) but is an OPTIONAL table —
    # only query it when present, else return empty frames (panels degrade).
    by_route = top_users = cost_daily = pd.DataFrame()
    audit_available = _has(AUDIT_TABLE)
    if audit_available:
        awin = f"WHERE timestamp >= current_timestamp() - INTERVAL {int(since_days)} DAYS"
        by_route = _q(f"SELECT COALESCE(NULLIF(route,''),'unknown') route, COUNT(*) runs, "
                      f"SUM(input_tokens+output_tokens) tokens, SUM(estimated_cost) cost, "
                      f"AVG(latency_ms) avg_latency_ms "
                      f"FROM {AUDIT_TABLE} {awin} GROUP BY COALESCE(NULLIF(route,''),'unknown') ORDER BY runs DESC")
        top_users = _q(f"SELECT COALESCE(NULLIF(user_name,''),'unknown') user_name, COUNT(*) runs, "
                       f"SUM(estimated_cost) cost FROM {AUDIT_TABLE} {awin} "
                       f"GROUP BY COALESCE(NULLIF(user_name,''),'unknown') ORDER BY runs DESC LIMIT 15")
        cost_daily = _q(f"SELECT CAST(timestamp AS DATE) day, SUM(estimated_cost) cost, "
                        f"SUM(input_tokens+output_tokens) tokens "
                        f"FROM {AUDIT_TABLE} {awin} GROUP BY CAST(timestamp AS DATE) ORDER BY day")

    return {"kpi": kpi, "by_source": by_source, "daily": daily, "feedback": feedback,
            "by_route": by_route, "top_users": top_users, "cost_daily": cost_daily,
            "audit_available": audit_available}
