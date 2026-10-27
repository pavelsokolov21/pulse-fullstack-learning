import builtins
from datetime import timedelta
from typing import get_type_hints

import pytest

from pulse.monitors.errors import MonitorNotFoundError
from pulse.monitors.models import Monitor
from pulse.monitors.store import MonitorStore

FIVE_MINUTES = timedelta(minutes=5)


def add(
    store: MonitorStore, url: str = "https://a.test", timeout: float = 10.0
) -> Monitor:
    return store.create(url=url, interval=FIVE_MINUTES, timeout=timeout)


def test_new_store_is_empty() -> None:
    assert MonitorStore().list_all() == []


def test_create_assigns_increasing_ids_from_one() -> None:
    store = MonitorStore()

    ids = [add(store, f"https://{n}.test").id for n in range(3)]

    assert ids == [1, 2, 3]


def test_create_keeps_given_values() -> None:
    monitor = add(MonitorStore(), "https://a.test", timeout=3.0)

    assert monitor == Monitor(1, "https://a.test", FIVE_MINUTES, 3.0)


def test_get_returns_what_was_created() -> None:
    store = MonitorStore()
    created = add(store)

    assert store.get(created.id) == created


def test_list_keeps_creation_order() -> None:
    store = MonitorStore()
    created = [add(store, f"https://{n}.test") for n in range(3)]

    assert store.list_all() == created


def test_list_returns_a_copy() -> None:
    store = MonitorStore()
    add(store)

    store.list_all().clear()

    assert len(store.list_all()) == 1


def test_update_applies_only_given_fields() -> None:
    store = MonitorStore()
    created = add(store, timeout=3.0)

    updated = store.update(created.id, timeout=9.0)

    assert updated == Monitor(created.id, created.url, FIVE_MINUTES, 9.0)
    assert store.get(created.id) == updated


def test_update_can_change_every_field_at_once() -> None:
    store = MonitorStore()
    created = add(store)

    updated = store.update(
        created.id, url="https://b.test", interval=timedelta(hours=1), timeout=1.0
    )

    assert updated == Monitor(created.id, "https://b.test", timedelta(hours=1), 1.0)


def test_update_without_fields_keeps_monitor() -> None:
    store = MonitorStore()
    created = add(store)

    assert store.update(created.id) == created


def test_update_does_not_touch_other_monitors() -> None:
    store = MonitorStore()
    first, second = add(store, "https://a.test"), add(store, "https://b.test")

    store.update(first.id, url="https://changed.test")

    assert store.get(second.id) == second


def test_delete_removes_only_that_monitor() -> None:
    store = MonitorStore()
    first, second = add(store, "https://a.test"), add(store, "https://b.test")

    store.delete(first.id)

    assert store.list_all() == [second]


def test_ids_are_not_reused_after_delete() -> None:
    store = MonitorStore()
    first = add(store)
    store.delete(first.id)

    assert add(store).id == first.id + 1


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_unknown_id_raises_monitor_not_found(operation: str) -> None:
    store = MonitorStore()
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
    first, second = MonitorStore(), MonitorStore()
    add(first)

    assert second.list_all() == []


def test_monitor_is_immutable() -> None:
    monitor = add(MonitorStore())

    with pytest.raises(AttributeError):
        monitor.url = "https://other.test"  # type: ignore[misc]


def test_store_annotations_are_resolvable() -> None:
    # Метод с именем `list` затенял бы встроенный `list` в аннотациях класса:
    # get_type_hints падает с TypeError, а pyright этого не замечает.
    for name in ("list_all", "get", "create", "update", "delete"):
        get_type_hints(getattr(MonitorStore, name))


def test_list_all_return_type_is_list_of_monitors() -> None:
    assert get_type_hints(MonitorStore.list_all)["return"] == list[Monitor]


def test_store_has_no_method_shadowing_a_builtin() -> None:
    public = {name for name in vars(MonitorStore) if not name.startswith("_")}

    assert not public & set(dir(builtins))
