from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditEntry:
    """Запись журнала: кто (`actor_id`), что (`action`), над чем и когда.

    `actor_id` пуст, если пользователя потом удалили или действие совершила система.
    `details` это снимок значимых полей в момент действия: журнал не должен зависеть
    от того, что объект уже изменён или удалён.
    """

    id: int
    occurred_at: datetime
    actor_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    details: dict[str, object]
    request_id: str | None
