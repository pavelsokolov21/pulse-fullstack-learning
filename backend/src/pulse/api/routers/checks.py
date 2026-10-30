from typing import Annotated

from fastapi import APIRouter, Query, status

from pulse.api.deps import CheckServiceDep
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.checks import CheckRead
from pulse.repositories.checks import MAX_CHECKS_PER_MONITOR

router = APIRouter(
    prefix="/monitors/{monitor_id}/checks",
    tags=["checks"],
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

DEFAULT_LIMIT = 20
Limit = Annotated[int, Query(ge=1, le=MAX_CHECKS_PER_MONITOR)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def run_check(monitor_id: int, service: CheckServiceDep) -> CheckRead:
    return CheckRead.model_validate(await service.run(monitor_id))


@router.get("")
async def list_checks(
    monitor_id: int, service: CheckServiceDep, limit: Limit = DEFAULT_LIMIT
) -> list[CheckRead]:
    checks = service.list_recent(monitor_id, limit)
    return [CheckRead.model_validate(c) for c in checks]
