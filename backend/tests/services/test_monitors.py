from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.errors import (
    DuplicateMonitorError,
    InvalidMonitorError,
    MonitorNotFoundError,
)
from pulse.domain.monitors import Monitor
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitors import MonitorService

FIVE_MINUTES = timedelta(minutes=5)

ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)
VIEWER = User(4, "viewer@example.com", "h", Role.VIEWER)


@pytest.fixture
def repository() -> MonitorRepository:
    return MonitorRepository()


@pytest.fixture
def check_repository() -> CheckRepository:
    return CheckRepository()


@pytest.fixture
def service(
    repository: MonitorRepository, check_repository: CheckRepository
) -> MonitorService:
    return MonitorService(repository, check_repository)


def add(
    service: MonitorService, url: str = "https://a.test", actor: User = ALICE
) -> Monitor:
    return service.create(actor, url=url, interval=FIVE_MINUTES, timeout=10.0)


# --- создание и чтение ------------------------------------------------------


def test_create_stores_monitor_in_repository(
    service: MonitorService, repository: MonitorRepository
) -> None:
    created = add(service)

    assert repository.get(created.id) == created


def test_creator_becomes_the_owner(service: MonitorService) -> None:
    assert add(service, actor=BOB).owner_id == BOB.id


def test_admin_owns_what_admin_creates(service: MonitorService) -> None:
    assert add(service, actor=ADMIN).owner_id == ADMIN.id


def test_get_returns_own_monitor(service: MonitorService) -> None:
    created = add(service)

    assert service.get(ALICE, created.id) == created


def test_list_returns_only_own_monitors(service: MonitorService) -> None:
    mine = add(service, "https://a.test", ALICE)
    add(service, "https://b.test", BOB)

    assert service.list_all(ALICE) == [mine]
    assert [m.url for m in service.list_all(BOB)] == ["https://b.test"]


def test_list_of_user_without_monitors_is_empty(service: MonitorService) -> None:
    add(service, actor=ALICE)

    assert service.list_all(BOB) == []


def test_admin_lists_every_monitor(service: MonitorService) -> None:
    first = add(service, "https://a.test", ALICE)
    second = add(service, "https://b.test", BOB)

    assert service.list_all(ADMIN) == [first, second]


# --- чужие мониторы (IDOR) -------------------------------------------------


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_foreign_monitor_looks_like_a_missing_one(
    service: MonitorService, operation: str
) -> None:
    foreign = add(service, actor=ALICE)
    calls = {
        "get": lambda: service.get(BOB, foreign.id),
        "update": lambda: service.update(BOB, foreign.id, timeout=1.0),
        "delete": lambda: service.delete(BOB, foreign.id),
    }

    with pytest.raises(MonitorNotFoundError) as exc_info:
        calls[operation]()

    # Тот же текст, что у несуществующего id: ответ не выдаёт, что монитор есть.
    assert str(exc_info.value) == str(MonitorNotFoundError(foreign.id))


def test_foreign_monitor_error_is_identical_to_unknown_id_error(
    service: MonitorService,
) -> None:
    foreign = add(service, actor=ALICE)

    with pytest.raises(MonitorNotFoundError) as foreign_error:
        service.get(BOB, foreign.id)
    with pytest.raises(MonitorNotFoundError) as unknown_error:
        service.get(BOB, 999)

    assert type(foreign_error.value) is type(unknown_error.value)
    assert str(foreign_error.value).replace(str(foreign.id), "N") == str(
        unknown_error.value
    ).replace("999", "N")


def test_foreign_monitor_is_not_changed_or_deleted(
    service: MonitorService, repository: MonitorRepository
) -> None:
    foreign = add(service, actor=ALICE)

    with pytest.raises(MonitorNotFoundError):
        service.update(BOB, foreign.id, timeout=1.0)
    with pytest.raises(MonitorNotFoundError):
        service.delete(BOB, foreign.id)

    assert repository.get(foreign.id) == foreign


@pytest.mark.parametrize("role", [Role.MANAGER, Role.VIEWER])
def test_non_admin_cannot_reach_foreign_monitor(
    service: MonitorService, role: Role
) -> None:
    foreign = add(service, actor=ALICE)
    stranger = User(9, "s@example.com", "h", role)

    with pytest.raises(MonitorNotFoundError):
        service.get(stranger, foreign.id)


def test_admin_reaches_any_monitor(service: MonitorService) -> None:
    foreign = add(service, actor=ALICE)

    assert service.get(ADMIN, foreign.id) == foreign
    assert service.update(ADMIN, foreign.id, timeout=2.0).timeout == 2.0


def test_admin_edit_does_not_change_the_owner(service: MonitorService) -> None:
    foreign = add(service, actor=ALICE)

    assert service.update(ADMIN, foreign.id, timeout=2.0).owner_id == ALICE.id


def test_admin_can_delete_foreign_monitor(service: MonitorService) -> None:
    foreign = add(service, actor=ALICE)

    service.delete(ADMIN, foreign.id)

    assert service.list_all(ADMIN) == []


def test_viewer_can_read_what_they_own(service: MonitorService) -> None:
    # Роль ограничивает действия (это проверяет require_role), а не доступ к
    # собственным объектам: понижённый менеджер продолжает видеть свои мониторы.
    owned = add(service, actor=VIEWER)

    assert service.get(VIEWER, owned.id) == owned
    assert service.list_all(VIEWER) == [owned]


# --- обновление ------------------------------------------------------------


def test_update_changes_only_given_fields(service: MonitorService) -> None:
    created = add(service)

    updated = service.update(ALICE, created.id, timeout=3.0)

    assert updated == Monitor(created.id, ALICE.id, created.url, FIVE_MINUTES, 3.0)
    assert service.get(ALICE, created.id) == updated


def test_update_without_fields_keeps_monitor(service: MonitorService) -> None:
    created = add(service)

    assert service.update(ALICE, created.id) == created


def test_delete_removes_monitor(service: MonitorService) -> None:
    created = add(service)

    service.delete(ALICE, created.id)

    assert service.list_all(ALICE) == []


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_unknown_id_raises_not_found(service: MonitorService, operation: str) -> None:
    calls = {
        "get": lambda: service.get(ALICE, 7),
        "update": lambda: service.update(ALICE, 7, timeout=1.0),
        "delete": lambda: service.delete(ALICE, 7),
    }

    with pytest.raises(MonitorNotFoundError):
        calls[operation]()


# --- дубликаты URL: уникальность (owner_id, url) ---------------------------


def test_create_rejects_duplicate_url_of_the_same_owner(
    service: MonitorService,
) -> None:
    existing = add(service, "https://a.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        add(service, "https://a.test")

    assert exc_info.value.url == "https://a.test"
    assert exc_info.value.existing_id == existing.id


def test_same_url_is_allowed_for_different_owners(service: MonitorService) -> None:
    add(service, "https://a.test", ALICE)

    assert add(service, "https://a.test", BOB).owner_id == BOB.id


def test_duplicate_error_never_exposes_a_foreign_monitor_id(
    service: MonitorService,
) -> None:
    add(service, "https://a.test", ALICE)

    # У Боба такого URL нет: конфликта нет, и id чужого монитора не раскрывается.
    created = add(service, "https://a.test", BOB)

    assert created.owner_id == BOB.id


def test_failed_create_stores_nothing_and_keeps_id_sequence(
    service: MonitorService,
) -> None:
    add(service, "https://a.test")
    with pytest.raises(DuplicateMonitorError):
        add(service, "https://a.test")

    assert [m.id for m in service.list_all(ALICE)] == [1]
    assert add(service, "https://b.test").id == 2


def test_url_can_be_reused_after_delete(service: MonitorService) -> None:
    first = add(service, "https://a.test")
    service.delete(ALICE, first.id)

    assert add(service, "https://a.test").id == 2


def test_update_rejects_url_of_another_monitor_of_the_same_owner(
    service: MonitorService,
) -> None:
    first = add(service, "https://a.test")
    second = add(service, "https://b.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        service.update(ALICE, second.id, url="https://a.test")

    assert exc_info.value.existing_id == first.id
    assert service.get(ALICE, second.id).url == "https://b.test"


def test_update_may_take_a_url_that_only_another_owner_has(
    service: MonitorService,
) -> None:
    add(service, "https://a.test", BOB)
    mine = add(service, "https://b.test", ALICE)

    assert service.update(ALICE, mine.id, url="https://a.test").url == "https://a.test"


def test_update_may_keep_its_own_url(service: MonitorService) -> None:
    created = add(service, "https://a.test")

    updated = service.update(ALICE, created.id, url="https://a.test", timeout=3.0)

    assert (updated.url, updated.timeout) == ("https://a.test", 3.0)


def test_admin_edit_checks_uniqueness_within_the_monitors_owner(
    service: MonitorService,
) -> None:
    add(service, "https://a.test", ALICE)
    second = add(service, "https://b.test", ALICE)
    add(service, "https://c.test", ADMIN)

    with pytest.raises(DuplicateMonitorError):
        service.update(ADMIN, second.id, url="https://a.test")
    # У админа самого есть c.test, но монитор принадлежит Алисе: конфликта нет.
    assert (
        service.update(ADMIN, second.id, url="https://c.test").url == "https://c.test"
    )


def test_update_to_a_free_url_frees_the_old_one(service: MonitorService) -> None:
    created = add(service, "https://a.test")
    service.update(ALICE, created.id, url="https://b.test")

    assert add(service, "https://a.test").id == 2


def test_update_without_url_never_checks_duplicates(service: MonitorService) -> None:
    created = add(service, "https://a.test")

    assert service.update(ALICE, created.id, timeout=2.0).timeout == 2.0


def test_update_unknown_id_is_not_found_not_conflict(service: MonitorService) -> None:
    add(service, "https://a.test")

    with pytest.raises(MonitorNotFoundError):
        service.update(ALICE, 99, url="https://a.test")


def test_update_foreign_monitor_is_not_found_not_conflict(
    service: MonitorService,
) -> None:
    add(service, "https://a.test", BOB)
    foreign = add(service, "https://b.test", ALICE)

    with pytest.raises(MonitorNotFoundError):
        service.update(BOB, foreign.id, url="https://a.test")


# --- каскад ----------------------------------------------------------------


def add_check(checks: CheckRepository, monitor_id: int) -> None:
    checks.add(
        monitor_id=monitor_id,
        url="https://a.test",
        status_code=200,
        latency_ms=1.0,
        error=None,
        checked_at=datetime(2026, 1, 1, tzinfo=UTC),
        ok=True,
    )


def test_delete_removes_checks_of_that_monitor_only(
    service: MonitorService, check_repository: CheckRepository
) -> None:
    first, second = add(service, "https://a.test"), add(service, "https://b.test")
    add_check(check_repository, first.id)
    add_check(check_repository, second.id)

    service.delete(ALICE, first.id)

    assert check_repository.list_recent(first.id, 10) == []
    assert len(check_repository.list_recent(second.id, 10)) == 1


def test_failed_foreign_delete_keeps_checks(
    service: MonitorService, check_repository: CheckRepository
) -> None:
    foreign = add(service, actor=ALICE)
    add_check(check_repository, foreign.id)

    with pytest.raises(MonitorNotFoundError):
        service.delete(BOB, foreign.id)

    assert len(check_repository.list_recent(foreign.id, 10)) == 1


def test_delete_of_unknown_monitor_keeps_checks(
    service: MonitorService, check_repository: CheckRepository
) -> None:
    add_check(check_repository, 5)

    with pytest.raises(MonitorNotFoundError):
        service.delete(ALICE, 5)

    assert len(check_repository.list_recent(5, 10)) == 1


def test_services_over_different_repositories_do_not_share_state() -> None:
    first = MonitorService(MonitorRepository(), CheckRepository())
    second = MonitorService(MonitorRepository(), CheckRepository())
    add(first)

    assert second.list_all(ALICE) == []


# --- согласованность интервала и таймаута -----------------------------------


def test_create_rejects_timeout_not_shorter_than_interval(
    service: MonitorService, repository: MonitorRepository
) -> None:
    with pytest.raises(InvalidMonitorError) as exc_info:
        service.create(
            ALICE, url="https://a.test", interval=timedelta(seconds=30), timeout=30
        )

    assert exc_info.value.field == "timeout"
    assert repository.list_all() == []


def test_update_rejects_timeout_that_does_not_fit_the_current_interval(
    service: MonitorService,
) -> None:
    monitor = add(service)  # интервал 5 минут

    with pytest.raises(InvalidMonitorError) as exc_info:
        service.update(ALICE, monitor.id, timeout=300)

    assert exc_info.value.field == "timeout"
    assert service.get(ALICE, monitor.id).timeout == 10.0


def test_update_rejects_interval_shorter_than_the_current_timeout(
    service: MonitorService,
) -> None:
    monitor = service.create(
        ALICE, url="https://a.test", interval=FIVE_MINUTES, timeout=100
    )

    with pytest.raises(InvalidMonitorError) as exc_info:
        service.update(ALICE, monitor.id, interval=timedelta(seconds=60))

    assert exc_info.value.field == "interval"
    assert service.get(ALICE, monitor.id).interval == FIVE_MINUTES


def test_update_accepts_a_consistent_pair_changed_together(
    service: MonitorService,
) -> None:
    monitor = service.create(
        ALICE, url="https://a.test", interval=FIVE_MINUTES, timeout=100
    )

    changed = service.update(
        ALICE, monitor.id, interval=timedelta(seconds=60), timeout=50
    )

    assert (changed.interval, changed.timeout) == (timedelta(seconds=60), 50)


def test_update_of_other_fields_ignores_the_pair_rule_when_it_holds(
    service: MonitorService,
) -> None:
    monitor = add(service)

    changed = service.update(ALICE, monitor.id, url="https://b.test")

    assert changed.url == "https://b.test"


def test_missing_monitor_is_reported_before_invalid_values(
    service: MonitorService,
) -> None:
    with pytest.raises(MonitorNotFoundError):
        service.update(ALICE, 999, timeout=10_000)


def test_foreign_monitor_is_reported_as_missing_not_as_invalid(
    service: MonitorService,
) -> None:
    monitor = add(service, actor=BOB)

    with pytest.raises(MonitorNotFoundError):
        service.update(ALICE, monitor.id, timeout=10_000)
