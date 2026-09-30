from difflib import SequenceMatcher
import re
from pathlib import Path

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_importer.sponsor_study.template_workbook import _is_excel_error_fund_name


MANUAL_ALIAS_OVERRIDE_SHEET = "xref_map"


FUND_VENDOR_COMPARISON_COLUMNS = [
    "fund_vendor_comparison_id",
    "fund_id",
    "source_system",
    "source_fund_id",
    "sponsor_name_seed",
    "fund_name_seed",
    "vintage_seed",
    "sponsor_name_vendor",
    "fund_name_vendor",
    "vintage_vendor",
    "match_status",
    "match_method",
    "as_of_date",
    "commitment_usd_m",
    "pct_called",
    "dpi",
    "rvpi",
    "net_moic",
    "net_irr",
    "fund_status",
    "fund_strategy",
]

FUND_VENDOR_COVERAGE_COLUMNS = [
    "source_system",
    "seed_fund_count",
    "matched_fund_count",
    "matched_fund_pct",
    "funds_with_as_of_date_count",
    "funds_with_commitment_count",
    "funds_with_pct_called_count",
    "funds_with_dpi_count",
    "funds_with_rvpi_count",
    "funds_with_net_moic_count",
    "funds_with_net_irr_count",
]

FUND_ALIAS_COLUMNS = [
    "fund_alias_id",
    "fund_id",
    "sponsor_id",
    "sponsor",
    "sponsor_fund_name",
    "sponsor_fund_slug",
    "sponsor_vintage",
    "source_system",
    "source_entity_id",
    "source_entity_table",
    "source_sponsor_id",
    "source_sponsor_name",
    "source_fund_name",
    "source_fund_slug",
    "source_vintage",
    "source_as_of_date",
    "fund_name_similarity",
    "sponsor_name_similarity",
    "token_overlap_score",
    "vintage_match_flag",
    "match_score",
    "alias_type",
    "match_method",
    "match_confidence",
    "review_status",
    "preferred_metric_source_system",
    "evidence_source",
]

INFRASTRUCTURE_FUND_HINTS = ("infrastructure", "infra")
REVIEWED_FUND_ALIAS_STATUSES = {"auto_matched", "manual_matched", "needs_review"}

STOPWORDS = {
    "a",
    "and",
    "co",
    "company",
    "fund",
    "funds",
    "gp",
    "inc",
    "international",
    "limited",
    "llc",
    "lp",
    "ltd",
    "management",
    "partners",
    "partnership",
    "private",
    "the",
}

ROMAN_NUMERALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
}


def _match_key(value) -> str:
    if pd.isna(value):
        return "unknown"
    text = str(value).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    tokens = [ROMAN_NUMERALS.get(token, token) for token in tokens]
    tokens = [token for token in tokens if token not in STOPWORDS]
    return "_".join(tokens) or slug(value)


def _tokens(value) -> set[str]:
    if pd.isna(value):
        return set()
    return {
        ROMAN_NUMERALS.get(token, token)
        for token in re.findall(r"[a-z0-9]+", str(value).lower())
        if token not in STOPWORDS
    }


def _ratio(left, right) -> float:
    left_key = " ".join(sorted(_tokens(left)))
    right_key = " ".join(sorted(_tokens(right)))
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


SPONSOR_CONTAINMENT_AGREEMENT = 0.95

_ORDINAL_TOKENS = frozenset(ROMAN_NUMERALS.values())


def _ordered_tokens(value) -> list[str]:
    if pd.isna(value):
        return []
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value).lower())
        if token not in STOPWORDS
    ]


def _spells_out(initials: str, tokens: list[str]) -> bool:
    """Whether `initials` is the letter sequence of `tokens`, taken as initials or prefixes.

    Every token has to claim at least one letter, so the whole vendor name is accounted for:
    `cdr` spells out Clayton|Dubilier|Rice and `amsec` spells out Am(erican)|Sec(urities), while
    `ta` cannot spell out Thomas|H|Lee. The split needs backtracking because a greedy pass would
    let `kr` fall entirely inside Kravis and starve Roberts of a letter.
    """
    if not initials or not tokens:
        return False
    if len(tokens) == 1:
        return tokens[0].startswith(initials)
    head, rest = tokens[0], tokens[1:]
    longest = min(len(head), len(initials) - len(rest))
    for take in range(1, longest + 1):
        if head.startswith(initials[:take]) and _spells_out(initials[take:], rest):
            return True
    return False


def _sponsor_agreement(left, right) -> float:
    """Similarity between a study sponsor label and a vendor's name for the same firm.

    Vendors carry the legal advisory entity where v31 carries the house name, so a raw
    character ratio punishes the verbosity rather than any real disagreement: `CVC` against
    `CVC Advisers Ltd.` scores 0.43 and drags an otherwise perfect fund match below the
    auto-accept line. Two relationships are therefore read as agreement — one side's tokens
    contained in the other's, and one side abbreviating the other, which is how the study
    writes `KKR`, `CD&R`, `AmSec` and `H&F`. Unrelated houses satisfy neither and still fall
    through to the ratio, so `TPG` stays away from `Angelo, Gordon & Co.`
    """
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if left_tokens and right_tokens and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    ):
        return SPONSOR_CONTAINMENT_AGREEMENT
    left_ordered = _ordered_tokens(left)
    right_ordered = _ordered_tokens(right)
    if _spells_out("".join(left_ordered), right_ordered) or _spells_out(
        "".join(right_ordered), left_ordered
    ):
        return SPONSOR_CONTAINMENT_AGREEMENT
    return _ratio(left, right)


def _series_ordinals(value) -> set[str]:
    return _tokens(value) & _ORDINAL_TOKENS


def _series_conflict(left, right) -> bool:
    """Whether two fund names name different vintages of the same series.

    Fund families differ by one ordinal, so `CVC Capital Partners VI` and
    `CVC Capital Partners Asia Pacific IV` read as near-identical to a character scorer. Once
    sponsor verbosity stops suppressing the score, that near-identity is enough to auto-accept
    the wrong fund, so a disagreeing ordinal has to block it.
    """
    left_series = _series_ordinals(left)
    right_series = _series_ordinals(right)
    return bool(left_series and right_series and not (left_series & right_series))


def _token_overlap(left, right) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)


def _vintage_match(left, right) -> bool:
    left_year = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right_year = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    return bool(pd.notna(left_year) and pd.notna(right_year) and int(left_year) == int(right_year))


def _seed_funds(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    funds = dataset.get("dim_fund", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    if funds.empty:
        return pd.DataFrame(columns=["fund_id", "sponsor_id", "sponsor_name_seed", "fund_name_seed", "vintage_seed"])
    out = funds.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    out = out.rename(columns={
        "sponsor_name": "sponsor_name_seed",
        "fund_name": "fund_name_seed",
        "vintage": "vintage_seed",
    })
    out["sponsor_match_key"] = out["sponsor_name_seed"].map(_match_key)
    out["fund_match_key"] = out["fund_name_seed"].map(_match_key)
    return out[["fund_id", "sponsor_id", "sponsor_name_seed", "fund_name_seed", "vintage_seed", "sponsor_match_key", "fund_match_key"]].drop_duplicates()


def _vendor_funds(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    facts = dataset.get("fact_fund_performance_snapshot", pd.DataFrame())
    if facts.empty:
        return pd.DataFrame(columns=[
            "source_system",
            "source_fund_id",
            "sponsor_name_vendor",
            "fund_name_vendor",
            "vintage_vendor",
            "sponsor_match_key",
            "fund_match_key",
        ])
    out = facts.copy()
    out = out.rename(columns={
        "sponsor_name": "sponsor_name_vendor",
        "fund_name": "fund_name_vendor",
        "vintage": "vintage_vendor",
    })
    out["sponsor_match_key"] = out["sponsor_name_vendor"].map(_match_key)
    out["fund_match_key"] = out["fund_name_vendor"].map(_match_key)
    out = out.sort_values(["source_system", "source_fund_id", "as_of_date"], ascending=[True, True, False])
    return out.drop_duplicates(["source_system", "source_fund_id"])


def _preqin_asset_scope_hint(fund_name) -> str | None:
    text = str(fund_name or "").strip().lower()
    if any(hint in text for hint in INFRASTRUCTURE_FUND_HINTS):
        return "infrastructure"
    return None


def _scope_preqin_candidates(candidates: pd.DataFrame, *, asset_scope: str | None) -> pd.DataFrame:
    if candidates.empty or not asset_scope or "preqin_asset_class" not in candidates.columns:
        return candidates
    scoped = candidates[candidates["preqin_asset_class"].astype("string").str.lower().eq(asset_scope)]
    return scoped if not scoped.empty else candidates


def _preqin_metric_funds(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    facts = dataset.get("fact_fund_performance_snapshot", pd.DataFrame())
    if facts.empty:
        return pd.DataFrame(columns=[
            "source_fund_id",
            "source_fund_source_table",
            "preqin_asset_class",
            "source_sponsor_id",
            "sponsor_name",
            "fund_name",
            "vintage",
            "as_of_date",
            "commitment_usd_m",
            "pct_called",
            "dpi",
            "rvpi",
            "net_moic",
            "net_irr",
        ])
    out = facts[facts["source_system"].eq("preqin")].copy()
    if out.empty:
        return out
    if "source_fund_source_table" not in out.columns:
        out["source_fund_source_table"] = pd.NA
    if "preqin_asset_class" not in out.columns:
        out["preqin_asset_class"] = out.get("buyout_strategy_flag", pd.Series(False, index=out.index)).map(
            {True: "private_equity", False: "infrastructure"}
        )
    out["sponsor_match_key"] = out["sponsor_name"].map(_match_key)
    out["fund_match_key"] = out["fund_name"].map(_match_key)
    out["fund_tokens"] = out["fund_name"].map(_tokens)
    out["sponsor_tokens"] = out["sponsor_name"].map(_tokens)
    out = out.sort_values(["source_fund_id", "source_fund_source_table", "as_of_date"], ascending=[True, True, False])
    return out.drop_duplicates(["source_fund_id", "source_fund_source_table"]).reset_index(drop=True)


def _factset_pevc_funds(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    summary = dataset.get("source_factset_pevc_fund_summary", pd.DataFrame())
    investment = dataset.get("source_factset_pevc_portco_investment", pd.DataFrame())
    frames = []
    if not summary.empty:
        frames.append(pd.DataFrame({
            "source_fund_id": summary["factset_fund_entity_id"],
            "source_fund_source_table": "pevc_fund",
            "source_sponsor_id": summary["factset_gp_entity_id"],
            "sponsor_name": summary["sponsor_name_factset"],
            "fund_name": summary["fund_name_factset"],
            "vintage": summary["vintage"],
            "as_of_date": summary.get("as_of_date", pd.Series(pd.NaT, index=summary.index)),
            "commitment_usd_m": summary.get("commitment_usd_m", pd.Series(pd.NA, index=summary.index)),
            "fund_strategy": summary.get("fund_strategy", pd.Series(pd.NA, index=summary.index)),
            "_source_priority": 0,
        }))
    if not investment.empty:
        fund_name = investment["transaction_fund_name"].combine_first(investment["fund_name_factset"])
        fund_id = investment["transaction_fund_entity_id"].combine_first(investment["factset_fund_entity_id"])
        frames.append(pd.DataFrame({
            "source_fund_id": fund_id,
            "source_fund_source_table": "pevc_fund",
            "source_sponsor_id": investment["factset_gp_entity_id"],
            "sponsor_name": investment["sponsor_name_factset"],
            "fund_name": fund_name,
            "vintage": investment["vintage"],
            "as_of_date": pd.NaT,
            "commitment_usd_m": pd.NA,
            "fund_strategy": investment.get("fund_strategy", pd.Series(pd.NA, index=investment.index)),
            "_source_priority": 1,
        }))
    if not frames:
        return pd.DataFrame(columns=[
            "source_fund_id",
            "source_fund_source_table",
            "source_sponsor_id",
            "sponsor_name",
            "fund_name",
            "vintage",
            "as_of_date",
            "commitment_usd_m",
            "fund_strategy",
            "sponsor_match_key",
            "fund_match_key",
            "fund_tokens",
            "sponsor_tokens",
        ])
    out = pd.concat(frames, ignore_index=True)
    out = out[out["source_fund_id"].notna() & out["fund_name"].notna()].copy()
    out["sponsor_match_key"] = out["sponsor_name"].map(_match_key)
    out["fund_match_key"] = out["fund_name"].map(_match_key)
    out["fund_tokens"] = out["fund_name"].map(_tokens)
    out["sponsor_tokens"] = out["sponsor_name"].map(_tokens)
    out["_vintage_numeric"] = pd.to_numeric(out["vintage"], errors="coerce")
    out = out.sort_values(["source_fund_id", "_source_priority", "as_of_date"], ascending=[True, True, False])
    return out.drop_duplicates(["source_fund_id"]).drop(columns=["_source_priority", "_vintage_numeric"]).reset_index(drop=True)


def _accepted_preqin_aliases(alias_map: pd.DataFrame) -> pd.DataFrame:
    if alias_map is None or alias_map.empty:
        return pd.DataFrame(columns=["fund_id", "preqin_fund_name", "preqin_sponsor_name", "preqin_vintage"])
    preqin = alias_map[
        alias_map["source_system"].eq("preqin")
        & alias_map["review_status"].isin(REVIEWED_FUND_ALIAS_STATUSES)
        & alias_map["source_entity_id"].notna()
    ].copy()
    if preqin.empty:
        return pd.DataFrame(columns=["fund_id", "preqin_fund_name", "preqin_sponsor_name", "preqin_vintage"])
    return (
        preqin.sort_values(["fund_id", "match_score"], ascending=[True, False], na_position="last")
        .drop_duplicates("fund_id")
        .rename(columns={
            "source_fund_name": "preqin_fund_name",
            "source_sponsor_name": "preqin_sponsor_name",
            "source_vintage": "preqin_vintage",
        })[["fund_id", "preqin_fund_name", "preqin_sponsor_name", "preqin_vintage"]]
        .reset_index(drop=True)
    )


def _vintage_score(left, right) -> float:
    left_year = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right_year = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    if pd.isna(left_year) or pd.isna(right_year):
        return 0.35
    diff = abs(int(left_year) - int(right_year))
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.70
    return 0.0


def _best_name_ratio(source_names: list, candidate_name) -> float:
    return max((_ratio(name, candidate_name) for name in source_names if not _is_null_like(name)), default=0.0)


def _best_sponsor_agreement(source_names: list, candidate_name) -> float:
    return max(
        (_sponsor_agreement(name, candidate_name) for name in source_names if not _is_null_like(name)),
        default=0.0,
    )


def _any_series_conflict(source_names: list, candidate_name) -> bool:
    named = [name for name in source_names if not _is_null_like(name)]
    return bool(named) and all(_series_conflict(name, candidate_name) for name in named)


def _best_token_overlap(source_names: list, candidate_name) -> float:
    return max((_token_overlap(name, candidate_name) for name in source_names if not _is_null_like(name)), default=0.0)


def _preqin_match_candidates(seed_row: pd.Series, preqin: pd.DataFrame) -> pd.DataFrame:
    seed_vintage = pd.to_numeric(pd.Series([seed_row.get("vintage_seed")]), errors="coerce").iloc[0]
    seed_fund_key = seed_row.get("fund_match_key") or _match_key(seed_row.get("fund_name_seed"))
    candidates = pd.DataFrame()
    if "fund_match_key" in preqin.columns:
        exact = preqin[preqin["fund_match_key"].eq(seed_fund_key)].copy()
        if pd.notna(seed_vintage) and not exact.empty:
            exact_vintage = exact[pd.to_numeric(exact["vintage"], errors="coerce").eq(seed_vintage)].copy()
            exact = exact_vintage if not exact_vintage.empty else exact
        candidates = exact

    if candidates.empty:
        if pd.notna(seed_vintage):
            candidates = preqin[pd.to_numeric(preqin["vintage"], errors="coerce").eq(seed_vintage)].copy()
            if candidates.empty:
                candidates = preqin.copy()
        else:
            candidates = preqin.copy()
    candidates = _scope_preqin_candidates(candidates, asset_scope=_preqin_asset_scope_hint(seed_row.get("fund_name_seed")))
    if candidates.empty:
        return candidates

    fund_name = seed_row.get("fund_name_seed")
    sponsor_name = seed_row.get("sponsor_name_seed")
    seed_fund_tokens = _tokens(fund_name)
    if seed_fund_tokens and "fund_tokens" in candidates.columns:
        token_matched = candidates[candidates["fund_tokens"].map(lambda tokens: bool(seed_fund_tokens & tokens))].copy()
        if not token_matched.empty:
            candidates = token_matched
    candidates["fund_name_similarity"] = candidates["fund_name"].map(lambda value: _ratio(fund_name, value))
    candidates["sponsor_name_similarity"] = candidates["sponsor_name"].map(lambda value: _ratio(sponsor_name, value))
    candidates["token_overlap_score"] = candidates["fund_name"].map(lambda value: _token_overlap(fund_name, value))
    candidates["vintage_match_flag"] = candidates["vintage"].map(lambda value: _vintage_match(seed_row.get("vintage_seed"), value))
    candidates["match_score"] = (
        candidates["fund_name_similarity"] * 0.60
        + candidates["sponsor_name_similarity"] * 0.20
        + candidates["token_overlap_score"] * 0.10
        + candidates["vintage_match_flag"].astype(float) * 0.10
    )
    return candidates.sort_values(
        ["match_score", "fund_name_similarity", "token_overlap_score", "as_of_date"],
        ascending=[False, False, False, False],
    )


def _factset_match_candidates(seed_row: pd.Series, factset: pd.DataFrame) -> pd.DataFrame:
    seed_fund_names = [
        seed_row.get("fund_name_seed"),
        seed_row.get("preqin_fund_name"),
    ]
    seed_sponsor_names = [
        seed_row.get("sponsor_name_seed"),
        seed_row.get("preqin_sponsor_name"),
    ]
    seed_fund_tokens = set().union(*[_tokens(name) for name in seed_fund_names if not _is_null_like(name)])
    seed_sponsor_tokens = set().union(*[_tokens(name) for name in seed_sponsor_names if not _is_null_like(name)])

    candidates = factset.copy()
    if seed_fund_tokens or seed_sponsor_tokens:
        fund_overlap = candidates["fund_tokens"].map(lambda tokens: len(tokens & seed_fund_tokens))
        sponsor_overlap = candidates["sponsor_tokens"].map(lambda tokens: len(tokens & seed_sponsor_tokens))
        token_candidates = candidates[(fund_overlap >= 2) | ((fund_overlap >= 1) & (sponsor_overlap >= 1))].copy()
        if not token_candidates.empty:
            candidates = token_candidates

    candidates["fund_name_similarity"] = candidates["fund_name"].map(lambda value: _best_name_ratio(seed_fund_names, value))
    candidates["sponsor_name_similarity"] = candidates["sponsor_name"].map(lambda value: _best_sponsor_agreement(seed_sponsor_names, value))
    candidates["series_conflict"] = candidates["fund_name"].map(lambda value: _any_series_conflict(seed_fund_names, value))
    candidates["token_overlap_score"] = candidates["fund_name"].map(lambda value: _best_token_overlap(seed_fund_names, value))
    candidates["vintage_score"] = candidates["vintage"].map(lambda value: _vintage_score(seed_row.get("vintage_seed"), value))
    candidates["vintage_match_flag"] = candidates["vintage_score"].eq(1.0)
    candidates["match_score"] = (
        candidates["fund_name_similarity"] * 0.56
        + candidates["sponsor_name_similarity"] * 0.26
        + candidates["token_overlap_score"] * 0.10
        + candidates["vintage_score"] * 0.08
    )
    return candidates.sort_values(
        ["match_score", "fund_name_similarity", "sponsor_name_similarity", "vintage_score"],
        ascending=[False, False, False, False],
    )


def _match_confidence(review_status: str) -> str:
    return {
        "auto_matched": "high",
        "manual_matched": "high",
        "needs_review": "medium",
        "manual_unmatched": "unmatched",
        "unmatched": "unmatched",
    }.get(str(review_status), "unmatched")


def _is_null_like(value) -> bool:
    if pd.isna(value):
        return True
    text = str(value).strip().lower()
    return text in {"", "null", "none", "nan"}


def _override_vintage_key(value) -> str | int:
    vintage = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return int(vintage) if pd.notna(vintage) else "unknown"


def _override_match_key(sponsor, fund_name, vintage) -> tuple[str, str, str | int]:
    return (
        str(sponsor or "").strip().lower(),
        str(fund_name or "").strip().lower(),
        _override_vintage_key(vintage),
    )


def _is_manual_no_match(status) -> bool:
    return "no match" in str(status or "").lower()


def _cell_is_yellow(cell) -> bool:
    fill = getattr(cell, "fill", None)
    if fill is None:
        return False
    for attr in ("fgColor", "start_color"):
        color = getattr(fill, attr, None)
        if color is None or not color.rgb:
            continue
        rgb = str(color.rgb).upper()
        if "FFFF" in rgb and rgb not in {"FFFFFFFF", "00FFFFFF"}:
            return True
    return False


def read_manual_fund_alias_overrides(path: str | Path, *, sheet_name: str = MANUAL_ALIAS_OVERRIDE_SHEET) -> pd.DataFrame:
    import openpyxl

    workbook_path = Path(path)
    if not workbook_path.exists():
        return pd.DataFrame()

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    if sheet_name not in workbook.sheetnames:
        return pd.DataFrame()

    worksheet = workbook[sheet_name]
    headers = [worksheet.cell(1, col).value for col in range(1, worksheet.max_column + 1)]
    rows = []
    for row_num in range(2, worksheet.max_row + 1):
        if not any(_cell_is_yellow(worksheet.cell(row_num, col)) for col in range(1, worksheet.max_column + 1)):
            continue
        rows.append({headers[col - 1]: worksheet.cell(row_num, col).value for col in range(1, worksheet.max_column + 1)})
    if not rows:
        return pd.DataFrame()

    overrides = pd.DataFrame(rows)
    overrides.columns = [str(col).strip().lower() for col in overrides.columns]
    return overrides


def _lookup_preqin_fund(
    preqin: pd.DataFrame,
    *,
    source_fund_name,
    source_sponsor_name=pd.NA,
    source_vintage=pd.NA,
    source_entity_id=pd.NA,
    source_entity_table=pd.NA,
) -> pd.Series:
    if preqin.empty:
        return pd.Series(dtype="object")

    candidates = preqin.copy()
    if not _is_null_like(source_entity_table) and "source_fund_source_table" in candidates.columns:
        table_match = candidates["source_fund_source_table"].astype("string").eq(str(source_entity_table).strip())
        if table_match.any():
            candidates = candidates[table_match]
    if not _is_null_like(source_entity_id):
        entity_match = candidates["source_fund_id"].astype(str).eq(str(source_entity_id).strip())
        if entity_match.any():
            return candidates.loc[entity_match].iloc[0]

    if not _is_null_like(source_fund_name):
        candidates = candidates[candidates["fund_name"].astype(str).str.strip().eq(str(source_fund_name).strip())]
    if candidates.empty:
        return pd.Series(dtype="object")

    if not _is_null_like(source_sponsor_name):
        sponsor_match = candidates["sponsor_name"].astype(str).str.strip().eq(str(source_sponsor_name).strip())
        if sponsor_match.any():
            candidates = candidates[sponsor_match]
    if candidates.empty:
        return pd.Series(dtype="object")

    if not _is_null_like(source_vintage):
        vintage = pd.to_numeric(pd.Series([source_vintage]), errors="coerce").iloc[0]
        if pd.notna(vintage):
            vintage_match = pd.to_numeric(candidates["vintage"], errors="coerce").eq(vintage)
            if vintage_match.any():
                candidates = candidates[vintage_match]

    return candidates.iloc[0] if not candidates.empty else pd.Series(dtype="object")


def apply_manual_fund_alias_overrides(
    alias_map: pd.DataFrame,
    overrides: pd.DataFrame,
    dataset: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    if alias_map.empty or overrides is None or overrides.empty:
        return alias_map

    out = alias_map.copy()
    preqin = _preqin_metric_funds(dataset)
    override_by_key: dict[tuple[str, str, str | int], pd.Series] = {}
    override_by_sponsor_vintage: dict[tuple[str, str | int], pd.Series] = {}

    for override in overrides.itertuples(index=False):
        override_row = pd.Series(override._asdict())
        sponsor = override_row.get("sponsor")
        fund_name = override_row.get("sponsor_fund_name")
        vintage = override_row.get("sponsor_vintage")
        if _is_null_like(sponsor) or _is_null_like(fund_name):
            continue
        key = _override_match_key(sponsor, fund_name, vintage)
        override_by_key[key] = override_row
        if not _is_manual_no_match(override_row.get("status")) and not _is_null_like(override_row.get("source_fund_name")):
            override_by_sponsor_vintage[(key[0], key[2])] = override_row

    for idx, row in out.iterrows():
        key = _override_match_key(row.get("sponsor"), row.get("sponsor_fund_name"), row.get("sponsor_vintage"))
        override = override_by_key.get(key)
        if override is None and _is_excel_error_fund_name(row.get("sponsor_fund_name")):
            override = override_by_sponsor_vintage.get((key[0], key[2]))
        if override is None:
            continue

        if _is_manual_no_match(override.get("status")):
            out.loc[idx, "source_entity_id"] = pd.NA
            out.loc[idx, "source_entity_table"] = pd.NA
            out.loc[idx, "source_sponsor_id"] = pd.NA
            out.loc[idx, "source_sponsor_name"] = pd.NA
            out.loc[idx, "source_fund_name"] = pd.NA
            out.loc[idx, "source_fund_slug"] = pd.NA
            out.loc[idx, "source_vintage"] = pd.NA
            out.loc[idx, "source_as_of_date"] = pd.NaT
            out.loc[idx, "preferred_metric_source_system"] = pd.NA
            out.loc[idx, "alias_type"] = "source_candidate_name"
            out.loc[idx, "match_method"] = "manual_xref_workbook"
            out.loc[idx, "match_confidence"] = "unmatched"
            out.loc[idx, "review_status"] = "manual_unmatched"
            out.loc[idx, "evidence_source"] = "manual_fund_alias_override_workbook"
            continue

        source_fund_name = override.get("source_fund_name")
        if _is_null_like(source_fund_name):
            continue

        preqin_row = _lookup_preqin_fund(
            preqin,
            source_fund_name=source_fund_name,
            source_sponsor_name=override.get("source_sponsor_name"),
            source_vintage=override.get("source_vintage"),
            source_entity_id=override.get("source_entity_id"),
            source_entity_table=override.get("source_entity_table"),
        )
        source_entity_id = (
            preqin_row.get("source_fund_id")
            if not preqin_row.empty
            else override.get("source_entity_id") if not _is_null_like(override.get("source_entity_id")) else pd.NA
        )
        if _is_null_like(source_entity_id):
            continue

        source_entity_table = (
            preqin_row.get("source_fund_source_table")
            if not preqin_row.empty
            else override.get("source_entity_table") if not _is_null_like(override.get("source_entity_table")) else pd.NA
        )
        source_fund_name = preqin_row.get("fund_name") if not preqin_row.empty else source_fund_name
        source_sponsor_name = preqin_row.get("sponsor_name") if not preqin_row.empty else override.get("source_sponsor_name")
        source_vintage = preqin_row.get("vintage") if not preqin_row.empty else override.get("source_vintage")
        source_sponsor_id = preqin_row.get("source_sponsor_id") if not preqin_row.empty else pd.NA
        source_as_of_date = preqin_row.get("as_of_date") if not preqin_row.empty else pd.NaT

        out.loc[idx, "fund_alias_id"] = stable_id(
            "fund_alias",
            row.get("fund_id"),
            "preqin",
            source_entity_table,
            source_entity_id,
        )
        out.loc[idx, "source_entity_id"] = source_entity_id
        out.loc[idx, "source_entity_table"] = source_entity_table
        out.loc[idx, "source_sponsor_id"] = source_sponsor_id
        out.loc[idx, "source_sponsor_name"] = source_sponsor_name
        out.loc[idx, "source_fund_name"] = source_fund_name
        out.loc[idx, "source_fund_slug"] = slug(source_fund_name)
        out.loc[idx, "source_vintage"] = source_vintage
        out.loc[idx, "source_as_of_date"] = source_as_of_date
        out.loc[idx, "fund_name_similarity"] = override.get("fund_name_similarity", pd.NA)
        out.loc[idx, "sponsor_name_similarity"] = override.get("sponsor_name_similarity", pd.NA)
        out.loc[idx, "token_overlap_score"] = override.get("token_overlap_score", pd.NA)
        out.loc[idx, "vintage_match_flag"] = _vintage_match(row.get("sponsor_vintage"), source_vintage)
        out.loc[idx, "match_score"] = override.get("match_score", pd.NA)
        out.loc[idx, "alias_type"] = "source_fund_name"
        out.loc[idx, "match_method"] = "manual_xref_workbook"
        out.loc[idx, "match_confidence"] = "high"
        out.loc[idx, "review_status"] = "manual_matched"
        preferred_source = override.get("preferred_metric_source_system")
        out.loc[idx, "preferred_metric_source_system"] = (
            preferred_source if not _is_null_like(preferred_source) else "preqin"
        )
        out.loc[idx, "evidence_source"] = "manual_fund_alias_override_workbook"

    return out.reindex(columns=FUND_ALIAS_COLUMNS).reset_index(drop=True)


def build_fund_alias_name_map(dataset: dict[str, pd.DataFrame], *, min_match_score: float = 0.72) -> pd.DataFrame:
    seed = _seed_funds(dataset)
    preqin = _preqin_metric_funds(dataset)
    if seed.empty or preqin.empty:
        return pd.DataFrame(columns=FUND_ALIAS_COLUMNS)

    rows = []
    for seed_row in seed.itertuples(index=False):
        seed_series = pd.Series(seed_row._asdict())
        candidates = _preqin_match_candidates(seed_series, preqin)
        best = candidates.iloc[0] if not candidates.empty else pd.Series(dtype="object")
        score = best.get("match_score", 0.0) if not best.empty else 0.0
        matched = bool(score >= min_match_score and best.get("fund_name_similarity", 0.0) >= 0.65)
        review_status = "auto_matched" if matched and score >= 0.86 else "needs_review" if matched else "unmatched"
        match_method = "fuzzy_preqin_fund_name_vintage" if matched else "unmatched_best_candidate"
        rows.append({
            "fund_alias_id": stable_id(
                "fund_alias",
                seed_series.get("fund_id"),
                "preqin",
                best.get("source_fund_source_table") if matched else pd.NA,
                best.get("source_fund_id"),
            ),
            "fund_id": seed_series.get("fund_id"),
            "sponsor_id": seed_series.get("sponsor_id"),
            "sponsor": seed_series.get("sponsor_name_seed"),
            "sponsor_fund_name": seed_series.get("fund_name_seed"),
            "sponsor_fund_slug": slug(seed_series.get("fund_name_seed")),
            "sponsor_vintage": seed_series.get("vintage_seed"),
            "source_system": "preqin",
            "source_entity_id": best.get("source_fund_id") if matched else pd.NA,
            "source_entity_table": best.get("source_fund_source_table") if matched else pd.NA,
            "source_sponsor_id": best.get("source_sponsor_id") if matched else pd.NA,
            "source_sponsor_name": best.get("sponsor_name") if matched else pd.NA,
            "source_fund_name": best.get("fund_name") if matched else pd.NA,
            "source_fund_slug": slug(best.get("fund_name")) if matched else pd.NA,
            "source_vintage": best.get("vintage") if matched else pd.NA,
            "source_as_of_date": best.get("as_of_date") if matched else pd.NaT,
            "fund_name_similarity": best.get("fund_name_similarity", pd.NA),
            "sponsor_name_similarity": best.get("sponsor_name_similarity", pd.NA),
            "token_overlap_score": best.get("token_overlap_score", pd.NA),
            "vintage_match_flag": best.get("vintage_match_flag", False) if matched else False,
            "match_score": score,
            "alias_type": "source_fund_name" if matched else "source_candidate_name",
            "match_method": match_method,
            "match_confidence": _match_confidence(review_status),
            "review_status": review_status,
            "preferred_metric_source_system": "preqin" if matched else pd.NA,
            "evidence_source": "fact_fund_performance_snapshot",
        })
    return pd.DataFrame(rows, columns=FUND_ALIAS_COLUMNS)


def build_factset_pevc_fund_alias_name_map(
    dataset: dict[str, pd.DataFrame],
    preqin_alias_map: pd.DataFrame | None = None,
    *,
    min_match_score: float = 0.70,
) -> pd.DataFrame:
    seed = _seed_funds(dataset)
    factset = _factset_pevc_funds(dataset)
    if seed.empty or factset.empty:
        return pd.DataFrame(columns=FUND_ALIAS_COLUMNS)

    preqin_aliases = _accepted_preqin_aliases(preqin_alias_map if preqin_alias_map is not None else pd.DataFrame())
    if not preqin_aliases.empty:
        seed = seed.merge(preqin_aliases, on="fund_id", how="left")
    else:
        seed["preqin_fund_name"] = pd.NA
        seed["preqin_sponsor_name"] = pd.NA
        seed["preqin_vintage"] = pd.NA
    rows = []
    for seed_row in seed.itertuples(index=False):
        seed_series = pd.Series(seed_row._asdict())
        candidates = _factset_match_candidates(seed_series, factset)
        best = candidates.iloc[0] if not candidates.empty else pd.Series(dtype="object")
        score = best.get("match_score", 0.0) if not best.empty else 0.0
        second_score = candidates.iloc[1].get("match_score", 0.0) if len(candidates) > 1 else 0.0
        margin = score - second_score
        matched = bool(score >= min_match_score and best.get("fund_name_similarity", 0.0) >= 0.58)
        auto_matched = (
            matched
            and score >= 0.86
            and margin >= 0.03
            and not bool(best.get("series_conflict", False))
        )
        review_status = "auto_matched" if auto_matched else "needs_review" if matched else "unmatched"
        match_method = "fuzzy_factset_pevc_fund_name_sponsor_vintage" if matched else "unmatched_best_candidate"
        rows.append({
            "fund_alias_id": stable_id(
                "fund_alias",
                seed_series.get("fund_id"),
                "factset_pevc",
                best.get("source_fund_source_table") if matched else pd.NA,
                best.get("source_fund_id"),
            ),
            "fund_id": seed_series.get("fund_id"),
            "sponsor_id": seed_series.get("sponsor_id"),
            "sponsor": seed_series.get("sponsor_name_seed"),
            "sponsor_fund_name": seed_series.get("fund_name_seed"),
            "sponsor_fund_slug": slug(seed_series.get("fund_name_seed")),
            "sponsor_vintage": seed_series.get("vintage_seed"),
            "source_system": "factset_pevc",
            "source_entity_id": best.get("source_fund_id") if matched else pd.NA,
            "source_entity_table": best.get("source_fund_source_table") if matched else pd.NA,
            "source_sponsor_id": best.get("source_sponsor_id") if matched else pd.NA,
            "source_sponsor_name": best.get("sponsor_name") if matched else pd.NA,
            "source_fund_name": best.get("fund_name") if matched else pd.NA,
            "source_fund_slug": slug(best.get("fund_name")) if matched else pd.NA,
            "source_vintage": best.get("vintage") if matched else pd.NA,
            "source_as_of_date": best.get("as_of_date") if matched else pd.NaT,
            "fund_name_similarity": best.get("fund_name_similarity", pd.NA),
            "sponsor_name_similarity": best.get("sponsor_name_similarity", pd.NA),
            "token_overlap_score": best.get("token_overlap_score", pd.NA),
            "vintage_match_flag": best.get("vintage_match_flag", False) if matched else False,
            "match_score": score,
            "alias_type": "source_fund_name" if matched else "source_candidate_name",
            "match_method": match_method,
            "match_confidence": _match_confidence(review_status),
            "review_status": review_status,
            "preferred_metric_source_system": "factset_pevc",
            "evidence_source": "source_factset_pevc_fund_summary",
        })
    return pd.DataFrame(rows, columns=FUND_ALIAS_COLUMNS)


def build_fund_name_preqin_map(dataset: dict[str, pd.DataFrame], *, min_match_score: float = 0.72) -> pd.DataFrame:
    return build_fund_alias_name_map(dataset, min_match_score=min_match_score)


def build_fund_vendor_comparison(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    seed = _seed_funds(dataset)
    vendor = _vendor_funds(dataset)
    if seed.empty or vendor.empty:
        return pd.DataFrame(columns=FUND_VENDOR_COMPARISON_COLUMNS)

    candidates = seed.merge(
        vendor,
        on=["sponsor_match_key", "fund_match_key"],
        how="left",
        suffixes=("", "_vendor"),
    )
    vintage_match = (
        candidates["vintage_seed"].isna()
        | candidates["vintage_vendor"].isna()
        | (pd.to_numeric(candidates["vintage_seed"], errors="coerce") == pd.to_numeric(candidates["vintage_vendor"], errors="coerce"))
    )
    matched = candidates[vintage_match & candidates["source_system"].notna()].copy()
    matched["match_status"] = "matched"
    matched["match_method"] = "sponsor_fund_key_vintage_check"

    unmatched_seed = seed[~seed["fund_id"].isin(matched["fund_id"])].copy()
    unmatched_seed["match_status"] = "unmatched_seed_fund"
    unmatched_seed["match_method"] = "unmatched_seed_fund"
    merged = pd.concat([matched, unmatched_seed], ignore_index=True)
    merged["fund_vendor_comparison_id"] = merged.apply(
        lambda row: stable_id("fund_cmp", row.get("fund_id"), row.get("source_system"), row.get("source_fund_id")),
        axis=1,
    )
    return merged.reindex(columns=FUND_VENDOR_COMPARISON_COLUMNS).reset_index(drop=True)


def build_fund_vendor_coverage(comparison: pd.DataFrame) -> pd.DataFrame:
    if comparison.empty:
        return pd.DataFrame(columns=FUND_VENDOR_COVERAGE_COLUMNS)
    seed_fund_count = comparison["fund_id"].nunique()
    matched = comparison[comparison["match_status"].eq("matched")].copy()
    if matched.empty:
        return pd.DataFrame(columns=FUND_VENDOR_COVERAGE_COLUMNS)
    rows = []
    for source_system, group in matched.groupby("source_system", dropna=False):
        rows.append({
            "source_system": source_system,
            "seed_fund_count": seed_fund_count,
            "matched_fund_count": group["fund_id"].nunique(),
            "matched_fund_pct": group["fund_id"].nunique() / seed_fund_count if seed_fund_count else pd.NA,
            "funds_with_as_of_date_count": group.dropna(subset=["as_of_date"])["fund_id"].nunique(),
            "funds_with_commitment_count": group.dropna(subset=["commitment_usd_m"])["fund_id"].nunique(),
            "funds_with_pct_called_count": group.dropna(subset=["pct_called"])["fund_id"].nunique(),
            "funds_with_dpi_count": group.dropna(subset=["dpi"])["fund_id"].nunique(),
            "funds_with_rvpi_count": group.dropna(subset=["rvpi"])["fund_id"].nunique(),
            "funds_with_net_moic_count": group.dropna(subset=["net_moic"])["fund_id"].nunique(),
            "funds_with_net_irr_count": group.dropna(subset=["net_irr"])["fund_id"].nunique(),
        })
    return pd.DataFrame(rows, columns=FUND_VENDOR_COVERAGE_COLUMNS)


def add_fund_vendor_outputs(
    dataset: dict[str, pd.DataFrame],
    *,
    manual_alias_override_path: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    comparison = build_fund_vendor_comparison(out)
    out["validation_fund_vendor_comparison"] = comparison
    out["validation_fund_vendor_coverage"] = build_fund_vendor_coverage(comparison)
    alias_map = build_fund_alias_name_map(out)
    if manual_alias_override_path:
        overrides = read_manual_fund_alias_overrides(manual_alias_override_path)
        alias_map = apply_manual_fund_alias_overrides(alias_map, overrides, out)
    factset_alias_map = build_factset_pevc_fund_alias_name_map(out, alias_map)
    if not factset_alias_map.empty:
        alias_map = pd.concat([alias_map, factset_alias_map], ignore_index=True)
    if not alias_map.empty:
        alias_map = alias_map.copy()
        alias_map["source_as_of_date"] = pd.to_datetime(alias_map["source_as_of_date"], errors="coerce")
        alias_map = alias_map.reindex(columns=FUND_ALIAS_COLUMNS).reset_index(drop=True)
    out["xref_fund_alias_name_map"] = alias_map
    return out
