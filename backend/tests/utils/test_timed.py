from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest

import pulse.utils.timed as timed_module
from pulse.utils.timed import Timed, TimeTracker, timed

TimerFactory = Callable[[], AbstractContextManager[TimeTracker]]

# Оба варианта (генератор с @contextmanager и класс) обязаны вести себя
# одинаково, поэтому контракт проверяется на обоих.
both_variants = pytest.mark.parametrize(
    "make_timer", [timed, Timed], ids=["timed", "Timed"]
)


def fake_clock(monkeypatch: pytest.MonkeyPatch, *ticks: float) -> None:
    """Каждый вызов perf_counter внутри timed.py отдаёт следующее значение."""
    values = iter(ticks)
    monkeypatch.setattr(timed_module, "perf_counter", lambda: next(values))


@both_variants
def test_elapsed_is_measured_in_milliseconds(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    fake_clock(monkeypatch, 1.0, 1.25)

    with make_timer() as tracker:
        pass

    assert tracker.elapsed_ms == 250.0


@both_variants
def test_enter_returns_time_tracker(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    fake_clock(monkeypatch, 0.0, 0.0)

    with make_timer() as tracker:
        assert isinstance(tracker, TimeTracker)


@both_variants
def test_elapsed_is_zero_until_block_exits(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    # Итог известен только после выхода из блока, внутри него поле нулевое.
    fake_clock(monkeypatch, 1.0, 2.0)

    with make_timer() as tracker:
        assert tracker.elapsed_ms == 0.0

    assert tracker.elapsed_ms == 1000.0


@both_variants
def test_elapsed_is_recorded_when_block_raises(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    fake_clock(monkeypatch, 1.0, 1.5)

    with pytest.raises(RuntimeError, match="boom"), make_timer() as tracker:
        raise RuntimeError("boom")

    assert tracker.elapsed_ms == 500.0


@both_variants
def test_exception_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    fake_clock(monkeypatch, 0.0, 0.0)

    with pytest.raises(ValueError, match="bad value"), make_timer():
        raise ValueError("bad value")


@both_variants
def test_each_use_gets_its_own_tracker(
    monkeypatch: pytest.MonkeyPatch, make_timer: TimerFactory
) -> None:
    fake_clock(monkeypatch, 0.0, 1.0, 10.0, 10.5)

    with make_timer() as first:
        pass
    with make_timer() as second:
        pass

    assert first is not second
    assert first.elapsed_ms == 1000.0
    assert second.elapsed_ms == 500.0


@both_variants
def test_real_clock_gives_non_negative_elapsed(make_timer: TimerFactory) -> None:
    with make_timer() as tracker:
        pass

    assert tracker.elapsed_ms >= 0.0
