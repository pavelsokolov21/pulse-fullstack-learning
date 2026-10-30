from collections import deque
from datetime import datetime
from itertools import count

from pulse.domain.checks import Check

MAX_CHECKS_PER_MONITOR = 100


class CheckRepository:
    """Последние проверки мониторов в памяти.

    На монитор хранится не больше `MAX_CHECKS_PER_MONITOR` проверок: старые
    вытесняются. Как и `MonitorRepository`, не потокобезопасен и рассчитан на
    вызовы из одного потока event loop.
    """

    def __init__(self) -> None:
        self._checks: dict[int, deque[Check]] = {}
        self._ids = count(1)

    def add(
        self,
        *,
        monitor_id: int,
        url: str,
        status_code: int | None,
        latency_ms: float,
        error: str | None,
        checked_at: datetime,
        ok: bool,
    ) -> Check:
        check = Check(
            id=next(self._ids),
            monitor_id=monitor_id,
            url=url,
            status_code=status_code,
            latency_ms=latency_ms,
            error=error,
            checked_at=checked_at,
            ok=ok,
        )
        window = self._checks.setdefault(
            monitor_id, deque[Check](maxlen=MAX_CHECKS_PER_MONITOR)
        )
        window.append(check)
        return check

    def list_recent(self, monitor_id: int, limit: int) -> list[Check]:
        """До `limit` последних проверок монитора, новые первыми."""
        window = self._checks.get(monitor_id, ())
        return list(reversed(window))[:limit]

    def delete_for_monitor(self, monitor_id: int) -> None:
        self._checks.pop(monitor_id, None)
