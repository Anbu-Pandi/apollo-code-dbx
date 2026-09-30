import contextlib
import logging
import threading
import time


class InteractiveActivity:

    def __init__(self):
        self._lock = threading.Lock()
        self._inflight = 0
        self._last_end = 0.0
        self._generation = 0

    def begin(self):
        with self._lock:
            self._inflight += 1

    def end(self):
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1
            self._last_end = time.time()

    @contextlib.contextmanager
    def track(self):
        self.begin()
        try:
            yield
        finally:
            self.end()

    def inflight(self):
        with self._lock:
            return self._inflight

    def busy(self, quiet_s=0.0):
        with self._lock:
            if self._inflight > 0:
                return True
            return bool(quiet_s) and (time.time() - self._last_end) < quiet_s

    def await_idle(self, max_wait_s, quiet_s=0.0, poll_s=0.05):
        deadline = time.time() + max_wait_s
        while self.busy(quiet_s=quiet_s) and time.time() < deadline:
            time.sleep(poll_s)
        return not self.busy(quiet_s=quiet_s)

    def note_generation(self, generation):
        with self._lock:
            if generation and generation > self._generation:
                self._generation = generation
            return self._generation

    def current_generation(self):
        with self._lock:
            return self._generation

    def is_generation_current(self, generation):
        with self._lock:
            return not generation or generation >= self._generation


interactive_activity = InteractiveActivity()


class _StageTimer:

    def __init__(self, stage, fields):
        self._stage = stage
        self._fields = fields
        self._t0 = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        extra = ' '.join(f'{k}={v}' for k, v in self._fields.items() if v not in (None, ''))
        logging.info('cdx_stage %s ms=%.1f %s', self._stage, self.elapsed_ms, extra)
        return False


def stage_timer(stage, **fields):
    return _StageTimer(stage, fields)
