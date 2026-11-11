"""Список мониторов с последней проверкой: права, пагинация, число запросов."""

from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.monitors import Monitor, MonitorWithLastCheck
from pulse.domain.users import Role, User
from pulse.repositories.audit_log import AuditLogRepository
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitor_overview import MonitorOverviewService
from pulse.services.monitors import MonitorService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)


@pytest.fixture(autouse=True)
async def _owners(persist_users: Callable[..., Awaitable[None]]) -> None:
    await persist_users(ALICE, BOB, ADMIN)


@pytest.fixture
def checks(session: Session) -> CheckRepository:
    return CheckRepository(session)


@pytest.fixture
def monitors(session: Session) -> MonitorRepository:
    return MonitorRepository(session)


@pytest.fixture
def service(
    session: Session, monitors: MonitorRepository, checks: CheckRepository
) -> MonitorOverviewService:
    return MonitorOverviewService(
        MonitorService(monitors, AuditLogRepository(session)), checks
    )


@pytest.fixture
def selects(engine: AsyncEngine) -> Iterator[list[str]]:
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *_: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


async def make(
    monitors: MonitorRepository, owner: User, i: int, interval_minutes: int = 5
) -> Monitor:
    return await monitors.create(
        owner_id=owner.id,
        url=f"https://m{i}.test",
        interval=timedelta(minutes=interval_minutes),
        timeout=5.0,
    )


async def add_check(checks: CheckRepository, monitor: Monitor, seconds: int) -> int:
    check = await checks.add(
        monitor_id=monitor.id,
        url=monitor.url,
        status_code=200,
        latency_ms=1.0,
        error=None,
        checked_at=NOW + timedelta(seconds=seconds),
    )
    return check.id


async def test_each_monitor_comes_with_its_newest_check(
    service: MonitorOverviewService,
    monitors: MonitorRepository,
    checks: CheckRepository,
) -> None:
    first, second, empty = [await make(monitors, ALICE, i) for i in range(3)]
    await add_check(checks, first, 10)
    newest = await add_check(checks, first, 20)
    only = await add_check(checks, second, 5)

    page = await service.list_page(ALICE, limit=10, offset=0)

    by_id = {m.id: m for m in page.items}
    assert by_id[first.id].last_check is not None
    assert by_id[first.id].last_check.id == newest  # type: ignore[union-attr]
    assert by_id[second.id].last_check is not None
    assert by_id[second.id].last_check.id == only  # type: ignore[union-attr]
    assert by_id[empty.id].last_check is None
    assert page.total == 3


async def test_manager_sees_only_own_monitors_admin_sees_all(
    service: MonitorOverviewService, monitors: MonitorRepository
) -> None:
    mine = await make(monitors, ALICE, 1)
    theirs = await make(monitors, BOB, 2)

    alice = await service.list_page(ALICE, limit=10, offset=0)
    admin = await service.list_page(ADMIN, limit=10, offset=0)

    assert [m.id for m in alice.items] == [mine.id]
    assert {m.id for m in admin.items} == {mine.id, theirs.id}
    assert alice.total == 1
    assert admin.total == 2


async def test_pagination_returns_checks_of_the_requested_page_only(
    service: MonitorOverviewService,
    monitors: MonitorRepository,
    checks: CheckRepository,
) -> None:
    created = [await make(monitors, ALICE, i) for i in range(5)]
    for i, m in enumerate(created):
        await add_check(checks, m, i)

    page = await service.list_page(ALICE, limit=2, offset=2)

    assert [m.id for m in page.items] == [created[2].id, created[3].id]
    assert [m.last_check.monitor_id for m in page.items if m.last_check] == [
        created[2].id,
        created[3].id,
    ]
    assert page.total == 5


async def test_empty_list_needs_no_checks_query(
    service: MonitorOverviewService, selects: list[str]
) -> None:
    page = await service.list_page(ALICE, limit=10, offset=0)

    assert (page.items, page.total) == ([], 0)
    assert len(selects) == 2  # страница и итог; запроса за проверками нет


@pytest.mark.parametrize("count", [1, 10, 40])
async def test_query_count_does_not_depend_on_page_size(
    service: MonitorOverviewService,
    monitors: MonitorRepository,
    checks: CheckRepository,
    selects: list[str],
    count: int,
) -> None:
    for i in range(count):
        await add_check(checks, await make(monitors, ALICE, i), i)
    selects.clear()

    page = await service.list_page(ALICE, limit=100, offset=0)

    assert len(page.items) == count
    # Страница мониторов, их общее число, последние проверки: ровно три.
    assert len(selects) == 3


async def test_result_equals_the_naive_one_query_per_monitor_version(
    session: Session,
    service: MonitorOverviewService,
    monitors: MonitorRepository,
    checks: CheckRepository,
) -> None:
    created = [await make(monitors, ALICE, i) for i in range(6)]
    for i, m in enumerate(created[:4]):
        for second in range(i + 1):
            await add_check(checks, m, second)

    fast = await service.list_page(ALICE, limit=100, offset=0)

    page = await MonitorService(monitors, AuditLogRepository(session)).list_page(
        ALICE, limit=100, offset=0
    )
    naive: list[MonitorWithLastCheck] = []
    for m in page.items:
        recent = await checks.list_recent(m.id, 1)
        naive.append(MonitorWithLastCheck.of(m, recent[0] if recent else None))
    assert fast.items == naive
