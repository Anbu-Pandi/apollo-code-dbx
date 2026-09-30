"""Identity, verification, and publication rules for immutable crawl attempts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from data_access.dbtables.sponsor_study.website_quality import CompanyReference, company_coverage


@dataclass(frozen=True)
class CrawlAttempt:
    run_id: str
    logical_run_id: str
    started_at: datetime

    def __post_init__(self) -> None:
        if not self.run_id or not self.logical_run_id or self.started_at.tzinfo is None:
            raise ValueError("A crawl attempt requires identities and a timezone-aware start")

    @classmethod
    def start(cls, logical_run_id: str) -> "CrawlAttempt":
        return cls(f"{logical_run_id}__{uuid4().hex}", logical_run_id, datetime.now(timezone.utc))


@dataclass(frozen=True)
class SponsorAttempt:
    crawl: CrawlAttempt
    website_id: str
    sponsor: str
    fingerprint: str
    number: int

    def __post_init__(self) -> None:
        if not all((self.website_id, self.sponsor, self.fingerprint)) or self.number < 1:
            raise ValueError("A sponsor attempt requires a site, fingerprint, and positive attempt number")


def configuration_fingerprint(config_json: str, code_version: str, seed: pd.Series) -> str:
    """A changed release, extraction setting, or seed must not reuse an old success."""
    fields = ("sponsor_website_id", "sponsor", "website", "portfolio_page", "crawl_mode",
              "source_completeness", "extraction_profile", "browser_profile_key", "max_pages_override")
    payload = {"configuration": json.loads(config_json), "code_version": code_version,
               "seed": {key: None if pd.isna(seed.get(key)) else str(seed[key]) for key in fields}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class CompanyLoss:
    previous_count: int
    missing: tuple[str, ...]
    maximum_fraction: float = 0.1

    def __post_init__(self) -> None:
        if not 0 <= self.maximum_fraction <= 1:
            raise ValueError("Maximum company loss fraction must be between zero and one")

    @property
    def acceptable(self) -> bool:
        return len(self.missing) <= self.previous_count * self.maximum_fraction


def compare_company_sets(previous: tuple[CompanyReference, ...], current: tuple[CompanyReference, ...],
                         *, maximum_fraction: float = 0.1) -> CompanyLoss:
    coverage = company_coverage(previous, current)
    return CompanyLoss(coverage.discovered, coverage.missing, maximum_fraction)


def frame_digest(frame: pd.DataFrame, schema_types: dict[str, str]) -> str:
    """Compare the full SQL round trip, independent of row order and pandas dtypes."""
    normalized = frame.copy()
    for column in normalized:
        kind = schema_types.get(column, "STRING")
        values = normalized[column]
        if kind == "TIMESTAMP":
            values = pd.Series(pd.to_datetime(values.tolist(), utc=True, errors="raise", format="mixed"),
                               index=frame.index).dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif kind == "BIGINT":
            values = pd.to_numeric(values, errors="raise").astype("Int64")
        elif kind in {"DOUBLE", "FLOAT"}:
            values = pd.to_numeric(values, errors="raise").astype("Float64")
        elif kind == "BOOLEAN":
            values = values.astype("boolean")
        normalized[column] = values.astype("string")
    rows = sorted(json.dumps([None if pd.isna(value) else str(value) for value in row],
                             ensure_ascii=False) for row in normalized.itertuples(index=False, name=None))
    return hashlib.sha256(json.dumps([list(frame.columns), rows]).encode()).hexdigest()


def assert_persisted_frame(expected: pd.DataFrame, actual: pd.DataFrame, *, table: str) -> None:
    if len(expected) != len(actual) or set(expected.columns) - set(actual.columns):
        raise ValueError(f"Readback mismatch for {table}: expected {len(expected)} rows, found {len(actual)}")
    types = expected.attrs.get("delta_schema_types", {})
    if frame_digest(expected, types) != frame_digest(actual[list(expected.columns)], types):
        different = [column for column in expected if frame_digest(expected[[column]], types)
                     != frame_digest(actual[[column]], types)]
        raise ValueError(f"Readback content mismatch for {table}: {', '.join(different)}")


def published_sponsor_predicate(sponsor_alias: str, run_alias: str) -> str:
    """New attempts publish per sponsor; legacy runs retain their parent-run guard."""
    return (f"{sponsor_alias}.status = 'succeeded' AND ("
            f"{sponsor_alias}.publication_verified_at IS NOT NULL OR ("
            f"{run_alias}.logical_crawl_run_id IS NULL AND "
            f"{run_alias}.status IN ('succeeded', 'succeeded_with_warnings')))" )
