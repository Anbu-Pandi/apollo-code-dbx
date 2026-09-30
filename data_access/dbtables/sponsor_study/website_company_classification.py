"""Classify extracted entities without discarding their raw source evidence."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping
import re
from urllib.parse import unquote, urlsplit

import pandas as pd


CLASSIFICATION_VERSION = "website_company_v2"
CLASSIFICATION_COLUMNS = [
    "company_classification", "company_classification_reason", "company_classification_version",
]
TAXONOMY_PARTS = frozenset({
    "category", "categories", "sector", "sectors", "stage", "stages", "industry",
    "industries", "region", "regions", "tag", "tags", "portfolio-category", "investment-status",
})
DETAIL_PARTS = frozenset({
    "company", "companies", "portfolio", "investments", "investment", "partnerships",
    "current-portfolio", "portfolio-companies", "portfolio-of-investments", "our-company",
})
NON_COMPANY_PARTS = frozenset({
    "news", "press", "blog", "insights", "people", "team", "careers", "contact",
    "esg", "esg-sustainability", "sustainability",
})


class PageClass(StrEnum):
    CATEGORY = "category"
    INDEX = "portfolio_index"
    DETAIL = "company_detail"
    NON_COMPANY = "non_company"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageEvidence:
    url: str
    title: str = ""
    body: str = ""
    company_modal: bool = False

    @classmethod
    def from_record(cls, row: Mapping[str, object]) -> "PageEvidence":
        return cls(
            _text(row.get("final_url")) or _text(row.get("source_url")),
            _text(row.get("page_title")), _text(row.get("body_text")),
            _text(row.get("snapshot_kind")).startswith("portfolio_detail_modal"),
        )


@dataclass(frozen=True)
class CandidateEvidence:
    name: str
    company_url: str
    method: str
    evidence: str
    page: PageEvidence


@dataclass(frozen=True)
class CompanyDecision:
    classification: str
    reason: str


def normalize_company_label(value: str) -> str:
    """Remove explicit image-label wrappers while retaining the displayed brand."""
    return re.sub(r"^logo for\s+|\s+logo$", "", value.strip(), flags=re.I).strip()


def classify_page(page: PageEvidence) -> PageClass:
    parts = [p for p in unquote(urlsplit(page.url).path).casefold().split("/") if p]
    parents = parts[:-1]
    taxonomy = [i for i, part in enumerate(parents) if part in TAXONOMY_PARTS]
    # A company nested below a sector still has its own company route.
    if taxonomy and not any(p in DETAIL_PARTS for p in parents[taxonomy[-1] + 1:]):
        return PageClass.CATEGORY
    if any(p in NON_COMPANY_PARTS for p in parents):
        return PageClass.NON_COMPANY
    archive_title = bool(re.search(r"\barchives?\b", page.title, re.I))
    aggregate = bool(re.search(
        r"\btotal companies\b|\bcompanies are included in more than one\b|\bfilter by (?:sector|industry|stage)\b",
        page.body, re.I,
    ))
    if archive_title and aggregate:
        return PageClass.CATEGORY
    if page.company_modal:
        return PageClass.DETAIL
    if not parts or parts[-1] in DETAIL_PARTS | {"our-companies", "our-portfolio", "all"}:
        return PageClass.INDEX
    if any(p in DETAIL_PARTS for p in parents):
        return PageClass.DETAIL
    return PageClass.UNKNOWN


def classify_company(candidate: CandidateEvidence) -> CompanyDecision:
    if re.fullmatch(r"(?:no (?:records|results|companies)(?: found)?|loading)[.!\s]*", candidate.name.strip(), re.I):
        return CompanyDecision("non_company", "empty_portfolio_state")
    target = classify_page(PageEvidence(candidate.company_url)) if candidate.company_url else PageClass.UNKNOWN
    source = classify_page(candidate.page)
    if target == PageClass.CATEGORY:
        return CompanyDecision("non_company", "category_target_url")
    if target == PageClass.NON_COMPANY:
        if (candidate.method == "portfolio_card_heuristic"
                and candidate.evidence.casefold().startswith(candidate.name.casefold())
                and re.search(r"\b(?:status|sector):", candidate.evidence, re.I)):
            return CompanyDecision("company", "company_card_non_company_link")
        return CompanyDecision("needs_review", "non_company_target_url")
    if candidate.method == "page_title_heuristic" and source in {
        PageClass.CATEGORY, PageClass.INDEX, PageClass.NON_COMPANY,
    }:
        return CompanyDecision("non_company", f"{source.value}_page_title")
    if source == PageClass.NON_COMPANY:
        return CompanyDecision("non_company", "non_portfolio_source_page")
    if not candidate.name:
        return CompanyDecision("needs_review", "missing_company_name")
    if target == PageClass.DETAIL or source == PageClass.DETAIL:
        return CompanyDecision("company", "company_detail_evidence")
    if candidate.page.company_modal and candidate.evidence:
        return CompanyDecision("company", "company_modal_evidence")
    if candidate.method in {"portfolio_card_heuristic", "llm_snapshot"} and candidate.evidence:
        # Index/category sources can contain legitimate text or visual company cards.
        title_name = re.split(r"\s+[|–—-]\s+", candidate.page.title)[0]
        if source == PageClass.CATEGORY and candidate.name.casefold() == re.sub(
            r"\s+archives?$", "", title_name, flags=re.I,
        ).casefold():
            return CompanyDecision("non_company", "category_heading_as_company")
        return CompanyDecision("company", "company_card_evidence")
    return CompanyDecision("needs_review", "missing_company_evidence")


def _text(value: object) -> str:
    return str(value).strip() if value is not None and pd.notna(value) else ""


def classify_holding_candidates(
    candidates: pd.DataFrame, snapshots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add decisions alongside unchanged candidate fields; IDs and capture dates stay fixed."""
    out = candidates.copy()
    pages: dict[tuple[str, str], PageEvidence] = {}
    if snapshots is not None:
        for row in snapshots.to_dict("records"):
            pages[(_text(row.get("crawl_run_id")), _text(row.get("site_snapshot_id")))] = PageEvidence.from_record(row)
    decisions = []
    for row in out.to_dict("records"):
        page = pages.get((_text(row.get("crawl_run_id")), _text(row.get("site_snapshot_id"))), PageEvidence(
            _text(row.get("final_url")) or _text(row.get("source_url")),
        ))
        decision = classify_company(CandidateEvidence(
            _text(row.get("company_name")), _text(row.get("company_url")),
            _text(row.get("extraction_method")), _text(row.get("evidence_text")), page,
        ))
        decisions.append((decision.classification, decision.reason, CLASSIFICATION_VERSION))
    out[CLASSIFICATION_COLUMNS] = pd.DataFrame(decisions, index=out.index, columns=CLASSIFICATION_COLUMNS)
    return out


def accepted_company_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """Enforce the same gate for producer and consumer, including legacy candidate inputs."""
    if candidates.empty:
        return candidates.copy()
    present = set(CLASSIFICATION_COLUMNS) & set(candidates)
    if present and present != set(CLASSIFICATION_COLUMNS):
        raise ValueError("Incomplete website company classification contract")
    if not present:
        candidates = classify_holding_candidates(candidates)
    if not candidates.company_classification.isin({"company", "non_company", "needs_review"}).all():
        raise ValueError("Invalid website company classification")
    # Version upgrades require the original snapshots, through
    # classify_holding_candidates. Consumers must not reinterpret old decisions
    # from candidate URLs alone or downgrade other rows in a mixed-version batch.
    accepted = candidates.loc[
        candidates.company_classification.eq("company")
        & candidates.company_classification_version.eq(CLASSIFICATION_VERSION)
    ].copy()
    if accepted.empty:
        return accepted
    # A contact link inside a genuine company card is not its company website.
    invalid_link = accepted.company_classification_reason.eq("company_card_non_company_link")
    if invalid_link.any():
        accepted.loc[invalid_link, "company_url"] = ""
    return accepted
