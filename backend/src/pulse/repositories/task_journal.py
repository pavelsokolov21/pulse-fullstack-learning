import json
import logging
from datetime import datetime

from redis.exceptions import RedisError

from pulse.domain.notifications import TaskEvent, TaskState
from pulse.repositories.redis_client import RedisClient

logger = logging.getLogger("pulse.tasks")

JOURNAL_KEY = "pulse:tasks:journal"
MAX_EVENTS = 300
# Журнал для глаз: через сутки без новых событий исчезает сам.
JOURNAL_TTL_SECONDS = 24 * 3600


def _encode(event: TaskEvent) -> str:
    return json.dumps(
        {
            "check_id": event.check_id,
            "state": event.state.value,
            "at": event.at.isoformat(),
            "attempt": event.attempt,
            "detail": event.detail,
            "retry_in": event.retry_in,
        },
        ensure_ascii=False,
    )


def _decode(raw: str | bytes) -> TaskEvent:
    data = json.loads(raw)
    return TaskEvent(
        check_id=data["check_id"],
        state=TaskState(data["state"]),
        at=datetime.fromisoformat(data["at"]),
        attempt=data["attempt"],
        detail=data["detail"],
        retry_in=data["retry_in"],
    )


class RedisTaskJournal:
    """Короткий журнал событий задач: что сейчас происходит с уведомлениями.

    Нужен, чтобы жизнь задачи можно было увидеть: результаты Celery у нас отключены,
    а логи воркера в браузер не попадают. Это не источник правды (доставкой
    управляет брокер), а витрина: список из последних `MAX_EVENTS` событий, новые
    первыми. Запись не должна ронять ни задачу, ни запрос: сбой Redis только логируется.
    """

    def __init__(
        self,
        client: RedisClient,
        key: str = JOURNAL_KEY,
        max_events: int = MAX_EVENTS,
    ) -> None:
        self._client = client
        self._key = key
        self._max_events = max_events

    async def record(self, event: TaskEvent) -> None:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.lpush(self._key, _encode(event))
                pipe.ltrim(self._key, 0, self._max_events - 1)
                pipe.expire(self._key, JOURNAL_TTL_SECONDS)
                await pipe.execute()
        except (RedisError, OSError):
            logger.warning("Журнал задач недоступен, событие потеряно", exc_info=True)

    async def recent(self, limit: int) -> list[TaskEvent]:
        """До `limit` последних событий, новые первыми. Битые записи пропускаются."""
        raw_events = await self._client.lrange(self._key, 0, limit - 1)
        events: list[TaskEvent] = []
        for raw in raw_events:
            try:
                events.append(_decode(raw))
            except (ValueError, KeyError, TypeError):
                logger.warning("Битая запись журнала задач пропущена")
        return events
