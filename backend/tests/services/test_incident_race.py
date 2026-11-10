"""Параллельные обновления счётчика `incidents.failed_checks` (неделя 5, фаза 2).

Раньше `register_failed_check` читал, прибавлял и писал без блокировки, и два
параллельных вызова теряли один прирост (lost update). Теперь чтение берёт
`SELECT ... FOR UPDATE`: второй вызов ждёт, пока первый не сделает `commit()`.

Второй способ защиты это версия строки (`register_failed_check_optimistic`): запись
идёт `WHERE version = <прочитанная>`, проигравший получает `ConcurrentUpdateError`, а
повтор делает вызывающий код (`retry_on_conflict`). Его тесты ниже, после блокировки.

Тесты блокировки доказывают это тремя способами: итог `initial + 2`, разные возвращённые
значения и настоящее ожидание блокировки в `pg_stat_activity` (а не просто «пришёл
позже»). Нужны настоящие коммиты и два соединения, поэтому откатываемая сессия из
`conftest.py` не подходит: данные создаются и стираются самим тестом.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.errors import ConcurrentUpdateError
from pulse.repositories.database import SessionFactory, create_session_factory
from pulse.repositories.incidents import IncidentRepository
from pulse.services.incidents import IncidentService
from pulse.services.retry import retry_on_conflict

Db = psycopg.Connection[tuple[object, ...]]
INITIAL = 3
HOLD = 0.5  # секунды: так долго первый вызов держит блокировку между чтением и записью

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
def factory(engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(engine)


def open_incident(db: Db, failed_checks: int) -> int:
    """Пользователь, монитор и открытый инцидент: настоящие, закоммиченные строки."""
    owner = db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('owner@example.com', 'h', 'manager') RETURNING id"
    ).fetchone()
    assert owner is not None
    monitor = db.execute(
        "INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)"
        " VALUES (%s, 'https://a.test', 300, 5) RETURNING id",
        (owner[0],),
    ).fetchone()
    assert monitor is not None
    incident = db.execute(
        "INSERT INTO incidents (monitor_id, started_at, failed_checks)"
        " VALUES (%s, now() - interval '1 hour', %s) RETURNING id",
        (monitor[0], failed_checks),
    ).fetchone()
    assert incident is not None
    return int(incident[0])  # pyright: ignore[reportArgumentType]


def hold_lock() -> Callable[[], Awaitable[None]]:
    """Хук `before_write`: пауза с удерживаемой блокировкой строки."""

    async def wait() -> None:
        await asyncio.sleep(HOLD)

    return wait


def fail_after_hold() -> Callable[[], Awaitable[None]]:
    async def fail() -> None:
        await asyncio.sleep(HOLD)
        raise RuntimeError("сбой между чтением и записью")

    return fail


async def register_in_own_session(
    factory: SessionFactory,
    incident_id: int,
    before_write: Callable[[], Awaitable[None]],
    *,
    optimistic: bool = False,
) -> int:
    """Один «запрос»: своя сессия, своё соединение, настоящий коммит в сервисе."""
    async with factory() as session:
        service = IncidentService(
            IncidentRepository(session), before_write=before_write
        )
        if optimistic:
            return await service.register_failed_check_optimistic(incident_id)
        return await service.register_failed_check(incident_id)


def lock_waiters(db: Db) -> int:
    """Сколько запросов `FOR UPDATE` сейчас стоят в очереди за блокировкой строки."""
    row = db.execute(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
        " AND query ILIKE '%FOR UPDATE%'"
    ).fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


async def observe_waiter(db: Db) -> bool:
    """Ждёт, пока в базе не появится запрос, ожидающий блокировку (или 5 секунд)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if await asyncio.to_thread(lock_waiters, db) > 0:
            return True
        await asyncio.sleep(0.02)
    return False


def failed_checks_of(db: Db, incident_id: int) -> int:
    row = db.execute(
        "SELECT failed_checks FROM incidents WHERE id = %s", (incident_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


async def test_two_parallel_registrations_add_two(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)

    async with asyncio.timeout(15):
        started = time.monotonic()
        results = await asyncio.gather(
            register_in_own_session(factory, incident_id, hold_lock()),
            register_in_own_session(factory, incident_id, hold_lock()),
        )
        elapsed = time.monotonic() - started

    # Итог: оба прироста на месте.
    assert failed_checks_of(admin_db, incident_id) == INITIAL + 2
    # Второй вызов прочитал результат первого, поэтому значения разные.
    assert sorted(results) == [INITIAL + 1, INITIAL + 2]
    # Вызовы шли друг за другом: каждый держит блокировку не меньше HOLD.
    assert elapsed >= 2 * HOLD


async def test_second_call_waits_for_the_row_lock(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)

    async with asyncio.timeout(15):
        calls = asyncio.gather(
            register_in_own_session(factory, incident_id, hold_lock()),
            register_in_own_session(factory, incident_id, hold_lock()),
        )
        # Пока первый спит с блокировкой, второй обязан стоять в очереди в самой базе
        # (admin_db в это время больше нигде не используется).
        saw_waiter = await observe_waiter(admin_db)
        await calls

    assert saw_waiter, "в pg_stat_activity не нашлось запроса, ждущего блокировку"


async def test_failed_call_releases_the_lock(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)

    async with asyncio.timeout(15):
        results = await asyncio.gather(
            register_in_own_session(factory, incident_id, fail_after_hold()),
            register_in_own_session(factory, incident_id, hold_lock()),
            return_exceptions=True,
        )

    errors = [r for r in results if isinstance(r, RuntimeError)]
    values = [r for r in results if isinstance(r, int)]
    # Упавший вызов откатился и не оставил блокировку: второй дошёл до конца.
    assert len(errors) == 1
    assert values == [INITIAL + 1]
    assert failed_checks_of(admin_db, incident_id) == INITIAL + 1


def version_of(db: Db, incident_id: int) -> int:
    row = db.execute(
        "SELECT version FROM incidents WHERE id = %s", (incident_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


def meet_once(barrier: asyncio.Barrier) -> Callable[[], Awaitable[None]]:
    """Хук `before_write`: встреча на барьере только при первом вызове.

    Нужна, чтобы оба участника гарантированно прочитали версию до чьей-либо записи.
    Повторная попытка того, кто проиграл, барьер уже не ждёт: иначе она зависла бы,
    ведь второго участника там больше нет.
    """
    met = False

    async def wait() -> None:
        nonlocal met
        if not met:
            met = True
            await barrier.wait()

    return wait


async def test_optimistic_without_retry_reports_the_conflict_instead_of_losing_data(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)
    barrier = asyncio.Barrier(2)

    async with asyncio.timeout(15):
        results = await asyncio.gather(
            register_in_own_session(
                factory, incident_id, meet_once(barrier), optimistic=True
            ),
            register_in_own_session(
                factory, incident_id, meet_once(barrier), optimistic=True
            ),
            return_exceptions=True,
        )

    conflicts = [r for r in results if isinstance(r, ConcurrentUpdateError)]
    values = [r for r in results if isinstance(r, int)]
    # Победил один; проигравший получил ошибку, а не тихо затёр чужую запись.
    assert len(conflicts) == 1
    assert values == [INITIAL + 1]
    assert failed_checks_of(admin_db, incident_id) == INITIAL + 1
    assert version_of(admin_db, incident_id) == 2


async def test_optimistic_with_retry_in_the_caller_adds_two(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)
    barrier = asyncio.Barrier(2)

    async def request() -> int:
        # Одна «попытка» открывает свою сессию внутри register_in_own_session.
        hook = meet_once(barrier)
        return await retry_on_conflict(
            lambda: register_in_own_session(
                factory, incident_id, hook, optimistic=True
            ),
            base_delay=0.01,
        )

    async with asyncio.timeout(15):
        results = await asyncio.gather(request(), request())

    assert failed_checks_of(admin_db, incident_id) == INITIAL + 2
    assert sorted(results) == [INITIAL + 1, INITIAL + 2]
    assert version_of(admin_db, incident_id) == 3
