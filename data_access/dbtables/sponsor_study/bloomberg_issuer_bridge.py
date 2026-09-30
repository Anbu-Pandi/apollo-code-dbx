"""Bridge admitted Bloomberg company identifiers into `dbo.debt_features`.

Historical runs use the dated issuer master for both name matching and corporate paths.
Only the company's own names and admitted downstream issuer names corroborate a ticker
when a debt row lacks borrower IDs. A stale ticker alone cannot establish a debt link.
Parent and sibling names do not establish the portfolio company's borrower identity.
"""


from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd
from data_access.dbtables.sponsor_study.refresh_runtime import active_runtime, checkpointed, fingerprint

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.company_aliases import (
    REVIEW_MATCH_MIN_SCORE,
    _company_key,
    score_company_name_with_aliases,
)
from data_access.dbtables.sponsor_study.debt_owner_bindings import (
    DebtIdentifierOwner,
    DebtIssuerNameOwner,
    admitted_identifier_owners,
    bind_debt_rows,
)
from data_access.dbtables.sponsor_study.internal_joins import (
    _asof_cutoff_predicate,
    _quote_id_values,
    fetch_table_columns,
)
from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
    bbg_company_id_sql,
    normalize_bbg_company_id,
    normalize_bbg_id_series,
)

BLOOMBERG_ISSUER_MASTER_VIEW = "dbo.v_bond_issuer_master"
BLOOMBERG_BRIDGE_TIER = "debt_tier2_bbg_issuer_master"
BLOOMBERG_BRIDGE_METHOD = "bloomberg_issuer_master_name_bridge"
CAPITAL_STACK_EXPANSION_METHOD = "bloomberg_capital_stack_descendant"
MAX_CAPITAL_STACK_DESCENDANTS = 100


@dataclass(frozen=True)
class BridgeKey:
    """One join path from an issuer-master column into `debt_features` columns."""

    view_column: str
    debt_columns: tuple[str, ...]
    label: str


# Only identifiers of the company itself. Parent and ultimate-parent columns are excluded
# deliberately: joining on them attributes a corporate parent's entire debt stack to one
# portfolio company, and Bloomberg's parent is frequently a former owner. Paycor HCM carries
# `ult_parent_corp_ticker = PAYX`, Cole Haan carries `NKE`, and every Apax company shares
# ultimate parent 313082, so a single ticker pulled tens of thousands of unrelated
# instruments. Company ids are ordered ahead of the corporate ticker because a ticker can
# still span unrelated issuers -- SAFELN covers Safety-Kleen Europe and Safestore Holdings --
# but the ticker carries the loan coverage: 100% of loan rows against 9.8% for id_bb_company.
BRIDGE_KEYS: tuple[BridgeKey, ...] = (
    BridgeKey("id_bb_company", ("id_bb_company",), "bbg_company_id"),
    BridgeKey("id_bb_global_company", ("id_bb_global_company",), "bbg_global_company_id"),
    BridgeKey("obligor_bbid", ("id_bb_company",), "bbg_obligor_id"),
    BridgeKey("company_corp_ticker", ("ticker", "company_corp_ticker"), "bbg_corp_ticker"),
)

# A company's own names only. Parent and ultimate-parent names are deliberately excluded:
# the view holds a row per company, so a parent is reached through its own row instead.
BLOOMBERG_NAME_FIELDS: tuple[str, ...] = (
    "long_comp_name",
    "company_legal_name",
    "alternate_company_name",
    "previous_company_names",
    "lei_name",
    "other_lei_name",
)

BLOOMBERG_CONTEXT_FIELDS: tuple[str, ...] = (
    "long_parent_comp_name",
    "long_ult_parent_comp_name",
    "company_to_parent_relationship",
)

CAPITAL_STACK_ADMISSION_FIELDS: tuple[str, ...] = (
    "company_status",
    "acquired_by_parent",
    "knowledge_date",
    "id_bb_global_obligor_name",
)

CAPITAL_STACK_HIERARCHY_FIELDS: tuple[str, ...] = (
    "id_bb_parent_co",
    "id_bb_ultimate_parent_co",
    "parent_obligor_id",
    "ultimate_obligor_id",
)

BRIDGE_MATCH_COLUMNS = [
    "external_company_id",
    "company_name",
    "matched_bbg_name",
    "matched_name_field",
    "bbg_primary_name",
    "bbg_parent_name",
    "bbg_ult_parent_name",
    "bbg_relationship",
    "match_score",
    "exact_match_flag",
    "sponsor_corroborated",
    "bridge_key_label",
    "candidate_count",
    *[key.view_column for key in BRIDGE_KEYS],
    *CAPITAL_STACK_ADMISSION_FIELDS,
    *CAPITAL_STACK_HIERARCHY_FIELDS,
]

CAPITAL_STACK_COLUMNS = [
    "external_company_id",
    "company_name",
    "original_seed_bbg_company_id",
    "seed_bbg_company_id",
    "seed_bbg_name",
    "legacy_seed_promoted",
    "capital_stack_bbg_company_id",
    "capital_stack_name",
    "capital_stack_relationship",
    "capital_stack_rank",
    "capital_stack_match_method",
    "capital_stack_descendant_count",
    "capital_stack_expansion_truncated",
    "exact_match_flag",
    "match_score",
    "path_depth",
    "admitted_corporate_path",
    "resolved_obligor_company_id",
    "resolved_obligor_company_name",
    "obligor_inside_stack",
    "stack_eligibility",
    "stack_rejection_reason",
    *BLOOMBERG_NAME_FIELDS,
    *BLOOMBERG_CONTEXT_FIELDS,
    *CAPITAL_STACK_ADMISSION_FIELDS,
    *[key.view_column for key in BRIDGE_KEYS],
    *CAPITAL_STACK_HIERARCHY_FIELDS,
]

_DICT_VALUE_RE = re.compile(r"'\s*:\s*'([^']*)'")
_NUMERIC_TOKEN_RE = re.compile(r"^\d+$")
_EMPTY_TEXT = {"", "[]", "none", "nan", "null"}


def parse_bloomberg_names(value: object) -> tuple[str, ...]:
    """Pull company names out of Bloomberg's two encoded multi-value formats.

    Alias columns arrive either semicolon-delimited behind numeric control tokens
    (`;2;1;1;1;Varsity Spirit;`) or as a repr of dict rows
    (`[{'LEI_PREVIOUS_NAME': 'Riddell Sports Inc'}]`). Read raw, both normalize to strings
    like `2 1 1 1 varsity spirit` that match nothing.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    text = str(value).strip()
    if text.lower() in _EMPTY_TEXT:
        return ()
    if text.startswith("["):
        found = _DICT_VALUE_RE.findall(text)
        return tuple(dict.fromkeys(name.strip() for name in found if name.strip()))
    if ";" in text:
        return tuple(dict.fromkeys(
            token for token in (part.strip() for part in text.split(";"))
            if token and not _NUMERIC_TOKEN_RE.match(token)
        ))
    return (text,)


def fetch_debt_identifier_universe(
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> dict[str, set[str]]:
    """Distinct values of every bridge join column present in `debt_features`."""
    available = {col.lower(): col for col in fetch_table_columns(table_name, app=app)}
    debt_columns = sorted({
        col for key in BRIDGE_KEYS for col in key.debt_columns if col in available
    })
    if not debt_columns:
        return {}
    date_col = available.get("asof_date", "asof_date")
    selects = "\nUNION ALL\n".join(
        f"SELECT DISTINCT '{col}' AS id_column, {bbg_company_id_sql(available[col])} AS id_value "
        f"FROM base WHERE {available[col]} IS NOT NULL"
        for col in debt_columns
    )
    sql = f"""
WITH base AS (
    SELECT *
    FROM {table_name}
    WHERE cast({date_col} as date) = (
        SELECT max(cast({date_col} as date)) FROM {table_name}
        {_asof_cutoff_predicate(date_col, asof_cutoff)}
    )
)
{selects}
"""
    frame = readdfsql(sql, app=app)
    if frame.empty:
        return {}
    return {
        str(column): set(group["id_value"].dropna().astype(str))
        for column, group in frame.groupby("id_column")
    }


def _issuer_master_source(view_name: str, asof_cutoff: str | pd.Timestamp | None) -> str:
    if asof_cutoff is None:
        return view_name
    if not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+v_bond_issuer_master", view_name):
        raise ValueError("Dated issuer lookup requires the standard issuer-master view")
    cutoff = pd.Timestamp(asof_cutoff)
    if pd.isna(cutoff):
        raise ValueError("Issuer lookup cutoff must be a valid date")
    function = view_name.removesuffix("v_bond_issuer_master") + "f_get_bond_issuer_master"
    return f"{function}(DATE '{cutoff.date().isoformat()}')"


@checkpointed
def fetch_bloomberg_issuer_universe(
    debt_identifiers: dict[str, set[str]],
    *,
    app: str = "databricks",
    view_name: str = BLOOMBERG_ISSUER_MASTER_VIEW,
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Issuer-master rows whose identifiers appear somewhere in the debt universe.

    The view holds 8M companies, so filtering it by name scans all of them; the debt
    universe is three orders of magnitude smaller, so restricting by identifier first turns
    a multi-minute scan into a few seconds.
    """
    if not debt_identifiers:
        return pd.DataFrame()
    clauses = []
    for key in BRIDGE_KEYS:
        values: set[str] = set()
        for debt_column in key.debt_columns:
            values |= debt_identifiers.get(debt_column, set())
        if key.view_column in {"id_bb_company", "obligor_bbid"}:
            values = {
                normalized
                for value in values
                if (normalized := normalize_bbg_company_id(value)) is not None
            }
        if values:
            clauses.append(
                f"{bbg_company_id_sql(key.view_column)} IN ({_quote_id_values(sorted(values))})"
            )
    if not clauses:
        return pd.DataFrame()
    columns = ", ".join(
        f"{bbg_company_id_sql(col)} AS {col}"
        for col in [*BLOOMBERG_NAME_FIELDS, *BLOOMBERG_CONTEXT_FIELDS,
                    *CAPITAL_STACK_ADMISSION_FIELDS,
                    *[key.view_column for key in BRIDGE_KEYS],
                    *CAPITAL_STACK_HIERARCHY_FIELDS]
    )
    logging.info("Fetching Bloomberg issuer master across %s identifier families", len(clauses))
    where_clause = "\n   OR ".join(clauses)
    source = _issuer_master_source(view_name, asof_cutoff)
    return readdfsql(
        f"SELECT DISTINCT {columns}\nFROM {source}\nWHERE {where_clause}",
        app=app,
    )


def build_bloomberg_name_index(universe: pd.DataFrame) -> pd.DataFrame:
    """One row per (Bloomberg name, company), keyed for matching against external names."""
    identifier_columns = [key.view_column for key in BRIDGE_KEYS]
    metadata_columns = [
        *BLOOMBERG_CONTEXT_FIELDS,
        *CAPITAL_STACK_ADMISSION_FIELDS,
        *CAPITAL_STACK_HIERARCHY_FIELDS,
        *identifier_columns,
    ]
    if universe.empty:
        return pd.DataFrame(columns=[
            "bbg_name", "name_key", "matched_name_field", "bbg_primary_name",
            *metadata_columns,
        ])
    frames = []
    for field in BLOOMBERG_NAME_FIELDS:
        if field not in universe:
            continue
        part = universe.reindex(
            columns=list(dict.fromkeys([*metadata_columns, "long_comp_name"]))
        ).copy()
        part["bbg_name"] = universe[field].map(parse_bloomberg_names)
        part["matched_name_field"] = field
        frames.append(part.explode("bbg_name"))
    if not frames:
        return pd.DataFrame()
    index = pd.concat(frames, ignore_index=True)
    index = index.rename(columns={"long_comp_name": "bbg_primary_name"})
    index = index[index["bbg_name"].notna()]
    index["name_key"] = index["bbg_name"].map(_company_key)
    index = index[index["name_key"].str.len().ge(3)]
    return index.drop_duplicates(subset=["name_key", *identifier_columns]).reset_index(drop=True)


def _identifier_priority(row: pd.Series) -> int:
    """Rank of the most precise identifier family this Bloomberg row can join on."""
    for position, key in enumerate(BRIDGE_KEYS):
        value = row.get(key.view_column)
        if value is not None and not pd.isna(value) and str(value).strip():
            return position
    return len(BRIDGE_KEYS)


def _bridge_key_label(row: pd.Series) -> str:
    for key in BRIDGE_KEYS:
        value = row.get(key.view_column)
        if value is not None and not pd.isna(value) and str(value).strip():
            return key.label
    return ""


# Tokens shared by most sponsor names; matching on them would corroborate any parent that
# happens to be a fund manager, so `Ergon Capital Partners SA` would appear to confirm Apax.
GENERIC_SPONSOR_TOKENS = frozenset({
    "advisors", "asset", "capital", "equity", "fund", "group", "holdings", "investment",
    "investments", "management", "partners",
})


def _sponsor_tokens(sponsor_names: frozenset[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for name in sponsor_names:
        tokens |= {
            token for token in _company_key(name).split()
            if len(token) > 2 and token not in GENERIC_SPONSOR_TOKENS
        }
    return frozenset(tokens)


def _corroborates_sponsor(row: pd.Series, sponsor_tokens: frozenset[str]) -> bool:
    """Whether the Bloomberg parent chain names the sponsor under study.

    Recorded as evidence rather than enforced: Bloomberg still shows Palex Medical under
    `Ergon Capital Partners SA`, a predecessor sponsor, yet its debt link is correct.
    """
    if not sponsor_tokens:
        return False
    chain = " ".join(
        _company_key(row.get(field))
        for field in ("long_parent_comp_name", "long_ult_parent_comp_name")
    )
    return bool(sponsor_tokens & set(chain.split()))


def _match_names(company: pd.Series) -> tuple[str, ...]:
    names = [str(company.get("company_name") or "").strip()]
    aliases = company.get("alias_names") or ()
    names.extend(str(alias).strip() for alias in aliases)
    return tuple(name for name in dict.fromkeys(names) if name)


def resolve_companies_to_bloomberg(
    companies: pd.DataFrame,
    name_index: pd.DataFrame,
    *,
    sponsor_names: frozenset[str] = frozenset(),
    min_score: float = REVIEW_MATCH_MIN_SCORE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve each external company to one Bloomberg company, keeping the alternates.

    A single winner per company is deliberate even under a permissive threshold: fanning a
    company out across several Bloomberg matches would pull unrelated issuers' instruments
    into its tranche totals, which corrupts the aggregates rather than adding a reviewable
    false positive. Everything not selected is returned for audit.
    """
    if companies.empty or name_index.empty:
        return pd.DataFrame(columns=BRIDGE_MATCH_COLUMNS), pd.DataFrame()

    sponsor_tokens = _sponsor_tokens(sponsor_names)
    buckets: dict[str, list[int]] = {}
    for position, key in enumerate(name_index["name_key"]):
        buckets.setdefault(key.split()[0], []).append(position)

    candidates = []
    for _, company in companies.iterrows():
        seen: set[int] = set()
        for name in _match_names(company):
            company_key = _company_key(name)
            if not company_key:
                continue
            for position in buckets.get(company_key.split()[0], ()):
                if position in seen:
                    continue
                seen.add(position)
                row = name_index.iloc[position]
                score, overlap, exact = score_company_name_with_aliases(name, row["bbg_name"])
                if score < min_score:
                    continue
                candidates.append({
                    "external_company_id": company["external_company_id"],
                    "company_name": company["company_name"],
                    "matched_bbg_name": row["bbg_name"],
                    "matched_name_field": row["matched_name_field"],
                    "bbg_primary_name": row["bbg_primary_name"],
                    "bbg_parent_name": row.get("long_parent_comp_name"),
                    "bbg_ult_parent_name": row.get("long_ult_parent_comp_name"),
                    "bbg_relationship": row.get("company_to_parent_relationship"),
                    "match_score": float(score),
                    "token_overlap_score": float(overlap),
                    "exact_match_flag": bool(exact),
                    "sponsor_corroborated": _corroborates_sponsor(row, sponsor_tokens),
                    "_identifier_priority": _identifier_priority(row),
                    "_name_length": len(str(row["bbg_primary_name"] or "")),
                    "bridge_key_label": _bridge_key_label(row),
                    **{key.view_column: row.get(key.view_column) for key in BRIDGE_KEYS},
                    **{
                        field: row.get(field)
                        for field in CAPITAL_STACK_ADMISSION_FIELDS
                    },
                    **{
                        field: row.get(field)
                        for field in CAPITAL_STACK_HIERARCHY_FIELDS
                    },
                })
    if not candidates:
        return pd.DataFrame(columns=BRIDGE_MATCH_COLUMNS), pd.DataFrame()

    audit = pd.DataFrame(candidates)
    audit["candidate_count"] = audit.groupby("external_company_id")["matched_bbg_name"].transform(
        "nunique"
    )
    audit = audit.sort_values(
        ["external_company_id", "match_score", "sponsor_corroborated",
         "_identifier_priority", "_name_length"],
        ascending=[True, False, False, True, True],
    )
    audit["selected"] = ~audit.duplicated("external_company_id")
    best = audit[audit["selected"]][BRIDGE_MATCH_COLUMNS].reset_index(drop=True)
    logging.info(
        "Bloomberg bridge resolved %s of %s external companies", len(best), len(companies)
    )
    return best, audit.drop(columns=["_identifier_priority", "_name_length"]).reset_index(drop=True)


def _sql_text(value: object) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    return "NULL" if not text else "'" + text.replace("'", "''") + "'"


def _metadata_text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def promote_legacy_corporate_seeds(seed_matches: pd.DataFrame) -> pd.DataFrame:
    """Promote an inactive Bloomberg legacy entity to its explicit current parent.

    This is deliberately narrower than following an obligor: Bloomberg must mark the row
    inactive, classify it as a legacy company, mark it acquired, and point both its corporate
    parent and obligor to the same successor.
    """
    if seed_matches.empty:
        return seed_matches
    out = seed_matches.copy()
    out["original_seed_bbg_company_id"] = normalize_bbg_id_series(
        out["id_bb_company"]
    )
    out["legacy_seed_promoted"] = False
    for index, row in out.iterrows():
        status = _metadata_text(row.get("company_status")).upper()
        relationship = _metadata_text(
            row.get("company_to_parent_relationship")
        ).casefold()
        acquired = _metadata_text(row.get("acquired_by_parent")).casefold()
        parent_id = normalize_bbg_company_id(row.get("id_bb_parent_co"))
        obligor_id = normalize_bbg_company_id(row.get("obligor_bbid"))
        if not (
            status == "INAC"
            and relationship == "legacy company"
            and acquired in {"1", "true", "yes"}
            and parent_id is not None
            and parent_id == obligor_id
        ):
            continue
        out.at[index, "id_bb_company"] = parent_id
        out.at[index, "obligor_bbid"] = parent_id
        successor_name = row.get("id_bb_global_obligor_name")
        if successor_name is not None and not pd.isna(successor_name):
            out.at[index, "bbg_primary_name"] = successor_name
        out.at[index, "legacy_seed_promoted"] = True
    return out


@checkpointed
def fetch_bloomberg_capital_stack(
    seed_matches: pd.DataFrame,
    *,
    app: str = "databricks",
    view_name: str = BLOOMBERG_ISSUER_MASTER_VIEW,
    max_descendants: int = MAX_CAPITAL_STACK_DESCENDANTS,
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Expand a resolved Bloomberg issuer strictly downward through its capital tree.

    The ultimate-parent family is fetched as candidates, then the issuer-tree adapter admits
    only corporate paths containing the seed. Obligor hierarchy fields are evidence only.
    """
    if seed_matches.empty:
        return pd.DataFrame(columns=CAPITAL_STACK_COLUMNS)
    promoted_matches = promote_legacy_corporate_seeds(seed_matches)
    seed_columns = [
        "external_company_id",
        "company_name",
        "bbg_primary_name",
        "id_bb_company",
        "id_bb_ultimate_parent_co",
        "original_seed_bbg_company_id",
        "legacy_seed_promoted",
    ]
    seeds = promoted_matches.reindex(columns=seed_columns).dropna(
        subset=["external_company_id", "id_bb_company"]
    ).drop_duplicates()
    if seeds.empty:
        return pd.DataFrame(columns=CAPITAL_STACK_COLUMNS)
    seeds = seeds.copy()
    seeds["id_bb_company"] = normalize_bbg_id_series(seeds["id_bb_company"])
    seeds["id_bb_ultimate_parent_co"] = normalize_bbg_id_series(
        seeds["id_bb_ultimate_parent_co"]
    ).fillna(seeds["id_bb_company"])
    values = ",\n        ".join(
        "(" + ", ".join([
            _sql_text(row.external_company_id),
            _sql_text(row.company_name),
            _sql_text(row.bbg_primary_name),
            _sql_text(row.original_seed_bbg_company_id),
            _sql_text(row.id_bb_company),
            _sql_text(row.id_bb_ultimate_parent_co),
            _sql_text(row.legacy_seed_promoted),
        ]) + ")"
        for row in seeds.itertuples(index=False)
    )
    node_columns = [
        *BLOOMBERG_NAME_FIELDS,
        *BLOOMBERG_CONTEXT_FIELDS,
        *CAPITAL_STACK_ADMISSION_FIELDS,
        *[key.view_column for key in BRIDGE_KEYS],
        *CAPITAL_STACK_HIERARCHY_FIELDS,
    ]
    selected = ",\n            ".join(
        f"{bbg_company_id_sql(f'node.{column}')} AS {column}"
        for column in dict.fromkeys(node_columns)
    )
    source = _issuer_master_source(view_name, asof_cutoff)
    runtime = active_runtime()
    if runtime is not None:
        from data_access.dbtables.sponsor_study.relationship_helper import pinned_issuer_source
        if view_name != BLOOMBERG_ISSUER_MASTER_VIEW or asof_cutoff is None:
            raise ValueError("Distributed capital stacks require the standard dated issuer source")
        source = pinned_issuer_source(runtime, asof_cutoff)
    sql = f"""
WITH seeds (
    external_company_id,
    company_name,
    seed_bbg_name,
    original_seed_bbg_company_id,
    seed_bbg_company_id,
    seed_ultimate_parent_id,
    legacy_seed_promoted
) AS (
    VALUES
        {values}
),
candidates AS (
    SELECT
        seed.external_company_id,
        seed.company_name,
        seed.original_seed_bbg_company_id,
        seed.seed_bbg_company_id,
        seed.seed_bbg_name,
        CAST(seed.legacy_seed_promoted AS BOOLEAN) AS legacy_seed_promoted,
        {selected},
        CASE
            WHEN {bbg_company_id_sql("node.id_bb_company")} = seed.seed_bbg_company_id
                THEN 'seed'
            WHEN {bbg_company_id_sql("node.id_bb_parent_co")} = seed.seed_bbg_company_id
                THEN 'direct_child'
            ELSE 'ultimate_family_candidate'
        END AS capital_stack_relationship,
        CASE
            WHEN {bbg_company_id_sql("node.id_bb_company")} = seed.seed_bbg_company_id THEN 0
            WHEN {bbg_company_id_sql("node.id_bb_parent_co")} = seed.seed_bbg_company_id THEN 1
            ELSE 99
        END AS capital_stack_rank
    FROM seeds seed
    JOIN {source} node
      ON {bbg_company_id_sql("node.id_bb_company")} = seed.seed_bbg_company_id
      OR {bbg_company_id_sql("node.id_bb_company")} = seed.seed_ultimate_parent_id
      OR {bbg_company_id_sql("node.id_bb_ultimate_parent_co")} = seed.seed_ultimate_parent_id
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY external_company_id, id_bb_company
            ORDER BY capital_stack_rank
        ) AS node_rank
    FROM candidates
    WHERE capital_stack_relationship IS NOT NULL
)
SELECT
    ranked.*
FROM ranked
WHERE ranked.node_rank = 1
""".strip()
    runtime = active_runtime()
    if runtime is not None:
        from functools import partial
        from data_access.dbtables.sponsor_study.relationship_helper import capital_stack_group, capital_stack_schema
        key = fingerprint((seeds, asof_cutoff, max_descendants))
        candidates = runtime.materialize("capital_stack_candidates", key, lambda: runtime.spark.sql(sql))
        resolved = runtime.materialize("capital_stack_resolution", key, lambda: candidates.groupBy(
            "external_company_id").applyInPandas(partial(capital_stack_group, max_descendants=max_descendants),
                                               schema=capital_stack_schema()))
        # All candidates and rejections remain in the Delta helper for audit.
        # Only admissible descendants and seed diagnostics are needed downstream.
        return resolved.where("stack_eligibility = 'admitted' OR capital_stack_rank = 0").toPandas()
    stack = readdfsql(sql, app=app)
    if stack.empty:
        return pd.DataFrame(columns=CAPITAL_STACK_COLUMNS)
    stack["capital_stack_bbg_company_id"] = stack["id_bb_company"]
    stack["capital_stack_name"] = stack["long_comp_name"]
    stack["capital_stack_match_method"] = pd.to_numeric(
        stack["capital_stack_rank"], errors="coerce"
    ).map(
        lambda rank: (
            BLOOMBERG_BRIDGE_METHOD
            if rank == 0
            else CAPITAL_STACK_EXPANSION_METHOD
        )
    )
    stack["exact_match_flag"] = True
    stack["match_score"] = 1.0
    from data_access.dbtables.sponsor_study.issuer_tree_adapter import (
        annotate_capital_stack_admission,
    )
    tree_cache = {
        int(seed_id): group.copy()
        for seed_value, group in stack.groupby("seed_bbg_company_id", sort=False)
        if (seed_id := normalize_bbg_company_id(seed_value)) is not None
    }
    stack = annotate_capital_stack_admission(
        stack,
        tree_cache=tree_cache,
        app=app,
        max_descendants=max_descendants,
    )
    for column in CAPITAL_STACK_COLUMNS:
        if column not in stack:
            stack[column] = pd.NA
    return stack[CAPITAL_STACK_COLUMNS].drop_duplicates(
        ["external_company_id", "capital_stack_bbg_company_id"]
    ).reset_index(drop=True)


def capital_stack_aliases(stack: pd.DataFrame) -> pd.DataFrame:
    """All Bloomberg names carried by deterministically linked descendant nodes."""
    columns = [
        "external_company_id",
        "capital_stack_alias",
        "capital_stack_bbg_company_id",
        "capital_stack_relationship",
        "capital_stack_match_method",
    ]
    if stack.empty:
        return pd.DataFrame(columns=columns)
    if "stack_eligibility" in stack:
        stack = stack[stack["stack_eligibility"].eq("admitted")]
    if stack.empty:
        return pd.DataFrame(columns=columns)
    records = []
    for _, row in stack.iterrows():
        names = []
        for field in BLOOMBERG_NAME_FIELDS:
            names.extend(parse_bloomberg_names(row.get(field)))
        names.append(str(row.get("capital_stack_name") or "").strip())
        for name in dict.fromkeys(name for name in names if name):
            records.append({
                "external_company_id": row["external_company_id"],
                "capital_stack_alias": name,
                "capital_stack_bbg_company_id": row["capital_stack_bbg_company_id"],
                "capital_stack_relationship": row["capital_stack_relationship"],
                "capital_stack_match_method": row["capital_stack_match_method"],
            })
    return pd.DataFrame(records, columns=columns).drop_duplicates().reset_index(drop=True)


@checkpointed
def fetch_debt_features_for_bloomberg_matches(
    matches: pd.DataFrame,
    *,
    app: str = "databricks",
    table_name: str = "dbo.debt_features",
    asof_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """`debt_features` rows reachable from resolved Bloomberg identifiers, with `portco_id`."""
    if matches.empty:
        return pd.DataFrame()
    available = {col.lower(): col for col in fetch_table_columns(table_name, app=app)}
    company_keys = pd.Series(None, index=matches.index, dtype="object")
    if "id_bb_company" in matches:
        company_keys = normalize_bbg_id_series(matches["id_bb_company"]).map(
            lambda value: f"bbg_company:{value}" if value else None
        )
    if "id_bb_global_company" in matches:
        global_keys = matches["id_bb_global_company"].astype("string").str.strip()
        company_keys = company_keys.fillna(global_keys.where(global_keys.ne("")).map(
            lambda value: f"bbg_global:{value}" if pd.notna(value) else None
        ))
    observations: list[DebtIdentifierOwner] = []
    for priority, key in enumerate(BRIDGE_KEYS):
        if key.view_column not in matches:
            continue
        pairs = matches[[key.view_column, "external_company_id"]].copy()
        pairs["_company_key"] = company_keys
        if key.view_column in {"obligor_bbid", "company_corp_ticker"}:
            own_company = (
                normalize_bbg_id_series(matches["id_bb_company"])
                if "id_bb_company" in matches
                else pd.Series(pd.NA, index=matches.index)
            )
            obligor = normalize_bbg_id_series(matches["obligor_bbid"])
            audited_inside = (
                matches["obligor_inside_stack"].astype("boolean").fillna(False)
                if "obligor_inside_stack" in matches
                else pd.Series(False, index=matches.index)
            )
            eligible = audited_inside | obligor.eq(own_company)
            if key.view_column == "company_corp_ticker":
                eligible |= obligor.isna()
            pairs = pairs[eligible]
        pairs = pairs.dropna(subset=[key.view_column, "external_company_id"])
        numeric = key.view_column in {"id_bb_company", "obligor_bbid"}
        for value, owner, company_key in zip(
            pairs[key.view_column], pairs["external_company_id"], pairs["_company_key"]
        ):
            cleaned = (
                normalize_bbg_company_id(value)
                if numeric
                else str(value).strip()
            )
            if cleaned:
                for debt_column in key.debt_columns:
                    if debt_column in available:
                        observations.append(DebtIdentifierOwner(
                            available[debt_column], cleaned, str(owner),
                            company_key if pd.notna(company_key) else None, priority, numeric,
                        ))
    bindings = admitted_identifier_owners(tuple(observations))
    clauses = []
    for column in sorted({item.column for item in bindings}):
        values = sorted({item.value for item in bindings if item.column == column})
        clauses.append(f"{bbg_company_id_sql(column)} IN ({_quote_id_values(values)})")
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
    issuer_names = tuple({
        DebtIssuerNameOwner(str(row.external_company_id), name_key)
        for row in matches.itertuples(index=False)
        for column in (*BLOOMBERG_NAME_FIELDS, "bbg_primary_name", "matched_bbg_name")
        for name in parse_bloomberg_names(getattr(row, column, None))
        if (name_key := _company_key(name))
    })
    return bind_debt_rows(features, tuple(item for item in bindings if item.column in features),
                          issuer_names=issuer_names)
