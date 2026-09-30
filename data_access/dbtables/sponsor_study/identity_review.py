"""Sponsor-scoped pair review; human identity decisions never imply episode equality."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from functools import lru_cache
import json
import logging

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import stable_id, slug
from data_access.dbtables.sponsor_study.external_identity import (
    CompanySourceObservation, TOKEN_MORPHOLOGY, evaluate_company_identity,
    normalized_company_tokens, score_company_identity_name,
)
from data_access.dbtables.sponsor_study.external_identity_feedback import _direct_entity_evidence
from data_access.dbtables.sponsor_study.external_universe import _source_observations
from data_access.dbtables.sponsor_study.source_priority import holding_source_priority
from data_access.dbtables.sponsor_study.sponsor_identity import sponsor_identity_conflicts

REVIEW_METHOD = "manual_company_identity_review_v1"
IDENTITY_ONLY_METHOD = "manual_company_identity_only_v1"
ASSUMED_COHORT_METHOD = "manual_company_assumed_cohort_review_v1"
REVIEW_METHODS = {REVIEW_METHOD, IDENTITY_ONLY_METHOD, ASSUMED_COHORT_METHOD}
QUEUE_TABLE = "company_identity_review_queue"
EVENT_TABLE = "company_identity_review_event"
RULE_VERSION = "company_pair_review_v3"
QUEUE_COLUMNS = [
    "pair_id", "external_sponsor_id", "sponsor_name",
    "left_source", "left_company_id", "left_portco_id", "left_name",
    "right_source", "right_company_id", "right_portco_id", "right_name",
    "match_score", "token_score", "character_score", "token_order_score",
    "candidate_margin", "direct_evidence", "same_fund", "same_vintage",
    "episode_conflict", "review_reason", "decision_status", "decision_ids", "decision_methods", "decision_rule_versions",
    "suggested_policy", "left_holdings_json", "right_holdings_json", "rule_version",
]
EVENT_COLUMNS = [
    "event_id", "pair_id", "base_refresh_run_id", "expected_event_id",
    "external_sponsor_id", "left_source", "left_company_id", "left_portco_id",
    "right_source", "right_company_id", "right_portco_id", "decision_status",
    "holding_policy", "reviewed_by", "reviewed_at", "rationale",
]


def text_value(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


@dataclass(frozen=True, order=True)
class CompanyKey:
    sponsor: str
    source: str
    company: str


@dataclass(frozen=True)
class ReviewCompany:
    key: CompanyKey
    portco: str
    name: str
    observations: tuple[CompanySourceObservation, ...]
    holdings_json: str
    entities: frozenset[str]


def company_pair_id(left: CompanyKey, right: CompanyKey) -> str:
    if left.sponsor != right.sponsor or left == right:
        raise ValueError("A review pair requires two distinct companies within one sponsor")
    a, b = sorted((left, right))
    return stable_id("company_pair_v1", a.sponsor, a.source, a.company, b.source, b.company)


def review_companies(dataset: dict[str, pd.DataFrame]) -> tuple[ReviewCompany, ...]:
    holdings = dataset["fact_external_holding"]
    required = {"external_sponsor_id", "holding_source_system", "source_company_id",
                "external_portco_id", "company_name", "external_holding_id"}
    if not required.issubset(holdings):
        raise ValueError(f"Review holdings missing columns: {sorted(required - set(holdings))}")
    if holdings["external_holding_id"].duplicated().any():
        raise ValueError("Review requires unique holding IDs from one refresh")
    entities: dict[str, set[str]] = {}
    for row in _direct_entity_evidence(dataset).itertuples(index=False):
        if row.evidence_value:
            entities.setdefault(row.external_portco_id, set()).add(
                f"{row.evidence_type}={row.evidence_value}")
    profiles = dataset.get("source_factset_company_profile", pd.DataFrame())
    profile_lookup = {}
    if not profiles.empty:
        if profiles.factset_entity_id.duplicated().any():
            raise ValueError("FactSet review profiles require unique entity IDs")
        profile_lookup = profiles.set_index("factset_entity_id").to_dict("index")
    detail_columns = ["external_holding_id", "fund_name", "external_fund_id", "vintage",
                      "investment_date", "exit_date", "exit_is_full", "exit_evidence", "fund_assignment_is_assumed",
                      "canonical_holding_flag", "source_url", "company_url", "external_company_url",
                      "company_country", "industry", "company_description", "evidence_text"]
    result = []
    for values, group in holdings.groupby(
        ["external_sponsor_id", "holding_source_system", "source_company_id"], dropna=False
    ):
        key = CompanyKey(*(text_value(value) for value in values))
        if not all((key.sponsor, key.source, key.company)):
            raise ValueError("Review requires nonempty sponsor, source, and source company IDs")
        ids = group["external_portco_id"].dropna().astype(str).unique()
        if len(ids) != 1:
            raise ValueError(f"Source company maps to multiple canonical IDs: {key}")
        if not group["company_name"].map(text_value).any():
            logging.warning("Identity review excludes unnamed source entity %s; holdings retained", key)
            continue
        observations = _source_observations(
            group, source_system=key.source, sponsor_column="external_sponsor_id",
            company_id_column="source_company_id", fund_column="external_fund_id",
        )[(key.sponsor, key.company)]
        details = group.reindex(columns=detail_columns).sort_values("external_holding_id")
        # Supplemental current metadata uses the original source ID, never a
        # reviewed/canonical ID that could copy evidence across an unresolved pair.
        if key.source == "factset_pevc" and key.company in profile_lookup:
            profile = profile_lookup[key.company]
            details["company_url"] = profile["company_url"]
            details["external_company_url"] = profile["company_url"]
            details["company_description"] = profile["company_description"]
            details["evidence_text"] = profile["company_profile_json"]
            details["profile_observed_at"] = profile["profile_observed_at"]
        result.append(ReviewCompany(
            key, ids[0], min(row.company_name for row in observations),
            tuple(set(observations)), details.to_json(orient="records", date_format="iso"),
            frozenset(entities.get(ids[0], ())),
        ))
    return tuple(sorted(result, key=lambda company: company.key))


def episode_uncertainty(observations: tuple[CompanySourceObservation, ...]) -> bool:
    """Conservatively retain cohorts with incomplete or divergent ownership dates."""
    cohorts: dict[tuple[str, int | None], list[CompanySourceObservation]] = {}
    for row in observations:
        if not row.fund_id or row.vintage is None or row.fund_is_assumed:
            return True
        cohorts.setdefault((row.fund_id, row.vintage), []).append(row)
    for rows in cohorts.values():
        entries = {row.investment_year for row in rows}
        exits = {row.exit_year for row in rows}
        if None in entries or max(entries) - min(entries) > 1:
            return True
        if len(exits) > 1:
            return True
    return False


def latest_review_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    if events["event_id"].duplicated().any():
        raise ValueError("Duplicate review event IDs")
    if events.duplicated(["pair_id", "expected_event_id"]).any():
        raise ValueError("Concurrent review events branch from the same previous event")
    if not events.decision_status.isin(["accepted", "rejected", "revoked"]).all():
        raise ValueError("Invalid stored review status")
    events = events.copy()
    events["reviewed_at"] = pd.to_datetime(events.reviewed_at, utc=True).dt.tz_localize(None)
    if events.reviewed_at.isna().any():
        raise ValueError("Review timestamps cannot be missing")
    return events.sort_values(["reviewed_at", "event_id"]).drop_duplicates("pair_id", keep="last")


def build_review_queue(dataset: dict[str, pd.DataFrame], *, refresh_run_id: str,
                       review_events: pd.DataFrame | None = None) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.identity_review_policy import confirmed_reinvestment, investment_episodes

    if dataset["fact_external_holding"].empty:
        return pd.DataFrame(columns=[*QUEUE_COLUMNS, "refresh_run_id"])
    conflicts = sponsor_identity_conflicts(dataset["fact_external_holding"])
    if not conflicts.empty:
        raise ValueError(f"Review blocked: incompatible sponsor names share IDs: {conflicts.external_sponsor_id.nunique()}")
    companies = review_companies(dataset)
    names = dataset["fact_external_holding"][["external_sponsor_id", "sponsor_name"]].drop_duplicates()
    sponsors = names.sort_values("sponsor_name").drop_duplicates("external_sponsor_id").set_index("external_sponsor_id")["sponsor_name"].to_dict()
    by_sponsor: dict[str, list[ReviewCompany]] = {}
    for company in companies:
        by_sponsor.setdefault(company.key.sponsor, []).append(company)
    decisions = dataset.get("xref_external_company_name_decision", pd.DataFrame())
    events = latest_review_events(review_events) if review_events is not None else pd.DataFrame(columns=EVENT_COLUMNS)
    records = []
    score_name = lru_cache(maxsize=100000)(score_company_identity_name)
    for sponsor, members in by_sponsor.items():
        logging.info("Review candidates: %s (%s source companies)", sponsors[sponsor], len(members))
        # A score >= .80 must share a scoring token (the remaining components total .40).
        # Include own-entity matches independently, even with unrelated names.
        blocks: dict[str, set[int]] = {}
        for i, company in enumerate(members):
            tokens = {TOKEN_MORPHOLOGY.get(t, t) for o in company.observations
                      for t in normalized_company_tokens(o.company_name)}
            for token in tokens | {f"entity:{e}" for e in company.entities}:
                blocks.setdefault(token, set()).add(i)
        pairs = {(i, j) for indexes in blocks.values() for i, j in combinations(sorted(indexes), 2)
                 if members[i].key.source != members[j].key.source}
        member_indexes = {company.key: i for i, company in enumerate(members)}
        required_pairs = set()
        for event in events[events.external_sponsor_id.eq(sponsor)].itertuples(index=False):
            a = member_indexes.get(CompanyKey(sponsor, event.left_source, event.left_company_id))
            b = member_indexes.get(CompanyKey(sponsor, event.right_source, event.right_company_id))
            if a is not None and b is not None and a != b:
                required_pairs.add(tuple(sorted((a, b))))
        if not decisions.empty:
            settled = decisions[decisions.external_sponsor_id.eq(sponsor)
                                & decisions.decision_status.isin(["accepted", "rejected"])]
            for decision in settled.itertuples(index=False):
                a = member_indexes.get(CompanyKey(sponsor, decision.source_system, text_value(decision.source_company_id)))
                if a is not None:
                    required_pairs.update(tuple(sorted((a, b))) for b, company in enumerate(members)
                                          if a != b and company.portco == decision.external_portco_id)
        # Trust each source's entity list, including when historical decisions exist.
        required_pairs = {(i, j) for i, j in required_pairs
                          if members[i].key.source != members[j].key.source}
        pairs |= required_pairs
        source_counts = Counter(company.key.source for company in members)
        scored = []
        scores: dict[int, list[tuple[float, int]]] = {}
        for i, j in sorted(pairs):
            left, right = members[i], members[j]
            name = max((score_name(a, b)
                        for a in {o.company_name for o in left.observations}
                        for b in {o.company_name for o in right.observations}),
                       key=lambda value: value.name_score)
            direct = left.entities & right.entities
            scores.setdefault(i, []).append((name.name_score, j))
            scores.setdefault(j, []).append((name.name_score, i))
            if name.name_score >= .80 or direct or (i, j) in required_pairs:
                scored.append((i, j, name, direct))
        for i, j, name, direct in scored:
            left, right = members[i], members[j]
            alternatives = [score for index, other in ((i, j), (j, i))
                            for score, competitor in scores[index] if competitor != other]
            # Unscored no-token alternatives cannot exceed .40. Use that upper
            # bound so the displayed margin never overstates candidate separation.
            unseen = any(len(scores[index]) < len(members) - source_counts[members[index].key.source]
                         for index in (i, j))
            margin = name.name_score - max([*alternatives, .40 if unseen else 0.0])
            evidence = evaluate_company_identity(left.observations, right.observations,
                                                 candidate_margin=margin)
            conflict = episode_uncertainty(left.observations + right.observations)
            reasons = []
            if conflict:
                reasons.append("ownership_context_unresolved")
            if margin < .15:
                reasons.append("competing_company_match")
            if name.name_score <= .96:
                reasons.append("below_auto_name_threshold")
            if confirmed_reinvestment(investment_episodes(left.holdings_json)
                    + investment_episodes(right.holdings_json), name.name_score):
                reasons.append("confirmed_full_exit_reinvestment_over_10_years")
                conflict = True
            if not evidence.same_vintage:
                reasons.append("no_shared_fund_vintage")
            if name.shortest_compact_length < 4:
                reasons.append("short_name")
            pair_id = company_pair_id(left.key, right.key)
            matching = matching_decisions(decisions, left, right)
            statuses = set(matching.get("decision_status", pd.Series(dtype=str)))
            displayed_cohorts = []
            for company in (left, right):
                displayed_cohorts.append({(h["external_fund_id"], h["vintage"])
                    for h in json.loads(company.holdings_json) if h["canonical_holding_flag"] is True})
            unresolved_display = bool(displayed_cohorts[0] & displayed_cohorts[1])
            if left.portco == right.portco and unresolved_display:
                reasons.append("shared_identity_multiple_display_rows")
            status = ("accepted" if "accepted" in statuses else "rejected" if "rejected" in statuses
                      else "already_same_identity" if left.portco == right.portco and not unresolved_display else "candidate")
            records.append(dict(zip(QUEUE_COLUMNS, [
                pair_id, sponsor, sponsors[sponsor], left.key.source, left.key.company, left.portco,
                left.name, right.key.source, right.key.company, right.portco, right.name,
                name.name_score, name.token_coverage_score, name.character_score, name.token_order_score,
                margin, "|".join(sorted(direct)), evidence.same_fund, evidence.same_vintage,
                conflict, "|".join(reasons) or "manual_confirmation", status,
                "|".join(sorted(matching.get("external_company_name_decision_id", pd.Series(dtype=str)))),
                "|".join(sorted(matching.get("decision_method", pd.Series(dtype=str)).dropna().unique())),
                "|".join(sorted(matching.get("rule_version", pd.Series(dtype=str)).dropna().unique())),
                "identity_only" if conflict else "compatible_cohorts",
                left.holdings_json, right.holdings_json, RULE_VERSION,
            ])))
    out = pd.DataFrame(records, columns=QUEUE_COLUMNS)
    out["refresh_run_id"] = refresh_run_id
    return out.sort_values(["sponsor_name", "match_score", "pair_id"], ascending=[True, False, True])


def matching_decisions(decisions: pd.DataFrame, left: ReviewCompany, right: ReviewCompany) -> pd.DataFrame:
    if decisions.empty:
        return decisions
    masks = []
    for source, target in ((left, right), (right, left)):
        masks.append(
            decisions["external_sponsor_id"].map(text_value).eq(source.key.sponsor)
            & decisions["source_system"].eq(source.key.source)
            & (decisions["source_company_id"].eq(source.key.company)
               | decisions["source_external_portco_id"].eq(source.portco))
            & decisions["external_portco_id"].eq(target.portco)
        )
    return decisions[(masks[0] | masks[1]) & decisions["is_active"].fillna(False).astype(bool)]


@dataclass(frozen=True)
class ReviewSubmission:
    pair_id: str
    base_refresh_run_id: str
    decision_status: str
    holding_policy: str
    reviewed_by: str
    rationale: str
    expected_event_id: str = ""

    def __post_init__(self):
        if self.decision_status not in {"accepted", "rejected", "revoked"}:
            raise ValueError("Review status must be accepted, rejected, or revoked")
        if self.holding_policy not in {"identity_only", "compatible_cohorts", "including_assumed_cohorts"}:
            raise ValueError("Invalid holding policy")
        if not all(value.strip() for value in (self.pair_id, self.base_refresh_run_id,
                                               self.reviewed_by, self.rationale)):
            raise ValueError("Pair, refresh, reviewer, and rationale are required")


@dataclass(frozen=True)
class RejectionConstraint:
    left: tuple[str, str]
    right: tuple[str, str]
    event_id: str
    pair_id: str


def prepare_review_event(submission: ReviewSubmission, queue: pd.DataFrame,
                         events: pd.DataFrame, *, reviewed_at: pd.Timestamp) -> pd.DataFrame:
    selected = queue[queue["pair_id"].eq(submission.pair_id)]
    if len(selected) != 1:
        raise ValueError("Review pair is missing or ambiguous in the current queue")
    pair = selected.iloc[0]
    if pair.refresh_run_id != submission.base_refresh_run_id:
        raise ValueError("Stale review: regenerate the report before submitting")
    current = latest_review_events(events)
    previous = current[current["pair_id"].eq(submission.pair_id)]
    expected = "" if previous.empty else previous.iloc[0].event_id
    if expected != submission.expected_event_id:
        raise ValueError("Review changed since it was opened; reload the latest event")
    # The suggestion is automated; an explicit human review can approve compatible
    # fund cohorts. Serving still enforces fund and investment-date safeguards.
    record = {name: pair[name] for name in EVENT_COLUMNS if name in pair.index}
    record.update(vars(submission), reviewed_at=reviewed_at)
    record["event_id"] = stable_id("company_review_event", submission.pair_id,
        submission.base_refresh_run_id, expected, submission.decision_status,
        submission.holding_policy, submission.reviewed_by, submission.rationale)
    return pd.DataFrame([record], columns=EVENT_COLUMNS)


def apply_review_events(decisions: pd.DataFrame, events: pd.DataFrame,
                        holdings: pd.DataFrame) -> pd.DataFrame:
    """Apply manual decisions first and suppress automatic edges they contradict."""
    if events.empty:
        return decisions
    from data_access.dbtables.sponsor_study.external_prod_contracts import XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS

    current = latest_review_events(events)
    base = decisions[~decisions["decision_method"].isin(REVIEW_METHODS)].copy()
    base["rationale"] = base["rationale"].astype("object")
    companies = review_companies({"fact_external_holding": holdings[
        holdings.external_sponsor_id.isin(current.external_sponsor_id)]})
    lookup = {company.key: company for company in companies}
    # Remove superseded automatic edges for every explicitly reviewed pair.
    for event in current.itertuples(index=False):
        a = CompanyKey(event.external_sponsor_id, event.left_source, event.left_company_id)
        b = CompanyKey(event.external_sponsor_id, event.right_source, event.right_company_id)
        if a in lookup and b in lookup and event.decision_status != "rejected":
            matches = matching_decisions(base, lookup[a], lookup[b])
            base = base.drop(index=matches.index)
    parents: dict[tuple[str, str], tuple[str, str]] = {}

    def root(node: tuple[str, str]) -> tuple[str, str]:
        parents.setdefault(node, node)
        while parents[node] != node:
            node = parents[node]
        return node

    def join(a: tuple[str, str], b: tuple[str, str]) -> None:
        parents[root(a)] = root(b)

    active_events = []
    for event in current.itertuples(index=False):
        a = CompanyKey(event.external_sponsor_id, event.left_source, event.left_company_id)
        b = CompanyKey(event.external_sponsor_id, event.right_source, event.right_company_id)
        if a not in lookup or b not in lookup:
            continue  # Ledger survives source disappearance; no name-based rebinding.
        left, right = lookup[a], lookup[b]
        active_events.append((event, left, right))

    reviewed_portcos: dict[CompanyKey, str] = {}
    for event, left, right in active_events:
        for side, company in (("left", left), ("right", right)):
            portco = text_value(getattr(event, f"{side}_portco_id"))
            previous = reviewed_portcos.setdefault(company.key, portco)
            if previous != portco:
                raise ValueError(
                    f"Manual review ledger has conflicting portco IDs for {company.key}"
                )

    def company_node(company: ReviewCompany) -> tuple[str, str]:
        return company.key.sponsor, reviewed_portcos.get(company.key, company.portco)

    for event, left, right in active_events:
        if event.decision_status == "accepted":
            join(company_node(left), company_node(right))

    constraints = [
        RejectionConstraint(
            company_node(left), company_node(right),
            event.event_id, event.pair_id,
        )
        for event, left, right in active_events
        if event.decision_status == "rejected"
    ]
    company_by_source = {
        (company.key.sponsor, company.key.source, company.key.company): company
        for company in companies
    }
    for decision in base.itertuples(index=False):
        if decision.decision_status == "rejected" and decision.is_active:
            sponsor = text_value(decision.external_sponsor_id)
            company = company_by_source.get(
                (sponsor, decision.source_system, text_value(decision.source_company_id))
            )
            if company is not None:
                constraints.append(RejectionConstraint(
                    company_node(company), (sponsor, text_value(decision.external_portco_id)),
                    text_value(decision.external_company_name_decision_id), "",
                ))

    def conflict_for_join(left: tuple[str, str], right: tuple[str, str]) -> RejectionConstraint | None:
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            return None
        for constraint in constraints:
            rejected_left, rejected_right = root(constraint.left), root(constraint.right)
            if {rejected_left, rejected_right} == {left_root, right_root}:
                return constraint
        return None

    automatic_approvers = {"sponsor_stripped_name_identity", "system_score_identity_policy"}
    accepted = base[
        base["decision_status"].eq("accepted")
        & base["is_active"].fillna(False).astype(bool)
    ].copy()
    accepted["_automatic"] = accepted["approved_by"].map(text_value).isin(automatic_approvers)
    accepted = accepted.sort_values(
        ["_automatic", "approved_at", "external_company_name_decision_id"],
        na_position="first",
    )
    for index, decision in accepted.iterrows():
        sponsor = text_value(decision.external_sponsor_id)
        company = company_by_source.get(
            (sponsor, decision.source_system, text_value(decision.source_company_id))
        )
        if company is None:
            continue
        source = company_node(company)
        target = (sponsor, text_value(decision.external_portco_id))
        conflict = conflict_for_join(source, target)
        if conflict is None:
            join(source, target)
            continue
        if not decision["_automatic"]:
            raise ValueError(
                "Manual identity acceptance conflicts with manual rejection "
                f"{conflict.event_id or conflict.pair_id}"
            )
        reason = "blocked_by_manual_rejection:" + ":".join(
            value for value in (conflict.event_id, conflict.pair_id) if value
        )
        base.loc[index, "decision_status"] = "candidate"
        base.loc[index, "is_active"] = False
        base.loc[index, "rationale"] = reason
        logging.warning(
            "Suppressed automatic identity decision %s: %s",
            decision.external_company_name_decision_id, reason,
        )

    violated = [
        constraint for constraint in constraints
        if root(constraint.left) == root(constraint.right)
    ]
    if violated:
        conflict = sorted(violated, key=lambda value: (value.event_id, value.pair_id))[0]
        raise ValueError(
            "Rejected pair has conflicting manual identity decisions: "
            f"{conflict.event_id or conflict.pair_id}"
        )
    records = []
    for event, left, right in active_events:
        component = [company for company in companies
                     if company.key.sponsor == left.key.sponsor
                     and root(company_node(company)) == root(company_node(left))]
        canonical = min(component, key=lambda c: (holding_source_priority(c.key.source), company_node(c)[1]))
        only = any(e.decision_status == "accepted" and e.holding_policy == "identity_only"
                   and root(company_node(a)) == root(company_node(left))
                   for e, a, b in active_events)
        targets = component if event.decision_status == "accepted" else [left]
        for source in targets:
            target = canonical if event.decision_status == "accepted" else right
            if event.decision_status == "accepted" and source.key == target.key:
                continue
            records.append({
                "external_company_name_decision_id": stable_id("manual_pair_binding", event.event_id, source.key.source, source.key.company),
                "external_sponsor_id": source.key.sponsor, "source_system": source.key.source,
                "source_company_id": source.key.company, "source_external_portco_id": company_node(source)[1],
                "external_portco_id": company_node(target)[1], "source_company_name": source.name,
                "source_company_slug": slug(source.name), "canonical_company_name": target.name,
                "decision_status": "candidate" if event.decision_status == "revoked" else event.decision_status,
                "decision_method": (IDENTITY_ONLY_METHOD if only else ASSUMED_COHORT_METHOD
                    if event.holding_policy == "including_assumed_cohorts" else REVIEW_METHOD),
                "approved_by": event.reviewed_by, "approved_at": event.reviewed_at,
                "rationale": event.rationale, "identity_evidence_type": "human_pair_review",
                "identity_evidence_value": event.pair_id, "rule_version": RULE_VERSION,
                "first_seen_run_id": event.base_refresh_run_id, "last_seen_run_id": event.base_refresh_run_id,
                "is_active": event.decision_status != "revoked",
                "revoked_at": event.reviewed_at if event.decision_status == "revoked" else pd.NaT,
                "revoked_by": event.reviewed_by if event.decision_status == "revoked" else pd.NA,
            })
    manual = pd.DataFrame(records, columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    if manual.empty:
        return base
    manual = manual.sort_values(["approved_at", "external_company_name_decision_id"]).drop_duplicates(
        ["external_sponsor_id", "source_system", "source_company_id"], keep="last")
    redirects = manual[manual["decision_status"].ne("rejected")]
    keys = set(zip(redirects.external_sponsor_id, redirects.source_system, redirects.source_company_id))
    base = base[[tuple(row) not in keys for row in base[
        ["external_sponsor_id", "source_system", "source_company_id"]].itertuples(index=False, name=None)]]
    return pd.concat([base, manual], ignore_index=True)
