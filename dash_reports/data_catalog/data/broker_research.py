import json
import logging
import math
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from apo_config.config import config
from dash_reports.data_catalog.data.categorization import category_description
from dash_reports.data_catalog.data.models import (
    ASSET_COLUMNS,
    AssetKey,
    SourceCapabilities,
    asset_record,
)

log = logging.getLogger(__name__)

SOURCE = "Broker Research"
CONNECTION = "argus_wiki"
CATALOG = "BrokerResearch"
CATEGORY = "Research & analyst content"

_EXT_KIND = {
    ".pdf": "PDF",
    ".eml": "email",
    ".msg": "email",
    ".html": "HTML",
    ".htm": "HTML",
    ".txt": "text",
    ".md": "note",
}
_TYPE_LABELS = {
    "sell_side": "Sell-Side Research",
    "independent_research": "Independent Research",
    "buy_side": "Buy-Side Research",
    "internal_research": "Internal Research",
    "media": "Media",
    "primary_source": "Primary Source",
    "unknown": "Research",
}
_CAPABILITIES = SourceCapabilities(
    columns=False, tags=False, lineage=False, quality=False, usage=True, access=True
)


def _vault_root():
    root = config.getconfig(CONNECTION, "vault_root")
    return Path(str(root)) if root else None


def _dimensions(vault):
    path = vault / os.environ.get("ARGUS_CONFIG_DIR_NAME", "config") / "dimensions.yaml"
    if not path.exists():
        return {}, {}, {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = (data.get("display") or {}).get("sender_labels") or {}
    entries = [s for s in (data.get("senders") or []) if isinstance(s, dict) and s.get("name")]
    types = {str(s["name"]): str(s.get("type") or "unknown") for s in entries}
    alias = {}
    for entry in entries:
        canonical = str(entry["name"])
        alias[canonical.lower()] = canonical
        for name in entry.get("aliases") or []:
            alias[str(name).lower()] = canonical
    return labels, types, alias


def _read_manifest(vault):
    path = vault / os.environ.get("ARGUS_STATE_DIR_NAME", "state") / "ingested.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _canonical(row, alias):
    affected = row.get("affected") or {}
    names = affected.get("senders") or affected.get("brokers") or []
    name = str((names[0] if names else row.get("sender")) or "").strip()
    return alias.get(name.lower(), name)


def _content_kind(row):
    ext = os.path.splitext(str(row.get("raw_path") or ""))[1].lower()
    if ext in _EXT_KIND:
        return _EXT_KIND[ext]
    kind = str(row.get("kind") or "").lower()
    return _EXT_KIND.get(f".{kind}", kind or "other")


def _day(row):
    value = str(row.get("day") or "").strip()
    if len(value) == 8 and value.isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    stamp = str(row.get("ingested_at") or "")[:10]
    try:
        return datetime.strptime(stamp, "%Y-%m-%d").date()
    except ValueError:
        return None


def _record(canonical, rows, labels, types):
    label = labels.get(canonical, canonical)
    sender_type = types.get(canonical, str(rows[0].get("sender_type") or "unknown"))
    schema = _TYPE_LABELS.get(sender_type, _TYPE_LABELS["unknown"])
    days = sorted(day for day in (_day(row) for row in rows) if day)
    kinds = Counter(_content_kind(row) for row in rows)
    doc_kinds = Counter(str(row.get("doc_kind") or "").strip() for row in rows if row.get("doc_kind"))
    total = len(rows)
    today = date.today()
    q30 = sum(1 for day in days if (today - day).days <= 30)
    q90 = sum(1 for day in days if (today - day).days <= 90)
    earliest = days[0] if days else None
    latest = days[-1] if days else None
    span = (latest - earliest).days if days else 0
    kind_text = ", ".join(f"{count} {name}" for name, count in kinds.most_common())
    coverage = f"{earliest:%Y-%m-%d} to {latest:%Y-%m-%d}" if days else "no dated documents"
    description = (
        f"{label} {schema.lower()} ingested into the Argus wiki — {total:,} documents"
        f"{f' ({kind_text})' if kind_text else ''} spanning {coverage} ({span:,} days), "
        f"{q30:,} in the last 30 days."
    )
    usage_score = 4 * math.log1p(q30) + math.log1p(max(q90 - q30, 0))
    age = (today - latest).days if latest else 365
    recency = max(0, 365 - min(age, 365)) / 365 * 9
    key = AssetKey(SOURCE, CONNECTION, CATALOG, schema, label)
    tags = ["tier=gold", *(f"{name}:{count}" for name, count in doc_kinds.most_common(6))]
    return asset_record(
        key,
        _CAPABILITIES,
        object_type="Research feed",
        owner="Argus Research Wiki",
        comment=description,
        tags=", ".join(tags),
        vendor=label,
        data_category=CATEGORY,
        category_description=category_description(CATEGORY),
        business_description=description,
        example_fields=", ".join(name for name, _ in doc_kinds.most_common(8)),
        asset_kind="Research feed",
        domain=CATEGORY,
        provider=label,
        created=pd.Timestamp(earliest) if earliest else pd.NaT,
        last_altered=pd.Timestamp(latest) if latest else pd.NaT,
        data_format=kind_text,
        row_count=total,
        column_count=0,
        documented=True,
        certified=False,
        has_lineage=False,
        has_quality=False,
        has_usage=q90 > 0,
        queries_30d=q30,
        queries_90d=q90,
        last_accessed=latest.isoformat() if latest else "",
        service_can_select=True,
        ranking_tier=0 if q30 else 1,
        ranking_score=round(usage_score * 100 + 20 + recency, 6),
    )


def broker_research_assets():
    vault = _vault_root()
    if not vault or not vault.exists():
        return pd.DataFrame(columns=ASSET_COLUMNS)
    rows = _read_manifest(vault)
    if not rows:
        return pd.DataFrame(columns=ASSET_COLUMNS)
    labels, types, alias = _dimensions(vault)
    grouped = defaultdict(list)
    for row in rows:
        canonical = _canonical(row, alias)
        if canonical:
            grouped[canonical].append(row)
    records = [_record(name, items, labels, types) for name, items in grouped.items()]
    frame = pd.DataFrame(records, columns=ASSET_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values("ranking_score", ascending=False).reset_index(drop=True)
