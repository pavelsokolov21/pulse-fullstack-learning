from typing import Any

from fastapi import APIRouter, Depends, status

from pulse.api.deps import MonitorServiceDep, get_current_user
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.monitors import MonitorCreate, MonitorRead, MonitorUpdate

# Обработчики `async def` без await намеренно: так они выполняются в потоке
# event loop, и репозиторий в памяти не требует блокировок. `def` ушёл бы в пул потоков.
router = APIRouter(
    prefix="/monitors",
    tags=["monitors"],
    # Весь роутер закрыт авторизацией: новую ручку нельзя «забыть» защитить.
    dependencies=[Depends(get_current_user)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": ErrorResponse}
}


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
async def create_monitor(
    body: MonitorCreate, service: MonitorServiceDep
) -> MonitorRead:
    return MonitorRead.model_validate(service.create(**body.model_dump()))


@router.get("")
async def list_monitors(service: MonitorServiceDep) -> list[MonitorRead]:
    return [MonitorRead.model_validate(m) for m in service.list_all()]


@router.get("/{monitor_id}", responses=NOT_FOUND)
async def get_monitor(monitor_id: int, service: MonitorServiceDep) -> MonitorRead:
    return MonitorRead.model_validate(service.get(monitor_id))


@router.patch("/{monitor_id}", responses=NOT_FOUND | CONFLICT)
async def update_monitor(
    monitor_id: int, body: MonitorUpdate, service: MonitorServiceDep
) -> MonitorRead:
    changes = body.model_dump(exclude_unset=True)
    return MonitorRead.model_validate(service.update(monitor_id, **changes))


@router.delete(
    "/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_monitor(monitor_id: int, service: MonitorServiceDep) -> None:
    service.delete(monitor_id)
