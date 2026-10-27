import pytest

from pulse.monitors.errors import MonitorNotFoundError
from pulse.monitors.models import MonitorCreate, MonitorUpdate
from pulse.monitors.store import MonitorStore


def make(url: str = "a.test", **overrides: object) -> MonitorCreate:
    return MonitorCreate.model_validate({"url": url, "interval": "5m", **overrides})


def test_new_store_is_empty() -> None:
    assert MonitorStore().list() == []


def test_create_assigns_increasing_ids_from_one() -> None:
    store = MonitorStore()

    ids = [store.create(make(f"{n}.test")).id for n in range(3)]

    assert ids == [1, 2, 3]


def test_create_copies_validated_fields() -> None:
    monitor = MonitorStore().create(make("A.test", timeout=3))

    assert (monitor.url, monitor.timeout) == ("https://a.test", 3.0)


def test_get_returns_what_was_created() -> None:
    store = MonitorStore()
    created = store.create(make())

    assert store.get(created.id) == created


def test_list_keeps_creation_order() -> None:
    store = MonitorStore()
    created = [store.create(make(f"{n}.test")) for n in range(3)]

    assert store.list() == created


def test_list_returns_a_copy() -> None:
    store = MonitorStore()
    store.create(make())

    store.list().clear()

    assert len(store.list()) == 1


def test_update_applies_only_set_fields() -> None:
    store = MonitorStore()
    created = store.create(make(timeout=3))

    updated = store.update(created.id, MonitorUpdate(timeout=9))

    assert updated == created.model_copy(update={"timeout": 9.0})
    assert store.get(created.id) == updated


def test_update_with_no_fields_keeps_monitor() -> None:
    store = MonitorStore()
    created = store.create(make())

    assert store.update(created.id, MonitorUpdate()) == created


def test_delete_removes_only_that_monitor() -> None:
    store = MonitorStore()
    first, second = store.create(make("a.test")), store.create(make("b.test"))

    store.delete(first.id)

    assert store.list() == [second]


def test_ids_are_not_reused_after_delete() -> None:
    store = MonitorStore()
    first = store.create(make())
    store.delete(first.id)

    assert store.create(make()).id == first.id + 1


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_unknown_id_raises_monitor_not_found(operation: str) -> None:
    store = MonitorStore()
    calls = {
        "get": lambda: store.get(5),
        "update": lambda: store.update(5, MonitorUpdate(timeout=1)),
        "delete": lambda: store.delete(5),
    }

    with pytest.raises(MonitorNotFoundError) as exc_info:
        calls[operation]()

    assert exc_info.value.monitor_id == 5
    assert str(exc_info.value) == "Монитор 5 не найден"


def test_stores_do_not_share_state() -> None:
    first, second = MonitorStore(), MonitorStore()
    first.create(make())

    assert second.list() == []
