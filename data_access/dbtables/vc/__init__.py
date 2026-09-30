"""VC Dashboard data-access layer.

All VC dashboard tables are `vc_*`-prefixed EXTERNAL Delta tables on Azure blob,
in schema ``dbo``, created with the same house convention as
``data_access.dbtables.sponsor_study`` (see ``tables.py``).

Tables
------
- ``vc_deals``            one financing round (Funding list + Deals heatmap)
- ``vc_companies``        tracked company dimension
- ``vc_investors``        investor dimension (Apollo-relevance flag)
- ``vc_news``             one article: summary + link + tags (no full body)
- ``vc_sources``          UI-managed source registry (feeds / handles)
- ``vc_universe``         UI-managed tracked universe (companies/investors/sectors/themes)
- ``vc_subsectors``       curated subsector taxonomy (seeded from history, feedback-editable)
- ``vc_meta_refresh_run`` run-control lineage for the ingest jobs
"""

# execsql/readdfsql app used for the external Delta tables (Databricks SQL).
VC_APP = "databricks"
DEFAULT_SCHEMA = "dbo"

# Logical table names (no schema/catalog prefix).
VC_DEALS = "vc_deals"
VC_COMPANIES = "vc_companies"
VC_INVESTORS = "vc_investors"
VC_NEWS = "vc_news"
VC_SOURCES = "vc_sources"
VC_UNIVERSE = "vc_universe"
VC_SUBSECTORS = "vc_subsectors"
VC_META_REFRESH_RUN = "vc_meta_refresh_run"

ALL_TABLES = [
    VC_DEALS,
    VC_COMPANIES,
    VC_INVESTORS,
    VC_NEWS,
    VC_SOURCES,
    VC_UNIVERSE,
    VC_SUBSECTORS,
    VC_META_REFRESH_RUN,
]
