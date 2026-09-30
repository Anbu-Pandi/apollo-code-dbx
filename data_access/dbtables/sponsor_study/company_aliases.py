import re
from difflib import SequenceMatcher

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id


REVIEWED_FUND_XREF_STATUSES = {"auto_matched", "manual_matched", "needs_review"}
ACCEPTED_COMPANY_ALIAS_STATUSES = {"accepted_seed", "auto_matched", "manual_matched"}
AUTO_MATCH_MIN_SCORE = 0.92
REVIEW_MATCH_MIN_SCORE = 0.84
AMBIGUITY_MARGIN = 0.04
TOKEN_OVERLAP_AUTO_MIN = 0.60
CONTAINMENT_MATCH_SCORE = 0.95
MAX_COMPANY_NAME_VARIANTS = 6
COMPANY_CONNECTOR_TOKENS = {"and", "the"}
COMPANY_TRAILING_SUFFIX_TOKENS = {
    "ag",
    "ab",
    "bv",
    "co",
    "company",
    "corp",
    "corporation",
    "gmbh",
    "group",
    "holding",
    "holdings",
    "inc",
    "incorporated",
    "1",
    "2",
    "3",
    "ii",
    "iii",
    "iv",
    "vi",
    "vii",
    "viii",
    "ix",
    "kk",
    "limited",
    "llc",
    "lp",
    "ltd",
    "plc",
    "pty",
    "pvt",
    "sa",
    "sas",
    "sasu",
    "spa",
    "srl",
    "uk",
}
CAPITAL_STRUCTURE_LEGAL_SUFFIX_TOKENS = {
    "ag", "ab", "bv", "co", "company", "corp", "corporation", "gmbh", "inc",
    "incorporated", "kk", "limited", "llc", "lp", "ltd", "plc", "pty", "pvt",
    "sa", "sas", "sasu", "spa", "srl",
}

COMPANY_ALIAS_MARKER_RE = re.compile(
    r"^\s*(?:f\s*/?\s*k\s*/?\s*a|d\s*/?\s*b\s*/?\s*a|a\s*/?\s*k\s*/?\s*a|n\s*/?\s*k\s*/?\s*a"
    r"|formerly(?:\s+known\s+as)?|previously(?:\s+known\s+as)?|now(?:\s+known\s+as)?"
    r"|renamed(?:\s+to)?)\b[\s:,.\-]*",
    re.IGNORECASE,
)
COMPANY_BRACKET_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
COMPANY_POSSESSIVE_RE = re.compile(r"(?<=[A-Za-z0-9])['’]s\b")
COMPANY_STATUS_PARENTHETICALS = {
    "active",
    "bankrupt",
    "closed",
    "exited",
    "filed",
    "ipo",
    "merged",
    "pending",
    "private",
    "public",
    "realized",
    "restructured",
    "sold",
}

STRUCTURED_ALIAS_SOURCES = [
    "dealogic",
    "factset",
    "jpm_lev_loan_index",
    "ice_bond_index",
    "moodys",
    "sp",
    "preqin",
    "pitchbook",
]

ALIAS_COLUMNS = [
    "company_alias_id",
    "portco_id",
    "ownership_id",
    "sponsor_id",
    "sponsor",
    "sponsor_company_name",
    "sponsor_company_slug",
    "source_system",
    "source_entity_id",
    "source_company_name",
    "source_company_slug",
    "alias_type",
    "match_method",
    "match_confidence",
    "review_status",
    "evidence_source",
]


def _clean_id(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _company_key(value) -> str:
    tokens = [
        token
        for token in slug(
            COMPANY_POSSESSIVE_RE.sub("s", str(value))
            if pd.notna(value)
            else value
        ).split("_")
        if token and token not in COMPANY_CONNECTOR_TOKENS
    ]
    while tokens and tokens[-1] in COMPANY_TRAILING_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _blend_score(seed_key: str, source_key: str) -> tuple[float, float]:
    seed_tokens = set(seed_key.split())
    source_tokens = set(source_key.split())
    overlap = len(seed_tokens & source_tokens) / max(len(seed_tokens | source_tokens), 1)
    ratio = SequenceMatcher(None, seed_key, source_key).ratio()
    return (0.70 * ratio) + (0.30 * overlap), overlap


def score_company_name(seed_name, source_name) -> tuple[float, float, bool]:
    """Score company names using suffix-aware normalization without first-token shortcuts."""
    seed_key = _company_key(seed_name)
    source_key = _company_key(source_name)
    if not seed_key or not source_key:
        return 0.0, 0.0, False
    if seed_key == source_key:
        return 1.0, 1.0, True
    score, overlap = _blend_score(seed_key, source_key)
    return score, overlap, False


def capital_structure_name_key(value) -> str:
    """Conservative key for HoldCo/FinCo aliases.

    Unlike the general company key, this keeps structural words and numerals:
    `Compass IV Ltd` must not become identical to `Compass Group PLC`.
    """
    tokens = [
        token
        for token in slug(
            COMPANY_POSSESSIVE_RE.sub("s", str(value))
            if pd.notna(value)
            else value
        ).split("_")
        if token and token not in COMPANY_CONNECTOR_TOKENS
    ]
    while tokens and tokens[-1] in CAPITAL_STRUCTURE_LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def score_capital_structure_name(
    seed_name, source_name
) -> tuple[float, float, bool]:
    seed_key = capital_structure_name_key(seed_name)
    source_key = capital_structure_name_key(source_name)
    if not seed_key or not source_key:
        return 0.0, 0.0, False
    if seed_key == source_key:
        return 1.0, 1.0, True
    score, overlap = _blend_score(seed_key, source_key)
    return score, overlap, False


def _is_usable_alias(key: str) -> bool:
    tokens = [token for token in key.split() if not token.isdigit()]
    if not tokens or all(len(token) < 3 for token in tokens):
        return False
    return not (len(tokens) == 1 and tokens[0] in COMPANY_STATUS_PARENTHETICALS)


def company_name_variants(value) -> tuple[str, ...]:
    """Normalized keys for a company's current name plus any embedded former/alternate names.

    Parenthetical and slash-delimited segments are emitted as separate keys so that
    `Viasat (f/k/a Inmarsat)` can match either `Viasat` or `Inmarsat` on the other side.
    """
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return ()
    bracketed = COMPANY_BRACKET_RE.findall(text)
    base = COMPANY_ALIAS_MARKER_RE.sub("", COMPANY_BRACKET_RE.sub(" ", text)).strip()
    keys: list[str] = []
    primary = _company_key(base)
    if primary:
        keys.append(primary)
    derived = base.split("/") if "/" in base else []
    for segment in bracketed:
        if not COMPANY_ALIAS_MARKER_RE.match(segment):
            continue
        cleaned = COMPANY_ALIAS_MARKER_RE.sub("", segment).strip()
        derived.append(cleaned)
        if "/" in cleaned:
            derived.extend(cleaned.split("/"))
    for segment in derived:
        if not re.search(r"[a-zA-Z0-9]", segment):
            continue
        key = _company_key(segment)
        if key and key not in keys and _is_usable_alias(key):
            keys.append(key)
    return tuple(keys[:MAX_COMPANY_NAME_VARIANTS])


def _is_token_prefix(seed_tokens: list[str], source_tokens: list[str]) -> bool:
    shorter, longer = sorted((seed_tokens, source_tokens), key=len)
    return bool(shorter) and longer[: len(shorter)] == shorter


def _score_alias_keys(seed_key: str, source_key: str) -> tuple[float, float, bool]:
    if seed_key == source_key:
        return 1.0, 1.0, True
    if _is_token_prefix(seed_key.split(), source_key.split()):
        return CONTAINMENT_MATCH_SCORE, 1.0, False
    score, overlap = _blend_score(seed_key, source_key)
    return score, overlap, False


def score_company_name_with_aliases(seed_name, source_name) -> tuple[float, float, bool]:
    """Score across current and former names on both sides; the best variant pair wins.

    Also treats a full token-prefix (`ECI Software` vs `ECI Software Solutions`) as a
    match, which the strict scorer penalizes for the extra descriptive tokens.
    """
    seed_keys = company_name_variants(seed_name)
    source_keys = company_name_variants(source_name)
    if not seed_keys or not source_keys:
        return 0.0, 0.0, False
    best = (0.0, 0.0, False)
    for seed_key in seed_keys:
        for source_key in source_keys:
            candidate = _score_alias_keys(seed_key, source_key)
            if candidate[0] > best[0]:
                best = candidate
    return best


def _company_score(seed_name, source_name) -> tuple[float, float, bool]:
    return score_company_name(seed_name, source_name)


def _base_company_rows(company_snapshot: pd.DataFrame) -> pd.DataFrame:
    if company_snapshot is None or company_snapshot.empty:
        return pd.DataFrame(columns=[
            "portco_id",
            "ownership_id",
            "sponsor_id",
            "sponsor",
            "sponsor_company_name",
            "sponsor_company_slug",
        ])
    rows = company_snapshot[[
        "portco_id",
        "ownership_id",
        "sponsor_id",
        "sponsor",
        "company",
        "company_slug",
    ]].dropna(subset=["portco_id", "company"]).drop_duplicates().copy()
    rows = rows.rename(columns={
        "company": "sponsor_company_name",
        "company_slug": "sponsor_company_slug",
    })
    return rows


def _seed_fund_portco_rows(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    bridge = dataset.get("bridge_fund_portco", pd.DataFrame())
    funds = dataset.get("dim_fund", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    portcos = dataset.get("dim_portco", pd.DataFrame())
    if bridge.empty or funds.empty or sponsors.empty or portcos.empty:
        return pd.DataFrame(columns=[
            "portco_id",
            "ownership_id",
            "sponsor_id",
            "sponsor",
            "fund_id",
            "fund_name",
            "vintage",
            "sponsor_company_name",
            "sponsor_company_slug",
        ])

    out = bridge[["portco_id", "ownership_id", "fund_id", "sponsor_id"]].drop_duplicates().copy()
    out = out.merge(portcos[["portco_id", "company_name"]], on="portco_id", how="left")
    out = out.merge(funds[["fund_id", "fund_name", "vintage"]], on="fund_id", how="left")
    out = out.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    out = out.rename(columns={
        "company_name": "sponsor_company_name",
        "sponsor_name": "sponsor",
    })
    out["sponsor_company_slug"] = out["sponsor_company_name"].map(slug)
    return out.dropna(subset=["portco_id", "fund_id", "sponsor_company_name"]).reset_index(drop=True)


def _factset_pevc_portco_rows(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pevc = dataset.get("source_factset_pevc_portco_investment", pd.DataFrame())
    xref = dataset.get("xref_fund_alias_name_map", pd.DataFrame())
    required = {
        "factset_fund_entity_id",
        "factset_portco_entity_id",
        "portco_name_factset",
    }
    if pevc.empty or xref.empty or not required.issubset(pevc.columns):
        return pd.DataFrame(columns=["fund_id", "source_entity_id", "source_company_name"])

    factset_xref = xref[
        xref["source_system"].eq("factset_pevc")
        & xref["review_status"].isin(REVIEWED_FUND_XREF_STATUSES)
        & xref["source_entity_id"].notna()
    ][["fund_id", "source_entity_id"]].drop_duplicates().copy()
    if factset_xref.empty:
        return pd.DataFrame(columns=["fund_id", "source_entity_id", "source_company_name"])

    factset_xref["_factset_fund_entity_id"] = factset_xref["source_entity_id"].map(_clean_id)
    factset_xref = factset_xref[factset_xref["_factset_fund_entity_id"].ne("")]
    pevc = pevc.copy()
    pevc["_factset_fund_entity_id"] = pevc["factset_fund_entity_id"].map(_clean_id)
    transaction_fund = (
        pevc["transaction_fund_entity_id"].map(_clean_id)
        if "transaction_fund_entity_id" in pevc
        else pd.Series("", index=pevc.index)
    )
    pevc["_transaction_fund_entity_id"] = transaction_fund

    direct = pevc.merge(
        factset_xref,
        on="_factset_fund_entity_id",
        how="inner",
    )
    transaction = pevc[pevc["_transaction_fund_entity_id"].ne("")].merge(
        factset_xref,
        left_on="_transaction_fund_entity_id",
        right_on="_factset_fund_entity_id",
        how="inner",
    )
    matched = pd.concat([direct, transaction], ignore_index=True)
    if matched.empty:
        return pd.DataFrame(columns=["fund_id", "source_entity_id", "source_company_name"])

    matched["source_entity_id"] = matched["factset_portco_entity_id"].map(_clean_id)
    matched = matched[matched["source_entity_id"].ne("")]
    matched = matched.rename(columns={"portco_name_factset": "source_company_name"})
    return matched[["fund_id", "source_entity_id", "source_company_name"]].dropna(
        subset=["source_company_name"]
    ).drop_duplicates().reset_index(drop=True)


def _factset_pevc_company_alias_rows(dataset: dict[str, pd.DataFrame]) -> list[dict]:
    seed = _seed_fund_portco_rows(dataset)
    pevc = _factset_pevc_portco_rows(dataset)
    if seed.empty or pevc.empty:
        return []

    candidates = seed.merge(pevc, on="fund_id", how="inner")
    if candidates.empty:
        return []

    scores = candidates.apply(
        lambda row: _company_score(row["sponsor_company_name"], row["source_company_name"]),
        axis=1,
        result_type="expand",
    )
    candidates[["match_score", "token_overlap_score", "exact_match_flag"]] = scores
    candidates = candidates[candidates["match_score"].ge(REVIEW_MATCH_MIN_SCORE)].copy()
    if candidates.empty:
        return []

    candidates["_seed_match_id"] = candidates["ownership_id"].astype(str) + "|" + candidates["portco_id"].astype(str)
    candidates = candidates.sort_values(
        ["_seed_match_id", "match_score", "token_overlap_score"],
        ascending=[True, False, False],
    )
    candidates["candidate_rank"] = candidates.groupby("_seed_match_id").cumcount() + 1
    second_scores = (
        candidates[candidates["candidate_rank"].eq(2)]
        .set_index("_seed_match_id")["match_score"]
        .to_dict()
    )
    top = candidates[candidates["candidate_rank"].eq(1)].copy()
    top["second_match_score"] = top["_seed_match_id"].map(second_scores).fillna(0.0)
    top["score_margin"] = top["match_score"] - top["second_match_score"]

    high_source = top[top["match_score"].ge(AUTO_MATCH_MIN_SCORE)]
    source_seed_counts = high_source.groupby(["fund_id", "source_entity_id"])["_seed_match_id"].nunique()
    ambiguous_sources = set(source_seed_counts[source_seed_counts.gt(1)].index)

    rows = []
    for _, row in top.iterrows():
        ambiguous = (
            row["score_margin"] < AMBIGUITY_MARGIN
            or (row["fund_id"], row["source_entity_id"]) in ambiguous_sources
        )
        auto_match = (
            not ambiguous
            and row["match_score"] >= AUTO_MATCH_MIN_SCORE
            and (bool(row["exact_match_flag"]) or row["token_overlap_score"] >= TOKEN_OVERLAP_AUTO_MIN)
        )
        rows.append(_make_alias_record(
            portco_id=row.get("portco_id"),
            ownership_id=row.get("ownership_id"),
            sponsor_id=row.get("sponsor_id"),
            sponsor=row.get("sponsor"),
            sponsor_company_name=row.get("sponsor_company_name"),
            sponsor_company_slug=row.get("sponsor_company_slug"),
            source_system="factset_pevc",
            source_entity_id=row.get("source_entity_id"),
            source_company_name=row.get("source_company_name"),
            alias_type="source_company_name",
            match_method=(
                "factset_pevc_fund_scoped_company_exact"
                if bool(row["exact_match_flag"])
                else "factset_pevc_fund_scoped_company_fuzzy"
            ),
            match_confidence="high" if auto_match else "medium",
            review_status="auto_matched" if auto_match else "needs_review",
            evidence_source="source_factset_pevc_portco_investment",
        ))
    return rows


def apply_company_aliases_to_portco_xref(xref: pd.DataFrame, aliases: pd.DataFrame) -> pd.DataFrame:
    if xref.empty or aliases.empty:
        return xref.copy()
    accepted = aliases[
        aliases["source_system"].eq("factset_pevc")
        & aliases["review_status"].isin(ACCEPTED_COMPANY_ALIAS_STATUSES)
        & aliases["source_entity_id"].notna()
    ][["portco_id", "source_entity_id"]].drop_duplicates().copy()
    if accepted.empty:
        return xref.copy()

    accepted["source_entity_id"] = accepted["source_entity_id"].map(_clean_id)
    accepted = accepted[accepted["source_entity_id"].ne("")]
    unique_counts = accepted.groupby("portco_id")["source_entity_id"].nunique()
    accepted = accepted[accepted["portco_id"].isin(unique_counts[unique_counts.eq(1)].index)]
    if accepted.empty:
        return xref.copy()

    best = accepted.drop_duplicates("portco_id").rename(columns={"source_entity_id": "factset_entity_id_alias"})
    out = xref.merge(best, on="portco_id", how="left")
    found = out["factset_entity_id_alias"].notna() & out["factset_entity_id"].isna()
    out.loc[found, "factset_entity_id"] = out.loc[found, "factset_entity_id_alias"]
    out.loc[found, "match_method"] = "factset_pevc_company_alias"
    out.loc[found, "match_confidence"] = "high"
    return out.drop(columns=["factset_entity_id_alias"], errors="ignore")


def _make_alias_record(
    *,
    portco_id,
    ownership_id,
    sponsor_id,
    sponsor,
    sponsor_company_name,
    sponsor_company_slug,
    source_system,
    source_entity_id,
    source_company_name,
    alias_type,
    match_method,
    match_confidence,
    review_status,
    evidence_source,
) -> dict:
    source_company_slug = slug(source_company_name)
    return {
        "company_alias_id": stable_id("alias", portco_id, ownership_id, source_system, source_entity_id, source_company_slug),
        "portco_id": portco_id,
        "ownership_id": ownership_id,
        "sponsor_id": sponsor_id,
        "sponsor": sponsor,
        "sponsor_company_name": sponsor_company_name,
        "sponsor_company_slug": sponsor_company_slug,
        "source_system": source_system,
        "source_entity_id": source_entity_id,
        "source_company_name": source_company_name,
        "source_company_slug": source_company_slug,
        "alias_type": alias_type,
        "match_method": match_method,
        "match_confidence": match_confidence,
        "review_status": review_status,
        "evidence_source": evidence_source,
    }


def build_company_alias_name_map(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base_rows = _base_company_rows(dataset.get("stage_consolidated_company_snapshot", pd.DataFrame()))
    records = []
    for _, row in base_rows.iterrows():
        records.append(_make_alias_record(
            portco_id=row.get("portco_id"),
            ownership_id=row.get("ownership_id"),
            sponsor_id=row.get("sponsor_id"),
            sponsor=row.get("sponsor"),
            sponsor_company_name=row.get("sponsor_company_name"),
            sponsor_company_slug=row.get("sponsor_company_slug"),
            source_system="sponsor_study_workbook",
            source_entity_id=row.get("ownership_id"),
            source_company_name=row.get("sponsor_company_name"),
            alias_type="seed_name",
            match_method="workbook_seed",
            match_confidence="high",
            review_status="accepted_seed",
            evidence_source="source_annual_2026_consolidated",
        ))
        for source_system in STRUCTURED_ALIAS_SOURCES:
            records.append(_make_alias_record(
                portco_id=row.get("portco_id"),
                ownership_id=row.get("ownership_id"),
                sponsor_id=row.get("sponsor_id"),
                sponsor=row.get("sponsor"),
                sponsor_company_name=row.get("sponsor_company_name"),
                sponsor_company_slug=row.get("sponsor_company_slug"),
                source_system=source_system,
                source_entity_id=pd.NA,
                source_company_name=row.get("sponsor_company_name"),
                alias_type="source_candidate_name",
                match_method="normalized_name_pending_source_id",
                match_confidence="unmatched",
                review_status="pending_source_match",
                evidence_source="stage_consolidated_company_snapshot",
            ))

    website_matches = dataset.get("xref_website_holding_to_portco", pd.DataFrame())
    if website_matches is not None and not website_matches.empty:
        lookup = base_rows.drop_duplicates(subset=["portco_id"])[[
            "portco_id",
            "sponsor_company_name",
            "sponsor_company_slug",
        ]]
        matched = website_matches.merge(lookup, on="portco_id", how="left")
        for _, row in matched.iterrows():
            records.append(_make_alias_record(
                portco_id=row.get("portco_id"),
                ownership_id=row.get("ownership_id"),
                sponsor_id=row.get("sponsor_id"),
                sponsor=row.get("sponsor"),
                sponsor_company_name=row.get("sponsor_company_name") or row.get("seed_company_name"),
                sponsor_company_slug=row.get("sponsor_company_slug") or slug(row.get("seed_company_name")),
                source_system="sponsor_website_public_portfolio",
                source_entity_id=row.get("website_holding_candidate_id"),
                source_company_name=row.get("website_company_name"),
                alias_type="public_website_name",
                match_method=row.get("match_method"),
                match_confidence=row.get("match_confidence"),
                review_status="pending_review",
                evidence_source="xref_website_holding_to_portco",
            ))

    records.extend(_factset_pevc_company_alias_rows(dataset))

    if not records:
        return pd.DataFrame(columns=ALIAS_COLUMNS)
    return pd.DataFrame(records)[ALIAS_COLUMNS].drop_duplicates().reset_index(drop=True)


def add_company_alias_outputs(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    out["xref_company_alias_name_map"] = build_company_alias_name_map(out)
    if "xref_portco_internal_ids" in out:
        out["xref_portco_internal_ids"] = apply_company_aliases_to_portco_xref(
            out["xref_portco_internal_ids"],
            out["xref_company_alias_name_map"],
        )
    return out
