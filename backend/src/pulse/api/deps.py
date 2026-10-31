from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from pulse.api.settings import ApiSettings
from pulse.core.passwords import PasswordHasher
from pulse.domain.errors import MissingTokenError
from pulse.domain.users import User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.users import UserRepository
from pulse.services.auth import AuthService
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService
from pulse.services.tokens import TokenService

# Зависимости `async def`: синхронную FastAPI выполнил бы в пуле потоков, а здесь
# нужно только вернуть ссылку.


async def get_api_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


async def get_monitor_repository(request: Request) -> MonitorRepository:
    repository: MonitorRepository = request.app.state.monitor_repository
    return repository


async def get_check_repository(request: Request) -> CheckRepository:
    repository: CheckRepository = request.app.state.check_repository
    return repository


async def get_user_repository(request: Request) -> UserRepository:
    repository: UserRepository = request.app.state.user_repository
    return repository


async def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


async def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


SettingsDep = Annotated[ApiSettings, Depends(get_api_settings)]
MonitorRepositoryDep = Annotated[MonitorRepository, Depends(get_monitor_repository)]
CheckRepositoryDep = Annotated[CheckRepository, Depends(get_check_repository)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


async def get_monitor_service(
    monitors: MonitorRepositoryDep, checks: CheckRepositoryDep
) -> MonitorService:
    # Сервис без собственного состояния: дёшево собирать на каждый запрос, а
    # репозитории (состояние) приходят снаружи и подменяются отдельно.
    return MonitorService(monitors, checks)


async def get_check_service(
    monitors: MonitorRepositoryDep,
    checks: CheckRepositoryDep,
    client: HttpClientDep,
) -> CheckService:
    return CheckService(monitors, checks, client)


async def get_token_service(settings: SettingsDep) -> TokenService:
    return TokenService(
        settings.jwt_secret, timedelta(minutes=settings.access_token_ttl_minutes)
    )


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


async def get_auth_service(
    users: UserRepositoryDep, hasher: PasswordHasherDep, tokens: TokenServiceDep
) -> AuthService:
    return AuthService(users, hasher, tokens)


MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]
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
    return auth.authenticate(token)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
