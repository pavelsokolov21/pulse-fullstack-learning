from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class ReportWindow(StrEnum):
    """Окно отчёта: за какой срок считать. Значения это то, что видит клиент API."""

    DAY = "24h"
    WEEK = "7d"
    MONTH = "30d"

    @property
    def delta(self) -> timedelta:
        return {
            ReportWindow.DAY: timedelta(hours=24),
            ReportWindow.WEEK: timedelta(days=7),
            ReportWindow.MONTH: timedelta(days=30),
        }[self]


@dataclass(frozen=True)
class ReportStats:
    """Сырые итоги по проверкам за окно (то, что считает база)."""

    total: int
    ok: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None


@dataclass(frozen=True)
class MonitorReport:
    """Отчёт монитора: аптайм и задержки за окно.

    Задержки считаются только по проверкам, на которые пришёл ответ: у сетевой
    ошибки «задержка» это время до таймаута, и она исказила бы p95. `None` значит
    «нечего считать» (нет проверок или нет ответов), а не ноль.
    """

    monitor_id: int
    window: ReportWindow
    total: int
    ok: int
    uptime_percent: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    generated_at: datetime


@dataclass(frozen=True)
class ReportResult:
    report: MonitorReport
    cached: bool  # True: отдан из кеша, базу за отчётом не спрашивали
