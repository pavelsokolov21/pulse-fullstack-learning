from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CheckRead(BaseModel):
    """Проверка монитора в ответе API."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    monitor_id: int
    url: str
    status_code: int | None
    latency_ms: float
    error: str | None
    checked_at: datetime
    ok: bool
