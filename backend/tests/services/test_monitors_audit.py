"""Журнал аудита при создании, изменении и удалении мониторов."""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from pulse.domain.errors import (
    DuplicateMonitorError,
    InvalidMonitorError,
    MonitorNotFoundError,
)
from pulse.domain.users import Role, User
from pulse.repositories.audit_log import AuditLogRepository
from pulse.repositories.database import Session
from pulse.repositories.models import AuditLogModel
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitors import MonitorService

FIVE = timedelta(minutes=5)
ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)
UNKNOWN_ID = 2_000_000_000


@pytest.fixture(autouse=True)
async def _owners(persist_users: Callable[..., Awaitable[None]]) -> None:
    await persist_users(ALICE, BOB, ADMIN)


@pytest.fixture
def audit(session: Session) -> AuditLogRepository:
    return AuditLogRepository(session)


@pytest.fixture
def service(session: Session, audit: AuditLogRepository) -> MonitorService:
    return MonitorService(MonitorRepository(session), audit)


async def test_create_is_logged_with_a_snapshot(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE, timeout=3
    )

    [entry] = await audit.list_for_entity("monitor", monitor.id)

    assert entry.action == "monitor.create"
    assert entry.actor_id == ALICE.id
    assert entry.details == {
        "url": "https://a.test",
        "interval_seconds": 300.0,
        "timeout": 3.0,
    }


async def test_update_logs_only_what_changed(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE, timeout=3
    )

    await service.update(ALICE, monitor.id, timeout=4, url="https://a.test")

    entry = (await audit.list_for_entity("monitor", monitor.id))[-1]
    assert entry.action == "monitor.update"
    assert entry.details == {
        "owner_id": ALICE.id,
        # url передан, но не изменился: в журнале его нет
        "changes": {"timeout": {"from": 3.0, "to": 4.0}},
    }


async def test_update_with_the_same_values_writes_nothing(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE, timeout=3
    )

    await service.update(ALICE, monitor.id, timeout=3, interval=FIVE)

    actions = [e.action for e in await audit.list_for_entity("monitor", monitor.id)]
    assert actions == ["monitor.create"]


async def test_update_can_change_several_fields(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE, timeout=3
    )

    await service.update(
        ALICE, monitor.id, url="https://b.test", interval=timedelta(minutes=10)
    )

    entry = (await audit.list_for_entity("monitor", monitor.id))[-1]
    assert entry.details["changes"] == {
        "url": {"from": "https://a.test", "to": "https://b.test"},
        "interval_seconds": {"from": 300.0, "to": 600.0},
    }


async def test_delete_keeps_a_snapshot_of_the_deleted_monitor(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE, timeout=3
    )

    await service.delete(ALICE, monitor.id)

    entry = (await audit.list_for_entity("monitor", monitor.id))[-1]
    assert entry.action == "monitor.delete"
    assert entry.actor_id == ALICE.id
    assert entry.details == {
        "owner_id": ALICE.id,
        "url": "https://a.test",
        "interval_seconds": 300.0,
        "timeout": 3.0,
    }


async def test_admin_actions_on_foreign_monitors_name_the_admin_and_the_owner(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    monitor = await service.create(BOB, url="https://a.test", interval=FIVE, timeout=3)

    await service.update(ADMIN, monitor.id, timeout=4)
    await service.delete(ADMIN, monitor.id)

    update, delete = (await audit.list_for_entity("monitor", monitor.id))[-2:]
    assert (update.actor_id, update.details["owner_id"]) == (ADMIN.id, BOB.id)
    assert (delete.actor_id, delete.details["owner_id"]) == (ADMIN.id, BOB.id)


async def test_failed_create_leaves_no_entry(
    service: MonitorService, audit: AuditLogRepository
) -> None:
    first = await service.create(ALICE, url="https://a.test", interval=FIVE, timeout=3)

    with pytest.raises(DuplicateMonitorError):
        await service.create(ALICE, url="https://a.test", interval=FIVE, timeout=3)

    assert len(await audit.list_for_entity("monitor", first.id)) == 1


async def test_invalid_create_leaves_no_entry(
    service: MonitorService, session: Session
) -> None:
    with pytest.raises(InvalidMonitorError):
        await service.create(
            ALICE, url="https://a.test", interval=timedelta(seconds=5), timeout=10
        )

    count = await session.scalar(_count_entries())
    assert count == 0


async def test_foreign_or_missing_monitor_leaves_no_entry(
    service: MonitorService, session: Session
) -> None:
    mine = await service.create(ALICE, url="https://a.test", interval=FIVE, timeout=3)
    before = await session.scalar(_count_entries())

    with pytest.raises(MonitorNotFoundError):
        await service.update(BOB, mine.id, timeout=4)
    with pytest.raises(MonitorNotFoundError):
        await service.delete(BOB, mine.id)
    with pytest.raises(MonitorNotFoundError):
        await service.delete(ALICE, UNKNOWN_ID)

    assert await session.scalar(_count_entries()) == before


def _count_entries():

    return select(func.count()).select_from(AuditLogModel)
