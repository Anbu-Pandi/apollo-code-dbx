"""Publication decisions use measured capture quality, independently of fund matches."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from data_access.dbtables.sponsor_study.canonical import slug
from data_access.dbtables.sponsor_study.website_company_classification import (
    PageClass, PageEvidence, classify_page, normalize_company_label,
)


class SponsorStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    EMPTY = "empty"
    VENDOR_ONLY = "skipped_vendor_only"


@dataclass(frozen=True)
class SponsorCaptureQuality:
    holdings: int
    blocked: int = 0
    errors: int = 0
    page_limits: int = 0
    llm_failures: int = 0
    review_candidates: int = 0
    missing_companies: int = 0
    vendor_only: bool = False

    def __post_init__(self) -> None:
        if min(self.holdings, self.blocked, self.errors, self.page_limits,
               self.llm_failures, self.review_candidates, self.missing_companies) < 0:
            raise ValueError("Capture quality counts must be non-negative")

    @property
    def status(self) -> SponsorStatus:
        if self.vendor_only:
            return SponsorStatus.VENDOR_ONLY
        if not self.holdings:
            if self.errors:
                return SponsorStatus.FAILED
            return SponsorStatus.BLOCKED if self.blocked else SponsorStatus.EMPTY
        return SponsorStatus.PARTIAL if self.problems else SponsorStatus.SUCCEEDED

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(f"{count} {label}" for count, label in (
            (self.blocked, "blocked snapshots"), (self.errors, "snapshot errors"),
            (self.page_limits, "page-limited snapshots"), (self.llm_failures, "LLM extraction failures"),
            (self.review_candidates, "unresolved company candidates"),
            (self.missing_companies, "discovered companies missing from extraction"),
        ) if count)

    @property
    def publishable(self) -> bool:
        return self.status in {SponsorStatus.SUCCEEDED, SponsorStatus.VENDOR_ONLY}


class IncompleteWebsiteCrawl(RuntimeError):
    """A persisted partial attempt must be visible to its scheduler as a failure."""


@dataclass(frozen=True)
class CompanyReference:
    name: str
    url: str

    @property
    def name_key(self) -> str:
        return slug(normalize_company_label(self.name))

    @property
    def url_key(self) -> str:
        parts = urlsplit(self.url)
        return urlunsplit(("https", parts.netloc.casefold().removeprefix("www."),
                          parts.path.rstrip("/"), parts.query, parts.fragment)) if self.url else ""


@dataclass(frozen=True)
class CompanyCoverage:
    discovered: int
    extracted: int
    missing: tuple[str, ...]


def company_coverage(
    discovered: tuple[CompanyReference, ...], extracted: tuple[CompanyReference, ...],
) -> CompanyCoverage:
    """Company names and specific card URLs bridge text, modal, and logo evidence."""
    names = {company.name_key for company in extracted if company.name_key}
    extracted_url_names: dict[str, set[str]] = {}
    discovered_url_names: dict[str, set[str]] = {}
    for references, groups in ((extracted, extracted_url_names), (discovered, discovered_url_names)):
        for company in references:
            if company.url_key:
                groups.setdefault(company.url_key, set()).add(company.name_key)
    # Listing URLs shared by many companies cannot stand in for company identity.
    urls = {company.url_key for company in extracted if company.url_key
            and (urlsplit(company.url).fragment or classify_page(PageEvidence(company.url)) not in {
                PageClass.INDEX, PageClass.CATEGORY, PageClass.NON_COMPANY})
            and len(extracted_url_names[company.url_key]) == 1}
    missing = set()
    identities = set()
    for company in discovered:
        identity = company.name_key or company.url_key
        if not identity:
            raise ValueError("Discovered company requires a name or a specific card URL")
        identities.add(identity)
        shared_discovery_url = len(discovered_url_names.get(company.url_key, ())) > 1
        if company.name_key not in names and (company.url_key not in urls or shared_discovery_url):
            missing.add(company.name or company.url)
    return CompanyCoverage(len(identities), len(names), tuple(sorted(missing)))


def assert_crawl_publishable(outcomes: tuple[tuple[str, SponsorStatus], ...]) -> None:
    failures = [f"{sponsor}: {status}" for sponsor, status in outcomes
                if status not in {SponsorStatus.SUCCEEDED, SponsorStatus.VENDOR_ONLY}]
    if not outcomes or failures:
        raise IncompleteWebsiteCrawl("Website crawl incomplete: " + ("; ".join(failures) or "no sponsor outcomes"))
