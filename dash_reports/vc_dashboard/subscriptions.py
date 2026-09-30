import hashlib
from html import escape
from urllib.parse import urlencode

import pandas as pd

from dash_reports.subscriptions import Digest, Filter, Report
from dash_reports.vc_dashboard.constants import BACKERS_MIN_SIZE_OPTIONS, WINDOW_OPTIONS


def build_digest(filters, now, review=False):
    from apo_config.config import config
    from dash_reports.vc_dashboard.data import queries
    from data_access.dbtables.vc.tables import read_table_databricks
    frame = read_table_databricks(queries.VC_DEALS, env=config.getenv())
    if frame is None:
        raise RuntimeError("VC deal data is unavailable.")
    if not frame.empty:
        frame = frame[frame["source"].eq("pitchbook_mcp")].copy()
        frame = frame.sort_values("ingested_at", na_position="first").drop_duplicates("deal_id", keep="last")
        if review:
            frame = frame[frame["sub_sector_review"].fillna(False).astype(bool)]
        else:
            end = pd.Timestamp(now).tz_convert("America/New_York").tz_localize(None).normalize() + pd.Timedelta(days=1)
            dates = pd.to_datetime(frame["deal_date"], errors="coerce")
            frame = frame[dates.ge(end - pd.Timedelta(days=filters["window_days"])) & dates.lt(end)]
        frame = queries._filter_selection(queries._filter_apollo(frame, filters["apollo_only"]),
                                          filters["search"], filters["min_size_usd"])
    columns = ["deal_id", "company", "deal_date", "deal_status", "deal_size_usd", "round_type", "sector",
               "sub_sector", "investors", "sub_sector_confidence"]
    snapshot = frame.reindex(columns=columns).sort_values(["deal_date", "deal_id"], ascending=[False, True])
    fingerprint = hashlib.sha256(snapshot.to_json(orient="records", date_format="iso").encode()).hexdigest()
    display = snapshot.head(50).drop(columns=["deal_id", "sub_sector_confidence"] if not review else ["deal_id"])
    display["deal_date"] = pd.to_datetime(display["deal_date"], errors="coerce").dt.strftime("%d %b %Y").fillna("")
    display["deal_size_usd"] = pd.to_numeric(display["deal_size_usd"], errors="coerce").map(lambda value: f"${value:,.0f}" if pd.notna(value) else "Undisclosed")
    display.columns = [column.replace("_usd", " (USD)").replace("_", " ").title() for column in display.columns]
    base = config.getconfig("dash_reports", "email_subscriptions_url") or "https://argus.apollo.com"
    params = {"tab": "deals" if review else "funding", "q": filters["search"],
              "min": filters["min_size_usd"], "apollo": "1" if filters["apollo_only"] else "0",
              "window": filters.get("window_days", 365)}
    url = base.rstrip("/") + "/vc-dashboard?" + urlencode(params)
    description = f"{len(snapshot)} matching {'deals awaiting subsector review' if review else 'deals'}. Showing up to 50, newest first."
    coverage = "Coverage follows the tracked PitchBook firms. Values represent entire rounds, not investor check sizes."
    body = f'<p>{description}</p><p>{escape(coverage)}</p>' + display.fillna("").to_html(index=False, escape=True, border=0)
    body += f'<p><a href="{escape(url, quote=True)}">Open this selection in VC dashboard</a></p>'
    return Digest(body, description + "\n" + coverage + "\n\n" + display.fillna("").to_string(index=False) + "\n\n" + url,
                  fingerprint, len(snapshot))


def register(registry):
    filters = (Filter("search", "Company, investor, sector or location"),
               Filter("min_size_usd", "Minimum deal size (USD)", "choice", 0,
                      tuple((option["value"], option["label"]) for option in BACKERS_MIN_SIZE_OPTIONS)),
               Filter("apollo_only", "Apollo-relevant only", "bool", False))
    registry.register(Report("vc_deals", "VC deal updates", "A snapshot of matching deals in your chosen window, using the same search and filters as the dashboard.",
        "/vc-dashboard", build_digest,
        filters + (Filter("window_days", "Lookback window", "choice", 30,
                          tuple((option["value"], option["label"]) for option in WINDOW_OPTIONS)),)))
    registry.register(Report("vc_review", "VC subsector reviews", "Matching deals awaiting subsector confirmation. Review the suggestions in the dashboard.",
        "/vc-dashboard", lambda filters, now: build_digest(filters, now, review=True), filters))
