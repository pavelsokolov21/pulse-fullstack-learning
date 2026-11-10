from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Incident:
    """Серия неудачных проверок монитора; `resolved_at` пуст, пока инцидент открыт.

    `failed_checks` растёт с каждой неудачной проверкой серии. `opened_by_check_id` и
    `resolved_by_check_id` пустеют, когда соответствующую проверку удаляет чистка
    истории: инцидент при этом остаётся. `version` растёт при каждой записи счётчика
    через `set_failed_checks_if_version`: по ней видно, что строку успели изменить.
    """

    id: int
    monitor_id: int
    started_at: datetime
    resolved_at: datetime | None
    failed_checks: int
    last_error: str | None
    opened_by_check_id: int | None
    resolved_by_check_id: int | None
    version: int
