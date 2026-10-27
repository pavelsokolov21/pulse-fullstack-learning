from typing import Any

from fastapi import APIRouter, status

from pulse.api.deps import StoreDep
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas import MonitorCreate, MonitorRead, MonitorUpdate

# Обработчики `async def` без await намеренно: так они выполняются в потоке
# event loop, и хранилище в памяти не требует блокировок. `def` ушёл бы в пул потоков.
router = APIRouter(
    prefix="/monitors",
    tags=["monitors"],
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse}
    },
)

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_monitor(body: MonitorCreate, store: StoreDep) -> MonitorRead:
    return MonitorRead.model_validate(store.create(**body.model_dump()))


@router.get("")
async def list_monitors(store: StoreDep) -> list[MonitorRead]:
    return [MonitorRead.model_validate(m) for m in store.list_all()]


@router.get("/{monitor_id}", responses=NOT_FOUND)
async def get_monitor(monitor_id: int, store: StoreDep) -> MonitorRead:
    return MonitorRead.model_validate(store.get(monitor_id))


@router.patch("/{monitor_id}", responses=NOT_FOUND)
async def update_monitor(
    monitor_id: int, body: MonitorUpdate, store: StoreDep
) -> MonitorRead:
    changes = body.model_dump(exclude_unset=True)
    return MonitorRead.model_validate(store.update(monitor_id, **changes))


@router.delete(
    "/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_monitor(monitor_id: int, store: StoreDep) -> None:
    store.delete(monitor_id)
