"""FinDox Core asset identifiers bridge confirmed companies to maintained debt prices."""
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
import json
import logging
import re

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables.sponsor_study.refresh_runtime import checkpointed


IDENTIFIER_COLUMNS = (
    "instrument_id_uniq", "instrument_id", "instrument_id_index", "instrument_id_uniq_index",
    "loanx_id", "isin", "cusip", "bbg_id", "bbg_id_index", "figi", "fsym_id", "fsym_security_id",
)
XREF_COLUMNS = [
    "external_portco_id", "findox_issuer_id", "asset_id", "asset_name", "deal_ids",
    "identifier_type", "identifier", "observed_at", "company_match_method",
    "company_match_score", "market_date", "instrument_id_uniq", "resolution_status",
    "resolution_source", "price_source", "price", "evidence_json",
]


def identifier_keys(value: object) -> frozenset[str]:
    """Exact identifiers plus check-digit verified Bloomberg and US/CA ISIN forms."""
    if value is None or pd.isna(value):
        return frozenset()
    text = re.sub(r"\s+(CORP|GOVT|LOAN)$", "", str(value).strip().upper())
    if not re.fullmatch(r"[A-Z0-9_-]+", text) or text in {"NAN", "NONE", "NA"}:
        return frozenset()
    keys = {text}
    if re.fullmatch(r"CO[A-Z]{2}[A-Z0-9]{7}", text):
        keys.add(text[2:])
    for key in tuple(keys):
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{6,7}", key) and not key.startswith("LX"):
            numbers = [(int(c) if c.isdigit() else ord(c) - 55) * (1 + i % 2)
                       for i, c in enumerate(key[:8])]
            digit = str((10 - sum(n // 10 + n % 10 for n in numbers) % 10) % 10)
            if len(key) == 8:
                keys.add(key + digit)
            elif key[-1] == digit:
                keys.add(key[:8])
        if re.fullmatch(r"(?:US|CA)[A-Z0-9]{9}[0-9]", key):
            # Validate ISIN's Luhn checksum before deriving its CUSIP.
            digits = "".join(c if c.isdigit() else str(ord(c) - 55) for c in key)
            total = sum((int(c) * (1 + i % 2)) // 10 + (int(c) * (1 + i % 2)) % 10
                        for i, c in enumerate(reversed(digits)))
            if total % 10 == 0:
                keys.add(key[2:11])
    return frozenset(keys)


@dataclass(frozen=True)
class CoreIdentifier:
    findox_issuer_id: str
    asset_id: str
    asset_name: str
    deal_ids: str
    identifier_type: str
    identifier: str
    observed_at: pd.Timestamp

    @property
    def asset_key(self) -> tuple[str, str]:
        # Null asset IDs must never glue unrelated securities together.
        return self.findox_issuer_id, self.asset_id or f"{self.identifier_type}:{self.identifier}"


def parse_core_identifiers(frame: pd.DataFrame) -> tuple[CoreIdentifier, ...]:
    if frame.empty:
        return ()
    required = {"IssuerId", "AssetId", "AssetName", "DealIds", "IdentifierTypeName", "Identifier", "observed_at"}
    if not required.issubset(frame):
        raise ValueError(f"Core identifier response missing {sorted(required - set(frame))}")
    output = []
    for row in frame.itertuples(index=False):
        kind = str(row.IdentifierTypeName).lower()
        keys = identifier_keys(row.Identifier)
        if kind not in {"lxid", "blid", "isin", "figi", "cusip"} or not keys:
            continue
        if pd.isna(row.IssuerId) or pd.isna(row.observed_at):
            raise ValueError("Core security identifier requires issuer and observation timestamp")
        deals = row.DealIds.tolist() if hasattr(row.DealIds, "tolist") else row.DealIds
        output.append(CoreIdentifier(
            str(int(row.IssuerId)), "" if pd.isna(row.AssetId) else str(int(row.AssetId)),
            "" if pd.isna(row.AssetName) else str(row.AssetName),
            json.dumps(deals, default=str), kind, str(row.Identifier).strip().upper(),
            pd.Timestamp(row.observed_at),
        ))
    return tuple(dict.fromkeys(output))


@checkpointed
def fetch_core_identifiers(issuer_ids: tuple[str, ...]) -> pd.DataFrame:
    """One API session; bounded requests, checkpointed with their real retrieval time."""
    if not issuer_ids:
        return pd.DataFrame()
    from data_importer.findox_api.findox import FindoxApi

    api = FindoxApi()
    frames = []
    for start in range(0, len(issuer_ids), 100):
        batch = issuer_ids[start:start + 100]
        frame = api.get_common_ids([int(value) for value in batch])
        if frame is None:
            raise RuntimeError("FinDox Core identifier request failed; refresh cannot claim complete coverage")
        frame = frame.copy()
        frame["observed_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)
        parse_core_identifiers(frame)
        frames.append(frame)
        logging.info("Sponsor FinDox Core identifiers issuers=%s/%s rows=%s", start + len(batch), len(issuer_ids), len(frame))
    return pd.concat(frames, ignore_index=True)


def _table_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){1,2}", value):
        raise ValueError("Expected a qualified configured table name")
    return value


@checkpointed
def _market_date_frame(*, table_name: str, app: str, asof_cutoff: str) -> pd.DataFrame:
    cutoff = pd.Timestamp(asof_cutoff).date().isoformat()
    return readdfsql(f"SELECT MAX(CAST(asof_date AS DATE)) AS market_date FROM {_table_name(table_name)} "
                    f"WHERE asof_date <= DATE '{cutoff}'", app=app)


def fetch_market_date(*, table_name: str, app: str, asof_cutoff: str) -> str:
    result = _market_date_frame(table_name=table_name, app=app, asof_cutoff=asof_cutoff)
    if result.empty or pd.isna(result.iloc[0].market_date):
        raise ValueError("No debt_features market date available at the requested cutoff")
    return pd.Timestamp(result.iloc[0].market_date).date().isoformat()


@checkpointed
def fetch_identifier_rows(keys: tuple[str, ...], *, table_name: str, app: str,
                          market_date: str, debt_features: bool) -> pd.DataFrame:
    """Exact date and bounded ID predicates; never scan a historical price window."""
    if not keys:
        return pd.DataFrame()
    table = _table_name(table_name)
    day = date.fromisoformat(market_date).isoformat()
    columns = readdfsql(f"SELECT * FROM {table} LIMIT 0", app=app).columns
    id_columns = [c for c in IDENTIFIER_COLUMNS if c in columns] if debt_features else ["sec_id"]
    if not id_columns:
        raise ValueError(f"No maintained security identifiers in {table}")
    kind = "instrument_type" if debt_features else "sec_type"
    frames = []
    for start in range(0, len(keys), 500):
        batch = keys[start:start + 500]
        if any(not re.fullmatch(r"[A-Z0-9_-]+", key) for key in batch):
            raise ValueError("Invalid normalized security key")
        values = ",".join(f"'{key}'" for key in batch)
        predicate = " OR ".join(f"UPPER(TRIM({column})) IN ({values})" for column in id_columns)
        frames.append(readdfsql(f"SELECT * FROM {table} WHERE asof_date = DATE '{day}' "
                               f"AND LOWER({kind}) IN ('loan','bond') AND ({predicate})", app=app))
    # A security can match several ID batches. Full source rows are scalar warehouse values.
    return pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True)


def synchronized_rows(frame: pd.DataFrame, market_date: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    day = pd.Timestamp(market_date)
    selected = pd.to_datetime(frame.asof_date).dt.normalize().eq(day)
    if "price_date" in frame:
        dates = pd.to_datetime(frame.price_date)
        selected &= dates.isna() | dates.dt.normalize().eq(day)
    return frame[selected].copy()


def synchronized_feature_quotes(frame: pd.DataFrame, market_date: str) -> pd.DataFrame:
    """Suppress stale quotes without discarding the row's financials or instrument metadata."""
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    valid = synchronized_rows(out, market_date).index
    quote_columns = [c for c in ("price", "bid_price", "mid_price", "ytw", "ytm", "yield", "spread", "oas",
        "yield_to_worst", "yield_to_worst_conv", "yield_to_maturity", "yield_to_maturity_conv",
        "duration", "modified_duration", "effective_duration") if c in out]
    out.loc[~out.index.isin(valid), quote_columns] = float("nan")
    return out


def resolve_core_debt(matches: pd.DataFrame, core: tuple[CoreIdentifier, ...],
                      debt: pd.DataFrame, prices: pd.DataFrame, *, market_date: str) -> dict[str, pd.DataFrame]:
    """Resolve asset components, refusing issuer conflicts and multiple instruments per asset."""
    assets: dict[tuple[str, str], list[CoreIdentifier]] = defaultdict(list)
    owners: dict[str, set[str]] = defaultdict(set)
    for item in core:
        assets[item.asset_key].append(item)
        for key in identifier_keys(item.identifier):
            owners[key].add(item.findox_issuer_id)
    # Repeated Core asset records can share one exact security ID. Unite only within an issuer.
    parent = {asset: asset for asset in assets}

    def root(asset: tuple[str, str]) -> tuple[str, str]:
        while parent[asset] != asset:
            parent[asset] = parent[parent[asset]]
            asset = parent[asset]
        return asset

    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for asset, items in assets.items():
        for item in items:
            for key in identifier_keys(item.identifier):
                pair = item.findox_issuer_id, key
                if pair in seen:
                    parent[root(asset)] = root(seen[pair])
                seen[pair] = asset
    groups: dict[tuple[str, str], list[CoreIdentifier]] = defaultdict(list)
    for asset, items in assets.items():
        groups[root(asset)].extend(items)
    debt = synchronized_feature_quotes(debt, market_date).reset_index(drop=True)
    prices = synchronized_rows(prices, market_date).reset_index(drop=True)
    indices: list[dict[str, set[int]]] = []
    for frame, columns in ((debt, IDENTIFIER_COLUMNS), (prices, ("sec_id",))):
        index: dict[str, set[int]] = defaultdict(set)
        for column in columns:
            if column in frame:
                for position, value in frame[column].items():
                    for key in identifier_keys(value):
                        index[key].add(position)
        indices.append(index)
    selected = matches.copy()
    if not selected.empty:
        selected = selected[selected.selected.eq(True)]
        if selected.groupby("external_company_id").findox_issuer_id.nunique().gt(1).any():
            raise ValueError("Ambiguous company-to-FinDox issuer selection")
    audit, feature_rows, price_rows = [], [], []
    for match in selected.itertuples(index=False):
        issuer = str(int(match.findox_issuer_id))
        for (group_issuer, _), items in groups.items():
            if issuer != group_issuer:
                continue
            keys = set().union(*(identifier_keys(item.identifier) for item in items))
            status = "unpriced_on_market_date"
            chosen, source = None, ""
            hits = [set().union(*(index.get(key, set()) for key in keys)) for index in indices]
            df_hits = debt.loc[sorted(hits[0])]
            price_hits = prices.loc[sorted(hits[1])]
            # Distinct identifiers of the same type imply an over-broad asset/deal record.
            types: dict[str, set[frozenset[str]]] = defaultdict(set)
            for item in items:
                types[item.identifier_type].add(identifier_keys(item.identifier))
            if any("equity" in item.asset_name.lower() for item in items):
                status = "excluded_equity"
            elif any(len(owners[key]) > 1 for key in keys):
                status = "conflicting_core_issuers"
            elif any(len(values) > 1 for values in types.values()):
                status = "conflicting_asset_identifiers"
            elif not df_hits.empty and (df_hits.instrument_id_uniq.nunique() != 1 or len(df_hits) != 1):
                status = "ambiguous_debt_features_instrument"
            else:
                if not df_hits.empty:
                    chosen, source = df_hits.iloc[0].copy(), "debt_features"
                valid_prices = price_hits[pd.to_numeric(price_hits.get("price", pd.Series(dtype=float)), errors="coerce").notna()]
                if chosen is None or pd.isna(chosen.get("price")):
                    if not valid_prices.empty:
                        # All candidate keys must be direct identifiers of this one Core asset.
                        # Stable vendor/key order selects one complete observation; values are not coalesced.
                        quote = valid_prices.sort_values(["source", "sec_id"]).iloc[0].copy()
                        quote["instrument_id_uniq"] = (chosen.instrument_id_uniq if chosen is not None else
                                                       sorted(keys, key=lambda k: (not k.startswith("LX"), k))[0])
                        quote["portco_id"] = str(match.external_company_id)
                        price_rows.append(quote)
                        if chosen is None:
                            chosen = quote
                        source = "v_securities_data"
                if chosen is not None:
                    status = "priced" if pd.notna(chosen.get("price")) or source == "v_securities_data" else "debt_features_without_price"
                    if not df_hits.empty:
                        feature = df_hits.iloc[0].copy()
                        feature["portco_id"] = str(match.external_company_id)
                        feature["debt_bridge_source_system"] = "findox_core_identifier"
                        feature["debt_bridge_source"] = f"findox_core:{issuer}:" + ",".join(sorted({i.asset_id for i in items}))
                        feature["debt_bridge_match_method"] = "selected_findox_issuer_exact_asset_identifier"
                        feature["debt_bridge_relation"] = "findox_issuer_asset"
                        feature_rows.append(feature)
            evidence = json.dumps({"keys": sorted(keys),
                "debt_instruments": df_hits.get("instrument_id_uniq", pd.Series(dtype=str)).tolist(),
                "price_observations": price_hits.reindex(columns=["sec_id", "source", "asof_date", "price"]).to_dict("records"),
                "identifier_time_basis": "retrieved_snapshot"}, default=str)
            for item in items:
                audit.append({**asdict(item), "external_portco_id": str(match.external_company_id),
                              "company_match_method": str(match.matched_name_field), "company_match_score": match.match_score,
                              "market_date": market_date, "instrument_id_uniq": None if chosen is None else chosen.instrument_id_uniq,
                              "resolution_status": status, "resolution_source": source,
                              "price_source": ("debt_features" if source == "debt_features" else
                                               str(quote.source) if source == "v_securities_data" else None),
                              "price": (quote.price if source == "v_securities_data" else
                                        chosen.get("price") if chosen is not None else None), "evidence_json": evidence})
    return {"xref": pd.DataFrame(audit, columns=XREF_COLUMNS), "features": pd.DataFrame(feature_rows),
            "prices": pd.DataFrame(price_rows)}


@checkpointed
def fetch_findox_debt(matches: pd.DataFrame, *, debt_features_table: str, prices_table: str,
                      app: str, market_date: str) -> dict[str, pd.DataFrame]:
    issuer_ids = tuple(sorted({str(int(v)) for v in matches.findox_issuer_id.dropna()})) if not matches.empty else ()
    core = parse_core_identifiers(fetch_core_identifiers(issuer_ids))
    keys = tuple(sorted(set().union(*(identifier_keys(item.identifier) for item in core))))
    debt = fetch_identifier_rows(keys, table_name=debt_features_table, app=app, market_date=market_date, debt_features=True)
    prices = fetch_identifier_rows(keys, table_name=prices_table, app=app, market_date=market_date, debt_features=False)
    result = resolve_core_debt(matches, core, debt, prices, market_date=market_date)
    logging.info("Sponsor FinDox debt market_date=%s identifiers=%s feature_rows=%s fallback_prices=%s status=%s",
                 market_date, len(core), len(result["features"]), len(result["prices"]),
                 result["xref"].resolution_status.value_counts().to_dict())
    return result


def core_price_snapshots(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.internal_joins import EXTERNAL_MARKET_COLUMNS, EXTERNAL_DEBT_INSTRUMENT_COLUMNS

    if prices.empty:
        return pd.DataFrame(columns=EXTERNAL_MARKET_COLUMNS), pd.DataFrame(columns=EXTERNAL_DEBT_INSTRUMENT_COLUMNS)
    rows = prices.rename(columns={"portco_id": "external_portco_id"}).copy()
    rows["asof_date"] = pd.to_datetime(rows.asof_date)
    rows["source_system"] = "v_securities_data"
    rows["price_source"] = rows.source
    rows["bridge_provenance"] = "findox_core_identifier->" + rows.sec_id.astype(str)
    rows["tranche_type"] = "Other"
    rows["ytm"] = rows.get("yield")
    return rows.reindex(columns=EXTERNAL_MARKET_COLUMNS), rows.reindex(columns=EXTERNAL_DEBT_INSTRUMENT_COLUMNS)


def merge_core_market(existing: pd.DataFrame, additional: pd.DataFrame, evidence: pd.DataFrame) -> pd.DataFrame:
    """Collapse only proven security aliases, preserving a complete preferred quote row."""
    rows = pd.concat([existing, additional], ignore_index=True)
    if rows.empty:
        return rows
    aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in evidence[evidence.resolution_status.isin(["priced", "debt_features_without_price"])].itertuples():
        for key in identifier_keys(item.identifier):
            aliases[item.external_portco_id, key].add(item.instrument_id_uniq)
    canonical = []
    for item in rows.itertuples():
        targets = set().union(*(aliases.get((item.external_portco_id, key), set())
                               for key in identifier_keys(item.instrument_id_uniq)))
        canonical.append(next(iter(targets)) if len(targets) == 1 else item.instrument_id_uniq)
    rows["instrument_id_uniq"] = canonical
    rows["asof_date"] = pd.to_datetime(rows.asof_date)
    rows["_missing_price"] = rows.price.isna()
    rows["_fallback"] = rows.source_system.eq("v_securities_data")
    keys = ["external_portco_id", "instrument_id_uniq", "asof_date"]
    return (rows.sort_values(keys + ["_missing_price", "_fallback", "price_source"], na_position="last")
            .drop_duplicates(keys).drop(columns=["_missing_price", "_fallback"]).reset_index(drop=True))
