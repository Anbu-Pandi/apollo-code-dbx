"""Match sponsor companies through Core aliases and read persisted aggregate fundamentals.

Stored values retain aggregate access flags and model classifications for downstream handling.
Core documents remain evidence; their availability does not imply structured financial coverage.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.company_aliases import (
    AUTO_MATCH_MIN_SCORE,
    AMBIGUITY_MARGIN,
    REVIEW_MATCH_MIN_SCORE,
    TOKEN_OVERLAP_AUTO_MIN,
    _company_key,
    _score_alias_keys,
    company_name_variants,
    score_capital_structure_name,
    score_company_name_with_aliases,
)

FINDOX_FUNDAMENTAL_TIER = "debt_tier3_findox_model"
FINDOX_FUNDAMENTAL_METHOD = "findox_issuer_name_model"

FINDOX_SNAPSHOT_LINES = frozenset({"revenue", "ebitda_fda", "total_debt", "cash_and_equivs"})

FINDOX_LINE_COLUMNS = [
    "issuer_id",
    "line_name",
    "value",
    "value_as_reported",
    "currency",
    "fx_rate_usd",
    "period_end_date",
    "period_id",
    "period_name",
]

FINDOX_MATCH_COLUMNS = [
    "external_company_id",
    "company_name",
    "matched_input_name",
    "findox_issuer_id",
    "matched_findox_name",
    "matched_name_field",
    "match_score",
    "token_overlap_score",
    "exact_match_flag",
    "selected",
]

# Findox spells the same company differently across its registry and its deal archive, and the
# archive form is the one that carries the aliases the study needs ("ADMI Corp. (aka Aspen
# Dental)"). Both are indexed so either spelling can win.
FINDOX_NAME_INDEX_SQL = """
SELECT DISTINCT findox_issuer_id, findox_name, matched_name_field FROM (
    SELECT CAST(findox_id AS STRING) AS findox_issuer_id, trading_name AS findox_name,
           'aggregate_trading_name' AS matched_name_field
    FROM dbo.findox_agg_universe
    UNION ALL
    SELECT CAST(deal_issuer_id AS STRING), alias.findox_name, alias.matched_name_field
    FROM dbo.findox_core_holdings
    LATERAL VIEW STACK(4,
        issuer_name, 'core_issuer_name',
        system_issuer_name, 'core_system_issuer_name',
        client_issuer_name, 'core_client_issuer_name',
        deal_issuer_name, 'core_deal_issuer_name'
    ) alias AS findox_name, matched_name_field
    UNION ALL
    SELECT CAST(holding_deal_issuer_id AS STRING), holding_deal_issuer_name, 'core_document_issuer_name'
    FROM dbo.findox_core_documents
    UNION ALL
    SELECT CAST(holding_deal_issuer_id AS STRING), holding_client_issuer_name, 'core_document_client_name'
    FROM dbo.findox_core_documents
) names WHERE TRY_CAST(findox_issuer_id AS BIGINT) > 0 AND findox_name IS NOT NULL
"""


def _findox_name_variants(name: str) -> tuple[str, ...]:
    """Core contains legal aliases in brackets, and accented or anglicized spellings."""
    text = str(name)
    variants = list(company_name_variants(text))
    ascii_name = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    variants.extend(company_name_variants(ascii_name))
    german_name = text.translate(str.maketrans({"ö": "oe", "Ö": "Oe", "ü": "ue", "Ü": "Ue", "ä": "ae", "Ä": "Ae", "ß": "ss"}))
    variants.extend(company_name_variants(german_name))
    for bracket in re.findall(r"\(([^()]*)\)", text):
        if re.search(r"\b(combining|merged|acquired|owned|sold|subsidiary)\b", bracket, re.I):
            continue
        # 'via Sponsor' is deal context, never an issuer alias.
        bracket = re.split(r"\s+via\s+", bracket, flags=re.I)[0]
        for alias in re.split(r",\s*", bracket):
            variants.extend(company_name_variants(alias))
    return tuple(dict.fromkeys(v for v in variants if len(v) >= 3))


def fetch_findox_name_index(app: str = "databricks") -> pd.DataFrame:
    """One row per (Findox issuer, spelling), keyed for matching against study company names."""
    raw = readdfsql(FINDOX_NAME_INDEX_SQL, app=app)
    return build_findox_name_index(raw)


def build_findox_name_index(names: pd.DataFrame) -> pd.DataFrame:
    """Index every spelling, including the aliases Findox buries in parentheses.

    Findox names its deals after the borrowing entity and parenthesizes the trading name, as in
    `ADMI Corp. (aka Aspen Dental)`. Each variant is kept so the candidate lookup can be reached
    from any of them; without that, a company known only by its trading name never gets scored.
    """
    columns = ["findox_issuer_id", "findox_name", "matched_name_field", "name_key", "name_variants"]
    if names.empty:
        return pd.DataFrame(columns=columns)
    valid_id = pd.to_numeric(names["findox_issuer_id"], errors="coerce").gt(0)
    index = names[names["findox_name"].notna() & valid_id].copy()
    index["findox_issuer_id"] = pd.to_numeric(index["findox_issuer_id"]).astype("int64").astype(str)
    index["name_variants"] = index["findox_name"].map(_findox_name_variants)
    index = index[index["name_variants"].str.len().gt(0)]
    index["name_key"] = index["name_variants"].str[0]
    # A registry short name must not erase Core's longer spelling and embedded aliases.
    return index[columns].drop_duplicates(subset=["findox_issuer_id", "name_variants"]).reset_index(drop=True)


def fetch_findox_coverage(app: str = "databricks") -> pd.DataFrame:
    """Structured aggregate coverage and independent Core document evidence."""
    names = fetch_findox_name_index(app=app)
    lines = fetch_findox_fundamental_lines(
        names.findox_issuer_id.unique().tolist(), asof_cutoff=pd.Timestamp.today(), app=app)
    flags = lines.assign(present=1).pivot_table(
        index="issuer_id", columns="line_name", values="present", aggfunc="max")
    flags = flags.reindex(columns=["ebitda_fda", "revenue", "total_debt"], fill_value=0).fillna(0)
    flags.columns = ["has_ebitda", "has_revenue", "has_total_debt"]
    flags.index.name = "findox_issuer_id"
    docs = readdfsql("""
        SELECT CAST(holding_deal_issuer_id AS STRING) findox_issuer_id,
               COUNT_IF(lower(folder_path) LIKE '%financial%') docs_financial,
               COUNT_IF(lower(folder_path) LIKE '%financial%' AND NOT is_private) docs_financial_public
        FROM dbo.findox_core_documents GROUP BY holding_deal_issuer_id
    """, app=app)
    return flags.reset_index().merge(docs, on="findox_issuer_id", how="outer").fillna(0)


def resolve_companies_to_findox(
    companies: pd.DataFrame,
    name_index: pd.DataFrame,
    *,
    min_score: float = REVIEW_MATCH_MIN_SCORE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve each company to one Findox issuer, returning the alternates for audit.

    One winner per company, for the same reason the Bloomberg bridge picks one: fanning a company
    across several Findox issuers would blend unrelated lender models into one leverage figure.
    """
    if companies.empty or name_index.empty:
        return pd.DataFrame(columns=FINDOX_MATCH_COLUMNS), pd.DataFrame()

    buckets: dict[str, list[int]] = {}
    for position, variants in enumerate(name_index["name_variants"]):
        for variant in variants:
            buckets.setdefault(variant.split()[0], []).append(position)

    candidates = []
    for _, company in companies.iterrows():
        name = str(company.get("company_name") or "").strip()
        aliases = company.get("alias_names")
        capital_aliases = company.get("capital_stack_alias_names")
        search_names: list[tuple[str, bool]] = [(name, False)]
        if isinstance(aliases, (tuple, list, set)):
            search_names.extend(
                (str(alias).strip(), False) for alias in aliases
            )
        if isinstance(capital_aliases, (tuple, list, set)):
            search_names.extend(
                (str(alias).strip(), True) for alias in capital_aliases
            )
        search_names = list(dict.fromkeys(
            item for item in search_names if item[0]
        ))
        for input_name, is_capital_alias in search_names:
            seen: set[int] = set()
            variants = (
                (_company_key(input_name),)
                if is_capital_alias
                else _findox_name_variants(input_name)
            )
            for variant in variants:
                if not variant:
                    continue
                for position in buckets.get(variant.split()[0], ()):
                    if position in seen:
                        continue
                    seen.add(position)
                    row = name_index.iloc[position]
                    scorer = (
                        score_capital_structure_name
                        if is_capital_alias
                        else score_company_name_with_aliases
                    )
                    if is_capital_alias:
                        score, overlap, exact = scorer(input_name, row["findox_name"])
                    else:
                        # A short generic prefix cannot erase an input's distinguishing words.
                        scores = [_score_alias_keys(left, right) for left in variants
                                  for right in row["name_variants"]
                                  if len(right.split()) >= len(left.split()) or left == right]
                        score, overlap, exact = max(scores, default=(0.0, 0.0, False))
                    threshold = (
                        max(min_score, AUTO_MATCH_MIN_SCORE)
                        if is_capital_alias
                        else min_score
                    )
                    if score < threshold or (
                        is_capital_alias
                        and not exact
                        and overlap < TOKEN_OVERLAP_AUTO_MIN
                    ):
                        continue
                    candidates.append({
                        "external_company_id": company["external_company_id"],
                        "company_name": name,
                        "matched_input_name": input_name,
                        "findox_issuer_id": row["findox_issuer_id"],
                        "matched_findox_name": row["findox_name"],
                        "matched_name_field": row["matched_name_field"],
                        "match_score": score,
                        "token_overlap_score": overlap,
                        "exact_match_flag": bool(exact),
                        "_input_priority": 0 if input_name == name else 1,
                        "capital_stack_alias_flag": is_capital_alias,
                    })
    if not candidates:
        return pd.DataFrame(columns=FINDOX_MATCH_COLUMNS), pd.DataFrame()

    audit = pd.DataFrame(candidates)
    ranked = audit.sort_values(
        [
            "external_company_id",
            "exact_match_flag",
            "match_score",
            "token_overlap_score",
            "_input_priority",
        ],
        ascending=[True, False, False, False, True],
    )
    issuers = ranked.drop_duplicates(["external_company_id", "findox_issuer_id"]).copy()
    issuers["runner_up_score"] = issuers.groupby("external_company_id")["match_score"].shift(-1).fillna(0)
    winners = issuers.drop_duplicates("external_company_id", keep="first").copy()
    # Multiple spellings of the same issuer are not competing identities; different IDs are.
    required_margin = winners.matched_input_name.map(
        lambda name: 0.15 if len(_company_key(name).split()) == 1 else AMBIGUITY_MARGIN)
    winners = winners[
        winners.match_score.ge(AUTO_MATCH_MIN_SCORE)
        & (winners.match_score - winners.runner_up_score).ge(required_margin)
    ].copy()
    winners["selected"] = True
    audit = audit.merge(
        winners[["external_company_id", "findox_issuer_id", "selected"]],
        on=["external_company_id", "findox_issuer_id"],
        how="left",
    )
    audit["selected"] = audit["selected"].fillna(False)
    logging.info(
        "Findox name bridge resolved=%s requested=%s candidate_companies=%s withheld=%s",
        len(winners), companies["external_company_id"].nunique(), audit.external_company_id.nunique(),
        audit.external_company_id.nunique() - len(winners),
    )
    return (
        winners[FINDOX_MATCH_COLUMNS].reset_index(drop=True),
        audit.drop(columns="_input_priority"),
    )


def fetch_findox_fundamental_lines(
    issuer_ids: list[str], *, asof_cutoff: str | pd.Timestamp, app: str = "databricks",
) -> pd.DataFrame:
    """Statement/model history known by cutoff, in USD millions; selection happens after scope validation."""
    from data_access.dbtables.sponsor_study.findox_aggregate import aggregate_financials_sql

    ids = sorted({str(int(value)) for value in issuer_ids if str(value).strip()})
    if not ids:
        return pd.DataFrame(columns=FINDOX_LINE_COLUMNS)
    asof = pd.Timestamp(asof_cutoff).date().isoformat()
    frames = [readdfsql(aggregate_financials_sql(ids[start:start + 200], asof), app=app)
              for start in range(0, len(ids), 200)]
    lines = pd.concat(frames, ignore_index=True)
    if lines.empty:
        return pd.DataFrame(columns=FINDOX_LINE_COLUMNS)
    lines["issuer_id"] = lines["issuer_id"].astype(str)
    lines["value_as_reported"] = pd.to_numeric(lines["value"], errors="coerce")
    # Aggregate monetary values are already in millions, unlike legacy model exports.
    lines = restate_findox_values_usd_millions(
        lines, asof_cutoff=asof_cutoff, app=app, source_in_millions=True, retain_unconvertible=True)
    logging.info("FinDox aggregate reader requested_issuers=%s returned_issuers=%s values=%s cutoff=%s",
                 len(ids), lines.issuer_id.nunique(), len(lines), asof)
    logging.info("FinDox stored values by model allowed=%s model_type=%s period_allowed=%s",
                 lines.groupby("findox_allowed", dropna=False).size().to_dict(),
                 lines.groupby("findox_model_type", dropna=False).size().to_dict(),
                 lines.groupby("findox_period_allowed", dropna=False).size().to_dict())
    return drop_issuers_without_snapshot_lines(lines).reset_index(drop=True)


def restate_findox_values_usd_millions(
    lines: pd.DataFrame,
    *,
    asof_cutoff: str | pd.Timestamp,
    app: str = "databricks",
    source_in_millions: bool = False,
    retain_unconvertible: bool = False,
) -> pd.DataFrame:
    """Restate Findox values in USD millions, the scale the v31 benchmark uses.

    Findox reports raw currency units in the issuer's own currency, while the financial snapshot is
    denominated in USD millions. Both have to be applied or the figure is silently wrong rather than
    absent: an unconverted EUR amount is indistinguishable from a USD one. Rows whose currency has no
    rate are dropped for that reason. The rate is taken as of the period end date so a figure struck
    years before the cutoff is not converted at today's spot.
    """
    if lines.empty:
        return lines
    out = lines.copy()
    out["currency"] = out["currency"].astype("string").str.upper().str.strip()
    out["period_end_date"] = pd.to_datetime(out["period_end_date"], errors="coerce")
    out["fx_rate_usd"] = _fx_rate_per_row(out, asof_cutoff=asof_cutoff, app=app)

    unconvertible = out["fx_rate_usd"].isna()
    if unconvertible.any():
        logging.info(
            "%s %s Findox values across %s issuers with no USD rate for currency %s",
            "Retaining in audit" if retain_unconvertible else "Dropping",
            int(unconvertible.sum()),
            out.loc[unconvertible, "issuer_id"].nunique(),
            sorted(out.loc[unconvertible, "currency"].dropna().unique().tolist()) or ["(null)"],
        )
    if not retain_unconvertible:
        out = out[~unconvertible].copy()
    out["value"] = out["value_as_reported"] * out["fx_rate_usd"] / (1 if source_in_millions else 1e6)
    if retain_unconvertible:
        out.loc[unconvertible, "value"] = out.loc[unconvertible, "value_as_reported"]
        out.loc[unconvertible, "eligibility_basis"] = "currency_unresolved"
    return out


def _fx_rate_per_row(
    lines: pd.DataFrame,
    *,
    asof_cutoff: str | pd.Timestamp,
    app: str,
) -> pd.Series:
    """Local-to-USD multiplier for each row, effective as of that row's period end date."""
    from data_access.dbtables.sponsor_study.internal_joins import fetch_factset_fx_rate_history

    currencies = sorted(
        c for c in lines["currency"].dropna().unique().tolist() if c and c != "USD"
    )
    # USD needs no conversion; an unknown currency cannot be converted at all.
    rate = pd.Series(float("nan"), index=lines.index, dtype="float64")
    rate[lines["currency"].eq("USD")] = 1.0
    if not currencies:
        return rate

    history = fetch_factset_fx_rate_history(
        currencies=currencies, asof_cutoff=asof_cutoff, app=app
    )
    if history.empty:
        return rate
    history = history.assign(
        iso_currency=history["iso_currency"].astype("string").str.upper().str.strip(),
        fx_rate_date=pd.to_datetime(history["fx_rate_date"], errors="coerce"),
    ).dropna(subset=["fx_rate_date"])

    foreign = (
        lines.loc[
            lines["currency"].isin(currencies) & lines["period_end_date"].notna(),
            ["period_end_date", "currency"],
        ]
        .rename_axis("row_index")
        .reset_index()
        .sort_values("period_end_date")
    )
    matched = pd.merge_asof(
        foreign,
        history.rename(columns={"iso_currency": "currency"}).sort_values("fx_rate_date"),
        left_on="period_end_date",
        right_on="fx_rate_date",
        by="currency",
        direction="backward",
    )
    resolved = matched.set_index("row_index")["exch_rate_usd"]
    rate.loc[resolved.index] = pd.to_numeric(resolved, errors="coerce")
    return rate


def drop_issuers_without_snapshot_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """Drop issuers whose model carries none of the lines the snapshot reads."""
    if lines.empty:
        return lines
    usable = lines["line_name"].astype(str).str.lower().isin(FINDOX_SNAPSHOT_LINES)
    keep_issuers = set(lines.loc[usable, "issuer_id"])
    dropped = lines["issuer_id"].nunique() - len(keep_issuers)
    if dropped:
        logging.info("Dropped %s Findox issuers whose model carries no comparable line", dropped)
    return lines[lines["issuer_id"].isin(keep_issuers)]


def attach_findox_issuer_ids(xref: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Put `findox_issuer_id` on the debt xref so the financial snapshot can join on it."""
    out = xref.copy()
    if matches.empty:
        out["findox_issuer_id"] = pd.NA
        return out
    resolved = dict(
        zip(matches["external_company_id"].astype(str), matches["findox_issuer_id"].astype(str))
    )
    out["findox_issuer_id"] = out["portco_id"].astype(str).map(resolved)
    return out
