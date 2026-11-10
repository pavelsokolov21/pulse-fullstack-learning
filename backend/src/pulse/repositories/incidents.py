from sqlalchemy import update

from pulse.domain.errors import IncidentNotFoundError
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
    )


class IncidentRepository:
    """Инциденты в PostgreSQL (пока только то, что нужно счётчику неудач)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, incident_id: int) -> Incident:
        model = await self._session.get(IncidentModel, incident_id)
        if model is None:
            raise IncidentNotFoundError(incident_id)
        return _to_domain(model)

    async def set_failed_checks(self, incident_id: int, value: int) -> None:
        """Записывает счётчик как есть: `UPDATE ... SET failed_checks = value`.

        Значение приходит снаружи, и что оно устарело, база не узнает: безусловная
        запись затирает чужие изменения (lost update).
        """
        updated = await self._session.scalar(
            update(IncidentModel)
            .where(IncidentModel.id == incident_id)
            .values(failed_checks=value)
            .returning(IncidentModel.id)
        )
        if updated is None:
            raise IncidentNotFoundError(incident_id)

    async def commit(self) -> None:
        """Фиксирует транзакцию сразу, не дожидаясь конца запроса."""
        await self._session.commit()
