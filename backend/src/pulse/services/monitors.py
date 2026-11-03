from datetime import timedelta

from pulse.domain.errors import (
    DuplicateMonitorError,
    InvalidMonitorError,
    MonitorNotFoundError,
)
from pulse.domain.monitors import Monitor, ensure_timeout_fits_interval
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository


class MonitorService:
    """Операции над мониторами: единственное место, которое знает правила.

    Роль отвечает на вопрос «какие действия разрешены», владение на вопрос «над
    какими объектами». Роль проверяет зависимость `require_role` на границе API,
    владение проверяет этот сервис: админ работает с любыми мониторами, остальные
    только со своими. Чужой монитор неотличим от несуществующего (404, а не 403):
    иначе по ответу можно перебирать чужие id (IDOR).

    Проверка «найти, потом создать» не атомарна. Пока всё выполняется в одном
    потоке event loop без `await` между шагами, гонки нет; с базой данных
    её закроет UNIQUE (owner_id, url), а эта проверка останется для понятной ошибки.
    """

    def __init__(self, repository: MonitorRepository, checks: CheckRepository) -> None:
        self._repository = repository
        self._checks = checks

    def list_all(self, actor: User) -> list[Monitor]:
        if actor.role is Role.ADMIN:
            return self._repository.list_all()
        return self._repository.list_for_owner(actor.id)

    def get(self, actor: User, monitor_id: int) -> Monitor:
        monitor = self._repository.get(monitor_id)
        if actor.role is not Role.ADMIN and monitor.owner_id != actor.id:
            raise MonitorNotFoundError(monitor_id)
        return monitor

    def create(
        self, actor: User, *, url: str, interval: timedelta, timeout: float
    ) -> Monitor:
        # Владелец всегда тот, кто создаёт: его нельзя передать из запроса.
        self._ensure_timeout_fits(interval, timeout)
        self._ensure_url_is_free(actor.id, url)
        return self._repository.create(
            owner_id=actor.id, url=url, interval=interval, timeout=timeout
        )

    def update(
        self,
        actor: User,
        monitor_id: int,
        *,
        url: str | None = None,
        interval: timedelta | None = None,
        timeout: float | None = None,
    ) -> Monitor:
        """Меняет только переданные поля; `None` означает «не менять»."""
        monitor = self.get(actor, monitor_id)  # 404 раньше, чем 409
        # Правило про сочетание полей: PATCH мог поменять только одно из них.
        self._ensure_timeout_fits(
            interval if interval is not None else monitor.interval,
            timeout if timeout is not None else monitor.timeout,
            # Виновато то поле, которое пришло в запросе; если оба, то таймаут.
            field="interval" if timeout is None and interval is not None else "timeout",
        )
        if url is not None:
            # Уникальность в рамках владельца монитора, а не того, кто правит.
            self._ensure_url_is_free(monitor.owner_id, url, allow_id=monitor_id)
        return self._repository.update(
            monitor_id, url=url, interval=interval, timeout=timeout
        )

    def delete(self, actor: User, monitor_id: int) -> None:
        self.get(actor, monitor_id)
        self._repository.delete(monitor_id)
        # Каскад: проверки удалённого монитора никому не нужны. В базе (фаза 2)
        # это сделает внешний ключ с ON DELETE CASCADE.
        self._checks.delete_for_monitor(monitor_id)

    @staticmethod
    def _ensure_timeout_fits(
        interval: timedelta, timeout: float, *, field: str = "timeout"
    ) -> None:
        try:
            ensure_timeout_fits_interval(interval, timeout)
        except ValueError as exc:
            raise InvalidMonitorError(field, str(exc)) from exc

    def _ensure_url_is_free(
        self, owner_id: int, url: str, *, allow_id: int | None = None
    ) -> None:
        # allow_id: монитор может «сменить» URL на свой же, это не дубликат.
        existing = self._repository.find_by_url(owner_id, url)
        if existing is not None and existing.id != allow_id:
            raise DuplicateMonitorError(url, existing.id)
