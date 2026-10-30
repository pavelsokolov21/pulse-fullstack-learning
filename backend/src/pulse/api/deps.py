from typing import Annotated

import httpx
from fastapi import Depends, Request

from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService

# Зависимости `async def`: синхронную FastAPI выполнил бы в пуле потоков, а здесь
# нужно только вернуть ссылку.


async def get_monitor_repository(request: Request) -> MonitorRepository:
    repository: MonitorRepository = request.app.state.monitor_repository
    return repository


async def get_check_repository(request: Request) -> CheckRepository:
    repository: CheckRepository = request.app.state.check_repository
    return repository


async def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


MonitorRepositoryDep = Annotated[MonitorRepository, Depends(get_monitor_repository)]
CheckRepositoryDep = Annotated[CheckRepository, Depends(get_check_repository)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


async def get_monitor_service(
    monitors: MonitorRepositoryDep, checks: CheckRepositoryDep
) -> MonitorService:
    # Сервис без собственного состояния: дёшево собирать на каждый запрос, а
    # репозитории (состояние) приходят снаружи и подменяются отдельно.
    return MonitorService(monitors, checks)


async def get_check_service(
    monitors: MonitorRepositoryDep,
    checks: CheckRepositoryDep,
    client: HttpClientDep,
) -> CheckService:
    return CheckService(monitors, checks, client)


MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]
CheckServiceDep = Annotated[CheckService, Depends(get_check_service)]
