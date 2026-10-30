from datetime import timedelta

from pulse.domain.errors import DuplicateMonitorError
from pulse.domain.monitors import Monitor
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository


class MonitorService:
    """Операции над мониторами: единственное место, которое знает правила.

    Правила, которым нет места ни в роутере (HTTP), ни в репозитории (хранение),
    живут здесь. Сейчас это запрет дубликатов URL; дальше, например, лимит
    мониторов на пользователя.

    Проверка «найти, потом создать» не атомарна. Пока всё выполняется в одном
    потоке event loop без `await` между шагами, гонки нет; с базой данных
    её закроет UNIQUE-ограничение, а эта проверка останется для понятной ошибки.
    """

    def __init__(self, repository: MonitorRepository, checks: CheckRepository) -> None:
        self._repository = repository
        self._checks = checks

    def list_all(self) -> list[Monitor]:
        return self._repository.list_all()

    def get(self, monitor_id: int) -> Monitor:
        return self._repository.get(monitor_id)

    def create(self, *, url: str, interval: timedelta, timeout: float) -> Monitor:
        self._ensure_url_is_free(url)
        return self._repository.create(url=url, interval=interval, timeout=timeout)

    def update(
        self,
        monitor_id: int,
        *,
        url: str | None = None,
        interval: timedelta | None = None,
        timeout: float | None = None,
    ) -> Monitor:
        """Меняет только переданные поля; `None` означает «не менять»."""
        self._repository.get(monitor_id)  # 404 раньше, чем 409
        if url is not None:
            self._ensure_url_is_free(url, allow_id=monitor_id)
        return self._repository.update(
            monitor_id, url=url, interval=interval, timeout=timeout
        )

    def delete(self, monitor_id: int) -> None:
        self._repository.delete(monitor_id)
        # Каскад: проверки удалённого монитора никому не нужны. В базе (фаза 2)
        # это сделает внешний ключ с ON DELETE CASCADE.
        self._checks.delete_for_monitor(monitor_id)

    def _ensure_url_is_free(self, url: str, *, allow_id: int | None = None) -> None:
        # allow_id: монитор может «сменить» URL на свой же, это не дубликат.
        existing = self._repository.find_by_url(url)
        if existing is not None and existing.id != allow_id:
            raise DuplicateMonitorError(url, existing.id)
