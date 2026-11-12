from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import BigInteger, any_, bindparam, delete, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from pulse.domain.checks import Check
from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.reports import MonitorUptime, ReportStats
from pulse.repositories.database import Session, constraint_name
from pulse.repositories.models import CheckModel, MonitorModel

DEFAULT_MAX_CHECKS_PER_MONITOR = 100


def _to_domain(model: CheckModel) -> Check:
    return Check(
        id=model.id,
        monitor_id=model.monitor_id,
        url=model.url,
        status_code=model.status_code,
        latency_ms=model.duration_ms,
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
            duration_ms=latency_ms,
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

    async def get(self, check_id: int) -> Check | None:
        """Проверка по id или `None`: её могли удалить чисткой старых записей."""
        model = await self._session.get(CheckModel, check_id)
        return None if model is None else _to_domain(model)

    async def uptime_by_monitor(self, since: datetime) -> list[MonitorUptime]:
        """Сколько проверок и сколько успешных у каждого монитора с момента `since`.

        Один запрос на все мониторы вместо запроса на каждый (N+1). Идёт по индексу
        `checks_checked_at_idx`: условие только по времени, без монитора.
        """
        rows = await self._session.execute(
            select(
                CheckModel.monitor_id, func.count(), func.count().filter(CheckModel.ok)
            )
            .where(CheckModel.checked_at >= since)
            .group_by(CheckModel.monitor_id)
            .order_by(CheckModel.monitor_id)
        )
        return [MonitorUptime(monitor_id, total, ok) for monitor_id, total, ok in rows]

    async def list_recent(self, monitor_id: int, limit: int) -> list[Check]:
        """До `limit` последних проверок монитора, новые первыми."""
        result = await self._session.scalars(
            select(CheckModel)
            .where(CheckModel.monitor_id == monitor_id)
            .order_by(CheckModel.checked_at.desc(), CheckModel.id.desc())
            .limit(limit)
        )
        return [_to_domain(c) for c in result]

    async def stats(self, monitor_id: int, since: datetime) -> ReportStats:
        """Итоги монитора с момента `since`: число проверок, успешные, задержки.

        Один проход по индексу `(monitor_id, checked_at DESC)`. Всё считает база
        (`count`, `avg`, `percentile_cont`), а не Python: тысячи строк по сети ради
        одного числа были бы дороже самого запроса. Задержки берутся только у
        проверок с ответом (`status_code IS NOT NULL`).
        """
        answered = CheckModel.status_code.is_not(None)
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count().filter(CheckModel.ok),
                    func.avg(CheckModel.duration_ms).filter(answered),
                    func.percentile_cont(0.95)
                    .within_group(CheckModel.duration_ms)
                    .filter(answered),
                ).where(
                    CheckModel.monitor_id == monitor_id, CheckModel.checked_at >= since
                )
            )
        ).one()
        total, ok, avg, p95 = row
        return ReportStats(
            total=int(total),
            ok=int(ok),
            avg_latency_ms=None if avg is None else float(avg),
            p95_latency_ms=None if p95 is None else float(p95),
        )

    async def last_for_monitors(self, monitor_ids: Sequence[int]) -> dict[int, Check]:
        """Последняя проверка каждого монитора ОДНИМ запросом (без N+1).

        Мониторов без проверок в ответе нет. Форма запроса `JOIN LATERAL ... LIMIT 1`:
        для каждого монитора из списка база делает один заход в индекс
        `(monitor_id, checked_at DESC)` и читает ровно одну строку, поэтому стоимость
        зависит от числа мониторов, а не от числа проверок. `DISTINCT ON` и оконная
        функция читают ВСЕ проверки этих мониторов и отбрасывают лишнее.

        Порядок такой же, как в `list_recent` (новые первыми, при равном времени
        больший id), поэтому результаты обоих методов совпадают.
        """
        if not monitor_ids:
            return {}
        latest = (
            select(CheckModel)
            .where(CheckModel.monitor_id == MonitorModel.id)
            .order_by(CheckModel.checked_at.desc(), CheckModel.id.desc())
            .limit(1)
            .lateral("latest")
        )
        last = aliased(CheckModel, latest)
        # `= ANY(массив)`, а не `IN (...)`: текст запроса не зависит от числа id, и
        # план можно переиспользовать.
        ids = bindparam("ids", list(monitor_ids), type_=ARRAY(BigInteger))
        result = await self._session.scalars(
            select(last)
            .select_from(MonitorModel)
            .join(latest, true())
            .where(MonitorModel.id == any_(ids))
        )
        return {c.monitor_id: _to_domain(c) for c in result}

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
