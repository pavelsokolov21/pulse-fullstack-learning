"""`CheckRepository.get` и `uptime_by_monitor`: чтение для фоновых задач."""

from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.monitors import Monitor
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
MISSING_ID = 2_000_000_000


@pytest.fixture
def repo(session: Session) -> CheckRepository:
    return CheckRepository(session)


async def add(
    repo: CheckRepository,
    monitor: Monitor,
    *,
    status: int | None = 200,
    at: datetime = NOW,
):
    return await repo.add(
        monitor_id=monitor.id,
        url=monitor.url,
        status_code=status,
        latency_ms=10.0,
        error=None if status is not None else "Network Error: ConnectError",
        checked_at=at,
    )


async def test_get_returns_the_stored_check(
    repo: CheckRepository, monitor: Monitor
) -> None:
    stored = await add(repo, monitor, status=503)

    assert await repo.get(stored.id) == stored


async def test_get_of_a_missing_check_is_none(repo: CheckRepository) -> None:
    assert await repo.get(MISSING_ID) is None


async def test_uptime_is_grouped_per_monitor(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    await add(repo, monitor, status=200)
    await add(repo, monitor, status=503)
    await add(repo, monitor, status=None)
    await add(repo, other_monitor, status=200)

    result = await repo.uptime_by_monitor(NOW - timedelta(hours=1))

    by_id = {u.monitor_id: (u.total, u.ok) for u in result}
    assert by_id[monitor.id] == (3, 1)
    assert by_id[other_monitor.id] == (1, 1)


async def test_checks_before_the_period_are_not_counted(
    repo: CheckRepository, monitor: Monitor
) -> None:
    since = NOW - timedelta(hours=24)
    await add(repo, monitor, at=since - timedelta(seconds=1))
    await add(repo, monitor, at=since)  # граница входит в период

    result = await repo.uptime_by_monitor(since)

    assert [(u.monitor_id, u.total) for u in result] == [(monitor.id, 1)]


async def test_monitor_without_checks_is_absent(
    repo: CheckRepository, monitor: Monitor
) -> None:
    result = await repo.uptime_by_monitor(NOW - timedelta(days=1))

    assert monitor.id not in {u.monitor_id for u in result}
