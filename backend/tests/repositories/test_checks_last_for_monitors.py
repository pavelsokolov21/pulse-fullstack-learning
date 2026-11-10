"""`CheckRepository.last_for_monitors`: последняя проверка каждого монитора."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.checks import Check
from pulse.domain.monitors import Monitor
from pulse.domain.users import User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session

from .conftest import make_monitor

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo(session: Session) -> CheckRepository:
    return CheckRepository(session)


async def add(
    repo: CheckRepository, monitor: Monitor, seconds: int, latency: float = 1.0
) -> Check:
    return await repo.add(
        monitor_id=monitor.id,
        url="https://a.test",
        status_code=200,
        latency_ms=latency,
        error=None,
        checked_at=NOW + timedelta(seconds=seconds),
    )


@pytest.fixture
def selects(engine: AsyncEngine) -> Iterator[list[str]]:
    """Тексты SELECT, ушедшие в базу: считать надо на свежем состоянии, не по кешу."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *_: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_returns_the_newest_check_of_each_monitor(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    await add(repo, monitor, 10)
    newest = await add(repo, monitor, 30)
    await add(repo, monitor, 20)
    other_newest = await add(repo, other_monitor, 5)

    last = await repo.last_for_monitors([monitor.id, other_monitor.id])

    assert last == {monitor.id: newest, other_monitor.id: other_newest}


async def test_monitor_without_checks_is_absent(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    check = await add(repo, monitor, 1)

    last = await repo.last_for_monitors([monitor.id, other_monitor.id])

    assert last == {monitor.id: check}


async def test_unknown_ids_are_ignored(repo: CheckRepository, monitor: Monitor) -> None:
    check = await add(repo, monitor, 1)

    assert await repo.last_for_monitors([monitor.id, 2_000_000_000]) == {
        monitor.id: check
    }


async def test_other_monitors_are_not_returned(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    await add(repo, other_monitor, 1)

    assert await repo.last_for_monitors([monitor.id]) == {}


async def test_empty_input_returns_nothing_and_skips_the_database(
    repo: CheckRepository, selects: list[str]
) -> None:
    assert await repo.last_for_monitors([]) == {}
    assert selects == []


async def test_equal_timestamps_are_resolved_by_the_larger_id(
    repo: CheckRepository, monitor: Monitor
) -> None:
    await add(repo, monitor, 7, latency=1.0)
    later_id = await add(repo, monitor, 7, latency=2.0)

    last = await repo.last_for_monitors([monitor.id])

    assert last[monitor.id] == later_id


async def test_agrees_with_list_recent_for_every_monitor(
    session: Session, repo: CheckRepository, owner: User
) -> None:
    monitors = [
        await make_monitor(session, owner, f"https://m{i}.test") for i in range(5)
    ]
    for i, m in enumerate(monitors):
        for second in (3, 9, 9, 1)[: i % 4 + 1]:
            await add(repo, m, second)

    last = await repo.last_for_monitors([m.id for m in monitors])

    for m in monitors:
        expected = await repo.list_recent(m.id, 1)
        assert last.get(m.id) == (expected[0] if expected else None)


@pytest.mark.parametrize("count", [1, 5, 25])
async def test_one_select_whatever_the_number_of_monitors(
    session: Session,
    repo: CheckRepository,
    owner: User,
    selects: list[str],
    count: int,
) -> None:
    monitors = [
        await make_monitor(session, owner, f"https://m{i}.test") for i in range(count)
    ]
    for m in monitors:
        await add(repo, m, 1)
    selects.clear()

    last = await repo.last_for_monitors([m.id for m in monitors])

    assert len(last) == count
    assert len(selects) == 1
