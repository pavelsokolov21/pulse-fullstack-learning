import json
import logging
from datetime import datetime
from typing import Any

from redis.exceptions import RedisError

from pulse.domain.notifications import (
    JournalEntry,
    JournalUnavailableError,
    TaskEvent,
    TaskState,
)
from pulse.repositories.redis_client import RedisClient

logger = logging.getLogger("pulse.tasks")

# Новый ключ, а не старый список `pulse:tasks:journal`: тип ключа в Redis не меняется,
# запись XADD в список упала бы с WRONGTYPE. Старый ключ исчезнет сам по TTL.
STREAM_KEY = "pulse:tasks:stream"
MAX_EVENTS = 300
# Журнал для глаз: через сутки без новых событий исчезает сам.
JOURNAL_TTL_SECONDS = 24 * 3600
FIELD = "event"


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


def _entries(rows: Any) -> list[JournalEntry]:
    """Записи потока в события. Битые пропускаются: одна запись не ломает чтение."""
    # `rows`: пары (номер, {поле: значение}). Типы redis-py для потоков неточны
    # (ответ зависит от протокола), поэтому Any.
    entries: list[JournalEntry] = []
    for entry_id, fields in rows:
        try:
            entries.append(JournalEntry(str(entry_id), _decode(fields[FIELD])))
        except (ValueError, KeyError, TypeError):
            logger.warning("Битая запись журнала задач %s пропущена", entry_id)
    return entries


class RedisTaskJournal:
    """Журнал событий задач на Redis Stream: что сейчас происходит с уведомлениями.

    Нужен, чтобы жизнь задачи можно было увидеть: результаты Celery у нас отключены,
    а логи воркера в браузер не попадают. Это не источник правды (доставкой
    управляет брокер), а витрина: последние `MAX_EVENTS` событий.

    Поток, а не Pub/Sub, ради возобновления: у каждой записи свой растущий номер
    (`1700000000000-0`), и читатель, у которого оборвалось соединение, просит всё
    «после номера N» и не теряет и не дублирует события. Pub/Sub доставляет только
    тем, кто слушает в эту секунду.

    Запись не должна ронять ни задачу, ни запрос: сбой Redis только логируется.
    Чтение при сбое бросает `JournalUnavailableError`.
    """

    def __init__(
        self,
        client: RedisClient,
        key: str = STREAM_KEY,
        max_events: int = MAX_EVENTS,
    ) -> None:
        self._client = client
        self._key = key
        self._max_events = max_events

    async def record(self, event: TaskEvent) -> None:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                # Точная обрезка (не `~`): у «примерной» длина гуляет, а для журнала на
                # сотни записей точная стоит копейки.
                pipe.xadd(
                    self._key,
                    {FIELD: _encode(event)},
                    maxlen=self._max_events,
                    approximate=False,
                )
                pipe.expire(self._key, JOURNAL_TTL_SECONDS)
                await pipe.execute()
        except (RedisError, OSError):
            logger.warning("Журнал задач недоступен, событие потеряно", exc_info=True)

    async def latest(self, limit: int) -> list[JournalEntry]:
        """До `limit` последних записей, новые первыми."""
        try:
            rows: Any = await self._client.xrevrange(self._key, count=limit)
        except (RedisError, OSError) as exc:
            raise JournalUnavailableError from exc
        return _entries(rows)

    async def after(self, entry_id: str, block_ms: int) -> list[JournalEntry]:
        """Записи новее `entry_id`, старые первыми.

        Если новых нет, ждёт до `block_ms` миллисекунд (Redis сам будит читателя, когда
        запись появится) и возвращает пустой список по таймауту. `"0-0"`: с самого
        начала потока. Ожидание должно быть короче `socket_timeout` клиента Redis.
        """
        try:
            reply: Any = await self._client.xread({self._key: entry_id}, block=block_ms)
        except (RedisError, OSError) as exc:
            raise JournalUnavailableError from exc
        if not reply:
            return []
        return _entries(reply[0][1])
