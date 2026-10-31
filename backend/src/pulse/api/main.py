import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulse.api.errors import register_error_handlers
from pulse.api.http_client import create_http_client
from pulse.api.middlewares.request_id import RequestIdMiddleware
from pulse.api.routers.auth import router as auth_router
from pulse.api.routers.checks import router as checks_router
from pulse.api.routers.monitors import router as monitors_router
from pulse.api.settings import ApiSettings, get_settings
from pulse.core.passwords import PasswordHasher
from pulse.core.rate_limit import SlidingWindowRateLimiter
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.refresh_tokens import RefreshTokenRepository
from pulse.repositories.users import UserRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Состояние (репозитории, HTTP-клиент) создаётся на запуск приложения: у
    # каждого приложения (и у каждого теста с `with TestClient(app)`) своё, а не
    # глобальная переменная. Сервисы собираются поверх него в зависимостях.
    settings: ApiSettings = app.state.settings
    app.state.monitor_repository = MonitorRepository()
    app.state.check_repository = CheckRepository(settings.max_checks_per_monitor)
    app.state.user_repository = UserRepository()
    app.state.refresh_token_repository = RefreshTokenRepository()
    app.state.password_hasher = PasswordHasher()
    app.state.login_rate_limiter = SlidingWindowRateLimiter(
        settings.login_rate_limit_attempts, settings.login_rate_limit_window_seconds
    )

    logger = logging.getLogger("pulse")
    previous_level = logger.level
    logger.setLevel(settings.log_level)
    # Один клиент на всё приложение: общий пул соединений; закрывается на выходе.
    try:
        async with create_http_client() as client:
            app.state.http_client = client
            yield
    finally:
        logger.setLevel(previous_level)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Собирает приложение.

    Настройки читаются здесь, до `FastAPI(...)`: от них зависит, включены ли
    `/docs` и `/redoc`. Неверное значение в окружении роняет запуск, а не
    первый запрос.
    """
    settings = settings or get_settings()
    is_prod = settings.environment == "prod"

    app = FastAPI(
        title="Pulse",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(auth_router)
    app.include_router(monitors_router)
    app.include_router(checks_router)
    return app


# Нужен `fastapi dev` и uvicorn: в `fastapi dev` нет флага --factory.
app = create_app()
