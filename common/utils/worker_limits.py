from __future__ import annotations

import logging
import os


def resolve_max_workers(name: str, default: int, *, env_var: str | None = None,
                         apo_app: str | None = None) -> int:
    if env_var:
        raw = os.environ.get(env_var)
        if raw not in (None, ""):
            return max(1, int(raw))
    if apo_app:
        try:
            from apo_config.config import config as _apo
            value = _apo.getconfig(apo_app, name)
            if value not in (None, ""):
                return max(1, int(value))
        except Exception as exc:
            logging.debug("worker_limits: apo_config lookup failed for %s: %s", name, exc)
    return max(1, int(default))
