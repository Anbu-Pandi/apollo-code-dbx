"""Select sponsor fundamentals per metric and retain reproducible source evidence."""
from dataclasses import asdict, dataclass, replace
from functools import cached_property, lru_cache
from hashlib import sha256
import json
import math
from typing import Literal

import pandas as pd


BASE_METRICS = ("current_total_debt", "current_total_cash", "current_net_debt",
                "current_ltm_revenue", "current_ltm_ebitda", "current_enterprise_value")
OTHER_METRICS = ("current_gross_leverage", "current_net_leverage", "ltv",
                 "yoy_revenue_growth", "yoy_ebitda_growth")

DEBT_METRIC_COLUMNS = {
    "current_enterprise_value": ("ff_entrpr_val_usd", "ff_entrpr_val", "ff_entrpr_val_factset", "ff_entrpr_val_findox"),
    "current_total_debt": ("ff_debt_usd", "ff_debt", "ff_debt_findox", "ff_debt_factset",
                           "total_debt", "current_total_debt", "debt"),
    "current_total_cash": ("ff_cash_only_usd", "ff_cash_only", "ff_cash_only_findox",
                           "ff_cash_only_factset", "cash", "current_total_cash"),
    "current_net_debt": ("ff_net_debt_usd", "ff_net_debt", "ff_net_debt_findox",
                         "ff_net_debt_factset", "net_debt", "current_net_debt"),
    "current_ltm_revenue": ("ff_sales_usd", "ff_sales", "ff_sales_findox", "ff_sales_factset",
                            "revenue", "ltm_revenue", "current_ltm_revenue"),
    "current_ltm_ebitda": ("ff_ebitda_oper_usd", "ff_ebitda_oper", "ff_ebitda_oper_findox",
                           "ff_ebitda_oper_factset", "ebitda", "ltm_ebitda", "current_ltm_ebitda"),
    "current_gross_leverage": ("ff_debt_ebitda_oper", "ff_debt_ebitda_oper_findox",
                               "ff_debt_ebitda_oper_factset", "current_gross_leverage"),
    "current_net_leverage": ("current_net_leverage", "net_leverage"),
}


@dataclass(frozen=True)
class FinancialCandidate:
    portco_id: str
    metric: str
    value: float
    source: str
    identifier: str | None
    identifier_type: str | None
    period: str | None
    asof: str | None
    source_column: str
    source_row: str | None = None
    currency: str | None = None
    fx_rate: float | None = None
    source_value: float | None = None
    source_loaded_at: str | None = None
    company_ticker: str | None = None
    source_table: str = "dbo.debt_features"
    aggregate_company_id: str | None = None
    report_issue_date: str | None = None
    source_unit: str | None = None
    source_sign_multiplier: float = 1.0
    eligibility_basis: str | None = None
    findox_allowed: bool | None = None
    findox_period_allowed: bool | None = None
    findox_model_type: str | None = None
    findox_access_type: str | None = None
    findox_access_asof: str | None = None
    provider: Literal['factset', 'findox', 'unattributed'] = 'unattributed'
    reporting_entity_id: str | None = None
    reporting_scope_id: str | None = None
    scope_status: Literal['unverified', 'confirmed', 'rejected'] = 'unverified'
    scope_valid_from: str | None = None
    scope_valid_to: str | None = None
    scope_evidence_id: str | None = None
    period_id: str | None = None
    source_version: str | None = None
    source_record_id: str | None = None
    metric_preference: int = 0
    scope_recorded_at: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("Financial values must be finite")
        if self.metric not in (*BASE_METRICS, *OTHER_METRICS, "current_ebitda_margin"):
            raise ValueError(f"Unknown financial metric: {self.metric}")
        if self.source == 'findox' and self.provider == 'unattributed':
            object.__setattr__(self, 'provider', 'findox')
        if self.scope_status == 'confirmed' and not (
                self.reporting_scope_id and self.scope_evidence_id and self.scope_valid_from and self.scope_recorded_at):
            raise ValueError('Confirmed reporting scope requires dated evidence')
        if self.provider not in PROVIDER_PRIORITY or self.scope_status not in ('unverified', 'confirmed', 'rejected'):
            raise ValueError('Invalid financial provider or scope status')
        if self.scope_valid_to and (not self.scope_valid_from or
                pd.Timestamp(self.scope_valid_to) <= pd.Timestamp(self.scope_valid_from)):
            raise ValueError('Reporting scope interval must be nonempty')

    @cached_property
    def candidate_id(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]

    @cached_property
    def statement_id(self) -> str:
        """A reporting statement identity, independent of its revisions and retrieval route."""
        key = (self.provider, self.reporting_entity_id or self.identifier, self.aggregate_company_id,
               self.source_row if self.source == 'findox' else None, self.period_id, self.period, self.metric)
        return sha256(json.dumps(key).encode()).hexdigest()[:32]


PROVIDER_PRIORITY = {'factset': 0, 'findox': 1, 'unattributed': 2}


def _scope_key(candidate: FinancialCandidate) -> tuple[str, str, str]:
    return (candidate.reporting_entity_id or candidate.identifier or '',
            candidate.aggregate_company_id or '', candidate.reporting_scope_id or '')


def _scope_reason(candidate: FinancialCandidate, cutoff: str | None, *, historical: bool = False) -> str | None:
    if candidate.eligibility_basis == 'currency_unresolved':
        return 'currency_unresolved'
    if candidate.scope_status == 'rejected':
        return 'reporting_scope_rejected'
    if cutoff and any(value and _timestamp(value) > _timestamp(cutoff) for value in
                      (candidate.period, candidate.asof, candidate.report_issue_date,
                       candidate.source_loaded_at, candidate.scope_recorded_at)):
        return 'not_known_at_cutoff'
    if candidate.scope_status == 'confirmed':
        # Effective intervals are half-open; never carry an old M&A perimeter forward.
        for observed in (candidate.period, None if historical else cutoff):
            if observed and (_timestamp(observed) < _timestamp(candidate.scope_valid_from)
                    or candidate.scope_valid_to and _timestamp(observed) >= _timestamp(candidate.scope_valid_to)):
                return 'outside_reporting_scope_interval'
    return None


@lru_cache(maxsize=4096)
def _timestamp(value: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def resolve_financial_candidates(candidates: tuple[FinancialCandidate, ...], *,
                                 cutoff: str | None = None, historical: bool = False) -> tuple[dict[str, FinancialCandidate], dict[str, str]]:
    """Resolve business scope before date/provider ranking; retain every decision."""
    candidates = tuple(dict.fromkeys(candidates))
    reasons = {c.candidate_id: reason for c in candidates if (reason := _scope_reason(c, cutoff, historical=historical))}
    eligible = [c for c in candidates if c.candidate_id not in reasons]
    # Different models/entities are not revisions. A reviewed current scope may resolve them.
    for provider in PROVIDER_PRIORITY:
        group = [c for c in eligible if c.provider == provider]
        confirmed = [c for c in group if c.scope_status == 'confirmed']
        modeled = [c for c in group if c.aggregate_company_id]
        scopes = {_scope_key(c) for c in (confirmed or modeled or group)}
        if len(scopes) > 1:
            reasons.update((c.candidate_id, 'ambiguous_reporting_scope') for c in group)
        elif confirmed:
            reasons.update((c.candidate_id, 'unconfirmed_reporting_scope') for c in group
                           if _scope_key(c) not in scopes)
        elif modeled:
            reasons.update((c.candidate_id, 'reporting_model_not_identified') for c in group
                           if not c.aggregate_company_id)
    chosen: dict[str, FinancialCandidate] = {}
    for metric in dict.fromkeys(c.metric for c in candidates):
        options = [c for c in eligible if c.metric == metric and c.candidate_id not in reasons]
        if not options:
            continue
        # Source precedence applies only after scope validation; zero is a valid value.
        options.sort(key=lambda c: (PROVIDER_PRIORITY[c.provider],
            -(_timestamp(c.period).value if c.period else 0),
            -(_timestamp(c.asof).value if c.asof else 0),
            c.metric_preference,
            -(_timestamp(c.source_loaded_at).value if c.source_loaded_at else 0),
            c.source_table != 'dbo.debt_features', c.candidate_id))
        while options:
            winner = options[0]
            tied = [c for c in options if (c.provider, c.period, c.asof, c.source_loaded_at, c.source_table, c.metric_preference)
                    == (winner.provider, winner.period, winner.asof, winner.source_loaded_at, winner.source_table, winner.metric_preference)]
            if len({c.value for c in tied}) <= 1:
                break
            reasons.update((c.candidate_id, 'conflicting_statement_values') for c in options if c.provider == winner.provider)
            options = [c for c in options if c.provider != winner.provider]
        if not options:
            continue
        chosen[metric] = winner
        for c in options:
            reasons[c.candidate_id] = ('selected_' + c.provider if c == winner else
                'provider_precedence' if c.provider != winner.provider else
                'older_reporting_period' if c.period != winner.period else
                'metric_definition_preference' if c.metric_preference != winner.metric_preference else
                'alternative_statement' if c.statement_id != winner.statement_id else
                'unchanged_statement_observation' if c.value == winner.value else
                'superseded_statement_revision' if (c.asof, c.source_loaded_at, c.source_version) !=
                (winner.asof, winner.source_loaded_at, winner.source_version) else 'duplicate_observation')
    return chosen, reasons


def debt_provider_candidates(candidate: FinancialCandidate, row: pd.Series) -> tuple[FinancialCandidate, ...]:
    """Parse provider-specific producer columns, preserving both values and their units."""
    columns = DEBT_METRIC_COLUMNS.get(candidate.metric, ())
    root = columns[0].removesuffix('_usd') if columns else ''
    observations = []
    for provider in ('factset', 'findox'):
        column = root + '_' + provider
        raw = pd.to_numeric(row.get(column), errors='coerce')
        if pd.isna(raw) or not math.isfinite(raw):
            continue
        currency = row.get('currency_' + provider)
        if pd.isna(currency) and provider == 'factset':
            currency = row.get('currency_financial')
        currency = str(currency).upper() if pd.notna(currency) else None
        usd = pd.to_numeric(row.get(column + '_usd'), errors='coerce')
        coalesced = pd.to_numeric(row.get(root), errors='coerce')
        coalesced_usd = pd.to_numeric(row.get(root + '_usd'), errors='coerce')
        fx = pd.to_numeric(row.get('fx_rate'), errors='coerce')
        eligibility = 'provider_column'
        if pd.notna(usd):
            value = float(usd)
        elif pd.notna(coalesced) and raw == coalesced and pd.notna(coalesced_usd):
            value = float(coalesced_usd)
        elif currency == 'USD' or candidate.metric not in BASE_METRICS:
            value = float(raw)
        elif provider == 'factset' and pd.notna(fx) and fx > 0:
            value = float(raw / fx)
        else:
            value, eligibility = float(raw), 'currency_unresolved'
        period = row.get('period_end_date_' + provider)
        if pd.isna(period) and provider == 'factset':
            period = candidate.period
        entity = row.get('findox_issuer_id') if provider == 'findox' else row.get('ticker_region')
        observations.append(replace(candidate, provider=provider, value=value, source_column=column,
            source_value=float(raw), currency=currency,
            fx_rate=float(raw / value) if value else None, eligibility_basis=eligibility,
            period=str(period) if pd.notna(period) else None,
            reporting_entity_id=str(entity) if pd.notna(entity) else candidate.identifier,
            source_version=candidate.source_record_id or candidate.asof,
            source_unit='currency_millions' if candidate.metric in BASE_METRICS else 'ratio'))
    return tuple(observations) or (candidate,)


def _combined_permission(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    return True if left is True and right is True else None


def select_financial_values(candidates: tuple[FinancialCandidate, ...], *, cutoff: str | None = None) -> dict[str, object]:
    """One eligible value per metric: FactSet, then FinDox, then unattributed legacy data."""
    chosen, reasons = resolve_financial_candidates(candidates, cutoff=cutoff)
    ledger = [{**asdict(c), 'candidate_id': c.candidate_id, 'statement_id': c.statement_id,
               'selection_reason': reasons[c.candidate_id],
               'selected': chosen.get(c.metric) == c} for c in dict.fromkeys(candidates)]
    values = {metric: chosen[metric].value if metric in chosen else None
              for metric in (*BASE_METRICS, *OTHER_METRICS, "current_ebitda_margin")}
    audit = {metric: {**asdict(item), 'candidate_id': item.candidate_id, "selection_reason": reasons[item.candidate_id],
                     'comparability_status': 'confirmed_scope' if item.scope_status == 'confirmed' else 'unverified_scope'}
             for metric, item in chosen.items()}

    def derive(metric: str, left: str, right: str, divide: bool) -> None:
        if values[metric] is not None or left not in chosen or right not in chosen:
            return
        a, b = chosen[left], chosen[right]
        # Do not silently combine different reporting periods, revisions, or borrower IDs.
        same_row = a.source_record_id is not None and a.source_record_id == b.source_record_id
        if ((a.period is None or a.identifier is None) and not same_row or a.period != b.period
                or a.asof != b.asof
                or (a.source, a.identifier, a.aggregate_company_id) !=
                   (b.source, b.identifier, b.aggregate_company_id)
                or (a.provider, a.reporting_scope_id) != (b.provider, b.reporting_scope_id)):
            return
        if divide and b.value == 0:
            return
        values[metric] = a.value / b.value if divide else a.value - b.value
        chosen[metric] = replace(a, metric=metric, value=values[metric],
            findox_allowed=_combined_permission(a.findox_allowed, b.findox_allowed),
            findox_period_allowed=_combined_permission(a.findox_period_allowed, b.findox_period_allowed),
            findox_model_type=a.findox_model_type if a.findox_model_type == b.findox_model_type else "mixed",
            findox_access_type=a.findox_access_type if a.findox_access_type == b.findox_access_type else "mixed")
        audit[metric] = {**asdict(chosen[metric]), "source": "derived", "inputs": [left, right],
                         "candidate_id": chosen[metric].candidate_id,
                         "leaf_candidate_ids": {**audit[left].get("leaf_candidate_ids", {left: a.candidate_id}),
                                                **audit[right].get("leaf_candidate_ids", {right: b.candidate_id})},
                         "period": a.period, "asof": a.asof,
                         "selection_reason": "same_source_period_and_snapshot"}

    derive("current_net_debt", "current_total_debt", "current_total_cash", False)
    derive("current_gross_leverage", "current_total_debt", "current_ltm_ebitda", True)
    derive("current_net_leverage", "current_net_debt", "current_ltm_ebitda", True)
    derive("current_ebitda_margin", "current_ltm_ebitda", "current_ltm_revenue", True)
    derive("ltv", "current_total_debt", "current_enterprise_value", True)
    from data_access.dbtables.sponsor_study.financial_history import comparison_status
    comparison = {}
    for metric, growth in [('current_ltm_revenue', 'yoy_revenue_growth'), ('current_ltm_ebitda', 'yoy_ebitda_growth')]:
        current = chosen.get(metric)
        prior = [c for c in candidates if current and current.period and c.metric == metric and c.period
                 and pd.Timestamp(c.period) == pd.Timestamp(current.period) - pd.DateOffset(years=1)
                 and c.provider == current.provider and c.scope_status != 'rejected'
                 and (not cutoff or not _scope_reason(c, cutoff, historical=True))]
        previous, _ = resolve_financial_candidates(tuple(prior), cutoff=cutoff, historical=True)
        previous = previous.get(metric)
        status = comparison_status(current, previous) if current and previous else 'prior_period_unavailable_or_ambiguous'
        comparison[metric] = status
        if current and previous and status == 'comparable' and previous.value != 0:
            values[growth] = current.value / previous.value - 1
            audit[growth] = {**asdict(current), 'source': 'derived',
                             'leaf_candidate_ids': {metric: current.candidate_id}, 'selection_reason': 'same_confirmed_reporting_scope',
                             'candidate_ids': [current.candidate_id, previous.candidate_id]}
        else:
            values[growth] = None
        if metric in audit:
            audit[metric]['yoy_comparability_status'] = status
    for metric, value in values.items():
        if value is None:
            audit[metric] = {"source": None, "selection_reason": "no_eligible_value"}
    if not chosen:
        return {'portco_id': candidates[0].portco_id, 'financial_provenance_json': json.dumps(audit),
                'financial_review_required': True, 'financial_comparability_json': json.dumps(comparison),
                '_candidate_ledger': ledger} if candidates else {}
    sources = {item.source for item in chosen.values()}
    primary = chosen.get("current_ltm_ebitda") or next(iter(chosen.values()))
    source = primary.source if len(sources) == 1 else (
        "debt_features+findox" if "findox" in sources else primary.source)
    return {**values, "portco_id": primary.portco_id, "source_system": source,
            "financial_identifier": primary.identifier, "financial_identifier_type": primary.identifier_type,
            "asof_date": pd.Timestamp(primary.asof) if primary.asof else pd.NaT,
            "period_end_date": pd.Timestamp(primary.period) if primary.period else pd.NaT,
            "company_corp_ticker": primary.company_ticker,
            "financial_provenance_json": json.dumps(audit, sort_keys=True, allow_nan=False),
            'financial_review_required': any(reason in ('ambiguous_reporting_scope', 'conflicting_statement_values') for reason in reasons.values()),
            'financial_comparability_json': json.dumps(comparison, sort_keys=True),
            '_candidate_ledger': ledger}
