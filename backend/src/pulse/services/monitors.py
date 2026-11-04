from datetime import timedelta

from pulse.domain.errors import (
    InvalidMonitorError,
    MonitorNotFoundError,
)
from pulse.domain.monitors import (
    Monitor,
    MonitorWithOwner,
    ensure_timeout_fits_interval,
)
from pulse.domain.pagination import Page
from pulse.domain.users import Role, User
from pulse.repositories.monitors import MonitorRepository


class MonitorService:
    """Операции над мониторами: единственное место, которое знает правила.

    Роль отвечает на вопрос «какие действия разрешены», владение на вопрос «над
    какими объектами». Роль проверяет зависимость `require_role` на границе API,
    владение проверяет этот сервис: админ работает с любыми мониторами, остальные
    только со своими. Чужой монитор неотличим от несуществующего (404, а не 403):
    иначе по ответу можно перебирать чужие id (IDOR).

    Уникальность URL в рамках владельца отдельно не проверяется: «найти, потом
    создать» не атомарно, и два параллельных запроса оба прошли бы проверку. Дубликат
    ловит UNIQUE (owner_id, url) в базе, а репозиторий поднимает
    `DuplicateMonitorError`.
    Каскадное удаление проверок монитора тоже на стороне базы (ON DELETE CASCADE).
    """

    def __init__(self, repository: MonitorRepository) -> None:
        self._repository = repository

    async def list_page(
        self, actor: User, *, limit: int, offset: int
    ) -> Page[MonitorWithOwner]:
        """Страница списка: админ видит все мониторы, остальные свои."""
        owner_id = None if actor.role is Role.ADMIN else actor.id
        return await self._repository.list_page(
            owner_id=owner_id, limit=limit, offset=offset
        )

    async def get(self, actor: User, monitor_id: int) -> Monitor:
        monitor = await self._repository.get(monitor_id)
        if actor.role is not Role.ADMIN and monitor.owner_id != actor.id:
            raise MonitorNotFoundError(monitor_id)
        return monitor

    async def create(
        self, actor: User, *, url: str, interval: timedelta, timeout: float
    ) -> Monitor:
        # Владелец всегда тот, кто создаёт: его нельзя передать из запроса.
        self._ensure_timeout_fits(interval, timeout)
        return await self._repository.create(
            owner_id=actor.id, url=url, interval=interval, timeout=timeout
        )

    async def update(
        self,
        actor: User,
        monitor_id: int,
        *,
        url: str | None = None,
        interval: timedelta | None = None,
        timeout: float | None = None,
    ) -> Monitor:
        """Меняет только переданные поля; `None` означает «не менять»."""
        monitor = await self.get(actor, monitor_id)  # 404 раньше, чем 409
        # Правило про сочетание полей: PATCH мог поменять только одно из них.
        self._ensure_timeout_fits(
            interval if interval is not None else monitor.interval,
            timeout if timeout is not None else monitor.timeout,
            # Виновато то поле, которое пришло в запросе; если оба, то таймаут.
            field="interval" if timeout is None and interval is not None else "timeout",
        )
        # Уникальность URL в рамках владельца монитора, а не того, кто правит:
        # репозиторий проверяет её по самому монитору.
        return await self._repository.update(
            monitor_id, url=url, interval=interval, timeout=timeout
        )

    async def delete(self, actor: User, monitor_id: int) -> None:
        await self.get(actor, monitor_id)
        await self._repository.delete(monitor_id)

    @staticmethod
    def _ensure_timeout_fits(
        interval: timedelta, timeout: float, *, field: str = "timeout"
    ) -> None:
        try:
            ensure_timeout_fits_interval(interval, timeout)
        except ValueError as exc:
            raise InvalidMonitorError(field, str(exc)) from exc
