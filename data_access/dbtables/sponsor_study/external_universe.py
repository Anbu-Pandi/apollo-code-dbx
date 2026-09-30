"""External-only PE sponsor study universe: sponsors, funds, holdings, and crosswalks."""

from __future__ import annotations

import logging
from time import perf_counter

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed
from data_access.dbtables.sponsor_study.website_company_classification import accepted_company_candidates

from common.utils.memutil import get_process_memory_gb
from data_access.dbtables.sponsor_study.canonical import slug, stable_id
from data_access.dbtables.sponsor_study.external_identity import (
    AUTO_CANDIDATE_MARGIN_MIN,
    AUTO_SINGLE_CORE_MARGIN_MIN,
    CompanySourceObservation,
    evaluate_company_identity,
    normalized_company_domain,
    normalized_company_name,
    score_company_identity_name,
)
from data_access.dbtables.sponsor_study.external_identity_feedback import (
    DIRECT_ENTITY_DECISION_METHOD,
)
from data_access.dbtables.sponsor_study.fund_vendor import (
    _factset_pevc_funds,
    _match_key,
    _ratio,
    _tokens,
)
from data_access.dbtables.sponsor_study.source_priority import (
    holding_source_priority,
)
from data_access.dbtables.sponsor_study.sponsor_identity import sponsor_family_key, same_sponsor_family
from data_access.dbtables.sponsor_study.website_sources import (
    WEBSITE_HOLDING_COLUMNS,
    WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
    _company_keys,
    _company_match_score,
    _coerce_investment_date,
    _fund_key,
    _fund_match_score,
    _normalize_confidence,
    _normalize_status,
)

WEBSITE_FUND_ASSIGNMENT_RULE_VERSION = "website_fund_assignment_v1"
EXTERNAL_WEBSITE_HOLDING_AUDIT_COLUMNS = [
    "source_fund_name",
    "source_investment_year",
    "fund_assignment_score",
    "fund_assignment_margin",
    "fund_assignment_rationale",
    "fund_assignment_rule_version",
]
EXTERNAL_WEBSITE_HOLDING_COLUMNS = [
    *WEBSITE_HOLDING_COLUMNS,
    *EXTERNAL_WEBSITE_HOLDING_AUDIT_COLUMNS,
]
EXTERNAL_WEBSITE_HOLDING_DELTA_SCHEMA_TYPES = {
    **WEBSITE_HOLDING_DELTA_SCHEMA_TYPES,
    "source_investment_year": "BIGINT",
    "fund_assignment_score": "DOUBLE",
    "fund_assignment_margin": "DOUBLE",
}


EXTERNAL_SPONSOR_SCOPE_COLUMNS = [
    "external_sponsor_id",
    "sponsor_name",
    "sponsor_slug",
    "website",
    "portfolio_page",
    "source_file",
    "source_sheet",
    "source_excel_row",
    "preqin_firm_id",
    "factset_gp_entity_id",
    "sponsor_match_method",
    "sponsor_match_score",
    "sponsor_match_confidence",
    "sponsor_review_status",
]

DIM_EXTERNAL_FUND_COLUMNS = [
    "external_fund_id",
    "external_sponsor_id",
    "sponsor_name",
    "fund_name",
    "fund_slug",
    "vintage",
    "fund_strategy",
    "fund_status",
    "preqin_fund_id",
    "preqin_fund_source_table",
    "preqin_firm_id",
    "factset_fund_entity_id",
    "factset_gp_entity_id",
    "commitment_usd_m",
    "as_of_date",
    "pct_called",
    "dpi",
    "rvpi",
    "net_moic",
    "net_irr",
    "flagship_flag",
    "flagship_selection_rationale",
    "source_system",
]

XREF_EXTERNAL_FUND_SOURCE_COLUMNS = [
    "external_fund_xref_id",
    "external_fund_id",
    "external_sponsor_id",
    "source_system",
    "source_fund_id",
    "source_fund_source_table",
    "source_sponsor_id",
    "source_fund_name",
    "source_sponsor_name",
    "source_vintage",
    "match_method",
    "match_score",
    "match_confidence",
    "review_status",
]

EXTERNAL_PORTCO_FACTSET_XREF_COLUMNS = [
    "external_portco_id",
    "company_name",
    "company_slug",
    "external_sponsor_id",
    "factset_portco_entity_id",
    "match_method",
    "match_confidence",
    "review_status",
]

FACT_EXTERNAL_HOLDING_COLUMNS = [
    "external_holding_id",
    "external_sponsor_id",
    "external_fund_id",
    "external_portco_id",
    "sponsor_name",
    "fund_name",
    "vintage",
    "company_name",
    "company_slug",
    "company_country",
    "industry",
    "investment_date",
    "exit_date",
    "exit_is_full",
    "exit_evidence",
    "entry_type",
    "transaction_close_date",
    "round_of_funding",
    "total_investment_amount",
    "investment_description",
    "amount_invested_by_sponsor",
    "factset_gp_entity_id",
    "factset_fund_entity_id",
    "factset_portco_entity_id",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "holding_source_system",
    "source_holding_id",
    "source_company_id",
    "source_confidence",
    "fund_assignment_method",
    "fund_assignment_confidence",
    "fund_assignment_is_assumed",
    "fund_assignment_rationale",
    "match_method",
    "holding_review_status",
    "contributing_source_systems",
    "canonical_holding_flag",
    "company_url",
    "external_company_url",
    "company_description",
    "evidence_text",
    "source_url",
    "final_url",
    "crawl_run_id",
    "knowledge_datetime",
    "source_fund_name",
    "source_investment_year",
    "fund_assignment_score",
    "fund_assignment_margin",
    "fund_assignment_rule_version",
]

NON_FLAGSHIP_FUND_HINTS = (
    "infrastructure",
    "infra",
    "credit",
    "continuation",
    "co-invest",
    "coinvest",
    "co invest",
    "sidecar",
    "secondaries",
    "special situations",
    "venture",
    "mezzanine",
    "distressed",
    "opportunistic",
    "real assets",
    "natural resources",
    "renewable",
    "growth equity",
    "tactical",
    "credit opportunities",
)

FLAGSHIP_NAME_BOOST_TOKENS = {"buyout", "private", "equity", "pe", "partners", "capital"}


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _clean_id(value) -> str:
    if isinstance(value, bool):
        text = str(value).strip()
        return (
            ""
            if text.lower() in {"", "nan", "none", "na", "n/a"}
            else text
        )
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    numeric = pd.to_numeric(
        pd.Series([value], dtype="object"),
        errors="coerce",
    ).iloc[0]
    if pd.notna(numeric) and float(numeric).is_integer():
        return str(int(numeric))
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _numeric_year(value):
    year = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.NA if pd.isna(year) else int(year)


def _sponsor_match_score(seed_name, vendor_name) -> float:
    return float(same_sponsor_family(_clean_id(seed_name), _clean_id(vendor_name)))


def _is_qualifying_pe_fund(fund_name, fund_strategy, asset_class) -> bool:
    text = f"{fund_name or ''} {fund_strategy or ''}".lower()
    if any(hint in text for hint in NON_FLAGSHIP_FUND_HINTS):
        return False
    asset = str(asset_class or "").strip().lower()
    if asset and asset not in {"private_equity", "pe"}:
        return False
    strategy = str(fund_strategy or "").strip().lower()
    if strategy and strategy not in {"buyout", "balanced", "growth", ""}:
        if strategy not in {"buyout", "balanced", "growth"}:
            return False
    return True


def _flagship_name_score(fund_name) -> float:
    tokens = set(_fund_key(fund_name).split("_"))
    overlap = len(tokens & FLAGSHIP_NAME_BOOST_TOKENS)
    base = _ratio(fund_name, "private equity buyout fund")
    return base + (0.05 * overlap)


def build_external_sponsor_scope(sponsor_website_list: pd.DataFrame | None) -> pd.DataFrame:
    if sponsor_website_list is None or sponsor_website_list.empty:
        return _empty(EXTERNAL_SPONSOR_SCOPE_COLUMNS)
    out = sponsor_website_list.copy()
    out["external_sponsor_id"] = out["sponsor"].map(lambda value: stable_id("ext_sponsor", value))
    out["sponsor_name"] = out["sponsor"]
    out["sponsor_slug"] = out["sponsor_name"].map(slug)
    for col in EXTERNAL_SPONSOR_SCOPE_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    out["sponsor_match_method"] = "curated_website_seed"
    out["sponsor_match_score"] = 1.0
    out["sponsor_match_confidence"] = "high"
    out["sponsor_review_status"] = "accepted_seed"
    return out[EXTERNAL_SPONSOR_SCOPE_COLUMNS].drop_duplicates("external_sponsor_id").reset_index(drop=True)


def _attach_sponsor_source_ids(scope: pd.DataFrame, vendors: pd.DataFrame, *, column: str) -> pd.DataFrame:
    if scope.empty or vendors.empty:
        return scope
    identities = vendors[["source_sponsor_id", "sponsor_name"]].dropna().drop_duplicates().copy()
    identities["source_sponsor_id"] = identities.source_sponsor_id.map(_clean_id)
    identities["sponsor_family_key"] = identities.sponsor_name.map(sponsor_family_key)
    ids = identities.groupby("sponsor_family_key").source_sponsor_id.agg(lambda values: tuple(sorted(set(values))))
    out = scope.copy()
    for index, row in out.iterrows():
        if pd.notna(row.get(column)):
            continue  # An explicit source ID supplied in the seed is authoritative.
        candidates = ids.get(sponsor_family_key(row.sponsor_name), ())
        # A name family can span multiple vendor GP entities. Leave the scalar
        # crosswalk unresolved rather than choosing the first vendor row.
        out.loc[index, column] = candidates[0] if len(candidates) == 1 else pd.NA
        if len(candidates) == 1:
            out.loc[index, "sponsor_match_method"] = "exact_sponsor_family"
            out.loc[index, "sponsor_match_score"] = 1.0
            out.loc[index, "sponsor_match_confidence"] = "high"
            out.loc[index, "sponsor_review_status"] = "auto_matched"
        elif candidates:
            out.loc[index, "sponsor_review_status"] = "needs_source_id_review"
    return out


def _match_sponsors_to_preqin(scope: pd.DataFrame, preqin_funds: pd.DataFrame) -> pd.DataFrame:
    return _attach_sponsor_source_ids(scope, preqin_funds, column="preqin_firm_id")


def _match_sponsors_to_factset_gp(scope: pd.DataFrame, factset_funds: pd.DataFrame) -> pd.DataFrame:
    return _attach_sponsor_source_ids(scope, factset_funds, column="factset_gp_entity_id")


def enrich_external_sponsor_scope(
    scope: pd.DataFrame,
    *,
    preqin_funds: pd.DataFrame | None = None,
    factset_funds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    preqin = preqin_funds if preqin_funds is not None else pd.DataFrame()
    factset = factset_funds if factset_funds is not None else pd.DataFrame()
    if (
        not preqin.empty
        and "source_sponsor_id" not in preqin.columns
        and "preqin_firm_id" in preqin.columns
    ):
        preqin = preqin.rename(columns={"preqin_firm_id": "source_sponsor_id", "sponsor_name_preqin": "sponsor_name"})
    if not factset.empty and "source_sponsor_id" not in factset.columns and "factset_gp_entity_id" in factset.columns:
        factset = _normalize_factset_fund_vendor_frame(factset)
    out = _match_sponsors_to_preqin(scope, preqin)
    return _match_sponsors_to_factset_gp(out, factset)


def build_dim_external_fund(
    sponsor_scope: pd.DataFrame,
    preqin_fund_summary: pd.DataFrame | None,
) -> pd.DataFrame:
    if sponsor_scope.empty or preqin_fund_summary is None or preqin_fund_summary.empty:
        return _empty(DIM_EXTERNAL_FUND_COLUMNS)

    preqin = preqin_fund_summary.copy()
    preqin.columns = [str(col).lower() for col in preqin.columns]
    if "preqin_asset_class" not in preqin.columns:
        strategy = preqin.get("fund_strategy", pd.Series("", index=preqin.index)).astype("string").str.lower()
        preqin["preqin_asset_class"] = strategy.map(
            lambda value: "private_equity" if str(value or "") in {"buyout", "balanced", "growth"} else "other"
        )
    pe = preqin[preqin["preqin_asset_class"].astype("string").str.lower().eq("private_equity")].copy()
    if pe.empty:
        return _empty(DIM_EXTERNAL_FUND_COLUMNS)

    sponsor_lookup = _external_sponsor_lookup(sponsor_scope)
    seed_ids = set(sponsor_scope.external_sponsor_id)
    family_matches = {name: _match_external_sponsor_id(name, sponsor_lookup)[0]
                      for name in pe.sponsor_name_preqin.dropna().unique()}
    pe = pe[pe.sponsor_name_preqin.map(family_matches).isin(seed_ids)]

    if pe.empty:
        return _empty(DIM_EXTERNAL_FUND_COLUMNS)

    pe = pe[pe.apply(
        lambda row: _is_qualifying_pe_fund(
            row.get("fund_name_preqin"),
            row.get("fund_strategy"),
            row.get("preqin_asset_class"),
        ),
        axis=1,
    )]

    sponsor_by_firm = dict(zip(
        sponsor_scope["preqin_firm_id"].astype("string"),
        sponsor_scope["external_sponsor_id"],
    ))
    sponsor_names_by_id = sponsor_scope.set_index("external_sponsor_id").sponsor_name.to_dict()

    rows = []
    for _, fund in pe.iterrows():
        firm_id = _clean_id(fund.get("preqin_firm_id"))
        source_table = _clean_id(fund.get("preqin_fund_source_table")) or "FUND_PE"
        fund_id = _clean_id(fund.get("preqin_fund_id"))
        if not fund_id:
            continue
        external_fund_id = stable_id("ext_fund", source_table, fund_id)
        external_sponsor_id = sponsor_by_firm.get(firm_id)
        if not external_sponsor_id:
            sponsor_name = fund.get("sponsor_name_preqin")
            external_sponsor_id = family_matches[sponsor_name]
        if not same_sponsor_family(sponsor_names_by_id[external_sponsor_id], fund["sponsor_name_preqin"]):
            raise ValueError(f"Preqin firm crosswalk changes sponsor identity: {firm_id}")
        rows.append({
            "external_fund_id": external_fund_id,
            "external_sponsor_id": external_sponsor_id,
            "sponsor_name": sponsor_names_by_id[external_sponsor_id],
            "fund_name": fund.get("fund_name_preqin"),
            "fund_slug": slug(fund.get("fund_name_preqin")),
            "vintage": _numeric_year(fund.get("vintage")),
            "fund_strategy": fund.get("fund_strategy"),
            "fund_status": fund.get("fund_status"),
            "preqin_fund_id": fund_id,
            "preqin_fund_source_table": source_table,
            "preqin_firm_id": firm_id or pd.NA,
            "factset_fund_entity_id": pd.NA,
            "factset_gp_entity_id": pd.NA,
            "commitment_usd_m": pd.to_numeric(fund.get("commitment_usd_m"), errors="coerce"),
            "as_of_date": pd.to_datetime(fund.get("as_of_date"), errors="coerce"),
            "pct_called": pd.to_numeric(fund.get("paid_in_pct"), errors="coerce"),
            "dpi": pd.to_numeric(fund.get("dpi"), errors="coerce"),
            "rvpi": pd.to_numeric(fund.get("rvpi"), errors="coerce"),
            "net_moic": pd.to_numeric(fund.get("net_moic"), errors="coerce"),
            "net_irr": pd.to_numeric(fund.get("net_irr"), errors="coerce"),
            "flagship_flag": False,
            "flagship_selection_rationale": pd.NA,
            "source_system": "preqin",
        })
    if not rows:
        return _empty(DIM_EXTERNAL_FUND_COLUMNS)
    out = pd.DataFrame(rows)
    out = out.sort_values(["external_sponsor_id", "vintage", "commitment_usd_m"], ascending=[True, False, False], na_position="last")
    return out.drop_duplicates(["preqin_fund_id", "preqin_fund_source_table"], keep="first")[DIM_EXTERNAL_FUND_COLUMNS].reset_index(drop=True)


def select_flagship_external_funds(dim_external_fund: pd.DataFrame) -> pd.DataFrame:
    if dim_external_fund.empty:
        return dim_external_fund
    out = dim_external_fund.copy()
    out["flagship_flag"] = False
    out["flagship_selection_rationale"] = pd.NA
    for sponsor_id, group in out.groupby("external_sponsor_id", dropna=False):
        candidates = group.copy()
        candidates["_vintage"] = pd.to_numeric(candidates["vintage"], errors="coerce")
        candidates = candidates[candidates["_vintage"].notna()]
        if candidates.empty:
            continue
        candidates["_name_score"] = candidates["fund_name"].map(_flagship_name_score)
        candidates = candidates.sort_values(
            ["_vintage", "_name_score", "commitment_usd_m"],
            ascending=[False, False, False],
            na_position="last",
        )
        winner = candidates.iloc[0]
        rationale = (
            f"latest_vintage={int(winner['_vintage'])};"
            f"name_score={winner['_name_score']:.3f};"
            f"commitment_usd_m={winner.get('commitment_usd_m')}"
        )
        out.loc[out["external_fund_id"].eq(winner["external_fund_id"]), "flagship_flag"] = True
        out.loc[out["external_fund_id"].eq(winner["external_fund_id"]), "flagship_selection_rationale"] = rationale
    return out[DIM_EXTERNAL_FUND_COLUMNS]


def _normalize_factset_fund_vendor_frame(factset_funds: pd.DataFrame) -> pd.DataFrame:
    if factset_funds.empty:
        return factset_funds
    out = factset_funds.copy()
    if "source_sponsor_id" not in out.columns and "factset_gp_entity_id" in out.columns:
        out["source_sponsor_id"] = out["factset_gp_entity_id"]
    if "sponsor_name" not in out.columns and "sponsor_name_factset" in out.columns:
        out["sponsor_name"] = out["sponsor_name_factset"]
    if "fund_name" not in out.columns and "fund_name_factset" in out.columns:
        out["fund_name"] = out["fund_name_factset"]
    if "source_fund_id" not in out.columns and "factset_fund_entity_id" in out.columns:
        out["source_fund_id"] = out["factset_fund_entity_id"]
    if "source_fund_source_table" not in out.columns:
        out["source_fund_source_table"] = "pevc_fund"
    return out


def build_xref_external_fund_source(
    dim_external_fund: pd.DataFrame,
    factset_funds: pd.DataFrame | None,
) -> pd.DataFrame:
    if dim_external_fund.empty:
        return _empty(XREF_EXTERNAL_FUND_SOURCE_COLUMNS)
    rows = []
    for _, fund in dim_external_fund.iterrows():
        rows.append({
            "external_fund_xref_id": stable_id("ext_fund_xref", "preqin", fund["preqin_fund_source_table"], fund["preqin_fund_id"]),
            "external_fund_id": fund["external_fund_id"],
            "external_sponsor_id": fund["external_sponsor_id"],
            "source_system": "preqin",
            "source_fund_id": fund["preqin_fund_id"],
            "source_fund_source_table": fund["preqin_fund_source_table"],
            "source_sponsor_id": fund["preqin_firm_id"],
            "source_fund_name": fund["fund_name"],
            "source_sponsor_name": fund["sponsor_name"],
            "source_vintage": fund["vintage"],
            "match_method": "preqin_source_identity",
            "match_score": 1.0,
            "match_confidence": "high",
            "review_status": "auto_matched",
        })
    if factset_funds is None or factset_funds.empty:
        return pd.DataFrame(rows)[XREF_EXTERNAL_FUND_SOURCE_COLUMNS] if rows else _empty(XREF_EXTERNAL_FUND_SOURCE_COLUMNS)

    factset = _normalize_factset_fund_vendor_frame(factset_funds)
    factset["_source_sponsor_key"] = (
        factset["source_sponsor_id"].astype("string")
    )
    factset["_vintage_numeric"] = pd.to_numeric(
        factset["vintage"], errors="coerce"
    )
    funds_by_sponsor = {
        str(sponsor_id): group
        for sponsor_id, group in factset.groupby(
            "_source_sponsor_key", dropna=False
        )
    }
    factset["_sponsor_family_key"] = factset.sponsor_name.map(sponsor_family_key)
    funds_by_family = dict(tuple(factset.groupby("_sponsor_family_key")))
    started_at = perf_counter()
    total_funds = len(dim_external_fund)
    for position, (_, fund) in enumerate(
        dim_external_fund.iterrows(), start=1
    ):
        if position == 1 or position % 100 == 0 or position == total_funds:
            logging.info(
                "Fund xref progress=%s/%s elapsed_seconds=%.2f "
                "rss_gb=%.2f matches=%s",
                position,
                total_funds,
                perf_counter() - started_at,
                get_process_memory_gb(),
                len(rows),
            )
        sponsor_id = fund.get("factset_gp_entity_id")
        scoped = (
            funds_by_sponsor.get(str(sponsor_id), factset.iloc[0:0])
            if pd.notna(sponsor_id)
            else factset.iloc[0:0]
        )
        if scoped.empty:
            scoped = funds_by_family.get(sponsor_family_key(fund["sponsor_name"]), factset.iloc[0:0])
        if scoped.empty:
            continue
        scoped = scoped.copy()
        scoped["_fund_score"] = scoped["fund_name"].map(lambda value: _fund_match_score(fund["fund_name"], value))
        scoped["_vintage_match"] = scoped["_vintage_numeric"].eq(
            pd.to_numeric(fund["vintage"], errors="coerce")
        )
        scoped["_score"] = scoped["_fund_score"] + scoped["_vintage_match"].map({True: 0.15, False: 0.0})
        scoped = scoped.sort_values(["_score", "_fund_score"], ascending=[False, False])
        if scoped.empty or float(scoped.iloc[0]["_fund_score"]) < 0.72:
            continue
        best = scoped.iloc[0]
        method = "factset_fund_name_vintage" if bool(best["_vintage_match"]) else "factset_fund_name_sponsor_scoped"
        confidence = "high" if float(best["_fund_score"]) >= 0.92 else "medium"
        rows.append({
            "external_fund_xref_id": stable_id("ext_fund_xref", "factset_pevc", best["source_fund_id"]),
            "external_fund_id": fund["external_fund_id"],
            "external_sponsor_id": fund["external_sponsor_id"],
            "source_system": "factset_pevc",
            "source_fund_id": best["source_fund_id"],
            "source_fund_source_table": best.get("source_fund_source_table", "pevc_fund"),
            "source_sponsor_id": best["source_sponsor_id"],
            "source_fund_name": best["fund_name"],
            "source_sponsor_name": best["sponsor_name"],
            "source_vintage": best["vintage"],
            "match_method": method,
            "match_score": float(best["_score"]),
            "match_confidence": confidence,
            "review_status": "auto_matched" if confidence == "high" else "needs_review",
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return _empty(XREF_EXTERNAL_FUND_SOURCE_COLUMNS)
    return out[XREF_EXTERNAL_FUND_SOURCE_COLUMNS].drop_duplicates("external_fund_xref_id").reset_index(drop=True)


def _apply_factset_xref_to_dim_fund(dim_external_fund: pd.DataFrame, xref: pd.DataFrame) -> pd.DataFrame:
    if dim_external_fund.empty or xref.empty:
        return dim_external_fund
    factset = xref[xref["source_system"].eq("factset_pevc")].copy()
    if factset.empty:
        return dim_external_fund
    out = dim_external_fund.copy()
    for _, row in factset.iterrows():
        mask = out["external_fund_id"].eq(row["external_fund_id"])
        out.loc[mask, "factset_fund_entity_id"] = row["source_fund_id"]
        out.loc[mask, "factset_gp_entity_id"] = row["source_sponsor_id"]
    return out


def build_external_portco_factset_xref(
    fact_external_holdings: pd.DataFrame,
) -> pd.DataFrame:
    if fact_external_holdings.empty:
        return _empty(EXTERNAL_PORTCO_FACTSET_XREF_COLUMNS)
    scoped = fact_external_holdings[fact_external_holdings["factset_portco_entity_id"].notna()].copy()
    if scoped.empty:
        return _empty(EXTERNAL_PORTCO_FACTSET_XREF_COLUMNS)
    rows = []
    for _, row in scoped.drop_duplicates(["external_portco_id", "factset_portco_entity_id"]).iterrows():
        rows.append({
            "external_portco_id": row["external_portco_id"],
            "company_name": row["company_name"],
            "company_slug": row["company_slug"],
            "external_sponsor_id": row["external_sponsor_id"],
            "factset_portco_entity_id": row["factset_portco_entity_id"],
            "match_method": row.get("match_method", "factset_pevc_holding"),
            "match_confidence": row.get("source_confidence", "high"),
            "review_status": "auto_matched",
        })
    return pd.DataFrame(rows)[EXTERNAL_PORTCO_FACTSET_XREF_COLUMNS].reset_index(drop=True)


def _factset_holdings_rows(
    pevc: pd.DataFrame,
    dim_external_fund: pd.DataFrame,
    xref_fund: pd.DataFrame,
    sponsor_scope: pd.DataFrame | None = None,
) -> list[dict]:
    if pevc.empty:
        return []
    sponsor_lookup = _external_sponsor_lookup(
        sponsor_scope if sponsor_scope is not None else pd.DataFrame()
    )
    fund_lookup = {}
    if not xref_fund.empty:
        factset_xref = xref_fund[xref_fund["source_system"].eq("factset_pevc")]
        fund_by_factset = dict(zip(
            factset_xref["source_fund_id"].astype("string"),
            factset_xref["external_fund_id"],
        ))
        sponsor_by_factset_fund = dict(zip(
            factset_xref["source_fund_id"].astype("string"),
            factset_xref["external_sponsor_id"],
        ))
    else:
        fund_by_factset = {}
        sponsor_by_factset_fund = {}
    dim_by_external = dim_external_fund.set_index("external_fund_id") if not dim_external_fund.empty else pd.DataFrame().T
    vendor_names = set(pevc["sponsor_name_factset"].dropna())
    if "sponsor_name" in dim_external_fund:
        vendor_names.update(dim_external_fund.sponsor_name.dropna())
    sponsor_matches = {name: _match_external_sponsor_id(str(name), sponsor_lookup) for name in vendor_names}
    seed_names = sponsor_lookup.drop_duplicates("external_sponsor_id").set_index("external_sponsor_id").sponsor_name.to_dict()

    rows = []
    for _, inv in pevc.iterrows():
        factset_fund_id = _clean_id(inv.get("factset_fund_entity_id")) or _clean_id(inv.get("transaction_fund_entity_id"))
        external_fund_id = fund_by_factset.get(factset_fund_id)
        external_sponsor_id = sponsor_by_factset_fund.get(factset_fund_id)
        fund_name = inv.get("fund_name_factset") or inv.get("transaction_fund_name")
        sponsor_name = inv.get("sponsor_name_factset")
        vintage = _numeric_year(inv.get("vintage"))
        if external_fund_id and external_fund_id in dim_by_external.index:
            dim_row = dim_by_external.loc[external_fund_id]
            if not same_sponsor_family(_clean_id(sponsor_name), _clean_id(dim_row.get("sponsor_name"))):
                raise ValueError(f"FactSet fund crosswalk changes sponsor identity: {factset_fund_id}")
            fund_name = dim_row.get("fund_name", fund_name)
            sponsor_name = dim_row.get("sponsor_name", sponsor_name)
            vintage = dim_row.get("vintage", vintage)
            external_sponsor_id = dim_row.get("external_sponsor_id", external_sponsor_id)
        if pd.notna(sponsor_name) and str(sponsor_name).strip():
            seed_id, seed_method = sponsor_matches[sponsor_name]
            if seed_method != "generated_from_website_sponsor":
                external_sponsor_id = seed_id
                sponsor_name = seed_names[seed_id]
            elif not external_sponsor_id:
                external_sponsor_id = seed_id
        elif not external_sponsor_id:
            external_sponsor_id = stable_id("ext_sponsor", sponsor_name)
        assumed_flagship = False
        flagship = None
        if not external_fund_id:
            flagship = _flagship_for_sponsor(
                external_sponsor_id,
                dim_external_fund,
            )
            if flagship and not (factset_fund_id or fund_name):
                external_fund_id = flagship["external_fund_id"]
                fund_name = flagship["fund_name"]
                vintage = flagship.get("vintage", vintage)
                assumed_flagship = True
            else:
                external_fund_id = stable_id(
                    "ext_fund",
                    "factset_pevc",
                    factset_fund_id or fund_name,
                    vintage,
                )
        factset_portco_id = _clean_id(inv.get("factset_portco_entity_id"))
        external_portco_id = stable_id("ext_portco", factset_portco_id or inv.get("portco_name_factset"))
        investment_date = pd.to_datetime(inv.get("entry_date"), errors="coerce")
        source_holding_id = stable_id(
            "ext_hold_src",
            factset_fund_id,
            factset_portco_id,
            inv.get("pevc_trans_deal_id"),
            investment_date.date() if pd.notna(investment_date) else None,
        )
        rows.append({
            "external_holding_id": stable_id(
                "ext_hold", "factset_pevc", source_holding_id
            ),
            "external_sponsor_id": external_sponsor_id,
            "external_fund_id": external_fund_id,
            "external_portco_id": external_portco_id,
            "sponsor_name": sponsor_name,
            "fund_name": fund_name,
            "vintage": vintage,
            "company_name": inv.get("portco_name_factset"),
            "company_slug": slug(inv.get("portco_name_factset")),
            "company_country": inv.get("portco_country"),
            "industry": inv.get("portco_industry"),
            "investment_date": investment_date,
            "exit_date": pd.to_datetime(inv.get("exit_date"), errors="coerce"),
            "entry_type": inv.get("entry_type"),
            "transaction_close_date": pd.to_datetime(inv.get("transaction_close_date"), errors="coerce"),
            "round_of_funding": inv.get("round_of_funding"),
            "total_investment_amount": inv.get("total_investment_amount"),
            "investment_description": inv.get("investment_description"),
            "amount_invested_by_sponsor": inv.get("amount_invested_by_sponsor"),
            "factset_gp_entity_id": inv.get("factset_gp_entity_id"),
            "factset_fund_entity_id": inv.get("factset_fund_entity_id"),
            "factset_portco_entity_id": factset_portco_id or pd.NA,
            "transaction_fund_entity_id": inv.get("transaction_fund_entity_id"),
            "transaction_fund_name": inv.get("transaction_fund_name"),
            "holding_source_system": "factset_pevc",
            "source_holding_id": source_holding_id,
            "source_company_id": factset_portco_id or pd.NA,
            "source_confidence": "high",
            "fund_assignment_method": (
                "assumed_latest_flagship"
                if assumed_flagship
                else "factset_pevc_fund_xref"
            ),
            "fund_assignment_confidence": (
                "low" if assumed_flagship else "high"
            ),
            "fund_assignment_is_assumed": assumed_flagship,
            "fund_assignment_rationale": (
                (
                    f"selected_external_fund_id={external_fund_id};"
                    f"selected_fund_name={fund_name};selected_vintage={vintage};"
                    "flagship_selection_rationale="
                    f"{flagship.get('flagship_selection_rationale') or 'latest_qualifying_flagship_pe_fund'}"
                )
                if assumed_flagship
                else "matched_factset_fund_entity"
            ),
            "match_method": "factset_pevc_holding",
            "holding_review_status": (
                "assumed_flagship" if assumed_flagship else "auto_mapped"
            ),
            "contributing_source_systems": "factset_pevc",
            "canonical_holding_flag": True,
            "company_url": _optional_text(inv.get("portco_website")),
            "external_company_url": _optional_text(inv.get("portco_website")),
            "company_description": _optional_text(inv.get("portco_description")),
            "exit_is_full": (float(inv["retaining_stake"]) == 0
                if pd.notna(inv.get("exit_date")) and pd.notna(inv.get("retaining_stake")) else pd.NA),
            "exit_evidence": (f"FactSet pevc_retaining_stake={inv['retaining_stake']}; exit_code={inv.get('exit_code')}"
                if pd.notna(inv.get("retaining_stake")) else pd.NA),
            "evidence_text": _optional_text(inv.get("portco_profile_json")),
            "source_url": pd.NA,
            "final_url": pd.NA,
            "crawl_run_id": pd.NA,
            "knowledge_datetime": pd.NaT,
            "source_fund_name": pd.NA,
            "source_investment_year": pd.NA,
            "fund_assignment_score": pd.NA,
            "fund_assignment_margin": pd.NA,
            "fund_assignment_rule_version": pd.NA,
        })
    return rows


def _external_fund_lookup(dim_external_fund: pd.DataFrame, sponsor_scope: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "external_fund_id",
        "external_sponsor_id",
        "sponsor_name",
        "fund_name",
        "vintage",
        "flagship_flag",
        "commitment_usd_m",
        "flagship_selection_rationale",
    ]
    if dim_external_fund.empty:
        return pd.DataFrame(columns=columns)
    return dim_external_fund.reindex(columns=columns).drop_duplicates()


def _external_sponsor_lookup(sponsor_scope: pd.DataFrame) -> pd.DataFrame:
    columns = ["external_sponsor_id", "sponsor_name", "sponsor_key"]
    if sponsor_scope.empty:
        return pd.DataFrame(columns=columns)
    out = sponsor_scope[["external_sponsor_id", "sponsor_name"]].drop_duplicates().copy()
    out["sponsor_key"] = out["sponsor_name"].map(sponsor_family_key)
    return out[columns].drop_duplicates()


def _match_external_sponsor_id(sponsor: str, sponsor_lookup: pd.DataFrame) -> tuple[str, str]:
    key = sponsor_family_key(sponsor)
    if not key or sponsor_lookup.empty:
        return stable_id("ext_sponsor", sponsor), "generated_from_website_sponsor"
    matched = sponsor_lookup[sponsor_lookup["sponsor_key"].eq(key)]
    if matched.external_sponsor_id.nunique() == 1:
        row = matched.iloc[0]
        return row["external_sponsor_id"], "sponsor_slug_xref"
    return stable_id("ext_sponsor", sponsor), "generated_from_website_sponsor"


def _flagship_for_sponsor(external_sponsor_id: str, funds: pd.DataFrame) -> dict | None:
    required = {"external_sponsor_id", "flagship_flag"}
    if funds.empty or not required.issubset(funds.columns):
        return None
    scoped = funds[(funds["external_sponsor_id"].eq(external_sponsor_id)) & (funds["flagship_flag"].eq(True))]
    if scoped.empty:
        scoped = funds[funds["external_sponsor_id"].eq(external_sponsor_id)].copy()
        if scoped.empty:
            return None
        scoped["_vintage"] = pd.to_numeric(scoped["vintage"], errors="coerce")
        scoped = scoped.sort_values(["_vintage", "commitment_usd_m"], ascending=[False, False], na_position="last")
    row = scoped.iloc[0]
    return {
        "external_fund_id": row["external_fund_id"],
        "fund_name": row["fund_name"],
        "vintage": row.get("vintage"),
        "flagship_selection_rationale": row.get("flagship_selection_rationale"),
    }


def _external_factset_lookup(factset_holdings: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "external_sponsor_id",
        "external_fund_id",
        "fund_name",
        "vintage",
        "external_portco_id",
        "company_name",
        "company_country",
        "industry",
        "investment_date",
        "exit_date",
        "factset_portco_entity_id",
    ]
    if factset_holdings.empty:
        return pd.DataFrame(columns=columns + ["company_key"])
    out = factset_holdings[columns].dropna(subset=["external_sponsor_id", "company_name"]).drop_duplicates().copy()
    out["company_key"] = out["company_name"].map(_company_keys)
    return out.explode("company_key").dropna(subset=["company_key"]).drop_duplicates()


def _external_website_fund_matches(
    row: pd.Series,
    funds: pd.DataFrame,
    factset: pd.DataFrame,
    flagship_by_sponsor: dict[str, dict],
) -> list[dict[str, object]]:
    fund_raw = str(row.get("fund") or "").strip()
    external_sponsor_id = row.get("external_sponsor_id")
    if fund_raw:
        scoped = funds[funds["external_sponsor_id"].eq(external_sponsor_id)] if not funds.empty else pd.DataFrame()
        if not scoped.empty:
            candidates = scoped.copy()
            candidates["_score"] = candidates["fund_name"].map(lambda value: _fund_match_score(value, fund_raw))
            candidates = candidates.sort_values(["_score", "fund_name"], ascending=[False, True])
            top = candidates.iloc[0]
            second_score = float(candidates.iloc[1]["_score"]) if len(candidates) > 1 else 0.0
            score = float(top["_score"])
            margin = score - second_score
            if score >= 0.92 and margin >= 0.03:
                return [{
                    "external_fund_id": top["external_fund_id"],
                    "fund_name": top["fund_name"],
                    "vintage": top.get("vintage"),
                    "fund_assignment_method": "website_explicit_fund_match",
                    "fund_assignment_confidence": "high",
                    "fund_assignment_is_assumed": False,
                    "fund_assignment_score": score,
                    "fund_assignment_margin": margin,
                    "fund_assignment_rationale": (
                        f"source_fund_name={fund_raw};"
                        f"selected_external_fund_id={top['external_fund_id']};"
                        f"selected_fund_name={top['fund_name']};"
                        f"selected_vintage={top.get('vintage')};"
                        f"match_score={score:.3f};runner_up_margin={margin:.3f}"
                    ),
                    "fund_assignment_rule_version": WEBSITE_FUND_ASSIGNMENT_RULE_VERSION,
                }]
    if not factset.empty:
        scoped = factset[factset["external_sponsor_id"].eq(external_sponsor_id)].copy()
        scoped = scoped.drop_duplicates(
            ["external_portco_id", "external_fund_id", "company_name"]
        )
        website_company = row.get("company_name")
        scoped["_score"] = scoped["company_name"].map(lambda value: _company_match_score(value, website_company))
        scoped = scoped.sort_values(["_score", "company_name"], ascending=[False, True])
        if not scoped.empty:
            top_score = float(scoped.iloc[0]["_score"])
            second_score = float(scoped.iloc[1]["_score"]) if len(scoped) > 1 else 0.0
            margin = top_score - second_score
            if top_score >= 0.8 and (top_score >= 1.0 or margin >= 0.03):
                top_company = scoped.iloc[0]["company_name"]
                matched = scoped[scoped["company_name"].eq(top_company)]
                method = "factset_pevc_company_exact_sponsor_scoped" if top_score >= 1.0 else "factset_pevc_company_fuzzy_sponsor_scoped"
                confidence = "high" if top_score >= 1.0 else "medium"
                return [{
                    "external_fund_id": factset_row["external_fund_id"],
                    "fund_name": factset_row["fund_name"],
                    "vintage": factset_row.get("vintage"),
                    "fund_assignment_method": method,
                    "fund_assignment_confidence": confidence,
                    "fund_assignment_is_assumed": False,
                    "fund_assignment_score": top_score,
                    "fund_assignment_margin": margin,
                    "fund_assignment_rationale": (
                        f"matched_factset_company={top_company};"
                        "matched_factset_portco_entity_id="
                        f"{_clean_id(factset_row.get('factset_portco_entity_id'))};"
                        f"selected_external_fund_id={factset_row['external_fund_id']};"
                        f"selected_fund_name={factset_row['fund_name']};"
                        f"selected_vintage={factset_row.get('vintage')};"
                        f"match_score={top_score:.3f};runner_up_margin={margin:.3f}"
                    ),
                    "fund_assignment_rule_version": WEBSITE_FUND_ASSIGNMENT_RULE_VERSION,
                } for _, factset_row in matched.drop_duplicates(["external_fund_id"]).iterrows()]
    flagship = flagship_by_sponsor.get(external_sponsor_id)
    if flagship:
        flagship_rationale = (
            flagship.get("flagship_selection_rationale")
            or "latest_qualifying_flagship_pe_fund"
        )
        return [{
            "external_fund_id": flagship["external_fund_id"],
            "fund_name": flagship["fund_name"],
            "vintage": flagship.get("vintage"),
            "fund_assignment_method": "assumed_latest_flagship",
            "fund_assignment_confidence": "low",
            "fund_assignment_is_assumed": True,
            "fund_assignment_score": pd.NA,
            "fund_assignment_margin": pd.NA,
            "fund_assignment_rationale": (
                f"source_fund_name={fund_raw or '<missing>'};"
                f"selected_external_fund_id={flagship['external_fund_id']};"
                f"selected_fund_name={flagship['fund_name']};"
                f"selected_vintage={flagship.get('vintage')};"
                f"flagship_selection_rationale={flagship_rationale}"
            ),
            "fund_assignment_rule_version": WEBSITE_FUND_ASSIGNMENT_RULE_VERSION,
        }]
    return [{
        "external_fund_id": pd.NA,
        "fund_name": pd.NA,
        "vintage": pd.NA,
        "fund_assignment_method": "missing_fund_assignment",
        "fund_assignment_confidence": "unmatched",
        "fund_assignment_is_assumed": False,
        "fund_assignment_score": pd.NA,
        "fund_assignment_margin": pd.NA,
        "fund_assignment_rationale": (
            f"source_fund_name={fund_raw or '<missing>'};"
            "no_explicit_factset_or_flagship_match"
        ),
        "fund_assignment_rule_version": WEBSITE_FUND_ASSIGNMENT_RULE_VERSION,
    }]


def build_external_website_holdings(
    holding_candidates: pd.DataFrame | None,
    sponsor_scope: pd.DataFrame,
    dim_external_fund: pd.DataFrame,
    fact_external_holdings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    candidates = accepted_company_candidates(holding_candidates) if holding_candidates is not None else pd.DataFrame()
    if candidates.empty:
        empty = _empty(EXTERNAL_WEBSITE_HOLDING_COLUMNS)
        empty.attrs["delta_schema_types"] = EXTERNAL_WEBSITE_HOLDING_DELTA_SCHEMA_TYPES
        return empty

    sponsor_lookup = _external_sponsor_lookup(sponsor_scope)
    funds = _external_fund_lookup(dim_external_fund, sponsor_scope)
    factset = _external_factset_lookup(
        fact_external_holdings if fact_external_holdings is not None else pd.DataFrame()
    )
    flagship_by_sponsor = {}
    for sponsor_id, group in funds.groupby("external_sponsor_id"):
        flagship = _flagship_for_sponsor(sponsor_id, group)
        if flagship:
            flagship_by_sponsor[sponsor_id] = flagship

    candidates[["external_sponsor_id", "sponsor_match_method"]] = candidates["sponsor"].apply(
        lambda sponsor: pd.Series(_match_external_sponsor_id(sponsor, sponsor_lookup))
    )
    candidates["sponsor_id"] = candidates["external_sponsor_id"]
    candidates["sponsor_name"] = candidates.get("sponsor")

    expanded_rows = []
    for _, candidate in candidates.iterrows():
        for fund_match in _external_website_fund_matches(candidate, funds, factset, flagship_by_sponsor):
            expanded_rows.append({**candidate.to_dict(), **fund_match})
    candidates = pd.DataFrame(expanded_rows)

    investment_date = candidates.apply(
        lambda row: row.get("investment_date")
        if pd.notna(row.get("investment_date")) and not isinstance(row.get("investment_date"), str)
        else _coerce_investment_date(row.get("investment_date")),
        axis=1,
    )
    exit_date = pd.to_datetime(candidates.get("exit_date"), errors="coerce")
    company_slug = candidates.get("company_slug", candidates["company_name"].map(slug))
    source_confidence = candidates.get("confidence", pd.Series("", index=candidates.index)).map(_normalize_confidence)
    blank = pd.Series(pd.NA, index=candidates.index)
    company_country = candidates.get("company_country", blank)
    geography = candidates.get("geography", blank)
    industry = candidates.get("industry", blank)
    sector = candidates.get("sector", blank)
    entry_type = candidates.get("entry_type", blank)
    investment_description = candidates.get("investment_description", blank)
    evidence_text = candidates.get("evidence_text", blank)
    transaction_fund_name = candidates.get("transaction_fund_name", blank)
    fund_raw = candidates.get("fund", blank)
    source_investment_year = pd.to_numeric(
        candidates.get("investment_year", blank),
        errors="coerce",
    ).astype("Int64")
    knowledge_datetime = candidates.get("knowledge_datetime", blank)
    extracted_at = candidates.get("extracted_at", blank)

    out = pd.DataFrame({
        "website_holding_id": candidates.apply(
            lambda row: stable_id(
                "web_hold",
                row.get("crawl_run_id"),
                row.get("sponsor"),
                row.get("fund") or "",
                row.get("company_name"),
                row.get("source_url"),
            ),
            axis=1,
        ),
        "crawl_run_id": candidates.get("crawl_run_id"),
        "knowledge_datetime": knowledge_datetime.where(knowledge_datetime.notna(), extracted_at),
        "source_system": "sponsor_website",
        "source_confidence": source_confidence,
        "sponsor_id": candidates["external_sponsor_id"],
        "sponsor_name": candidates.get("sponsor"),
        "fund_id": candidates["external_fund_id"],
        "fund_name": candidates["fund_name"],
        "fund_name_raw": candidates.get("fund"),
        "vintage": candidates["vintage"],
        "website_holding_candidate_id": candidates.get("website_holding_candidate_id"),
        "site_snapshot_id": candidates.get("site_snapshot_id"),
        "structured_portco_id": candidates.apply(
            lambda row: row.get("external_portco_id")
            if pd.notna(row.get("external_portco_id"))
            else stable_id("ext_portco", "sponsor_website", row.get("sponsor"), row.get("company_name")),
            axis=1,
        ),
        "benchmark_portco_id": pd.NA,
        "company_name": candidates.get("company_name"),
        "company_slug": company_slug,
        "company_country": company_country.where(company_country.notna(), geography),
        "industry": industry.where(industry.notna(), sector),
        "employee_count": candidates.get("employee_count"),
        "investment_date": investment_date,
        "exit_date": exit_date,
        "entry_type": entry_type.where(entry_type.notna(), "Website Portfolio Holding"),
        "transaction_close_date": pd.to_datetime(candidates.get("transaction_close_date"), errors="coerce"),
        "round_of_funding": candidates.get("round_of_funding"),
        "total_investment_amount": candidates.get("total_investment_amount"),
        "investment_description": investment_description.where(investment_description.notna(), evidence_text),
        "amount_invested_by_sponsor": candidates.get("amount_invested_by_sponsor"),
        "transaction_fund_entity_id": candidates.get("transaction_fund_entity_id"),
        "transaction_fund_name": transaction_fund_name.where(transaction_fund_name.notna(), fund_raw),
        "portfolio_status": candidates.get("portfolio_status"),
        "sector": candidates.get("sector"),
        "geography": candidates.get("geography"),
        "company_url": candidates.get("company_url"),
        "external_company_url": candidates.get("external_company_url"),
        "company_description": candidates.get("company_description"),
        "source_url": candidates.get("source_url"),
        "final_url": candidates.get("final_url"),
        "evidence_text": candidates.get("evidence_text"),
        "extraction_method": candidates.get("extraction_method"),
        "fund_match_method": candidates["fund_assignment_method"],
        "fund_match_confidence": candidates["fund_assignment_confidence"],
        "holding_review_status": candidates["fund_assignment_method"].map(
            {
                "assumed_latest_flagship": "assumed_flagship",
                "missing_fund_assignment": "needs_fund_review",
            }
        ).fillna("auto_mapped"),
        "source_fund_name": fund_raw.replace("", pd.NA),
        "source_investment_year": source_investment_year,
        "fund_assignment_score": pd.to_numeric(
            candidates["fund_assignment_score"],
            errors="coerce",
        ),
        "fund_assignment_margin": pd.to_numeric(
            candidates["fund_assignment_margin"],
            errors="coerce",
        ),
        "fund_assignment_rationale": candidates["fund_assignment_rationale"],
        "fund_assignment_rule_version": candidates[
            "fund_assignment_rule_version"
        ],
    })
    out["source_confidence"] = out["source_confidence"].astype("string").fillna("medium")
    out = out[EXTERNAL_WEBSITE_HOLDING_COLUMNS].drop_duplicates(
        subset=["crawl_run_id", "sponsor_id", "fund_id", "fund_name", "company_slug", "source_url"]
    ).reset_index(drop=True)
    out.attrs["delta_schema_types"] = EXTERNAL_WEBSITE_HOLDING_DELTA_SCHEMA_TYPES
    return out


def _website_holding_rows(website_holdings: pd.DataFrame) -> list[dict]:
    if website_holdings.empty:
        return []
    rows = []
    for _, row in website_holdings.iterrows():
        external_portco_id = row.get("structured_portco_id")
        if pd.isna(external_portco_id):
            external_portco_id = stable_id(
                "ext_portco", "sponsor_website", row.get("sponsor_name"), row.get("company_name")
            )
        rows.append({
            "external_holding_id": stable_id(
                "ext_hold",
                "sponsor_website",
                row.get("website_holding_id"),
                row.get("fund_id"),
            ),
            "external_sponsor_id": row.get("sponsor_id"),
            "external_fund_id": row.get("fund_id"),
            "external_portco_id": external_portco_id,
            "sponsor_name": row.get("sponsor_name"),
            "fund_name": row.get("fund_name"),
            "vintage": row.get("vintage"),
            "company_name": row.get("company_name"),
            "company_slug": row.get("company_slug"),
            "company_country": row.get("company_country"),
            "industry": row.get("industry"),
            "investment_date": pd.to_datetime(row.get("investment_date"), errors="coerce"),
            "exit_date": pd.to_datetime(row.get("exit_date"), errors="coerce"),
            "exit_is_full": pd.NA,
            "exit_evidence": pd.NA,
            "entry_type": row.get("entry_type"),
            "transaction_close_date": pd.to_datetime(row.get("transaction_close_date"), errors="coerce"),
            "round_of_funding": row.get("round_of_funding"),
            "total_investment_amount": row.get("total_investment_amount"),
            "investment_description": row.get("investment_description"),
            "amount_invested_by_sponsor": row.get("amount_invested_by_sponsor"),
            "factset_gp_entity_id": pd.NA,
            "factset_fund_entity_id": pd.NA,
            "factset_portco_entity_id": pd.NA,
            "transaction_fund_entity_id": row.get("transaction_fund_entity_id"),
            "transaction_fund_name": row.get("transaction_fund_name"),
            "holding_source_system": "sponsor_website",
            "source_holding_id": row.get("website_holding_id"),
            "source_company_id": row.get(
                "source_portco_id", row.get("structured_portco_id")
            ),
            "source_confidence": row.get("source_confidence"),
            "fund_assignment_method": row.get("fund_match_method"),
            "fund_assignment_confidence": row.get("fund_match_confidence"),
            "fund_assignment_is_assumed": bool(str(row.get("fund_match_method") or "") == "assumed_latest_flagship"),
            "fund_assignment_rationale": row.get(
                "fund_assignment_rationale",
                row.get("fund_match_method"),
            ),
            "match_method": row.get("fund_match_method"),
            "holding_review_status": row.get("holding_review_status"),
            "contributing_source_systems": "sponsor_website",
            "canonical_holding_flag": True,
            "company_url": row.get("company_url"),
            "external_company_url": row.get("external_company_url"),
            "company_description": row.get("company_description"),
            "evidence_text": row.get("evidence_text"),
            "source_url": row.get("source_url"),
            "final_url": row.get("final_url"),
            "crawl_run_id": row.get("crawl_run_id"),
            "knowledge_datetime": pd.to_datetime(row.get("knowledge_datetime"), errors="coerce"),
            "source_fund_name": row.get(
                "source_fund_name",
                row.get("fund_name_raw"),
            ),
            "source_investment_year": row.get("source_investment_year"),
            "fund_assignment_score": row.get("fund_assignment_score"),
            "fund_assignment_margin": row.get("fund_assignment_margin"),
            "fund_assignment_rule_version": row.get(
                "fund_assignment_rule_version"
            ),
        })
    return rows


def _pitchbook_fund_match(
    row: pd.Series,
    external_sponsor_id: str,
    dim_external_fund: pd.DataFrame,
) -> dict[str, object]:
    if dim_external_fund.empty:
        return {}
    source_fund_names = row.get("pitchbook_investor_fund_names")
    fund_names = [
        name.strip()
        for name in (
            "" if pd.isna(source_fund_names) else str(source_fund_names)
        ).split("|")
        if name.strip()
    ]
    if not fund_names:
        return {}
    candidates = dim_external_fund[
        dim_external_fund["external_sponsor_id"].eq(external_sponsor_id)
    ].copy()
    if candidates.empty:
        return {}
    candidates["_pitchbook_fund_score"] = candidates["fund_name"].map(
        lambda candidate: max(
            (_fund_match_score(candidate, fund_name) for fund_name in fund_names),
            default=0.0,
        )
    )
    candidates = candidates.sort_values(
        ["_pitchbook_fund_score", "fund_name"],
        ascending=[False, True],
    )
    best = candidates.iloc[0]
    score = float(best["_pitchbook_fund_score"])
    second_score = (
        float(candidates.iloc[1]["_pitchbook_fund_score"])
        if len(candidates) > 1
        else 0.0
    )
    if score < 0.92 or score - second_score < 0.03:
        return {}
    return {
        "external_fund_id": best["external_fund_id"],
        "fund_name": best["fund_name"],
        "vintage": best.get("vintage"),
        "fund_assignment_method": "pitchbook_explicit_fund_match",
        "fund_assignment_confidence": "high",
        "fund_assignment_rationale": f"pitchbook_fund_name_match_score={score:.3f}",
    }


def _pitchbook_holding_rows(
    pitchbook_holdings: pd.DataFrame,
    sponsor_scope: pd.DataFrame,
    dim_external_fund: pd.DataFrame,
) -> list[dict]:
    if pitchbook_holdings.empty:
        return []
    sponsor_lookup = _external_sponsor_lookup(sponsor_scope)
    rows = []
    for _, row in pitchbook_holdings.iterrows():
        sponsor_name = _clean_id(row.get("sponsor_name"))
        external_sponsor_id, sponsor_match_method = _match_external_sponsor_id(
            sponsor_name,
            sponsor_lookup,
        )
        fund_match = _pitchbook_fund_match(
            row,
            external_sponsor_id,
            dim_external_fund,
        )
        if not fund_match:
            flagship = _flagship_for_sponsor(
                external_sponsor_id,
                dim_external_fund,
            )
            if flagship:
                fund_match = {
                    **flagship,
                    "fund_assignment_method": "assumed_latest_flagship",
                    "fund_assignment_confidence": "low",
                    "fund_assignment_rationale": (
                        flagship.get("flagship_selection_rationale")
                        or "latest_qualifying_flagship_pe_fund"
                    ),
                }
        company_name = row.get("company_name")
        pitchbook_company_id = _clean_id(row.get("pitchbook_company_id"))
        pitchbook_holding_id = _clean_id(row.get("pitchbook_holding_id")) or stable_id(
            "pb_hold",
            external_sponsor_id,
            pitchbook_company_id or company_name,
        )
        fund_assignment_method = fund_match.get(
            "fund_assignment_method",
            "pitchbook_sponsor_only",
        )
        fund_assignment_assumed = (
            fund_assignment_method == "assumed_latest_flagship"
        )
        rows.append({
            "external_holding_id": stable_id("ext_hold", "pitchbook", pitchbook_holding_id),
            "external_sponsor_id": external_sponsor_id,
            "external_fund_id": fund_match.get("external_fund_id", pd.NA),
            "external_portco_id": stable_id(
                "ext_portco",
                "pitchbook",
                pitchbook_company_id or external_sponsor_id,
                pitchbook_company_id or company_name,
            ),
            "sponsor_name": sponsor_name,
            "fund_name": fund_match.get("fund_name", pd.NA),
            "vintage": fund_match.get("vintage", pd.NA),
            "company_name": company_name,
            "company_slug": slug(company_name),
            "company_country": pd.NA,
            "industry": pd.NA,
            "investment_date": pd.to_datetime(row.get("investment_date"), errors="coerce"),
            "exit_date": pd.NaT,
            "exit_is_full": pd.NA,
            "exit_evidence": pd.NA,
            "entry_type": "PitchBook Sponsor Holding",
            "transaction_close_date": pd.to_datetime(
                row.get("latest_deal_date"),
                errors="coerce",
            ),
            "round_of_funding": row.get("pitchbook_deal_types"),
            "total_investment_amount": pd.NA,
            "investment_description": row.get("pitchbook_deal_type2s"),
            "amount_invested_by_sponsor": pd.NA,
            "factset_gp_entity_id": pd.NA,
            "factset_fund_entity_id": pd.NA,
            "factset_portco_entity_id": pd.NA,
            "transaction_fund_entity_id": pd.NA,
            "transaction_fund_name": row.get("pitchbook_investor_fund_names"),
            "holding_source_system": "pitchbook",
            "source_holding_id": pitchbook_holding_id,
            "source_company_id": row.get("pitchbook_company_id"),
            "source_confidence": row.get("source_confidence", "high"),
            "fund_assignment_method": fund_assignment_method,
            "fund_assignment_confidence": fund_match.get(
                "fund_assignment_confidence",
                "unmatched",
            ),
            "fund_assignment_is_assumed": fund_assignment_assumed,
            "fund_assignment_rationale": fund_match.get(
                "fund_assignment_rationale",
                "pitchbook_source_has_no_unambiguous_external_fund",
            ),
            "match_method": (
                f"{sponsor_match_method}+{fund_assignment_method}"
            ),
            "holding_review_status": (
                "assumed_flagship"
                if fund_assignment_assumed
                else "auto_mapped" if fund_match else "needs_fund_review"
            ),
            "contributing_source_systems": "pitchbook",
            "canonical_holding_flag": True,
            "company_url": row.get("external_company_url"),
            "external_company_url": row.get("external_company_url"),
            "company_description": row.get("company_description"),
            "evidence_text": row.get("pitchbook_deal_statuses"),
            "source_url": pd.NA,
            "final_url": pd.NA,
            "crawl_run_id": pd.NA,
            "knowledge_datetime": pd.to_datetime(
                row.get("knowledge_datetime"),
                errors="coerce",
            ),
            "source_fund_name": pd.NA,
            "source_investment_year": pd.NA,
            "fund_assignment_score": pd.NA,
            "fund_assignment_margin": pd.NA,
            "fund_assignment_rule_version": pd.NA,
        })
    return rows


def _lineage_group_columns(combined: pd.DataFrame) -> pd.DataFrame:
    out = combined.copy()
    out["_lineage_sponsor_id"] = out["external_sponsor_id"].map(_clean_id)
    out["_lineage_fund_id"] = out["external_fund_id"].map(_clean_id)
    for index, row in out[out["_lineage_fund_id"].eq("")].iterrows():
        higher = out[
            out["_lineage_sponsor_id"].eq(row["_lineage_sponsor_id"])
            & out["company_key"].eq(row["company_key"])
            & out["_lineage_fund_id"].ne("")
            & out["source_priority"].lt(row["source_priority"])
        ]
        candidate_funds = higher["_lineage_fund_id"].drop_duplicates()
        if len(candidate_funds) == 1:
            out.at[index, "_lineage_fund_id"] = candidate_funds.iloc[0]
    return out


PORTCO_DEDUP_DECISION_METHOD = "website_factset_normalized_name"
PORTCO_EVIDENCE_DECISION_METHOD = "website_factset_sponsor_evidence_v1"
PORTCO_DEDUP_SOURCE_SYSTEM = "sponsor_website"
IDENTITY_DEDUP_APPROVED_BY = "sponsor_stripped_name_identity"
PORTCO_IDENTITY_RULE_VERSION = "sponsor_company_identity_v2"
PORTCO_IDENTITY_CANDIDATE_MIN_SCORE = 0.80
SYSTEM_PORTCO_DEDUP_METHODS = {
    PORTCO_DEDUP_DECISION_METHOD,
    PORTCO_EVIDENCE_DECISION_METHOD,
    DIRECT_ENTITY_DECISION_METHOD,
}
PORTCO_IDENTITY_EVIDENCE_COLUMNS = {
    "external_sponsor_id",
    "source_company_name",
    "source_company_slug",
    "canonical_company_name",
    "match_score",
    "source_name_key",
    "canonical_name_key",
    "character_score",
    "token_order_score",
    "token_coverage_score",
    "prefix_match_flag",
    "exact_name_flag",
    "same_fund_flag",
    "same_vintage_flag",
    "independent_fund_match_flag",
    "shared_fund_count",
    "shared_cohort_count",
    "investment_year_distance",
    "industry_match_flag",
    "country_match_flag",
    "company_domain_match_flag",
    "candidate_margin",
    "ownership_episode_conflict",
    "corroboration_count",
    "auto_match_eligible",
    "evidence_summary",
    "rule_version",
    "rationale",
    "last_seen_run_id",
}


def _optional_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _optional_year(value) -> int | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return int(parsed.year) if pd.notna(parsed) else None


def _optional_vintage(value) -> int | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return int(numeric) if pd.notna(numeric) else None


def _is_true(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return bool(value)


def _candidate_requires_system_demotion(row: pd.Series) -> bool:
    if _is_true(row.get("ownership_episode_conflict")):
        return True
    if (
        row.get("same_fund_flag") is not None
        and pd.notna(row.get("same_fund_flag"))
        and not _is_true(row.get("same_fund_flag"))
    ):
        return True
    if (
        row.get("same_vintage_flag") is not None
        and pd.notna(row.get("same_vintage_flag"))
        and not _is_true(row.get("same_vintage_flag"))
    ):
        return True
    margin = pd.to_numeric(
        pd.Series([row.get("candidate_margin")]),
        errors="coerce",
    ).iloc[0]
    if pd.isna(margin) or margin < AUTO_CANDIDATE_MARGIN_MIN:
        return True
    name = score_company_identity_name(
        row.get("source_company_name"),
        row.get("canonical_company_name"),
    )
    if name.generic_short_name or not name.exact_core_token_match:
        return True
    return (
        name.shared_core_token_count == 1
        and not _is_true(row.get("independent_fund_match_flag"))
        and margin < AUTO_SINGLE_CORE_MARGIN_MIN
    )


def _source_observations(
    frame: pd.DataFrame,
    *,
    source_system: str,
    sponsor_column: str,
    company_id_column: str,
    fund_column: str,
) -> dict[tuple[str, str], tuple[CompanySourceObservation, ...]]:
    grouped: dict[tuple[str, str], list[CompanySourceObservation]] = {}
    for _, row in frame.iterrows():
        sponsor_id = _optional_text(row.get(sponsor_column))
        company_id = _optional_text(row.get(company_id_column))
        company_name = _optional_text(row.get("company_name"))
        if not sponsor_id or not company_id or not company_name:
            continue
        fund_method = _optional_text(
            row.get("fund_assignment_method", row.get("fund_match_method"))
        )
        assumed_value = row.get("fund_assignment_is_assumed", False)
        fund_is_assumed = (
            False if pd.isna(assumed_value) else bool(assumed_value)
        ) or fund_method == "assumed_latest_flagship"
        company_url = row.get("external_company_url")
        if not _optional_text(company_url) and source_system != "sponsor_website":
            company_url = row.get("company_url")
        grouped.setdefault((sponsor_id, company_id), []).append(
            CompanySourceObservation(
                sponsor_id=sponsor_id,
                company_id=company_id,
                company_name=company_name,
                source_system=source_system,
                fund_id=_optional_text(row.get(fund_column)),
                fund_is_assumed=fund_is_assumed,
                fund_evidence_independent=(
                    source_system == "factset_pevc"
                    or fund_method == "website_explicit_fund_match"
                ),
                vintage=_optional_vintage(row.get("vintage")),
                investment_year=_optional_year(row.get("investment_date")),
                exit_year=_optional_year(row.get("exit_date")),
                industry=_optional_text(row.get("industry")),
                country=_optional_text(row.get("company_country")),
                company_domain=normalized_company_domain(company_url),
            )
        )
    return {
        key: tuple(observations)
        for key, observations in grouped.items()
    }


def _candidate_evidence_summary(evidence) -> str:
    signals = [
        f"exact_core_tokens={evidence.name.shared_core_token_count}",
        f"weighted_token_f1={evidence.name.token_coverage_score:.3f}",
    ]
    if evidence.same_fund:
        signals.append(f"shared_funds={evidence.shared_fund_count}")
        signals.append(f"shared_cohorts={evidence.shared_cohort_count}")
        signals.append(
            "fund_context=independent"
            if evidence.independent_fund_match
            else "fund_context=name_derived"
        )
    if evidence.investment_year_distance is not None:
        signals.append(
            f"investment_year_distance={evidence.investment_year_distance}"
        )
    if evidence.industry_match:
        signals.append("industry")
    if evidence.country_match:
        signals.append("country")
    if evidence.company_domain_match:
        signals.append("company_domain")
    if evidence.ownership_episode_conflict:
        signals.append("ownership_episode_conflict")
    return "|".join(signals) or "name_only"


def build_website_factset_dedup_candidates(
    factset_holdings: pd.DataFrame,
    website_holdings: pd.DataFrame,
    *,
    refresh_run_id: str = "",
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS,
    )

    if factset_holdings.empty or website_holdings.empty:
        return _empty(XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    fs = factset_holdings.copy()
    web = website_holdings.copy()
    sponsor_col = "external_sponsor_id" if "external_sponsor_id" in web else "sponsor_id"
    if sponsor_col not in web:
        return _empty(XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    website_id_col = (
        "structured_portco_id"
        if "structured_portco_id" in web
        else "external_portco_id"
    )
    website_fund_col = (
        "fund_id" if "fund_id" in web else "external_fund_id"
    )
    factset = _source_observations(
        fs,
        source_system="factset_pevc",
        sponsor_column="external_sponsor_id",
        company_id_column="external_portco_id",
        fund_column="external_fund_id",
    )
    website = _source_observations(
        web,
        source_system=PORTCO_DEDUP_SOURCE_SYSTEM,
        sponsor_column=sponsor_col,
        company_id_column=website_id_col,
        fund_column=website_fund_col,
    )
    records = []
    factset_by_sponsor: dict[
        str,
        list[tuple[str, tuple[CompanySourceObservation, ...]]],
    ] = {}
    for (sponsor_id, company_id), observations in factset.items():
        factset_by_sponsor.setdefault(sponsor_id, []).append(
            (company_id, observations)
        )

    for (sponsor_id, source_company_id), web_observations in sorted(
        website.items()
    ):
        scored = []
        for canonical_portco_id, fs_observations in factset_by_sponsor.get(
            sponsor_id, []
        ):
            if source_company_id == canonical_portco_id:
                continue
            name = max(
                (
                    score_company_identity_name(
                        web_row.company_name,
                        fs_row.company_name,
                    )
                    for web_row in web_observations
                    for fs_row in fs_observations
                ),
                key=lambda value: (
                    value.name_score,
                    value.character_score,
                    value.left_key,
                    value.right_key,
                ),
            )
            scored.append(
                (name.name_score, canonical_portco_id, fs_observations)
            )
        scored.sort(key=lambda value: (-value[0], value[1]))
        if (
            not scored
            or scored[0][0] < PORTCO_IDENTITY_CANDIDATE_MIN_SCORE
        ):
            continue
        top_score, canonical_portco_id, fs_observations = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        candidate_margin = top_score - second_score
        if len(scored) > 1 and candidate_margin <= 0:
            continue
        evidence = evaluate_company_identity(
            web_observations,
            fs_observations,
            candidate_margin=candidate_margin,
        )
        source_name = web_observations[0].company_name
        canonical_name = fs_observations[0].company_name
        decision_method = (
            PORTCO_DEDUP_DECISION_METHOD
            if evidence.name.exact_match
            else PORTCO_EVIDENCE_DECISION_METHOD
        )
        evidence_summary = _candidate_evidence_summary(evidence)
        records.append({
            "external_company_name_decision_id": stable_id(
                "ext_portco_dedup",
                sponsor_id,
                source_company_id,
                canonical_portco_id,
            ),
            "external_portco_id": canonical_portco_id,
            "external_sponsor_id": sponsor_id,
            "source_system": PORTCO_DEDUP_SOURCE_SYSTEM,
            "source_company_id": source_company_id,
            "source_company_name": source_name,
            "source_company_slug": slug(source_name),
            "canonical_company_name": canonical_name,
            "decision_status": "candidate",
            "decision_method": decision_method,
            "match_score": evidence.name.name_score,
            "source_name_key": evidence.name.left_key,
            "canonical_name_key": evidence.name.right_key,
            "character_score": evidence.name.character_score,
            "token_order_score": evidence.name.token_order_score,
            "token_coverage_score": evidence.name.token_coverage_score,
            "prefix_match_flag": evidence.name.prefix_match,
            "exact_name_flag": evidence.name.exact_match,
            "same_fund_flag": evidence.same_fund,
            "same_vintage_flag": evidence.same_vintage,
            "independent_fund_match_flag": evidence.independent_fund_match,
            "shared_fund_count": evidence.shared_fund_count,
            "shared_cohort_count": evidence.shared_cohort_count,
            "investment_year_distance": evidence.investment_year_distance,
            "industry_match_flag": evidence.industry_match,
            "country_match_flag": evidence.country_match,
            "company_domain_match_flag": evidence.company_domain_match,
            "candidate_margin": evidence.candidate_margin,
            "ownership_episode_conflict": (
                evidence.ownership_episode_conflict
            ),
            "corroboration_count": evidence.corroboration_count,
            "auto_match_eligible": evidence.auto_match_eligible,
            "evidence_summary": evidence_summary,
            "rule_version": PORTCO_IDENTITY_RULE_VERSION,
            "approved_by": pd.NA,
            "approved_at": pd.NaT,
            "rationale": (
                f"Sponsor-scoped company evidence: {evidence_summary}"
            ),
            "first_seen_run_id": refresh_run_id or pd.NA,
            "last_seen_run_id": refresh_run_id or pd.NA,
            "is_active": True,
            "revoked_at": pd.NaT,
            "revoked_by": pd.NA,
        })
    if not records:
        return _empty(XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    return pd.DataFrame(records, columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS).drop_duplicates(
        "external_company_name_decision_id"
    ).reset_index(drop=True)


def merge_external_company_name_decisions(
    persisted: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    refresh_run_id: str = "",
    refreshed_methods: set[str] | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS,
    )

    frames = [
        frame for frame in [persisted, candidates]
        if frame is not None and not frame.empty
    ]
    if not frames:
        return _empty(XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    merged = pd.concat(frames, ignore_index=True)
    for column in XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS:
        if column not in merged:
            merged[column] = pd.NA
    merged = merged[XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS]
    merged["approved_at"] = pd.to_datetime(
        merged["approved_at"],
        errors="coerce",
        utc=True,
    ).dt.tz_localize(None)
    settled_statuses = {"accepted", "rejected"}
    active_refresh_methods = (
        SYSTEM_PORTCO_DEDUP_METHODS
        if refreshed_methods is None
        else refreshed_methods
    )
    current_candidate_ids = set(
        candidates.get(
            "external_company_name_decision_id", pd.Series(dtype="object")
        ).dropna().astype(str)
    )
    out_rows: list[dict] = []
    for decision_id, group in merged.groupby("external_company_name_decision_id", sort=False):
        group = group.copy()
        settled = group[group["decision_status"].isin(settled_statuses)]
        if not settled.empty:
            chosen = settled.sort_values(
                ["approved_at", "last_seen_run_id"],
                na_position="first",
            ).iloc[-1]
            current = group[group["decision_status"].eq("candidate")]
            if not current.empty:
                chosen = chosen.copy()
                current_row = current.iloc[0]
                system_accepted = (
                    _optional_text(chosen.get("approved_by"))
                    == IDENTITY_DEDUP_APPROVED_BY
                )
                current_auto_eligible = _is_true(
                    current_row.get("auto_match_eligible")
                )
                if system_accepted and current_auto_eligible:
                    for column in PORTCO_IDENTITY_EVIDENCE_COLUMNS:
                        if pd.notna(current_row.get(column)):
                            chosen[column] = current_row[column]
                elif (
                    system_accepted
                    and pd.notna(current_row.get("auto_match_eligible"))
                    and _candidate_requires_system_demotion(current_row)
                ):
                    current_name = score_company_identity_name(
                        current_row.get("source_company_name"),
                        current_row.get("canonical_company_name"),
                    )
                    preserve_legacy_exact = (
                        pd.isna(chosen.get("auto_match_eligible"))
                        and current_name.exact_match
                        and current_name.exact_core_token_match
                        and not _is_true(
                            current_row.get("ownership_episode_conflict")
                        )
                    )
                    if not preserve_legacy_exact:
                        demoted = current_row.copy()
                        if pd.notna(chosen.get("first_seen_run_id")):
                            demoted["first_seen_run_id"] = chosen[
                                "first_seen_run_id"
                            ]
                        chosen = demoted
                for column in XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS:
                    if (
                        column not in PORTCO_IDENTITY_EVIDENCE_COLUMNS
                        and pd.isna(chosen.get(column))
                        and pd.notna(current_row.get(column))
                    ):
                        chosen[column] = current_row[column]
            if (
                str(decision_id) not in current_candidate_ids
                and chosen.get("decision_method") in active_refresh_methods
                and _optional_text(chosen.get("approved_by"))
                == IDENTITY_DEDUP_APPROVED_BY
            ):
                chosen = chosen.copy()
                chosen["is_active"] = False
            out_rows.append(chosen.to_dict())
            continue
        candidate = group[group["decision_status"].eq("candidate")]
        if candidate.empty:
            out_rows.append(group.iloc[-1].to_dict())
            continue
        chosen = candidate.iloc[0].to_dict()
        if refresh_run_id:
            chosen["last_seen_run_id"] = refresh_run_id
        if str(decision_id) not in current_candidate_ids:
            chosen["is_active"] = False
        out_rows.append(chosen)
    out = pd.DataFrame(out_rows, columns=XREF_EXTERNAL_COMPANY_NAME_DECISION_COLUMNS)
    return out.drop_duplicates("external_company_name_decision_id").reset_index(drop=True)


def _revert_ambiguous_system_dedup_accepts(
    decisions: pd.DataFrame,
    newly_accepted: pd.Series,
) -> pd.DataFrame:
    """Keep ambiguous source->portco collapses as candidates for manual review."""
    if decisions.empty or newly_accepted is None or not newly_accepted.any():
        return decisions
    out = decisions
    active_accepted = (
        out["is_active"].astype("boolean").fillna(False)
        & out["decision_status"].eq("accepted")
        & out["decision_method"].isin(SYSTEM_PORTCO_DEDUP_METHODS)
    )
    if not active_accepted.any():
        return out
    conflict_sources = (
        out.loc[active_accepted]
        .groupby("source_company_id")["external_portco_id"]
        .nunique()
        .loc[lambda counts: counts.gt(1)]
        .index
    )
    if conflict_sources.empty:
        return out
    revert = newly_accepted & out["source_company_id"].isin(conflict_sources)
    if not revert.any():
        return out
    logging.warning(
        "Leaving %s ambiguous portco-dedup rows as candidates for sources %s",
        int(revert.sum()),
        sorted(map(str, conflict_sources))[:20],
    )
    out.loc[revert, "decision_status"] = "candidate"
    out.loc[revert, "approved_by"] = pd.NA
    out.loc[revert, "approved_at"] = pd.NA
    return out


def accept_high_confidence_portco_dedup_candidates(
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    """Accept only candidates that passed the versioned evidence gates."""
    if decisions is None or decisions.empty or "auto_match_eligible" not in decisions:
        return decisions
    out = decisions.copy()
    mask = (
        out["decision_status"].eq("candidate")
        & out["is_active"].astype("boolean").fillna(False)
        & out["auto_match_eligible"].astype("boolean").fillna(False)
        & out["decision_method"].isin(SYSTEM_PORTCO_DEDUP_METHODS)
    )
    if not mask.any():
        return out
    if "approved_by" not in out:
        out["approved_by"] = pd.Series(
            pd.NA,
            index=out.index,
            dtype="string",
        )
    if "approved_at" not in out:
        out["approved_at"] = pd.NaT
    out.loc[mask, "decision_status"] = "accepted"
    out.loc[mask, "approved_by"] = IDENTITY_DEDUP_APPROVED_BY
    out.loc[mask, "approved_at"] = pd.Timestamp.now(tz="UTC").tz_convert(None)
    return _revert_ambiguous_system_dedup_accepts(out, mask)


def accept_identity_applied_dedup_candidates(
    decisions: pd.DataFrame,
    company_source: pd.DataFrame,
) -> pd.DataFrame:
    """Accept dedup candidates already applied by canonical identity collapse."""
    if (
        decisions is None
        or decisions.empty
        or company_source is None
        or company_source.empty
    ):
        return decisions
    required_decisions = {
        "decision_status",
        "is_active",
        "source_company_id",
        "external_portco_id",
        "decision_method",
    }
    if not required_decisions.issubset(decisions.columns):
        return decisions
    required_source = {"source_company_id", "external_portco_id"}
    if not required_source.issubset(company_source.columns):
        return decisions
    applied = company_source[
        ["source_company_id", "external_portco_id"]
    ].dropna().copy()
    applied["source_company_id"] = applied["source_company_id"].astype(str)
    applied["external_portco_id"] = applied["external_portco_id"].astype(str)
    applied = applied[
        applied["external_portco_id"].ne(applied["source_company_id"])
    ].drop_duplicates()
    unique_counts = applied.groupby("source_company_id")[
        "external_portco_id"
    ].nunique()
    applied = applied[
        applied["source_company_id"].isin(
            unique_counts[unique_counts.eq(1)].index
        )
    ]
    if applied.empty:
        return decisions
    redirect = dict(
        zip(applied["source_company_id"], applied["external_portco_id"])
    )
    out = decisions.copy()
    source_ids = out["source_company_id"].astype("string")
    mask = (
        out["is_active"].astype("boolean").fillna(False)
        & out["decision_status"].eq("candidate")
        & out["decision_method"].eq(PORTCO_DEDUP_DECISION_METHOD)
        & source_ids.isin(redirect)
    )
    if "auto_match_eligible" in out:
        has_evidence = out["auto_match_eligible"].notna()
        mask &= (
            ~has_evidence
            | out["auto_match_eligible"].astype("boolean").fillna(False)
        )
    if mask.any():
        out.loc[mask, "external_portco_id"] = source_ids[mask].map(redirect)
        out.loc[mask, "decision_status"] = "accepted"
        out.loc[mask, "approved_by"] = IDENTITY_DEDUP_APPROVED_BY
        out.loc[mask, "approved_at"] = pd.Timestamp.now(tz="UTC").tz_convert(None)
        blank_rationale = (
            out["rationale"].isna()
            | out["rationale"].astype("string").str.strip().eq("")
        )
        out.loc[mask & blank_rationale, "rationale"] = (
            "Identity collapse under seed sponsor and stripped company name"
        )
        out = _revert_ambiguous_system_dedup_accepts(out, mask)
        source_ids = out["source_company_id"].astype("string")
    accepted_align = (
        out["is_active"].astype("boolean").fillna(False)
        & out["decision_status"].eq("accepted")
        & out["source_system"].eq(PORTCO_DEDUP_SOURCE_SYSTEM)
        & out["decision_method"].isin({
            PORTCO_DEDUP_DECISION_METHOD,
            PORTCO_EVIDENCE_DECISION_METHOD,
        })
        & source_ids.isin(redirect)
    )
    if accepted_align.any():
        aligned_ids = source_ids[accepted_align].map(redirect)
        changed = aligned_ids.astype(str).ne(
            out.loc[accepted_align, "external_portco_id"].astype(str)
        )
        if changed.any():
            logging.info(
                "Aligned %s accepted portco-dedup rows onto live canonical portco ids",
                int(changed.sum()),
            )
            out.loc[accepted_align, "external_portco_id"] = aligned_ids
    return out


def apply_accepted_portco_dedup_to_website_holdings(
    website_holdings: pd.DataFrame,
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    if website_holdings.empty:
        return website_holdings
    out = website_holdings.copy()
    if "source_portco_id" not in out:
        out["source_portco_id"] = out["structured_portco_id"]
    if decisions.empty:
        return out
    accepted = decisions[
        decisions["decision_status"].eq("accepted")
        & decisions["is_active"].astype("boolean").fillna(False)
        & decisions["source_system"].eq(PORTCO_DEDUP_SOURCE_SYSTEM)
    ]
    if accepted.empty:
        return out
    if accepted.groupby("source_company_id")["external_portco_id"].nunique().gt(1).any():
        raise ValueError("Accepted website dedup decisions map one source to multiple portcos")
    redirect = {
        str(source_id): str(canonical_id)
        for source_id, canonical_id in zip(
            accepted["source_company_id"],
            accepted["external_portco_id"],
        )
        if pd.notna(source_id) and pd.notna(canonical_id)
    }
    if "structured_portco_id" in out:
        out["structured_portco_id"] = out["structured_portco_id"].astype("string").map(
            lambda value: redirect.get(str(value), value)
        )
    return out


def build_fact_external_holding(
    pevc: pd.DataFrame | None,
    website_holdings: pd.DataFrame | None,
    dim_external_fund: pd.DataFrame,
    xref_external_fund: pd.DataFrame,
    pitchbook_holdings: pd.DataFrame | None = None,
    sponsor_scope: pd.DataFrame | None = None,
) -> pd.DataFrame:
    pevc_df = pevc if pevc is not None else pd.DataFrame()
    website_df = website_holdings if website_holdings is not None else pd.DataFrame()
    pitchbook_df = (
        pitchbook_holdings if pitchbook_holdings is not None else pd.DataFrame()
    )
    factset_rows = _factset_holdings_rows(
        pevc_df,
        dim_external_fund,
        xref_external_fund,
        sponsor_scope if sponsor_scope is not None else pd.DataFrame(),
    )
    website_rows = _website_holding_rows(website_df)
    pitchbook_rows = _pitchbook_holding_rows(
        pitchbook_df,
        sponsor_scope if sponsor_scope is not None else pd.DataFrame(),
        dim_external_fund,
    )
    if not factset_rows and not website_rows and not pitchbook_rows:
        return _empty(FACT_EXTERNAL_HOLDING_COLUMNS)

    combined = pd.DataFrame(factset_rows + website_rows + pitchbook_rows)
    combined["company_key"] = combined["company_name"].map(lambda value: _match_key(value))
    combined["source_priority"] = (
        combined["holding_source_system"].map(holding_source_priority)
    )
    combined = _lineage_group_columns(combined)
    combined = combined.sort_values(
        [
            "_lineage_sponsor_id",
            "_lineage_fund_id",
            "company_key",
            "source_priority",
            "investment_date",
            "knowledge_datetime",
        ],
        ascending=[True, True, True, True, True, False],
        na_position="last",
    )
    combined = combined.drop_duplicates("external_holding_id", keep="first")
    combined["canonical_rank"] = combined.groupby(
        ["_lineage_sponsor_id", "_lineage_fund_id", "company_key"],
        dropna=False,
    ).cumcount() + 1
    combined["canonical_holding_flag"] = combined["canonical_rank"].eq(1)
    source_sets = combined.groupby(
        ["_lineage_sponsor_id", "_lineage_fund_id", "company_key"],
        dropna=False,
    )["holding_source_system"].apply(
        lambda values: "|".join(
            sorted(
                set(values.dropna().astype(str)),
                key=holding_source_priority,
            )
        )
    ).rename("merged_source_systems")
    combined = combined.drop(columns=["contributing_source_systems"], errors="ignore").merge(
        source_sets.reset_index(),
        on=["_lineage_sponsor_id", "_lineage_fund_id", "company_key"],
        how="left",
    )
    combined["contributing_source_systems"] = combined["merged_source_systems"].fillna(combined["holding_source_system"])
    combined = combined.drop(columns=["merged_source_systems"], errors="ignore")
    combined = canonicalize_portco_ids_under_sponsor(combined)
    return combined[FACT_EXTERNAL_HOLDING_COLUMNS].reset_index(drop=True)


def canonicalize_portco_ids_under_sponsor(
    holdings: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve unambiguous source identities; preserve conflicting FactSet entities."""
    from data_access.dbtables.sponsor_study.company_identity_keys import CompanyKeyObservation, canonical_company_redirects
    if holdings.empty or "external_portco_id" not in holdings.columns:
        return holdings
    out = holdings.copy()
    columns = ["external_sponsor_id", "company_name", "external_portco_id", "factset_portco_entity_id"]
    observations = tuple(
        CompanyKeyObservation(_clean_id(row.external_sponsor_id), normalized_company_name(row.company_name),
            str(row.external_portco_id), _clean_id(row.factset_portco_entity_id) or None)
        for row in out[columns].drop_duplicates().itertuples(index=False)
        if pd.notna(row.external_portco_id) and str(row.external_portco_id).strip()
    )
    redirects = canonical_company_redirects(observations)
    if redirects:
        out["external_portco_id"] = (
            out["external_portco_id"].astype("string").map(
                lambda value: redirects.get(str(value), value)
            )
        )
    return out


def build_dim_external_portco(
    holdings: pd.DataFrame,
    *,
    refresh_run_id: str = "",
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        DIM_EXTERNAL_PORTCO_COLUMNS,
    )

    if holdings.empty:
        return _empty(DIM_EXTERNAL_PORTCO_COLUMNS)
    ranked = holdings.copy()
    ranked["_source_rank"] = ranked["holding_source_system"].map(
        holding_source_priority
    )
    ranked["_canonical_rank"] = (
        ranked["canonical_holding_flag"].astype("boolean").fillna(False)
        .map({True: 0, False: 1})
    )
    ranked = ranked.sort_values(
        [
            "external_portco_id",
            "_canonical_rank",
            "_source_rank",
            "knowledge_datetime",
        ],
        ascending=[True, True, True, False],
        na_position="last",
    )
    selected = ranked.drop_duplicates("external_portco_id").copy()
    active = holdings["exit_date"].isna().groupby(
        holdings["external_portco_id"]
    ).any()
    out = pd.DataFrame({
        "external_portco_id": selected["external_portco_id"],
        "company_name": selected["company_name"],
        "company_slug": selected["company_slug"],
        "company_country": selected["company_country"],
        "industry": selected["industry"],
        "preferred_source_system": selected["holding_source_system"],
        "first_seen_run_id": refresh_run_id or pd.NA,
        "last_seen_run_id": refresh_run_id or pd.NA,
        "is_active": selected["external_portco_id"].map(active).fillna(False),
    })
    return out[DIM_EXTERNAL_PORTCO_COLUMNS].reset_index(drop=True)


def _accepted_dedup_source_lookup(
    decisions: pd.DataFrame | None,
) -> dict[tuple[str, str], str]:
    if decisions is None or decisions.empty:
        return {}
    accepted = decisions[
        decisions["decision_status"].eq("accepted")
        & decisions["is_active"].astype("boolean").fillna(False)
        & decisions["source_system"].eq(PORTCO_DEDUP_SOURCE_SYSTEM)
    ]
    return {
        (str(canonical_id), _match_key(source_name)): str(source_id)
        for canonical_id, source_name, source_id in zip(
            accepted["external_portco_id"],
            accepted["source_company_name"],
            accepted["source_company_id"],
        )
        if pd.notna(canonical_id) and pd.notna(source_id)
    }


def build_xref_external_holding_source(
    holdings: pd.DataFrame,
    decisions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        XREF_EXTERNAL_HOLDING_SOURCE_COLUMNS,
    )

    if holdings.empty:
        return _empty(XREF_EXTERNAL_HOLDING_SOURCE_COLUMNS)
    out = holdings.copy()
    out["source_system"] = out["holding_source_system"]
    out["source_sponsor_id"] = out["factset_gp_entity_id"].combine_first(
        out["external_sponsor_id"]
    )
    out["source_fund_id"] = out["factset_fund_entity_id"].combine_first(
        out["external_fund_id"]
    )
    fallback_company_id = out["factset_portco_entity_id"].combine_first(
        out["external_portco_id"]
    )
    out["source_company_id"] = (
        out["source_company_id"].combine_first(fallback_company_id)
        if "source_company_id" in out
        else fallback_company_id
    )
    source_lookup = _accepted_dedup_source_lookup(decisions)
    website = out["holding_source_system"].eq(PORTCO_DEDUP_SOURCE_SYSTEM)
    out.loc[website, "source_company_id"] = [
        source_lookup.get(
            (str(portco_id), _match_key(company_name)),
            source_company_id,
        )
        if str(source_company_id) == str(portco_id)
        else source_company_id
        for portco_id, company_name, source_company_id in zip(
            out.loc[website, "external_portco_id"],
            out.loc[website, "company_name"],
            out.loc[website, "source_company_id"],
        )
    ]
    out["source_company_name"] = out["company_name"]
    out["source_investment_date"] = out["investment_date"]
    out["source_knowledge_datetime"] = out["knowledge_datetime"]
    out["match_confidence"] = out["source_confidence"]
    out["canonical_source_flag"] = out["canonical_holding_flag"]
    out["external_holding_source_xref_id"] = out.apply(
        lambda row: stable_id(
            "ext_holding_source",
            row["external_holding_id"],
            row["source_system"],
            row["source_holding_id"],
        ),
        axis=1,
    )
    return out[XREF_EXTERNAL_HOLDING_SOURCE_COLUMNS].drop_duplicates(
        "external_holding_source_xref_id"
    ).reset_index(drop=True)


def build_xref_external_company_source(
    holdings: pd.DataFrame,
    *,
    refresh_run_id: str = "",
    decisions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        XREF_EXTERNAL_COMPANY_SOURCE_COLUMNS,
    )

    if holdings.empty:
        return _empty(XREF_EXTERNAL_COMPANY_SOURCE_COLUMNS)
    out = holdings.copy()
    out["source_system"] = out["holding_source_system"]
    fallback_company_id = out["factset_portco_entity_id"].combine_first(
        out["external_portco_id"]
    )
    out["source_company_id"] = (
        out["source_company_id"].combine_first(fallback_company_id)
        if "source_company_id" in out
        else fallback_company_id
    )
    source_lookup = _accepted_dedup_source_lookup(decisions)
    website = out["holding_source_system"].eq(PORTCO_DEDUP_SOURCE_SYSTEM)
    out.loc[website, "source_company_id"] = [
        source_lookup.get(
            (str(portco_id), _match_key(company_name)),
            source_company_id,
        )
        if str(source_company_id) == str(portco_id)
        else source_company_id
        for portco_id, company_name, source_company_id in zip(
            out.loc[website, "external_portco_id"],
            out.loc[website, "company_name"],
            out.loc[website, "source_company_id"],
        )
    ]
    out["source_company_name"] = out["company_name"]
    out["source_company_slug"] = out["company_slug"]
    out["match_score"] = 1.0
    out["match_confidence"] = out["source_confidence"]
    out["review_status"] = "auto_matched"
    out["first_seen_run_id"] = refresh_run_id or pd.NA
    out["last_seen_run_id"] = refresh_run_id or pd.NA
    out["is_active"] = True
    out["external_company_source_xref_id"] = out.apply(
        lambda row: stable_id(
            "ext_company_source",
            row["external_portco_id"],
            row["source_system"],
            row["source_company_id"],
        ),
        axis=1,
    )
    return out[XREF_EXTERNAL_COMPANY_SOURCE_COLUMNS].drop_duplicates(
        "external_company_source_xref_id"
    ).reset_index(drop=True)


def _log_external_universe_stage(
    stage: str,
    event: str,
    started_at: float,
    **frames: pd.DataFrame | None,
) -> None:
    row_counts = {
        name: 0 if frame is None else len(frame)
        for name, frame in frames.items()
    }
    logging.info(
        "External universe stage=%s event=%s elapsed_seconds=%.2f "
        "rss_gb=%.2f rows=%s",
        stage,
        event,
        perf_counter() - started_at,
        get_process_memory_gb(),
        row_counts,
    )


@checkpointed
def add_external_universe_outputs(
    dataset: dict[str, pd.DataFrame],
    *,
    refresh_run_id: str = "",
    persisted_name_decisions: pd.DataFrame | None = None,
    identity_review_events: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    total_started = perf_counter()
    # Recompute automatic bindings from authoritative sources under current
    # sponsor boundaries. Prior runs retain the old decisions for audit.
    if persisted_name_decisions is not None and not persisted_name_decisions.empty:
        automatic = persisted_name_decisions["approved_by"].isin(
            [IDENTITY_DEDUP_APPROVED_BY, "system_score_identity_policy"])
        persisted_name_decisions = persisted_name_decisions[
            ~automatic | persisted_name_decisions["decision_status"].eq("rejected")].copy()
    out = dict(dataset)
    sponsor_list = out.get("source_sponsor_website_list", pd.DataFrame())
    _log_external_universe_stage(
        "sponsor_scope",
        "start",
        total_started,
        sponsor_list=sponsor_list,
        preqin_funds=out.get("source_preqin_fund_summary"),
        factset_funds=out.get("source_factset_pevc_fund_summary"),
    )
    scope = enrich_external_sponsor_scope(
        build_external_sponsor_scope(sponsor_list),
        preqin_funds=out.get("source_preqin_fund_summary", pd.DataFrame()),
        factset_funds=_factset_pevc_funds(out),
    )
    _log_external_universe_stage(
        "sponsor_scope", "complete", total_started, scope=scope
    )
    _log_external_universe_stage(
        "dim_fund", "start", total_started, scope=scope
    )
    dim_fund = select_flagship_external_funds(
        build_dim_external_fund(scope, out.get("source_preqin_fund_summary"))
    )
    _log_external_universe_stage(
        "dim_fund", "complete", total_started, dim_fund=dim_fund
    )
    _log_external_universe_stage(
        "fund_xref", "start", total_started, dim_fund=dim_fund
    )
    xref_fund = build_xref_external_fund_source(dim_fund, _factset_pevc_funds(out))
    dim_fund = _apply_factset_xref_to_dim_fund(dim_fund, xref_fund)
    _log_external_universe_stage(
        "fund_xref",
        "complete",
        total_started,
        dim_fund=dim_fund,
        xref_fund=xref_fund,
    )

    _log_external_universe_stage(
        "factset_holdings",
        "start",
        total_started,
        source_factset_holdings=out.get(
            "source_factset_pevc_portco_investment"
        ),
        dim_fund=dim_fund,
        xref_fund=xref_fund,
    )
    factset_holdings = build_fact_external_holding(
        out.get("source_factset_pevc_portco_investment"),
        None,
        dim_fund,
        xref_fund,
        sponsor_scope=scope,
    )
    _log_external_universe_stage(
        "factset_holdings",
        "complete",
        total_started,
        factset_holdings=factset_holdings,
    )
    _log_external_universe_stage(
        "website_holdings",
        "start",
        total_started,
        website_candidates=out.get("source_sponsor_site_holding_candidate"),
        factset_holdings=factset_holdings,
    )
    website_holdings = build_external_website_holdings(
        out.get("source_sponsor_site_holding_candidate"),
        scope,
        dim_fund,
        factset_holdings,
    )
    dedup_candidates = build_website_factset_dedup_candidates(
        factset_holdings,
        website_holdings,
        refresh_run_id=refresh_run_id,
    )
    merged_decisions = merge_external_company_name_decisions(
        persisted_name_decisions if persisted_name_decisions is not None else pd.DataFrame(),
        dedup_candidates,
        refresh_run_id=refresh_run_id,
        refreshed_methods={
            PORTCO_DEDUP_DECISION_METHOD,
            PORTCO_EVIDENCE_DECISION_METHOD,
        },
    )
    merged_decisions = accept_high_confidence_portco_dedup_candidates(
        merged_decisions
    )
    source_website_holdings = website_holdings.copy()
    upstream_decisions = merged_decisions
    if identity_review_events is not None and not identity_review_events.empty:
        from data_access.dbtables.sponsor_study.identity_review import latest_review_events

        reviewed_keys = {
            (row.external_sponsor_id, getattr(row, side + "_source"), getattr(row, side + "_company_id"))
            for row in latest_review_events(identity_review_events).itertuples(index=False)
            for side in ("left", "right")
        }
        # Keep reviewed source IDs intact until the effective ledger is applied.
        upstream_decisions = merged_decisions[[
            tuple(row) not in reviewed_keys for row in merged_decisions[
                ["external_sponsor_id", "source_system", "source_company_id"]
            ].itertuples(index=False, name=None)
        ]]
    website_holdings = apply_accepted_portco_dedup_to_website_holdings(
        website_holdings,
        upstream_decisions,
    )
    out["xref_external_company_name_decision"] = merged_decisions
    _log_external_universe_stage(
        "website_holdings",
        "complete",
        total_started,
        website_holdings=website_holdings,
    )
    out["source_external_sponsor_website_holding"] = source_website_holdings
    _log_external_universe_stage(
        "canonical_holdings",
        "start",
        total_started,
        source_factset_holdings=out.get(
            "source_factset_pevc_portco_investment"
        ),
        website_holdings=website_holdings,
        pitchbook_holdings=out.get("source_pitchbook_holding"),
    )
    holdings = build_fact_external_holding(
        out.get("source_factset_pevc_portco_investment"),
        website_holdings,
        dim_fund,
        xref_fund,
        pitchbook_holdings=out.get("source_pitchbook_holding"),
        sponsor_scope=scope,
    )
    _log_external_universe_stage(
        "canonical_holdings",
        "complete",
        total_started,
        holdings=holdings,
    )
    out["source_external_sponsor_scope"] = scope
    out["dim_external_fund"] = dim_fund
    out["xref_external_fund_source"] = xref_fund
    out["fact_external_holding"] = holdings
    _log_external_universe_stage(
        "dim_portco", "start", total_started, holdings=holdings
    )
    out["dim_external_portco"] = build_dim_external_portco(
        holdings,
        refresh_run_id=refresh_run_id,
    )
    _log_external_universe_stage(
        "dim_portco",
        "complete",
        total_started,
        dim_portco=out["dim_external_portco"],
    )
    _log_external_universe_stage(
        "holding_xref", "start", total_started, holdings=holdings
    )
    out["xref_external_holding_source"] = (
        build_xref_external_holding_source(holdings, merged_decisions)
    )
    _log_external_universe_stage(
        "holding_xref",
        "complete",
        total_started,
        holding_xref=out["xref_external_holding_source"],
    )
    _log_external_universe_stage(
        "company_xref", "start", total_started, holdings=holdings
    )
    out["xref_external_company_source"] = (
        build_xref_external_company_source(
            holdings,
            refresh_run_id=refresh_run_id,
            decisions=merged_decisions,
        )
    )
    out["xref_external_company_name_decision"] = (
        accept_identity_applied_dedup_candidates(
            out.get("xref_external_company_name_decision", pd.DataFrame()),
            out["xref_external_company_source"],
        )
    )
    _log_external_universe_stage(
        "company_xref",
        "complete",
        total_started,
        company_xref=out["xref_external_company_source"],
    )
    _log_external_universe_stage(
        "factset_xref", "start", total_started, holdings=holdings
    )
    out["xref_external_portco_factset"] = build_external_portco_factset_xref(holdings)
    _log_external_universe_stage(
        "factset_xref",
        "complete",
        total_started,
        factset_xref=out["xref_external_portco_factset"],
    )
    return out


EXTERNAL_DELTA_BASE_TABLES = (
    "source_factset_pevc_fund_summary",
    "source_factset_pevc_portco_investment",
    "source_pitchbook_holding",
    "source_preqin_fund_summary",
    "source_preqin_dry_powder_macro",
    "source_preqin_buyout_deal_flow",
    "fact_external_fund_performance_snapshot",
    "source_external_sponsor_website_holding",
    "dim_external_sponsor",
    "dim_external_fund",
    "dim_external_portco",
    "fact_external_holding",
    "xref_external_fund_source",
    "xref_external_holding_source",
    "xref_external_company_source",
    "xref_external_company_name_decision",
    "xref_external_manual_entity_link",
    "xref_pitchbook_sponsor_name",
    "xref_external_company_internal_id",
    "xref_external_company_debt_identifier",
    "xref_external_company_capital_stack",
    "xref_external_company_findox",
    "xref_external_company_findox_instrument",
    "source_benchmark_v31_portco_row",
    "source_benchmark_v31_sponsor_xref",
    "source_benchmark_v31_comment",
    "source_benchmark_v31_holding_xref",
    "source_external_debt_issuer_bridge",
    "fact_external_debt_feature_snapshot",
    "source_external_financial_snapshot",
    "fact_external_financial_candidate",
    "fact_external_entity_lineage",
    "source_external_market_trading_snapshot",
    "source_external_debt_instrument_snapshot",
    "source_external_factset_dcs_debt_detail",
    "source_external_findox_document_index",
    "meta_external_refresh_run",
    "validation_external_enrichment",
    "validation_external_source_reconciliation",
    "company_identity_review_queue",
)


def external_delta_table_contracts() -> dict[str, pd.DataFrame]:
    """Schema-stable empty frames so Delta DDL can be created before the first data refresh."""
    from data_access.dbtables.sponsor_study.external_prod_contracts import (
        external_prod_table_contracts,
    )

    return external_prod_table_contracts()


def merge_external_delta_contracts(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    contracts = external_delta_table_contracts()
    out = dict(dataset)
    for name in EXTERNAL_DELTA_BASE_TABLES:
        frame = out.get(name)
        if frame is None or frame.empty or not len(frame.columns):
            out[name] = contracts[name]
    return out
