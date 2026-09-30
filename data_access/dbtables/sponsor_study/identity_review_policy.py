"""Audited cross-source identity acceptance, independent of ownership episodes."""

from dataclasses import dataclass
from datetime import date
from itertools import combinations
import json

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.identity_review import (
    CompanyKey, latest_review_events, text_value,
)
from data_access.dbtables.sponsor_study.source_priority import holding_source_priority

AUTO_METHOD = "score_company_identity_v1"
AUTO_ONLY_METHOD = "score_company_identity_only_v1"
AUTO_METHODS = {AUTO_METHOD, AUTO_ONLY_METHOD}
SCORE_THRESHOLD = .96


@dataclass(frozen=True)
class InvestmentEpisode:
    holding_id: str
    entry: date | None
    exit: date | None
    full_exit: bool | None


def investment_episodes(payload: str) -> tuple[InvestmentEpisode, ...]:
    def parsed(value: object) -> date | None:
        return None if value is None else pd.Timestamp(value).date()

    return tuple(InvestmentEpisode(str(row["external_holding_id"]),
        parsed(row.get("investment_date")), parsed(row.get("exit_date")), row.get("exit_is_full"))
        for row in json.loads(payload))


def confirmed_reinvestment(episodes: tuple[InvestmentEpisode, ...], score: float) -> bool:
    """Strictly over ten calendar years; documented full exit precedes re-entry."""
    if score <= SCORE_THRESHOLD:
        return False
    for left, right in combinations(episodes, 2):
        if left.entry is None or right.entry is None:
            continue
        earlier, later = sorted((left, right), key=lambda episode: episode.entry)
        if (earlier.full_exit is not True or earlier.exit is None
                or not earlier.entry <= earlier.exit < later.entry
                or pd.Timestamp(later.entry) <= pd.Timestamp(earlier.entry) + pd.DateOffset(years=10)):
            continue
        # Conflicting observations of that acquisition keep the episode unresolved.
        if any(e.entry == earlier.entry and (e.full_exit is False or e.exit is None
               or e.exit >= later.entry) for e in episodes):
            continue
        return True
    return False


@dataclass(frozen=True)
class Pair:
    pair_id: str
    left: CompanyKey
    right: CompanyKey
    left_portco: str
    right_portco: str
    left_name: str
    right_name: str
    score: float
    identity_only: bool

    @classmethod
    def from_row(cls, row) -> "Pair":
        return cls(row.pair_id,
            CompanyKey(row.external_sponsor_id, row.left_source, row.left_company_id),
            CompanyKey(row.external_sponsor_id, row.right_source, row.right_company_id),
            row.left_portco_id, row.right_portco_id, row.left_name, row.right_name,
            float(row.match_score), row.suggested_policy == "identity_only")


def apply_score_policy(dataset: dict[str, pd.DataFrame], queue: pd.DataFrame,
                       events: pd.DataFrame, *, refresh_run_id: str) -> pd.DataFrame:
    """Accept >.96 pending pairs unless the complete component contradicts source IDs/review."""
    from data_access.dbtables.sponsor_study.external_prod_contracts import XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS

    decisions = dataset["xref_external_company_name_decision"].copy()
    out = queue.copy()
    parents: dict[CompanyKey, CompanyKey] = {}
    companies: dict[CompanyKey, tuple[str, str]] = {}

    def root(key: CompanyKey) -> CompanyKey:
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def join(a: CompanyKey, b: CompanyKey) -> None:
        parents[root(a)] = root(b)

    pairs = tuple(Pair.from_row(row) for row in queue.itertuples(index=False))
    for pair in pairs:
        companies[pair.left] = (pair.left_portco, pair.left_name)
        companies[pair.right] = (pair.right_portco, pair.right_name)
    # Include source IDs absent from candidate blocking, so transitive collisions
    # cannot hide outside the report's candidate subset.
    holdings = dataset["fact_external_holding"]
    for row in holdings.drop_duplicates(["external_sponsor_id", "holding_source_system", "source_company_id"]).itertuples(index=False):
        key = CompanyKey(row.external_sponsor_id, row.holding_source_system, row.source_company_id)
        companies.setdefault(key, (row.external_portco_id, row.company_name))
    by_portco: dict[tuple[str, str], list[CompanyKey]] = {}
    for key, (portco, _) in companies.items():
        by_portco.setdefault((key.sponsor, portco), []).append(key)
    for keys in by_portco.values():
        for key in keys[1:]:
            join(keys[0], key)
    rejected = []
    reviewed = set()
    for row in decisions[decisions.is_active.fillna(False)].itertuples(index=False):
        source = CompanyKey(text_value(row.external_sponsor_id), row.source_system, text_value(row.source_company_id))
        targets = by_portco.get((source.sponsor, row.external_portco_id), ())
        if source not in companies or not targets:
            continue
        if row.decision_status == "accepted":
            join(source, targets[0])
            if text_value(row.approved_by) not in {"sponsor_stripped_name_identity", "system_score_identity_policy"}:
                reviewed.add(source)
        elif row.decision_status == "rejected":
            rejected.append((source, targets[0]))
    for row in latest_review_events(events).itertuples(index=False):
        reviewed.update((CompanyKey(row.external_sponsor_id, row.left_source, row.left_company_id),
                         CompanyKey(row.external_sponsor_id, row.right_source, row.right_company_id)))
    candidates = {row.pair_id for row in queue.itertuples(index=False)
                  if row.decision_status == "candidate" and row.match_score > SCORE_THRESHOLD}
    eligible = [p for p in pairs if p.pair_id in candidates and p.left.source != p.right.source]
    for pair in eligible:
        join(pair.left, pair.right)
    groups: dict[CompanyKey, list[CompanyKey]] = {}
    for key in companies:
        groups.setdefault(root(key), []).append(key)
    blocked = {root(a) for a, b in rejected if root(a) == root(b)}
    blocked.update(root(key) for key in reviewed if key in companies)
    for key, members in groups.items():
        sources = [member.source for member in members]
        if len(sources) != len(set(sources)):
            blocked.add(key)
    records = []
    accepted_groups: dict[CompanyKey, list[Pair]] = {}
    for pair in eligible:
        if root(pair.left) in blocked:
            mask = out.pair_id.eq(pair.pair_id)
            out.loc[mask, "review_reason"] += "|score_policy_blocked_source_or_review_conflict"
        else:
            accepted_groups.setdefault(root(pair.left), []).append(pair)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    for key, edges in accepted_groups.items():
        members = groups[key]
        canonical = min(members, key=lambda c: (holding_source_priority(c.source), companies[c][0]))
        target, target_name = companies[canonical]
        only = any(edge.identity_only for edge in edges)
        method = AUTO_ONLY_METHOD if only else AUTO_METHOD
        pair_ids = "|".join(sorted(edge.pair_id for edge in edges))
        decision_ids = []
        for source in members:
            portco, name = companies[source]
            if source == canonical:
                continue
            # Prior automatic edges remain in the ledger as inactive audit records.
            superseded = (decisions.external_sponsor_id.eq(source.sponsor)
                & decisions.source_system.eq(source.source) & decisions.source_company_id.eq(source.company)
                & decisions.decision_status.eq("accepted"))
            decisions.loc[superseded, "is_active"] = False
            decision_id = stable_id("score_identity", source.sponsor, source.source, source.company, target)
            decision_ids.append(decision_id)
            records.append({
                "external_company_name_decision_id": decision_id,
                "external_sponsor_id": source.sponsor, "source_system": source.source,
                "source_company_id": source.company, "source_external_portco_id": portco,
                "external_portco_id": target, "source_company_name": name, "source_company_slug": slug(name),
                "canonical_company_name": target_name, "decision_status": "accepted", "decision_method": method,
                "match_score": min(edge.score for edge in edges), "approved_by": "system_score_identity_policy",
                "approved_at": now, "rationale": "Cross-source component accepted; each new pair scores > 0.96. Stored score is the minimum new-edge score. Unresolved ownership remains identity-only.",
                "identity_evidence_type": "scored_company_pair_component", "identity_evidence_value": pair_ids,
                "rule_version": "company_score_identity_v1", "first_seen_run_id": refresh_run_id,
                "last_seen_run_id": refresh_run_id, "is_active": True,
            })
        mask = out.pair_id.isin([edge.pair_id for edge in edges])
        out.loc[mask, "decision_status"] = "accepted"
        out.loc[mask, "decision_ids"] = "|".join(sorted(decision_ids))
        out.loc[mask, "decision_methods"] = method
        out.loc[mask, "decision_rule_versions"] = "company_score_identity_v1"
        out.loc[mask, "review_reason"] += "|score_above_0_96_identity_accepted"
    additions = pd.DataFrame(records, columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    dataset["xref_external_company_name_decision"] = pd.concat([decisions, additions], ignore_index=True).drop_duplicates(
        "external_company_name_decision_id", keep="last")
    return out


def score_policy_error_count(dataset: dict[str, pd.DataFrame]) -> int:
    decisions = dataset.get("xref_external_company_name_decision", pd.DataFrame())
    if decisions.empty or "decision_method" not in decisions:
        return 0
    scored = decisions[decisions.decision_method.isin(AUTO_METHODS)
        & decisions.decision_status.eq("accepted") & decisions.is_active.fillna(False)]
    if scored.empty:
        return 0
    holdings = dataset["fact_external_holding"]
    sources = set(holdings[["external_sponsor_id", "holding_source_system", "source_company_id"]]
                  .itertuples(index=False, name=None))
    targets = set(holdings[["external_sponsor_id", "external_portco_id"]].itertuples(index=False, name=None))
    return sum(not (pd.notna(row.match_score) and float(row.match_score) > SCORE_THRESHOLD
        and (row.external_sponsor_id, row.source_system, row.source_company_id) in sources
        and (row.external_sponsor_id, row.external_portco_id) in targets
        and row.approved_by == "system_score_identity_policy"
        and pd.notna(row.identity_evidence_value)) for row in scored.itertuples(index=False))
