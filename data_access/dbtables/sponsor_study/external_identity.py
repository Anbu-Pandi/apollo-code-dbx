"""Auditable company-name evidence for sponsor-scoped source identity matching."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from urllib.parse import urlsplit

import pandas as pd


LEGAL_SUFFIX_TOKENS = frozenset({
    "ab",
    "ag",
    "as",
    "bv",
    "co",
    "company",
    "corp",
    "corporation",
    "gmbh",
    "inc",
    "incorporated",
    "jsc",
    "kk",
    "kg",
    "limited",
    "llc",
    "llp",
    "lp",
    "ltd",
    "nv",
    "oy",
    "oyj",
    "plc",
    "pte",
    "pty",
    "pvt",
    "sa",
    "sas",
    "sasu",
    "sau",
    "se",
    "spa",
    "srl",
    "sro",
})
CONNECTOR_TOKENS = frozenset({"and", "the"})
GENERIC_SINGLE_TOKENS = frozenset({
    "access",
    "american",
    "capital",
    "first",
    "global",
    "international",
    "national",
    "services",
    "united",
})
STRUCTURAL_GENERIC_TOKENS = frozenset({
    "global",
    "group",
    "holding",
    "holdings",
    "network",
    "networks",
    "software",
    "solution",
    "solutions",
    "system",
    "systems",
    "technology",
    "technologies",
})
TOKEN_MORPHOLOGY = {"technologies": "technology"}
STRUCTURAL_GENERIC_MULTIPLIER = 0.25
INDUSTRY_FAMILIES = (
    frozenset({"software", "technology", "technologies", "tech", "saas"}),
    frozenset({"health", "healthcare", "medical", "pharma", "pharmaceutical"}),
    frozenset({"financial", "finance", "fintech", "banking", "insurance"}),
    frozenset({"industrial", "industrials", "manufacturing"}),
    frozenset({"consumer", "retail"}),
    frozenset({"business", "services"}),
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_POSSESSIVE_RE = re.compile(r"(?<=[a-z0-9])['’]s\b")
_DOTTED_SUFFIX_RE = re.compile(
    r"(?<![a-z])((?:[a-z]\s*\.\s*){1,5}[a-z]\.?)\s*$"
)
# Name score is mandatory; fund agreement, exact core-token, candidate
# separation, generic-name, and episode gates remain independent.
AUTO_NAME_SCORE_MIN = 0.96
AUTO_CANDIDATE_MARGIN_MIN = 0.15
AUTO_SINGLE_CORE_MARGIN_MIN = 0.25
AUTO_SHORTEST_NAME_LENGTH_MIN = 4


@dataclass(frozen=True)
class CompanyNameEvidence:
    left_key: str
    right_key: str
    character_score: float
    token_order_score: float
    token_coverage_score: float
    prefix_match: bool
    exact_match: bool
    name_score: float
    shortest_compact_length: int
    generic_short_name: bool
    exact_core_token_match: bool
    shared_core_token_count: int


def compatible_investment_dates(dates: tuple[pd.Timestamp, ...]) -> bool:
    return not dates or max(dates) <= min(dates) + pd.DateOffset(years=10)


@dataclass(frozen=True)
class CompanySourceObservation:
    sponsor_id: str
    company_id: str
    company_name: str
    source_system: str
    fund_id: str = ""
    fund_is_assumed: bool = False
    fund_evidence_independent: bool = False
    vintage: int | None = None
    investment_year: int | None = None
    exit_year: int | None = None
    industry: str = ""
    country: str = ""
    company_domain: str = ""


@dataclass(frozen=True)
class CompanyIdentityEvidence:
    name: CompanyNameEvidence
    same_fund: bool
    same_vintage: bool
    independent_fund_match: bool
    shared_fund_count: int
    shared_cohort_count: int
    investment_year_distance: int | None
    industry_match: bool
    country_match: bool
    company_domain_match: bool
    candidate_margin: float
    ownership_episode_conflict: bool
    corroboration_count: int
    auto_match_eligible: bool


def normalized_company_tokens(value: object) -> tuple[str, ...]:
    """Normalize spelling while retaining identity-bearing structural words."""
    if value is None or pd.isna(value):
        return ()
    text = _POSSESSIVE_RE.sub(
        "s",
        str(value).lower().replace("&", " and "),
    )
    text = re.sub(r"\blogo(?:\s+image)?\s+thumbnail\b", " ", text)
    text = re.sub(r"\barchives?\b", " ", text)
    text = re.sub(r"\ba\s*/\s*s\b", " as ", text)
    dotted = _DOTTED_SUFFIX_RE.search(text)
    if dotted:
        compact = "".join(_TOKEN_RE.findall(dotted.group(1)))
        if compact in LEGAL_SUFFIX_TOKENS:
            text = f"{text[:dotted.start()]} {compact}"
    tokens = [
        token
        for token in _TOKEN_RE.findall(text)
        if token not in CONNECTOR_TOKENS
    ]
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return tuple(tokens)


def normalized_company_name(value: object) -> str:
    return " ".join(normalized_company_tokens(value))


def _scoring_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(TOKEN_MORPHOLOGY.get(token, token) for token in tokens)


def _token_weight(token: str) -> float:
    return (
        STRUCTURAL_GENERIC_MULTIPLIER
        if token in STRUCTURAL_GENERIC_TOKENS
        else 1.0
    )


def _weighted_token_f1(
    left_tokens: tuple[str, ...],
    right_tokens: tuple[str, ...],
) -> float:
    left_counts = Counter(left_tokens)
    right_counts = Counter(right_tokens)
    overlap = sum(
        min(left_counts[token], right_counts[token]) * _token_weight(token)
        for token in left_counts.keys() & right_counts.keys()
    )
    left_weight = sum(
        count * _token_weight(token) for token, count in left_counts.items()
    )
    right_weight = sum(
        count * _token_weight(token) for token, count in right_counts.items()
    )
    if not overlap or not left_weight or not right_weight:
        return 0.0
    precision = overlap / left_weight
    recall = overlap / right_weight
    return 2 * precision * recall / (precision + recall)


def _token_order_similarity(
    left_tokens: tuple[str, ...],
    right_tokens: tuple[str, ...],
) -> float:
    shorter_length = min(len(left_tokens), len(right_tokens))
    if not shorter_length:
        return 0.0
    matching = SequenceMatcher(None, left_tokens, right_tokens)
    return sum(block.size for block in matching.get_matching_blocks()) / shorter_length


def score_company_identity_name(
    left_name: object,
    right_name: object,
) -> CompanyNameEvidence:
    normalized_left_tokens = normalized_company_tokens(left_name)
    normalized_right_tokens = normalized_company_tokens(right_name)
    left_key = " ".join(normalized_left_tokens)
    right_key = " ".join(normalized_right_tokens)
    if not left_key or not right_key:
        return CompanyNameEvidence(
            left_key=left_key,
            right_key=right_key,
            character_score=0.0,
            token_order_score=0.0,
            token_coverage_score=0.0,
            prefix_match=False,
            exact_match=False,
            name_score=0.0,
            shortest_compact_length=0,
            generic_short_name=False,
            exact_core_token_match=False,
            shared_core_token_count=0,
        )

    left_tokens = _scoring_tokens(normalized_left_tokens)
    right_tokens = _scoring_tokens(normalized_right_tokens)
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    character_score = SequenceMatcher(
        None,
        "".join(left_tokens),
        "".join(right_tokens),
    ).ratio()
    token_order_score = _token_order_similarity(left_tokens, right_tokens)
    token_coverage_score = _weighted_token_f1(left_tokens, right_tokens)
    prefix_match = (
        len(shorter) < len(longer)
        and longer[:len(shorter)] == shorter
    )
    exact_match = left_tokens == right_tokens
    shared_core_tokens = (
        left_set & right_set
    ) - STRUCTURAL_GENERIC_TOKENS
    blended_score = (
        0.60 * token_coverage_score
        + 0.25 * character_score
        + 0.15 * token_order_score
    )
    prefix_score = 0.0
    if prefix_match and shared_core_tokens:
        unmatched_weight = sum(
            _token_weight(token) for token in longer[len(shorter):]
        )
        prefix_score = max(0.0, 1.0 - 0.02 * unmatched_weight)
    name_score = min(1.0, max(blended_score, prefix_score))
    shortest_compact_length = min(
        len("".join(left_tokens)),
        len("".join(right_tokens)),
    )
    generic_short_name = (
        len(shorter) == 1 and shorter[0] in GENERIC_SINGLE_TOKENS
    )
    return CompanyNameEvidence(
        left_key=left_key,
        right_key=right_key,
        character_score=character_score,
        token_order_score=token_order_score,
        token_coverage_score=token_coverage_score,
        prefix_match=prefix_match,
        exact_match=exact_match,
        name_score=name_score,
        shortest_compact_length=shortest_compact_length,
        generic_short_name=generic_short_name,
        exact_core_token_match=bool(shared_core_tokens),
        shared_core_token_count=len(shared_core_tokens),
    )


def normalized_company_domain(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").removeprefix("www.")
    return host.rstrip(".")


def values_compatible(left: object, right: object) -> bool:
    left_tokens = set(normalized_company_tokens(left))
    right_tokens = set(normalized_company_tokens(right))
    if not left_tokens or not right_tokens:
        return False
    if left_tokens & right_tokens:
        return True
    return any(
        left_tokens & family and right_tokens & family
        for family in INDUSTRY_FAMILIES
    )


def _minimum_year_distance(
    left: tuple[CompanySourceObservation, ...],
    right: tuple[CompanySourceObservation, ...],
) -> int | None:
    distances = [
        abs(left_row.investment_year - right_row.investment_year)
        for left_row in left
        for right_row in right
        if left_row.investment_year is not None
        and right_row.investment_year is not None
    ]
    return min(distances) if distances else None


def evaluate_company_identity(
    left: tuple[CompanySourceObservation, ...],
    right: tuple[CompanySourceObservation, ...],
    *,
    candidate_margin: float,
) -> CompanyIdentityEvidence:
    if not left or not right:
        raise ValueError("Company identity evidence requires both source observations")
    if {row.sponsor_id for row in left + right} != {left[0].sponsor_id}:
        raise ValueError("Company identity evidence must be sponsor-scoped")

    scored_names = [
        score_company_identity_name(left_name, right_name)
        for left_name in sorted({row.company_name for row in left})
        for right_name in sorted({row.company_name for row in right})
    ]
    name = max(
        scored_names,
        key=lambda item: (
            item.name_score,
            item.character_score,
            item.left_key,
            item.right_key,
        ),
    )
    left_funds = {
        row.fund_id for row in left if row.fund_id and not row.fund_is_assumed
    }
    right_funds = {
        row.fund_id for row in right if row.fund_id and not row.fund_is_assumed
    }
    shared_fund_count = len(left_funds & right_funds)
    left_cohorts = {
        (row.fund_id, row.vintage)
        for row in left
        if row.fund_id
        and not row.fund_is_assumed
        and row.vintage is not None
    }
    right_cohorts = {
        (row.fund_id, row.vintage)
        for row in right
        if row.fund_id
        and not row.fund_is_assumed
        and row.vintage is not None
    }
    shared_cohort_count = len(left_cohorts & right_cohorts)
    left_independent_funds = {
        row.fund_id
        for row in left
        if row.fund_id
        and not row.fund_is_assumed
        and row.fund_evidence_independent
    }
    right_independent_funds = {
        row.fund_id
        for row in right
        if row.fund_id
        and not row.fund_is_assumed
        and row.fund_evidence_independent
    }
    independent_fund_match = bool(
        left_independent_funds & right_independent_funds
    )
    investment_year_distance = _minimum_year_distance(left, right)
    industry_match = any(
        values_compatible(left_row.industry, right_row.industry)
        for left_row in left
        for right_row in right
    )
    country_match = any(
        left_row.country
        and right_row.country
        and left_row.country.casefold() == right_row.country.casefold()
        for left_row in left
        for right_row in right
    )
    company_domain_match = any(
        left_row.company_domain
        and left_row.company_domain == right_row.company_domain
        for left_row in left
        for right_row in right
    )
    left_has_active = any(row.exit_year is None for row in left)
    right_has_exit = any(row.exit_year is not None for row in right)
    ownership_episode_conflict = (
        shared_fund_count > 1 and left_has_active and right_has_exit
    )
    corroboration_count = sum((
        independent_fund_match,
        investment_year_distance is not None and investment_year_distance <= 1,
        industry_match,
        country_match,
        company_domain_match,
    ))
    # Fund overlap constrains which holding rows may collapse. It is not treated
    # as independent corroboration when the website fund came from this same
    # name match; that distinction is persisted for audit.
    auto_match_eligible = (
        name.name_score >= AUTO_NAME_SCORE_MIN
        and name.shortest_compact_length >= AUTO_SHORTEST_NAME_LENGTH_MIN
        and not name.generic_short_name
        and name.exact_core_token_match
        and shared_cohort_count > 0
        and candidate_margin >= AUTO_CANDIDATE_MARGIN_MIN
        and (
            name.shared_core_token_count > 1
            or independent_fund_match
            or candidate_margin >= AUTO_SINGLE_CORE_MARGIN_MIN
        )
        and not ownership_episode_conflict
    )
    return CompanyIdentityEvidence(
        name=name,
        same_fund=shared_fund_count > 0,
        same_vintage=shared_cohort_count > 0,
        independent_fund_match=independent_fund_match,
        shared_fund_count=shared_fund_count,
        shared_cohort_count=shared_cohort_count,
        investment_year_distance=investment_year_distance,
        industry_match=industry_match,
        country_match=country_match,
        company_domain_match=company_domain_match,
        candidate_margin=candidate_margin,
        ownership_episode_conflict=ownership_episode_conflict,
        corroboration_count=corroboration_count,
        auto_match_eligible=auto_match_eligible,
    )
