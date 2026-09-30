"""Pluggable parallel-backend registry for JobExecutor.

Lives in `common` (the central/leaf package) but knows NOTHING about any concrete
backend. Backends register themselves at import time via `register_backend(name, factory)`,
so adding a backend never requires editing `common` or JobExecutor.

Discovery — how the backend modules get imported so their registration runs — is
config-driven, so `common` does not name `data_access` either:

    apo_data_access.parallel.backend_modules = "data_access.parallel"   # comma-separated

Each listed module, on import, calls register_backend(...) for the backends it provides
(e.g. data_access/parallel/__init__.py imports the ray + spark backend modules). The
default is the single convention package `data_access.parallel`; override it in config to
point elsewhere. The only string `common` carries is that one overridable convention — not
`ray`, not `spark`.

Backend factory contract:
    factory(max_workers, timeout) -> object with
        submitjob(funcs, params, initconfig=False, job_name=None, **func_kwargs)
            -> [{'result': ..., 'error': ...}, ...]   # same shape JobExecutor returns
"""
import importlib
import logging

# Convention default; overridable via apo_data_access.parallel.backend_modules.
_DEFAULT_BACKEND_PACKAGE = 'data_access.parallel'

_BACKENDS = {}
_discovered = False


def register_backend(name, factory):
    """Register a backend factory under an exec_type name (called by backend modules)."""
    _BACKENDS[name.lower()] = factory
    logging.info(f'registered parallel backend: {name}')


def _backend_modules():
    try:
        from apo_config.config import config
        mods = config.getconfig('apo_data_access.parallel', 'backend_modules')
    except Exception:
        mods = None
    if not mods:
        return [_DEFAULT_BACKEND_PACKAGE]
    if isinstance(mods, str):
        return [m.strip() for m in mods.split(',') if m.strip()]
    return list(mods)


def ensure_loaded():
    """Import the configured backend modules once so they self-register."""
    global _discovered
    if _discovered:
        return
    _discovered = True
    for module_path in _backend_modules():
        try:
            importlib.import_module(module_path)
        except Exception:
            logging.warning(f'could not load parallel backend module: {module_path}', exc_info=True)


def get_backend_factory(name):
    """Return the registered factory for an exec_type, triggering discovery if needed."""
    name = (name or '').lower()
    if name not in _BACKENDS:
        ensure_loaded()
    return _BACKENDS.get(name)
