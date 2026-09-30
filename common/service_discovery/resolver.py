"""Service discovery / endpoint resolution for the Argus cluster.

Topology logic, not config: it composes raw ``app_config`` values (host/port/
scheme) into the URL a caller should dial, and decides *which* node to talk to.
Its own bounded component (a service registry) that depends only on ``apo_config``
for raw values -- every app resolves endpoints through it, so it can live neither
in ``apo_config`` (which stays pure config) nor in any single app.

Two resolution styles:
  * Pinned singleton -> ``resolve_internal_service_host`` /
    ``resolve_internal_service_url`` (reads ``cluster.internal_service_host``,
    legacy fallback ``dash_reports.internal_service_host``).
  * Co-located, run-on-every-node service -> ``resolve_service_hosts`` /
    ``resolve_service_urls`` (reads ``<app>.hosts``, local-first with fallback).
"""
import re
import socket

from apo_config.config import config, required_config

# internal_service_host is a CLUSTER-level topology setting (where the pinned
# singletons run) -- not a dash_reports concern. It historically lived under the
# 'dash_reports' app; we read the neutral 'cluster' namespace first and fall back
# to the legacy location, so this shared module carries no app-specific coupling.
SERVICE_HOST_APP = "cluster"
LEGACY_SERVICE_HOST_APP = "dash_reports"
INTERNAL_HOST_KEY = "internal_service_host"

# Per-app config key listing the node hostnames a service runs on, e.g.
#   mcp_server.hosts = pue2mal01/pue2mal02
# Comma- or slash-separated. Drives local-first resolution (resolve_service_hosts).
SERVICE_HOSTS_KEY = "hosts"


def resolve_internal_service_host():
    value = (
        config.getconfig(SERVICE_HOST_APP, INTERNAL_HOST_KEY)
        or config.getconfig(LEGACY_SERVICE_HOST_APP, INTERNAL_HOST_KEY)
    )
    if not value:
        raise RuntimeError(
            f"Missing dbo.app_config value: {SERVICE_HOST_APP}.{INTERNAL_HOST_KEY} "
            f"(or legacy {LEGACY_SERVICE_HOST_APP}.{INTERNAL_HOST_KEY})"
        )
    return value


def resolve_internal_service_url(app, port_key="server.port", scheme_key="server.scheme", path=""):
    port = int(required_config(app, port_key))
    scheme = required_config(app, scheme_key)
    suffix = f"/{path.lstrip('/')}" if path else ""
    return f"{scheme}://{resolve_internal_service_host()}:{port}{suffix}"


def _local_aliases():
    """Names that mean 'this machine' when matching a configured host list."""
    name = socket.gethostname().lower()
    return {name, name.split(".")[0], "localhost", "127.0.0.1"}


def resolve_service_hosts(app):
    """Ordered hosts for a service, local-first with peer fallback.

    Reads ``<app>.hosts`` -- a comma/slash-separated list of the node hostnames
    the service runs on (e.g. ``pue2mal01/pue2mal02``). Behaviour:

    * If this machine is one of the listed hosts, we talk to our own co-located
      copy first (``127.0.0.1``) and keep the remaining hosts as fallbacks --
      "whichever is available".
    * If ``<app>.hosts`` is unset, we fall back to the pinned
      ``internal_service_host`` (correct for singletons -- unchanged behaviour).
    * If a list is set but this machine is not in it, we use the listed hosts in
      order (this node reaches out to wherever the service actually runs).

    Keying on the local hostname (not the OS account) makes resolution robust
    even though both nodes run services under the same service account.
    """
    raw = config.getconfig(app, SERVICE_HOSTS_KEY)
    if not raw:
        return [resolve_internal_service_host()]
    hosts = [h.strip() for h in re.split(r"[,/]", str(raw)) if h.strip()]
    if not hosts:
        return [resolve_internal_service_host()]
    local = _local_aliases()
    localish = ["127.0.0.1"] if any(h.lower() in local for h in hosts) else []
    remote = [h for h in hosts if h.lower() not in local]
    return localish + remote


def resolve_service_urls(app, port_key="server.port", scheme_key="server.scheme", path=""):
    """Candidate base URLs for a co-located service, local-first (see
    resolve_service_hosts). Callers try them in order and fall through on a
    connection failure."""
    port = int(required_config(app, port_key))
    scheme = required_config(app, scheme_key)
    suffix = f"/{path.lstrip('/')}" if path else ""
    return [f"{scheme}://{host}:{port}{suffix}" for host in resolve_service_hosts(app)]


def resolve_service_url(app, port_key="server.port", scheme_key="server.scheme", path=""):
    """Primary (local-first) base URL -- drop-in for single-endpoint callers
    that don't implement fallback."""
    return resolve_service_urls(app, port_key, scheme_key, path)[0]
