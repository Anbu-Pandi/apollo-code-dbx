"""Schema contracts for the ``vc_*`` tables.

Each contract is an *empty typed DataFrame*; the DDL builder in ``tables.py`` infers
the Delta column types from the dtypes (with STRING overrides carried in
``df.attrs['delta_schema_types']`` for id/text columns), mirroring the
``sponsor_study`` persistence convention. Keeping the schema here means one source of
truth shared by table creation, ingest canonicalization, and the sub-server reads.
"""

import pandas as pd


def _frame(columns: dict, *, string_cols: tuple = ()) -> pd.DataFrame:
    """Build an empty typed frame and stamp STRING overrides for text/id columns."""
    df = pd.DataFrame({c: pd.Series(dtype=t) for c, t in columns.items()})
    if string_cols:
        df.attrs["delta_schema_types"] = {c: "STRING" for c in string_cols}
    return df


def vc_deals_contract() -> pd.DataFrame:
    return _frame(
        {
            "deal_id": "object",          # PitchBook deal PBID (NNNNN-NNT)
            "company": "object",
            "company_pbid": "object",
            "deal_date": "datetime64[ns]",
            "round_type": "object",       # Seed / Series A / B / Growth / Debt / Secondary
            "deal_size_usd": "float64",
            "post_valuation_usd": "float64",
            "lead_investors": "object",   # comma-joined
            "investors": "object",        # comma-joined
            "sector": "object",           # heatmap axis
            "sub_sector": "object",
            "themes": "object",           # comma-joined
            "region": "object",
            "deal_size_band": "object",   # derived bucket for heatmap axis
            "is_apollo_relevant": "boolean",
            "source": "object",
            "refresh_run_id": "object",
            "ingested_at": "datetime64[ns]",
            "deal_status": "object",
            "description": "object",
            "source_observed_at": "datetime64[ns]",
            "sub_sector_confidence": "float64",
            "sub_sector_source": "object",   # pitchbook | ai | user
            "sub_sector_review": "boolean",  # True = awaiting user confirm/validate
        },
        string_cols=(
            "deal_id", "company", "company_pbid", "round_type", "lead_investors",
            "investors", "sector", "sub_sector", "themes", "region",
            "deal_size_band", "source", "refresh_run_id", "deal_status", "description",
            "sub_sector_source",
        ),
    )


def vc_companies_contract() -> pd.DataFrame:
    return _frame(
        {
            "company_pbid": "object",
            "company": "object",
            "sector": "object",
            "sub_sector": "object",
            "region": "object",
            "themes": "object",
            "website": "object",
            "description": "object",
            "is_apollo_relevant": "boolean",
            "refresh_run_id": "object",
            "ingested_at": "datetime64[ns]",
        },
        string_cols=(
            "company_pbid", "company", "sector", "sub_sector", "region",
            "themes", "website", "description", "refresh_run_id",
        ),
    )


def vc_investors_contract() -> pd.DataFrame:
    return _frame(
        {
            "investor_pbid": "object",
            "investor_name": "object",
            "investor_type": "object",
            "is_apollo_relevant": "boolean",
            "refresh_run_id": "object",
            "ingested_at": "datetime64[ns]",
        },
        string_cols=(
            "investor_pbid", "investor_name", "investor_type", "refresh_run_id",
        ),
    )


def vc_news_contract() -> pd.DataFrame:
    # Lightweight: summary + browseable link + tags only. No article body.
    return _frame(
        {
            "doc_id": "object",
            "message_hash": "object",
            "url": "object",
            "source": "object",
            "source_type": "object",       # blog|newsletter|media|x_handle
            "title": "object",
            "summary": "object",           # HuggingNews one-liner
            "sector": "object",
            "themes": "object",            # comma-joined
            "companies": "object",         # comma-joined
            "published_at": "datetime64[ns]",
            "ingested_at": "datetime64[ns]",
            "trust_weight": "float64",
        },
        string_cols=(
            "doc_id", "message_hash", "url", "source", "source_type",
            "title", "summary", "sector", "themes", "companies",
        ),
    )


def vc_sources_contract() -> pd.DataFrame:
    # UI-managed source registry. The Sources admin tab CRUDs this.
    return _frame(
        {
            "source_id": "object",
            "name": "object",
            "type": "object",              # blog|newsletter|media|x_handle
            "url_or_handle": "object",
            "trust_weight": "float64",
            "sector_hint": "object",
            "enabled": "boolean",
            "added_by": "object",
            "added_at": "datetime64[ns]",
            "last_ok_at": "datetime64[ns]",
            "last_error": "object",
        },
        string_cols=(
            "source_id", "name", "type", "url_or_handle", "sector_hint",
            "added_by", "last_error",
        ),
    )


def vc_universe_contract() -> pd.DataFrame:
    # UI-managed tracked universe (companies/investors/sectors/themes + x handles).
    return _frame(
        {
            "entity_id": "object",
            "kind": "object",              # company|investor|sector|theme|x_handle
            "name": "object",
            "pbid": "object",
            "enabled": "boolean",
            "added_by": "object",
            "added_at": "datetime64[ns]",
        },
        string_cols=("entity_id", "kind", "name", "pbid", "added_by"),
    )


def vc_subsectors_contract() -> pd.DataFrame:
    # Curated subsector taxonomy: seeded from historical deal/company data, then
    # maintained through the dashboard review flow. Keywords drive the classifier.
    return _frame(
        {
            "subsector_id": "object",      # <sector>/<sub_sector> slug
            "sector": "object",            # canonical parent sector
            "sub_sector": "object",
            "definition": "object",
            "keywords": "object",          # comma-joined classifier signals
            "enabled": "boolean",
            "added_by": "object",
            "added_at": "datetime64[ns]",
        },
        string_cols=(
            "subsector_id", "sector", "sub_sector", "definition", "keywords", "added_by",
        ),
    )


def vc_meta_refresh_run_contract() -> pd.DataFrame:
    return _frame(
        {
            "refresh_run_id": "object",
            "job": "object",               # vc_ingest_deals | vc_ingest_news
            "run_started_at": "datetime64[ns]",
            "run_completed_at": "datetime64[ns]",
            "status": "object",            # running | succeeded | failed
            "environment": "object",
            "catalog": "object",
            "schema": "object",
            "row_count": "int64",
            "config_json": "object",
            "error_message": "object",
        },
        string_cols=(
            "refresh_run_id", "job", "status", "environment", "catalog",
            "schema", "config_json", "error_message",
        ),
    )


def all_contracts() -> dict:
    """Logical table name -> empty typed contract frame."""
    from data_access.dbtables.vc import (
        VC_DEALS, VC_COMPANIES, VC_INVESTORS, VC_NEWS,
        VC_SOURCES, VC_UNIVERSE, VC_SUBSECTORS, VC_META_REFRESH_RUN,
    )
    return {
        VC_DEALS: vc_deals_contract(),
        VC_COMPANIES: vc_companies_contract(),
        VC_INVESTORS: vc_investors_contract(),
        VC_NEWS: vc_news_contract(),
        VC_SOURCES: vc_sources_contract(),
        VC_UNIVERSE: vc_universe_contract(),
        VC_SUBSECTORS: vc_subsectors_contract(),
        VC_META_REFRESH_RUN: vc_meta_refresh_run_contract(),
    }
