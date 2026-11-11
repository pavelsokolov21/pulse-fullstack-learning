"""`CheckRepository.stats`: итоги проверок монитора за окно."""

from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.monitors import Monitor
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(session: Session) -> CheckRepository:
    return CheckRepository(session)


async def add(
    repo: CheckRepository,
    monitor: Monitor,
    *,
    ago: timedelta = timedelta(minutes=1),
    status: int | None = 200,
    latency: float = 10.0,
) -> None:
    await repo.add(
        monitor_id=monitor.id,
        url="https://a.test",
        status_code=status,
        latency_ms=latency,
        error=None if status is not None else "Network Error: ConnectError",
        checked_at=NOW - ago,
    )


async def test_no_checks_gives_zero_and_none(
    repo: CheckRepository, monitor: Monitor
) -> None:
    stats = await repo.stats(monitor.id, NOW - timedelta(days=7))

    assert (stats.total, stats.ok) == (0, 0)
    assert stats.avg_latency_ms is None
    assert stats.p95_latency_ms is None


async def test_counts_totals_and_successes(
    repo: CheckRepository, monitor: Monitor
) -> None:
    await add(repo, monitor, status=200)
    await add(repo, monitor, status=301)
    await add(repo, monitor, status=503)
    await add(repo, monitor, status=None, latency=5000.0)

    stats = await repo.stats(monitor.id, NOW - timedelta(days=1))

    assert (stats.total, stats.ok) == (4, 2)


async def test_latency_statistics_use_only_checks_that_got_a_response(
    repo: CheckRepository, monitor: Monitor
) -> None:
    for latency in (10.0, 20.0, 30.0, 40.0, 100.0):
        await add(repo, monitor, latency=latency)
    await add(repo, monitor, status=503, latency=60.0)  # ответ есть, хотя и плохой
    await add(repo, monitor, status=None, latency=5000.0)  # таймаут: не считается

    stats = await repo.stats(monitor.id, NOW - timedelta(days=1))

    # Задержки с ответом: 10, 20, 30, 40, 60, 100. Среднее 260/6; p95 лежит между 60 и
    # 100: позиция 0.95 * 5 = 4.75, то есть 60 + 0.75 * 40 = 90.
    assert stats.avg_latency_ms == pytest.approx(260 / 6)
    assert stats.p95_latency_ms == pytest.approx(90.0)


async def test_only_network_errors_leave_latencies_empty(
    repo: CheckRepository, monitor: Monitor
) -> None:
    await add(repo, monitor, status=None, latency=5000.0)

    stats = await repo.stats(monitor.id, NOW - timedelta(days=1))

    assert (stats.total, stats.ok) == (1, 0)
    assert stats.avg_latency_ms is None
    assert stats.p95_latency_ms is None


async def test_checks_older_than_the_window_are_excluded(
    repo: CheckRepository, monitor: Monitor
) -> None:
    await add(repo, monitor, ago=timedelta(hours=1))
    await add(repo, monitor, ago=timedelta(days=3))

    day = await repo.stats(monitor.id, NOW - timedelta(hours=24))
    week = await repo.stats(monitor.id, NOW - timedelta(days=7))

    assert (day.total, week.total) == (1, 2)


async def test_window_start_is_inclusive(
    repo: CheckRepository, monitor: Monitor
) -> None:
    await add(repo, monitor, ago=timedelta(hours=24))

    stats = await repo.stats(monitor.id, NOW - timedelta(hours=24))

    assert stats.total == 1


async def test_other_monitors_are_not_counted(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    await add(repo, other_monitor)

    stats = await repo.stats(monitor.id, NOW - timedelta(days=1))

    assert stats.total == 0
