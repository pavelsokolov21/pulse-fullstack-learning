from typing import Any

from fastapi import APIRouter, status

from pulse.api.deps import StoreDep
from pulse.api.errors import ErrorResponse
from pulse.monitors.models import MonitorCreate, MonitorRead, MonitorUpdate

# Обработчики `async def` без await намеренно: так они выполняются в потоке
# event loop, и хранилище в памяти не требует блокировок. `def` ушёл бы в пул потоков.
router = APIRouter(prefix="/monitors", tags=["monitors"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_monitor(body: MonitorCreate, store: StoreDep) -> MonitorRead:
    return store.create(body)


@router.get("")
async def list_monitors(store: StoreDep) -> list[MonitorRead]:
    return store.list()


@router.get("/{monitor_id}", responses=NOT_FOUND)
async def get_monitor(monitor_id: int, store: StoreDep) -> MonitorRead:
    return store.get(monitor_id)


@router.patch("/{monitor_id}", responses=NOT_FOUND)
async def update_monitor(
    monitor_id: int, body: MonitorUpdate, store: StoreDep
) -> MonitorRead:
    return store.update(monitor_id, body)


@router.delete(
    "/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_monitor(monitor_id: int, store: StoreDep) -> None:
    store.delete(monitor_id)
