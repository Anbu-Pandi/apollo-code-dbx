"""Second-pass cross-source identity candidates from direct entity evidence."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.external_identity import (
    score_company_identity_name,
    compatible_investment_dates,
)
from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
    normalize_bbg_company_id,
)
from data_access.dbtables.sponsor_study.source_priority import (
    holding_source_priority,
)


DIRECT_ENTITY_DECISION_METHOD = "cross_source_direct_entity_v1"
DIRECT_ENTITY_RULE_VERSION = "sponsor_direct_entity_feedback_v2"


@dataclass(frozen=True)
class CohortPortco:
    sponsor_id: str
    fund_id: str
    vintage: int
    portco_id: str
    company_name: str
    source_system: str
    source_systems: tuple[str, ...]
    source_company_id: str
    has_source_duplicates: bool


@dataclass(frozen=True, order=True)
class OwnBloombergCompany:
    portco_id: str
    company_id: str

    def __post_init__(self) -> None:
        if not self.portco_id.strip() or not self.company_id.isdecimal() or int(self.company_id) == 0:
            raise ValueError("Own-company evidence requires a portfolio company and Bloomberg ID")


def own_bloomberg_companies(stack: pd.DataFrame) -> tuple[OwnBloombergCompany, ...]:
    """Parse own seed evidence; successor promotion and admitted children prove no identity."""
    ids = ("original_seed_bbg_company_id", "seed_bbg_company_id", "capital_stack_bbg_company_id")
    required = {"external_portco_id", "stack_eligibility", "legacy_seed_promoted", *ids}
    if not required.issubset(stack.columns):
        return ()
    rows = stack.loc[
        stack.stack_eligibility.eq("admitted")
        & stack.legacy_seed_promoted.astype("string").str.lower().eq("false")
    ].copy()
    for column in ids:
        rows = rows[rows[column].astype("string").str.fullmatch(r"\d+(?:\.0+)?").fillna(False)]
        rows[column] = rows[column].map(normalize_bbg_company_id)
    rows = rows[
        rows.seed_bbg_company_id.notna() & rows.seed_bbg_company_id.ne("0")
        & rows.original_seed_bbg_company_id.eq(rows.seed_bbg_company_id)
        & rows.capital_stack_bbg_company_id.eq(rows.seed_bbg_company_id)
    ]
    return tuple(sorted({
        OwnBloombergCompany(portco_id, row.seed_bbg_company_id)
        for row in rows.itertuples(index=False)
        if (portco_id := _text(row.external_portco_id))
    }))


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _vintage(value: object) -> int | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return int(numeric) if pd.notna(numeric) else None


def _cohort_portcos(holdings: pd.DataFrame) -> list[CohortPortco]:
    required = {
        "external_sponsor_id",
        "external_fund_id",
        "external_portco_id",
        "vintage",
        "company_name",
        "holding_source_system",
    }
    if holdings.empty or not required.issubset(holdings.columns):
        return []
    frame = holdings.copy()
    if "fund_assignment_is_assumed" in frame:
        frame = frame[
            ~frame["fund_assignment_is_assumed"]
            .astype("boolean")
            .fillna(True)
        ]
    rows: list[CohortPortco] = []
    group_columns = [
        "external_sponsor_id",
        "external_fund_id",
        "vintage",
        "external_portco_id",
    ]
    for cohort, group in frame.groupby(group_columns, dropna=False):
        sponsor_id, fund_id, vintage_value, portco_id = cohort
        vintage = _vintage(vintage_value)
        if not all((_text(sponsor_id), _text(fund_id), vintage, _text(portco_id))):
            continue
        sources = sorted(
            {
                _text(value)
                for value in group["holding_source_system"]
                if _text(value)
            },
            key=holding_source_priority,
        )
        if not sources:
            continue
        ranked = group.assign(
            _source_rank=group["holding_source_system"]
            .map(holding_source_priority)
        ).sort_values(
            ["_source_rank", "external_holding_id"],
            kind="mergesort",
        )
        selected = ranked.iloc[0]
        source_company_id = _text(selected.get("source_company_id"))
        rows.append(CohortPortco(
            sponsor_id=_text(sponsor_id),
            fund_id=_text(fund_id),
            vintage=vintage,
            portco_id=_text(portco_id),
            company_name=_text(selected.get("company_name")),
            source_system=sources[0],
            source_systems=tuple(sources),
            source_company_id=source_company_id or _text(portco_id),
            has_source_duplicates=bool(
                group.groupby("holding_source_system").size().gt(1).any()
            ),
        ))
    return rows


def _direct_entity_evidence(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    records: list[dict[str, str]] = []
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    if {
        "external_portco_id",
        "factset_portco_entity_id",
    }.issubset(holdings.columns):
        for row in holdings[
            ["external_portco_id", "factset_portco_entity_id"]
        ].dropna().drop_duplicates().itertuples(index=False):
            records.append({
                "external_portco_id": _text(row.external_portco_id),
                "evidence_type": "factset_own_entity",
                "evidence_value": _text(row.factset_portco_entity_id),
            })

    stack = dataset.get(
        "xref_external_company_capital_stack",
        pd.DataFrame(),
    )
    for own in own_bloomberg_companies(stack):
        records.append({
            "external_portco_id": own.portco_id,
            "evidence_type": "bloomberg_direct_seed",
            "evidence_value": own.company_id,
        })

    bridge = dataset.get("source_external_debt_issuer_bridge", pd.DataFrame())
    from data_access.dbtables.sponsor_study.factset_debt_attribution import admitted_factset_debt_rows
    bridge = admitted_factset_debt_rows(bridge)
    bridge_required = {
        "external_portco_id",
        "portco_factset_entity_id",
        "debt_factset_entity_id",
        "debt_bridge_relation",
    }
    if bridge_required.issubset(bridge.columns):
        direct = bridge[
            bridge["debt_bridge_relation"].eq("same_entity")
            & bridge["portco_factset_entity_id"].notna()
            & bridge["debt_factset_entity_id"].notna()
            & bridge["portco_factset_entity_id"]
            .astype(str)
            .eq(bridge["debt_factset_entity_id"].astype(str))
        ]
        for row in direct[
            ["external_portco_id", "debt_factset_entity_id"]
        ].drop_duplicates().itertuples(index=False):
            records.append({
                "external_portco_id": _text(row.external_portco_id),
                "evidence_type": "factset_direct_debt_entity",
                "evidence_value": _text(row.debt_factset_entity_id),
            })
    return pd.DataFrame(
        records,
        columns=[
            "external_portco_id",
            "evidence_type",
            "evidence_value",
        ],
    ).drop_duplicates()


def build_direct_entity_dedup_candidates(
    dataset: dict[str, pd.DataFrame],
    *,
    refresh_run_id: str,
) -> pd.DataFrame:
    """Build auditable direct-ID candidates; broad tree nodes are excluded."""
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS,
    )

    observations = _cohort_portcos(
        dataset.get("fact_external_holding", pd.DataFrame())
    )
    evidence = _direct_entity_evidence(dataset)
    if not observations or evidence.empty:
        return pd.DataFrame(columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)

    observation_by_portco: dict[str, list[CohortPortco]] = {}
    for observation in observations:
        observation_by_portco.setdefault(
            observation.portco_id,
            [],
        ).append(observation)

    records: list[dict[str, object]] = []
    grouped = evidence.groupby(["evidence_type", "evidence_value"])
    for (evidence_type, evidence_value), identity_group in grouped:
        portco_ids = sorted(
            identity_group["external_portco_id"].dropna().astype(str).unique()
        )
        if len(portco_ids) < 2:
            continue
        for left_id, right_id in combinations(portco_ids, 2):
            for left in observation_by_portco.get(left_id, []):
                matches = [
                    right
                    for right in observation_by_portco.get(right_id, [])
                    if (
                        right.sponsor_id,
                        right.fund_id,
                        right.vintage,
                    ) == (
                        left.sponsor_id,
                        left.fund_id,
                        left.vintage,
                    )
                ]
                for right in matches:
                    ordered = sorted(
                        (left, right),
                        key=lambda item: (
                            holding_source_priority(item.source_system),
                            item.portco_id,
                        ),
                    )
                    canonical, source = ordered
                    name = score_company_identity_name(
                        source.company_name,
                        canonical.company_name,
                    )
                    distinct_sources = set(
                        source.source_systems
                    ).isdisjoint(
                        canonical.source_systems
                    )
                    cohort_owners = {
                        observation.portco_id
                        for portco_id in portco_ids
                        for observation in observation_by_portco.get(
                            portco_id,
                            [],
                        )
                        if (
                            observation.sponsor_id,
                            observation.fund_id,
                            observation.vintage,
                        ) == (
                            left.sponsor_id,
                            left.fund_id,
                            left.vintage,
                        )
                    }
                    unique_entity_owners = len(cohort_owners) == 2
                    auto_eligible = (
                        distinct_sources
                        and unique_entity_owners
                        and len(source.source_systems) == 1
                        and not source.has_source_duplicates
                        and not canonical.has_source_duplicates
                    )
                    records.append({
                        "external_company_name_decision_id": stable_id(
                            "ext_portco_direct_dedup",
                            left.sponsor_id,
                            left.fund_id,
                            left.vintage,
                            source.portco_id,
                            canonical.portco_id,
                            evidence_type,
                            evidence_value,
                        ),
                        "external_portco_id": canonical.portco_id,
                        "external_sponsor_id": left.sponsor_id,
                        "source_system": source.source_system,
                        "source_company_id": source.source_company_id,
                        "source_external_portco_id": source.portco_id,
                        "source_company_name": source.company_name,
                        "source_company_slug": slug(source.company_name),
                        "canonical_company_name": canonical.company_name,
                        "source_holding_source_system": source.source_system,
                        "canonical_holding_source_system": (
                            canonical.source_system
                        ),
                        "decision_status": "candidate",
                        "decision_method": DIRECT_ENTITY_DECISION_METHOD,
                        "match_score": name.name_score,
                        "source_name_key": name.left_key,
                        "canonical_name_key": name.right_key,
                        "character_score": name.character_score,
                        "token_order_score": name.token_order_score,
                        "token_coverage_score": name.token_coverage_score,
                        "prefix_match_flag": name.prefix_match,
                        "exact_name_flag": name.exact_match,
                        "same_fund_flag": True,
                        "same_vintage_flag": True,
                        "independent_fund_match_flag": True,
                        "shared_fund_count": 1,
                        "shared_cohort_count": 1,
                        "candidate_margin": 1.0 if unique_entity_owners else 0.0,
                        "ownership_episode_conflict": False,
                        "corroboration_count": 2,
                        "auto_match_eligible": auto_eligible,
                        "identity_evidence_type": evidence_type,
                        "identity_evidence_value": evidence_value,
                        "evidence_summary": (
                            f"{evidence_type}={evidence_value}|"
                            f"cross_source={str(distinct_sources).lower()}|"
                            f"cohort_entity_owners={len(cohort_owners)}"
                        ),
                        "rule_version": DIRECT_ENTITY_RULE_VERSION,
                        "approved_by": pd.NA,
                        "approved_at": pd.NaT,
                        "rationale": (
                            "Same direct own-company entity inside "
                            "sponsor/fund/vintage cohort"
                        ),
                        "first_seen_run_id": refresh_run_id,
                        "last_seen_run_id": refresh_run_id,
                        "is_active": True,
                        "revoked_at": pd.NaT,
                        "revoked_by": pd.NA,
                    })
    if not records:
        return pd.DataFrame(columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    return (
        pd.DataFrame(
            records,
            columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS,
        )
        .drop_duplicates("external_company_name_decision_id")
        .reset_index(drop=True)
    )


def resolve_holdings_with_identity_decisions(
    holdings: pd.DataFrame,
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    """Apply accepted identity maps and rank whole rows for downstream matching."""
    if holdings.empty:
        return holdings.copy()
    required = {
        "external_sponsor_id",
        "external_fund_id",
        "external_portco_id",
        "vintage",
        "holding_source_system",
        "source_company_id",
        "canonical_holding_flag",
    }
    if not required.issubset(holdings.columns):
        return holdings.copy()
    accepted = decisions.copy() if decisions.empty else decisions[
        decisions["decision_status"].eq("accepted")
        & decisions["is_active"].astype("boolean").fillna(False)
    ].copy()
    accepted = accepted.sort_values(
        ["approved_at", "external_company_name_decision_id"], na_position="first"
    ) if "approved_at" in accepted else accepted

    source_redirect: dict[tuple[str, str, str], str] = {}
    portco_redirect: dict[tuple[str, str, str], str] = {}
    manual_identity_only_targets: set[tuple[str, str]] = set()
    for row in accepted.itertuples(index=False):
        sponsor_id = _text(getattr(row, "external_sponsor_id", ""))
        source_system = _text(getattr(row, "source_system", ""))
        target = _text(getattr(row, "external_portco_id", ""))
        if not source_system or not target:
            continue
        if getattr(row, "decision_method", "") == "manual_company_identity_only_v1":
            manual_identity_only_targets.add((sponsor_id, target))
        source_company_id = _text(getattr(row, "source_company_id", ""))
        source_portco_id = _text(
            getattr(row, "source_external_portco_id", "")
        )
        if source_company_id:
            source_redirect[
                (sponsor_id, source_system, source_company_id)
            ] = target
        if source_portco_id:
            portco_redirect[
                (sponsor_id, source_system, source_portco_id)
            ] = target

    out = holdings.copy()
    resolved_ids = []
    for row in out.itertuples(index=False):
        sponsor_id = _text(getattr(row, "external_sponsor_id", ""))
        source_system = _text(getattr(row, "holding_source_system", ""))
        portco_id = _text(getattr(row, "external_portco_id", ""))
        source_company_id = _text(getattr(row, "source_company_id", ""))
        resolved = portco_redirect.get(
            (sponsor_id, source_system, portco_id)
        )
        if resolved is None:
            resolved = portco_redirect.get(("", source_system, portco_id))
        if resolved is None:
            resolved = source_redirect.get(
                (sponsor_id, source_system, source_company_id),
            )
        if resolved is None:
            resolved = source_redirect.get(
                ("", source_system, source_company_id),
                portco_id,
            )
        resolved_ids.append(resolved)
    out["external_portco_id"] = resolved_ids

    group_columns = [
        "external_sponsor_id",
        "external_fund_id",
        "vintage",
        "external_portco_id",
    ]
    for _, indexes in out.groupby(group_columns, dropna=False).groups.items():
        group = out.loc[indexes]
        sponsor_id, fund_id, vintage_value, _ = (
            group.iloc[0][group_columns]
        )
        sources = group["holding_source_system"].dropna().astype(str)
        assumed = (
            group["fund_assignment_is_assumed"]
            .astype("boolean")
            .fillna(True)
            if "fund_assignment_is_assumed" in group
            else pd.Series(True, index=group.index)
        )
        eligible = (
            bool(_text(sponsor_id))
            and bool(_text(fund_id))
            and _vintage(vintage_value) is not None
            and sources.nunique() >= 2
            and bool(_text(group.iloc[0]["external_portco_id"]))
            and not bool(assumed.any())
            and compatible_investment_dates(tuple(pd.to_datetime(
                group.get("investment_date", pd.Series(pd.NaT, index=group.index))
            ).dropna()))
            and (str(sponsor_id), str(group.iloc[0]["external_portco_id"])) not in manual_identity_only_targets
            and ("", str(group.iloc[0]["external_portco_id"])) not in manual_identity_only_targets
        )
        if not eligible:
            continue
        ranked = group.assign(
            _exited_before_vintage=group.get(
                "exit_is_full", pd.Series(False, index=group.index)
            ).astype("boolean").fillna(False) & pd.to_datetime(
                group.get("exit_date", pd.Series(pd.NaT, index=group.index)),
                errors="coerce",
            ).dt.year.lt(_vintage(vintage_value)),
            _source_rank=group["holding_source_system"]
            .map(holding_source_priority),
            _canonical_rank=(
                ~group["canonical_holding_flag"]
                .astype("boolean")
                .fillna(False)
            ).astype(int),
            _investment=pd.to_datetime(
                group.get(
                    "investment_date",
                    pd.Series(pd.NaT, index=group.index),
                ),
                errors="coerce",
            ),
            _knowledge=pd.to_datetime(
                group.get(
                    "knowledge_datetime",
                    pd.Series(pd.NaT, index=group.index),
                ),
                errors="coerce",
            ),
        ).sort_values(
            [
                "holding_source_system",
                "_exited_before_vintage",
                "_canonical_rank",
                "_investment",
                "_knowledge",
                "external_holding_id",
            ],
            ascending=[True, True, True, True, False, True],
            na_position="last",
            kind="mergesort",
        )
        source_representatives = ranked.drop_duplicates(
            "holding_source_system",
            keep="first",
        ).sort_values(
            ["_source_rank", "_knowledge", "external_holding_id"],
            ascending=[True, False, True],
            na_position="last",
            kind="mergesort",
        )
        out.loc[indexes, "canonical_holding_flag"] = False
        out.loc[
            source_representatives.index[0],
            "canonical_holding_flag",
        ] = True
    return out
