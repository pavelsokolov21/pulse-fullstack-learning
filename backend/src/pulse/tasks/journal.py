from typing import Protocol

from pulse.domain.notifications import TaskEvent


class TaskJournal(Protocol):
    """Куда складываются и откуда читаются события задач (для страницы с задачами)."""

    async def record(self, event: TaskEvent) -> None: ...

    async def recent(self, limit: int) -> list[TaskEvent]: ...


class NullTaskJournal:
    """Очередь не настроена: журнал пуст."""

    async def record(self, event: TaskEvent) -> None:
        return None

    async def recent(self, limit: int) -> list[TaskEvent]:
        return []
