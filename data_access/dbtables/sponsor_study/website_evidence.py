"""Typed extraction evidence and lossless combination of text and image results."""

import json
from dataclasses import dataclass, fields, replace

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug
from data_access.dbtables.sponsor_study.website_company_classification import CLASSIFICATION_COLUMNS


@dataclass(frozen=True)
class HoldingEvidence:
    company_name: str
    portfolio_status: str = ""
    sector: str = ""
    geography: str = ""
    employee_count: str = ""
    fund: str = ""
    investment_year: str = ""
    investment_date: str = ""
    exit_date: str = ""
    exit_type: str = ""
    company_url: str = ""
    external_company_url: str = ""
    company_description: str = ""
    evidence_text: str = ""
    extraction_method: str = ""
    confidence: str = ""
    provenance: tuple[tuple[str, str], ...] = ()
    conflicts: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.company_name.strip():
            raise ValueError("Holding evidence requires a company name")

    @classmethod
    def from_record(cls, record: dict) -> "HoldingEvidence":
        values = {}
        for field in fields(cls):
            if field.name in {"provenance", "conflicts"}:
                continue
            value = record.get(field.name)
            values[field.name] = "" if value is None or pd.isna(value) else str(value).strip()
        evidence = cls(**values)
        return replace(evidence, provenance=tuple(
            (name, evidence.extraction_method) for name in FACT_FIELDS if _known(getattr(evidence, name))
        ))

    def as_record(self) -> dict:
        record = {field.name: getattr(self, field.name) for field in fields(self)
                  if field.name not in {"provenance", "conflicts"}}
        record["field_provenance_json"] = json.dumps(dict(self.provenance), sort_keys=True)
        record["field_conflicts_json"] = json.dumps([
            {"field": name, "retained": retained, "alternative": alternative}
            for name, retained, alternative in self.conflicts
        ])
        return record


FACT_FIELDS = tuple(field.name for field in fields(HoldingEvidence)
                    if field.name not in {"evidence_text", "extraction_method", "confidence", "provenance", "conflicts"})


def _known(value: str) -> bool:
    return bool(value) and value.casefold() not in {"unknown", "n/a"}


def supplement_holding(primary: HoldingEvidence, extra: HoldingEvidence) -> HoldingEvidence:
    """Retain supported primary facts; record conflicts and fill only missing fields."""
    if slug(primary.company_name) != slug(extra.company_name):
        raise ValueError("Cannot combine evidence for different companies")
    changes = {}
    provenance = dict(primary.provenance)
    conflicts = list(primary.conflicts)
    for name in FACT_FIELDS:
        original, additional = getattr(primary, name), getattr(extra, name)
        if not _known(additional):
            continue
        if not _known(original):
            changes[name] = additional
            provenance[name] = extra.extraction_method
        elif original.casefold() != additional.casefold():
            conflicts.append((name, original, additional))
    return replace(primary, **changes, provenance=tuple(provenance.items()), conflicts=tuple(conflicts))


def combine_holding_evidence(
    primary: tuple[HoldingEvidence, ...], supplemental: tuple[HoldingEvidence, ...],
) -> tuple[HoldingEvidence, ...]:
    result = list(primary)
    for extra in supplemental:
        matching = [index for index, row in enumerate(result) if slug(row.company_name) == slug(extra.company_name)]
        if matching:
            for index in matching:
                result[index] = supplement_holding(result[index], extra)
        else:
            result.append(extra)
    return tuple(result)


HOLDING_CANDIDATE_COLUMNS = [
    "crawl_run_id",
    "website_holding_candidate_id",
    "site_snapshot_id",
    "sponsor",
    "company_name",
    "company_slug",
    "portfolio_status",
    "sector",
    "geography",
    "employee_count",
    "fund",
    "investment_year",
    "investment_date",
    "exit_date",
    "exit_type",
    "company_url",
    "external_company_url",
    "company_description",
    "evidence_text",
    "source_url",
    "final_url",
    "extraction_method",
    "confidence",
    "extracted_at",
    "knowledge_datetime",
    "field_provenance_json",
    "field_conflicts_json",
    *CLASSIFICATION_COLUMNS,
]
