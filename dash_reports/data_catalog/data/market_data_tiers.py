import logging
import time

import requests

from apo_config.config import config
from dash_reports.data_catalog.data.categorization import (
    asset_kind,
    categorize,
    category_description,
    provider_for,
)
from dash_reports.data_catalog.data.models import AssetKey, SourceCapabilities, asset_record

log = logging.getLogger(__name__)

SECTION = "delphi_portal"
DEFAULT_BASE_URL = "https://delphi-portal-mcp.aks.apollo.com/api"
DEFAULT_TOKEN_URL = "https://apollo-prod.okta.com/oauth2/aus11xymubl0H9hb62p8/v1/token"
UNIVERSE_PATH = "/v1/assets/marketplace-universe"
PAGE_SIZE = 5000
DOMAIN_TYPE = "Physical Data Dictionary"
VALID_TIERS = {"bronze", "silver", "gold"}
_CAPABILITIES = SourceCapabilities(
    columns=False,
    tags=True,
    lineage=False,
    quality=False,
    usage=False,
    access=False,
)


def _cfg(key, default=""):
    return config.getconfig(SECTION, key) or default


def _token():
    static = _cfg("token")
    if static:
        return str(static)
    client_id = _cfg("client_id")
    client_secret = _cfg("client_secret")
    if not (client_id and client_secret):
        return None
    response = requests.post(
        str(_cfg("token_url", DEFAULT_TOKEN_URL)),
        data={"grant_type": "client_credentials", "scope": str(_cfg("scope"))},
        auth=(str(client_id), str(client_secret)),
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("access_token")


def _fetch_universe(token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{str(_cfg('base_url', DEFAULT_BASE_URL)).rstrip('/')}{UNIVERSE_PATH}"
    universe = []
    offset = 0
    while True:
        for attempt in range(3):
            response = requests.get(
                url,
                headers=headers,
                params={"limit": PAGE_SIZE, "offset": offset},
                timeout=120,
            )
            if response.status_code not in {429, 502, 503, 504}:
                break
            if attempt < 2:
                time.sleep(2 ** attempt)
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        universe.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += len(page)
    return universe


def parse_badges(universe):
    badges = {}
    for asset in universe or []:
        if asset.get("domain_type") != DOMAIN_TYPE:
            continue
        parts = str(asset.get("full_name") or "").split(">")
        if len(parts) != 4:
            continue
        badge = str((asset.get("string_attributes") or {}).get("badge") or "").strip()
        if badge.lower() in VALID_TIERS:
            badges[(parts[1].lower(), parts[2].lower(), parts[3].lower())] = str(badge)
    return badges


def parse_governance_assets(universe):
    records = []
    for asset in universe or []:
        parts = str(asset.get("full_name") or "").split(">")
        if (
            asset.get("domain_type") != DOMAIN_TYPE
            or len(parts) != 4
        ):
            continue
        attributes = asset.get("string_attributes") or {}
        definition = str(attributes.get("definition") or "")
        badge = str(attributes.get("badge") or "").strip().lower()
        badge = badge if badge in VALID_TIERS else ""
        tags = f"tier={badge}" if badge else ""
        object_type = attributes.get("tableType") or asset.get("type_name") or "TABLE"
        key = AssetKey("Snowflake", SECTION, parts[1], parts[2], parts[3])
        category = categorize(key.name, key.schema, key.catalog)
        vendor = provider_for(
            key.catalog,
            key.schema,
            key.name,
            definition,
            tags,
            connection=SECTION,
        )
        records.append(
            asset_record(
                key,
                _CAPABILITIES,
                object_type=object_type,
                owner=attributes.get("businessOwner") or "",
                comment=definition,
                tags=tags,
                vendor=vendor,
                data_category=category,
                category_description=category_description(category),
                business_description=definition,
                asset_kind=asset_kind(object_type),
                domain=category,
                provider=vendor,
                created=asset.get("created_on") or "",
                last_altered=asset.get("modified_on") or "",
                data_format="Snowflake",
                documented=bool(definition),
                certified=False,
                has_lineage=False,
                has_quality=False,
                has_usage=False,
                service_can_select=False,
                ranking_tier=1 if badge else 2,
                ranking_score=float((asset.get("numeric_attributes") or {}).get("badgeScore") or 0),
            )
        )
    return records


def marketplace_universe():
    return _fetch_universe(_token())


def market_data_tiers():
    return parse_badges(marketplace_universe())
