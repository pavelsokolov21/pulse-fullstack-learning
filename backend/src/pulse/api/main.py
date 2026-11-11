import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulse.api.errors import register_error_handlers
from pulse.api.http_client import create_http_client
from pulse.api.middlewares.request_id import RequestIdMiddleware
from pulse.api.openapi import generate_operation_id
from pulse.api.routers.auth import router as auth_router
from pulse.api.routers.checks import router as checks_router
from pulse.api.routers.demo import router as demo_router
from pulse.api.routers.monitors import router as monitors_router
from pulse.api.routers.reports import router as reports_router
from pulse.api.settings import ApiSettings, get_settings
from pulse.core.passwords import PasswordHasher
from pulse.repositories.database import create_engine, create_session_factory
from pulse.repositories.redis_client import create_redis, ping
from pulse.repositories.redis_rate_limiter import RedisRateLimiter
from pulse.repositories.report_cache import ReportCache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Состояние (пул соединений, HTTP-клиент) создаётся на запуск приложения: у
    # каждого приложения (и у каждого теста с `with TestClient(app)`) своё, а не
    # глобальная переменная. Репозитории и сервисы собираются поверх него в
    # зависимостях, на каждый запрос.
    settings: ApiSettings = app.state.settings
    engine = create_engine(settings.database_url.get_secret_value())
    app.state.session_factory = create_session_factory(engine)
    app.state.password_hasher = PasswordHasher()
    # Один клиент Redis на процесс (внутри пул соединений): им пользуются и кеш
    # отчётов, и лимитер входа.
    redis = create_redis(settings.redis_url.get_secret_value())
    app.state.redis = redis
    # Лимит общий для всех процессов сервера: счётчики лежат в Redis, а не в памяти.
    app.state.login_rate_limiter = RedisRateLimiter(
        redis,
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
        prefix="pulse:ratelimit:login",
    )
    app.state.report_cache = ReportCache(redis, settings.report_cache_ttl_seconds)

    logger = logging.getLogger("pulse")
    previous_level = logger.level
    logger.setLevel(settings.log_level)
    try:
        # Падаем при запуске, а не на первом запросе: с неверным паролем или без
        # Redis приложение не может ни ограничивать вход, ни кешировать.
        await ping(redis)
        # Один клиент на всё приложение: общий пул соединений; закрывается на выходе.
        async with create_http_client() as client:
            app.state.http_client = client
            yield
    finally:
        logger.setLevel(previous_level)
        await redis.aclose()
        await engine.dispose()


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
        generate_unique_id_function=generate_operation_id,
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(auth_router)
    app.include_router(monitors_router)
    app.include_router(checks_router)
    app.include_router(reports_router)
    if not is_prod:
        # Учебная демонстрация гонок: создаёт временные строки и теряет обновления
        # намеренно, поэтому в боевом окружении её нет.
        app.include_router(demo_router)
    return app


# Нужен `fastapi dev` и uvicorn: в `fastapi dev` нет флага --factory.
app = create_app()
