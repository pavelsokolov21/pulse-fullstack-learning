from datetime import timedelta

import pytest

from pulse.domain.errors import DuplicateMonitorError, MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitors import MonitorService

FIVE_MINUTES = timedelta(minutes=5)


@pytest.fixture
def repository() -> MonitorRepository:
    return MonitorRepository()


@pytest.fixture
def service(repository: MonitorRepository) -> MonitorService:
    return MonitorService(repository)


def add(service: MonitorService, url: str = "https://a.test") -> Monitor:
    return service.create(url=url, interval=FIVE_MINUTES, timeout=10.0)


def test_create_stores_monitor_in_repository(
    service: MonitorService, repository: MonitorRepository
) -> None:
    created = add(service)

    assert repository.get(created.id) == created


def test_get_and_list_return_what_was_created(service: MonitorService) -> None:
    first, second = add(service, "https://a.test"), add(service, "https://b.test")

    assert service.get(first.id) == first
    assert service.list_all() == [first, second]


def test_update_changes_only_given_fields(service: MonitorService) -> None:
    created = add(service)

    updated = service.update(created.id, timeout=3.0)

    assert updated == Monitor(created.id, created.url, FIVE_MINUTES, 3.0)
    assert service.get(created.id) == updated


def test_update_without_fields_keeps_monitor(service: MonitorService) -> None:
    created = add(service)

    assert service.update(created.id) == created


def test_delete_removes_monitor(service: MonitorService) -> None:
    created = add(service)

    service.delete(created.id)

    assert service.list_all() == []


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_unknown_id_raises_domain_error(
    service: MonitorService, operation: str
) -> None:
    calls = {
        "get": lambda: service.get(7),
        "update": lambda: service.update(7, timeout=1.0),
        "delete": lambda: service.delete(7),
    }

    with pytest.raises(MonitorNotFoundError):
        calls[operation]()


def test_services_over_different_repositories_do_not_share_state() -> None:
    first = MonitorService(MonitorRepository())
    second = MonitorService(MonitorRepository())
    add(first)

    assert second.list_all() == []


# --- дубликаты URL ---------------------------------------------------------


def test_create_rejects_duplicate_url(service: MonitorService) -> None:
    existing = add(service, "https://a.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        add(service, "https://a.test")

    assert exc_info.value.url == "https://a.test"
    assert exc_info.value.existing_id == existing.id
    assert str(existing.id) in str(exc_info.value)


def test_failed_create_stores_nothing_and_keeps_id_sequence(
    service: MonitorService,
) -> None:
    add(service, "https://a.test")
    with pytest.raises(DuplicateMonitorError):
        add(service, "https://a.test")

    assert [m.id for m in service.list_all()] == [1]
    assert add(service, "https://b.test").id == 2


def test_different_urls_are_allowed(service: MonitorService) -> None:
    add(service, "https://a.test")

    assert add(service, "https://a.test/other").id == 2


def test_url_can_be_reused_after_delete(service: MonitorService) -> None:
    first = add(service, "https://a.test")
    service.delete(first.id)

    assert add(service, "https://a.test").id == 2


def test_update_rejects_url_of_another_monitor(service: MonitorService) -> None:
    first = add(service, "https://a.test")
    second = add(service, "https://b.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        service.update(second.id, url="https://a.test")

    assert exc_info.value.existing_id == first.id
    assert service.get(second.id).url == "https://b.test"


def test_update_may_keep_its_own_url(service: MonitorService) -> None:
    created = add(service, "https://a.test")

    updated = service.update(created.id, url="https://a.test", timeout=3.0)

    assert (updated.url, updated.timeout) == ("https://a.test", 3.0)


def test_update_to_a_free_url_frees_the_old_one(service: MonitorService) -> None:
    created = add(service, "https://a.test")
    service.update(created.id, url="https://b.test")

    assert add(service, "https://a.test").id == 2


def test_update_without_url_never_checks_duplicates(service: MonitorService) -> None:
    created = add(service, "https://a.test")

    assert service.update(created.id, timeout=2.0).timeout == 2.0


def test_update_unknown_id_is_not_found_not_conflict(service: MonitorService) -> None:
    add(service, "https://a.test")

    with pytest.raises(MonitorNotFoundError):
        service.update(99, url="https://a.test")
