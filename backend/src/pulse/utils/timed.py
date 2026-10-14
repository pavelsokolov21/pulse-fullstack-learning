from contextlib import contextmanager
from time import perf_counter
from types import TracebackType


class TimeTracker:
    def __init__(self):
        self.elapsed_ms: float = 0.0


@contextmanager
def timed():
    tracker = TimeTracker()
    start = perf_counter()

    try:
        yield tracker
    finally:
        end = perf_counter()
        tracker.elapsed_ms = (end - start) * 1000


class Timed:
    def __init__(self):
        self._start = 0.0
        self.tracker: TimeTracker | None = None

    def __enter__(self):
        self.tracker = TimeTracker()
        self._start = perf_counter()

        return self.tracker

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        end = perf_counter()
        self.tracker.elapsed_ms = (end - self._start) * 1000
