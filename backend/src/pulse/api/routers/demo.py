from datetime import timedelta

from fastapi import APIRouter, Depends, status

from pulse.api.deps import ConcurrencyDemoServiceDep, CurrentUserDep, require_writer
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.demo import (
    CallResultRead,
    ConcurrencyRunRead,
    ConcurrencyRunRequest,
    TraceEventRead,
)

router = APIRouter(
    prefix="/demo",
    tags=["demo"],
    # Создаёт строки в базе: только вошедшие пользователи с правом записи.
    dependencies=[Depends(require_writer)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)


@router.post("/concurrency")
async def run_concurrency_demo(
    body: ConcurrencyRunRequest,
    service: ConcurrencyDemoServiceDep,
    user: CurrentUserDep,
) -> ConcurrencyRunRead:
    """Два параллельных обновления одного счётчика: что произошло и когда.

    Настоящая гонка на настоящей базе. Только для разработки (в prod ручки нет):
    на время прогона создаётся временный монитор, потом он удаляется.
    """
    run = await service.run(
        user.id, body.strategy, timedelta(milliseconds=body.hold_ms)
    )
    return ConcurrencyRunRead(
        strategy=run.strategy,
        hold_ms=run.hold_ms,
        initial=run.initial,
        expected=run.expected,
        final=run.final,
        final_version=run.final_version,
        elapsed_ms=run.elapsed_ms,
        lost_update=run.lost_update,
        events=[TraceEventRead.model_validate(e) for e in run.events],
        calls=[CallResultRead.model_validate(c) for c in run.calls],
    )
