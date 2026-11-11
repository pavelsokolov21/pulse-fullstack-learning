from datetime import datetime

from pulse.core.clock import Clock, utc_now
from pulse.domain.reports import MonitorReport, ReportResult, ReportStats, ReportWindow
from pulse.domain.users import User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.report_cache import ReportCache
from pulse.services.monitors import MonitorService

UPTIME_DIGITS = 3


class ReportService:
    """Отчёт монитора за окно: аптайм и задержки, с кешем.

    Права проверяются ДО кеша: закешированный отчёт чужого монитора не должен
    отдаваться только потому, что он уже лежит в Redis.
    """

    def __init__(
        self,
        monitors: MonitorService,
        checks: CheckRepository,
        cache: ReportCache,
        clock: Clock = utc_now,
    ) -> None:
        self._monitors = monitors
        self._checks = checks
        self._cache = cache
        self._clock = clock

    async def get(
        self, actor: User, monitor_id: int, window: ReportWindow
    ) -> ReportResult:
        await self._monitors.get(actor, monitor_id)  # 404 для чужого и несуществующего

        lookup = await self._cache.get(monitor_id, window)
        if lookup.report is not None:
            return ReportResult(lookup.report, cached=True)

        now = self._clock()
        stats = await self._checks.stats(monitor_id, now - window.delta)
        report = _build(monitor_id, window, stats, now)
        if lookup.version is not None:
            await self._cache.put(monitor_id, window, report, lookup.version)
        return ReportResult(report, cached=False)


def _build(
    monitor_id: int, window: ReportWindow, stats: ReportStats, now: datetime
) -> MonitorReport:
    uptime = round(stats.ok / stats.total * 100, UPTIME_DIGITS) if stats.total else None
    return MonitorReport(
        monitor_id=monitor_id,
        window=window,
        total=stats.total,
        ok=stats.ok,
        uptime_percent=uptime,
        avg_latency_ms=stats.avg_latency_ms,
        p95_latency_ms=stats.p95_latency_ms,
        generated_at=now,
    )
