import pytest

from pulse.checks.models import CheckResult, HostMeta
from pulse.utils.stats import (
    failure_rate_by_host,
    get_host_meta,
    group_by_host,
    p95_by_host,
    slowest,
    summarize,
)


def result(url: str, status_code: int | None, latency_ms: float = 10.0) -> CheckResult:
    # status_code=None моделирует сетевой сбой: ответа не было, есть error.
    error = "Network Error: ConnectError" if status_code is None else None
    return CheckResult(
        url=url, status_code=status_code, latency_ms=latency_ms, error=error
    )


@pytest.fixture
def sample() -> list[CheckResult]:
    return [
        result("http://a.test/", 200, 100.0),
        result("http://a.test/", 200, 120.0),
        result("http://a.test/", None, 5000.0),
        result("http://b.test/", 200, 80.0),
        result("http://b.test/", 503, 90.0),
        result("http://c.test/", 200, 60.0),
    ]


# --- slowest ---------------------------------------------------------------


def test_slowest_returns_n_slowest_in_descending_order(
    sample: list[CheckResult],
) -> None:
    assert [r.latency_ms for r in slowest(sample, 3)] == [120.0, 100.0, 90.0]


def test_slowest_ignores_checks_without_response(sample: list[CheckResult]) -> None:
    # Сетевой сбой (5000 мс) это время до таймаута, а не скорость сайта: иначе
    # топ почти всегда состоял бы из таймаутов. Решение то же, что у p95.
    slowest_all = slowest(sample, 100)

    assert all(r.status_code is not None for r in slowest_all)
    assert 5000.0 not in [r.latency_ms for r in slowest_all]


def test_slowest_keeps_error_responses(sample: list[CheckResult]) -> None:
    # 503 это настоящий ответ сайта, его задержка учитывается.
    assert 90.0 in [r.latency_ms for r in slowest(sample, 100)]


def test_slowest_default_n_is_three(sample: list[CheckResult]) -> None:
    assert len(slowest(sample)) == 3


def test_slowest_n_larger_than_input_returns_all_responses_sorted(
    sample: list[CheckResult],
) -> None:
    latencies = [r.latency_ms for r in slowest(sample, 100)]

    assert latencies == sorted(latencies, reverse=True)
    assert len(latencies) == 5  # шесть проверок минус один сетевой сбой


def test_slowest_zero_returns_empty_list(sample: list[CheckResult]) -> None:
    assert slowest(sample, 0) == []


def test_slowest_of_empty_input_is_empty() -> None:
    assert slowest([]) == []


def test_slowest_keeps_input_order_for_equal_latency() -> None:
    first = result("http://a.test/", 200, 50.0)
    second = result("http://b.test/", 200, 50.0)

    assert slowest([first, second], 2) == [first, second]


def test_slowest_does_not_mutate_input(sample: list[CheckResult]) -> None:
    before = list(sample)

    slowest(sample, 3)

    assert sample == before


def test_slowest_accepts_tuple(sample: list[CheckResult]) -> None:
    assert slowest(tuple(sample), 1) == slowest(sample, 1)


def test_slowest_rejects_negative_n(sample: list[CheckResult]) -> None:
    with pytest.raises(ValueError, match="'n'"):
        slowest(sample, -1)


def test_slowest_rejects_non_integer_n(sample: list[CheckResult]) -> None:
    with pytest.raises(TypeError, match="'n'"):
        slowest(sample, 2.5)  # pyright: ignore[reportArgumentType]


# --- group_by_host ---------------------------------------------------------


def test_group_by_host_groups_results_and_keeps_order(
    sample: list[CheckResult],
) -> None:
    groups = group_by_host(sample)

    assert list(groups) == ["a.test", "b.test", "c.test"]
    assert [r.latency_ms for r in groups["a.test"]] == [100.0, 120.0, 5000.0]
    assert len(groups["b.test"]) == 2
    assert len(groups["c.test"]) == 1


def test_group_by_host_is_case_insensitive() -> None:
    groups = group_by_host(
        [result("http://A.TEST/x", 200), result("http://a.test/y", 200)]
    )

    assert list(groups) == ["a.test"]
    assert len(groups["a.test"]) == 2


def test_group_by_host_ignores_port() -> None:
    # Группировка по hostname: порт в ключ не входит.
    groups = group_by_host(
        [result("http://a.test:8080/", 200), result("http://a.test/", 200)]
    )

    assert list(groups) == ["a.test"]


@pytest.mark.parametrize("url", ["", "not a url", "/just/a/path"])
def test_group_by_host_rejects_url_without_host(url: str) -> None:
    # URL монитора нормализуется при создании, хост есть всегда. Результат без
    # хоста это повреждённые данные, и их не прячут в фальшивую группу.
    with pytest.raises(ValueError, match="хоста"):
        group_by_host([result(url, 200)])


def test_group_by_host_of_empty_input_is_empty() -> None:
    assert group_by_host([]) == {}


# --- get_host_meta ---------------------------------------------------------


def test_get_host_meta_counts_total_and_failures() -> None:
    # 2xx и 3xx здоровые; 4xx, 5xx и отсутствие ответа считаются сбоем.
    results = [
        result("http://a.test/", 200),
        result("http://a.test/", 301),
        result("http://a.test/", 404),
        result("http://a.test/", 503),
        result("http://a.test/", None),
    ]

    assert get_host_meta(results) == HostMeta(total=5, failed=3)


def test_get_host_meta_of_empty_input() -> None:
    assert get_host_meta([]) == HostMeta(total=0, failed=0)


# --- failure_rate_by_host --------------------------------------------------


def test_failure_rate_is_failures_divided_by_total(
    sample: list[CheckResult],
) -> None:
    rates = failure_rate_by_host(sample)

    assert rates == pytest.approx({"a.test": 1 / 3, "b.test": 0.5, "c.test": 0.0})


def test_failure_rate_is_between_zero_and_one(sample: list[CheckResult]) -> None:
    assert all(0.0 <= rate <= 1.0 for rate in failure_rate_by_host(sample).values())


def test_failure_rate_values_are_floats(sample: list[CheckResult]) -> None:
    # Контракт сигнатуры dict[str, float]: Decimal сюда не подходит.
    assert all(
        isinstance(rate, float) for rate in failure_rate_by_host(sample).values()
    )


def test_failure_rate_is_one_when_every_check_failed() -> None:
    results = [result("http://a.test/", 500), result("http://a.test/", None)]

    assert failure_rate_by_host(results) == {"a.test": 1.0}


def test_failure_rate_is_zero_when_nothing_failed() -> None:
    results = [result("http://a.test/", 200), result("http://a.test/", 204)]

    assert failure_rate_by_host(results) == {"a.test": 0.0}


def test_failure_rate_of_empty_input_is_empty() -> None:
    assert failure_rate_by_host([]) == {}


# --- p95_by_host -----------------------------------------------------------


def test_p95_is_calculated_per_host(sample: list[CheckResult]) -> None:
    # nearest-rank. Сетевой сбой a.test (5000 мс, ответа не было) в выборку не
    # входит: остаются 100 и 120, ранг 2 даёт 120.
    assert p95_by_host(sample) == {"a.test": 120.0, "b.test": 90.0, "c.test": 60.0}


def test_p95_ignores_network_failures_but_keeps_error_responses() -> None:
    # 503 это ответ сайта, его задержка настоящая; None это сбой без ответа.
    results = [
        result("http://a.test/", 200, 10.0),
        result("http://a.test/", 503, 40.0),
        result("http://a.test/", None, 9000.0),
    ]

    assert p95_by_host(results) == {"a.test": 40.0}


def test_p95_is_none_for_host_without_any_response() -> None:
    # 0.0 прочитался бы как «самый быстрый сайт», хотя данных нет вовсе.
    results = [
        result("http://a.test/", None, 3000.0),
        result("http://b.test/", 200, 7.0),
    ]

    assert p95_by_host(results) == {"a.test": None, "b.test": 7.0}


def test_p95_does_not_depend_on_input_order() -> None:
    ordered = [result("http://a.test/", 200, ms) for ms in (10.0, 20.0, 30.0)]
    shuffled = [ordered[2], ordered[0], ordered[1]]

    assert p95_by_host(shuffled) == p95_by_host(ordered) == {"a.test": 30.0}


def test_p95_of_empty_input_is_empty() -> None:
    assert p95_by_host([]) == {}


# --- summarize -------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "total", "failed", "failure_rate", "p95"),
    [
        ("a.test", 3, 1, 1 / 3, 120.0),
        ("b.test", 2, 1, 0.5, 90.0),
        ("c.test", 1, 0, 0.0, 60.0),
    ],
)
def test_summarize_builds_host_stats(
    sample: list[CheckResult],
    host: str,
    total: int,
    failed: int,
    failure_rate: float,
    p95: float | None,
) -> None:
    stats = {s.host: s for s in summarize(sample)}[host]

    assert stats.total == total
    assert stats.failed == failed
    assert stats.failure_rate == pytest.approx(failure_rate)
    assert stats.p95 == p95


def test_summarize_covers_every_host_once(sample: list[CheckResult]) -> None:
    assert sorted(s.host for s in summarize(sample)) == ["a.test", "b.test", "c.test"]


def test_summarize_agrees_with_failure_rate_by_host(
    sample: list[CheckResult],
) -> None:
    rates = failure_rate_by_host(sample)

    assert {s.host: s.failure_rate for s in summarize(sample)} == pytest.approx(rates)


def test_summarize_agrees_with_p95_by_host(sample: list[CheckResult]) -> None:
    assert {s.host: s.p95 for s in summarize(sample)} == p95_by_host(sample)


def test_summarize_puts_hosts_with_most_failures_first(
    sample: list[CheckResult],
) -> None:
    # Больше сбоев раньше; при равенстве (a.test и b.test по одному) порядок
    # первого появления, потому что сортировка устойчива даже с reverse=True.
    assert [s.host for s in summarize(sample)] == ["a.test", "b.test", "c.test"]


def test_summarize_orders_by_failure_count_not_by_rate() -> None:
    # big: 2 сбоя из 10 (доля 0.2); small: 1 сбой из 1 (доля 1.0). Сортировка по
    # количеству («где больше всего проблем») ставит big первым.
    results = [result("http://big.test/", 500)] * 2 + [
        result("http://big.test/", 200)
    ] * 8
    results.append(result("http://small.test/", 500))

    assert [s.host for s in summarize(results)] == ["big.test", "small.test"]


def test_summarize_host_without_responses_has_no_p95() -> None:
    stats = summarize([result("http://a.test/", None, 3000.0)])

    assert [(s.host, s.failed, s.failure_rate, s.p95) for s in stats] == [
        ("a.test", 1, 1.0, None)
    ]


def test_summarize_of_empty_input_is_empty() -> None:
    assert summarize([]) == []


# --- любая последовательность на входе -------------------------------------


def test_functions_accept_tuple(sample: list[CheckResult]) -> None:
    as_tuple = tuple(sample)

    assert group_by_host(as_tuple) == group_by_host(sample)
    assert get_host_meta(as_tuple) == get_host_meta(sample)
    assert failure_rate_by_host(as_tuple) == failure_rate_by_host(sample)
    assert p95_by_host(as_tuple) == p95_by_host(sample)
    assert summarize(as_tuple) == summarize(sample)
