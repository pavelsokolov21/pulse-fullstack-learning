from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from pulse.api.deps import CheckServiceDep, get_current_user
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


@router.post("", status_code=status.HTTP_201_CREATED)
async def run_check(monitor_id: int, service: CheckServiceDep) -> CheckRead:
    return CheckRead.model_validate(await service.run(monitor_id))


@router.get("")
async def list_checks(
    monitor_id: int, service: CheckServiceDep, limit: Limit = DEFAULT_LIMIT
) -> list[CheckRead]:
    checks = service.list_recent(monitor_id, limit)
    return [CheckRead.model_validate(c) for c in checks]
