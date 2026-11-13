import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime

import pytest

from pulse.domain.notifications import (
    JournalEntry,
    JournalUnavailableError,
    TaskEvent,
    TaskState,
)
from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.repositories.task_journal import (
    FIELD,
    JOURNAL_TTL_SECONDS,
    STREAM_KEY,
    RedisTaskJournal,
)

AT = datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=UTC)
DEAD_REDIS = "redis://:x@127.0.0.1:1/0"


def event(
    check_id: int = 1,
    state: TaskState = TaskState.QUEUED,
    *,
    attempt: int | None = None,
    detail: str | None = None,
    retry_in: float | None = None,
) -> TaskEvent:
    return TaskEvent(check_id, state, AT, attempt, detail, retry_in)


@pytest.fixture
def journal(redis_client: RedisClient) -> RedisTaskJournal:
    return RedisTaskJournal(redis_client)


async def checks(entries: Awaitable[list[JournalEntry]]) -> list[int]:
    return [entry.event.check_id for entry in await entries]


# --- запись и снимок --------------------------------------------------------


async def test_empty_journal_has_no_events(journal: RedisTaskJournal) -> None:
    assert await journal.latest(10) == []


async def test_event_round_trips_with_all_fields(journal: RedisTaskJournal) -> None:
    full = event(
        7, TaskState.RETRY, attempt=2, detail="вебхук ответил 503", retry_in=3.5
    )

    await journal.record(full)

    (entry,) = await journal.latest(10)
    assert entry.event == full


async def test_optional_fields_stay_empty(journal: RedisTaskJournal) -> None:
    await journal.record(event(1, TaskState.STARTED))

    (entry,) = await journal.latest(10)

    stored = entry.event
    assert (stored.attempt, stored.detail, stored.retry_in) == (None, None, None)


async def test_latest_returns_the_newest_first(journal: RedisTaskJournal) -> None:
    for check_id in (1, 2, 3):
        await journal.record(event(check_id))

    assert await checks(journal.latest(10)) == [3, 2, 1]


async def test_latest_limit_returns_the_newest(journal: RedisTaskJournal) -> None:
    for check_id in range(1, 6):
        await journal.record(event(check_id))

    assert await checks(journal.latest(2)) == [5, 4]


async def test_entry_ids_grow(journal: RedisTaskJournal) -> None:
    for check_id in (1, 2, 3):
        await journal.record(event(check_id))

    newest, middle, oldest = await journal.latest(10)

    def key(entry_id: str) -> tuple[int, int]:
        millis, sequence = entry_id.split("-")
        return int(millis), int(sequence)

    assert key(oldest.id) < key(middle.id) < key(newest.id)


async def test_journal_keeps_only_the_latest_events(redis_client: RedisClient) -> None:
    journal = RedisTaskJournal(redis_client, max_events=3)

    for check_id in range(1, 8):
        await journal.record(event(check_id))

    assert await checks(journal.latest(100)) == [7, 6, 5]
    assert await redis_client.xlen(STREAM_KEY) == 3


async def test_journal_expires(
    journal: RedisTaskJournal, redis_client: RedisClient
) -> None:
    await journal.record(event())

    ttl = await redis_client.ttl(STREAM_KEY)

    assert 0 < ttl <= JOURNAL_TTL_SECONDS


async def test_corrupted_entries_are_skipped_not_fatal(
    journal: RedisTaskJournal,
    redis_client: RedisClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await journal.record(event(1))
    await redis_client.xadd(STREAM_KEY, {FIELD: "не json"})
    await redis_client.xadd(STREAM_KEY, {"другое": "поле"})
    await redis_client.xadd(STREAM_KEY, {FIELD: '{"check_id": 2}'})

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        entries = await journal.latest(10)

    assert [e.event.check_id for e in entries] == [1]
    assert caplog.text.count("Битая запись") == 3


# --- чтение с места обрыва --------------------------------------------------


async def test_after_returns_only_newer_entries_oldest_first(
    journal: RedisTaskJournal,
) -> None:
    for check_id in (1, 2, 3, 4):
        await journal.record(event(check_id))
    newest_first = await journal.latest(10)
    second = newest_first[2]  # запись №2

    assert await checks(journal.after(second.id, 100)) == [3, 4]


async def test_after_the_newest_entry_is_empty(journal: RedisTaskJournal) -> None:
    await journal.record(event(1))
    (newest,) = await journal.latest(1)

    assert await journal.after(newest.id, 50) == []


async def test_after_zero_reads_the_whole_stream(journal: RedisTaskJournal) -> None:
    for check_id in (1, 2):
        await journal.record(event(check_id))

    assert await checks(journal.after("0-0", 100)) == [1, 2]


async def test_after_waits_for_a_new_event_and_wakes_up(
    journal: RedisTaskJournal,
) -> None:
    loop = asyncio.get_running_loop()

    async def publish_later() -> None:
        await asyncio.sleep(0.2)
        await journal.record(event(42))

    publisher = asyncio.create_task(publish_later())
    started = loop.time()
    entries = await journal.after("0-0", 1500)
    waited = loop.time() - started
    await publisher

    assert [e.event.check_id for e in entries] == [42]
    # Разбудила запись, а не истечение ожидания.
    assert 0.1 < waited < 1.0


async def test_after_times_out_with_an_empty_list(journal: RedisTaskJournal) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()

    entries = await journal.after("0-0", 300)

    assert entries == []
    assert loop.time() - started >= 0.25


async def test_resuming_from_a_cursor_loses_and_repeats_nothing(
    journal: RedisTaskJournal,
) -> None:
    # Клиент прочитал до записи №2, соединение оборвалось, пока писались №3 и №4.
    for check_id in (1, 2):
        await journal.record(event(check_id))
    cursor = (await journal.latest(1))[0].id
    for check_id in (3, 4):
        await journal.record(event(check_id))

    resumed = await journal.after(cursor, 100)

    assert [e.event.check_id for e in resumed] == [3, 4]


# --- сбои Redis -------------------------------------------------------------


async def test_unavailable_redis_does_not_break_the_writer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Журнал витрина: из-за него нельзя уронить ни запрос, ни задачу.
    dead = create_redis(DEAD_REDIS)

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        await RedisTaskJournal(dead).record(event())
    await dead.aclose()

    assert "Журнал задач недоступен" in caplog.text


async def test_unavailable_redis_is_reported_to_the_reader() -> None:
    dead = create_redis(DEAD_REDIS)
    journal = RedisTaskJournal(dead)

    with pytest.raises(JournalUnavailableError):
        await journal.latest(10)
    with pytest.raises(JournalUnavailableError):
        await journal.after("0-0", 50)
    await dead.aclose()
