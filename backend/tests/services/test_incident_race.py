"""Потерянное обновление счётчика `incidents.failed_checks` (неделя 5, фаза 2).

Гонка здесь воспроизводится намеренно: `IncidentService.register_failed_check` читает,
прибавляет и записывает без блокировки. Тест описывает ПРАВИЛЬНОЕ поведение (два
вызова дают +2) и помечен `xfail(strict=True)`: он «падает как ожидалось», пока гонка не
исправлена. Исправили (`SELECT ... FOR UPDATE` или версия строки): тест начнёт
проходить, strict=True превратит это в ошибку, и останется убрать маркер `xfail`.

Нужны настоящие коммиты и два соединения, поэтому откатываемая сессия из
`conftest.py` не подходит: данные создаются и стираются самим тестом.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.repositories.database import SessionFactory, create_session_factory
from pulse.repositories.incidents import IncidentRepository
from pulse.services.incidents import IncidentService

Db = psycopg.Connection[tuple[object, ...]]
INITIAL = 3
BARRIER_TIMEOUT = 2.0  # секунды: см. rendezvous

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


def rendezvous(barrier: asyncio.Barrier) -> Callable[[], Awaitable[None]]:
    """Хук `before_write`: ждёт второго участника, но не вечно.

    Пока гонка есть, оба вызова доходят до барьера, прочитав одно значение, и
    проходят его сразу. Ограничение по времени нужно на случай исправления: при
    `SELECT ... FOR UPDATE` второй вызов ждёт блокировки на чтении и до барьера не
    доходит, а первый ждал бы его вечно (взаимная блокировка). Тогда по истечении
    времени первый пишет и коммитит, второй получает блокировку и читает уже новое
    значение. Сломанный барьер второго больше не задерживает.
    """

    async def wait() -> None:
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=BARRIER_TIMEOUT)

    return wait


async def register_in_own_session(
    factory: SessionFactory,
    incident_id: int,
    barrier: asyncio.Barrier,
) -> int:
    """Один «запрос»: своя сессия, своё соединение, настоящий коммит в сервисе."""
    async with factory() as session:
        service = IncidentService(
            IncidentRepository(session), before_write=rendezvous(barrier)
        )
        return await service.register_failed_check(incident_id)


@pytest.mark.xfail(strict=True, reason="lost update: чинится в неделе 5")
async def test_two_parallel_registrations_add_two(
    factory: SessionFactory, admin_db: Db
) -> None:
    incident_id = open_incident(admin_db, INITIAL)
    # Барьер на двоих: оба вызова прочитали INITIAL, и только потом любой из них пишет.
    barrier = asyncio.Barrier(2)

    async with asyncio.timeout(15):
        await asyncio.gather(
            register_in_own_session(factory, incident_id, barrier),
            register_in_own_session(factory, incident_id, barrier),
        )

    row = admin_db.execute(
        "SELECT failed_checks FROM incidents WHERE id = %s", (incident_id,)
    ).fetchone()
    assert row is not None
    final = row[0]
    assert final == INITIAL + 2, (
        f"потеряно обновление: счётчик был {INITIAL}, два вызова, ожидалось "
        f"{INITIAL + 2}, в базе {final}"
    )
