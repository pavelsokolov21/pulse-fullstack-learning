from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from pulse.api.settings import ApiSettings
from pulse.core.passwords import PasswordHasher
from pulse.core.rate_limit import SlidingWindowRateLimiter
from pulse.domain.errors import (
    MissingTokenError,
    PermissionDeniedError,
    RateLimitExceededError,
)
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session, SessionFactory
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.refresh_tokens import RefreshTokenRepository
from pulse.repositories.users import UserRepository
from pulse.services.auth import AuthService
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService
from pulse.services.refresh_tokens import RefreshTokenService
from pulse.services.tokens import TokenService

# Зависимости `async def`: синхронную FastAPI выполнил бы в пуле потоков, а здесь
# нужно только вернуть ссылку.


async def get_api_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


SettingsDep = Annotated[ApiSettings, Depends(get_api_settings)]


async def get_session(request: Request) -> AsyncIterator[Session]:
    """Сессия базы на один запрос: одна транзакция, коммит при успехе.

    Если обработчик бросил исключение (в том числе доменное: 404, 409), коммита нет,
    и `close()` откатывает всё сделанное. Аналог транзакционного interceptor в
    NestJS. Все репозитории запроса делят эту сессию, поэтому запрос либо
    сохраняется целиком, либо не сохраняется вовсе.
    """
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        yield session
        await session.commit()


# scope="function": код после yield (коммит) выполняется, как только отработал
# обработчик, ДО отправки ответа. Со scope по умолчанию он шёл бы после отправки, и
# клиент получил бы 201, даже если коммит потом упал.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


async def get_monitor_repository(session: SessionDep) -> MonitorRepository:
    return MonitorRepository(session)


async def get_check_repository(
    session: SessionDep, settings: SettingsDep
) -> CheckRepository:
    return CheckRepository(session, settings.max_checks_per_monitor)


async def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


async def get_refresh_token_repository(session: SessionDep) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


async def get_login_rate_limiter(request: Request) -> SlidingWindowRateLimiter:
    limiter: SlidingWindowRateLimiter = request.app.state.login_rate_limiter
    return limiter


async def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


async def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


MonitorRepositoryDep = Annotated[MonitorRepository, Depends(get_monitor_repository)]
CheckRepositoryDep = Annotated[CheckRepository, Depends(get_check_repository)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
RefreshTokenRepositoryDep = Annotated[
    RefreshTokenRepository, Depends(get_refresh_token_repository)
]
LoginRateLimiterDep = Annotated[
    SlidingWindowRateLimiter, Depends(get_login_rate_limiter)
]
PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


async def get_monitor_service(monitors: MonitorRepositoryDep) -> MonitorService:
    # Сервис без собственного состояния: дёшево собирать на каждый запрос, а
    # репозитории (сессия базы) приходят снаружи и подменяются отдельно.
    return MonitorService(monitors)


MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]


async def get_check_service(
    monitors: MonitorServiceDep,
    checks: CheckRepositoryDep,
    client: HttpClientDep,
) -> CheckService:
    # Проверки спрашивают права у MonitorService: правило владения в одном месте.
    return CheckService(monitors, checks, client)


async def get_token_service(settings: SettingsDep) -> TokenService:
    return TokenService(
        settings.jwt_secret, timedelta(minutes=settings.access_token_ttl_minutes)
    )


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


async def get_refresh_token_service(
    repository: RefreshTokenRepositoryDep, settings: SettingsDep
) -> RefreshTokenService:
    return RefreshTokenService(
        repository, timedelta(days=settings.refresh_token_ttl_days)
    )


RefreshTokenServiceDep = Annotated[
    RefreshTokenService, Depends(get_refresh_token_service)
]


async def get_auth_service(
    users: UserRepositoryDep,
    hasher: PasswordHasherDep,
    tokens: TokenServiceDep,
    refresh_tokens: RefreshTokenServiceDep,
) -> AuthService:
    return AuthService(users, hasher, tokens, refresh_tokens)


CheckServiceDep = Annotated[CheckService, Depends(get_check_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

# auto_error=False: без заголовка схема вернёт None, и 401 в нашем формате
# (с кодом и заголовком WWW-Authenticate) бросаем сами, а не стандартный detail.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)], auth: AuthServiceDep
) -> User:
    # Пустой токен («Authorization: Bearer» без значения) то же, что отсутствующий.
    if not token:
        raise MissingTokenError
    return await auth.authenticate(token)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Фабрика зависимости: пропускает только пользователей с одной из ролей.

    Отвечает на вопрос «может ли роль делать такое вообще» (403). «Её ли это
    объект» решает сервис: см. `MonitorService`.
    """

    async def dependency(user: CurrentUserDep) -> User:
        if user.role not in roles:
            raise PermissionDeniedError
        return user

    return dependency


# Кто может менять данные; читать могут все вошедшие.
require_writer = require_role(Role.ADMIN, Role.MANAGER)


async def enforce_login_rate_limit(
    request: Request, limiter: LoginRateLimiterDep
) -> None:
    # Адрес берём из соединения. За прокси сервер надо запускать с
    # --proxy-headers/--forwarded-allow-ips: заголовок X-Forwarded-For из запроса
    # подделывается, и доверять ему в приложении нельзя. Особенность прокси Next.js
    # (он заголовок не ставит, а пропускает) описан в README.
    key = request.client.host if request.client else "unknown"
    result = limiter.hit(key)
    if not result.allowed:
        raise RateLimitExceededError(result.retry_after)
