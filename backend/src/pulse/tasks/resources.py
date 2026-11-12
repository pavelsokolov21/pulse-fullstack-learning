from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from pulse.repositories.database import (
    SessionFactory,
    create_engine,
    create_session_factory,
)
from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.tasks.settings import WorkerSettings

HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Resources:
    session_factory: SessionFactory
    redis: RedisClient
    http: httpx.AsyncClient


@asynccontextmanager
async def open_resources(settings: WorkerSettings) -> AsyncGenerator[Resources]:
    """Соединения на время одной задачи.

    Celery выполняет обычные (синхронные) функции, а наш код асинхронный, поэтому
    задача запускает его через `asyncio.run`: на каждый вызов новый цикл событий. Пул
    соединений SQLAlchemy и клиент Redis привязаны к тому циклу, где созданы, и в
    следующем `asyncio.run` их использовать нельзя («attached to a different loop»).
    Поэтому ресурсы создаются и закрываются внутри задачи. Для задач в десятки в
    секунду накладные расходы заметны: тогда берут воркер с асинхронным ядром
    (Taskiq, ARQ) или держат один цикл на процесс.
    """
    engine = create_engine(settings.database_url.get_secret_value())
    redis = create_redis(settings.broker_url.get_secret_value())
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
            yield Resources(create_session_factory(engine), redis, http)
    finally:
        await redis.aclose()
        await engine.dispose()
