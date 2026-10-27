from typing import Annotated

from fastapi import Depends, Request

from pulse.monitors.store import MonitorStore


async def get_store(request: Request) -> MonitorStore:
    # `async def`: синхронную зависимость FastAPI выполнил бы в пуле потоков,
    # а здесь нужно только вернуть ссылку.
    store: MonitorStore = request.app.state.store
    return store


StoreDep = Annotated[MonitorStore, Depends(get_store)]
