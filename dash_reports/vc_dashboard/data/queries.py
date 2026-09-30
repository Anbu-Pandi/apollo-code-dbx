"""Pure data functions for the VC dashboard — used by both the ``vc_server`` sub-server
and the dashboard's direct-query fallback.

Reads go through ``read_table`` (cache-aware: per-host local Delta replica when present,
else ``readdfsql(app='databricks')``). Because the local replica serves **full tables**,
all window/apollo/sector filtering happens here in pandas (not SQL) — the VC serving set
is tiny, so a full read + pandas filter is cheap and keeps the cache DB-dialect-free.
News semantic search still hits the ES ``vc_news`` index.
"""

import logging

import pandas as pd

from data_access.dbtables.vc import (
    VC_DEALS,
    VC_INVESTORS,
    VC_SOURCES,
    VC_SUBSECTORS,
    VC_UNIVERSE,
)
from data_access.dbtables.vc.tables import (
    delete_by_key,
    read_table,
    upsert_by_key,
)

HEATMAP_AXES = {
    "round_type": "round_type",
    "region": "region",
    "deal_size_band": "deal_size_band",
    "time_bucket": "time_bucket",
    "sub_sector": "sub_sector",
}

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR",
}


# ── pandas filters (applied over full-table cache reads) ─────────────────────

def _filter_window(df: pd.DataFrame, days, col: str) -> pd.DataFrame:
    if df is None or df.empty or not days or int(days) <= 0:
        return df
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    cutoff = end - pd.Timedelta(days=int(days))
    dt = pd.to_datetime(df[col], errors="coerce")
    return df[(dt >= cutoff) & (dt < end)]


def _filter_apollo(df: pd.DataFrame, apollo_only: bool) -> pd.DataFrame:
    if apollo_only and df is not None and not df.empty and "is_apollo_relevant" in df:
        return df[df["is_apollo_relevant"].astype("boolean").fillna(False)]
    return df


# ── Funding / Deals ─────────────────────────────────────────────────────────

def _deals_df(env):
    """Read actual deal records, excluding investor portfolio memberships."""
    df = read_table(VC_DEALS, env=env)
    if df is None or df.empty:
        return df
    portfolio = df.get("source", pd.Series("", index=df.index)).eq("pitchbook_investor")
    portfolio |= df["deal_id"].astype(str).str.startswith("inv-")
    df = df.loc[~portfolio].copy()
    if "deal_id" in df.columns:
        sort_col = "ingested_at" if "ingested_at" in df.columns else "deal_date"
        df = (df.sort_values(sort_col, na_position="first")
                .drop_duplicates("deal_id", keep="last"))
    return df


def _filter_selection(df, search="", min_size_usd=0):
    if df is None or df.empty:
        return df
    if min_size_usd:
        df = df[pd.to_numeric(df["deal_size_usd"], errors="coerce") >= float(min_size_usd)]
    for term in str(search or "").strip().split():
        columns = [c for c in ("company", "investors", "lead_investors", "sector", "sub_sector",
                               "region", "round_type", "deal_status", "deal_id") if c in df]
        match = pd.Series(False, index=df.index)
        for column in columns:
            match |= df[column].fillna("").astype(str).str.contains(term, case=False, regex=False)
        df = df[match]
    return df


def get_activity(perspective="investor", window_days=90, apollo_only=False,
                 search="", min_size_usd=0, sort="deals", env=None):
    df = _filter_selection(_filter_apollo(_deals_df(env), apollo_only), search, min_size_usd)
    if df is None or df.empty:
        return []
    kind = "company" if perspective == "company" else "investor"
    df = df.copy()
    df["deal_size_usd"] = pd.to_numeric(df["deal_size_usd"], errors="coerce")
    df["participants"] = (df["investors"].fillna("") + "," + df["lead_investors"].fillna("")).map(
        lambda names: sorted({name.strip() for name in names.split(",") if name.strip()}))
    if kind == "investor":
        df = df.explode("participants").rename(columns={"participants": "entity_name"})
        df = df[df["entity_name"].isin(_watchlist_names(env))]
    else:
        df["entity_name"] = df["company"]
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=int(window_days))
    month = (end - pd.Timedelta(days=1)).to_period("M")
    periods = pd.period_range(month - 11, month, freq="M")
    out = []
    for name, history in df.groupby("entity_name"):
        dates = pd.to_datetime(history["deal_date"], errors="coerce")
        current = history[(dates >= start) & (dates < end)]
        if current.empty:
            continue
        previous = int(((dates >= start - pd.Timedelta(days=int(window_days))) & (dates < start)).sum())
        counterparties = {}
        for record in _records(current):
            names = [record["company"]] if kind == "investor" else record["participants"]
            for other in names:
                if not other:
                    continue
                stats = counterparties.setdefault(other, {"name": other, "deals": 0, "value_usd": 0.0})
                stats["deals"] += 1
                if record.get("deal_status") == "Completed":
                    stats["value_usd"] += record.get("deal_size_usd") or 0
        tops = sorted(counterparties.values(), key=lambda row: (-row["deals"], -row["value_usd"], row["name"]))[:5]
        completed = current.loc[current["deal_status"].eq("Completed"), "deal_size_usd"]
        counts = dates[dates < end].dt.to_period("M").value_counts()
        out.append({"name": name, "kind": kind, "deals": len(current), "previous_deals": previous,
                    "change": len(current) - previous, "value_usd": float(completed.sum()),
                    "disclosed": int(completed.notna().sum()), "latest": str(current["deal_date"].max())[:10],
                    "monthly": [int(counts.get(period, 0)) for period in periods],
                    "counterparties": tops, "counterparty_count": len(counterparties)})
    orders = {"deals": lambda row: (-row["deals"], -row["value_usd"], row["name"]),
              "value": lambda row: (-row["value_usd"], -row["deals"], row["name"]),
              "momentum": lambda row: (-row["change"], -row["deals"], row["name"]),
              "recent": lambda row: (row["latest"], row["name"])}
    return sorted(out, key=orders.get(sort, orders["deals"]), reverse=sort == "recent")


def get_portfolio(window_days=90, limit=500, env=None):
    df = read_table(VC_DEALS, env=env)
    if df is None or df.empty:
        return []
    df = df[df["source"].eq("pitchbook_investor")].copy()
    df = _filter_window(df, window_days, "deal_date")
    df = df.sort_values("deal_date", ascending=False).drop_duplicates("deal_id").head(limit)
    return _records(df[["company", "company_pbid", "investors", "deal_date"]].rename(
        columns={"deal_date": "investor_since"}))


def get_deals(window_days=90, apollo_only=False, sector=None, limit=500, env=None, search="", min_size_usd=0):
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return []
    df = _filter_window(df, window_days, "deal_date")
    df = _filter_apollo(df, apollo_only)
    if sector:
        df = df[df["sector"] == sector]
    if df.empty:
        return []
    df = df.sort_values("deal_date", ascending=False).head(int(limit))
    return _records(df)


def _time_bucket(df: pd.DataFrame) -> pd.Series:
    dt = pd.to_datetime(df["deal_date"], errors="coerce")
    return dt.dt.to_period("M").astype(str)


def get_sector_heatmap(window_days=90, axis="round_type", apollo_only=False,
                       metric="count", env=None, search="", min_size_usd=0):
    axis = HEATMAP_AXES.get(axis, "round_type")
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return {"sectors": [], "buckets": [], "matrix": [], "axis": axis}
    df = _filter_window(df, window_days, "deal_date")
    df = _filter_apollo(df, apollo_only)
    if df.empty:
        return {"sectors": [], "buckets": [], "matrix": [], "axis": axis}

    if axis == "time_bucket":
        df = df.copy()
        df["time_bucket"] = _time_bucket(df)

    df["sector"] = df["sector"].fillna("Unclassified").replace("", "Unclassified")
    df[axis] = df[axis].fillna("Unknown").replace("", "Unknown")

    values = "deal_id" if metric == "count" else "deal_size_usd"
    if metric == "size" and "deal_status" in df:
        df.loc[~df["deal_status"].eq("Completed"), "deal_size_usd"] = None
    aggfunc = "count" if metric == "count" else "sum"
    pivot = pd.pivot_table(df, index="sector", columns=axis, values=values,
                           aggfunc=aggfunc, fill_value=0)
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]
    if axis == "time_bucket":
        pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    elif axis == "deal_size_band":
        order = ["<$10M", "$10-50M", "$50-100M", "$100-250M", "$250-500M", "$500M+", "Undisclosed"]
        pivot = pivot.reindex([b for b in order if b in pivot.columns], axis=1)
    else:
        pivot = pivot[pivot.sum(axis=0).sort_values(ascending=False).index]
    return {
        "sectors": list(pivot.index.astype(str)),
        "buckets": list(pivot.columns.astype(str)),
        "matrix": pivot.values.tolist(),
        "axis": axis,
        "metric": metric,
    }


def get_deals_for_cell(sector, axis="round_type", bucket=None, window_days=90,
                       apollo_only=False, limit=200, env=None, search="", min_size_usd=0):
    axis = HEATMAP_AXES.get(axis, "round_type")
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return []
    df = _filter_window(df, window_days, "deal_date")
    df = _filter_apollo(df, apollo_only)
    if sector:
        df = df[df["sector"].fillna("Unclassified").replace("", "Unclassified") == str(sector)]
    if df.empty:
        return []
    if axis == "time_bucket":
        df = df.copy()
        df["time_bucket"] = _time_bucket(df)
    if bucket is not None:
        df = df[df[axis].fillna("Unknown").replace("", "Unknown").astype(str) == str(bucket)]
    df = df.sort_values("deal_date", ascending=False).head(int(limit))
    return _records(df)


def get_summary(window_days=90, apollo_only=False, env=None, search="", min_size_usd=0):
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return {"deal_count": 0, "total_raised_usd": 0.0, "sectors": 0, "trend": []}
    df = _filter_apollo(df, apollo_only)
    dates = pd.to_datetime(df["deal_date"], errors="coerce")
    observed = pd.to_datetime(df.get("source_observed_at", pd.Series(pd.NaT, index=df.index)), errors="coerce")
    loaded = pd.to_datetime(df.get("ingested_at", pd.Series(pd.NaT, index=df.index)), errors="coerce")
    source_as_of = observed.combine_first(loaded).max()
    df = df.copy()
    if "deal_status" in df:
        df.loc[~df["deal_status"].eq("Completed"), "deal_size_usd"] = None
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=int(window_days))
    previous = df[(dates >= start - pd.Timedelta(days=int(window_days))) & (dates < start)]
    current = df[(dates >= start) & (dates < end)].copy()
    amounts = pd.to_numeric(current["deal_size_usd"], errors="coerce")
    current["capital"] = amounts
    current["date"] = pd.to_datetime(current["deal_date"]).dt.normalize()
    daily = current.groupby("date").agg(capital=("capital", "sum"), deals=("deal_id", "count"))
    daily = daily.reindex(pd.date_range(start, end - pd.Timedelta(days=1)), fill_value=0)
    daily.index.name = "date"
    sectors = current.groupby("sector")["capital"].sum().nlargest(8)
    return {
        "deal_count": len(current),
        "total_raised_usd": float(amounts.sum()),
        "median_raised_usd": float(amounts.median()) if amounts.notna().any() else None,
        "disclosed_count": int(amounts.notna().sum()),
        "source_as_of": source_as_of.strftime("%d %b %Y %H:%M") if pd.notna(source_as_of) else None,
        "source_stale": pd.isna(source_as_of) or pd.Timestamp.now() - source_as_of > pd.Timedelta(hours=48),
        "detail_companies": int(df["company_pbid"].nunique()) if "company_pbid" in df else 0,
        "lead_known": int(current.get("lead_investors", pd.Series("", index=current.index)).fillna("").ne("").sum()),
        "sectors": int(current["sector"].nunique()),
        "previous_deal_count": len(previous),
        "previous_raised_usd": float(pd.to_numeric(previous["deal_size_usd"], errors="coerce").sum()),
        "latest_deal": dates[dates < end].max().strftime("%d %b %Y") if (dates < end).any() else None,
        "trend": _records(daily.reset_index()),
        "sector_capital": [{"sector": s, "capital": float(v)} for s, v in sectors.items()],
    }


# ── Followed-firm daily deal feed ───────────────────────────────────────────

def _watchlist_names(env=None) -> list:
    """Names of the FOLLOWED firms. ``vc_investors`` also accumulates every deal
    participant, so the watchlist is the seed-firm pbid set (the same set that
    scopes the PitchBook pull)."""
    firms = read_table(VC_INVESTORS, env=env)
    if firms is None or firms.empty:
        return []
    try:
        from data_importer.vc.seed_firms import FIRMS
        followed = firms[firms["investor_pbid"].isin(FIRMS)]
        if not followed.empty:
            firms = followed
    except Exception:
        logging.debug("seed firm watchlist unavailable; using all vc_investors")
    return sorted(firms["investor_name"].dropna().unique(), key=len, reverse=True)


def get_firm_deals(window_days=7, apollo_only=False, limit=500, env=None, search="", min_size_usd=0):
    """Deals in the window attributed to the followed-firm watchlist (vc_investors)."""
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return []
    df = _filter_window(df, window_days, "deal_date")
    df = _filter_apollo(df, apollo_only)
    if df.empty:
        return []
    watchlist = _watchlist_names(env)
    df = df.copy()
    df["followed_firms"] = df["investors"].fillna("").map(
        lambda names: ", ".join(f for f in watchlist if f in names))
    # Every stored deal has a followed firm among its participants by construction
    # (the pull is watchlist-scoped); only drop rows whose DISCLOSED participants
    # all fall outside the watchlist. Blank participants stay (attribution unknown).
    df = df[df["followed_firms"].ne("") | df["investors"].fillna("").eq("")]
    df = df.sort_values(["deal_date", "deal_size_usd"], ascending=False).head(int(limit))
    return _records(df)


def get_investor_activity(window_days=90, apollo_only=False, top_n=8, env=None):
    """Per followed firm: windowed deal aggregates + top deals by size.

    Returns firms sorted by activity, each with a ``top_deals`` list of
    {company, deal_size_usd, deal_date, round_type} (deduped by company, sized
    first) and a count of window portfolio adds from the relationship rows.
    """
    watchlist = _watchlist_names(env)
    if not watchlist:
        return []
    deals = _deals_df(env)
    trailing = _filter_window(deals, 365, "deal_date")  # sparkline basis, window-independent
    deals = _filter_apollo(_filter_window(deals, window_days, "deal_date"), apollo_only)
    holdings = read_table(VC_DEALS, env=env)
    holdings = holdings[holdings.get("source", pd.Series("", index=holdings.index))
                        .eq("pitchbook_investor")] if holdings is not None else None
    holdings = _filter_window(holdings, window_days, "deal_date")
    out = []
    for firm in sorted(watchlist):
        mine = deals[deals["investors"].fillna("").str.contains(firm, regex=False)] \
            if deals is not None and not deals.empty else pd.DataFrame()
        adds = int(holdings["investors"].fillna("").str.contains(firm, regex=False).sum()) \
            if holdings is not None and not holdings.empty else 0
        if mine.empty and not adds:
            continue
        sized = mine.copy()
        sized["deal_size_usd"] = pd.to_numeric(sized.get("deal_size_usd"), errors="coerce")
        completed = sized.loc[sized.get("deal_status", pd.Series("", index=sized.index))
                              .eq("Completed"), "deal_size_usd"]
        top = (sized.sort_values("deal_size_usd", ascending=False, na_position="last")
                    .drop_duplicates("company")
                    .head(int(top_n)))
        out.append({
            "firm": firm,
            "deals": int(len(mine)),
            "led": int(mine.get("lead_investors", pd.Series("", index=mine.index))
                       .fillna("").str.contains(firm, regex=False).sum()),
            "value_usd": float(completed.sum()) if len(completed) else 0.0,
            "new_holdings": adds,
            "latest": str(mine["deal_date"].max())[:10] if len(mine) else None,
            "monthly": _monthly_counts(trailing, firm),
            "top_deals": _records(top.reindex(
                columns=["company", "deal_size_usd", "deal_date", "round_type"])),
        })
    return sorted(out, key=lambda f: (-f["deals"], -f["value_usd"], f["firm"]))


def _monthly_counts(trailing: pd.DataFrame, firm: str, months: int = 12) -> list:
    """Trailing per-month deal counts for a firm (sparkline; window-independent)."""
    end = pd.Timestamp.now().normalize().to_period("M")
    periods = pd.period_range(end - (months - 1), end, freq="M")
    if trailing is None or trailing.empty:
        return [0] * months
    mine = trailing[trailing["investors"].fillna("").str.contains(firm, regex=False)]
    by_month = pd.to_datetime(mine["deal_date"], errors="coerce").dt.to_period("M").value_counts()
    return [int(by_month.get(p, 0)) for p in periods]


def get_firm_drill(firm, window_days=90, env=None):
    """One firm's window deals + its syndicate: co-investors ranked by shared deals,
    split into followed firms and follow candidates."""
    df = _deals_df(env)
    if df is None or df.empty or not firm:
        return {"firm": firm, "deals": [], "co_investors": []}
    df = _filter_window(df, window_days, "deal_date")
    mine = df[df["investors"].fillna("").str.contains(str(firm), regex=False)]
    watchlist = set(_watchlist_names(env))
    counts = {}
    for names in mine["investors"].fillna(""):
        for name in {n.strip() for n in names.split(",")}:
            if name and name != firm:
                counts[name] = counts.get(name, 0) + 1
    co = [{"name": n, "n": c, "followed": n in watchlist}
          for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
    deals = mine.sort_values("deal_date", ascending=False).head(50)
    return {"firm": firm, "deals": _records(deals), "co_investors": co}


def _attach_backers(df: pd.DataFrame, watchlist: set) -> list:
    """Records with a per-deal ``backers`` syndicate (lead + followed flags)."""
    out = []
    for row in _records(df):
        leads = {n.strip() for n in str(row.get("lead_investors") or "").split(",") if n.strip()}
        names = {n.strip() for n in str(row.get("investors") or "").split(",") if n.strip()} | leads
        row["backers"] = sorted(
            ({"name": n, "lead": n in leads, "followed": n in watchlist} for n in names),
            key=lambda b: (not b["lead"], not b["followed"], b["name"]))
        out.append(row)
    return out


def get_company_backers(window_days=90, min_size_usd=0, limit=100, env=None):
    """Company-centric backers view: recent deals above a minimum size, each with
    its full syndicate (lead + followed flags per investor)."""
    df = _deals_df(env)
    if df is None or df.empty:
        return []
    df = _filter_window(df, window_days, "deal_date")
    if df.empty:
        return []
    df = df.copy()
    df["deal_size_usd"] = pd.to_numeric(df["deal_size_usd"], errors="coerce")
    if min_size_usd:
        df = df[df["deal_size_usd"] >= float(min_size_usd)]
    df = df.sort_values(["deal_date", "deal_size_usd"], ascending=False).head(int(limit))
    return _attach_backers(df, set(_watchlist_names(env)))


def get_follow_candidates(window_days=90, limit=15, env=None):
    """Investors appearing most often alongside the watchlist that are NOT followed —
    candidates to add to the firm list."""
    df = _deals_df(env)
    if df is None or df.empty:
        return []
    df = _filter_window(df, window_days, "deal_date")
    watchlist = set(_watchlist_names(env))
    counts, sample = {}, {}
    for names, company in zip(df["investors"].fillna(""), df["company"].fillna("")):
        parts = {n.strip() for n in names.split(",") if n.strip()}
        if not parts & watchlist:
            continue
        for name in parts - watchlist:
            counts[name] = counts.get(name, 0) + 1
            sample.setdefault(name, company)
    return [{"name": n, "n": c, "example": sample.get(n, "")}
            for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:int(limit)]
            if c >= 2]


def get_hot_companies(window_days=90, limit=10, env=None, search="", min_size_usd=0, apollo_only=False):
    """Companies heating up in the window: capital raised, repeat rounds, and the
    valuation step-up vs their previous priced round (full history)."""
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return []
    df = _filter_apollo(df, apollo_only).copy()
    df["deal_size_usd"] = pd.to_numeric(df["deal_size_usd"], errors="coerce")
    df["post_valuation_usd"] = pd.to_numeric(df["post_valuation_usd"], errors="coerce")
    window = _filter_window(df, window_days, "deal_date")
    # A hot-companies board is venture flow: buyouts/LBOs are a different market.
    window = window[~window.get("round_type", pd.Series("", index=window.index))
                    .fillna("").str.contains("Buyout|LBO", case=False, regex=True)]
    if window.empty:
        return []
    out = []
    for company, mine in window.groupby("company", dropna=True):
        completed = mine.loc[mine.get("deal_status", pd.Series("", index=mine.index))
                             .eq("Completed"), "deal_size_usd"]
        latest = mine.sort_values("deal_date").iloc[-1]
        priced = (df[df["company"].eq(company)].dropna(subset=["post_valuation_usd"])
                    .sort_values("deal_date"))
        step_up = None
        if len(priced) >= 2:
            pv = priced["post_valuation_usd"].iloc[-2:]
            step_up = round(float(pv.iloc[1] / pv.iloc[0]), 2) if pv.iloc[0] else None
        out.append({
            "company": company,
            "raised_usd": float(completed.sum()) if len(completed) else 0.0,
            "rounds": int(len(mine)),
            "latest_round": latest.get("round_type"),
            "latest_date": str(latest.get("deal_date"))[:10],
            "sector": latest.get("sector"),
            "post_valuation_usd": float(priced["post_valuation_usd"].iloc[-1]) if len(priced) else None,
            "step_up": step_up,
        })
    out.sort(key=lambda r: (-r["raised_usd"], -r["rounds"], r["company"]))
    return out[:int(limit)]


# ── Geography breakdown ─────────────────────────────────────────────────────

def parse_geography(region) -> tuple:
    """Map a PitchBook location string to (country, metro).

    Handles "San Francisco, CA", "New York, NY, United States", "Tel Aviv, Israel",
    and bare regions like "North America" / "Europe" / "Unknown".
    """
    text = str(region or "").strip()
    if not text or text.lower() in ("unknown", "nan", "none"):
        return "Unknown", "Unknown"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) == 1:
        return parts[0], parts[0]
    if parts[-1].upper() in _US_STATES:
        return "United States", ", ".join(parts[-2:])
    if parts[-1] == "United States":
        return "United States", ", ".join(parts[:2])
    return parts[-1], f"{parts[0]}, {parts[-1]}"


def get_geo_breakdown(window_days=90, apollo_only=False, env=None, search="", min_size_usd=0):
    """Funding by geography: per-country chart rows + per-metro table rows."""
    df = _filter_selection(_deals_df(env), search, min_size_usd)
    if df is None or df.empty:
        return {"countries": [], "metros": []}
    df = _filter_window(df, window_days, "deal_date")
    df = _filter_apollo(df, apollo_only)
    if df.empty:
        return {"countries": [], "metros": []}
    df = df.copy()
    geo = df["region"].map(parse_geography)
    df["country"] = geo.str[0]
    df["metro"] = geo.str[1]
    if "deal_status" in df:
        df.loc[~df["deal_status"].eq("Completed"), "deal_size_usd"] = None
    df["value"] = pd.to_numeric(df["deal_size_usd"], errors="coerce")

    def agg(level):
        grouped = (df.groupby(level, dropna=False)
                     .agg(deals=("deal_id", "count"), value=("value", "sum"),
                          disclosed=("value", "count"))
                     .reset_index().sort_values(["value", "deals"], ascending=False))
        return _records(grouped)

    metros = (df.groupby(["country", "metro"], dropna=False)
                .agg(deals=("deal_id", "count"), value=("value", "sum"),
                     top_sector=("sector", lambda s: s.fillna("Unclassified").mode().iat[0]
                                 if len(s) else ""))
                .reset_index().sort_values(["value", "deals"], ascending=False))
    return {"countries": agg("country"), "metros": _records(metros)}


# ── Subsector review queue ──────────────────────────────────────────────────

def list_subsectors(env=None):
    df = read_table(VC_SUBSECTORS, env=env)
    if df is None or df.empty:
        return []
    df = df[df["enabled"].astype("boolean").fillna(True)]
    return _records(df.sort_values(["sector", "sub_sector"]))


def get_review_queue(env=None, limit=200):
    # Bypasses the local cache (explicit where) so a confirm is visible immediately.
    df = read_table(VC_DEALS, env=env,
                    where="sub_sector_review = true AND source = 'pitchbook_mcp'",
                    cols="deal_id, deal_date, company, sector, sub_sector, "
                         "sub_sector_confidence, description")
    if df is None or df.empty:
        return []
    df = df.drop_duplicates("deal_id", keep="last")
    df = df.sort_values("deal_date", ascending=False).head(int(limit))
    return _records(df)


def confirm_subsector(deal_id: str, sub_sector: str, sector: str = None, env=None):
    """User validation from the dashboard: the confirmed label becomes training data."""
    if not deal_id or not sub_sector:
        return {"ok": False, "error": "deal_id and sub_sector are required"}
    row = {"deal_id": deal_id, "sub_sector": sub_sector, "sub_sector_confidence": 1.0,
           "sub_sector_source": "user", "sub_sector_review": False}
    if sector:
        row["sector"] = sector
    upsert_by_key(VC_DEALS, pd.DataFrame([row]), key="deal_id", env=env)
    return {"ok": True, "deal_id": deal_id, "sub_sector": sub_sector}


# News is served by the VC wiki reader (/vcnews), not from here — see
# data_importer/vc/news/wiki_cycle.py + dash_reports/sub_servers/vc_wiki.


# ── Sources / Universe CRUD ─────────────────────────────────────────────────

def list_sources(env=None):
    df = read_table(VC_SOURCES, where="1 = 1", env=env)
    return _records(df) if df is not None else []


def list_universe(env=None):
    df = read_table(VC_UNIVERSE, env=env)
    return _records(df) if df is not None else []


def upsert_source(row: dict, env=None):
    upsert_by_key(VC_SOURCES, pd.DataFrame([row]), key="source_id", env=env)
    return {"ok": True, "source_id": row.get("source_id")}


def toggle_source(source_id: str, enabled: bool, env=None):
    # CRUD reads bypass the cache (source of truth) via an explicit where.
    from data_access.dbtables.vc.tables import read_table_databricks
    cur = read_table_databricks(VC_SOURCES, where=f"source_id = '{_esc(source_id)}'", env=env)
    if cur is None or cur.empty:
        return {"ok": False, "error": "not found"}
    cur = cur.copy()
    cur["enabled"] = bool(enabled)
    upsert_by_key(VC_SOURCES, cur, key="source_id", env=env)
    return {"ok": True, "source_id": source_id, "enabled": bool(enabled)}


def delete_source(source_id: str, env=None):
    delete_by_key(VC_SOURCES, "source_id", source_id, env=env)
    return {"ok": True, "source_id": source_id}


# ── helpers ─────────────────────────────────────────────────────────────────

def _esc(value) -> str:
    return str(value).replace("'", "''")


def _records(df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []
    out = df.copy().astype(object)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            out[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    out = out.where(pd.notna(out), None)
    return out.to_dict("records")
