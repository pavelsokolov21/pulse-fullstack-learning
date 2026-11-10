from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pulse.domain.errors import ConcurrentUpdateError, IncidentNotFoundError
from pulse.domain.incidents import Incident
from pulse.domain.monitors import Monitor
from pulse.repositories.database import Session
from pulse.repositories.incidents import IncidentRepository
from pulse.repositories.models import IncidentModel

UNKNOWN_ID = 2_000_000_000
NewIncident = Callable[..., Awaitable[int]]


@pytest.fixture
def repo(session: Session) -> IncidentRepository:
    return IncidentRepository(session)


async def test_get_returns_the_domain_incident(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)

    incident = await repo.get(incident_id)

    assert incident == Incident(
        id=incident_id,
        monitor_id=monitor.id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        resolved_at=None,
        failed_checks=3,
        last_error="Network Error",
        opened_by_check_id=None,
        resolved_by_check_id=None,
        version=1,
    )


async def test_get_unknown_incident_raises(repo: IncidentRepository) -> None:
    with pytest.raises(IncidentNotFoundError) as exc_info:
        await repo.get(UNKNOWN_ID)

    assert exc_info.value.incident_id == UNKNOWN_ID
    assert str(exc_info.value) == f"Инцидент {UNKNOWN_ID} не найден"


async def test_set_failed_checks_writes_the_value_as_is(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)

    await repo.set_failed_checks(incident_id, 10)

    assert (await repo.get(incident_id)).failed_checks == 10


async def test_set_failed_checks_overwrites_even_a_lower_value(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    # Безусловная запись: именно из-за неё возможно потерянное обновление.
    incident_id = await new_incident(monitor, failed_checks=9)

    await repo.set_failed_checks(incident_id, 4)

    assert (await repo.get(incident_id)).failed_checks == 4


async def test_set_failed_checks_changes_only_that_incident(
    session: Session,
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    first = await new_incident(monitor, failed_checks=3)
    second_id = await session.scalar(
        text(
            "INSERT INTO incidents (monitor_id, started_at, resolved_at, failed_checks)"
            " VALUES (:m, '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 7)"
            " RETURNING id"
        ),
        {"m": monitor.id},
    )
    assert second_id is not None

    await repo.set_failed_checks(first, 8)

    assert (await repo.get(second_id)).failed_checks == 7


async def test_set_failed_checks_of_unknown_incident_raises(
    repo: IncidentRepository,
) -> None:
    with pytest.raises(IncidentNotFoundError):
        await repo.set_failed_checks(UNKNOWN_ID, 5)


async def test_database_rejects_a_non_positive_counter(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor)

    with pytest.raises(IntegrityError, match="incidents_failed_checks_positive"):
        await repo.set_failed_checks(incident_id, 0)


async def test_commit_keeps_the_written_value_visible(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)
    await repo.set_failed_checks(incident_id, 4)

    await repo.commit()

    assert (await repo.get(incident_id)).failed_checks == 4


async def test_get_for_update_returns_the_same_incident_as_get(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)

    assert await repo.get_for_update(incident_id) == await repo.get(incident_id)


async def test_get_for_update_unknown_incident_raises(repo: IncidentRepository) -> None:
    with pytest.raises(IncidentNotFoundError) as exc_info:
        await repo.get_for_update(UNKNOWN_ID)

    assert exc_info.value.incident_id == UNKNOWN_ID


async def test_get_for_update_sees_a_value_changed_behind_the_session(
    session: Session,
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    # Значение в базе поменяли в обход ORM; ссылку на модель тест не держит.
    incident_id = await new_incident(monitor, failed_checks=3)
    await repo.get(incident_id)
    await session.execute(
        text("UPDATE incidents SET failed_checks = 8 WHERE id = :i"), {"i": incident_id}
    )

    assert (await repo.get_for_update(incident_id)).failed_checks == 8


async def test_set_if_version_writes_and_bumps_the_version(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)

    await repo.set_failed_checks_if_version(incident_id, 4, expected_version=1)

    incident = await repo.get(incident_id)
    assert (incident.failed_checks, incident.version) == (4, 2)


async def test_set_if_version_with_stale_version_raises_and_writes_nothing(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)
    await repo.set_failed_checks_if_version(incident_id, 4, expected_version=1)

    with pytest.raises(ConcurrentUpdateError) as exc_info:
        await repo.set_failed_checks_if_version(incident_id, 99, expected_version=1)

    assert exc_info.value.incident_id == incident_id
    incident = await repo.get(incident_id)
    assert (incident.failed_checks, incident.version) == (4, 2)


async def test_set_if_version_of_unknown_incident_is_not_a_conflict(
    repo: IncidentRepository,
) -> None:
    with pytest.raises(IncidentNotFoundError):
        await repo.set_failed_checks_if_version(UNKNOWN_ID, 2, expected_version=1)


async def test_set_if_version_changes_only_that_incident(
    session: Session,
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    first = await new_incident(monitor, failed_checks=3)
    second_id = await session.scalar(
        text(
            "INSERT INTO incidents (monitor_id, started_at, resolved_at, failed_checks)"
            " VALUES (:m, '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 7)"
            " RETURNING id"
        ),
        {"m": monitor.id},
    )
    assert second_id is not None

    await repo.set_failed_checks_if_version(first, 8, expected_version=1)

    other = await repo.get(second_id)
    assert (other.failed_checks, other.version) == (7, 1)


async def test_get_for_update_refreshes_an_object_the_session_still_holds(
    session: Session,
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    # Ссылка на модель удержана, поэтому она остаётся в identity map (иначе её
    # выбросил бы сборщик мусора и тест ничего не доказывал бы). Значение в базе
    # меняет «другая транзакция».
    incident_id = await new_incident(monitor, failed_checks=3)
    held = await session.get(IncidentModel, incident_id)
    assert held is not None
    await session.execute(
        text("UPDATE incidents SET failed_checks = 8 WHERE id = :i"), {"i": incident_id}
    )

    assert (await repo.get_for_update(incident_id)).failed_checks == 8


async def test_set_failed_checks_bumps_the_version(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)

    await repo.set_failed_checks(incident_id, 4)

    assert (await repo.get(incident_id)).version == 2


async def test_optimistic_write_conflicts_with_an_earlier_pessimistic_write(
    repo: IncidentRepository,
    monitor: Monitor,
    new_incident: NewIncident,
) -> None:
    incident_id = await new_incident(monitor, failed_checks=3)
    seen = (await repo.get(incident_id)).version
    # Чужая запись «по-пессимистичному»: версия должна измениться и здесь.
    await repo.set_failed_checks(incident_id, 4)

    with pytest.raises(ConcurrentUpdateError):
        await repo.set_failed_checks_if_version(incident_id, 99, expected_version=seen)

    assert (await repo.get(incident_id)).failed_checks == 4
