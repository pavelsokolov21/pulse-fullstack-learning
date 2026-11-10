from datetime import datetime

from sqlalchemy import select, update

from pulse.domain.errors import ConcurrentUpdateError, IncidentNotFoundError
from pulse.domain.incidents import Incident
from pulse.repositories.database import Session
from pulse.repositories.models import IncidentModel


def _to_domain(model: IncidentModel) -> Incident:
    return Incident(
        id=model.id,
        monitor_id=model.monitor_id,
        started_at=model.started_at,
        resolved_at=model.resolved_at,
        failed_checks=model.failed_checks,
        last_error=model.last_error,
        opened_by_check_id=model.opened_by_check_id,
        resolved_by_check_id=model.resolved_by_check_id,
        version=model.version,
    )


class IncidentRepository:
    """Инциденты в PostgreSQL (пока только то, что нужно счётчику неудач)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create(
        self, *, monitor_id: int, started_at: datetime, failed_checks: int = 1
    ) -> Incident:
        """Открытый инцидент монитора (только его создание, без правил открытия)."""
        model = IncidentModel(
            monitor_id=monitor_id, started_at=started_at, failed_checks=failed_checks
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_domain(model)

    async def get(self, incident_id: int) -> Incident:
        model = await self._session.get(IncidentModel, incident_id)
        if model is None:
            raise IncidentNotFoundError(incident_id)
        return _to_domain(model)

    async def get_for_update(self, incident_id: int) -> Incident:
        """Читает инцидент и блокирует строку до конца транзакции.

        `SELECT ... FOR UPDATE`: другая транзакция, которая тоже читает эту строку с
        блокировкой или меняет её, ждёт здесь, пока мы не сделаем `commit()` или откат.
        `populate_existing` обязателен: `FOR UPDATE` сам по себе НЕ обновляет объект,
        который уже лежит в identity map сессии (на него кто-то держит ссылку).
        Блокировка была бы взята, а значение осталось бы старым, то есть защиты от
        потерянного обновления нет. С `populate_existing` значения всегда из базы.
        """
        model = await self._session.scalar(
            select(IncidentModel)
            .where(IncidentModel.id == incident_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if model is None:
            raise IncidentNotFoundError(incident_id)
        return _to_domain(model)

    async def set_failed_checks(self, incident_id: int, value: int) -> None:
        """Записывает счётчик как есть: `UPDATE ... SET failed_checks = value`.

        Значение приходит снаружи, и что оно устарело, база не узнает: безусловная
        запись затирает чужие изменения (lost update). Безопасна только после
        `get_for_update` в той же транзакции. Версию строки увеличивает, как и любая
        запись счётчика: иначе оптимистичный путь (`set_failed_checks_if_version`)
        не заметил бы эту запись и прошёл бы поверх неё.
        """
        updated = await self._session.scalar(
            update(IncidentModel)
            .where(IncidentModel.id == incident_id)
            .values(failed_checks=value, version=IncidentModel.version + 1)
            .returning(IncidentModel.id)
        )
        if updated is None:
            raise IncidentNotFoundError(incident_id)

    async def set_failed_checks_if_version(
        self, incident_id: int, value: int, expected_version: int
    ) -> None:
        """Пишет счётчик, только если версия строки всё ещё `expected_version`.

        Оптимистичная блокировка: проверка и запись это один `UPDATE ... WHERE
        version = :expected`, поэтому между ними не втиснется чужая запись. Если
        параллельная транзакция меняет строку, наш `UPDATE` ждёт её завершения,
        перечитывает условие и не находит нужной версии. Версию увеличивает база
        (`version + 1`), а не Python.
        """
        updated = await self._session.scalar(
            update(IncidentModel)
            .where(
                IncidentModel.id == incident_id,
                IncidentModel.version == expected_version,
            )
            .values(failed_checks=value, version=IncidentModel.version + 1)
            .returning(IncidentModel.id)
        )
        if updated is not None:
            return
        # Ноль строк: либо инцидента нет, либо версия устарела. Различаем вторым
        # запросом, и только на промахе: на удачной записи он не нужен.
        exists = await self._session.scalar(
            select(IncidentModel.id).where(IncidentModel.id == incident_id)
        )
        if exists is None:
            raise IncidentNotFoundError(incident_id)
        raise ConcurrentUpdateError(incident_id)

    async def commit(self) -> None:
        """Фиксирует транзакцию сразу, не дожидаясь конца запроса."""
        await self._session.commit()
