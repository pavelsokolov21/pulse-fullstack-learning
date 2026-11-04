import builtins
from datetime import timedelta
from typing import get_type_hints

import pytest
from sqlalchemy.exc import IntegrityError

from pulse.domain.errors import DuplicateMonitorError, MonitorNotFoundError
from pulse.domain.monitors import Monitor, MonitorWithOwner
from pulse.domain.pagination import Page
from pulse.domain.users import User
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository

FIVE_MINUTES = timedelta(minutes=5)


@pytest.fixture
def store(session: Session) -> MonitorRepository:
    return MonitorRepository(session)


def plain(monitor: MonitorWithOwner) -> Monitor:
    return Monitor(
        monitor.id, monitor.owner_id, monitor.url, monitor.interval, monitor.timeout
    )


async def everyone(store: MonitorRepository) -> list[Monitor]:
    page = await store.list_page(owner_id=None, limit=1000, offset=0)
    return [plain(m) for m in page.items]


async def of_owner(store: MonitorRepository, owner_id: int) -> list[Monitor]:
    page = await store.list_page(owner_id=owner_id, limit=1000, offset=0)
    return [plain(m) for m in page.items]


async def add(
    store: MonitorRepository,
    owner: User,
    url: str = "https://a.test",
    timeout: float = 10.0,
) -> Monitor:
    return await store.create(
        owner_id=owner.id, url=url, interval=FIVE_MINUTES, timeout=timeout
    )


async def test_new_store_is_empty(store: MonitorRepository) -> None:
    assert await everyone(store) == []


async def test_create_assigns_increasing_ids(
    store: MonitorRepository, owner: User
) -> None:
    ids = [(await add(store, owner, f"https://{n}.test")).id for n in range(3)]

    assert ids == sorted(set(ids))


async def test_create_keeps_given_values(store: MonitorRepository, owner: User) -> None:
    monitor = await add(store, owner, "https://a.test", timeout=3.0)

    assert monitor == Monitor(monitor.id, owner.id, "https://a.test", FIVE_MINUTES, 3.0)


async def test_get_returns_what_was_created(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner)

    assert await store.get(created.id) == created


async def test_list_keeps_creation_order(store: MonitorRepository, owner: User) -> None:
    created = [await add(store, owner, f"https://{n}.test") for n in range(3)]

    assert await everyone(store) == created


async def test_update_applies_only_given_fields(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner, timeout=3.0)

    updated = await store.update(created.id, timeout=9.0)

    assert updated == Monitor(created.id, owner.id, created.url, FIVE_MINUTES, 9.0)
    assert await store.get(created.id) == updated


async def test_update_can_change_every_field_at_once(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner)

    updated = await store.update(
        created.id, url="https://b.test", interval=timedelta(hours=1), timeout=1.0
    )

    assert updated == Monitor(
        created.id, owner.id, "https://b.test", timedelta(hours=1), 1.0
    )
    assert await store.get(created.id) == updated


async def test_update_without_fields_keeps_monitor(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner)

    assert await store.update(created.id) == created


async def test_update_does_not_touch_other_monitors(
    store: MonitorRepository, owner: User
) -> None:
    first = await add(store, owner, "https://a.test")
    second = await add(store, owner, "https://b.test")

    await store.update(first.id, url="https://changed.test")

    assert await store.get(second.id) == second


async def test_delete_removes_only_that_monitor(
    store: MonitorRepository, owner: User
) -> None:
    first = await add(store, owner, "https://a.test")
    second = await add(store, owner, "https://b.test")

    await store.delete(first.id)

    assert await everyone(store) == [second]


async def test_ids_are_not_reused_after_delete(
    store: MonitorRepository, owner: User
) -> None:
    first = await add(store, owner)
    await store.delete(first.id)

    assert (await add(store, owner)).id > first.id


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
async def test_unknown_id_raises_monitor_not_found(
    store: MonitorRepository, operation: str
) -> None:
    calls = {
        "get": lambda: store.get(5),
        "update": lambda: store.update(5, timeout=1.0),
        "delete": lambda: store.delete(5),
    }

    with pytest.raises(MonitorNotFoundError) as exc_info:
        await calls[operation]()

    assert exc_info.value.monitor_id == 5
    assert str(exc_info.value) == "Монитор 5 не найден"


async def test_repositories_over_one_session_share_data(
    session: Session, store: MonitorRepository, owner: User
) -> None:
    # Репозиторий не хранит данные у себя: это окно в базу через сессию запроса.
    await add(store, owner)

    assert len(await everyone(MonitorRepository(session))) == 1


async def test_monitor_is_immutable(store: MonitorRepository, owner: User) -> None:
    monitor = await add(store, owner)

    with pytest.raises(AttributeError):
        monitor.url = "https://other.test"  # type: ignore[misc]


def test_store_annotations_are_resolvable() -> None:
    # Метод с именем `list` затенял бы встроенный `list` в аннотациях класса:
    # get_type_hints падает с TypeError, а pyright этого не замечает.
    for name in ("list_page", "get", "create", "update", "delete"):
        get_type_hints(getattr(MonitorRepository, name))


def test_list_page_return_type_is_a_page_of_monitors_with_owner() -> None:
    assert (
        get_type_hints(MonitorRepository.list_page)["return"] == Page[MonitorWithOwner]
    )


def test_store_has_no_method_shadowing_a_builtin() -> None:
    public = {name for name in vars(MonitorRepository) if not name.startswith("_")}

    assert not public & set(dir(builtins))


# --- поиск по URL ----------------------------------------------------------


async def test_find_by_url_returns_matching_monitor(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner, "https://a.test")
    target = await add(store, owner, "https://b.test")

    assert await store.find_by_url(owner.id, "https://b.test") == target


async def test_find_by_url_returns_none_when_absent(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner, "https://a.test")

    assert await store.find_by_url(owner.id, "https://other.test") is None


async def test_find_by_url_is_exact_and_does_not_normalize(
    store: MonitorRepository, owner: User
) -> None:
    # Нормализация это дело схем API: репозиторий сравнивает строки как есть.
    await add(store, owner, "https://a.test")

    assert await store.find_by_url(owner.id, "https://A.test") is None


async def test_find_by_url_does_not_see_deleted_monitors(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner, "https://a.test")
    await store.delete(created.id)

    assert await store.find_by_url(owner.id, "https://a.test") is None


async def test_find_by_url_sees_updated_url(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner, "https://a.test")
    await store.update(created.id, url="https://b.test")

    assert await store.find_by_url(owner.id, "https://a.test") is None
    assert await store.find_by_url(owner.id, "https://b.test") is not None


async def test_find_by_url_does_not_match_substrings(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner, "https://a.test/path")

    assert await store.find_by_url(owner.id, "https://a.test") is None
    assert await store.find_by_url(owner.id, "https://a.test/path/more") is None


async def test_find_by_url_treats_like_wildcards_literally(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner, "https://a.test/p_th")

    assert await store.find_by_url(owner.id, "https://a.test/path") is None


# --- владельцы -------------------------------------------------------------


async def test_create_keeps_the_owner(store: MonitorRepository, owner: User) -> None:
    assert (await add(store, owner)).owner_id == owner.id


async def test_list_for_owner_returns_only_that_owners_monitors_in_order(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    mine = [
        await add(store, owner, "https://a.test"),
        await add(store, owner, "https://c.test"),
    ]
    await add(store, other_owner, "https://b.test")

    assert await of_owner(store, owner.id) == mine


async def test_list_for_owner_without_monitors_is_empty(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    await add(store, owner)

    assert await of_owner(store, other_owner.id) == []


async def test_list_all_ignores_owners(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    first = await add(store, owner, "https://a.test")
    second = await add(store, other_owner, "https://b.test")

    assert await everyone(store) == [first, second]


async def test_same_url_may_belong_to_different_owners(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    first = await add(store, owner, "https://a.test")
    second = await add(store, other_owner, "https://a.test")

    assert await store.find_by_url(owner.id, "https://a.test") == first
    assert await store.find_by_url(other_owner.id, "https://a.test") == second


async def test_find_by_url_ignores_other_owners_monitors(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    await add(store, owner, "https://a.test")

    assert await store.find_by_url(other_owner.id, "https://a.test") is None


async def test_update_keeps_the_owner(store: MonitorRepository, owner: User) -> None:
    created = await add(store, owner)

    assert (await store.update(created.id, timeout=1.0)).owner_id == owner.id


# --- страницы --------------------------------------------------------------


async def test_page_carries_the_owner_email(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner)

    page = await store.list_page(owner_id=None, limit=10, offset=0)

    assert page.items == [
        MonitorWithOwner(
            created.id,
            owner.id,
            created.url,
            created.interval,
            created.timeout,
            owner.email,
        )
    ]


async def test_page_shows_each_monitor_with_its_own_owners_email(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    await add(store, owner, "https://a.test")
    await add(store, other_owner, "https://b.test")

    page = await store.list_page(owner_id=None, limit=10, offset=0)

    assert [(m.url, m.owner_email) for m in page.items] == [
        ("https://a.test", owner.email),
        ("https://b.test", other_owner.email),
    ]


async def test_page_applies_limit_and_offset_in_id_order(
    store: MonitorRepository, owner: User
) -> None:
    created = [await add(store, owner, f"https://{n}.test") for n in range(5)]

    first = await store.list_page(owner_id=None, limit=2, offset=0)
    second = await store.list_page(owner_id=None, limit=2, offset=2)
    last = await store.list_page(owner_id=None, limit=2, offset=4)

    assert [m.id for m in first.items] == [created[0].id, created[1].id]
    assert [m.id for m in second.items] == [created[2].id, created[3].id]
    assert [m.id for m in last.items] == [created[4].id]


async def test_total_counts_all_matching_rows_not_just_the_page(
    store: MonitorRepository, owner: User
) -> None:
    for n in range(5):
        await add(store, owner, f"https://{n}.test")

    page = await store.list_page(owner_id=None, limit=2, offset=0)

    assert (len(page.items), page.total) == (2, 5)


async def test_total_is_the_same_on_every_page(
    store: MonitorRepository, owner: User
) -> None:
    for n in range(5):
        await add(store, owner, f"https://{n}.test")

    totals = [
        (await store.list_page(owner_id=None, limit=2, offset=offset)).total
        for offset in (0, 2, 4, 10)
    ]

    assert totals == [5, 5, 5, 5]


async def test_offset_beyond_the_end_gives_an_empty_page_with_the_total(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner)

    page = await store.list_page(owner_id=None, limit=10, offset=100)

    assert (page.items, page.total) == ([], 1)


async def test_total_of_an_owner_ignores_other_owners(
    store: MonitorRepository, owner: User, other_owner: User
) -> None:
    await add(store, owner, "https://a.test")
    await add(store, owner, "https://b.test")
    await add(store, other_owner, "https://c.test")

    page = await store.list_page(owner_id=owner.id, limit=1, offset=0)

    assert (len(page.items), page.total) == (1, 2)


async def test_empty_table_gives_an_empty_page_with_zero_total(
    store: MonitorRepository,
) -> None:
    page = await store.list_page(owner_id=None, limit=10, offset=0)

    assert (page.items, page.total) == ([], 0)


# --- ограничения базы ------------------------------------------------------


async def test_create_duplicate_url_of_same_owner_raises_with_existing_id(
    store: MonitorRepository, owner: User
) -> None:
    existing = await add(store, owner, "https://a.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        await add(store, owner, "https://a.test")

    assert exc_info.value.existing_id == existing.id
    assert exc_info.value.url == "https://a.test"


async def test_update_to_taken_url_raises_with_the_holders_id(
    store: MonitorRepository, owner: User
) -> None:
    holder = await add(store, owner, "https://a.test")
    other = await add(store, owner, "https://b.test")

    with pytest.raises(DuplicateMonitorError) as exc_info:
        await store.update(other.id, url="https://a.test")

    assert exc_info.value.existing_id == holder.id


async def test_update_to_own_url_is_not_a_duplicate(
    store: MonitorRepository, owner: User
) -> None:
    created = await add(store, owner, "https://a.test")

    assert await store.update(created.id, url="https://a.test") == created


async def test_failed_update_leaves_the_monitor_and_session_intact(
    store: MonitorRepository, owner: User
) -> None:
    await add(store, owner, "https://a.test")
    other = await add(store, owner, "https://b.test")

    with pytest.raises(DuplicateMonitorError):
        await store.update(other.id, url="https://a.test", timeout=1.0)

    assert await store.get(other.id) == other


async def test_unknown_owner_is_rejected_by_foreign_key(
    store: MonitorRepository,
) -> None:
    with pytest.raises(IntegrityError, match="monitors_owner_id_fkey"):
        await store.create(
            owner_id=999_999, url="https://a.test", interval=FIVE_MINUTES, timeout=1.0
        )


async def test_database_rejects_timeout_not_below_interval(
    store: MonitorRepository, owner: User
) -> None:
    # Страховка схемы: сервис проверяет это раньше, но БД не доверяет коду.
    with pytest.raises(IntegrityError, match="monitors_timeout_below_interval"):
        await store.create(
            owner_id=owner.id,
            url="https://a.test",
            interval=timedelta(seconds=30),
            timeout=30.0,
        )
