import builtins
from datetime import timedelta
from typing import get_type_hints

import pytest

from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.repositories.monitors import MonitorRepository

FIVE_MINUTES = timedelta(minutes=5)
OWNER = 1


def add(
    store: MonitorRepository,
    url: str = "https://a.test",
    timeout: float = 10.0,
    owner_id: int = OWNER,
) -> Monitor:
    return store.create(
        owner_id=owner_id, url=url, interval=FIVE_MINUTES, timeout=timeout
    )


def test_new_store_is_empty() -> None:
    assert MonitorRepository().list_all() == []


def test_create_assigns_increasing_ids_from_one() -> None:
    store = MonitorRepository()

    ids = [add(store, f"https://{n}.test").id for n in range(3)]

    assert ids == [1, 2, 3]


def test_create_keeps_given_values() -> None:
    monitor = add(MonitorRepository(), "https://a.test", timeout=3.0)

    assert monitor == Monitor(1, OWNER, "https://a.test", FIVE_MINUTES, 3.0)


def test_get_returns_what_was_created() -> None:
    store = MonitorRepository()
    created = add(store)

    assert store.get(created.id) == created


def test_list_keeps_creation_order() -> None:
    store = MonitorRepository()
    created = [add(store, f"https://{n}.test") for n in range(3)]

    assert store.list_all() == created


def test_list_returns_a_copy() -> None:
    store = MonitorRepository()
    add(store)

    store.list_all().clear()

    assert len(store.list_all()) == 1


def test_update_applies_only_given_fields() -> None:
    store = MonitorRepository()
    created = add(store, timeout=3.0)

    updated = store.update(created.id, timeout=9.0)

    assert updated == Monitor(created.id, OWNER, created.url, FIVE_MINUTES, 9.0)
    assert store.get(created.id) == updated


def test_update_can_change_every_field_at_once() -> None:
    store = MonitorRepository()
    created = add(store)

    updated = store.update(
        created.id, url="https://b.test", interval=timedelta(hours=1), timeout=1.0
    )

    assert updated == Monitor(
        created.id, OWNER, "https://b.test", timedelta(hours=1), 1.0
    )


def test_update_without_fields_keeps_monitor() -> None:
    store = MonitorRepository()
    created = add(store)

    assert store.update(created.id) == created


def test_update_does_not_touch_other_monitors() -> None:
    store = MonitorRepository()
    first, second = add(store, "https://a.test"), add(store, "https://b.test")

    store.update(first.id, url="https://changed.test")

    assert store.get(second.id) == second


def test_delete_removes_only_that_monitor() -> None:
    store = MonitorRepository()
    first, second = add(store, "https://a.test"), add(store, "https://b.test")

    store.delete(first.id)

    assert store.list_all() == [second]


def test_ids_are_not_reused_after_delete() -> None:
    store = MonitorRepository()
    first = add(store)
    store.delete(first.id)

    assert add(store).id == first.id + 1


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_unknown_id_raises_monitor_not_found(operation: str) -> None:
    store = MonitorRepository()
    calls = {
        "get": lambda: store.get(5),
        "update": lambda: store.update(5, timeout=1.0),
        "delete": lambda: store.delete(5),
    }

    with pytest.raises(MonitorNotFoundError) as exc_info:
        calls[operation]()

    assert exc_info.value.monitor_id == 5
    assert str(exc_info.value) == "Монитор 5 не найден"


def test_stores_do_not_share_state() -> None:
    first, second = MonitorRepository(), MonitorRepository()
    add(first)

    assert second.list_all() == []


def test_monitor_is_immutable() -> None:
    monitor = add(MonitorRepository())

    with pytest.raises(AttributeError):
        monitor.url = "https://other.test"  # type: ignore[misc]


def test_store_annotations_are_resolvable() -> None:
    # Метод с именем `list` затенял бы встроенный `list` в аннотациях класса:
    # get_type_hints падает с TypeError, а pyright этого не замечает.
    for name in ("list_all", "get", "create", "update", "delete"):
        get_type_hints(getattr(MonitorRepository, name))


def test_list_all_return_type_is_list_of_monitors() -> None:
    assert get_type_hints(MonitorRepository.list_all)["return"] == list[Monitor]


def test_store_has_no_method_shadowing_a_builtin() -> None:
    public = {name for name in vars(MonitorRepository) if not name.startswith("_")}

    assert not public & set(dir(builtins))


# --- поиск по URL ----------------------------------------------------------


def test_find_by_url_returns_matching_monitor() -> None:
    store = MonitorRepository()
    add(store, "https://a.test")
    target = add(store, "https://b.test")

    assert store.find_by_url(OWNER, "https://b.test") == target


def test_find_by_url_returns_none_when_absent() -> None:
    store = MonitorRepository()
    add(store, "https://a.test")

    assert store.find_by_url(OWNER, "https://other.test") is None


def test_find_by_url_is_exact_and_does_not_normalize() -> None:
    # Нормализация это дело схем API: репозиторий сравнивает строки как есть.
    store = MonitorRepository()
    add(store, "https://a.test")

    assert store.find_by_url(OWNER, "https://A.test") is None


def test_find_by_url_does_not_see_deleted_monitors() -> None:
    store = MonitorRepository()
    created = add(store, "https://a.test")
    store.delete(created.id)

    assert store.find_by_url(OWNER, "https://a.test") is None


def test_find_by_url_sees_updated_url() -> None:
    store = MonitorRepository()
    created = add(store, "https://a.test")
    store.update(created.id, url="https://b.test")

    assert store.find_by_url(OWNER, "https://a.test") is None
    assert store.find_by_url(OWNER, "https://b.test") is not None


def test_find_by_url_does_not_match_substrings() -> None:
    store = MonitorRepository()
    add(store, "https://a.test/path")

    assert store.find_by_url(OWNER, "https://a.test") is None
    assert store.find_by_url(OWNER, "https://a.test/path/more") is None


# --- владельцы -------------------------------------------------------------


def test_create_keeps_the_owner() -> None:
    assert add(MonitorRepository(), owner_id=7).owner_id == 7


def test_list_for_owner_returns_only_that_owners_monitors_in_order() -> None:
    store = MonitorRepository()
    mine = [
        add(store, "https://a.test", owner_id=1),
        add(store, "https://c.test", owner_id=1),
    ]
    add(store, "https://b.test", owner_id=2)

    assert store.list_for_owner(1) == mine


def test_list_for_owner_without_monitors_is_empty() -> None:
    store = MonitorRepository()
    add(store, owner_id=1)

    assert store.list_for_owner(2) == []


def test_list_all_ignores_owners() -> None:
    store = MonitorRepository()
    first, second = (
        add(store, "https://a.test", owner_id=1),
        add(store, "https://b.test", owner_id=2),
    )

    assert store.list_all() == [first, second]


def test_same_url_may_belong_to_different_owners() -> None:
    store = MonitorRepository()
    first = add(store, "https://a.test", owner_id=1)
    second = add(store, "https://a.test", owner_id=2)

    assert store.find_by_url(1, "https://a.test") == first
    assert store.find_by_url(2, "https://a.test") == second


def test_find_by_url_ignores_other_owners_monitors() -> None:
    store = MonitorRepository()
    add(store, "https://a.test", owner_id=1)

    assert store.find_by_url(2, "https://a.test") is None


def test_update_keeps_the_owner() -> None:
    store = MonitorRepository()
    created = add(store, owner_id=5)

    assert store.update(created.id, timeout=1.0).owner_id == 5
