from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulse.api.errors import register_error_handlers
from pulse.api.middlewares.request_id import RequestIdMiddleware
from pulse.api.routers.monitors import router as monitors_router
from pulse.repositories.monitors import MonitorRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Состояние (репозиторий) создаётся на запуск приложения: у каждого
    # приложения (и у каждого теста с `with TestClient(app)`) своё, а не глобальная
    # переменная. Сервис собирается поверх него в зависимости на каждый запрос.
    app.state.monitor_repository = MonitorRepository()
    yield


app = FastAPI(title="Pulse", lifespan=lifespan)
register_error_handlers(app)
app.add_middleware(RequestIdMiddleware)
app.include_router(monitors_router)
