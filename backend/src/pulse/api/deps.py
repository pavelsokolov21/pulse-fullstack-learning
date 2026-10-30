from typing import Annotated

from fastapi import Depends, Request

from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitors import MonitorService


async def get_monitor_repository(request: Request) -> MonitorRepository:
    # `async def`: синхронную зависимость FastAPI выполнил бы в пуле потоков,
    # а здесь нужно только вернуть ссылку.
    repository: MonitorRepository = request.app.state.monitor_repository
    return repository


MonitorRepositoryDep = Annotated[MonitorRepository, Depends(get_monitor_repository)]


async def get_monitor_service(repository: MonitorRepositoryDep) -> MonitorService:
    # Сервис без собственного состояния: дёшево собирать на каждый запрос, а
    # репозиторий (состояние) приходит снаружи и подменяется отдельно.
    return MonitorService(repository)


MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]
