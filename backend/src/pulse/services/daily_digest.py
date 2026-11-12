import logging
from datetime import timedelta

from pulse.core.clock import Clock, utc_now
from pulse.domain.reports import DailyDigest, DigestLine
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository

logger = logging.getLogger("pulse.tasks")

PAGE_SIZE = 500
WORST_COUNT = 5
WINDOW = timedelta(hours=24)


class DailyDigestService:
    """Ночная сводка: аптайм по всем мониторам за сутки.

    Два запроса на всю сводку (проверки группой + страницы мониторов за адресами), а не
    по запросу на каждый монитор.
    """

    def __init__(
        self,
        monitors: MonitorRepository,
        checks: CheckRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._monitors = monitors
        self._checks = checks
        self._clock = clock

    async def build(self) -> DailyDigest:
        since = self._clock() - WINDOW
        uptime = {u.monitor_id: u for u in await self._checks.uptime_by_monitor(since)}

        lines: list[DigestLine] = []
        monitors = 0
        offset = 0
        while True:
            page = await self._monitors.list_page(
                owner_id=None, limit=PAGE_SIZE, offset=offset
            )
            for monitor in page.items:
                monitors += 1
                found = uptime.get(monitor.id)
                if found is not None and found.total:
                    lines.append(
                        DigestLine(monitor.id, monitor.url, found.total, found.ok)
                    )
            offset += PAGE_SIZE
            if offset >= page.total:
                break

        worst = sorted(lines, key=lambda line: (line.uptime_percent, line.monitor_id))
        return DailyDigest(
            since=since,
            monitors=monitors,
            total=sum(line.total for line in lines),
            ok=sum(line.ok for line in lines),
            worst=worst[:WORST_COUNT],
        )
