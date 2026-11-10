from datetime import timedelta

import pytest
from sqlalchemy import text

from pulse.domain.errors import IncidentNotFoundError
from pulse.domain.monitors import Monitor
from pulse.domain.users import Role
from pulse.repositories.database import Session
from pulse.repositories.incidents import IncidentRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.users import UserRepository
from pulse.services.incidents import IncidentService

UNKNOWN_ID = 2_000_000_000


@pytest.fixture
async def monitor(session: Session) -> Monitor:
    owner = await UserRepository(session).create(
        email="owner@example.com", password_hash="h", role=Role.MANAGER
    )
    return await MonitorRepository(session).create(
        owner_id=owner.id,
        url="https://a.test",
        interval=timedelta(minutes=5),
        timeout=5,
    )


@pytest.fixture
async def incident_id(session: Session, monitor: Monitor) -> int:
    created = await session.scalar(
        text(
            "INSERT INTO incidents (monitor_id, started_at, failed_checks)"
            " VALUES (:m, '2026-01-01T00:00:00Z', 3) RETURNING id"
        ),
        {"m": monitor.id},
    )
    assert created is not None
    return created


@pytest.fixture
def repository(session: Session) -> IncidentRepository:
    return IncidentRepository(session)


async def test_register_increments_the_counter_and_returns_it(
    repository: IncidentRepository, incident_id: int
) -> None:
    service = IncidentService(repository)

    assert await service.register_failed_check(incident_id) == 4
    assert (await repository.get(incident_id)).failed_checks == 4


async def test_sequential_calls_do_not_lose_updates(
    repository: IncidentRepository, incident_id: int
) -> None:
    # Без конкуренции «прочитать, прибавить, записать» работает верно: гонка
    # возможна только при параллельных вызовах (см. test_incident_race.py).
    service = IncidentService(repository)

    for _ in range(5):
        await service.register_failed_check(incident_id)

    assert (await repository.get(incident_id)).failed_checks == 8


async def test_before_write_runs_after_the_read_and_before_the_write(
    repository: IncidentRepository, incident_id: int
) -> None:
    seen: list[int] = []

    async def spy() -> None:
        seen.append((await repository.get(incident_id)).failed_checks)

    await IncidentService(repository, before_write=spy).register_failed_check(
        incident_id
    )

    # В момент хука запись ещё не сделана: в базе прежнее значение.
    assert seen == [3]
    assert (await repository.get(incident_id)).failed_checks == 4


async def test_before_write_is_optional(
    repository: IncidentRepository, incident_id: int
) -> None:
    assert (
        await IncidentService(repository, before_write=None).register_failed_check(
            incident_id
        )
        == 4
    )


async def test_unknown_incident_raises_and_skips_the_hook(
    repository: IncidentRepository,
) -> None:
    calls: list[str] = []

    async def hook() -> None:
        calls.append("called")

    service = IncidentService(repository, before_write=hook)

    with pytest.raises(IncidentNotFoundError):
        await service.register_failed_check(UNKNOWN_ID)

    assert calls == []


async def test_a_failing_hook_leaves_the_counter_untouched(
    repository: IncidentRepository, incident_id: int
) -> None:
    async def broken() -> None:
        raise RuntimeError("прервали между чтением и записью")

    with pytest.raises(RuntimeError):
        await IncidentService(repository, before_write=broken).register_failed_check(
            incident_id
        )

    assert (await repository.get(incident_id)).failed_checks == 3
