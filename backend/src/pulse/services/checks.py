import httpx

from pulse.checks.models import CheckResult
from pulse.checks.runner import check_url
from pulse.core.clock import Clock, utc_now
from pulse.domain.checks import Check
from pulse.domain.users import User
from pulse.repositories.checks import CheckRepository
from pulse.services.monitors import MonitorService


class CheckService:
    """Запуск проверок мониторов и чтение их истории.

    Эндпоинт запуска ходит по адресу, который прислал пользователь (SSRF): в
    учебном проекте это допустимо, в боевом нужен запрет внутренних адресов.
    """

    def __init__(
        self,
        monitors: MonitorService,
        checks: CheckRepository,
        client: httpx.AsyncClient,
        clock: Clock = utc_now,
    ) -> None:
        self._monitors = monitors
        self._checks = checks
        self._client = client
        self._clock = clock

    async def run(self, actor: User, monitor_id: int) -> Check:
        """Проверяет монитор и сохраняет результат.

        Если монитор удалили, пока шёл запрос, результат не сохраняется (иначе
        остались бы проверки несуществующего монитора) и поднимается
        `MonitorNotFoundError`.
        """
        monitor = self._monitors.get(actor, monitor_id)

        try:
            result = await check_url(self._client, monitor.url, timeout=monitor.timeout)
        except ValueError as exc:
            # URL монитора уже нормализован, так что сюда попадают только сбои,
            # которых быть не должно. Фиксируем их как неудачную проверку, как
            # это делает check_many, а не отдаём клиенту 500.
            result = CheckResult(
                url=monitor.url,
                status_code=None,
                latency_ms=0.0,
                error=f"Invalid URL: {exc}",
            )

        self._monitors.get(actor, monitor_id)  # за время await монитор мог исчезнуть

        return self._checks.add(
            monitor_id=monitor_id,
            url=result.url,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            error=result.error,
            checked_at=self._clock(),
            ok=result.ok,
        )

    def list_recent(self, actor: User, monitor_id: int, limit: int) -> list[Check]:
        self._monitors.get(actor, monitor_id)
        return self._checks.list_recent(monitor_id, limit)
