"""Retain model-access metadata independently of structured financial eligibility."""
from dataclasses import dataclass
from datetime import date
import json
import logging

import numpy as np
import pandas as pd

from common.utils.sqlutil import readdfsql


COVERAGE_COLUMNS = [
    "findox_allowed", "findox_model_count", "findox_allowed_model_count",
    "findox_denied_model_count", "findox_public_allowed_model_count", "findox_coverage_status",
    "findox_coverage_asof", "findox_model_access_json",
]


@dataclass(frozen=True)
class ModelAccess:
    company_id: str
    allowed: bool | None
    model_type: str
    access_type: str
    updated_at: str


def attach_findox_coverage(matches: pd.DataFrame, universe: pd.DataFrame, *, cutoff: date) -> pd.DataFrame:
    """Keep model access metadata separate from financial availability and disclosure."""
    models: dict[str, list[ModelAccess]] = {}
    for row in universe.itertuples(index=False):
        if pd.notna(row.allowed) and not isinstance(row.allowed, (bool, np.bool_)):
            raise ValueError("FinDox allowed must be a boolean or null")
        permission = None if pd.isna(row.allowed) else bool(row.allowed)
        models.setdefault(str(row.findox_issuer_id), []).append(ModelAccess(
            str(row.aggregate_company_id), permission,
            "" if pd.isna(row.model_type_label) else str(row.model_type_label),
            "" if pd.isna(row.access_type) else str(row.access_type),
            str(row.financial_data_updated)))
    records = []
    for issuer in matches.findox_issuer_id.dropna().astype(str).unique():
        items = models.get(issuer, [])
        allowed = sum(item.allowed is True for item in items)
        denied = sum(item.allowed is False for item in items)
        eligible = sum(item.allowed is True and item.model_type.lower() == "public" for item in items)
        status = ("no_aggregate_record" if not items else "public_allowed_model_present" if eligible else
                  "model_not_allowed" if denied == len(items) else
                  "model_not_public" if allowed == len(items) else "mixed_or_unknown_permission")
        records.append({"findox_issuer_id": issuer,
            "findox_allowed": True if items and allowed == len(items) else
                              False if items and denied == len(items) else pd.NA,
            "findox_model_count": len(items), "findox_allowed_model_count": allowed,
            "findox_denied_model_count": denied, "findox_public_allowed_model_count": eligible,
            "findox_coverage_status": status, "findox_coverage_asof": cutoff,
            "findox_model_access_json": json.dumps([{
                "aggregate_company_id": item.company_id, "allowed": item.allowed,
                "model_type": item.model_type, "access_type": item.access_type,
                "financial_data_updated": item.updated_at,
            } for item in sorted(items, key=lambda item: item.company_id)], sort_keys=True)})
    coverage = pd.DataFrame(records, columns=["findox_issuer_id", *COVERAGE_COLUMNS])
    coverage["findox_allowed"] = coverage.findox_allowed.astype("boolean")
    out = matches.drop(columns=COVERAGE_COLUMNS, errors="ignore").merge(
        coverage, on="findox_issuer_id", how="left", validate="many_to_one")
    logging.info("FinDox coverage issuers=%s statuses=%s; access metadata retained independently of stored financial availability",
                 len(coverage), coverage.findox_coverage_status.value_counts().to_dict())
    return out


def fetch_findox_model_coverage(matches: pd.DataFrame, *, asof_cutoff: str, app: str) -> pd.DataFrame:
    cutoff = date.fromisoformat(asof_cutoff)
    ids = sorted({int(value) for value in matches.findox_issuer_id.dropna()})
    frames = []
    for start in range(0, len(ids), 200):
        values = ",".join(map(str, ids[start:start + 200]))
        frames.append(readdfsql(f"""
            SELECT id AS aggregate_company_id, CAST(findox_id AS STRING) AS findox_issuer_id,
                   allowed, model_type_label, access_type, financial_data_updated
            FROM dbo.findox_agg_universe
            WHERE TRY_CAST(findox_id AS BIGINT) IN ({values})
              AND financial_data_updated < DATE '{cutoff}' + INTERVAL 1 DAY
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY financial_data_updated DESC)=1
        """, app=app))
    return attach_findox_coverage(matches, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), cutoff=cutoff)
