from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from pulse.domain.checks import Check
from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.repositories.checks import DEFAULT_MAX_CHECKS_PER_MONITOR, CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo(session: Session) -> CheckRepository:
    return CheckRepository(session)


async def add(
    repo: CheckRepository,
    monitor: Monitor,
    n: int = 0,
    status_code: int | None = 200,
) -> Check:
    return await repo.add(
        monitor_id=monitor.id,
        url="https://a.test",
        status_code=status_code,
        latency_ms=float(n),
        error=None if status_code is not None else "Network Error: ConnectError",
        checked_at=NOW + timedelta(seconds=n),
    )


async def test_add_assigns_increasing_ids_across_monitors(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    ids = [
        (await add(repo, monitor)).id,
        (await add(repo, other_monitor)).id,
        (await add(repo, monitor)).id,
    ]

    assert ids == sorted(set(ids))


async def test_add_keeps_all_given_fields(
    repo: CheckRepository, monitor: Monitor
) -> None:
    check = await repo.add(
        monitor_id=monitor.id,
        url="https://x.test",
        status_code=None,
        latency_ms=12.5,
        error="Network Error: ConnectError",
        checked_at=NOW,
    )

    assert check == Check(
        check.id,
        monitor.id,
        "https://x.test",
        None,
        12.5,
        "Network Error: ConnectError",
        NOW,
        False,
    )
    assert (await repo.list_recent(monitor.id, 1)) == [check]


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (None, False),
        (100, False),
        (199, False),
        (200, True),
        (301, True),
        (399, True),
        (400, False),
        (404, False),
        (500, False),
    ],
)
async def test_ok_is_computed_by_the_database_from_status_code(
    repo: CheckRepository, monitor: Monitor, status_code: int | None, expected: bool
) -> None:
    # Репозиторий не принимает `ok`: правило «200..399» хранится только в схеме.
    added = await add(repo, monitor, status_code=status_code)

    assert added.ok is expected
    assert (await repo.list_recent(monitor.id, 1))[0].ok is expected


async def test_list_recent_returns_newest_first(
    repo: CheckRepository, monitor: Monitor
) -> None:
    checks = [await add(repo, monitor, n=n) for n in range(3)]

    assert await repo.list_recent(monitor.id, 10) == list(reversed(checks))


async def test_list_recent_orders_by_check_time_not_by_insertion(
    repo: CheckRepository, monitor: Monitor
) -> None:
    newest = await add(repo, monitor, n=10)
    older = await add(repo, monitor, n=1)

    assert await repo.list_recent(monitor.id, 10) == [newest, older]


async def test_list_recent_breaks_time_ties_by_id(
    repo: CheckRepository, monitor: Monitor
) -> None:
    first = await add(repo, monitor, n=5)
    second = await add(repo, monitor, n=5)

    assert await repo.list_recent(monitor.id, 10) == [second, first]


async def test_list_recent_applies_limit_to_newest(
    repo: CheckRepository, monitor: Monitor
) -> None:
    checks = [await add(repo, monitor, n=n) for n in range(5)]

    assert await repo.list_recent(monitor.id, 2) == [checks[4], checks[3]]


async def test_list_recent_for_unknown_monitor_is_empty(repo: CheckRepository) -> None:
    assert await repo.list_recent(99, 10) == []


async def test_monitors_have_separate_histories(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    first = await add(repo, monitor)
    second = await add(repo, other_monitor)

    assert await repo.list_recent(monitor.id, 10) == [first]
    assert await repo.list_recent(other_monitor.id, 10) == [second]


async def test_history_is_capped_and_oldest_are_dropped(
    repo: CheckRepository, monitor: Monitor
) -> None:
    checks = [
        await add(repo, monitor, n=n) for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR + 5)
    ]

    stored = await repo.list_recent(monitor.id, DEFAULT_MAX_CHECKS_PER_MONITOR + 50)

    assert len(stored) == DEFAULT_MAX_CHECKS_PER_MONITOR
    assert stored[0] == checks[-1]
    assert stored[-1] == checks[5]


async def test_cap_is_per_monitor(
    repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR):
        await add(repo, monitor, n=n)
    other = await add(repo, other_monitor)

    assert len(await repo.list_recent(monitor.id, 1000)) == (
        DEFAULT_MAX_CHECKS_PER_MONITOR
    )
    assert await repo.list_recent(other_monitor.id, 10) == [other]


# --- связь с монитором -----------------------------------------------------


async def test_deleting_a_monitor_removes_its_checks_only(
    session: Session, repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    await add(repo, monitor)
    keep = await add(repo, other_monitor)

    await MonitorRepository(session).delete(monitor.id)  # ON DELETE CASCADE

    assert await repo.list_recent(monitor.id, 10) == []
    assert await repo.list_recent(other_monitor.id, 10) == [keep]


async def test_ids_are_not_reused_after_monitor_delete(
    session: Session, repo: CheckRepository, monitor: Monitor, other_monitor: Monitor
) -> None:
    old = await add(repo, monitor)
    await MonitorRepository(session).delete(monitor.id)

    assert (await add(repo, other_monitor)).id > old.id


async def test_adding_a_check_for_a_missing_monitor_raises_not_found(
    repo: CheckRepository,
) -> None:
    with pytest.raises(MonitorNotFoundError) as exc_info:
        await repo.add(
            monitor_id=424242,
            url="https://a.test",
            status_code=200,
            latency_ms=1.0,
            error=None,
            checked_at=NOW,
        )

    assert exc_info.value.monitor_id == 424242


async def test_session_stays_usable_after_a_missing_monitor(
    repo: CheckRepository, monitor: Monitor
) -> None:
    with pytest.raises(MonitorNotFoundError):
        await add(repo, Monitor(424242, 1, "https://a.test", timedelta(minutes=5), 1.0))

    assert (await add(repo, monitor)).monitor_id == monitor.id


async def test_database_rejects_an_inconsistent_result(
    repo: CheckRepository, monitor: Monitor
) -> None:
    # Либо ответ, либо ошибка: страховка схемы (CHECK checks_status_xor_error).
    with pytest.raises(IntegrityError, match="checks_status_xor_error"):
        await repo.add(
            monitor_id=monitor.id,
            url="https://a.test",
            status_code=200,
            latency_ms=1.0,
            error="и ответ, и ошибка",
            checked_at=NOW,
        )


# --- настраиваемый предел --------------------------------------------------


async def test_custom_cap_limits_history(session: Session, monitor: Monitor) -> None:
    repo = CheckRepository(session, max_per_monitor=3)
    checks = [await add(repo, monitor, n=n) for n in range(5)]

    assert await repo.list_recent(monitor.id, 10) == [checks[4], checks[3], checks[2]]


async def test_cap_of_one_keeps_only_the_latest(
    session: Session, monitor: Monitor
) -> None:
    repo = CheckRepository(session, max_per_monitor=1)
    await add(repo, monitor, n=0)
    latest = await add(repo, monitor, n=1)

    assert await repo.list_recent(monitor.id, 10) == [latest]


async def test_default_cap_is_the_module_default(
    repo: CheckRepository, monitor: Monitor
) -> None:
    for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR + 1):
        await add(repo, monitor, n=n)

    assert len(await repo.list_recent(monitor.id, 1000)) == (
        DEFAULT_MAX_CHECKS_PER_MONITOR
    )


@pytest.mark.parametrize("cap", [0, -1])
def test_non_positive_cap_is_rejected(session: Session, cap: int) -> None:
    with pytest.raises(ValueError, match="max_per_monitor"):
        CheckRepository(session, max_per_monitor=cap)


# --- соединение ------------------------------------------------------------


async def test_release_connection_keeps_written_checks_visible(
    repo: CheckRepository, monitor: Monitor
) -> None:
    check = await add(repo, monitor)

    await repo.release_connection()

    assert await repo.list_recent(monitor.id, 10) == [check]
