"""Typed V31 benchmark sponsor identity bridge to production dim_sponsor."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import (
    _clean_numeric,
    slug,
    stable_id,
)
from data_access.dbtables.sponsor_study.company_aliases import (
    _company_key,
    company_name_variants,
    score_company_name,
    score_company_name_with_aliases,
)
from data_access.dbtables.sponsor_study.fund_vendor import _match_key
from data_access.dbtables.sponsor_study.source_priority import (
    HOLDING_SOURCE_PRIORITY,
    HOLDING_SOURCE_PRIORITY_BY_SYSTEM,
)
from data_importer.sponsor_study.excel_openxml import excel_serial_to_datetime

BENCHMARK_V31_SPONSOR_XREF_COLUMNS = [
    "v31_sponsor_name",
    "prod_sponsor_name",
    "external_sponsor_id",
    "sponsor_slug",
    "match_method",
]
BENCHMARK_V31_PORTCO_COLUMNS = [
    "v31_holding_id",
    "source_workbook",
    "source_sheet",
    "source_excel_row",
    "v31_sponsor_name",
    "fund_name",
    "vintage",
    "company_name",
    "hq",
    "industry",
    "investment_date",
    "current_status",
    "active_flag",
    "deal_size_tev",
    "equity_commitment",
    "entry_multiple",
]
BENCHMARK_V31_COMMENT_COLUMNS = [
    "template_comment_id",
    "source_sheet",
    "source_excel_row",
    "field_name",
    "value",
    "author",
    "comment_text",
]
BENCHMARK_V31_HOLDING_XREF_COLUMNS = [
    "v31_holding_xref_id",
    "v31_holding_id",
    "external_holding_id",
    "external_sponsor_id",
    "external_fund_id",
    "external_portco_id",
    "match_method",
    "match_status",
]
ENTRY_METRIC_FIELDS = frozenset({
    "deal_size_tev",
    "equity_commitment",
    "entry_multiple",
})

V31_TO_PROD_SPONSOR_NAME: dict[str, str] = {
    "Advent": "Advent International",
    "AmSec": "American Securities",
    "Apax": "Apax Partners",
    "Ares": "Ares Management",
    "Audax": "Audax Private Equity",
    "BC Partners": "BC Partners",
    "Bain": "Bain Capital",
    "Berkshire": "Berkshire Partners",
    "Blackstone": "Blackstone",
    "Bridgepoint": "Bridgepoint",
    "Brookfield": "Brookfield Asset Management",
    "CD&R": "Clayton, Dubilier & Rice (CD&R)",
    "CVC": "CVC Capital Partners",
    "Carlyle": "The Carlyle Group",
    "Cinven": "Cinven",
    "Clearlake": "Clearlake Capital Group",
    "EQT": "EQT",
    "GTCR": "GTCR",
    "Genstar": "Genstar Capital",
    "H&F": "Hellman & Friedman",
    "KKR": "KKR",
    "Leonard Green": "Leonard Green & Partners",
    "New Mountain": "New Mountain Capital",
    "Nordic Capital": "Nordic Capital",
    "Onex": "Onex",
    "PAI": "PAI Partners",
    "Permira": "Permira",
    "Platinum": "Platinum Equity",
    "Providence": "Providence Equity Partners",
    "Silver Lake": "Silver Lake",
    "Stone Point": "Stone Point Capital",
    "TA": "TA Associates",
    "TH Lee": "Thomas H. Lee Partners (TH Lee)",
    "TPG": "TPG",
    "Thoma Bravo": "Thoma Bravo",
    "Veritas": "Veritas Capital",
    "Vista": "Vista Equity Partners",
    "Warburg": "Warburg Pincus",
}

PROD_TO_V31_SPONSOR_NAME = {
    prod.lower(): v31 for v31, prod in V31_TO_PROD_SPONSOR_NAME.items()
}

V31_SPONSOR_CANONICAL_NAMES = frozenset(V31_TO_PROD_SPONSOR_NAME)

# Legal / vendor spellings that must resolve to the seed GP. KKR stays "KKR"
# in both v31 and prod maps, so slug aliasing alone never linked Kohlberg.
SEED_SPONSOR_LEGAL_ALIASES: dict[str, tuple[str, ...]] = {
    "Advent": ("Advent International",),
    "AmSec": ("American Securities", "American Securities LLC"),
    "Apax": ("Apax Partners",),
    "Ares": ("Ares Management", "Ares Private Equity Group", "Ares Management Corp (Private Equity)"),
    "Audax": ("Audax Private Equity",),
    "Bain": ("Bain Capital",),
    "Blackstone": ("Blackstone", "Blackstone Corporate Private Equity"),
    "Brookfield": (
        "Brookfield Asset Management", "Brookfield Corporation",
        "Brookfield Asset Management PIC Adviser Private Equity LP",
    ),
    "CD&R": (
        "Clayton Dubilier & Rice",
        "Clayton, Dubilier & Rice",
        "Clayton Dubilier & Rice LLC",
    ),
    "CVC": ("CVC Capital Partners", "CVC Advisers", "CVC Advisers Ltd."),
    "Carlyle": (
        "The Carlyle Group", "Carlyle Group",
        "The Carlyle Group LP (Global Private Equity)",
        "The Carlyle Group LP (Corporate Private Equity)",
    ),
    "Clearlake": ("Clearlake Capital Group",),
    "EQT": ("EQT", "EQT Partners AB"),
    "Genstar": ("Genstar Capital",),
    "H&F": ("Hellman & Friedman", "Hellman & Friedman LLC"),
    "KKR": (
        "Kohlberg Kravis Roberts",
        "Kohlberg Kravis Roberts & Co",
        "Kohlberg Kravis Roberts & Co. LP",
        "KKR & Co",
        "KKR & Co. LP",
        "KKR Group",
    ),
    "Leonard Green": ("Leonard Green & Partners",),
    "New Mountain": ("New Mountain Capital",),
    "PAI": ("PAI Partners",),
    "Permira": ("Permira", "Permira Advisers LLP"),
    "Platinum": ("Platinum Equity", "Platinum Equity Advisors LLC"),
    "Providence": ("Providence Equity Partners",),
    "Silver Lake": ("Silver Lake",),
    "Stone Point": ("Stone Point Capital",),
    "TA": ("TA Associates", "TA Associates Management"),
    "TPG": ("TPG", "TPG Capital LLC"),
    "TH Lee": (
        "Thomas H. Lee Partners",
        "Thomas H. Lee Partners LP",
        "THL Partners",
    ),
    "Veritas": ("Veritas Capital", "Veritas Capital Fund Management"),
    "Vista": ("Vista Equity Partners", "Vista Equity Partners Management LLC"),
    "Warburg": ("Warburg Pincus",),
}

NAME_GATE_MIN_SCORE = 0.65
REVIEW_NAME_MIN_SCORE = 0.45
CORROBORATED_NAME_MIN_SCORE = 0.50
CORROBORATED_NAME_MARGIN_MIN = 0.10
INVESTMENT_DATE_TOLERANCE_DAYS = 366
AUTO_MATCH_CONFIRMATION_POINTS = 3
REVIEW_MAX_CANDIDATES = 3
REVIEWED_CROSS_SPONSOR_METHOD = "reviewed_cross_sponsor_portco"
_TEMPORAL_EXTRAS = frozenset({"investment_date", "exit_date", "transaction_close"})


def prod_sponsor_name_for_v31(v31_sponsor_name: str) -> str:
    return V31_TO_PROD_SPONSOR_NAME[str(v31_sponsor_name).strip()]


def v31_sponsor_name_for_prod(prod_sponsor_name: str) -> str:
    return PROD_TO_V31_SPONSOR_NAME[str(prod_sponsor_name).strip().lower()]


def _sponsor_family_slugs(*names: str) -> set[str]:
    slugs: set[str] = set()
    for name in names:
        key = slug(name)
        if key and key != "unknown":
            slugs.add(key)
    return slugs


def build_sponsor_alias_keys() -> dict[str, list[str]]:
    families: list[set[str]] = []
    for v31_name, prod_name in V31_TO_PROD_SPONSOR_NAME.items():
        family = _sponsor_family_slugs(
            v31_name,
            prod_name,
            *SEED_SPONSOR_LEGAL_ALIASES.get(v31_name, ()),
        )
        if len(family) > 1:
            families.append(family)
    keys: dict[str, set[str]] = {}
    for family in families:
        for member in family:
            keys.setdefault(member, set()).update(family - {member})
    return {key: sorted(values) for key, values in sorted(keys.items())}


SPONSOR_ALIAS_KEYS = build_sponsor_alias_keys()


def build_benchmark_v31_sponsor_xref(
    dim_external_sponsor: pd.DataFrame,
    *,
    v31_sponsor_names: Iterable[str],
    refresh_run_id: str,
    ingested_at: pd.Timestamp | str,
) -> pd.DataFrame:
    required_columns = {"external_sponsor_id", "sponsor_name"}
    missing = required_columns - set(dim_external_sponsor)
    if missing:
        raise ValueError(
            "Production sponsor dimension is missing columns: "
            + ", ".join(sorted(missing))
        )
    sponsors = dim_external_sponsor.dropna(
        subset=["external_sponsor_id", "sponsor_name"]
    ).copy()
    rows: list[dict[str, object]] = []
    for v31_name in sorted({
        str(name).strip() for name in v31_sponsor_names if str(name).strip()
    }):
        prod_name = V31_TO_PROD_SPONSOR_NAME.get(v31_name)
        matches = (
            sponsors[sponsors["sponsor_name"].eq(prod_name)]
            if prod_name is not None
            else sponsors.iloc[0:0]
        )
        sponsor_ids = matches["external_sponsor_id"].astype(str).unique()
        resolved = len(sponsor_ids) == 1
        match_method = (
            "v31_alias_exact"
            if resolved
            else "unknown_v31_sponsor"
            if prod_name is None
            else "ambiguous_prod_sponsor"
            if len(sponsor_ids) > 1
            else "unresolved"
        )
        external_sponsor_id = sponsor_ids[0] if resolved else None
        sponsor_slug = (
            matches.iloc[0].get("sponsor_slug", slug(prod_name))
            if resolved
            else slug(prod_name or v31_name)
        )
        rows.append({
            "v31_sponsor_name": v31_name,
            "prod_sponsor_name": prod_name,
            "external_sponsor_id": external_sponsor_id,
            "sponsor_slug": sponsor_slug,
            "match_method": match_method,
        })
    out = pd.DataFrame(rows, columns=BENCHMARK_V31_SPONSOR_XREF_COLUMNS)
    out["refresh_run_id"] = refresh_run_id
    out["ingested_at"] = pd.Timestamp(ingested_at)
    out.attrs["delta_schema_types"] = {"ingested_at": "TIMESTAMP"}
    return out


def _strict_boolean(values: pd.Series, *, column: str) -> pd.Series:
    normalized = values.astype("string").str.strip().str.lower()
    mapping = {"true": True, "1": True, "false": False, "0": False}
    invalid = normalized.notna() & ~normalized.isin(mapping)
    if invalid.any():
        raise ValueError(
            f"V31 {column} contains invalid values: "
            + ", ".join(sorted(normalized[invalid].unique()))
        )
    return normalized.map(mapping).astype("boolean")


def build_benchmark_v31_portco_snapshot(source: pd.DataFrame) -> pd.DataFrame:
    source_columns = (
        "source_file",
        "source_sheet",
        "source_excel_row",
        "sponsor",
        "fund",
        "fund_used",
        "vintage",
        "company",
        "investment_date",
        "current_status",
        "active_flag",
        "deal_size_tev",
        "equity_commitment",
        "entry_multiple",
    )
    missing = set(source_columns) - set(source)
    if missing:
        raise ValueError(
            "V31 portco source is missing columns: " + ", ".join(sorted(missing))
        )
    selected = list(source_columns)
    for optional_col in ("hq", "industry"):
        if optional_col in source.columns:
            selected.append(optional_col)
    out = source[selected].rename(columns={
        "source_file": "source_workbook",
        "sponsor": "v31_sponsor_name",
        "fund": "fund_name",
        "company": "company_name",
    }).copy()
    for optional_col in ("hq", "industry"):
        if optional_col not in out.columns:
            out[optional_col] = pd.NA
    for column in (
        "source_workbook",
        "source_sheet",
        "v31_sponsor_name",
        "fund_name",
        "fund_used",
        "company_name",
        "current_status",
        "hq",
        "industry",
    ):
        out[column] = out[column].astype("string").str.strip().replace("", pd.NA)
    out["fund_name"] = out["fund_used"].combine_first(out["fund_name"])
    out = out.drop(columns=["fund_used"])
    out = out.dropna(subset=["v31_sponsor_name", "company_name"])
    out["source_excel_row"] = pd.to_numeric(
        out["source_excel_row"], errors="raise"
    ).astype("Int64")
    out["vintage"] = pd.to_numeric(out["vintage"], errors="coerce").astype("Int64")
    out["investment_date"] = out["investment_date"].map(excel_serial_to_datetime)
    out["active_flag"] = _strict_boolean(out["active_flag"], column="active_flag")
    for column in ("deal_size_tev", "equity_commitment", "entry_multiple"):
        out[column] = _clean_numeric(out[column]) if column in out else pd.NA
    identity_columns = [
        "source_workbook",
        "source_sheet",
        "source_excel_row",
        "v31_sponsor_name",
        "company_name",
    ]
    if out[identity_columns].isna().any(axis=None):
        raise ValueError("V31 portco source contains incomplete holding identities")
    holding_key = [
        "v31_sponsor_name",
        "fund_name",
        "company_name",
        "investment_date",
    ]
    out = (
        out.sort_values(["source_sheet", "source_excel_row"])
        .drop_duplicates(holding_key, keep="first")
        .reset_index(drop=True)
    )
    out["v31_holding_id"] = [
        stable_id("v31_holding", *values)
        for values in zip(
            out["v31_sponsor_name"],
            out["fund_name"],
            out["company_name"],
            out["investment_date"],
            strict=True,
        )
    ]
    duplicates = out["v31_holding_id"].duplicated(keep=False)
    if duplicates.any():
        raise ValueError("V31 portco source contains duplicate holding identities")
    out = out.reindex(columns=BENCHMARK_V31_PORTCO_COLUMNS)
    out.attrs["delta_schema_types"] = {
        "source_excel_row": "BIGINT",
        "vintage": "BIGINT",
        "investment_date": "TIMESTAMP",
        "active_flag": "BOOLEAN",
        "deal_size_tev": "DOUBLE",
        "equity_commitment": "DOUBLE",
        "entry_multiple": "DOUBLE",
    }
    return out


def build_benchmark_v31_comment_snapshot(source: pd.DataFrame) -> pd.DataFrame:
    required_columns = {
        "template_comment_id",
        "source_sheet",
        "source_excel_row",
        "field_name",
        "cell_value",
        "comment_author",
        "comment_text",
    }
    missing = required_columns - set(source)
    if missing:
        raise ValueError(
            "V31 comment source is missing columns: " + ", ".join(sorted(missing))
        )
    out = source[list(required_columns)].rename(columns={
        "cell_value": "value",
        "comment_author": "author",
    }).copy()
    out = out[out["field_name"].astype(str).isin(ENTRY_METRIC_FIELDS)]
    out["source_sheet"] = out["source_sheet"].astype("string").str.strip()
    out["field_name"] = out["field_name"].astype("string").str.strip()
    out["source_excel_row"] = pd.to_numeric(
        out["source_excel_row"], errors="raise"
    ).astype("Int64")
    out["value"] = _clean_numeric(out["value"])
    out["author"] = out["author"].astype("string").str.strip().replace("", pd.NA)
    out["comment_text"] = (
        out["comment_text"].astype("string").str.strip().replace("", pd.NA)
    )
    out = out.dropna(subset=["source_sheet", "source_excel_row", "field_name"])
    out = out.drop_duplicates(
        ["source_sheet", "source_excel_row", "field_name"],
        keep="first",
    )
    out = out.reindex(columns=BENCHMARK_V31_COMMENT_COLUMNS)
    out.attrs["delta_schema_types"] = {
        "source_excel_row": "BIGINT",
        "value": "DOUBLE",
    }
    return out


_SOURCE_RANK = {
    **HOLDING_SOURCE_PRIORITY_BY_SYSTEM,
    "preqin": len(HOLDING_SOURCE_PRIORITY),
}

_REGION_CANON = {
    "us": "united_states",
    "usa": "united_states",
    "united_states": "united_states",
    "america": "united_states",
    "uk": "united_kingdom",
    "gb": "united_kingdom",
    "united_kingdom": "united_kingdom",
    "great_britain": "united_kingdom",
    "england": "united_kingdom",
    "na": "north_america",
    "north_america": "north_america",
    "emea": "emea",
    "europe": "europe",
    "eu": "europe",
    "apac": "asia_pacific",
    "asia": "asia",
    "asia_pacific": "asia_pacific",
}

_INDUSTRY_FAMILIES = (
    frozenset({
        "software", "technology", "tech", "saas", "it", "internet",
        "digital", "semiconductor", "electronics", "information",
    }),
    frozenset({
        "health", "healthcare", "pharma", "pharmaceutical", "biotech",
        "biotechnology", "medical", "life", "sciences", "hospital",
    }),
    frozenset({
        "industrial", "industrials", "manufacturing", "manufacturer",
        "chemical", "chemicals", "machinery",
    }),
    frozenset({
        "consumer", "retail", "food", "beverage", "restaurant", "apparel", "cpg",
    }),
    frozenset({
        "financial", "finance", "insurance", "bank", "banking", "fintech",
    }),
    frozenset({
        "energy", "oil", "gas", "power", "utility", "utilities", "renewable",
    }),
    frozenset({
        "telecom", "telecommunications", "media", "communication", "communications",
    }),
    frozenset({"business", "services", "bpo", "outsourcing"}),
    frozenset({"transport", "transportation", "logistics", "automotive"}),
    frozenset({"real", "estate", "infrastructure"}),
)


def _as_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _row_get(row: object, name: str):
    if isinstance(row, pd.Series):
        return row.get(name)
    return getattr(row, name, None)


def _token_set(value) -> set[str]:
    text = _as_text(value)
    if not text:
        return set()
    return {part for part in slug(text).split("_") if part}


def _region_compatible(left, right) -> bool:
    left_tokens = {_REGION_CANON.get(token, token) for token in _token_set(left)}
    right_tokens = {_REGION_CANON.get(token, token) for token in _token_set(right)}
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def _industry_compatible(left, right) -> bool:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens & right_tokens:
        return True
    for family in _INDUSTRY_FAMILIES:
        if (left_tokens & family) and (right_tokens & family):
            return True
    return False


def _year_value(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        year = int(numeric)
        if 1900 <= year <= 2100:
            return year
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.notna(parsed):
        return int(parsed.year)
    return None


def _date_within_year(
    left_date,
    right_date,
    left_vintage=None,
    right_vintage=None,
) -> bool:
    left_dt = pd.to_datetime(left_date, errors="coerce")
    right_dt = pd.to_datetime(right_date, errors="coerce")
    if pd.notna(left_dt) and pd.notna(right_dt):
        delta = abs((left_dt.normalize() - right_dt.normalize()).days)
        return delta <= INVESTMENT_DATE_TOLERANCE_DAYS
    left_year = _year_value(left_dt) or _year_value(left_vintage)
    right_year = _year_value(right_dt) or _year_value(right_vintage)
    return (
        left_year is not None
        and right_year is not None
        and abs(left_year - right_year) <= 1
    )


def _corroboration_extras(v31_row, holding_row) -> tuple[str, ...]:
    extras: list[str] = []
    if _region_compatible(
        _row_get(v31_row, "hq"),
        _row_get(holding_row, "company_country"),
    ):
        extras.append("region")
    if _date_within_year(
        _row_get(v31_row, "investment_date"),
        _row_get(holding_row, "investment_date"),
        _row_get(v31_row, "vintage"),
        _row_get(holding_row, "vintage"),
    ):
        extras.append("investment_date")
    if _industry_compatible(
        _row_get(v31_row, "industry"),
        _row_get(holding_row, "industry"),
    ):
        extras.append("industry")
    return tuple(extras)


def _confirmation_count(extras: tuple[str, ...]) -> int:
    return 2 + len(extras)


def _flagship_fund_ids(dim_external_fund: pd.DataFrame | None) -> set[str]:
    if dim_external_fund is None or dim_external_fund.empty:
        return set()
    if "flagship_flag" not in dim_external_fund.columns:
        return set()
    flagged = dim_external_fund[
        dim_external_fund["flagship_flag"].map(lambda value: bool(value))
    ]
    if flagged.empty:
        return set()
    return set(flagged["external_fund_id"].astype(str))


def _holding_rank(
    row: pd.Series,
    *,
    sponsor_id: str,
    fund_key: str,
    company_key: str,
) -> tuple:
    same_sponsor = _as_text(row.get("external_sponsor_id")) == sponsor_id
    same_fund = bool(fund_key) and _match_key(row.get("fund_name")) == fund_key
    same_company = _company_key(row.get("company_name")) == company_key
    flagship = bool(row.get("flagship_flag", False))
    canonical = bool(row.get("canonical_holding_flag", True))
    source = _SOURCE_RANK.get(_as_text(row.get("holding_source_system")), 9)
    return (
        not (same_sponsor and same_fund and same_company),
        not (same_sponsor and same_company and flagship),
        not (same_sponsor and same_company),
        not canonical,
        source,
        _as_text(row.get("external_holding_id")),
    )


def _pick_holding(
    holdings: pd.DataFrame,
    *,
    sponsor_id: str,
    fund_key: str,
    company_key: str,
) -> pd.Series | None:
    if holdings.empty:
        return None
    ranked = holdings.copy()
    ranked["_rank"] = ranked.apply(
        lambda row: _holding_rank(
            row,
            sponsor_id=sponsor_id,
            fund_key=fund_key,
            company_key=company_key,
        ),
        axis=1,
    )
    return ranked.sort_values("_rank", kind="mergesort").iloc[0]


def _exact_holdings_for_episode(
    holding_index: pd.DataFrame,
    *,
    sponsor_id: str,
    fund_key: str,
    company_key: str,
    investment_date,
    vintage,
) -> tuple[pd.DataFrame, str | None]:
    matches = holding_index[
        holding_index["external_sponsor_id"].astype(str).eq(sponsor_id)
        & holding_index["fund_match_key"].eq(fund_key)
        & holding_index["company_match_key"].eq(company_key)
    ]
    method = None
    if len(matches) > 1 and pd.notna(investment_date) and "investment_date" in matches:
        dated = matches[
            pd.to_datetime(matches["investment_date"], errors="coerce")
            .dt.normalize()
            .eq(pd.Timestamp(investment_date).normalize())
        ]
        if not dated.empty:
            matches = dated
    if len(matches) > 1 and pd.notna(vintage) and "vintage" in matches:
        vintaged = matches[
            pd.to_numeric(matches["vintage"], errors="coerce").eq(int(vintage))
        ]
        if not vintaged.empty:
            matches = vintaged
    if len(matches) > 1:
        matches = matches.sort_values("external_holding_id", kind="mergesort").head(1)
        method = "exact_sponsor_fund_company_deterministic"
    return matches, method


def _name_block_ok(left_key: str, right_key: str) -> bool:
    left = left_key.split()[:1]
    right = right_key.split()[:1]
    if not left or not right:
        return False
    return left[0] == right[0] or left[0].startswith(right[0]) or right[0].startswith(left[0])


def _name_variant_block_ok(left_name: object, right_name: object) -> bool:
    return any(
        _name_block_ok(left_key, right_key)
        for left_key in company_name_variants(left_name)
        for right_key in company_name_variants(right_name)
    )


def _is_short_name_key(key: str) -> bool:
    tokens = key.split()
    return len(tokens) == 1 and len(tokens[0]) <= 4


def _alias_name_relation(
    left_name: object,
    right_name: object,
) -> tuple[bool, bool, bool]:
    pairs = [
        (left_key, right_key)
        for left_key in company_name_variants(left_name)
        for right_key in company_name_variants(right_name)
    ]
    exact_keys = [left for left, right in pairs if left == right]
    if exact_keys:
        return True, False, any(_is_short_name_key(key) for key in exact_keys)
    prefix_keys = [
        min((left, right), key=lambda key: len(key.split()))
        for left, right in pairs
        if left.split() == right.split()[:len(left.split())]
        or right.split() == left.split()[:len(right.split())]
    ]
    if prefix_keys:
        return False, True, any(_is_short_name_key(key) for key in prefix_keys)
    return False, False, False


def _best_fuzzy_same_sponsor(
    v31_row,
    holding_index: pd.DataFrame,
    *,
    sponsor_id: str,
) -> tuple[pd.Series, float, bool, bool, bool, float] | None:
    scoped = holding_index[
        holding_index["external_sponsor_id"].astype(str).eq(sponsor_id)
    ]
    if scoped.empty:
        return None
    v31_name = _as_text(_row_get(v31_row, "company_name"))
    fund_key = _match_key(_row_get(v31_row, "fund_name"))
    company_key = _company_key(v31_name)
    candidates = []
    for _, row in scoped.iterrows():
        if not _name_variant_block_ok(v31_name, row.get("company_name")):
            continue
        score, _, _ = score_company_name_with_aliases(
            v31_name,
            row.get("company_name"),
        )
        alias_exact, prefix_identity, short_name = _alias_name_relation(
            v31_name,
            row.get("company_name"),
        )
        strict_score, _, _ = score_company_name(
            v31_name,
            row.get("company_name"),
        )
        extras = _corroboration_extras(v31_row, row)
        rank = (
            -score,
            not alias_exact,
            not prefix_identity,
            -strict_score,
            -len(extras),
            _holding_rank(
                row,
                sponsor_id=sponsor_id,
                fund_key=fund_key,
                company_key=company_key,
            ),
        )
        candidates.append(
            (rank, row, float(score), alias_exact, prefix_identity, short_name)
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, best_row, best_score, best_exact, best_prefix, best_short = candidates[0]
    if best_score < CORROBORATED_NAME_MIN_SCORE:
        return None
    best_portco_id = _as_text(best_row.get("external_portco_id"))
    competing_scores = [
        score
        for _, row, score, _, _, _ in candidates[1:]
        if _as_text(row.get("external_portco_id")) != best_portco_id
    ]
    candidate_margin = (
        best_score - max(competing_scores)
        if competing_scores
        else 1.0
    )
    return (
        best_row,
        best_score,
        best_exact,
        best_prefix,
        best_short,
        candidate_margin,
    )


def _first_token(name: object) -> str:
    key = _company_key(name)
    return key.split()[0] if key else ""


def _review_name_block_ok(left_name: object, right_name: object) -> bool:
    left = _first_token(left_name)
    right = _first_token(right_name)
    if not left or not right or min(len(left), len(right)) < 3:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _date_delta_days(left, right) -> int | None:
    left_dt = pd.to_datetime(left, errors="coerce")
    right_dt = pd.to_datetime(right, errors="coerce")
    if pd.isna(left_dt) or pd.isna(right_dt):
        return None
    return abs(int((left_dt.normalize() - right_dt.normalize()).days))


def _review_corroboration(v31_row, holding_row) -> tuple[str, ...]:
    extras = list(_corroboration_extras(v31_row, holding_row))
    v31_date = _row_get(v31_row, "investment_date")
    v31_vintage = _row_get(v31_row, "vintage")
    if "investment_date" not in extras and _date_within_year(
        v31_date,
        _row_get(holding_row, "transaction_close_date"),
        v31_vintage,
        None,
    ):
        extras.append("transaction_close")
    if _date_within_year(
        v31_date,
        _row_get(holding_row, "exit_date"),
        v31_vintage,
        None,
    ):
        extras.append("exit_date")
    return tuple(dict.fromkeys(extras))


def _review_eligible(score: float, extras: tuple[str, ...]) -> bool:
    if score < REVIEW_NAME_MIN_SCORE or not extras:
        return False
    temporal = [item for item in extras if item in _TEMPORAL_EXTRAS]
    if score < NAME_GATE_MIN_SCORE:
        return bool(temporal) and len(extras) >= 2
    return True


def _sponsor_name_compatible(left, right) -> bool:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    return bool(left_tokens and right_tokens and (left_tokens & right_tokens))


def _resolve_reviewed_portco_id(
    holdings: pd.DataFrame,
    decision,
) -> str | None:
    holding_id = _as_text(_row_get(decision, "external_holding_key"))
    if holding_id and not holdings.empty and "external_holding_id" in holdings:
        hits = holdings[holdings["external_holding_id"].astype(str).eq(holding_id)]
        portcos = hits["external_portco_id"].dropna().astype(str).unique()
        if len(portcos) == 1:
            return str(portcos[0])
        if len(portcos) > 1:
            raise ValueError(
                f"Reviewed holding {holding_id} maps to multiple portcos"
            )
    if holdings.empty:
        logging.warning(
            "Accepted reviewed decision %s cannot be resolved",
            holding_id,
        )
        return None
    company_key = _company_key(_row_get(decision, "candidate_company_name"))
    sponsor_name = _row_get(decision, "candidate_sponsor_name")
    named = holdings[holdings["company_name"].map(_company_key).eq(company_key)]
    if named.empty:
        logging.warning(
            "Accepted reviewed decision %s cannot be resolved",
            holding_id,
        )
        return None
    if "sponsor_name" in named:
        sponsored = named[named["sponsor_name"].map(
            lambda value: _sponsor_name_compatible(sponsor_name, value)
        )]
        if not sponsored.empty:
            named = sponsored
    portcos = named["external_portco_id"].dropna().astype(str).unique()
    if len(portcos) != 1:
        logging.warning(
            "Accepted reviewed decision %s cannot be resolved",
            holding_id,
        )
        return None
    return str(portcos[0])


def apply_reviewed_cross_sponsor_overlay(
    xref: pd.DataFrame,
    holdings: pd.DataFrame,
    reviewed: pd.DataFrame | None,
) -> pd.DataFrame:
    """Stamp accepted reviews as portco-only matches. Never copies foreign fund/holding."""
    if reviewed is None or reviewed.empty or xref.empty:
        return xref
    accepted = reviewed.copy()
    if "decision_status" in accepted:
        accepted = accepted[accepted["decision_status"].eq("accepted")]
    if accepted.empty:
        return xref
    if accepted["v31_holding_id"].duplicated().any():
        raise ValueError("Reviewed cross-sponsor decisions contain duplicate v31_holding_id")
    out = xref.copy()
    out["_vid"] = out["v31_holding_id"].astype(str)
    by_id = out.set_index("_vid", drop=False)
    for decision in accepted.itertuples(index=False):
        v31_id = _as_text(_row_get(decision, "v31_holding_id"))
        if not v31_id or v31_id not in by_id.index:
            raise ValueError(f"Reviewed decision {v31_id} is not on the V31 spine")
        current = by_id.loc[v31_id]
        if isinstance(current, pd.DataFrame):
            current = current.iloc[0]
        portco_id = _resolve_reviewed_portco_id(holdings, decision)
        if not portco_id:
            continue
        existing = _as_text(current.get("external_portco_id"))
        already_matched = _as_text(current.get("match_status")) == "matched"
        if already_matched and existing == portco_id:
            continue
        idx = out.index[out["v31_holding_id"].astype(str).eq(v31_id)][0]
        out.at[idx, "match_status"] = "matched"
        out.at[idx, "match_method"] = REVIEWED_CROSS_SPONSOR_METHOD
        out.at[idx, "external_portco_id"] = portco_id
        out.at[idx, "external_holding_id"] = pd.NA
        out.at[idx, "external_fund_id"] = pd.NA
        out.at[idx, "v31_holding_xref_id"] = stable_id(
            "v31_holding_xref",
            v31_id,
            "matched",
        )
    return out.drop(columns=["_vid"])


def rank_unmatched_review_candidates(
    portcos: pd.DataFrame,
    xref: pd.DataFrame,
    holdings: pd.DataFrame,
    *,
    max_candidates: int = REVIEW_MAX_CANDIDATES,
) -> pd.DataFrame:
    """Rank evidence-backed candidates for unmatched V31 names. Does not persist."""
    columns = [
        "v31_holding_id",
        "v31_sponsor_name",
        "v31_company_name",
        "v31_fund_name",
        "v31_investment_date",
        "v31_hq",
        "v31_industry",
        "candidate_rank",
        "external_portco_id",
        "external_holding_id",
        "candidate_company_name",
        "candidate_sponsor_name",
        "candidate_fund_name",
        "name_score",
        "strict_score",
        "token_overlap",
        "extras",
        "region_flag",
        "industry_flag",
        "investment_date_flag",
        "exit_date_flag",
        "transaction_close_flag",
        "date_delta_days",
        "ambiguity_margin",
        "ownership_history_risk",
        "same_sponsor_flag",
        "decision_status",
        "reviewed_by",
        "rationale",
    ]
    if portcos.empty or xref.empty or holdings.empty:
        return pd.DataFrame(columns=columns)
    unmatched = xref[xref["match_status"].ne("matched")][
        ["v31_holding_id", "external_sponsor_id"]
    ]
    if unmatched.empty:
        return pd.DataFrame(columns=columns)
    scoped = portcos.merge(unmatched, on="v31_holding_id", how="inner")
    work = holdings.copy()
    work["_first_token"] = work["company_name"].map(_first_token)
    by_token: dict[str, pd.DataFrame] = {
        token: group for token, group in work.groupby("_first_token", sort=False)
        if token
    }
    rows: list[dict[str, object]] = []
    for portco in scoped.itertuples(index=False):
        token = _first_token(portco.company_name)
        if not token:
            continue
        pool_parts = [by_token[token]] if token in by_token else []
        if len(token) >= 4:
            pool_parts.extend(
                group for key, group in by_token.items()
                if key != token and (key.startswith(token) or token.startswith(key))
                and min(len(key), len(token)) >= 4
            )
        if not pool_parts:
            continue
        pool = pd.concat(pool_parts, ignore_index=True)
        best_by_portco: dict[str, dict[str, object]] = {}
        for holding in pool.itertuples(index=False):
            if not _review_name_block_ok(portco.company_name, holding.company_name):
                continue
            cid = _as_text(holding.external_portco_id)
            if not cid:
                continue
            score, overlap, _ = score_company_name_with_aliases(
                portco.company_name,
                holding.company_name,
            )
            extras = _review_corroboration(portco, holding)
            if not _review_eligible(float(score), extras):
                continue
            strict, _, _ = score_company_name(
                portco.company_name,
                holding.company_name,
            )
            v31_date = getattr(portco, "investment_date", None)
            deltas = [
                delta for delta in (
                    _date_delta_days(v31_date, getattr(holding, "investment_date", None)),
                    _date_delta_days(v31_date, getattr(holding, "exit_date", None)),
                    _date_delta_days(
                        v31_date, getattr(holding, "transaction_close_date", None)
                    ),
                ) if delta is not None
            ]
            same_sponsor = _as_text(getattr(portco, "external_sponsor_id", "")) == _as_text(
                getattr(holding, "external_sponsor_id", "")
            )
            rank = (
                -len(extras),
                -int(any(item in _TEMPORAL_EXTRAS for item in extras)),
                -float(score),
                -float(strict),
                _as_text(holding.external_holding_id),
            )
            candidate = {
                "v31_holding_id": portco.v31_holding_id,
                "v31_sponsor_name": portco.v31_sponsor_name,
                "v31_company_name": portco.company_name,
                "v31_fund_name": portco.fund_name,
                "v31_investment_date": getattr(portco, "investment_date", pd.NaT),
                "v31_hq": getattr(portco, "hq", pd.NA),
                "v31_industry": getattr(portco, "industry", pd.NA),
                "external_portco_id": cid,
                "external_holding_id": getattr(holding, "external_holding_id", pd.NA),
                "candidate_company_name": holding.company_name,
                "candidate_sponsor_name": getattr(holding, "sponsor_name", pd.NA),
                "candidate_fund_name": getattr(holding, "fund_name", pd.NA),
                "name_score": float(score),
                "strict_score": float(strict),
                "token_overlap": float(overlap),
                "extras": "|".join(extras),
                "region_flag": "region" in extras,
                "industry_flag": "industry" in extras,
                "investment_date_flag": "investment_date" in extras,
                "exit_date_flag": "exit_date" in extras,
                "transaction_close_flag": "transaction_close" in extras,
                "date_delta_days": min(deltas) if deltas else pd.NA,
                "ownership_history_risk": not same_sponsor,
                "same_sponsor_flag": same_sponsor,
                "decision_status": pd.NA,
                "reviewed_by": pd.NA,
                "rationale": pd.NA,
                "_rank": rank,
            }
            current = best_by_portco.get(cid)
            if current is None or rank < current["_rank"]:
                best_by_portco[cid] = candidate
        ranked = sorted(best_by_portco.values(), key=lambda item: item["_rank"])
        scores = [float(item["name_score"]) for item in ranked]
        for index, item in enumerate(ranked[:max_candidates], start=1):
            nxt = scores[index] if index < len(scores) else None
            item["candidate_rank"] = index
            item["ambiguity_margin"] = (
                item["name_score"] - nxt if nxt is not None else pd.NA
            )
            item.pop("_rank", None)
            rows.append(item)
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns].reset_index(drop=True)


def _xref_row(
    portco,
    *,
    status: str,
    method: str,
    sponsor_id=None,
    holding=None,
) -> dict[str, object]:
    return {
        "v31_holding_xref_id": stable_id(
            "v31_holding_xref",
            portco.v31_holding_id,
            status,
        ),
        "v31_holding_id": portco.v31_holding_id,
        "external_holding_id": None if holding is None else holding.get(
            "external_holding_id"
        ),
        "external_sponsor_id": sponsor_id,
        "external_fund_id": None if holding is None else holding.get(
            "external_fund_id"
        ),
        "external_portco_id": None if holding is None else holding.get(
            "external_portco_id"
        ),
        "match_method": method,
        "match_status": status,
    }


def build_benchmark_v31_holding_xref(
    portcos: pd.DataFrame,
    sponsor_xref: pd.DataFrame,
    holdings: pd.DataFrame,
    *,
    refresh_run_id: str,
    ingested_at: pd.Timestamp | str,
    dim_external_fund: pd.DataFrame | None = None,
    reviewed_cross_sponsor: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if portcos.empty:
        return pd.DataFrame(columns=BENCHMARK_V31_HOLDING_XREF_COLUMNS)
    sponsor_map = (
        sponsor_xref.dropna(subset=["external_sponsor_id"])
        .drop_duplicates("v31_sponsor_name")
        .set_index("v31_sponsor_name")["external_sponsor_id"]
        .astype(str)
    )
    holding_index = pd.DataFrame(columns=holdings.columns)
    if not holdings.empty:
        flagged = holdings.get("canonical_holding_flag", True)
        if not isinstance(flagged, pd.Series):
            holding_index = holdings.copy()
        else:
            holding_index = holdings[flagged.astype("boolean").fillna(True)].copy()
            if holding_index.empty:
                holding_index = holdings.copy()
        holding_index["fund_match_key"] = holding_index["fund_name"].map(_match_key)
        holding_index["company_match_key"] = holding_index["company_name"].map(
            _company_key
        )
        flagship_ids = _flagship_fund_ids(dim_external_fund)
        if "flagship_flag" not in holding_index.columns:
            holding_index["flagship_flag"] = False
        if flagship_ids:
            holding_index["flagship_flag"] = (
                holding_index["external_fund_id"].astype("string").isin(flagship_ids)
            )

    rows: list[dict[str, object]] = []
    for portco in portcos.itertuples(index=False):
        sponsor_id = sponsor_map.get(str(portco.v31_sponsor_name))
        if not sponsor_id:
            rows.append(_xref_row(
                portco,
                status="benchmark_only",
                method="unresolved_sponsor",
            ))
            continue
        fund_key = _match_key(portco.fund_name)
        company_key = _company_key(portco.company_name)
        if holding_index.empty or not company_key:
            rows.append(_xref_row(
                portco,
                status="benchmark_only",
                method="benchmark_only",
                sponsor_id=str(sponsor_id),
            ))
            continue
        matches = pd.DataFrame()
        method = None
        if fund_key:
            matches, method = _exact_holdings_for_episode(
                holding_index,
                sponsor_id=str(sponsor_id),
                fund_key=fund_key,
                company_key=company_key,
                investment_date=portco.investment_date,
                vintage=portco.vintage,
            )
        if not matches.empty:
            match = matches.iloc[0]
            rows.append(_xref_row(
                portco,
                status="matched",
                method=method or "exact_sponsor_fund_company_date",
                sponsor_id=str(sponsor_id),
                holding=match,
            ))
            continue
        same_company = holding_index[
            holding_index["external_sponsor_id"].astype(str).eq(str(sponsor_id))
            & holding_index["company_match_key"].eq(company_key)
        ]
        if not same_company.empty:
            picked = _pick_holding(
                same_company,
                sponsor_id=str(sponsor_id),
                fund_key=fund_key,
                company_key=company_key,
            )
            rows.append(_xref_row(
                portco,
                status="matched",
                method="exact_sponsor_company",
                sponsor_id=str(sponsor_id),
                holding=picked,
            ))
            continue
        fuzzy = _best_fuzzy_same_sponsor(
            portco,
            holding_index,
            sponsor_id=str(sponsor_id),
        )
        if fuzzy is None:
            rows.append(_xref_row(
                portco,
                status="benchmark_only",
                method="benchmark_only",
                sponsor_id=str(sponsor_id),
            ))
            continue
        (
            candidate,
            score,
            alias_exact,
            prefix_identity,
            short_name,
            candidate_margin,
        ) = fuzzy
        extras = _corroboration_extras(portco, candidate)
        if score < NAME_GATE_MIN_SCORE:
            review_extras = _review_corroboration(portco, candidate)
            direct_value = candidate.get(
                "_direct_entity_corroborated",
                False,
            )
            direct_entity = (
                False if pd.isna(direct_value) else bool(direct_value)
            )
            if (
                direct_entity
                and candidate_margin >= CORROBORATED_NAME_MARGIN_MIN
                and _review_eligible(score, review_extras)
            ):
                extra_label = "_".join(review_extras)
                rows.append(_xref_row(
                    portco,
                    status="matched",
                    method=(
                        "corroborated_direct_entity_"
                        f"{extra_label}_{score:.2f}"
                    ),
                    sponsor_id=str(sponsor_id),
                    holding=candidate,
                ))
            else:
                rows.append(_xref_row(
                    portco,
                    status="benchmark_only",
                    method="corroborated_name_gate_missing_evidence",
                    sponsor_id=str(sponsor_id),
                ))
            continue
        if (alias_exact or prefix_identity) and not short_name:
            rows.append(_xref_row(
                portco,
                status="matched",
                method=(
                    "exact_sponsor_company_alias"
                    if alias_exact
                    else "prefix_sponsor_company_identity"
                ),
                sponsor_id=str(sponsor_id),
                holding=candidate,
            ))
            continue
        if _confirmation_count(extras) < AUTO_MATCH_CONFIRMATION_POINTS:
            rows.append(_xref_row(
                portco,
                status="benchmark_only",
                method="name_gate_missing_confirmation",
                sponsor_id=str(sponsor_id),
            ))
            continue
        extra_label = "_".join(extras) if extras else "none"
        rows.append(_xref_row(
            portco,
            status="matched",
            method=f"three_point_auto_{extra_label}_{score:.2f}",
            sponsor_id=str(sponsor_id),
            holding=candidate,
        ))
    out = pd.DataFrame(rows, columns=BENCHMARK_V31_HOLDING_XREF_COLUMNS)
    out = apply_reviewed_cross_sponsor_overlay(
        out,
        holdings,
        reviewed_cross_sponsor,
    )
    out["refresh_run_id"] = refresh_run_id
    out["ingested_at"] = pd.Timestamp(ingested_at)
    out.attrs["delta_schema_types"] = {"ingested_at": "TIMESTAMP"}
    return out


def _direct_entity_corroborated_portcos(
    dataset: dict[str, pd.DataFrame],
) -> set[str]:
    portcos: set[str] = set()
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    if {
        "external_portco_id",
        "factset_portco_entity_id",
    }.issubset(holdings.columns):
        portcos.update(
            holdings.loc[
                holdings["factset_portco_entity_id"].notna(),
                "external_portco_id",
            ].dropna().astype(str)
        )
    stack = dataset.get(
        "xref_external_company_capital_stack",
        pd.DataFrame(),
    )
    if {
        "external_portco_id",
        "seed_bbg_company_id",
        "stack_eligibility",
    }.issubset(stack.columns):
        portcos.update(
            stack.loc[
                stack["stack_eligibility"].eq("admitted")
                & stack["seed_bbg_company_id"].notna(),
                "external_portco_id",
            ].dropna().astype(str)
        )
    decisions = dataset.get(
        "xref_external_company_name_decision",
        pd.DataFrame(),
    )
    if {
        "external_portco_id",
        "decision_method",
        "decision_status",
        "is_active",
    }.issubset(decisions.columns):
        portcos.update(
            decisions.loc[
                decisions["decision_method"].eq(
                    "cross_source_direct_entity_v1"
                )
                & decisions["decision_status"].eq("accepted")
                & decisions["is_active"].astype("boolean").fillna(False),
                "external_portco_id",
            ].dropna().astype(str)
        )
    return portcos



def attach_benchmark_v31_sponsor_xref(
    dataset: dict[str, pd.DataFrame],
    *,
    refresh_run_id: str | None = None,
    ingested_at: pd.Timestamp | str | None = None,
    reviewed_cross_sponsor: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    sponsors = dataset.get("dim_external_sponsor", pd.DataFrame())
    if sponsors.empty:
        sponsors = dataset.get(
            "source_external_sponsor_scope", pd.DataFrame()
        )
    portcos = dataset.get("source_benchmark_v31_portco_row", pd.DataFrame())
    if sponsors.empty or portcos.empty:
        return dataset
    run_meta = dataset.get("meta_external_refresh_run", pd.DataFrame())
    run_id = refresh_run_id
    ingested = ingested_at
    if run_id is None and not run_meta.empty:
        run_id = str(run_meta.iloc[0]["refresh_run_id"])
    if ingested is None and not run_meta.empty:
        ingested = run_meta.iloc[0].get("run_completed_at") or run_meta.iloc[0].get(
            "run_started_at"
        )
    if run_id is None or ingested is None:
        return dataset
    if "v31_sponsor_name" not in portcos:
        raise ValueError("Typed V31 portco snapshot requires v31_sponsor_name")
    out = dict(dataset)
    sponsor_xref = build_benchmark_v31_sponsor_xref(
        sponsors,
        v31_sponsor_names=portcos["v31_sponsor_name"].dropna().astype(str),
        refresh_run_id=str(run_id),
        ingested_at=ingested,
    )
    out["source_benchmark_v31_sponsor_xref"] = sponsor_xref
    comments = dataset.get("source_benchmark_v31_comment", pd.DataFrame())
    if not comments.empty:
        if {"cell_value", "comment_author"}.issubset(comments.columns):
            out["source_benchmark_v31_comment"] = build_benchmark_v31_comment_snapshot(
                comments
            )
        else:
            out["source_benchmark_v31_comment"] = comments.reindex(
                columns=BENCHMARK_V31_COMMENT_COLUMNS
            )
    holdings = dataset.get(
        "_resolved_fact_external_holding_for_matching",
        dataset.get("fact_external_holding", pd.DataFrame()),
    )
    holdings = holdings.copy()
    direct_portcos = _direct_entity_corroborated_portcos(dataset)
    holdings["_direct_entity_corroborated"] = (
        holdings.get(
            "external_portco_id",
            pd.Series(dtype="object"),
        ).astype("string").isin(direct_portcos)
    )
    reviewed = (
        reviewed_cross_sponsor
        if reviewed_cross_sponsor is not None
        else dataset.get("manual_cross_sponsor_decisions", pd.DataFrame())
    )
    out["source_benchmark_v31_holding_xref"] = build_benchmark_v31_holding_xref(
        portcos,
        sponsor_xref,
        holdings,
        refresh_run_id=str(run_id),
        ingested_at=ingested,
        dim_external_fund=dataset.get("dim_external_fund"),
        reviewed_cross_sponsor=reviewed,
    )
    return out


def unresolved_v31_sponsors(
    xref: pd.DataFrame,
    *,
    v31_sponsor_names: Iterable[str] | None = None,
) -> list[str]:
    if xref.empty:
        names = list(v31_sponsor_names or ())
        return sorted({str(name).strip() for name in names if str(name).strip()})
    unresolved = xref[
        xref["external_sponsor_id"].isna()
        | xref["match_method"].astype(str).eq("unresolved")
    ]["v31_sponsor_name"].astype(str).tolist()
    if v31_sponsor_names is None:
        return sorted(set(unresolved))
    requested = {str(name).strip() for name in v31_sponsor_names if str(name).strip()}
    mapped = set(xref["v31_sponsor_name"].astype(str))
    return sorted(requested - mapped | set(unresolved))


def v31_holding_xref_unclassified_count(dataset: dict[str, pd.DataFrame]) -> int:
    portco = dataset.get("source_benchmark_v31_portco_row", pd.DataFrame())
    xref = dataset.get("source_benchmark_v31_holding_xref", pd.DataFrame())
    if portco.empty:
        return 0
    if xref.empty:
        return len(portco)
    allowed = {"matched", "benchmark_only"}
    invalid = xref[~xref["match_status"].astype(str).isin(allowed)]
    missing = set(portco["v31_holding_id"].astype(str)) - set(
        xref["v31_holding_id"].astype(str)
    )
    return len(invalid) + len(missing)


def v31_sponsor_xref_orphan_count(
    dataset: dict[str, pd.DataFrame],
    *,
    require_snapshot: bool = False,
) -> int:
    xref = dataset.get("source_benchmark_v31_sponsor_xref", pd.DataFrame())
    portco = dataset.get("source_benchmark_v31_portco_row", pd.DataFrame())
    if portco.empty or "v31_sponsor_name" not in portco:
        return int(require_snapshot)
    requested = set(portco["v31_sponsor_name"].dropna().astype(str))
    if not requested or xref.empty:
        return max(len(requested), 1)
    relevant = xref[xref["v31_sponsor_name"].astype(str).isin(requested)]
    counts = relevant.groupby("v31_sponsor_name").size()
    invalid = set(counts[counts.ne(1)].index)
    invalid.update(requested - set(counts.index))
    invalid.update(
        relevant.loc[
            relevant["external_sponsor_id"].isna()
            | ~relevant["match_method"].astype(str).eq("v31_alias_exact"),
            "v31_sponsor_name",
        ].astype(str)
    )
    return len(invalid)
