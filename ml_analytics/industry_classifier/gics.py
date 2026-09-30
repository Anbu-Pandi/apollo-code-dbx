import logging
from typing import Optional


GICS_SECTORS = {
    10: "Energy",
    15: "Materials",
    20: "Industrials",
    25: "Consumer Discretionary",
    30: "Consumer Staples",
    35: "Health Care",
    40: "Financials",
    45: "Information Technology",
    50: "Communication Services",
    55: "Utilities",
    60: "Real Estate",
}

INDUSTRY_TO_GICS = {
    # Energy
    "oil": "Energy", "gas": "Energy", "petroleum": "Energy", "energy": "Energy",
    "fossil fuel": "Energy", "natural gas": "Energy", "crude oil": "Energy",
    "oil and gas": "Energy", "petroleum industry": "Energy", "fuel": "Energy",
    "mining": "Energy", "coal": "Energy", "drilling": "Energy", "refining": "Energy",
    "oilfield": "Energy", "lng": "Energy", "pipeline": "Energy",

    # Materials
    "chemicals": "Materials", "chemical": "Materials", "steel": "Materials",
    "metals": "Materials", "paper": "Materials", "packaging": "Materials",
    "construction materials": "Materials", "lumber": "Materials", "cement": "Materials",
    "glass": "Materials", "aluminum": "Materials", "copper": "Materials",
    "gold mining": "Materials", "fertilizer": "Materials", "plastics": "Materials",
    "forestry": "Materials", "containers": "Materials", "metallurgy": "Materials",

    # Industrials
    "aerospace": "Industrials", "defense": "Industrials", "airline": "Industrials",
    "airlines": "Industrials", "aviation": "Industrials", "logistics": "Industrials",
    "shipping": "Industrials", "railroad": "Industrials", "railway": "Industrials",
    "trucking": "Industrials", "freight": "Industrials", "construction": "Industrials",
    "engineering": "Industrials", "machinery": "Industrials", "manufacturing": "Industrials",
    "industrial": "Industrials", "conglomerate": "Industrials", "waste management": "Industrials",
    "transportation": "Industrials", "delivery": "Industrials", "courier": "Industrials",
    "electrical equipment": "Industrials", "building products": "Industrials",

    # Consumer Discretionary
    "automotive": "Consumer Discretionary", "automobile": "Consumer Discretionary",
    "retail": "Consumer Discretionary", "apparel": "Consumer Discretionary",
    "fashion": "Consumer Discretionary", "luxury": "Consumer Discretionary",
    "hotel": "Consumer Discretionary", "hospitality": "Consumer Discretionary",
    "restaurant": "Consumer Discretionary", "leisure": "Consumer Discretionary",
    "casino": "Consumer Discretionary", "gambling": "Consumer Discretionary",
    "toys": "Consumer Discretionary", "sporting goods": "Consumer Discretionary",
    "home furnishing": "Consumer Discretionary", "textiles": "Consumer Discretionary",
    "e-commerce": "Consumer Discretionary", "online retail": "Consumer Discretionary",
    "footwear": "Consumer Discretionary", "jewelry": "Consumer Discretionary",
    "consumer electronics": "Consumer Discretionary", "homebuilding": "Consumer Discretionary",
    "car": "Consumer Discretionary", "motor vehicle": "Consumer Discretionary",

    # Consumer Staples
    "food": "Consumer Staples", "beverage": "Consumer Staples", "tobacco": "Consumer Staples",
    "grocery": "Consumer Staples", "supermarket": "Consumer Staples",
    "household products": "Consumer Staples", "personal care": "Consumer Staples",
    "cosmetics": "Consumer Staples", "agriculture": "Consumer Staples",
    "dairy": "Consumer Staples", "brewing": "Consumer Staples", "distilling": "Consumer Staples",
    "winery": "Consumer Staples", "confectionery": "Consumer Staples",
    "meat processing": "Consumer Staples", "snack food": "Consumer Staples",

    # Health Care
    "pharmaceutical": "Health Care", "biotechnology": "Health Care", "biotech": "Health Care",
    "health care": "Health Care", "healthcare": "Health Care", "medical": "Health Care",
    "hospital": "Health Care", "drug": "Health Care", "medical device": "Health Care",
    "life sciences": "Health Care", "diagnostics": "Health Care", "genomics": "Health Care",
    "clinical": "Health Care", "therapy": "Health Care", "dental": "Health Care",
    "ophthalmology": "Health Care", "veterinary": "Health Care",

    # Financials
    "bank": "Financials", "banking": "Financials", "insurance": "Financials",
    "financial services": "Financials", "investment": "Financials",
    "asset management": "Financials", "private equity": "Financials",
    "venture capital": "Financials", "brokerage": "Financials", "finance": "Financials",
    "credit": "Financials", "mortgage": "Financials", "payments": "Financials",
    "fintech": "Financials", "stock exchange": "Financials", "securities": "Financials",
    "wealth management": "Financials", "accounting": "Financials",
    "reinsurance": "Financials", "capital markets": "Financials",

    # Information Technology
    "software": "Information Technology", "hardware": "Information Technology",
    "semiconductor": "Information Technology", "computer": "Information Technology",
    "information technology": "Information Technology", "technology": "Information Technology",
    "internet": "Information Technology", "cloud computing": "Information Technology",
    "cybersecurity": "Information Technology", "artificial intelligence": "Information Technology",
    "electronics": "Information Technology", "it services": "Information Technology",
    "data processing": "Information Technology", "saas": "Information Technology",
    "enterprise software": "Information Technology", "microprocessor": "Information Technology",
    "networking": "Information Technology", "it consulting": "Information Technology",
    "integrated circuit": "Information Technology",

    # Communication Services
    "telecommunications": "Communication Services", "telecom": "Communication Services",
    "media": "Communication Services", "entertainment": "Communication Services",
    "broadcasting": "Communication Services", "publishing": "Communication Services",
    "advertising": "Communication Services", "social media": "Communication Services",
    "streaming": "Communication Services", "film": "Communication Services",
    "television": "Communication Services", "music": "Communication Services",
    "video games": "Communication Services", "gaming": "Communication Services",
    "wireless": "Communication Services", "cable": "Communication Services",
    "newspaper": "Communication Services", "radio": "Communication Services",

    # Utilities
    "electric utility": "Utilities", "water utility": "Utilities", "utility": "Utilities",
    "power generation": "Utilities", "electricity": "Utilities",
    "renewable energy": "Utilities", "solar energy": "Utilities", "wind power": "Utilities",
    "nuclear power": "Utilities", "hydroelectric": "Utilities",
    "gas utility": "Utilities", "water treatment": "Utilities",

    # Real Estate
    "real estate": "Real Estate", "reit": "Real Estate", "property": "Real Estate",
    "real estate investment trust": "Real Estate", "commercial property": "Real Estate",
    "residential": "Real Estate", "mortgage reit": "Real Estate",
    "property management": "Real Estate", "real property": "Real Estate",
}

SECTOR_NAMES = list(GICS_SECTORS.values())


def map_industry_to_gics(raw_industry: str) -> Optional[str]:
    if not raw_industry:
        return None

    label = raw_industry.strip().lower()

    if label in INDUSTRY_TO_GICS:
        return INDUSTRY_TO_GICS[label]

    for keyword, sector in INDUSTRY_TO_GICS.items():
        if keyword in label:
            return sector

    return None


def map_industries_bulk(industries: list) -> list:
    mapped = []
    unmapped_set = set()
    for ind in industries:
        sector = map_industry_to_gics(ind)
        if not sector:
            unmapped_set.add(ind)
        mapped.append(sector)

    if unmapped_set:
        logging.warning(f"Unmapped industries ({len(unmapped_set)}): {list(unmapped_set)[:20]}")

    return mapped
