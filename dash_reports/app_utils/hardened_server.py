"""Hardened WSGI server: TLS handshake in worker threads, not the acceptor.

Problem
-------
werkzeug's threaded dev server (what Dash's ``run_server`` uses) wraps the
LISTENING socket with SSL, so ``socket.accept()`` performs the TLS handshake
INLINE in the single acceptor thread before dispatching to a worker. Handshakes
are therefore single-file: a burst of fresh connections (a browser opening ~6
parallel connections, an MCP client retrying, a dashboard polling per-card) or a
slow / half-open / non-TLS client blocks *connection acceptance itself*. Measured
in prod: ~13s to first byte, virtually all of it in the TLS handshake, while
request handling was ~15ms. This is a client-agnostic weakness — the server must
not be knock-over-able by chatty or misbehaving clients.

Fix
---
Keep the listening socket plain TCP so the acceptor's ``accept()`` is instant.
Lazily wrap each accepted connection (``do_handshake_on_connect=False`` — no I/O)
and run ``do_handshake()`` in the per-connection WORKER thread, bounded by a
timeout. N handshakes then proceed concurrently across worker threads, and a
stalled or non-TLS client only costs its own thread and times out — it can never
block the accept loop.

Everything else is stock werkzeug (same ``WSGIRequestHandler``, threading model,
HTTP/1.1 keep-alive, streaming, flask-sock websockets), so behavior is unchanged
apart from *where* the handshake runs. Serving ``app.server`` directly is the
standard production Dash deployment pattern.

Only used in direct-TLS mode. Behind a TLS-terminating proxy the app serves plain
HTTP (no ssl_context) and this is inert. Gated by the HARDENED_TLS constant in
dash_reports/app.py (flip to False for the stock werkzeug server).
"""

import logging
import ssl

from werkzeug.serving import ThreadedWSGIServer, load_ssl_context

_DEFAULT_HANDSHAKE_TIMEOUT_SEC = 10.0


class DeferredTLSWSGIServer(ThreadedWSGIServer):
    """ThreadedWSGIServer that defers the TLS handshake to the worker thread."""

    def __init__(self, host, port, app, ssl_context, handler=None,
                 passthrough_errors=False, fd=None,
                 handshake_timeout=_DEFAULT_HANDSHAKE_TIMEOUT_SEC):
        # ssl_context=None here => the LISTENING socket stays plain TCP; the
        # acceptor must never do TLS work.
        super().__init__(host, port, app, handler=handler,
                         passthrough_errors=passthrough_errors,
                         ssl_context=None, fd=fd)
        if isinstance(ssl_context, tuple):
            ssl_context = load_ssl_context(*ssl_context)
        self._tls_context = ssl_context
        self._handshake_timeout = handshake_timeout
        # WSGIRequestHandler.make_environ sets wsgi.url_scheme='https' iff
        # server.ssl_context is not None. We do serve TLS (lazily), so advertise
        # https even though the listening socket itself isn't wrapped.
        self.ssl_context = ssl_context

    def get_request(self):
        # ACCEPTOR thread: plain TCP accept, then wrap WITHOUT handshaking
        # (do_handshake_on_connect=False does no network I/O). Returning the
        # SSLSocket as the tracked request keeps socketserver's shutdown/close
        # path operating on a single object.
        conn, addr = self.socket.accept()
        try:
            tls = self._tls_context.wrap_socket(
                conn, server_side=True,
                do_handshake_on_connect=False,
                suppress_ragged_eofs=True,
            )
        except OSError:
            try:
                conn.close()
            finally:
                raise  # OSError is swallowed by socketserver's accept loop
        return tls, addr

    def finish_request(self, request, client_address):
        # WORKER thread: run the handshake here, bounded by a timeout, so a slow
        # / half-open / non-TLS client can never wedge the accept loop.
        try:
            request.settimeout(self._handshake_timeout)
            request.do_handshake()
            request.settimeout(None)  # back to blocking: keep streaming + websockets working
        except (OSError, ssl.SSLError) as exc:
            logging.debug("dropped TLS handshake from %s: %s", client_address, exc)
            return  # process_request_thread's finally closes the socket
        super().finish_request(request, client_address)


def serve_hardened(app, host, port, ssl_context,
                   handshake_timeout=_DEFAULT_HANDSHAKE_TIMEOUT_SEC):
    """Blocking serve of ``app`` over TLS with worker-thread handshakes."""
    server = DeferredTLSWSGIServer(host, port, app, ssl_context,
                                   handshake_timeout=handshake_timeout)
    logging.info(
        "🔒 Hardened TLS server on %s:%s — handshakes run in worker threads "
        "(timeout=%.0fs); acceptor does plain TCP accept only.",
        host, port, handshake_timeout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
