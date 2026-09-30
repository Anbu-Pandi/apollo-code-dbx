"""
Covenant Review Database Tables

Target tables:
- dbo.creditsights_covenant_scores (portfolio data with lxid)
- dbo.creditsights_covenant_scores_index (anonymized index data)

Supports incremental loads with deduplication.
"""

COVENANT_REVIEW_APP = 'databricks'

from data_access.dbtables.covenant_review.covenant_data import (
    TABLE_PORTFOLIO,
    TABLE_INDEX,
    load_covenant_data,
    get_covenant_data,
    get_latest_scores,
    get_latest_load_date,
    get_incremental,
    write_data,
    load_index_data,
    get_index_data,
    get_latest_index_load_date,
    get_incremental_index,
    write_index_data,
)
