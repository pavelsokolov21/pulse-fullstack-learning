from itertools import count

from pulse.monitors.errors import MonitorNotFoundError
from pulse.monitors.models import MonitorCreate, MonitorRead, MonitorUpdate


class MonitorStore:
    """Хранилище мониторов в памяти (до появления базы в фазе 2).

    Не потокобезопасно: методы не делают `await` и вызываются из `async def`
    обработчиков, то есть выполняются в одном потоке event loop по очереди.
    """

    def __init__(self) -> None:
        self._monitors: dict[int, MonitorRead] = {}
        self._ids = count(1)

    def list(self) -> list[MonitorRead]:
        return list(self._monitors.values())

    def get(self, monitor_id: int) -> MonitorRead:
        try:
            return self._monitors[monitor_id]
        except KeyError:
            raise MonitorNotFoundError(monitor_id) from None

    def create(self, data: MonitorCreate) -> MonitorRead:
        monitor = MonitorRead(id=next(self._ids), **data.model_dump())
        self._monitors[monitor.id] = monitor
        return monitor

    def update(self, monitor_id: int, data: MonitorUpdate) -> MonitorRead:
        current = self.get(monitor_id)
        # Поля уже проверены в MonitorUpdate, повторная валидация не нужна.
        updated = current.model_copy(update=data.model_dump(exclude_unset=True))
        self._monitors[monitor_id] = updated
        return updated

    def delete(self, monitor_id: int) -> None:
        self.get(monitor_id)
        del self._monitors[monitor_id]
