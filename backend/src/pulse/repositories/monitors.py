from dataclasses import replace
from datetime import timedelta
from itertools import count

from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor


class MonitorRepository:
    """Хранилище мониторов в памяти (до появления базы в фазе 2).

    Не потокобезопасно: методы не делают `await` и вызываются из `async def`
    обработчиков, то есть выполняются в одном потоке event loop по очереди.
    Значения принимаются уже проверенными: валидация живёт на границе (схемы API).
    """

    def __init__(self) -> None:
        self._monitors: dict[int, Monitor] = {}
        self._ids = count(1)

    # Не `list`: имя метода затеняет встроенный `list` в теле класса, и аннотации
    # `list[...]` ниже по классу ломаются (TypeError), а pyright молчит.
    def list_all(self) -> list[Monitor]:
        return list(self._monitors.values())

    def list_for_owner(self, owner_id: int) -> list[Monitor]:
        """Мониторы одного владельца. Фильтр здесь, а не в сервисе: в базе это WHERE."""
        return [m for m in self._monitors.values() if m.owner_id == owner_id]

    def get(self, monitor_id: int) -> Monitor:
        try:
            return self._monitors[monitor_id]
        except KeyError:
            raise MonitorNotFoundError(monitor_id) from None

    def find_by_url(self, owner_id: int, url: str) -> Monitor | None:
        """Монитор владельца с точно таким URL (URL уже нормализован) или `None`."""
        return next(
            (
                m
                for m in self._monitors.values()
                if m.owner_id == owner_id and m.url == url
            ),
            None,
        )

    def create(
        self, *, owner_id: int, url: str, interval: timedelta, timeout: float
    ) -> Monitor:
        monitor = Monitor(next(self._ids), owner_id, url, interval, timeout)
        self._monitors[monitor.id] = monitor
        return monitor

    def update(
        self,
        monitor_id: int,
        *,
        url: str | None = None,
        interval: timedelta | None = None,
        timeout: float | None = None,
    ) -> Monitor:
        """Меняет только переданные поля; `None` означает «не менять»."""
        current = self.get(monitor_id)
        updated = replace(
            current,
            url=current.url if url is None else url,
            interval=current.interval if interval is None else interval,
            timeout=current.timeout if timeout is None else timeout,
        )
        self._monitors[monitor_id] = updated
        return updated

    def delete(self, monitor_id: int) -> None:
        self.get(monitor_id)
        del self._monitors[monitor_id]
