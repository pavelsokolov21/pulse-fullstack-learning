import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from fastapi.sse import KEEPALIVE_COMMENT, format_sse_event

from pulse.api.deps import (
    LastEventIdHeader,
    TaskJournalDep,
    get_current_user,
    require_writer,
)
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.tasks import TaskEventRead
from pulse.domain.notifications import JournalEntry, JournalUnavailableError
from pulse.tasks.journal import TaskJournal

logger = logging.getLogger("pulse.tasks")

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_writer)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

DEFAULT_LIMIT = 100
MAX_LIMIT = 300
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

# Сколько записей дать новому подписчику, чтобы страница сразу была не пустой.
SNAPSHOT_SIZE = 100
# Как часто заглядываем в журнал и проверяем, не пора ли закрыться. Меньше
# `socket_timeout` клиента Redis (2 с): иначе ожидание оборвётся ошибкой.
BLOCK_MS = 1000
# Пауза без событий, после которой шлём комментарий `: ping`. Без него прокси и
# браузеры закрывают «молчащее» соединение.
KEEPALIVE_SECONDS = 15.0
# Поток закрывается сам и клиент переподключается. Так отозванный доступ и протухший
# токен не живут в открытом соединении дольше этого срока (токен проверяется один
# раз, при подключении).
MAX_STREAM_SECONDS = 300.0
# Номер записи Redis Stream: `<миллисекунды>-<порядковый>`.
ENTRY_ID = re.compile(r"^\d+-\d+$")


@router.get("/events")
async def list_task_events(
    journal: TaskJournalDep, limit: Limit = DEFAULT_LIMIT
) -> list[TaskEventRead]:
    """Снимок: последние события фоновых задач, новые первыми.

    Журнал общий для всех пользователей и хранит только номера проверок и
    причины сбоев, поэтому читать его могут те же роли, что запускают проверки.
    """
    entries = await journal.latest(limit)
    return [TaskEventRead.model_validate(e.event) for e in entries]


def _frame(entry: JournalEntry) -> bytes:
    payload = TaskEventRead.model_validate(entry.event).model_dump_json()
    return format_sse_event(data_str=payload, event="task", id=entry.id)


async def _events(journal: TaskJournal, last_id: str | None) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    started = last_sent = loop.time()
    # Первый байт уходит сразу: прокси и браузер видят, что поток открыт, а не ждут
    # первого события.
    yield format_sse_event(comment="stream open")
    try:
        if last_id is None:
            # Новый подписчик: сначала снимок (старые первыми), дальше живые события.
            snapshot = await journal.latest(SNAPSHOT_SIZE)
            for entry in reversed(snapshot):
                yield _frame(entry)
            cursor = snapshot[0].id if snapshot else "0-0"
        else:
            # Переподключение: продолжаем с места обрыва, без дыр и без повторов.
            cursor = last_id
        while loop.time() - started < MAX_STREAM_SECONDS:
            entries = await journal.after(cursor, BLOCK_MS)
            for entry in entries:
                yield _frame(entry)
                cursor = entry.id
            now = loop.time()
            if entries:
                last_sent = now
            elif now - last_sent >= KEEPALIVE_SECONDS:
                yield KEEPALIVE_COMMENT
                last_sent = now
    except JournalUnavailableError:
        # Заголовки уже ушли, статус не поменять: закрываем поток, клиент
        # переподключится с паузой и продолжит с того же номера.
        logger.warning("Журнал задач недоступен, поток закрыт", exc_info=True)


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Поток Server-Sent Events: событие `task` с JSON `TaskEventRead` в "
                "`data` и номером записи в `id`."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_task_events(
    journal: TaskJournalDep, last_event_id: LastEventIdHeader = None
) -> StreamingResponse:
    """События фоновых задач в реальном времени (SSE).

    Без заголовка `Last-Event-ID` сначала приходит снимок последних событий, потом
    живые. С заголовком поток продолжается после этого номера. Через
    `MAX_STREAM_SECONDS` сервер закрывает поток, клиент переподключается.

    Обработчик возвращает ответ сразу, а зависимости со `scope="function"` (сессия
    базы при проверке токена) закрываются до начала потока: соединение с базой не
    висит все минуты, пока открыт поток.
    """
    last_id = last_event_id if last_event_id and ENTRY_ID.match(last_event_id) else None
    return StreamingResponse(
        _events(journal, last_id),
        media_type="text/event-stream",
        headers={
            # `no-transform`: без него сжатие в прокси (Next.js) копит ответ и
            # события приходят пачками. Nginx читает X-Accel-Buffering.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
