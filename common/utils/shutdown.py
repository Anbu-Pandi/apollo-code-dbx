from __future__ import annotations

import logging
import os
import signal
from typing import Callable


class ShutdownRegistry:
    def __init__(self) -> None:
        self._fns: dict[str, Callable[[], None]] = {}
        self._done = False

    def register(self, name: str, fn: Callable[[], None]) -> None:
        self._fns[name] = fn

    def shutdown(self) -> None:
        if self._done:
            return
        self._done = True
        for name, fn in list(self._fns.items()):
            logging.info("shutdown: running %s", name)
            try:
                fn()
            except Exception as exc:
                logging.exception("shutdown step %s failed: %s", name, exc)

    def reset(self) -> None:
        self._fns.clear()
        self._done = False


_GLOBAL = ShutdownRegistry()


def register(name: str, fn: Callable[[], None]) -> None:
    _GLOBAL.register(name, fn)


def shutdown() -> None:
    _GLOBAL.shutdown()


def install_signal_handlers(on_shutdown: Callable[[], None]) -> None:
    fired = {"once": False}

    def _handler(signum, _frame):
        if fired["once"]:
            return
        fired["once"] = True
        logging.info("shutdown: received signal %s", signum)
        on_shutdown()
        signal.signal(signum, signal.SIG_DFL)
        if hasattr(signal, "raise_signal"):
            signal.raise_signal(signum)
        else:
            os.kill(os.getpid(), signum)

    signals: list[int] = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for s in signals:
        signal.signal(s, _handler)
