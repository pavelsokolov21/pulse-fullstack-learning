from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import text

from pulse.domain.monitors import Monitor
from pulse.domain.users import Role, User
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.users import UserRepository


async def make_user(session: Session, email: str, role: Role = Role.MANAGER) -> User:
    return await UserRepository(session).create(
        email=email, password_hash="h", role=role
    )


@pytest.fixture
async def owner(session: Session) -> User:
    """Владелец мониторов: внешний ключ требует настоящую строку в users."""
    return await make_user(session, "owner@example.com")


@pytest.fixture
async def other_owner(session: Session) -> User:
    return await make_user(session, "other@example.com")


@pytest.fixture
async def monitor(session: Session, owner: User) -> Monitor:
    """Монитор, к которому привязываются проверки (внешний ключ checks.monitor_id)."""
    return await make_monitor(session, owner)


@pytest.fixture
async def other_monitor(session: Session, owner: User) -> Monitor:
    return await make_monitor(session, owner, "https://other.test")


async def make_monitor(
    session: Session, owner: User, url: str = "https://a.test"
) -> Monitor:
    return await MonitorRepository(session).create(
        owner_id=owner.id, url=url, interval=timedelta(minutes=5), timeout=10.0
    )


@pytest.fixture
def new_incident(session: Session) -> Callable[..., Awaitable[int]]:
    """Создаёт открытый инцидент монитора и возвращает его id.

    Строка нужна напрямую: репозиторий инциденты пока не создаёт.
    """

    async def create(monitor: Monitor, failed_checks: int = 3) -> int:
        incident_id = await session.scalar(
            text(
                "INSERT INTO incidents (monitor_id, started_at, failed_checks,"
                " last_error) VALUES (:monitor, '2026-01-01T00:00:00Z', :failed,"
                " 'Network Error') RETURNING id"
            ),
            {"monitor": monitor.id, "failed": failed_checks},
        )
        assert incident_id is not None
        return incident_id

    return create
