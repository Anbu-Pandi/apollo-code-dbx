"""Persisted binding from a company name to the keys that reach `dbo.debt_features`.

`debt_features` aggregates bonds, loans and CDS from many upstreams, and each upstream
populates a different identifier column: Bloomberg bond rows carry `id_bb_company`, JPM loan
rows carry only `ticker`, FactSet-sourced rows carry `fsym_id`/`factset_entity_id`. A company
name therefore has no single join key, and re-deriving the key on every run lets the same
name drift to a different identifier as the upstream masters change, which silently breaks
any metric series keyed on it.

This module owns the crosswalk that fixes a name to its keys once. Bindings are sticky: a
resolved row keeps its identity, its `first_seen_at` and any human `review_status` across
runs, so market-based metrics and fundamentals can be tracked against a stable key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.canonical import stable_id
from data_access.dbtables.sponsor_study.company_aliases import _company_key
from data_access.dbtables.sponsor_study.internal_joins import (
    _asof_cutoff_predicate,
    _quote_id_values,
    fetch_table_columns,
)
from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
    bbg_company_id_sql,
    normalize_bbg_company_id,
)

CROSSWALK_TABLE = "xref_company_debt_identifier"

CROSSWALK_COLUMNS = [
    "debt_identifier_id",
    "portco_id",
    "company_name",
    "company_slug",
    "key_type",
    "key_value",
    "debt_features_column",
    "key_priority",
    "resolution_source",
    "match_method",
    "match_confidence",
    "match_score",
    "review_status",
    "evidence_source",
    "resolved_asof_date",
    "first_seen_at",
    "last_seen_at",
    "is_active",
]

# Only bindings a human or an exact automated match has blessed are allowed to hydrate
# metrics. `needs_review` rows stay in the table as a work queue: a containment match such as
# `Foods Connected` -> `Ralphs Grocery Co` must be visible without being trusted.
ACCEPTED_BINDING_STATUSES = frozenset({"accepted_seed", "auto_matched", "manual_matched"})


@dataclass(frozen=True)
class DebtIdentifierKey:
    """One key family, and the `debt_features` columns it can be joined against."""

    key_type: str
    debt_columns: tuple[str, ...]
    priority: int


# Ordered by precision, mirroring the live tier ladder: the FactSet entity id identifies one
# company, Bloomberg company ids identify one company, and the corporate ticker can span
# unrelated issuers (SAFELN covers Safety-Kleen Europe and Safestore Holdings) but is the only
# key populated on loan rows. Issuer name ranks last because it is the one key that is not an
# identifier at all. Parent and ultimate-parent keys are absent by design -- joining debt on a
# corporate parent attributes a whole family's debt stack to one portfolio company.
KEY_TYPES: tuple[DebtIdentifierKey, ...] = (
    DebtIdentifierKey("factset_entity_id", ("factset_entity_id",), 0),
    DebtIdentifierKey("id_bb_company", ("id_bb_company",), 1),
    DebtIdentifierKey("obligor_bbid", ("id_bb_company",), 2),
    DebtIdentifierKey("id_bb_global_company", ("id_bb_global_company",), 3),
    DebtIdentifierKey("company_corp_ticker", ("ticker", "company_corp_ticker"), 4),
    DebtIdentifierKey("issuer_name", ("issuer_name", "issuer", "company_name"), 5),
)

_KEY_BY_TYPE = {key.key_type: key for key in KEY_TYPES}

_EMPTY_TEXT = {"", "nan", "none", "na", "n/a", "<na>"}


NUMERIC_DEBT_KEY_TYPES = frozenset({"id_bb_company", "obligor_bbid"})


def _normalize_key_value(key_type: str, key_value: str) -> str:
    if key_type in NUMERIC_DEBT_KEY_TYPES:
        normalized = normalize_bbg_company_id(key_value)
        return normalized or key_value
    return key_value


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _EMPTY_TEXT else text


@dataclass(frozen=True)
class DebtIdentifierBinding:
    """A single name-to-key binding, validated at the boundary before it reaches the table.

    Collectors parse whatever shape their upstream frame happens to have and emit this; the
    crosswalk builder accepts nothing else, so an unknown key type or an empty key value
    cannot reach the persisted table.
    """

    portco_id: str
    company_name: str
    key_type: str
    key_value: str
    resolution_source: str
    match_method: str
    match_confidence: str
    review_status: str
    match_score: float | None = None
    evidence_source: str = ""

    def __post_init__(self) -> None:
        if self.key_type not in _KEY_BY_TYPE:
            raise ValueError(f"Unknown debt identifier key type: {self.key_type}")
        for field in ("portco_id", "company_name", "key_value"):
            if not _clean(getattr(self, field)):
                raise ValueError(f"Debt identifier binding requires {field}")

    @property
    def key(self) -> DebtIdentifierKey:
        return _KEY_BY_TYPE[self.key_type]

    @property
    def binding_id(self) -> str:
        return stable_id("debt_xwalk", self.portco_id, self.key_type, self.key_value)


def _status_for_confidence(confidence: str) -> str:
    return "auto_matched" if _clean(confidence).lower() == "high" else "needs_review"


def bindings_from_xref(xref: pd.DataFrame) -> list[DebtIdentifierBinding]:
    """Bindings already carried by `xref_portco_internal_ids` from the identifier joins."""
    if xref.empty or "portco_id" not in xref:
        return []
    sources = {"factset_entity_id": "factset_entity_id", "bbg_company_id": "id_bb_company"}
    bindings = []
    for _, row in xref.iterrows():
        for column, key_type in sources.items():
            value = _clean(row.get(column))
            name = _clean(row.get("company_name"))
            if not value or not name:
                continue
            bindings.append(DebtIdentifierBinding(
                portco_id=_clean(row.get("portco_id")),
                company_name=name,
                key_type=key_type,
                key_value=_normalize_key_value(key_type, value),
                resolution_source="portco_internal_id_xref",
                match_method=_clean(row.get("match_method")) or "xref_identifier",
                match_confidence=_clean(row.get("match_confidence")) or "medium",
                review_status=_status_for_confidence(row.get("match_confidence")),
                evidence_source="xref_portco_internal_ids",
            ))
    return bindings


def bindings_from_bloomberg_matches(
    matches: pd.DataFrame,
    *,
    portco_id_column: str = "external_company_id",
) -> list[DebtIdentifierBinding]:
    """Bindings from resolved Bloomberg issuer-master rows, one per populated identifier.

    An exact name match becomes an accepted binding; a containment match is recorded for
    review instead, because token-prefix hits resolve to a parent or an unrelated issuer often
    enough to corrupt metrics -- `Orange Switzerland` reaches `Orange SA` this way.
    """
    if matches.empty or portco_id_column not in matches:
        return []
    bindings = []
    for _, row in matches.iterrows():
        exact = bool(row.get("exact_match_flag"))
        name = _clean(row.get("company_name"))
        portco = _clean(row.get(portco_id_column))
        if not name or not portco:
            continue
        stack_eligible = _clean(row.get("stack_eligibility")).lower()
        obligor_inside_value = row.get("obligor_inside_stack")
        obligor_inside = (
            None
            if obligor_inside_value is None or pd.isna(obligor_inside_value)
            else bool(obligor_inside_value)
        )
        if obligor_inside is None:
            obligor_id = normalize_bbg_company_id(row.get("obligor_bbid"))
            company_id = normalize_bbg_company_id(row.get("id_bb_company"))
            obligor_inside = (
                None
                if obligor_id is None
                else obligor_id == company_id
            )
        path_depth = pd.to_numeric(row.get("path_depth"), errors="coerce")
        descendant = bool(pd.notna(path_depth) and path_depth > 0)
        for key in KEY_TYPES:
            value = _clean(row.get(key.key_type))
            if not value:
                continue
            if key.key_type == "company_corp_ticker" and descendant:
                continue
            normalized_value = _normalize_key_value(key.key_type, value)
            review_status = "auto_matched" if exact else "needs_review"
            if stack_eligible and stack_eligible != "admitted":
                review_status = "needs_review"
            if key.key_type == "obligor_bbid" and obligor_inside is not True:
                review_status = "needs_review"
            if key.key_type == "company_corp_ticker" and obligor_inside is False:
                review_status = "needs_review"
            rejection_reason = _clean(row.get("stack_rejection_reason"))
            evidence = _clean(row.get("matched_bbg_name"))
            if review_status == "needs_review" and rejection_reason:
                evidence = "|".join(part for part in [evidence, rejection_reason] if part)
            bindings.append(DebtIdentifierBinding(
                portco_id=portco,
                company_name=name,
                key_type=key.key_type,
                key_value=normalized_value,
                resolution_source=(
                    _clean(row.get("resolution_source"))
                    or "bloomberg_issuer_master"
                ),
                match_method=(
                    _clean(row.get("match_method"))
                    or "bloomberg_issuer_master_name_bridge"
                ),
                match_confidence="high" if exact and review_status == "auto_matched" else "low",
                review_status=review_status,
                match_score=float(row.get("match_score") or 0.0),
                evidence_source=evidence,
            ))
    return bindings


def bindings_from_issuer_names(issuer_matches: pd.DataFrame) -> list[DebtIdentifierBinding]:
    """Bindings from fuzzy matches against `debt_features` issuer names."""
    if issuer_matches.empty or not {"portco_id", "issuer_label"}.issubset(issuer_matches.columns):
        return []
    bindings = []
    for _, row in issuer_matches.iterrows():
        label = _clean(row.get("issuer_label"))
        name = _clean(row.get("company_name"))
        portco = _clean(row.get("portco_id"))
        if not label or not name or not portco:
            continue
        accepted = _clean(row.get("match_status")).lower() == "accepted"
        bindings.append(DebtIdentifierBinding(
            portco_id=portco,
            company_name=name,
            key_type="issuer_name",
            key_value=label,
            resolution_source="debt_features_issuer_name",
            match_method=_clean(row.get("match_method")) or "fuzzy_company_name_mutual",
            match_confidence="medium" if accepted else "low",
            review_status="auto_matched" if accepted else "needs_review",
            match_score=float(row.get("match_score") or 0.0),
            evidence_source=label,
        ))
    return bindings


def build_debt_identifier_crosswalk(
    bindings: list[DebtIdentifierBinding],
    *,
    resolved_asof_date: str | pd.Timestamp,
    run_at: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Turn validated bindings into crosswalk rows, one per (portco, key type, key value)."""
    if not bindings:
        return pd.DataFrame(columns=CROSSWALK_COLUMNS)
    asof = pd.Timestamp(resolved_asof_date).date().isoformat()
    stamp = pd.Timestamp(run_at or pd.Timestamp.utcnow()).isoformat()
    rows = [
        {
            "debt_identifier_id": binding.binding_id,
            "portco_id": _clean(binding.portco_id),
            "company_name": _clean(binding.company_name),
            "company_slug": _company_key(binding.company_name),
            "key_type": binding.key_type,
            "key_value": _clean(binding.key_value),
            "debt_features_column": binding.key.debt_columns[0],
            "key_priority": binding.key.priority,
            "resolution_source": binding.resolution_source,
            "match_method": binding.match_method,
            "match_confidence": binding.match_confidence,
            "match_score": binding.match_score,
            "review_status": binding.review_status,
            "evidence_source": binding.evidence_source,
            "resolved_asof_date": asof,
            "first_seen_at": stamp,
            "last_seen_at": stamp,
            "is_active": True,
        }
        for binding in bindings
    ]
    frame = pd.DataFrame(rows, columns=CROSSWALK_COLUMNS)
    return frame.drop_duplicates(subset=["debt_identifier_id"]).reset_index(drop=True)


def merge_debt_identifier_crosswalk(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
) -> pd.DataFrame:
    """Fold a run's bindings into the stored crosswalk without disturbing settled rows.

    A binding seen again keeps its `first_seen_at` and its stored `review_status`, so a human
    promotion or demotion is never overwritten by the resolver that originally proposed it.

    A stored binding absent from this run is left untouched rather than retired: a company
    dropping out of the xref, or an upstream master briefly not returning it, is not evidence
    that its binding went stale. Retirement is a deliberate act, by setting `is_active` false.
    """
    if existing.empty:
        return incoming.reset_index(drop=True) if not incoming.empty else pd.DataFrame(columns=CROSSWALK_COLUMNS)
    stored = existing.copy()
    for column in CROSSWALK_COLUMNS:
        if column not in stored:
            stored[column] = pd.NA
    stored = stored[CROSSWALK_COLUMNS]
    if incoming.empty:
        return stored.reset_index(drop=True)

    settled = stored.set_index("debt_identifier_id")
    refreshed = incoming.set_index("debt_identifier_id")
    seen_again = refreshed.index.intersection(settled.index)
    for column in ("last_seen_at", "match_score", "match_method", "resolved_asof_date"):
        settled.loc[seen_again, column] = refreshed.loc[seen_again, column]
    added = refreshed.loc[refreshed.index.difference(settled.index)]
    merged = pd.concat([settled, added]).reset_index()
    logging.info(
        "Crosswalk merge: %s stored, %s refreshed, %s added", len(stored), len(seen_again), len(added)
    )
    return merged[CROSSWALK_COLUMNS]


def accepted_bindings(crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Active bindings cleared for metric hydration, most precise key first."""
    if crosswalk.empty:
        return pd.DataFrame(columns=CROSSWALK_COLUMNS)
    accepted = crosswalk[
        crosswalk["review_status"].isin(ACCEPTED_BINDING_STATUSES)
        & crosswalk["is_active"].astype("boolean").fillna(False)
    ]
    return accepted.sort_values("key_priority").reset_index(drop=True)


def fetch_debt_features_for_crosswalk(
    crosswalk: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """`debt_features` rows reachable from accepted bindings, tagged with `portco_id`.

    Whichever key family a company was bound through, the rows come back the same way, so a
    loan-only issuer reached by ticker and a bond issuer reached by Bloomberg company id are
    hydrated by one query. Where a company is bound through several keys the most precise one
    wins, so a ticker shared with an unrelated issuer cannot override a company id.
    """
    bindings = accepted_bindings(crosswalk)
    if bindings.empty:
        return pd.DataFrame()
    available = {col.lower(): col for col in fetch_table_columns(table_name, app=app)}
    clauses: list[str] = []
    owners: list[tuple[str, dict[str, str], bool]] = []
    for key_type, group in bindings.groupby("key_type", sort=False):
        key = _KEY_BY_TYPE.get(str(key_type))
        if key is None:
            continue
        numeric = str(key_type) in NUMERIC_DEBT_KEY_TYPES
        values = {
            _normalize_key_value(str(key_type), _clean(value)): _clean(portco)
            for value, portco in zip(group["key_value"], group["portco_id"])
            if _clean(value) and _clean(portco)
        }
        if not values:
            continue
        for debt_column in key.debt_columns:
            if debt_column not in available:
                continue
            clauses.append(
                f"{bbg_company_id_sql(available[debt_column])} IN ({_quote_id_values(sorted(values))})"
            )
            owners.append((available[debt_column], values, numeric))
    if not clauses:
        return pd.DataFrame()
    date_col = available.get("asof_date", "asof_date")
    where_clause = "\n       OR ".join(clauses)
    sql = f"""
SELECT *
FROM {table_name}
WHERE cast({date_col} as date) = (
    SELECT max(cast({date_col} as date)) FROM {table_name}
    {_asof_cutoff_predicate(date_col, asof_cutoff)}
)
  AND ({where_clause})
"""
    features = readdfsql(sql, app=app)
    if features.empty:
        return features
    portco_id = pd.Series(pd.NA, index=features.index, dtype="object")
    for debt_column, values, numeric in owners:
        if debt_column not in features:
            continue
        source = (
            features[debt_column].map(normalize_bbg_company_id)
            if numeric
            else features[debt_column].astype("string").str.strip()
        )
        mapped = source.map(values)
        portco_id = portco_id.fillna(mapped)
    features["portco_id"] = portco_id
    return features[features["portco_id"].notna()].copy()
