from difflib import SequenceMatcher

import pandas as pd

from data_access.dbtables.sponsor_study.canonical import stable_id
from data_access.dbtables.sponsor_study.fund_vendor import _match_key


REVIEWED_FUND_XREF_STATUSES = {"auto_matched", "manual_matched", "needs_review"}

STRUCTURED_FUND_SCOPE_COLUMNS = [
    "fund_id",
    "sponsor_id",
    "sponsor_name_seed",
    "fund_name_seed",
    "vintage_seed",
    "source_system",
    "source_fund_id",
    "source_fund_source_table",
    "source_sponsor_id",
    "review_status",
    "match_confidence",
    "preferred_metric_source_system",
]

STRUCTURED_INVESTMENT_COLUMNS = [
    "structured_investment_id",
    "fund_id",
    "sponsor_id",
    "sponsor_name",
    "fund_name",
    "vintage",
    "structured_portco_id",
    "benchmark_portco_id",
    "factset_gp_entity_id",
    "factset_fund_entity_id",
    "factset_portco_entity_id",
    "company_name",
    "company_country",
    "industry",
    "investment_date",
    "exit_date",
    "entry_type",
    "transaction_close_date",
    "round_of_funding",
    "total_investment_amount",
    "investment_description",
    "amount_invested_by_sponsor",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "source_system",
    "source_confidence",
    "match_method",
]

PEVC_FUND_MERGE_COLUMNS = [
    "factset_gp_entity_id",
    "sponsor_name_factset",
    "factset_fund_entity_id",
    "fund_name_factset",
    "transaction_fund_entity_id",
    "transaction_fund_name",
    "vintage",
]


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _clean_id(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "na", "n/a"} else text


def _numeric_year(value):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.NA if pd.isna(value) else int(value)


def _numeric_year_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _ratio(left, right) -> float:
    left_key = _match_key(left).replace("_", " ")
    right_key = _match_key(right).replace("_", " ")
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _match_tokens(value) -> set[str]:
    return {token for token in _match_key(value).split("_") if token}


def build_structured_fund_scope(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    funds = dataset.get("dim_fund", pd.DataFrame())
    sponsors = dataset.get("dim_sponsor", pd.DataFrame())
    xref = dataset.get("xref_fund_alias_name_map", pd.DataFrame())
    if funds.empty:
        return _empty(STRUCTURED_FUND_SCOPE_COLUMNS)

    seed = funds.merge(sponsors[["sponsor_id", "sponsor_name"]], on="sponsor_id", how="left")
    seed = seed.rename(columns={
        "sponsor_name": "sponsor_name_seed",
        "fund_name": "fund_name_seed",
        "vintage": "vintage_seed",
    })
    seed = seed[["fund_id", "sponsor_id", "sponsor_name_seed", "fund_name_seed", "vintage_seed"]].drop_duplicates()

    if xref.empty:
        for col in STRUCTURED_FUND_SCOPE_COLUMNS:
            if col not in seed:
                seed[col] = pd.NA
        return seed[STRUCTURED_FUND_SCOPE_COLUMNS].reset_index(drop=True)

    reviewed = xref[
        xref["review_status"].isin(REVIEWED_FUND_XREF_STATUSES)
        & xref["source_entity_id"].notna()
    ].copy()
    reviewed = reviewed.rename(columns={
        "source_entity_id": "source_fund_id",
        "source_entity_table": "source_fund_source_table",
    })
    reviewed_cols = [
        "fund_id",
        "source_system",
        "source_fund_id",
        "source_fund_source_table",
        "source_sponsor_id",
        "review_status",
        "match_confidence",
        "preferred_metric_source_system",
    ]
    out = seed.merge(reviewed[reviewed_cols], on="fund_id", how="left")
    return out[STRUCTURED_FUND_SCOPE_COLUMNS].drop_duplicates().reset_index(drop=True)


def _fund_match_candidates(pevc_row: pd.Series, scope: pd.DataFrame) -> pd.DataFrame:
    candidates = scope.copy()
    if candidates.empty:
        return candidates

    pevc_vintage = _numeric_year(pevc_row.get("vintage"))
    if pd.notna(pevc_vintage):
        vintage_match = pd.to_numeric(candidates["vintage_seed"], errors="coerce").eq(pevc_vintage)
        if vintage_match.any():
            candidates = candidates[vintage_match].copy()

    pevc_fund_name = pevc_row.get("transaction_fund_name")
    if pd.isna(pevc_fund_name) or not str(pevc_fund_name).strip():
        pevc_fund_name = pevc_row.get("fund_name_factset")
    pevc_sponsor_name = pevc_row.get("sponsor_name_factset")

    candidates["fund_name_similarity"] = candidates["fund_name_seed"].map(lambda value: _ratio(pevc_fund_name, value))
    candidates["sponsor_name_similarity"] = candidates["sponsor_name_seed"].map(lambda value: _ratio(pevc_sponsor_name, value))
    candidates["vintage_match_flag"] = pd.to_numeric(candidates["vintage_seed"], errors="coerce").eq(pevc_vintage)
    candidates["match_score"] = (
        candidates["fund_name_similarity"] * 0.70
        + candidates["sponsor_name_similarity"] * 0.20
        + candidates["vintage_match_flag"].astype(float) * 0.10
    )
    return candidates.sort_values(["match_score", "fund_name_similarity", "sponsor_name_similarity"], ascending=False)


def _keyed_pevc(pevc: pd.DataFrame) -> pd.DataFrame:
    out = pevc.copy()
    transaction_fund_name = out["transaction_fund_name"].astype("string").str.strip()
    out["_pevc_fund_name"] = out["transaction_fund_name"].where(transaction_fund_name.notna() & transaction_fund_name.ne(""), out["fund_name_factset"])
    fund_key_map = {name: _match_key(name) for name in out["_pevc_fund_name"].dropna().drop_duplicates()}
    sponsor_key_map = {name: _match_key(name) for name in out["sponsor_name_factset"].dropna().drop_duplicates()}
    out["_pevc_fund_key"] = out["_pevc_fund_name"].map(fund_key_map).fillna("unknown")
    out["_pevc_sponsor_key"] = out["sponsor_name_factset"].map(sponsor_key_map).fillna("unknown")
    out["_pevc_fund_tokens"] = out["_pevc_fund_key"].map(lambda value: set(str(value).split("_")) - {""})
    out["_pevc_sponsor_tokens"] = out["_pevc_sponsor_key"].map(lambda value: set(str(value).split("_")) - {""})
    out["_pevc_vintage"] = _numeric_year_series(out["vintage"])
    out["_pevc_fund_merge_id"] = _raw_pevc_fund_merge_id(out)
    out["_pevc_fund_match_id"] = out["_pevc_fund_merge_id"]
    return out


def _keyed_scope(scope: pd.DataFrame) -> pd.DataFrame:
    out = scope.copy()
    out["_scope_fund_key"] = out["fund_name_seed"].map(_match_key)
    out["_scope_sponsor_key"] = out["sponsor_name_seed"].map(_match_key)
    out["_scope_vintage"] = _numeric_year_series(out["vintage_seed"])
    return out


def _raw_pevc_fund_merge_id(df: pd.DataFrame) -> pd.Series:
    key = pd.Series("", index=df.index, dtype="string")
    for col in PEVC_FUND_MERGE_COLUMNS:
        key = key + "|" + df[col].astype("string").fillna("")
    return key


def _exact_pevc_fund_matches(pevc_funds: pd.DataFrame, keyed_scope: pd.DataFrame) -> pd.DataFrame:
    exact = pevc_funds.merge(
        keyed_scope,
        left_on=["_pevc_fund_key", "_pevc_vintage"],
        right_on=["_scope_fund_key", "_scope_vintage"],
        how="inner",
    )
    if exact.empty:
        return exact
    exact["fund_name_similarity"] = 1.0
    exact["sponsor_name_similarity"] = exact.apply(
        lambda row: _ratio(row.get("sponsor_name_factset"), row.get("sponsor_name_seed")),
        axis=1,
    )
    exact["vintage_match_flag"] = True
    exact["match_score"] = 0.80 + exact["sponsor_name_similarity"] * 0.20
    return (
        exact.sort_values(["_pevc_fund_match_id", "match_score"], ascending=[True, False])
        .drop_duplicates("_pevc_fund_match_id")
        .reset_index(drop=True)
    )


def _match_pevc_funds_to_scope(pevc: pd.DataFrame, scope: pd.DataFrame) -> pd.DataFrame:
    keyed_scope = _keyed_scope(scope)
    scope_fund_tokens = set().union(*keyed_scope["fund_name_seed"].map(_match_tokens).tolist())
    scope_sponsor_tokens = set().union(*keyed_scope["sponsor_name_seed"].map(_match_tokens).tolist())
    scope_vintages = set(keyed_scope["_scope_vintage"].dropna().astype(int).tolist())
    pevc_funds = pevc[[
        "_pevc_fund_match_id",
        "_pevc_fund_name",
        "_pevc_fund_key",
        "_pevc_sponsor_key",
        "_pevc_fund_tokens",
        "_pevc_sponsor_tokens",
        "_pevc_vintage",
        "factset_gp_entity_id",
        "sponsor_name_factset",
        "factset_fund_entity_id",
        "fund_name_factset",
        "transaction_fund_entity_id",
        "transaction_fund_name",
        "vintage",
    ]].drop_duplicates("_pevc_fund_match_id")

    exact = _exact_pevc_fund_matches(pevc_funds, keyed_scope)
    matched_ids = set(exact["_pevc_fund_match_id"]) if not exact.empty else set()

    fuzzy_rows = []
    unmatched = pevc_funds[~pevc_funds["_pevc_fund_match_id"].isin(matched_ids)]
    if scope_vintages:
        unmatched = unmatched[unmatched["_pevc_vintage"].isin(scope_vintages)]
    if not unmatched.empty and (scope_fund_tokens or scope_sponsor_tokens):
        fund_overlap = unmatched["_pevc_fund_tokens"].map(lambda tokens: len(tokens & scope_fund_tokens))
        sponsor_overlap = unmatched["_pevc_sponsor_tokens"].map(lambda tokens: len(tokens & scope_sponsor_tokens))
        unmatched = unmatched[(fund_overlap >= 2) | (sponsor_overlap >= 1)]
    for _, pevc_fund in unmatched.iterrows():
        candidates = _fund_match_candidates(pevc_fund, scope)
        if candidates.empty:
            continue
        best = candidates.iloc[0]
        if best.get("match_score", 0.0) < 0.72 or best.get("fund_name_similarity", 0.0) < 0.60:
            continue
        fuzzy_rows.append({
            **pevc_fund.to_dict(),
            **best.to_dict(),
        })

    fuzzy = pd.DataFrame(fuzzy_rows)
    matches = pd.concat([exact, fuzzy], ignore_index=True)
    if matches.empty:
        return pd.DataFrame(columns=["_pevc_fund_match_id", *STRUCTURED_FUND_SCOPE_COLUMNS, "match_score", "fund_name_similarity"])
    keep_cols = [
        "_pevc_fund_match_id",
        *STRUCTURED_FUND_SCOPE_COLUMNS,
        "match_score",
        "fund_name_similarity",
    ]
    return matches[keep_cols].drop_duplicates("_pevc_fund_match_id").reset_index(drop=True)


def _factset_pevc_scope(scope: pd.DataFrame) -> pd.DataFrame:
    if scope.empty or "source_system" not in scope:
        return _empty(STRUCTURED_FUND_SCOPE_COLUMNS)
    factset_scope = scope[
        scope["source_system"].eq("factset_pevc")
        & scope["source_fund_id"].notna()
    ].copy()
    if factset_scope.empty:
        return _empty(STRUCTURED_FUND_SCOPE_COLUMNS)
    factset_scope["_factset_fund_entity_id"] = factset_scope["source_fund_id"].map(_clean_id)
    factset_scope = factset_scope[factset_scope["_factset_fund_entity_id"].ne("")]
    return factset_scope.drop_duplicates(["fund_id", "_factset_fund_entity_id"]).reset_index(drop=True)


def _match_pevc_investments_from_xref(pevc: pd.DataFrame, scope: pd.DataFrame) -> pd.DataFrame:
    factset_scope = _factset_pevc_scope(scope)
    if factset_scope.empty:
        return pd.DataFrame()
    pevc = pevc.copy()
    pevc["_factset_fund_entity_id"] = pevc["factset_fund_entity_id"].map(_clean_id)
    transaction_ids = pevc["transaction_fund_entity_id"].map(_clean_id)
    pevc["_transaction_fund_entity_id"] = transaction_ids
    lookup_scope = factset_scope.rename(columns={"_factset_fund_entity_id": "_xref_factset_fund_entity_id"})
    direct = pevc.merge(
        lookup_scope,
        left_on="_factset_fund_entity_id",
        right_on="_xref_factset_fund_entity_id",
        how="inner",
    )
    transaction = pevc[pevc["_transaction_fund_entity_id"].ne("")].merge(
        lookup_scope,
        left_on="_transaction_fund_entity_id",
        right_on="_xref_factset_fund_entity_id",
        how="inner",
    )
    if transaction.empty:
        matched = direct
    elif direct.empty:
        matched = transaction
    else:
        matched = pd.concat([direct, transaction], ignore_index=True)
    if matched.empty:
        return matched
    matched["match_score"] = matched["match_confidence"].map({"high": 1.0, "medium": 0.80}).fillna(0.72)
    matched["fund_name_similarity"] = matched.apply(
        lambda row: _ratio(row.get("fund_name_factset"), row.get("fund_name_seed")),
        axis=1,
    )
    return matched.drop_duplicates(["fund_id", "factset_portco_entity_id", "pevc_trans_deal_id"]).reset_index(drop=True)


def build_structured_fund_portco_investment(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    scope = dataset.get("structured_fund_scope", pd.DataFrame())
    pevc = dataset.get("source_factset_pevc_portco_investment", pd.DataFrame())
    xref = dataset.get("xref_portco_internal_ids", pd.DataFrame())
    if scope.empty or pevc.empty:
        return _empty(STRUCTURED_INVESTMENT_COLUMNS)

    factset_to_benchmark = {}
    if not xref.empty and "factset_entity_id" in xref:
        ids = xref[["portco_id", "factset_entity_id"]].dropna().drop_duplicates()
        factset_to_benchmark = dict(zip(ids["factset_entity_id"].map(_clean_id), ids["portco_id"]))

    matched_pevc = _match_pevc_investments_from_xref(pevc, scope)
    match_method = "factset_pevc_fund_alias_xref"
    if matched_pevc.empty:
        pevc_funds = _keyed_pevc(pevc[PEVC_FUND_MERGE_COLUMNS].drop_duplicates().reset_index(drop=True))
        fund_matches = _match_pevc_funds_to_scope(pevc_funds, scope)
        if fund_matches.empty:
            return _empty(STRUCTURED_INVESTMENT_COLUMNS)
        matched_funds = pevc_funds.merge(fund_matches, on="_pevc_fund_match_id", how="inner")
        pevc = pevc.copy()
        pevc["_pevc_fund_merge_id"] = _raw_pevc_fund_merge_id(pevc)
        matched_pevc = pevc.merge(
            matched_funds[["_pevc_fund_merge_id", *STRUCTURED_FUND_SCOPE_COLUMNS, "match_score", "fund_name_similarity"]],
            on="_pevc_fund_merge_id",
            how="inner",
        )
        match_method = "factset_pevc_fund_name_vintage_scope"

    rows = []
    for _, pevc_row in matched_pevc.iterrows():
        factset_portco_id = _clean_id(pevc_row.get("factset_portco_entity_id"))
        benchmark_portco_id = factset_to_benchmark.get(factset_portco_id, pd.NA)
        structured_portco_id = stable_id("portco_struct", "factset_pevc", factset_portco_id or pevc_row.get("portco_name_factset"))
        structured_investment_id = stable_id(
            "struct_inv",
            pevc_row.get("fund_id"),
            factset_portco_id,
            pevc_row.get("pevc_trans_deal_id"),
            pevc_row.get("entry_date"),
        )

        rows.append({
            "structured_investment_id": structured_investment_id,
            "fund_id": pevc_row.get("fund_id"),
            "sponsor_id": pevc_row.get("sponsor_id"),
            "sponsor_name": pevc_row.get("sponsor_name_seed"),
            "fund_name": pevc_row.get("fund_name_seed"),
            "vintage": pevc_row.get("vintage_seed"),
            "structured_portco_id": structured_portco_id,
            "benchmark_portco_id": benchmark_portco_id,
            "factset_gp_entity_id": pevc_row.get("factset_gp_entity_id"),
            "factset_fund_entity_id": pevc_row.get("factset_fund_entity_id"),
            "factset_portco_entity_id": pevc_row.get("factset_portco_entity_id"),
            "company_name": pevc_row.get("portco_name_factset"),
            "company_country": pevc_row.get("portco_country"),
            "industry": pevc_row.get("portco_industry"),
            "investment_date": pevc_row.get("entry_date"),
            "exit_date": pevc_row.get("exit_date"),
            "entry_type": pevc_row.get("entry_type"),
            "transaction_close_date": pevc_row.get("transaction_close_date"),
            "round_of_funding": pevc_row.get("round_of_funding"),
            "total_investment_amount": pevc_row.get("total_investment_amount"),
            "investment_description": pevc_row.get("investment_description"),
            "amount_invested_by_sponsor": pevc_row.get("amount_invested_by_sponsor"),
            "transaction_fund_entity_id": pevc_row.get("transaction_fund_entity_id"),
            "transaction_fund_name": pevc_row.get("transaction_fund_name"),
            "source_system": "factset_pevc",
            "source_confidence": "high" if pevc_row.get("match_score", 0.0) >= 0.86 else "medium",
            "match_method": match_method,
        })

    if not rows:
        return _empty(STRUCTURED_INVESTMENT_COLUMNS)
    out = pd.DataFrame(rows)
    return out[STRUCTURED_INVESTMENT_COLUMNS].drop_duplicates("structured_investment_id").reset_index(drop=True)


def add_structured_universe_outputs(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    out["structured_fund_scope"] = build_structured_fund_scope(out)
    out["structured_fund_portco_investment"] = build_structured_fund_portco_investment(out)
    return out
