from datetime import datetime

from pydantic import BaseModel, ConfigDict

from pulse.domain.reports import ReportWindow


class MonitorReportRead(BaseModel):
    """Отчёт монитора за окно. Пустые значения (`null`) означают «нечего считать»."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    monitor_id: int
    window: ReportWindow
    total: int
    ok: int
    uptime_percent: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    generated_at: datetime
