from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from pulse.api.errors import register_error_handlers
from pulse.api.middlewares.request_id import RequestIdMiddleware
from pulse.api.routers.checks import router as checks_router
from pulse.api.routers.monitors import router as monitors_router
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Состояние (репозитории, HTTP-клиент) создаётся на запуск приложения: у
    # каждого приложения (и у каждого теста с `with TestClient(app)`) своё, а не
    # глобальная переменная. Сервисы собираются поверх него в зависимостях.
    app.state.monitor_repository = MonitorRepository()
    app.state.check_repository = CheckRepository()
    # Один клиент на всё приложение: общий пул соединений; закрывается на выходе.
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield


app = FastAPI(title="Pulse", lifespan=lifespan)
register_error_handlers(app)
app.add_middleware(RequestIdMiddleware)
app.include_router(monitors_router)
app.include_router(checks_router)
