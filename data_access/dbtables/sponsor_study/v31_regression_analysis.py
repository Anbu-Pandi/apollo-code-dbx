"""Reconciled V31 name, debt, and fundamentals regression at holding grain."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from data_access.dbtables.sponsor_study.company_aliases import _company_key
from data_access.dbtables.sponsor_study.factset_filing_attribution import admitted_dcs_attribution_rows
from data_access.dbtables.sponsor_study.fund_vendor import _match_key
from data_importer.sponsor_study.constants import STUDY_ASOF_DATE

LOGGER = logging.getLogger(__name__)

PUBLISHED_RUN_ID = "external_af47545add6c41dbaa9445462b7a59cb"
JAN15_CUTOFF = STUDY_ASOF_DATE
BENCHMARK_ROWS = 1837
PUBLISHED_NAMED = 1051
PROJECTED_NAMED = 1285
MANUAL_LEDGER_ROWS = 79

FUNNEL_STAGES = (
    "benchmark",
    "name_linked",
    "external_portco",
    "debt_reached",
    "fundamentals_reached",
    "leverage_pair",
)
METRIC_FLAGS = (
    "has_total_debt",
    "has_cash",
    "has_net_debt",
    "has_revenue",
    "has_ebitda",
    "has_gross_leverage",
    "has_net_leverage",
    "has_ltv",
    "has_tranche",
    "has_price_or_yield",
    "has_nearest_maturity",
)
METHOD_FAMILIES = (
    "exact_fund_date",
    "exact_company",
    "alias",
    "prefix_identity",
    "three_point",
    "name_gate",
    "reviewed",
    "unmatched",
    "other",
)
V12_BUCKETS = (
    "alias_matcher",
    "coverage_gap",
    "cross_sponsor",
    "bloomberg_issuer",
    "manual_review",
    "kept_or_new",
    "other",
)
NAME_DELTAS = ("kept", "gained", "unmatched", "changed_holding")
DEBT_PATHS = (
    "direct_identifier",
    "factset_entity_tree",
    "bloomberg_capital_stack",
    "bloomberg_issuer",
    "findox",
    "none",
)
SPONSOR_ALIASES = {
    "advent international": "Advent",
    "american securities": "AmSec",
    "apax partners": "Apax",
    "audax private equity": "Audax",
    "berkshire partners": "Berkshire",
    "cvc capital partners": "CVC",
    "clayton, dubilier & rice (cd&r)": "CD&R",
    "clearlake capital group": "Clearlake",
    "genstar capital": "Genstar",
    "hellman & friedman": "H&F",
    "new mountain capital": "New Mountain",
    "pai partners": "PAI",
    "providence equity partners": "Providence",
    "stone point capital": "Stone Point",
    "ta associates": "TA",
    "thomas h. lee partners (th lee)": "TH Lee",
    "warburg pincus": "Warburg",
}
_VEHICLE_PATTERNS = (
    ("holdco", re.compile(r"\bholdco\b|\bhold(?:ing)?s?\s*co(?:mpany)?\b", re.I)),
    ("bidco", re.compile(r"\bbidco\b|\bbid\s*co(?:mpany)?\b", re.I)),
    ("finco", re.compile(r"\bfinco\b|\bfin(?:ance)?\s*co(?:mpany)?\b", re.I)),
    ("midco", re.compile(r"\bmidco\b|\bmid\s*co(?:mpany)?\b", re.I)),
    ("newco", re.compile(r"\bnewco\b|\bnew\s*co(?:mpany)?\b", re.I)),
)
_HOLDING_LOOKUP_COLS = (
    "external_holding_id",
    "external_portco_id",
    "company_name",
    "factset_portco_entity_id",
    "holding_source_system",
    "sponsor_name",
    "source_confidence",
)


def canon_sponsor(value: object) -> str:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()
    return SPONSOR_ALIASES.get(text.casefold(), text)


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def match_method_family(method: object) -> str:
    text = _text(method)
    if not text or text == "benchmark_only":
        return "unmatched"
    if text in {"exact_sponsor_fund_company_date", "exact_sponsor_fund_company_deterministic"}:
        return "exact_fund_date"
    if text == "exact_sponsor_company":
        return "exact_company"
    if text == "exact_sponsor_company_alias":
        return "alias"
    if text == "prefix_sponsor_company_identity":
        return "prefix_identity"
    if text.startswith("three_point_auto"):
        return "three_point"
    if text == "name_gate_missing_confirmation":
        return "name_gate"
    if text == "reviewed_cross_sponsor_portco":
        return "reviewed"
    return "other"


def v12_cause_bucket(row: pd.Series) -> str:
    raw = _text(row.get("bucket"))
    if raw in {
        "alias_would_three_point",
        "alias_ok_missing_third_point",
        "same_sponsor_name_strict_scorer_killed",
        "strict_score_should_have_matched",
    }:
        return "alias_matcher"
    if raw in {"v12_name_gone_from_universe", "v12_name_moved_or_not_canonical"}:
        return "coverage_gap"
    if raw == "cross_sponsor_rejected":
        return "cross_sponsor"
    if raw == "bloomberg_issuer_not_a_holding":
        return "bloomberg_issuer"
    if raw in {"manual_issuer_review_not_ported", "manual_company_review_not_ported"}:
        return "manual_review"
    attr = _text(row.get("v12_attribution"))
    tier = _text(row.get("v12_tier"))
    method = _text(row.get("v12_method"))
    if attr == "cross_sponsor_company_exists":
        return "cross_sponsor"
    if tier == "tier7_issuer_master" or "bloomberg_issuer_master" in method:
        return "bloomberg_issuer"
    if tier in {"tier8_manual_company", "tier9_manual_issuer"}:
        return "manual_review"
    if raw:
        return "other"
    return "kept_or_new"


def vehicle_role(*, name: object = "", rank: object = 0, entity_relationship: object = "") -> str:
    text = _text(name)
    for role, pattern in _VEHICLE_PATTERNS:
        if pattern.search(text):
            return role
    rel = _text(entity_relationship)
    if rel in {"subsidiary_of_portco", "under_portco_ultimate"}:
        return "subsidiary"
    if rel in {"parent_of_portco", "ultimate_of_portco"}:
        return "holdco"
    try:
        depth = float(rank)
    except (TypeError, ValueError):
        depth = 0.0
    if pd.notna(depth) and depth > 0:
        return "borrower"
    return "canonical"


def name_delta(published_named: bool, projected_named: bool, holding_changed: bool) -> str:
    if published_named and projected_named:
        return "changed_holding" if holding_changed else "kept"
    if projected_named and not published_named:
        return "gained"
    return "unmatched"


def episode_key(frame: pd.DataFrame, sponsor_col: str, company_col: str, fund_col: str) -> pd.Series:
    vintage = pd.to_numeric(frame.get("vintage"), errors="coerce").fillna(-1).astype(int).astype(str)
    return (
        frame[sponsor_col].map(canon_sponsor).str.casefold()
        + "|"
        + frame[company_col].map(_company_key)
        + "|"
        + frame[fund_col].map(_match_key)
        + "|"
        + vintage
    )


def name_vintage_key(frame: pd.DataFrame, sponsor_col: str, company_col: str) -> pd.Series:
    vintage = pd.to_numeric(frame.get("vintage"), errors="coerce").fillna(-1).astype(int).astype(str)
    return (
        frame[sponsor_col].map(canon_sponsor).str.casefold()
        + "|"
        + frame[company_col].map(_company_key)
        + "|"
        + vintage
    )


def name_only_key(frame: pd.DataFrame, sponsor_col: str, company_col: str) -> pd.Series:
    return (
        frame[sponsor_col].map(canon_sponsor).str.casefold()
        + "|"
        + frame[company_col].map(_company_key)
    )


def _flag(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).ne("") & series.fillna("").astype(str).str.lower().ne("nan")


def _holding_lookup(holdings: pd.DataFrame) -> pd.DataFrame:
    cols = [col for col in _HOLDING_LOOKUP_COLS if col in holdings.columns]
    out = holdings[cols].drop_duplicates("external_holding_id").copy()
    out["external_holding_id"] = out["external_holding_id"].astype(str)
    return out.set_index("external_holding_id")


def _attach_holding(ids: pd.Series, lookup: pd.DataFrame, prefix: str) -> pd.DataFrame:
    keys = ids.fillna("").astype(str)
    missing = ~keys.isin(lookup.index) | keys.eq("")
    aligned = lookup.reindex(keys)
    frame = pd.DataFrame(index=ids.index)
    frame[f"{prefix}_external_portco_id"] = aligned["external_portco_id"].to_numpy()
    frame[f"{prefix}_external_company_name"] = aligned["company_name"].to_numpy()
    frame[f"{prefix}_factset_entity_id"] = aligned["factset_portco_entity_id"].to_numpy()
    frame[f"{prefix}_holding_source"] = aligned["holding_source_system"].to_numpy()
    frame.loc[missing.to_numpy(), :] = pd.NA
    return frame


def _attach_v12(spine: pd.DataFrame, v12_lost: pd.DataFrame) -> pd.DataFrame:
    out = spine.copy()
    out["v12_cause_bucket"] = "kept_or_new"
    out["v12_external_name"] = pd.NA
    out["v12_tier"] = pd.NA
    out["v12_source"] = pd.NA
    if v12_lost is None or v12_lost.empty:
        return out
    lost = v12_lost.copy()
    sponsor_col = "sponsor" if "sponsor" in lost.columns else "sponsor_name"
    lost["_nk"] = name_only_key(lost, sponsor_col, "company_name")
    lost["_nv"] = name_vintage_key(lost, sponsor_col, "company_name")
    if "fund_name" in lost.columns:
        lost["_ep"] = episode_key(lost, sponsor_col, "company_name", "fund_name")
    else:
        lost["_ep"] = lost["_nv"]
    lost["v12_cause_bucket"] = lost.apply(v12_cause_bucket, axis=1)
    out["_ep"] = episode_key(out, "v31_sponsor_name", "company_name", "fund_name")
    out["_nv"] = name_vintage_key(out, "v31_sponsor_name", "company_name")
    out["_nk"] = name_only_key(out, "v31_sponsor_name", "company_name")
    keep = [
        "v12_cause_bucket",
        "v12_external_name",
        "v12_tier",
        "v12_source",
        "_ep",
        "_nv",
        "_nk",
    ]
    for col in ("v12_external_name", "v12_tier", "v12_source"):
        if col not in lost.columns:
            lost[col] = pd.NA
    right = lost[keep].drop_duplicates("_ep", keep="first")
    merged = out.merge(right, on="_ep", how="left", suffixes=("", "_lost"))
    for col in ("v12_cause_bucket", "v12_external_name", "v12_tier", "v12_source"):
        fill = merged[f"{col}_lost"]
        missing = fill.notna()
        merged.loc[missing, col] = fill[missing]
        merged = merged.drop(columns=f"{col}_lost")
    for key in ("_nv", "_nk"):
        still = merged["v12_cause_bucket"].eq("kept_or_new")
        right = lost[keep].drop_duplicates(key, keep="first")
        fill = merged.loc[still, ["v31_holding_id", key]].merge(
            right.drop(columns=[c for c in ("_ep", "_nv", "_nk") if c != key]),
            on=key,
            how="left",
        )
        for col in ("v12_cause_bucket", "v12_external_name", "v12_tier", "v12_source"):
            merged.loc[still, col] = fill[col].to_numpy()
    merged["v12_cause_bucket"] = merged["v12_cause_bucket"].fillna("kept_or_new")
    return merged.drop(columns=["_ep", "_nv", "_nk"])


def _holding_host(spine: pd.DataFrame, key_col: str) -> pd.DataFrame:
    ranked = spine.assign(
        _rank=(
            (~spine["published_named"]).astype(int) * 4
            + (~spine["projected_named"]).astype(int) * 2
        )
    ).sort_values([key_col, "_rank"], ascending=[True, False])
    return ranked.drop_duplicates(key_col, keep="first")[["v31_holding_id", key_col]]


def _collapse_manual_hits(hits: pd.DataFrame) -> pd.DataFrame:
    if hits.empty:
        return pd.DataFrame(columns=[
            "v31_holding_id",
            "manual_review_count",
            "manual_accepted_flag",
            "manual_rejected_flag",
            "manual_review_scope",
            "manual_candidate_name",
            "manual_relationship_type",
            "manual_decision_ids",
        ])
    grouped = hits.groupby("v31_holding_id", dropna=False)
    return pd.DataFrame({
        "v31_holding_id": grouped.size().index,
        "manual_review_count": grouped.size().to_numpy(),
        "manual_accepted_flag": grouped["decision_status"].agg(lambda s: bool(s.eq("accepted").any())).to_numpy(),
        "manual_rejected_flag": grouped["decision_status"].agg(lambda s: bool(s.ne("accepted").any())).to_numpy(),
        "manual_review_scope": grouped["review_scope"].agg(
            lambda s: "|".join(sorted({_text(v) for v in s if _text(v)}))
        ).to_numpy(),
        "manual_candidate_name": grouped["candidate_entity_name"].agg(
            lambda s: next((_text(v) for v in s if _text(v)), "")
        ).to_numpy(),
        "manual_relationship_type": grouped["relationship_type"].agg(
            lambda s: next((_text(v) for v in s if _text(v)), "")
        ).to_numpy(),
        "manual_decision_ids": grouped["manual_entity_link_decision_id"].agg(
            lambda s: "|".join(sorted({_text(v) for v in s if _text(v)}))
        ).to_numpy(),
    })


def _attach_manual(spine: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    out = spine.copy()
    out["manual_review_count"] = 0
    out["manual_accepted_flag"] = False
    out["manual_rejected_flag"] = False
    out["manual_review_scope"] = pd.NA
    out["manual_candidate_name"] = pd.NA
    out["manual_relationship_type"] = pd.NA
    out["manual_decision_ids"] = pd.NA
    if reviews is None or reviews.empty:
        return out
    rev = reviews.copy()
    if "benchmark_sponsor_name" not in rev.columns:
        rev["benchmark_sponsor_name"] = pd.NA
    rev["_nk"] = name_only_key(rev, "benchmark_sponsor_name", "benchmark_company_name")
    rev["_ck"] = rev["benchmark_company_name"].map(_company_key)
    out["_nk"] = name_only_key(out, "v31_sponsor_name", "company_name")
    out["_ck"] = out["company_name"].map(_company_key)
    with_sponsor = rev[_flag(rev["benchmark_sponsor_name"])]
    no_sponsor = rev[~_flag(rev["benchmark_sponsor_name"])]
    hits = []
    if not with_sponsor.empty:
        hits.append(
            with_sponsor.merge(_holding_host(out, "_nk"), on="_nk", how="inner")
        )
    if not no_sponsor.empty:
        hits.append(
            no_sponsor.merge(_holding_host(out, "_ck"), on="_ck", how="inner")
        )
    if hits:
        collapsed = _collapse_manual_hits(pd.concat(hits, ignore_index=True))
        out = out.drop(columns=[
            "manual_review_count",
            "manual_accepted_flag",
            "manual_rejected_flag",
            "manual_review_scope",
            "manual_candidate_name",
            "manual_relationship_type",
            "manual_decision_ids",
        ])
        out = out.merge(collapsed, on="v31_holding_id", how="left")
        out["manual_review_count"] = out["manual_review_count"].fillna(0).astype(int)
        out["manual_accepted_flag"] = out["manual_accepted_flag"].fillna(False).astype(bool)
        out["manual_rejected_flag"] = out["manual_rejected_flag"].fillna(False).astype(bool)
        LOGGER.info("Joined manual reviews onto %s V31 holdings", int(out["manual_review_count"].gt(0).sum()))
    return out.drop(columns=["_nk", "_ck"], errors="ignore")


def build_name_spine(
    portcos: pd.DataFrame,
    published_xref: pd.DataFrame,
    projected_matched: pd.DataFrame,
    holdings: pd.DataFrame,
    v12_lost: pd.DataFrame | None = None,
    manual_reviews: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per V31 holding with published, projected, and manual overlays."""
    if portcos["v31_holding_id"].duplicated().any():
        raise ValueError("V31 portco frame is not unique on v31_holding_id")
    pub = published_xref.drop_duplicates("v31_holding_id", keep="first")
    proj = projected_matched.drop_duplicates("v31_holding_id", keep="first")
    spine = portcos.merge(
        pub[["v31_holding_id", "match_status", "match_method", "external_holding_id", "external_portco_id"]],
        on="v31_holding_id",
        how="left",
        suffixes=("", "_pub"),
    ).rename(columns={
        "match_status": "published_match_status",
        "match_method": "published_match_method",
        "external_holding_id": "published_external_holding_id",
        "external_portco_id": "published_xref_portco_id",
    })
    proj_keep = [col for col in (
        "v31_holding_id", "match_status", "match_method", "external_holding_id", "external_portco_id"
    ) if col in proj.columns]
    spine = spine.merge(proj[proj_keep], on="v31_holding_id", how="left").rename(columns={
        "match_status": "projected_match_status",
        "match_method": "projected_match_method",
        "external_holding_id": "projected_external_holding_id",
        "external_portco_id": "projected_xref_portco_id",
    })
    spine["published_named"] = spine["published_match_status"].eq("matched")
    spine["projected_named"] = spine["projected_match_status"].eq("matched")
    spine.loc[~spine["projected_named"], "projected_match_status"] = "benchmark_only"
    spine.loc[~spine["projected_named"], "projected_match_method"] = "benchmark_only"
    spine["published_method_family"] = spine["published_match_method"].map(match_method_family)
    spine["projected_method_family"] = spine["projected_match_method"].map(match_method_family)
    pub_id = spine["published_external_holding_id"].fillna("").astype(str)
    proj_id = spine["projected_external_holding_id"].fillna("").astype(str)
    spine["holding_changed_flag"] = (
        spine["published_named"]
        & spine["projected_named"]
        & pub_id.ne(proj_id)
        & pub_id.ne("")
        & proj_id.ne("")
    )
    lookup = _holding_lookup(holdings)
    spine = pd.concat(
        [
            spine,
            _attach_holding(spine["published_external_holding_id"], lookup, "published"),
            _attach_holding(spine["projected_external_holding_id"], lookup, "projected"),
        ],
        axis=1,
    )
    for prefix in ("published", "projected"):
        xref_col = f"{prefix}_xref_portco_id"
        hold_col = f"{prefix}_external_portco_id"
        if xref_col in spine.columns:
            spine[hold_col] = spine[hold_col].fillna(spine[xref_col])
    spine["name_delta"] = [
        name_delta(pub, proj, changed)
        for pub, proj, changed in zip(
            spine["published_named"],
            spine["projected_named"],
            spine["holding_changed_flag"],
        )
    ]
    spine = _attach_v12(spine, v12_lost if v12_lost is not None else pd.DataFrame())
    spine = _attach_manual(spine, manual_reviews if manual_reviews is not None else pd.DataFrame())
    spine["manual_counts_as_published"] = False
    return spine.reset_index(drop=True)


def prepare_external_holdings(holdings: pd.DataFrame, holding_ids: pd.Series) -> pd.DataFrame:
    ids = {str(v) for v in holding_ids.dropna().astype(str) if str(v).strip()}
    if not ids:
        return pd.DataFrame()
    out = holdings[holdings["external_holding_id"].astype(str).isin(ids)].copy()
    out["external_holding_key"] = out["external_holding_id"].astype(str)
    out["external_company_id"] = out["external_portco_id"].astype(str)
    out["company_key"] = out["company_name"].map(_company_key)
    out["source_system"] = out.get("holding_source_system", "factset_pevc")
    if "source_confidence" not in out.columns:
        out["source_confidence"] = "high"
    out["source_confidence"] = out["source_confidence"].fillna("high")
    return out.reset_index(drop=True)


def _present(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.notna().any():
        return values.notna()
    return _flag(frame[column])


def classify_debt_path(
    *,
    match_method: object,
    own_entity_id: object,
    financial_identifier: object,
    financial_source: object,
    stack_rank: object = 0,
) -> str:
    method = _text(match_method).casefold()
    source = _text(financial_source).casefold()
    own = _text(own_entity_id)
    ident = _text(financial_identifier)
    if "capital_stack" in method:
        return "bloomberg_capital_stack"
    if "bloomberg" in method:
        try:
            rank = float(stack_rank)
        except (TypeError, ValueError):
            rank = 0.0
        if pd.notna(rank) and rank > 0:
            return "bloomberg_capital_stack"
        return "bloomberg_issuer"
    if method == "factset_entity_tree" or "factset_entity_tree" in source:
        if own and ident and own == ident:
            return "direct_identifier"
        return "factset_entity_tree"
    if "findox" in method or source == "findox":
        return "findox"
    if own and ident and own == ident:
        return "direct_identifier"
    if method:
        return "factset_entity_tree" if "factset" in method else "none"
    return "none"


def coverage_from_enrichment(
    hydrated: pd.DataFrame,
    outputs: dict[str, pd.DataFrame],
    *,
    clock: str,
) -> pd.DataFrame:
    """Portco-grain coverage flags, paths, and metric completeness for one clock."""
    companies = hydrated.drop_duplicates("external_company_id").copy()
    if companies.empty:
        return pd.DataFrame(columns=["external_portco_id", "clock"])
    financial = outputs.get("smart_financial_snapshot", pd.DataFrame())
    instruments = outputs.get("smart_debt_instrument_snapshot", pd.DataFrame())
    market = outputs.get("smart_market_snapshot", pd.DataFrame())
    evidence = outputs.get("smart_debt_issuer_matches", pd.DataFrame())
    stack = outputs.get("smart_bloomberg_capital_stack", pd.DataFrame())
    dcs = admitted_dcs_attribution_rows(outputs.get("smart_factset_dcs_detail", pd.DataFrame()))
    fin = pd.DataFrame()
    if not financial.empty:
        fin = financial.rename(columns={"portco_id": "external_company_id"}).copy()
        fin = fin.sort_values(["external_company_id", "asof_date"]).drop_duplicates(
            "external_company_id", keep="last"
        )
    inst = pd.DataFrame()
    if not instruments.empty:
        inst = instruments.rename(columns={"portco_id": "external_company_id"}).copy()
    mkt = pd.DataFrame()
    if not market.empty:
        mkt = market.rename(columns={"portco_id": "external_company_id"}).copy()
    ev = pd.DataFrame()
    if not evidence.empty:
        ev = evidence.sort_values("match_status").drop_duplicates("external_company_id", keep="first")
    stack_seed = pd.DataFrame()
    if not stack.empty:
        admitted = stack[stack.get("stack_eligibility", "admitted").eq("admitted")] if "stack_eligibility" in stack else stack
        stack_seed = admitted.sort_values("capital_stack_rank").drop_duplicates(
            "external_company_id", keep="first"
        )
    rows = []
    for rec in companies.itertuples(index=False):
        cid = str(rec.external_company_id)
        fin_row = fin[fin["external_company_id"].astype(str).eq(cid)] if not fin.empty else fin
        inst_rows = inst[inst["external_company_id"].astype(str).eq(cid)] if not inst.empty else inst
        mkt_rows = mkt[mkt["external_company_id"].astype(str).eq(cid)] if not mkt.empty else mkt
        ev_row = ev[ev["external_company_id"].astype(str).eq(cid)] if not ev.empty else ev
        st_row = stack_seed[stack_seed["external_company_id"].astype(str).eq(cid)] if not stack_seed.empty else stack_seed
        fin_one = fin_row.iloc[0] if len(fin_row) else pd.Series(dtype="object")
        ev_one = ev_row.iloc[0] if len(ev_row) else pd.Series(dtype="object")
        st_one = st_row.iloc[0] if len(st_row) else pd.Series(dtype="object")
        has_total_debt = bool(_present(fin_row, "current_total_debt").any()) if len(fin_row) else False
        has_cash = bool(_present(fin_row, "current_total_cash").any()) if len(fin_row) else False
        has_net_debt = bool(_present(fin_row, "current_net_debt").any()) if len(fin_row) else False
        has_revenue = bool(_present(fin_row, "current_ltm_revenue").any()) if len(fin_row) else False
        has_ebitda = bool(_present(fin_row, "current_ltm_ebitda").any()) if len(fin_row) else False
        has_gross = bool(_present(fin_row, "current_gross_leverage").any()) if len(fin_row) else False
        has_net_lev = bool(_present(fin_row, "current_net_leverage").any()) if len(fin_row) else False
        has_ltv = bool(_present(fin_row, "ltv").any()) if len(fin_row) else False
        has_tranche = bool(_present(inst_rows, "tranche_size").any()) if len(inst_rows) else False
        has_price = False
        if len(mkt_rows):
            has_price = bool(
                _present(mkt_rows, "price").any()
                | _present(mkt_rows, "ytw").any()
                | _present(mkt_rows, "ytm").any()
            )
        if not has_price and len(hydrated):
            hold = hydrated[hydrated["external_company_id"].astype(str).eq(cid)]
            has_price = bool(
                _present(hold, "current_first_lien_price").any()
                | _present(hold, "current_first_lien_ytw").any()
            )
        has_maturity = False
        nearest = pd.NaT
        if len(inst_rows) and "nearest_maturity_date" in inst_rows:
            nearest = pd.to_datetime(inst_rows["nearest_maturity_date"], errors="coerce").min()
            has_maturity = pd.notna(nearest)
        node_name = _text(st_one.get("capital_stack_name")) or _text(ev_one.get("matched_issuer_label"))
        rank = st_one.get("capital_stack_rank", 0)
        rel = ""
        if not dcs.empty and "dcs_entity_relationship" in dcs.columns:
            dcs_rows = dcs[dcs.get("portco_id", pd.Series(dtype="object")).astype(str).eq(cid)]
            if len(dcs_rows):
                rel = _text(dcs_rows.iloc[0].get("dcs_entity_relationship"))
        path = classify_debt_path(
            match_method=ev_one.get("match_method"),
            own_entity_id=getattr(rec, "factset_portco_entity_id", None),
            financial_identifier=fin_one.get("financial_identifier"),
            financial_source=fin_one.get("source_system"),
            stack_rank=rank,
        )
        debt_reached = has_tranche or has_price or has_maturity or (
            has_total_debt and _text(fin_one.get("source_system")) != "findox"
        )
        if path in {"direct_identifier", "factset_entity_tree", "bloomberg_capital_stack", "bloomberg_issuer"}:
            debt_reached = True
        fundamentals_reached = has_revenue or has_ebitda or has_total_debt or has_cash
        if fundamentals_reached and path == "none":
            path = "findox" if _text(fin_one.get("source_system")) == "findox" else path
        asof = fin_one.get("asof_date")
        rows.append({
            "external_portco_id": cid,
            "clock": clock,
            "company_name": _text(getattr(rec, "company_name", "")),
            "factset_entity_id": _text(getattr(rec, "factset_portco_entity_id", "")),
            "debt_reached": bool(debt_reached),
            "fundamentals_reached": bool(fundamentals_reached),
            "leverage_pair": bool(has_ebitda and has_total_debt),
            "debt_path": path,
            "fundamental_source": _text(fin_one.get("source_system")) or "none",
            "debt_node_name": node_name,
            "debt_node_role": vehicle_role(name=node_name, rank=rank, entity_relationship=rel),
            "asof_date": asof,
            "has_total_debt": has_total_debt,
            "has_cash": has_cash,
            "has_net_debt": has_net_debt,
            "has_revenue": has_revenue,
            "has_ebitda": has_ebitda,
            "has_gross_leverage": has_gross,
            "has_net_leverage": has_net_lev,
            "has_ltv": has_ltv,
            "has_tranche": has_tranche,
            "has_price_or_yield": has_price,
            "has_nearest_maturity": has_maturity,
            "nearest_maturity_date": nearest,
            "current_total_debt": fin_one.get("current_total_debt"),
            "current_total_cash": fin_one.get("current_total_cash"),
            "current_net_debt": fin_one.get("current_net_debt"),
            "current_ltm_revenue": fin_one.get("current_ltm_revenue"),
            "current_ltm_ebitda": fin_one.get("current_ltm_ebitda"),
            "current_gross_leverage": fin_one.get("current_gross_leverage"),
            "current_net_leverage": fin_one.get("current_net_leverage"),
            "ltv": fin_one.get("ltv"),
        })
    out = pd.DataFrame(rows)
    if out["external_portco_id"].duplicated().any():
        raise ValueError("Coverage frame is not unique on external_portco_id")
    return out


def entity_tree_nodes(outputs: dict[str, pd.DataFrame], *, clock: str) -> pd.DataFrame:
    """Diagnostic node list. Metrics stay on the selected filing only."""
    frames = []
    dcs = outputs.get("smart_factset_dcs_detail", pd.DataFrame())
    if not dcs.empty:
        detail = dcs.copy()
        detail["clock"] = clock
        detail["node_kind"] = "factset_entity_tree"
        detail["node_id"] = detail.get("dcs_filer_entity_id", pd.NA)
        detail["node_role"] = [
            vehicle_role(entity_relationship=rel)
            for rel in detail.get("dcs_entity_relationship", pd.Series("", index=detail.index))
        ]
        if "dcs_entity_relationship" in detail.columns:
            detail["_own"] = detail["dcs_entity_relationship"].eq("portco_entity")
        else:
            detail["_own"] = True
        detail["_size"] = pd.to_numeric(detail.get("amount_outstanding"), errors="coerce").fillna(0.0).abs()
        chosen = (
            admitted_dcs_attribution_rows(detail).sort_values(
                ["portco_id", "_own", "_size"], ascending=[True, False, False])
            .drop_duplicates("portco_id", keep="first")
        )
        detail["selected_for_metrics"] = False
        detail.loc[chosen.index, "selected_for_metrics"] = True
        keep_cols = [col for col in (
            "clock", "portco_id", "node_id", "node_kind", "node_role",
            "selected_for_metrics", "dcs_filer_entity_id", "dcs_entity_relationship",
            "dcs_attribution_status", "dcs_attribution_reason", "dcs_relationship_path",
            "amount_outstanding",
        ) if col in detail.columns]
        frames.append(detail[keep_cols])
    stack = outputs.get("smart_bloomberg_capital_stack", pd.DataFrame())
    if not stack.empty:
        nodes = stack.copy()
        if "stack_eligibility" in nodes:
            nodes = nodes[nodes["stack_eligibility"].eq("admitted")].copy()
        nodes["clock"] = clock
        nodes["node_kind"] = "bloomberg_capital_stack"
        nodes["portco_id"] = nodes["external_company_id"]
        nodes["node_id"] = nodes.get("capital_stack_bbg_company_id", pd.NA)
        nodes["node_role"] = [
            vehicle_role(name=name, rank=rank)
            for name, rank in zip(
                nodes.get("capital_stack_name", pd.Series("", index=nodes.index)),
                nodes.get("capital_stack_rank", pd.Series(0, index=nodes.index)),
            )
        ]
        rank = pd.to_numeric(nodes.get("capital_stack_rank"), errors="coerce").fillna(99)
        nodes = nodes[rank.le(5)].copy()
        rank = pd.to_numeric(nodes.get("capital_stack_rank"), errors="coerce").fillna(99)
        nodes["selected_for_metrics"] = False
        seeds = nodes.loc[rank.eq(0)].drop_duplicates("portco_id", keep="first")
        nodes.loc[seeds.index, "selected_for_metrics"] = True
        keep_cols = [col for col in (
            "clock", "portco_id", "node_id", "node_kind", "node_role",
            "selected_for_metrics", "capital_stack_name", "capital_stack_rank",
            "capital_stack_relationship", "stack_eligibility",
        ) if col in nodes.columns]
        frames.append(nodes[keep_cols])
    if not frames:
        return pd.DataFrame(columns=[
            "clock", "portco_id", "node_id", "node_kind", "node_role", "selected_for_metrics",
        ])
    return pd.concat(frames, ignore_index=True, sort=False)


def _coverage_lookup(coverage: pd.DataFrame) -> pd.DataFrame:
    if coverage.empty:
        return coverage
    return coverage.drop_duplicates("external_portco_id").set_index("external_portco_id")


def _map_coverage(ids: pd.Series, lookup: pd.DataFrame, prefix: str) -> pd.DataFrame:
    frame = pd.DataFrame(index=ids.index)
    empty = lookup.empty
    keys = ids.fillna("").astype(str)
    for col in (
        "debt_reached",
        "fundamentals_reached",
        "leverage_pair",
        "debt_path",
        "fundamental_source",
        "debt_node_name",
        "debt_node_role",
        *METRIC_FLAGS,
    ):
        if empty or col not in lookup.columns:
            if col in {"debt_reached", "fundamentals_reached", "leverage_pair", *METRIC_FLAGS}:
                frame[f"{prefix}_{col}"] = False
            elif col in {"debt_path", "fundamental_source"}:
                frame[f"{prefix}_{col}"] = "none"
            else:
                frame[f"{prefix}_{col}"] = pd.NA
            continue
        mapped = lookup[col].reindex(keys)
        if col in {"debt_reached", "fundamentals_reached", "leverage_pair", *METRIC_FLAGS}:
            frame[f"{prefix}_{col}"] = mapped.fillna(False).astype(bool).to_numpy()
        else:
            frame[f"{prefix}_{col}"] = mapped.to_numpy()
        frame.loc[keys.eq(""), f"{prefix}_{col}"] = (
            False if col in {"debt_reached", "fundamentals_reached", "leverage_pair", *METRIC_FLAGS} else (
                "none" if col in {"debt_path", "fundamental_source"} else pd.NA
            )
        )
    return frame


def join_coverage(
    spine: pd.DataFrame,
    jan15: pd.DataFrame,
    live: pd.DataFrame,
) -> pd.DataFrame:
    out = spine.copy()
    jan_lookup = _coverage_lookup(jan15)
    live_lookup = _coverage_lookup(live)
    parts = [
        _map_coverage(out["published_external_portco_id"], jan_lookup, "published_jan15"),
        _map_coverage(out["projected_external_portco_id"], jan_lookup, "projected_jan15"),
        _map_coverage(out["published_external_portco_id"], live_lookup, "published_live"),
        _map_coverage(out["projected_external_portco_id"], live_lookup, "projected_live"),
    ]
    return pd.concat([out, *parts], axis=1)


def funnel_table(spine: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for matcher, named_col, portco_col in (
        ("published", "published_named", "published_external_portco_id"),
        ("projected", "projected_named", "projected_external_portco_id"),
    ):
        named = spine[named_col].astype(bool)
        has_portco = named & _flag(spine[portco_col])
        for clock in ("jan15", "live"):
            debt = spine[f"{matcher}_{clock}_debt_reached"].astype(bool)
            funds = spine[f"{matcher}_{clock}_fundamentals_reached"].astype(bool)
            pair = spine[f"{matcher}_{clock}_leverage_pair"].astype(bool)
            counts = {
                "benchmark": int(len(spine)),
                "name_linked": int(named.sum()),
                "external_portco": int(has_portco.sum()),
                "debt_reached": int((has_portco & debt).sum()),
                "fundamentals_reached": int((has_portco & funds).sum()),
                "leverage_pair": int((has_portco & pair).sum()),
            }
            for stage, n in counts.items():
                rows.append({
                    "matcher": matcher,
                    "clock": clock,
                    "stage": stage,
                    "n": n,
                    "rate": round(n / max(len(spine), 1), 4),
                })
    return pd.DataFrame(rows)


def metric_completeness_table(spine: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for matcher, named_col in (
        ("published", "published_named"),
        ("projected", "projected_named"),
    ):
        named = spine[named_col].astype(bool)
        denom = int(named.sum())
        for clock in ("jan15", "live"):
            for metric in METRIC_FLAGS:
                col = f"{matcher}_{clock}_{metric}"
                n = int((named & spine[col].astype(bool)).sum()) if col in spine else 0
                rows.append({
                    "matcher": matcher,
                    "clock": clock,
                    "metric": metric,
                    "n": n,
                    "named": denom,
                    "rate": round(n / max(denom, 1), 4),
                })
    return pd.DataFrame(rows)


def assert_unique_holding_grain(spine: pd.DataFrame) -> None:
    if len(spine) != spine["v31_holding_id"].nunique():
        raise AssertionError("V31 holding grain is not unique")


def assert_name_spine(
    spine: pd.DataFrame,
    *,
    expected_rows: int = BENCHMARK_ROWS,
    expected_published: int = PUBLISHED_NAMED,
    expected_projected: int = PROJECTED_NAMED,
    expected_manual: int | None = None,
    manual_reviews: pd.DataFrame | None = None,
) -> None:
    assert_unique_holding_grain(spine)
    if len(spine) != expected_rows:
        raise AssertionError(f"Expected {expected_rows} V31 rows, got {len(spine)}")
    published = int(spine["published_named"].sum())
    projected = int(spine["projected_named"].sum())
    if published != expected_published:
        raise AssertionError(f"Expected {expected_published} published names, got {published}")
    if projected != expected_projected:
        raise AssertionError(f"Expected {expected_projected} projected names, got {projected}")
    if bool(spine["manual_counts_as_published"].any()):
        raise AssertionError("Manual reviews must not count as published matches")
    if expected_manual is not None and manual_reviews is not None:
        n = int(manual_reviews["manual_entity_link_decision_id"].nunique())
        if n != expected_manual:
            raise AssertionError(f"Expected {expected_manual} manual decisions, got {n}")


def assert_no_post_cutoff(frame: pd.DataFrame, *, cutoff: str = JAN15_CUTOFF) -> None:
    limit = pd.Timestamp(cutoff).normalize()
    for col in ("asof_date", "period_end_date"):
        if col not in frame.columns:
            continue
        dates = pd.to_datetime(frame[col], errors="coerce")
        late = dates.notna() & dates.dt.normalize().gt(limit)
        if bool(late.any()):
            raise AssertionError(f"{col} has {int(late.sum())} rows after {cutoff}")


def assert_no_entity_tree_double_count(nodes: pd.DataFrame) -> None:
    if nodes is None or nodes.empty or "selected_for_metrics" not in nodes.columns:
        return
    selected = nodes[nodes["selected_for_metrics"].astype(bool)]
    if selected.empty:
        return
    keys = ["clock", "portco_id"]
    if "node_kind" in selected.columns:
        keys = ["clock", "node_kind", "portco_id"]
    if "node_id" in selected.columns:
        dupes = selected.groupby(keys, dropna=False)["node_id"].nunique()
    else:
        dupes = selected.groupby(keys, dropna=False).size()
    if bool(dupes.gt(1).any()):
        raise AssertionError("Multiple entity-tree nodes selected for the same portco metrics")


def json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def build_summary(
    spine: pd.DataFrame,
    funnel: pd.DataFrame,
    metrics: pd.DataFrame,
    *,
    manual_reviews: pd.DataFrame | None = None,
) -> dict[str, object]:
    published_methods = (
        spine.loc[spine["published_named"], "published_method_family"].value_counts().to_dict()
    )
    projected_methods = (
        spine.loc[spine["projected_named"], "projected_method_family"].value_counts().to_dict()
    )
    manual_n = 0 if manual_reviews is None or manual_reviews.empty else int(
        manual_reviews["manual_entity_link_decision_id"].nunique()
    )
    accepted_unmatched = int(
        (~spine["published_named"] & spine["manual_accepted_flag"]).sum()
    )
    return json_ready({
        "refresh_run_id": PUBLISHED_RUN_ID,
        "benchmark_rows": int(len(spine)),
        "published_named": int(spine["published_named"].sum()),
        "projected_named": int(spine["projected_named"].sum()),
        "projected_gained": int(spine["name_delta"].eq("gained").sum()),
        "holding_changed": int(spine["holding_changed_flag"].sum()),
        "manual_ledger_rows": manual_n,
        "manual_accepted_on_unpublished": accepted_unmatched,
        "manual_counts_as_published": False,
        "published_method_families": published_methods,
        "projected_method_families": projected_methods,
        "name_delta": spine["name_delta"].value_counts().to_dict(),
        "v12_cause_on_unpublished": (
            spine.loc[~spine["published_named"], "v12_cause_bucket"].value_counts().to_dict()
        ),
        "funnel": funnel.to_dict(orient="records"),
        "metric_completeness": metrics.to_dict(orient="records"),
    })


def enrich_matched_companies(
    external: pd.DataFrame,
    *,
    clock: str,
    app: str,
    table_name: str,
    asof_cutoff: str | pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.smart_comparison import (
        fetch_and_enrich_external_holdings,
    )
    if external.empty:
        empty = pd.DataFrame(columns=["external_portco_id", "clock"])
        return external, {}, empty, entity_tree_nodes({}, clock=clock)
    hydrated, outputs = fetch_and_enrich_external_holdings(
        external,
        app=app,
        table_name=table_name,
        asof_cutoff=asof_cutoff,
    )
    coverage = coverage_from_enrichment(hydrated, outputs, clock=clock)
    nodes = entity_tree_nodes(outputs, clock=clock)
    return hydrated, outputs, coverage, nodes


def write_diagnostics(
    *,
    out_dir: Path,
    spine: pd.DataFrame,
    funnel: pd.DataFrame,
    metrics: pd.DataFrame,
    summary: dict[str, object],
    nodes: pd.DataFrame | None = None,
    manual_reviews: pd.DataFrame | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    spine.to_csv(out_dir / "v31_comprehensive_regression_spine.csv", index=False)
    funnel.to_csv(out_dir / "v31_comprehensive_regression_funnel.csv", index=False)
    metrics.to_csv(out_dir / "v31_comprehensive_regression_metrics.csv", index=False)
    (out_dir / "v31_comprehensive_regression_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    if nodes is not None:
        nodes.to_csv(out_dir / "v31_comprehensive_regression_entity_nodes.csv", index=False)
    if manual_reviews is not None:
        manual_reviews.to_csv(
            out_dir / "v31_comprehensive_regression_manual.csv", index=False
        )
