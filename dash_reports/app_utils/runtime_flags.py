"""One place for every dash_reports startup toggle — the whole switchboard.

Two kinds of switch live here together so you can see them all at a glance:

- **Code constants** — behavior switches that default on and only flip for
  rollback / debugging. Edit them here; they are NOT per-deployment.
- **Deployment flags** — vary per host / per instance, so they resolve from an
  env var (and apo_config where applicable) at call time, not at import.

Keep new toggles in this module rather than sprinkling ``os.environ.get(...)``
through the codebase, so there's a single, transparent source of truth.
"""

import os

from apo_config.config import config
from dash_reports import APP


# ── Code constants (edit in-file; same convention as before) ──────────────
IS_DEBUG_MODE = False   # local HTTP + Dash dev tools
USE_OKTA = True         # Okta OAuth gate

# In direct-TLS mode, serve via the hardened WSGI server so the TLS handshake
# runs in worker threads instead of werkzeug's single acceptor (a burst of
# connections or a slow/half-open client otherwise serializes connection setup
# into multi-second stalls). Inert behind a TLS-terminating proxy (no
# ssl_context). Flip to False to fall back to the stock werkzeug server.
HARDENED_TLS = True


# ── Deployment flags (per host/instance; env / apo_config) ────────────────
def app_role() -> str:
    """Fleet role (env ``ARGUS_APP_ROLE``). The whole app is scale-out safe
    (stateless gateway + CDX relays the shared sub-server) EXCEPT the Teams bot,
    which holds in-process conversation memory + a BYOT registration and so runs
    on ONE instance only.

    - ``all`` (default) -> full app incl. the Teams bot; today's single-process
      behavior, and in a fleet the one instance that runs Teams.
    - ``web``           -> everything EXCEPT the Teams bot; run N behind the LB.
    """
    return os.environ.get("ARGUS_APP_ROLE", "all").strip().lower()


def run_teams_bot() -> bool:
    """Teams runs only on the single ``all`` instance (fixed flight_server
    ``client_id='teams_bot'`` registration — N copies would race it)."""
    return app_role() != "web"


def is_behind_proxy() -> bool:
    """True when a TLS-terminating reverse proxy / load balancer fronts the app;
    it then serves plain HTTP on a loopback port and the proxy owns 443.

    Env ``ARGUS_BEHIND_REVERSE_PROXY`` wins (it doubles as the "serve plain HTTP
    locally" switch: ``ARGUS_BEHIND_REVERSE_PROXY=1`` -> http://localhost:8050);
    otherwise the apo_config ``behind_reverse_proxy`` key; default False, so the
    current direct-TLS-on-443 behavior is unchanged until flipped.
    """
    env = os.environ.get("ARGUS_BEHIND_REVERSE_PROXY", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    try:
        return str(config.getconfig(APP, "behind_reverse_proxy")).lower() in ("1", "true", "yes")
    except Exception:
        return False
