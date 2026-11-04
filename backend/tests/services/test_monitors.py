from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.errors import (
    DuplicateMonitorError,
    InvalidMonitorError,
    MonitorNotFoundError,
)
from pulse.domain.monitors import Monitor, MonitorWithOwner
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitors import MonitorService

FIVE_MINUTES = timedelta(minutes=5)
# Заведомо не выданный identity-ключ: id не восстанавливаются после отката теста.
UNKNOWN_ID = 2_000_000_000

ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)
VIEWER = User(4, "viewer@example.com", "h", Role.VIEWER)


@pytest.fixture(autouse=True)
async def _owners(persist_users: Callable[..., Awaitable[None]]) -> None:
    """Владельцы мониторов существуют в базе с теми же id, что у констант выше."""
    await persist_users(ALICE, BOB, ADMIN, VIEWER)


@pytest.fixture
def repository(session: Session) -> MonitorRepository:
    return MonitorRepository(session)


@pytest.fixture
def check_repository(session: Session) -> CheckRepository:
    return CheckRepository(session)


@pytest.fixture
def service(repository: MonitorRepository) -> MonitorService:
    return MonitorService(repository)


def plain(monitor: MonitorWithOwner) -> Monitor:
    return Monitor(
        monitor.id, monitor.owner_id, monitor.url, monitor.interval, monitor.timeout
    )


async def listed(service: MonitorService, actor: User) -> list[Monitor]:
    page = await service.list_page(actor, limit=1000, offset=0)
    return [plain(m) for m in page.items]


async def listed_by_repository(repository: MonitorRepository) -> list[Monitor]:
    page = await repository.list_page(owner_id=None, limit=1000, offset=0)
    return [plain(m) for m in page.items]


async def add(
    service: MonitorService, url: str = "https://a.test", actor: User = ALICE
) -> Monitor:
    return await service.create(actor, url=url, interval=FIVE_MINUTES, timeout=10.0)


# --- создание и чтение ------------------------------------------------------


async def test_create_stores_monitor_in_repository(
    service: MonitorService, repository: MonitorRepository
) -> None:
    created = await add(service)

    assert await repository.get(created.id) == created


async def test_creator_becomes_the_owner(service: MonitorService) -> None:
    assert (await add(service, actor=BOB)).owner_id == BOB.id


async def test_admin_owns_what_admin_creates(service: MonitorService) -> None:
    assert (await add(service, actor=ADMIN)).owner_id == ADMIN.id


async def test_get_returns_own_monitor(service: MonitorService) -> None:
    created = await add(service)

    assert await service.get(ALICE, created.id) == created


async def test_list_returns_only_own_monitors(service: MonitorService) -> None:
    mine = await add(service, "https://a.test", ALICE)
    await add(service, "https://b.test", BOB)

    assert await listed(service, ALICE) == [mine]
    assert [m.url for m in await listed(service, BOB)] == ["https://b.test"]


async def test_list_of_user_without_monitors_is_empty(service: MonitorService) -> None:
    await add(service, actor=ALICE)

    assert await listed(service, BOB) == []


async def test_admin_lists_every_monitor(service: MonitorService) -> None:
    first = await add(service, "https://a.test", ALICE)
    second = await add(service, "https://b.test", BOB)

    assert await listed(service, ADMIN) == [first, second]


async def test_page_of_a_manager_has_only_own_monitors_and_total(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", ALICE)
    await add(service, "https://b.test", ALICE)
    await add(service, "https://c.test", BOB)

    page = await service.list_page(ALICE, limit=1, offset=0)

    assert (len(page.items), page.total) == (1, 2)
    assert page.items[0].owner_email == ALICE.email


async def test_page_of_an_admin_has_every_monitor_with_owner_emails(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", ALICE)
    await add(service, "https://b.test", BOB)

    page = await service.list_page(ADMIN, limit=10, offset=0)

    assert page.total == 2
    assert [(m.url, m.owner_email) for m in page.items] == [
        ("https://a.test", ALICE.email),
        ("https://b.test", BOB.email),
    ]


# --- чужие мониторы (IDOR) -------------------------------------------------


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
async def test_foreign_monitor_looks_like_a_missing_one(
    service: MonitorService, operation: str
) -> None:
    foreign = await add(service, actor=ALICE)
    calls = {
        "get": lambda: service.get(BOB, foreign.id),
        "update": lambda: service.update(BOB, foreign.id, timeout=1.0),
        "delete": lambda: service.delete(BOB, foreign.id),
    }

    with pytest.raises(MonitorNotFoundError) as exc_info:
        await calls[operation]()

    # Тот же текст, что у несуществующего id: ответ не выдаёт, что монитор есть.
    assert str(exc_info.value) == str(MonitorNotFoundError(foreign.id))


async def test_foreign_monitor_error_is_identical_to_unknown_id_error(
    service: MonitorService,
) -> None:
    foreign = await add(service, actor=ALICE)

    with pytest.raises(MonitorNotFoundError) as foreign_error:
        await service.get(BOB, foreign.id)
    with pytest.raises(MonitorNotFoundError) as unknown_error:
        await service.get(BOB, UNKNOWN_ID)

    assert type(foreign_error.value) is type(unknown_error.value)
    assert str(foreign_error.value).replace(str(foreign.id), "N") == str(
        unknown_error.value
    ).replace(str(UNKNOWN_ID), "N")


async def test_foreign_monitor_is_not_changed_or_deleted(
    service: MonitorService, repository: MonitorRepository
) -> None:
    foreign = await add(service, actor=ALICE)

    with pytest.raises(MonitorNotFoundError):
        await service.update(BOB, foreign.id, timeout=1.0)
    with pytest.raises(MonitorNotFoundError):
        await service.delete(BOB, foreign.id)

    assert await repository.get(foreign.id) == foreign


@pytest.mark.parametrize("role", [Role.MANAGER, Role.VIEWER])
async def test_non_admin_cannot_reach_foreign_monitor(
    service: MonitorService, role: Role
) -> None:
    foreign = await add(service, actor=ALICE)
    stranger = User(9, "s@example.com", "h", role)

    with pytest.raises(MonitorNotFoundError):
        await service.get(stranger, foreign.id)


async def test_admin_reaches_any_monitor(service: MonitorService) -> None:
    foreign = await add(service, actor=ALICE)

    assert await service.get(ADMIN, foreign.id) == foreign
    assert (await service.update(ADMIN, foreign.id, timeout=2.0)).timeout == 2.0


async def test_admin_edit_does_not_change_the_owner(service: MonitorService) -> None:
    foreign = await add(service, actor=ALICE)

    assert (await service.update(ADMIN, foreign.id, timeout=2.0)).owner_id == ALICE.id


async def test_admin_can_delete_foreign_monitor(service: MonitorService) -> None:
    foreign = await add(service, actor=ALICE)

    await service.delete(ADMIN, foreign.id)

    assert await listed(service, ADMIN) == []


async def test_viewer_can_read_what_they_own(service: MonitorService) -> None:
    # Роль ограничивает действия (это проверяет require_role), а не доступ к
    # собственным объектам: понижённый менеджер продолжает видеть свои мониторы.
    owned = await add(service, actor=VIEWER)

    assert await service.get(VIEWER, owned.id) == owned
    assert await listed(service, VIEWER) == [owned]


# --- обновление ------------------------------------------------------------


async def test_update_changes_only_given_fields(service: MonitorService) -> None:
    created = await add(service)

    updated = await service.update(ALICE, created.id, timeout=3.0)

    assert updated == Monitor(created.id, ALICE.id, created.url, FIVE_MINUTES, 3.0)
    assert await service.get(ALICE, created.id) == updated


async def test_update_without_fields_keeps_monitor(service: MonitorService) -> None:
    created = await add(service)

    assert await service.update(ALICE, created.id) == created


async def test_delete_removes_monitor(service: MonitorService) -> None:
    created = await add(service)

    await service.delete(ALICE, created.id)

    assert await listed(service, ALICE) == []


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
async def test_unknown_id_raises_not_found(
    service: MonitorService, operation: str
) -> None:
    calls = {
        "get": lambda: service.get(ALICE, UNKNOWN_ID),
        "update": lambda: service.update(ALICE, UNKNOWN_ID, timeout=1.0),
        "delete": lambda: service.delete(ALICE, UNKNOWN_ID),
    }

    with pytest.raises(MonitorNotFoundError):
        await calls[operation]()


# --- дубликаты URL: уникальность (owner_id, url) ---------------------------


async def test_create_rejects_duplicate_url_of_the_same_owner(
    service: MonitorService,
) -> None:
    existing = await add(service, "https://a.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        await add(service, "https://a.test")

    assert exc_info.value.url == "https://a.test"
    assert exc_info.value.existing_id == existing.id


async def test_same_url_is_allowed_for_different_owners(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", ALICE)

    assert (await add(service, "https://a.test", BOB)).owner_id == BOB.id


async def test_duplicate_error_never_exposes_a_foreign_monitor_id(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", ALICE)

    # У Боба такого URL нет: конфликта нет, и id чужого монитора не раскрывается.
    created = await add(service, "https://a.test", BOB)

    assert created.owner_id == BOB.id


async def test_failed_create_stores_nothing(
    service: MonitorService,
) -> None:
    first = await add(service, "https://a.test")
    with pytest.raises(DuplicateMonitorError):
        await add(service, "https://a.test")

    assert [m.id for m in await listed(service, ALICE)] == [first.id]


async def test_url_can_be_reused_after_delete(service: MonitorService) -> None:
    first = await add(service, "https://a.test")
    await service.delete(ALICE, first.id)

    assert (await add(service, "https://a.test")).id > first.id


async def test_update_rejects_url_of_another_monitor_of_the_same_owner(
    service: MonitorService,
) -> None:
    first = await add(service, "https://a.test")
    second = await add(service, "https://b.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        await service.update(ALICE, second.id, url="https://a.test")

    assert exc_info.value.existing_id == first.id
    assert (await service.get(ALICE, second.id)).url == "https://b.test"


async def test_update_may_take_a_url_that_only_another_owner_has(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", BOB)
    mine = await add(service, "https://b.test", ALICE)

    assert (
        await service.update(ALICE, mine.id, url="https://a.test")
    ).url == "https://a.test"


async def test_update_may_keep_its_own_url(service: MonitorService) -> None:
    created = await add(service, "https://a.test")

    updated = await service.update(ALICE, created.id, url="https://a.test", timeout=3.0)

    assert (updated.url, updated.timeout) == ("https://a.test", 3.0)


async def test_admin_edit_checks_uniqueness_within_the_monitors_owner(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", ALICE)
    second = await add(service, "https://b.test", ALICE)
    await add(service, "https://c.test", ADMIN)

    with pytest.raises(DuplicateMonitorError):
        await service.update(ADMIN, second.id, url="https://a.test")
    # У админа самого есть c.test, но монитор принадлежит Алисе: конфликта нет.
    assert (
        await service.update(ADMIN, second.id, url="https://c.test")
    ).url == "https://c.test"


async def test_update_to_a_free_url_frees_the_old_one(service: MonitorService) -> None:
    created = await add(service, "https://a.test")
    await service.update(ALICE, created.id, url="https://b.test")

    assert (await add(service, "https://a.test")).id > created.id


async def test_update_without_url_never_checks_duplicates(
    service: MonitorService,
) -> None:
    created = await add(service, "https://a.test")

    assert (await service.update(ALICE, created.id, timeout=2.0)).timeout == 2.0


async def test_update_unknown_id_is_not_found_not_conflict(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test")

    with pytest.raises(MonitorNotFoundError):
        await service.update(ALICE, 99, url="https://a.test")


async def test_update_foreign_monitor_is_not_found_not_conflict(
    service: MonitorService,
) -> None:
    await add(service, "https://a.test", BOB)
    foreign = await add(service, "https://b.test", ALICE)

    with pytest.raises(MonitorNotFoundError):
        await service.update(BOB, foreign.id, url="https://a.test")


# --- каскад ----------------------------------------------------------------


async def add_check(checks: CheckRepository, monitor_id: int) -> None:
    await checks.add(
        monitor_id=monitor_id,
        url="https://a.test",
        status_code=200,
        latency_ms=1.0,
        error=None,
        checked_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_delete_removes_checks_of_that_monitor_only(
    service: MonitorService, check_repository: CheckRepository
) -> None:
    first, second = (
        await add(service, "https://a.test"),
        await add(service, "https://b.test"),
    )
    await add_check(check_repository, first.id)
    await add_check(check_repository, second.id)

    await service.delete(ALICE, first.id)

    assert await check_repository.list_recent(first.id, 10) == []
    assert len(await check_repository.list_recent(second.id, 10)) == 1


async def test_failed_foreign_delete_keeps_checks(
    service: MonitorService, check_repository: CheckRepository
) -> None:
    foreign = await add(service, actor=ALICE)
    await add_check(check_repository, foreign.id)

    with pytest.raises(MonitorNotFoundError):
        await service.delete(BOB, foreign.id)

    assert len(await check_repository.list_recent(foreign.id, 10)) == 1


# --- согласованность интервала и таймаута -----------------------------------


async def test_create_rejects_timeout_not_shorter_than_interval(
    service: MonitorService, repository: MonitorRepository
) -> None:
    with pytest.raises(InvalidMonitorError) as exc_info:
        await service.create(
            ALICE, url="https://a.test", interval=timedelta(seconds=30), timeout=30
        )

    assert exc_info.value.field == "timeout"
    assert await listed_by_repository(repository) == []


async def test_update_rejects_timeout_that_does_not_fit_the_current_interval(
    service: MonitorService,
) -> None:
    monitor = await add(service)  # интервал 5 минут

    with pytest.raises(InvalidMonitorError) as exc_info:
        await service.update(ALICE, monitor.id, timeout=300)

    assert exc_info.value.field == "timeout"
    assert (await service.get(ALICE, monitor.id)).timeout == 10.0


async def test_update_rejects_interval_shorter_than_the_current_timeout(
    service: MonitorService,
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE_MINUTES, timeout=100
    )

    with pytest.raises(InvalidMonitorError) as exc_info:
        await service.update(ALICE, monitor.id, interval=timedelta(seconds=60))

    assert exc_info.value.field == "interval"
    assert (await service.get(ALICE, monitor.id)).interval == FIVE_MINUTES


async def test_update_accepts_a_consistent_pair_changed_together(
    service: MonitorService,
) -> None:
    monitor = await service.create(
        ALICE, url="https://a.test", interval=FIVE_MINUTES, timeout=100
    )

    changed = await service.update(
        ALICE, monitor.id, interval=timedelta(seconds=60), timeout=50
    )

    assert (changed.interval, changed.timeout) == (timedelta(seconds=60), 50)


async def test_update_of_other_fields_ignores_the_pair_rule_when_it_holds(
    service: MonitorService,
) -> None:
    monitor = await add(service)

    changed = await service.update(ALICE, monitor.id, url="https://b.test")

    assert changed.url == "https://b.test"


async def test_missing_monitor_is_reported_before_invalid_values(
    service: MonitorService,
) -> None:
    with pytest.raises(MonitorNotFoundError):
        await service.update(ALICE, UNKNOWN_ID, timeout=10_000)


async def test_foreign_monitor_is_reported_as_missing_not_as_invalid(
    service: MonitorService,
) -> None:
    monitor = await add(service, actor=BOB)

    with pytest.raises(MonitorNotFoundError):
        await service.update(ALICE, monitor.id, timeout=10_000)
