"""
Covenant Review Database Tables

Database operations for Covenant Review data.
"""

from data_access.dbtables.covreview.api import (
    get_covenant_data,
    get_covenant_metadata,
    upsert_covenant_data
)
