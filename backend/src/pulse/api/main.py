from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulse.api.errors import register_error_handlers
from pulse.api.monitors import router as monitors_router
from pulse.monitors.store import MonitorStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Хранилище создаётся на запуск приложения: у каждого приложения (и у каждого
    # теста с `with TestClient(app)`) своё состояние, а не глобальная переменная.
    app.state.store = MonitorStore()
    yield


app = FastAPI(title="Pulse", lifespan=lifespan)
register_error_handlers(app)
app.include_router(monitors_router)
