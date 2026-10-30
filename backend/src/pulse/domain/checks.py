from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Check:
    """Результат одной проверки монитора.

    `url` хранится в самой проверке: это снимок адреса, который проверяли. Если
    монитору потом сменят URL через PATCH, старые проверки не станут относиться
    к чужому сайту. В базе (фаза 2) это обычная денормализация ради истории.
    """

    id: int
    monitor_id: int
    url: str
    status_code: int | None
    latency_ms: float
    error: str | None
    checked_at: datetime
    ok: bool
