from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from pulse.domain.checks import Check
from pulse.domain.errors import MonitorNotFoundError
from pulse.repositories.database import Session, constraint_name
from pulse.repositories.models import CheckModel

DEFAULT_MAX_CHECKS_PER_MONITOR = 100


def _to_domain(model: CheckModel) -> Check:
    return Check(
        id=model.id,
        monitor_id=model.monitor_id,
        url=model.url,
        status_code=model.status_code,
        latency_ms=model.latency_ms,
        error=model.error,
        checked_at=model.checked_at,
        ok=model.ok,
    )


class CheckRepository:
    """Последние проверки мониторов в PostgreSQL.

    На монитор хранится не больше `max_per_monitor` проверок: после каждой записи
    старые удаляются. Это решение учебного проекта; на больших объёмах чистку
    выносят в периодическую задачу или секционирование (фаза 2, неделя 4).
    """

    def __init__(
        self, session: Session, max_per_monitor: int = DEFAULT_MAX_CHECKS_PER_MONITOR
    ) -> None:
        if max_per_monitor < 1:
            raise ValueError("max_per_monitor должен быть не меньше 1")
        self._session = session
        self._max_per_monitor = max_per_monitor

    async def add(
        self,
        *,
        monitor_id: int,
        url: str,
        status_code: int | None,
        latency_ms: float,
        error: str | None,
        checked_at: datetime,
    ) -> Check:
        """Сохраняет проверку. Поле `ok` вычисляет сама база по `status_code`.

        Если монитор удалили (в том числе параллельным запросом), внешний ключ не
        даст записать проверку: `MonitorNotFoundError`.
        """
        model = CheckModel(
            monitor_id=monitor_id,
            url=url,
            status_code=status_code,
            latency_ms=latency_ms,
            error=error,
            checked_at=checked_at,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
        except IntegrityError as exc:
            if constraint_name(exc) == "checks_monitor_id_fkey":
                raise MonitorNotFoundError(monitor_id) from exc
            raise
        check = _to_domain(model)
        await self._trim(monitor_id)
        return check

    async def list_recent(self, monitor_id: int, limit: int) -> list[Check]:
        """До `limit` последних проверок монитора, новые первыми."""
        result = await self._session.scalars(
            select(CheckModel)
            .where(CheckModel.monitor_id == monitor_id)
            .order_by(CheckModel.checked_at.desc(), CheckModel.id.desc())
            .limit(limit)
        )
        return [_to_domain(c) for c in result]

    async def release_connection(self) -> None:
        """Завершает текущую транзакцию, возвращая соединение в пул.

        Нужно перед долгим ожиданием не-базы (HTTP-запрос проверки): иначе сессия
        держит соединение в состоянии «idle in transaction» все 10+ секунд, а пул
        из 15 соединений кончается на 16-й одновременной проверке.
        """
        await self._session.commit()

    async def _trim(self, monitor_id: int) -> None:
        newest = (
            select(CheckModel.id)
            .where(CheckModel.monitor_id == monitor_id)
            .order_by(CheckModel.checked_at.desc(), CheckModel.id.desc())
            .limit(self._max_per_monitor)
        )
        await self._session.execute(
            delete(CheckModel).where(
                CheckModel.monitor_id == monitor_id, CheckModel.id.not_in(newest)
            )
        )
