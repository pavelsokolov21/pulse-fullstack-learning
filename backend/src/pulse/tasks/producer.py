import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from celery import Celery

from pulse.domain.notifications import TaskEvent, TaskState
from pulse.tasks.journal import NullTaskJournal, TaskJournal
from pulse.tasks.names import NOTIFY_CHECK_FAILED

logger = logging.getLogger("pulse.tasks")


class TaskProducer(Protocol):
    """Что API умеет поставить в очередь."""

    async def notify_check_failed(self, check_id: int) -> bool: ...


class CeleryTaskProducer:
    """Ставит задачи в очередь по имени. Сбой очереди не должен ронять запрос."""

    def __init__(self, app: Celery, journal: TaskJournal | None = None) -> None:
        self._app = app
        self._journal = journal or NullTaskJournal()

    async def notify_check_failed(self, check_id: int) -> bool:
        # Время берём до отправки: воркер может начать раньше, чем мы запишем
        # событие, и на дорожке «в очереди» всё равно должно стоять первым.
        queued_at = datetime.now(UTC)
        try:
            # send_task синхронный: открывает соединение с брокером и ждёт. Прямо в
            # async-обработчике он заморозил бы цикл событий для всех запросов, поэтому
            # уходит в поток.
            await asyncio.to_thread(
                self._app.send_task, NOTIFY_CHECK_FAILED, args=[check_id]
            )
        except Exception:
            # Проверка уже записана, её результат пользователю важнее. Уведомление
            # при этом потеряно: надёжно это решает outbox (запись в той же
            # транзакции, что и проверка, и отдельный отправитель).
            logger.exception("Не удалось поставить уведомление %s в очередь", check_id)
            await self._journal.record(
                TaskEvent(
                    check_id,
                    TaskState.FAILED,
                    queued_at,
                    detail="не удалось поставить в очередь",
                )
            )
            return False
        await self._journal.record(TaskEvent(check_id, TaskState.QUEUED, queued_at))
        return True

    def close(self) -> None:
        self._app.close()


class NullTaskProducer:
    """Очередь не настроена (нет адреса брокера): задачи не ставятся."""

    async def notify_check_failed(self, check_id: int) -> bool:
        logger.debug("Брокер не настроен, уведомление %s не поставлено", check_id)
        return False
