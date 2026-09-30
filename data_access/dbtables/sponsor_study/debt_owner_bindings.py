"""Deterministic owner expansion after source identifiers have been admitted."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from data_access.dbtables.sponsor_study.company_aliases import _company_key
from data_access.dbtables.sponsor_study.issuer_tree_adapter import normalize_bbg_id_series


@dataclass(frozen=True)
class DebtIdentifierOwner:
    column: str
    value: str
    owner: str
    company_key: str | None
    priority: int
    numeric: bool


@dataclass(frozen=True)
class DebtIssuerNameOwner:
    owner: str
    name_key: str


def admitted_identifier_owners(
    observations: tuple[DebtIdentifierOwner, ...],
) -> tuple[DebtIdentifierOwner, ...]:
    """A shared ticker may fan out only when the company identity agrees."""
    groups: dict[tuple[str, str], set[DebtIdentifierOwner]] = defaultdict(set)
    for item in observations:
        groups[item.column, item.value].add(item)
    accepted = []
    for (column, _), items in groups.items():
        owners = {item.owner for item in items}
        identities = {item.company_key for item in items}
        if column.lower() in {"ticker", "company_corp_ticker"} and len(owners) > 1:
            if None in identities or len(identities) != 1:
                continue
        accepted.extend(items)
    return tuple(sorted(accepted, key=lambda item: (item.priority, item.column, item.value, item.owner)))


def _filter_ticker_links(
    links: pd.DataFrame, source: pd.DataFrame, bindings: tuple[DebtIdentifierOwner, ...],
    issuer_names: tuple[DebtIssuerNameOwner, ...],
) -> pd.DataFrame:
    """Require an admitted borrower ID or an exact admitted issuer-name corroboration."""
    has_borrower_id = pd.Series(False, index=source.index)
    for column in source.columns:
        if column.lower() not in {"id_bb_company", "id_bb_global_company"}:
            continue
        values = (
            normalize_bbg_id_series(source[column])
            if column.lower() == "id_bb_company"
            else source[column].astype("string").str.strip().replace("", pd.NA)
        )
        has_borrower_id |= values.notna()
        allowed = pd.DataFrame(
            [(item.value, item.owner) for item in bindings if item.column == column],
            columns=["_company_id", "portco_id"],
        ).drop_duplicates()
        checked = links.merge(pd.DataFrame({"_debt_row": source.index, "_company_id": values}), on="_debt_row")
        checked = checked.merge(allowed, on=["_company_id", "portco_id"], how="left", indicator=True)
        links = checked[checked._company_id.isna() | checked._merge.eq("both")].drop(columns=["_company_id", "_merge"])
    issuer_column = next((col for col in source if col.lower() == "issuer_name"), None)
    names = source[issuer_column].map(_company_key) if issuer_column else pd.Series("", index=source.index)
    lookup = pd.DataFrame([(item.owner, item.name_key) for item in issuer_names],
                          columns=["portco_id", "_issuer_name_key"]).drop_duplicates()
    checked = links.merge(pd.DataFrame({"_debt_row": source.index, "_has_borrower_id": has_borrower_id,
                                       "_issuer_name_key": names}), on="_debt_row")
    checked = checked.merge(lookup, on=["portco_id", "_issuer_name_key"], how="left", indicator=True)
    return checked[checked._has_borrower_id | checked._merge.eq("both")].drop(
        columns=["_has_borrower_id", "_issuer_name_key", "_merge"])


def bind_debt_rows(
    features: pd.DataFrame, bindings: tuple[DebtIdentifierOwner, ...],
    *, issuer_names: tuple[DebtIssuerNameOwner, ...] = (),
) -> pd.DataFrame:
    """Keep complete source rows once per owner, preferring stronger identifiers."""
    source = features.reset_index(drop=True)
    groups: dict[tuple[str, int, bool], list[DebtIdentifierOwner]] = defaultdict(list)
    for item in bindings:
        groups[item.column, item.priority, item.numeric].append(item)
    links = []
    for (column, priority, numeric), items in groups.items():
        values = normalize_bbg_id_series(source[column]) if numeric else source[column].astype("string").str.strip()
        lookup = pd.DataFrame([(item.value, item.owner) for item in items], columns=["identifier", "portco_id"])
        matched = pd.DataFrame({"_debt_row": source.index, "identifier": values}).merge(lookup, on="identifier")
        if column.lower() in {"ticker", "company_corp_ticker"}:
            matched = _filter_ticker_links(matched, source, bindings, issuer_names)
        matched["_priority"] = priority
        matched["debt_bridge_source"] = "bloomberg_issuer_master." + column + ":" + matched.identifier
        links.append(matched)
    if not links:
        return source.iloc[:0].assign(portco_id=pd.Series(dtype="string"))
    matched = pd.concat(links, ignore_index=True)
    matched = matched[matched._priority.eq(matched.groupby("_debt_row")._priority.transform("min"))]
    matched = matched.sort_values(["_debt_row", "portco_id", "debt_bridge_source"]).drop_duplicates(["_debt_row", "portco_id"])
    result = source.iloc[matched._debt_row].copy().reset_index(drop=True)
    result["portco_id"] = matched.portco_id.to_numpy()
    result["debt_bridge_source"] = matched.debt_bridge_source.to_numpy()
    return result
