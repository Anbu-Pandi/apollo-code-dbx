"""Argus service registry: resolve a logical service name to its endpoint(s).

Depends only on apo_config (raw values); consumed by every app, so it lives here
as its own component rather than inside any single app or in pure-config.
"""
from common.service_discovery.resolver import (
    INTERNAL_HOST_KEY,
    LEGACY_SERVICE_HOST_APP,
    SERVICE_HOST_APP,
    SERVICE_HOSTS_KEY,
    resolve_internal_service_host,
    resolve_internal_service_url,
    resolve_service_hosts,
    resolve_service_url,
    resolve_service_urls,
)

__all__ = [
    "INTERNAL_HOST_KEY",
    "LEGACY_SERVICE_HOST_APP",
    "SERVICE_HOST_APP",
    "SERVICE_HOSTS_KEY",
    "resolve_internal_service_host",
    "resolve_internal_service_url",
    "resolve_service_hosts",
    "resolve_service_url",
    "resolve_service_urls",
]
