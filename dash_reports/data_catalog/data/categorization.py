import re
from difflib import SequenceMatcher

from data_access.data_catalog.rules import (
    DATA_CATALOG_CATEGORY_DESCRIPTIONS,
    DATA_CATALOG_PREFIX_RULES,
    DATA_CATALOG_PROVIDER_RULES,
)

PREFIX_RULES = DATA_CATALOG_PREFIX_RULES
BUSINESS_TOPICS = {
    "Credit pricing & quotes": {
        "description": "Bid/ask, spreads, yields, prices, TRACE prints, indexes, and security reference data.",
        "examples": "Markit · Bloomberg · ICE BofA · Tradeweb",
        "icon": "◉",
    },
    "Loans, covenants & issuance": {
        "description": "Deal terms, covenant tests, facilities, borrowers, and new-issue pipelines.",
        "examples": "LCD · Covenant Review · Dealogic",
        "icon": "◇",
    },
    "Derivatives, rates & vol": {
        "description": "CDS levels, swap curves, futures, options, volatility surfaces, and regulatory trades.",
        "examples": "DTCC SDR · Markit · JPM DataQuery",
        "icon": "▥",
    },
    "Company financials & estimates": {
        "description": "P&L, balance sheets, cash flow, estimates, guidance, geography, and segment splits.",
        "examples": "FactSet · analyst research",
        "icon": "▤",
    },
    "Macro & alt-data series": {
        "description": "Economic vintages, web traffic, app activity, and other external time series.",
        "examples": "Haver · Similarweb · Sensor Tower",
        "icon": "◎",
    },
    "Our book — risk & P&L": {
        "description": "Exposure, beta, duration, DV01, Greeks, positions, scenarios, and P&L by fund.",
        "examples": "Permissioned to you",
        "icon": "♢",
    },
    "Securities, issuers & reference": {
        "description": "Security terms, identifiers, issuer hierarchies, ratings, currencies, and crosswalks.",
        "examples": "CUSIP · ISIN · Bloomberg · reference master",
        "icon": "⌘",
    },
    "Portfolio holdings & positions": {
        "description": "Current and historical holdings, quantities, costs, market values, and ownership.",
        "examples": "Funds · portfolios · positions · holdings",
        "icon": "▦",
    },
    "Funds & investor analytics": {
        "description": "Fund structures, strategies, commitments, cash flows, performance, and investors.",
        "examples": "Fund master · NAV · commitments · performance",
        "icon": "◫",
    },
    "Trading & execution": {
        "description": "Orders, fills, tickets, transaction costs, markouts, and execution workflows.",
        "examples": "TRACE · electronic trading · cash movements",
        "icon": "⇄",
    },
    "Indexes & benchmarks": {
        "description": "Index constituents, weights, returns, spreads, yields, ratings, and benchmark analytics.",
        "examples": "ICE BofA · Bloomberg indexes · market benchmarks",
        "icon": "⌁",
    },
    "Private markets & direct lending": {
        "description": "Private-credit borrowers, facilities, sponsors, investments, terms, and performance.",
        "examples": "Direct lending · BDC holdings · private assets",
        "icon": "⬡",
    },
    "Research & analyst content": {
        "description": "Company research, analyst questionnaires, estimates, themes, and ingested publications.",
        "examples": "Sell-side research · independent research · analyst views",
        "icon": "◧",
    },
    "Scenarios & quantitative analytics": {
        "description": "Betas, factors, shocked markets, modeled returns, sensitivities, and quantitative features.",
        "examples": "Scenario P&L · beta estimation · factor analytics",
        "icon": "⌇",
    },
    "Data quality & governance": {
        "description": "Definitions, ownership, tags, lineage, quality checks, mappings, and operational metadata.",
        "examples": "Delphi · field metadata · quality monitors",
        "icon": "✓",
    },
    "Reporting & productivity": {
        "description": "Curated reporting datasets and operational measures prepared for repeatable analysis.",
        "examples": "Power BI · shared dimensions · productivity measures",
        "icon": "▣",
    },
    "Enterprise & operational data": {
        "description": "Production datasets supporting enterprise applications, workflows, and shared operations.",
        "examples": "Core platforms · application data · operational records",
        "icon": "⌂",
    },
}
_CATEGORY_TOPICS = {
    "Analyst": "Research & analyst content",
    "BDC Holdings": "Private markets & direct lending",
    "Beta Estimation": "Scenarios & quantitative analytics",
    "Bond Features": "Credit pricing & quotes",
    "Bond Issuers": "Loans, covenants & issuance",
    "Bond Securities": "Loans, covenants & issuance",
    "Bond Trades": "Trading & execution",
    "Bonds": "Loans, covenants & issuance",
    "Broker Research": "Research & analyst content",
    "CreditSights": "Loans, covenants & issuance",
    "Dealogic": "Loans, covenants & issuance",
    "Debt Features": "Credit pricing & quotes",
    "Direct Lending": "Private markets & direct lending",
    "Equity": "Company financials & estimates",
    "FactSet": "Company financials & estimates",
    "Funds": "Funds & investor analytics",
    "Futures": "Derivatives, rates & vol",
    "Holdings": "Portfolio holdings & positions",
    "Index": "Indexes & benchmarks",
    "Instruments": "Securities, issuers & reference",
    "Issuers": "Securities, issuers & reference",
    "JPM": "Derivatives, rates & vol",
    "Macro": "Macro & alt-data series",
    "Market Data": "Credit pricing & quotes",
    "Markit": "Derivatives, rates & vol",
    "Metadata": "Data quality & governance",
    "Options": "Derivatives, rates & vol",
    "PnL": "Our book — risk & P&L",
    "PnL Chat Context": "Our book — risk & P&L",
    "Portfolio": "Portfolio holdings & positions",
    "Positions": "Portfolio holdings & positions",
    "Power BI": "Reporting & productivity",
    "Pricing": "Credit pricing & quotes",
    "Productivity": "Macro & alt-data series",
    "Quality": "Data quality & governance",
    "Rates": "Derivatives, rates & vol",
    "Reference Data": "Securities, issuers & reference",
    "Risk": "Our book — risk & P&L",
    "Risk / PnL": "Our book — risk & P&L",
    "SDR / Trades": "Derivatives, rates & vol",
    "SDR CDX": "Derivatives, rates & vol",
    "SDR CDX Options": "Derivatives, rates & vol",
    "Scenarios": "Scenarios & quantitative analytics",
    "Securities": "Securities, issuers & reference",
    "Security": "Securities, issuers & reference",
    "Semi Analysis": "Research & analyst content",
    "TRACE": "Credit pricing & quotes",
    "Trading": "Trading & execution",
}
FIELD_LABELS = {
    "asof_date": "as-of date",
    "fund_entity_code": "fund",
    "fo_strategy_broad_category": "strategy",
    "issuer_ult": "ultimate issuer",
    "mv_usd": "market value",
    "mve_usd": "market-value exposure",
    "amt_outstanding_loc": "amount outstanding",
    "effective_duration": "effective duration",
    "spread_duration": "spread duration",
    "column_name": "field",
    "percent_null": "null rate",
}
TECHNICAL_FIELD_TOKENS = (
    "load_",
    "system_",
    "knowledge_date",
    "inserted_",
    "updated_",
    "file_path",
    "row_number",
)
FIELD_PRIORITY = (
    "asof",
    "effective_date",
    "fund",
    "portfolio",
    "issuer",
    "security",
    "instrument",
    "notional",
    "market_value",
    "mv_usd",
    "price",
    "yield",
    "spread",
    "rating",
    "maturity",
    "quantity",
    "return",
    "pnl",
)
WORD_LABELS = {
    "apo": "Apollo",
    "bdc": "BDC",
    "bbg": "Bloomberg",
    "cdx": "CDX",
    "cusip": "CUSIP",
    "dcs": "DCS",
    "hist": "History",
    "id": "ID",
    "isin": "ISIN",
    "jpm": "JPM",
    "pbi": "Power BI",
    "pnl": "PnL",
    "sdr": "SDR",
    "trace": "TRACE",
    "upi": "UPI",
}


def categorize(name, schema="", catalog=""):
    lower = str(name).lower()
    for prefix, domain in PREFIX_RULES:
        if lower.startswith(prefix):
            return _CATEGORY_TOPICS.get(domain, "Credit pricing & quotes")
    schema_lower = str(schema).lower()
    if schema_lower in {"risk", "portfolio", "security", "quality"}:
        return _CATEGORY_TOPICS[schema_lower.title()]
    catalog_lower = str(catalog).lower()
    if "factset" in catalog_lower:
        return "Company financials & estimates"
    if "portfolio" in catalog_lower:
        return "Portfolio holdings & positions"
    if "marketdata" in catalog_lower:
        return "Credit pricing & quotes"
    if "reference" in catalog_lower:
        return "Securities, issuers & reference"
    evidence = " ".join((lower, schema_lower, catalog_lower))
    rules = (
        ("Our book — risk & P&L", ("risk", "pnl", "exposure", "duration", "dv01", "greek")),
        ("Derivatives, rates & vol", ("sdr", "cdx", "swap", "option", "future", "volatility", "derivative", "rates")),
        ("Private markets & direct lending", ("direct_lending", "private_credit", "private_market", "bdc")),
        ("Loans, covenants & issuance", ("loan", "covenant", "issuance", "facility", "borrower", "dealogic")),
        ("Macro & alt-data series", ("macro", "haver", "similarweb", "sensortower", "economic", "productivity")),
        ("Company financials & estimates", ("financial", "estimate", "fundamental", "earnings", "analyst", "equity")),
        ("Portfolio holdings & positions", ("portfolio", "position", "holding")),
        ("Funds & investor analytics", ("fund", "investor", "commitment", "nav")),
        ("Trading & execution", ("trade", "order", "fill", "execution", "ticket")),
        ("Indexes & benchmarks", ("index", "benchmark", "constituent")),
        ("Research & analyst content", ("research", "publication", "questionnaire")),
        ("Scenarios & quantitative analytics", ("scenario", "beta", "factor", "model")),
        ("Data quality & governance", ("quality", "metadata", "governance", "lineage")),
        ("Reporting & productivity", ("report", "power_bi", "dashboard")),
        ("Securities, issuers & reference", ("security", "issuer", "instrument", "reference", "cusip", "isin")),
    )
    return next(
        (topic for topic, tokens in rules if any(token in evidence for token in tokens)),
        "Enterprise & operational data",
    )


def _provider_match(value, token):
    value = str(value).lower()
    token = str(token).lower()
    compact_token = re.sub(r"[^a-z0-9]", "", token)
    if len(compact_token) >= 5:
        return compact_token in re.sub(r"[^a-z0-9]", "", value)
    normalized = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    normalized_token = re.sub(r"[^a-z0-9]+", "_", token).strip("_")
    return bool(re.search(rf"(?:^|_){re.escape(normalized_token)}(?:_|$)", normalized))


def provider_for(
    catalog,
    schema="",
    name="",
    comment="",
    tags="",
    connection="",
):
    evidence = (connection, catalog, schema, name, tags, comment)
    return next(
        (
            provider
            for value in evidence
            for token, provider in DATA_CATALOG_PROVIDER_RULES
            if _provider_match(value, token)
        ),
        "Apollo",
    )


def category_description(category):
    if category in BUSINESS_TOPICS:
        return BUSINESS_TOPICS[category]["description"]
    return DATA_CATALOG_CATEGORY_DESCRIPTIONS.get(
        category,
        "Newly classified assets awaiting a steward-approved business definition.",
    )


def asset_kind(object_type):
    return "Ready-to-use view" if "VIEW" in str(object_type).upper() else "Source table"


def friendly_name(value):
    words = str(value).strip("_").replace("__", "_").split("_")
    if words and words[0].lower() == "v":
        words = words[1:]
    return " ".join(WORD_LABELS.get(word.lower(), word.title()) for word in words).strip()


def _field_label(value):
    normalized = str(value).strip().lower()
    return FIELD_LABELS.get(normalized, normalized.replace("_", " "))


def _representative_fields(fields, limit=5):
    candidates = []
    seen = set()
    for position, field in enumerate(fields):
        normalized = str(field).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        if any(token in key for token in TECHNICAL_FIELD_TOKENS):
            continue
        seen.add(key)
        candidates.append((position, key, normalized))
    selected = []
    selected_keys = set()
    for token in FIELD_PRIORITY:
        match = next(
            (candidate for candidate in candidates if token in candidate[1] and candidate[1] not in selected_keys),
            None,
        )
        if match:
            selected.append(match)
            selected_keys.add(match[1])
        if len(selected) == limit:
            break
    selected.extend(
        candidate
        for candidate in candidates
        if candidate[1] not in selected_keys
    )
    return [_field_label(value) for _, _, value in selected[:limit]]


def _source_summary(comment):
    text = " ".join(str(comment or "").split())
    if len(text) < 25:
        return ""
    generic = (
        "significant to the business",
        "crucial for",
        "valuable resource",
        "allows for analysis",
        "informed decision",
    )
    useful = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if any(phrase in sentence.lower() for phrase in generic):
            break
        useful.append(sentence)
        if len(useful) == 2 or sum(map(len, useful)) >= 320:
            break
    return " ".join(useful)


def describe_asset(record, fields):
    name = friendly_name(record.get("name", "Dataset"))
    vendor = record.get("vendor") or record.get("provider") or "Apollo"
    kind = (record.get("asset_kind") or "Dataset").lower()
    category = record.get("data_category") or record.get("domain") or "General"
    representative = _representative_fields(fields)
    field_text = ", ".join(representative[:-1])
    if len(representative) > 1:
        field_text += f", and {representative[-1]}"
    elif representative:
        field_text = representative[0]
    summary = _source_summary(record.get("comment"))
    evidence = f" Key fields include {field_text}." if field_text else ""
    purpose = summary or category_description(category)
    return f"{name} is a {kind} from {vendor}. {purpose}{evidence}"


def _similarity(token, text):
    words = text.split()
    return max((SequenceMatcher(None, token, word).ratio() for word in words), default=0)


def score_record(record, query):
    tokens = [token for token in str(query).lower().split() if token]
    if not tokens:
        return 1.0
    name = str(record.get("name", "")).lower()
    full_name = str(record.get("full_name", "")).lower()
    primary = " ".join(
        str(value)
        for key, value in record.items()
        if key not in {"asset_id", "name", "full_name", "column_search"}
    ).lower()
    columns = str(record.get("column_search", "")).lower()
    score = 0.0
    for token in tokens:
        if name == token:
            score += 100
        elif name.startswith(token):
            score += 60
        elif token in name:
            score += 40
        elif token in full_name:
            score += 30
        elif token in primary:
            score += 20
        elif token in columns:
            score += 12
        else:
            similarity = _similarity(token, f"{name} {primary}")
            if similarity < 0.68:
                return 0.0
            score += similarity * 8
    return score
