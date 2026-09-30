from __future__ import annotations

import logging
import os
import sys


def _rss_mb() -> str:
    try:
        import psutil
    except ImportError:
        return "-"
    rss = psutil.Process(os.getpid()).memory_info().rss
    return f"{rss / (1024 * 1024):.0f}MB"


def _open_handles() -> str:
    if sys.platform != "win32":
        return "-"
    try:
        import psutil
    except ImportError:
        return "-"
    proc = psutil.Process(os.getpid())
    fn = getattr(proc, "num_handles", None)
    return str(fn()) if fn else "-"


def log_cycle_done(*, new_files: int, ingested: int, synth: int,
                   rendered: int, normalized: int) -> None:
    logging.info(
        "cycle done: new_files=%d ingested=%d synth=%d rendered=%d normalized=%d "
        "rss=%s open_handles=%s",
        new_files, ingested, synth, rendered, normalized, _rss_mb(), _open_handles(),
    )
