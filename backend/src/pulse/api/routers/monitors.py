from typing import Any

from fastapi import APIRouter, Depends, status

from pulse.api.deps import (
    CurrentUserDep,
    MonitorServiceDep,
    get_current_user,
    require_writer,
)
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

FORBIDDEN: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": ErrorResponse}
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
    responses=CONFLICT | FORBIDDEN,
)
async def create_monitor(
    body: MonitorCreate, service: MonitorServiceDep, user: CurrentUserDep
) -> MonitorRead:
    return MonitorRead.model_validate(service.create(user, **body.model_dump()))


@router.get("")
async def list_monitors(
    service: MonitorServiceDep, user: CurrentUserDep
) -> list[MonitorRead]:
    return [MonitorRead.model_validate(m) for m in service.list_all(user)]


@router.get("/{monitor_id}", responses=NOT_FOUND)
async def get_monitor(
    monitor_id: int, service: MonitorServiceDep, user: CurrentUserDep
) -> MonitorRead:
    return MonitorRead.model_validate(service.get(user, monitor_id))


@router.patch(
    "/{monitor_id}",
    dependencies=[Depends(require_writer)],
    responses=NOT_FOUND | CONFLICT | FORBIDDEN,
)
async def update_monitor(
    monitor_id: int,
    body: MonitorUpdate,
    service: MonitorServiceDep,
    user: CurrentUserDep,
) -> MonitorRead:
    changes = body.model_dump(exclude_unset=True)
    return MonitorRead.model_validate(service.update(user, monitor_id, **changes))


@router.delete(
    "/{monitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_writer)],
    responses=NOT_FOUND | FORBIDDEN,
)
async def delete_monitor(
    monitor_id: int, service: MonitorServiceDep, user: CurrentUserDep
) -> None:
    service.delete(user, monitor_id)
