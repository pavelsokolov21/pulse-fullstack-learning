from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from pulse.api.deps import (
    RefreshTokenRepositoryDep,
    get_current_user,
    get_login_rate_limiter,
    get_password_hasher,
    get_refresh_token_service,
    get_token_service,
)
from pulse.api.main import app, create_app
from pulse.api.settings import ApiSettings
from pulse.core.passwords import PasswordHasher
from pulse.core.rate_limit import SlidingWindowRateLimiter
from pulse.domain.users import Role, User
from pulse.repositories.users import UserRepository
from pulse.services.refresh_tokens import RefreshTokenService
from pulse.services.tokens import TokenService

TEST_USER = User(
    id=1, email="tester@example.com", password_hash="not-a-real-hash", role=Role.MANAGER
)


@pytest.fixture(autouse=True)
def authenticated_user() -> Iterator[None]:
    """Тесты ручек мониторов и проверок не занимаются входом.

    Авторизацию проверяют отдельные тесты (`test_auth*.py`, `test_protection.py`)
    на собственных приложениях без подмены.
    """
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


# --- приложение с настоящей авторизацией -----------------------------------


class FakeClock:
    """Управляемые часы: время стоит, пока тест не сдвинет его."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def fast_password_hasher() -> PasswordHasher:
    # Настоящий argon2 стоит ~50 мс на хеш: сотни тестов вошли бы в минуты.
    cheap = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
    return PasswordHasher(cheap)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def auth_app(clock: FakeClock) -> FastAPI:
    """Новое приложение без подмены `get_current_user`; время и хеширование свои."""
    settings = ApiSettings(**dict[str, Any]())
    application = create_app(settings)
    hasher = fast_password_hasher()
    tokens = TokenService(
        settings.jwt_secret, timedelta(minutes=settings.access_token_ttl_minutes), clock
    )
    # Лимит входа щедрый: тесты входят десятки раз с одного адреса. Тесты самого
    # ограничения собирают приложение с узким лимитом (см. test_rate_limit.py).
    limiter = SlidingWindowRateLimiter(10_000, 60)

    async def refresh_service(
        repository: RefreshTokenRepositoryDep,
    ) -> RefreshTokenService:
        ttl = timedelta(days=settings.refresh_token_ttl_days)
        return RefreshTokenService(repository, ttl, clock)

    application.dependency_overrides[get_password_hasher] = lambda: hasher
    application.dependency_overrides[get_token_service] = lambda: tokens
    application.dependency_overrides[get_refresh_token_service] = refresh_service
    application.dependency_overrides[get_login_rate_limiter] = lambda: limiter
    return application


@pytest.fixture
def auth_client(auth_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(auth_app) as client:
        yield client


PASSWORD = "correct horse battery"

SignIn = Callable[..., dict[str, str]]


@pytest.fixture
def sign_in(auth_app: FastAPI, auth_client: TestClient) -> SignIn:
    """Создаёт пользователя с нужной ролью и возвращает заголовок с его токеном.

    Роль через API не выдаётся (регистрация всегда создаёт менеджера), поэтому
    пользователь кладётся прямо в репозиторий, а вход идёт настоящим запросом.
    """
    cheap = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))

    def _sign_in(email: str, role: Role = Role.MANAGER) -> dict[str, str]:
        users: UserRepository = auth_app.state.user_repository
        users.create(email=email, password_hash=cheap.hash(PASSWORD), role=role)
        response = auth_client.post(
            "/auth/login", data={"username": email, "password": PASSWORD}
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _sign_in
