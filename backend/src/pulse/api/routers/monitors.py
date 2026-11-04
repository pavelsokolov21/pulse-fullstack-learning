from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from pulse.api.deps import (
    CurrentUserDep,
    MonitorServiceDep,
    get_current_user,
    require_writer,
)
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.monitors import (
    MonitorCreate,
    MonitorListItem,
    MonitorPage,
    MonitorRead,
    MonitorUpdate,
)
from pulse.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

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

DEFAULT_LIMIT = DEFAULT_PAGE_SIZE
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Offset = Annotated[int, Query(ge=0)]

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
    return MonitorRead.model_validate(await service.create(user, **body.model_dump()))


@router.get("")
async def list_monitors(
    service: MonitorServiceDep,
    user: CurrentUserDep,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MonitorPage:
    """Страница мониторов с email владельца: админу все, остальным свои."""
    page = await service.list_page(user, limit=limit, offset=offset)
    return MonitorPage(
        items=[MonitorListItem.model_validate(m) for m in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{monitor_id}", responses=NOT_FOUND)
async def get_monitor(
    monitor_id: int, service: MonitorServiceDep, user: CurrentUserDep
) -> MonitorRead:
    return MonitorRead.model_validate(await service.get(user, monitor_id))


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
    updated = await service.update(user, monitor_id, **changes)
    return MonitorRead.model_validate(updated)


@router.delete(
    "/{monitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_writer)],
    responses=NOT_FOUND | FORBIDDEN,
)
async def delete_monitor(
    monitor_id: int, service: MonitorServiceDep, user: CurrentUserDep
) -> None:
    await service.delete(user, monitor_id)
