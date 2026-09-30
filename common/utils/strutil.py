import re
from urllib.parse import urlparse
from typing import List

# Generic tokens that should be ignored when building route hashes
GENERIC_ROUTE_TOKENS = {
    "www", "com", "net", "org", "co", "research", "markets", 
    "market", "login", "static", "api", "auth"
}
ROUTE_TOKEN_SPLIT_REGEX = re.compile(r"[^a-z0-9]+")
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
DOMAIN_LIKE_REGEX = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_host_from_url(url_or_host: str) -> str:
    """
    Extracts and normalizes the host from a full URL or host string.
    Removes 'www.', trims, and converts to lowercase.
    """
    raw = str(url_or_host or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or parsed.path or "").split("@")[-1].split(":")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def root_domain(host: str) -> str:
    """
    Gets the root domain (e.g., 'example.com') from a sub-domained host (e.g., 'api.example.com').
    """
    normalized = normalize_host_from_url(host)
    if not normalized or "." not in normalized:
        return normalized
    parts = [part for part in normalized.split(".") if part]
    if len(parts) < 2:
        return normalized
    return ".".join(parts[-2:])


def dedupe_normalized(values: List[str]) -> List[str]:
    """
    Removes duplicates from a list of strings, ignoring case and whitespace.
    Order is preserved.
    """
    deduped: List[str] = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def tokenize_route_source(value: str) -> List[str]:
    """
    Splits a string into route tokens, ignoring short tokens and generic ones.
    """
    tokens = [token for token in ROUTE_TOKEN_SPLIT_REGEX.split(str(value or "").lower()) if token]
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in GENERIC_ROUTE_TOKENS
    ]
