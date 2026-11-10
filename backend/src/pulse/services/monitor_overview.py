from pulse.domain.monitors import MonitorWithLastCheck
from pulse.domain.pagination import Page
from pulse.domain.users import User
from pulse.repositories.checks import CheckRepository
from pulse.services.monitors import MonitorService


class MonitorOverviewService:
    """Список мониторов вместе с последней проверкой каждого.

    Права на список те же, что у `MonitorService.list_page` (админ видит всё, остальные
    своё): правило владения остаётся в одном месте.
    """

    def __init__(self, monitors: MonitorService, checks: CheckRepository) -> None:
        self._monitors = monitors
        self._checks = checks

    async def list_page(
        self, actor: User, *, limit: int, offset: int
    ) -> Page[MonitorWithLastCheck]:
        """Два запроса на страницу мониторов (список и итог) плюс один за проверками.

        Число запросов не зависит от размера страницы. Наивный вариант (по запросу
        `list_recent(monitor_id, 1)` на каждый монитор) даёт 1 + N.
        """
        page = await self._monitors.list_page(actor, limit=limit, offset=offset)
        last = await self._checks.last_for_monitors([m.id for m in page.items])
        return Page(
            [MonitorWithLastCheck.of(m, last.get(m.id)) for m in page.items],
            page.total,
        )
