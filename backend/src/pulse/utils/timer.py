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

    def __enter__(self) -> TimeTracker:
        self.tracker = TimeTracker()
        self._start = perf_counter()

        return self.tracker

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        end = perf_counter()

        # Через `with` __exit__ всегда идёт после __enter__. Явная проверка
        # сужает тип до TimeTracker и громко падает при ручном вызове.
        if self.tracker is None:
            raise RuntimeError("Timed.__exit__ вызван без __enter__")

        self.tracker.elapsed_ms = (end - self._start) * 1000
