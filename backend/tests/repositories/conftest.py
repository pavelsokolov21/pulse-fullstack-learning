from datetime import timedelta

import pytest

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
