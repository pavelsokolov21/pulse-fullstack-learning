import asyncio
from typing import Protocol

from pulse.domain.notifications import JournalEntry, TaskEvent


class TaskJournal(Protocol):
    """Куда складываются и откуда читаются события задач (для страницы с задачами)."""

    async def record(self, event: TaskEvent) -> None: ...

    async def latest(self, limit: int) -> list[JournalEntry]: ...

    async def after(self, entry_id: str, block_ms: int) -> list[JournalEntry]: ...


class NullTaskJournal:
    """Очередь не настроена: журнал пуст."""

    async def record(self, event: TaskEvent) -> None:
        return None

    async def latest(self, limit: int) -> list[JournalEntry]:
        return []

    async def after(self, entry_id: str, block_ms: int) -> list[JournalEntry]:
        # Как у настоящего журнала: ждёт и возвращает пустой список. Без паузы цикл
        # чтения крутился бы вхолостую.
        await asyncio.sleep(block_ms / 1000)
        return []
