"""Enumerate the system SSL certificate store once and reuse the context.

Why this exists
---------------
`pip_system_certs` (in requirements.txt: "needed for python request.get ssl
error in py39 onwards") re-wraps ``requests.adapters.HTTPAdapter.init_poolmanager``
so that EVERY new ``HTTPAdapter`` does::

    ssl_context = ssl.create_default_context()   # walks the cert store
    ssl_context.load_default_certs()              # walks it AGAIN

On Windows ``load_default_certs()`` -> ``_load_windows_store_certs()`` is a C
call that enumerates the entire Windows certificate store **while holding the
GIL**. It is slow on a domain-joined box.

The trap: ``requests.request()`` / ``requests.get()`` build a *fresh* ``Session``
(two adapters) on every call, and flask-dance builds a *fresh* ``OAuth2Session``
on every login. So this enumeration ran on every proxied request and every
login, and under concurrency all worker threads serialized on the GIL behind
whichever thread was mid-enumeration -> multi-second per-request stalls, even on
loopback ``/health``. (Root-caused with py-spy: every active request thread was
parked in ``_load_windows_store_certs`` / ``init_poolmanager``; one held the GIL.)

The fix
-------
Enumerate the store ONCE, cache the resulting ``SSLContext``, and hand that
cached context to every adapter. This preserves ``pip_system_certs`` behavior
(system/corporate CAs are used; the ``cert_verify`` wrapper that sets
``conn.ca_certs = None`` is left intact) but pays the enumeration cost a single
time for the life of the process instead of once per request.

Install once at process startup, after ``requests`` is importable::

    from common.utils.ssl_cert_cache import install_cached_system_ssl_context
    install_cached_system_ssl_context()
"""

import logging
import threading

_lock = threading.Lock()
_cached_context = None
_installed = False


def _get_cached_context():
    global _cached_context
    if _cached_context is None:
        with _lock:
            if _cached_context is None:
                import ssl
                ctx = ssl.create_default_context()
                ctx.load_default_certs()
                _cached_context = ctx
    return _cached_context


def install_cached_system_ssl_context():
    """Replace pip_system_certs' per-adapter cert load with a cached context.

    No-op (and safe) if requests isn't importable or pip_system_certs hasn't
    wrapped ``init_poolmanager`` — in that case default requests/certifi
    behavior is left untouched. Never raises; a failure here must not stop the
    app from starting.
    """
    global _installed
    if _installed:
        return
    try:
        import requests.adapters as _ra  # importing triggers pip_system_certs' patch
    except Exception as e:
        logging.info("ssl_cert_cache: requests not available (%s); skipping", e)
        return

    try:
        adapter = _ra.HTTPAdapter
        wrapped = adapter.init_poolmanager
        # pip_system_certs wraps init_poolmanager via wrapt; the real requests
        # function is reachable through __wrapped__. If it isn't wrapped, the
        # expensive per-adapter cert load isn't happening -> leave as-is.
        original = getattr(wrapped, "__wrapped__", None)
        if original is None:
            logging.info(
                "ssl_cert_cache: init_poolmanager not wrapped (pip_system_certs "
                "inactive); nothing to optimize")
            return
        while hasattr(original, "__wrapped__"):  # unwrap any nested layers
            original = original.__wrapped__

        def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
            pool_kwargs.setdefault("ssl_context", _get_cached_context())
            return original(self, connections, maxsize, block=block, **pool_kwargs)

        adapter.init_poolmanager = init_poolmanager
        _installed = True
        _get_cached_context()  # prime now so the first real request pays nothing
        logging.info(
            "ssl_cert_cache: installed cached system SSL context "
            "(Windows cert store enumerated once, not per request)")
    except Exception as e:
        logging.warning(
            "ssl_cert_cache: install failed (%s); leaving pip_system_certs "
            "behavior unchanged", e)
