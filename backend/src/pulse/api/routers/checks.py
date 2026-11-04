from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from pulse.api.deps import (
    CheckServiceDep,
    CurrentUserDep,
    get_current_user,
    require_writer,
)
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.checks import CheckRead

router = APIRouter(
    prefix="/monitors/{monitor_id}/checks",
    tags=["checks"],
    # Весь роутер закрыт авторизацией: новую ручку нельзя «забыть» защитить.
    dependencies=[Depends(get_current_user)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

# Лимит ответа это часть контракта API и не зависит от того, сколько проверок
# хранится: хранится меньше лимита, вернётся столько, сколько есть.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
async def run_check(
    monitor_id: int, service: CheckServiceDep, user: CurrentUserDep
) -> CheckRead:
    return CheckRead.model_validate(await service.run(user, monitor_id))


@router.get("")
async def list_checks(
    monitor_id: int,
    service: CheckServiceDep,
    user: CurrentUserDep,
    limit: Limit = DEFAULT_LIMIT,
) -> list[CheckRead]:
    checks = await service.list_recent(user, monitor_id, limit)
    return [CheckRead.model_validate(c) for c in checks]
