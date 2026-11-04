"""Гонки между параллельными запросами на настоящих соединениях.

Каждая «заявка» получает свою сессию и коммитит сама, как отдельный HTTP-запрос.
Откатываемая сессия из `conftest.py` тут не годится: она одна на все задачи. Данные
настоящие, поэтому `clean_database` стирает их до и после теста.
"""

import asyncio
from datetime import timedelta

import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.core.passwords import PasswordHasher
from pulse.domain.errors import (
    DuplicateMonitorError,
    DuplicateUserError,
    InvalidRefreshTokenError,
)
from pulse.domain.refresh_tokens import RefreshToken
from pulse.domain.users import User
from pulse.repositories.database import (
    Session,
    SessionFactory,
    create_session_factory,
)
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.refresh_tokens import RefreshTokenRepository
from pulse.repositories.users import UserRepository
from pulse.services.auth import AuthService
from pulse.services.monitors import MonitorService
from pulse.services.refresh_tokens import RefreshTokenService
from pulse.services.tokens import TokenService

PASSWORD = "correct horse battery"
CHEAP = PasswordHasher(
    PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
)
FIVE_MINUTES = timedelta(minutes=5)

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
def factory(engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(engine)


async def register_once(factory: SessionFactory, email: str) -> User:
    async with factory() as session:
        auth = AuthService(
            UserRepository(session),
            CHEAP,
            TokenService(
                SecretStr("concurrency-test-secret-long-enough-hs256"),
                timedelta(minutes=15),
            ),
            RefreshTokenService(RefreshTokenRepository(session), timedelta(days=7)),
        )
        user = await auth.register(email, PASSWORD)
        await session.commit()
        return user


class MeetingRepository(RefreshTokenRepository):
    """Репозиторий, который после чтения ждёт второго участника.

    Без этого второй запрос мог бы прочитать токен уже после коммита первого, и
    гонки не было бы: тест проходил бы и без защиты от неё.
    """

    def __init__(self, session: Session, barrier: asyncio.Barrier | None) -> None:
        super().__init__(session)
        self._barrier = barrier

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        found = await super().find_by_hash(token_hash)
        if self._barrier is not None:
            await self._barrier.wait()
        return found


async def rotate_once(
    factory: SessionFactory, token: str, barrier: asyncio.Barrier | None = None
) -> str:
    """Один «запрос» обновления: возвращает новый токен, ошибку не прячет."""
    async with factory() as session:
        service = RefreshTokenService(
            MeetingRepository(session, barrier), timedelta(days=7)
        )
        # Ошибка вылетает без commit: транзакцию с ошибкой не фиксируем, как это
        # делает `get_session`. Отзыв цепочки сервис фиксирует сам.
        _, issued = await service.rotate(token)
        await session.commit()
        return issued.token


async def create_monitor_once(factory: SessionFactory, actor: User, url: str) -> int:
    async with factory() as session:
        service = MonitorService(MonitorRepository(session))
        monitor = await service.create(actor, url=url, interval=FIVE_MINUTES, timeout=5)
        await session.commit()
        return monitor.id


async def test_concurrent_registrations_of_one_email_create_one_user(
    factory: SessionFactory,
) -> None:
    results = await asyncio.gather(
        *(register_once(factory, "race@example.com") for _ in range(5)),
        return_exceptions=True,
    )

    created = [r for r in results if isinstance(r, User)]
    errors = [r for r in results if isinstance(r, DuplicateUserError)]
    assert len(created) == 1
    assert len(errors) == 4


async def test_concurrent_creation_of_one_url_makes_one_monitor(
    factory: SessionFactory,
) -> None:
    owner = await register_once(factory, "owner@example.com")

    results = await asyncio.gather(
        *(create_monitor_once(factory, owner, "https://a.test") for _ in range(5)),
        return_exceptions=True,
    )

    assert sum(isinstance(r, int) for r in results) == 1
    assert sum(isinstance(r, DuplicateMonitorError) for r in results) == 4


async def test_two_parallel_refreshes_of_one_token_let_only_one_through(
    factory: SessionFactory,
) -> None:
    user = await register_once(factory, "alice@example.com")
    async with factory() as session:
        issued = await RefreshTokenService(
            RefreshTokenRepository(session), timedelta(days=7)
        ).issue(user.id)
        await session.commit()

    meeting = asyncio.Barrier(2)  # оба прочитали токен действующим, потом пишут
    results = await asyncio.gather(
        rotate_once(factory, issued.token, meeting),
        rotate_once(factory, issued.token, meeting),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, str)]
    losers = [r for r in results if isinstance(r, InvalidRefreshTokenError)]
    assert (len(winners), len(losers)) == (1, 1)

    # Проигравший это повторное использование: цепочка отозвана целиком, и новый
    # токен победителя больше не работает. Вор и владелец оба вынуждены войти заново.
    with pytest.raises(InvalidRefreshTokenError):
        await rotate_once(factory, winners[0])


async def test_user_is_forced_out_after_a_reuse_rejected_with_an_error(
    factory: SessionFactory,
) -> None:
    # Запрос с повторным токеном заканчивается ошибкой, но отзыв цепочки остаётся.
    user = await register_once(factory, "alice@example.com")
    async with factory() as session:
        first = await RefreshTokenService(
            RefreshTokenRepository(session), timedelta(days=7)
        ).issue(user.id)
        await session.commit()
    current = await rotate_once(factory, first.token)

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_once(factory, first.token)  # вор предъявил старый токен

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_once(factory, current)  # законный владелец тоже отозван
