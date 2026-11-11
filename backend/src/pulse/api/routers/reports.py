from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from pulse.api.deps import CurrentUserDep, ReportServiceDep, get_current_user
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.reports import MonitorReportRead
from pulse.domain.reports import ReportWindow

router = APIRouter(
    prefix="/monitors/{monitor_id}/report",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

CACHE_HEADER = "X-Cache"


@router.get("")
async def get_monitor_report(
    monitor_id: int,
    service: ReportServiceDep,
    user: CurrentUserDep,
    response: Response,
    window: Annotated[ReportWindow, Query()] = ReportWindow.WEEK,
) -> MonitorReportRead:
    """Аптайм и задержки (среднее, p95) монитора за окно.

    Заголовок `X-Cache`: `HIT`, если отчёт отдан из кеша, `MISS`, если посчитан по
    базе. Новая проверка монитора сбрасывает кеш сразу.
    """
    result = await service.get(user, monitor_id, window)
    response.headers[CACHE_HEADER] = "HIT" if result.cached else "MISS"
    return MonitorReportRead.model_validate(result.report)
