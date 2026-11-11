from sqlalchemy import select

from pulse.core.request_context import get_request_id
from pulse.domain.audit import AuditEntry
from pulse.repositories.database import Session
from pulse.repositories.models import AuditLogModel


def _to_domain(model: AuditLogModel) -> AuditEntry:
    return AuditEntry(
        id=model.id,
        occurred_at=model.occurred_at,
        actor_id=model.actor_id,
        action=model.action,
        entity_type=model.entity_type,
        entity_id=model.entity_id,
        details=model.details,
        request_id=model.request_id,
    )


class AuditLogRepository:
    """Журнал аудита в PostgreSQL.

    Пишет в ТУ ЖЕ сессию, что и репозиторий изменяемого объекта: запись журнала и само
    изменение это одна транзакция. Либо есть и то и другое, либо нет ни того ни
    другого: нельзя изменить данные и «забыть» записать, и нельзя записать то, чего не
    случилось. Поэтому здесь нет собственного `commit()`: фиксирует тот, кто владеет
    транзакцией (в API это `get_session` в конце запроса).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor_id: int | None,
        action: str,
        entity_type: str,
        entity_id: int | None,
        details: dict[str, object] | None = None,
    ) -> None:
        """Добавляет запись. Ошибка базы (например, нарушение CHECK) всплывёт сразу."""
        self._session.add(
            AuditLogModel(
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details or {},
                # Связывает запись с логами запроса (X-Request-ID); вне запроса пусто.
                request_id=get_request_id(),
            )
        )
        # flush отправляет INSERT сейчас, внутри транзакции изменения: при сбое
        # откатится и сам монитор, а не только запись журнала.
        await self._session.flush()

    async def list_for_entity(
        self, entity_type: str, entity_id: int
    ) -> list[AuditEntry]:
        """История объекта от старых записей к новым."""
        result = await self._session.scalars(
            select(AuditLogModel)
            .where(
                AuditLogModel.entity_type == entity_type,
                AuditLogModel.entity_id == entity_id,
            )
            .order_by(AuditLogModel.occurred_at, AuditLogModel.id)
        )
        return [_to_domain(e) for e in result]
