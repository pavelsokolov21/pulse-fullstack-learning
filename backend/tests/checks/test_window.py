import pytest

from pulse.checks.models import CheckResult
from pulse.checks.window import RollingWindow


def result(status_code: int | None, latency_ms: float = 10.0) -> CheckResult:
    # status_code=None моделирует сетевой сбой: ответа не было, есть error.
    error = "Network Error: ConnectError" if status_code is None else None
    return CheckResult(
        url="http://a.test",
        status_code=status_code,
        latency_ms=latency_ms,
        error=error,
    )


def window_with(size: int, *results: CheckResult) -> RollingWindow:
    window = RollingWindow(size)
    for res in results:
        window.add(res)
    return window


# --- пустое окно -----------------------------------------------------------


def test_empty_window_has_no_data() -> None:
    window = RollingWindow(3)

    assert len(window) == 0
    # None, а не 0.0: «0 сбоев» и «0 мс» прочитались бы как «всё отлично».
    assert window.failure_rate() is None
    assert window.p95() is None


# --- размер и вытеснение ---------------------------------------------------


def test_len_grows_until_size_and_then_stays() -> None:
    window = RollingWindow(3)
    lengths: list[int] = []

    for _ in range(5):
        window.add(result(200))
        lengths.append(len(window))

    assert lengths == [1, 2, 3, 3, 3]


def test_size_one_keeps_only_the_last_result() -> None:
    window = window_with(1, result(500), result(200))

    assert len(window) == 1
    assert window.failure_rate() == 0.0


def test_overflow_evicts_the_oldest_results_from_failure_rate() -> None:
    window = window_with(3, result(500), result(500), result(200))
    assert window.failure_rate() == pytest.approx(2 / 3)

    window.add(result(200))
    window.add(result(200))

    # Обе старые ошибки вытеснены, в окне три успеха.
    assert len(window) == 3
    assert window.failure_rate() == 0.0


def test_overflow_evicts_old_outlier_from_p95() -> None:
    window = window_with(
        3,
        result(200, 5000.0),
        result(200, 10.0),
        result(200, 20.0),
    )
    assert window.p95() == 5000.0

    window.add(result(200, 30.0))

    # 5000 вытеснен, p95 считается по 10, 20, 30 (nearest-rank, ранг 3).
    assert window.p95() == 30.0


# --- failure_rate ----------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([200, 204, 301], 0.0),
        ([500, 503], 1.0),
        ([200, 500, None, 200], 0.5),
        ([None, None, 200], 2 / 3),
    ],
)
def test_failure_rate_counts_network_errors_and_bad_responses(
    statuses: list[int | None], expected: float
) -> None:
    window = window_with(10, *(result(status) for status in statuses))

    assert window.failure_rate() == pytest.approx(expected)


# --- p95 -------------------------------------------------------------------


def test_p95_is_calculated_over_the_window() -> None:
    window = window_with(10, *(result(200, float(ms)) for ms in (10, 20, 30, 40)))

    assert window.p95() == 40.0


def test_p95_ignores_network_failures() -> None:
    # Задержка сетевого сбоя это таймаут, а не скорость сайта.
    window = window_with(10, result(200, 10.0), result(None, 9000.0))

    assert window.p95() == 10.0


def test_p95_keeps_error_responses() -> None:
    # 503 это настоящий ответ сайта, его задержка учитывается.
    window = window_with(10, result(200, 10.0), result(503, 40.0))

    assert window.p95() == 40.0


def test_window_of_only_network_failures_has_failure_rate_but_no_p95() -> None:
    window = window_with(5, result(None, 3000.0), result(None, 3000.0))

    assert window.failure_rate() == 1.0
    assert window.p95() is None


# --- независимость экземпляров ---------------------------------------------


def test_windows_do_not_share_state() -> None:
    first = window_with(3, result(500))
    second = RollingWindow(3)

    assert len(first) == 1
    assert len(second) == 0
    assert second.failure_rate() is None


# --- валидация размера -----------------------------------------------------


@pytest.mark.parametrize("size", [0, -1])
def test_non_positive_size_raises(size: int) -> None:
    # deque(maxlen=0) молча создала бы окно, которое всегда пусто.
    with pytest.raises(ValueError, match="size"):
        RollingWindow(size)


def test_non_integer_size_raises() -> None:
    with pytest.raises(TypeError, match="size"):
        RollingWindow(2.5)  # pyright: ignore[reportArgumentType]


def test_bool_size_raises() -> None:
    # bool это подкласс int: True молча стал бы окном размера 1.
    with pytest.raises(TypeError, match="size"):
        RollingWindow(True)
