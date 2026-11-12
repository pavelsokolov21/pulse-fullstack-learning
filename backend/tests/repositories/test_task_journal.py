import json
import logging
from datetime import UTC, datetime

import pytest

from pulse.domain.notifications import TaskEvent, TaskState
from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.repositories.task_journal import (
    JOURNAL_KEY,
    JOURNAL_TTL_SECONDS,
    RedisTaskJournal,
)

AT = datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=UTC)


def event(check_id: int = 1, state: TaskState = TaskState.QUEUED, **extra: object):
    return TaskEvent(check_id, state, AT, **extra)  # type: ignore[arg-type]


@pytest.fixture
def journal(redis_client: RedisClient) -> RedisTaskJournal:
    return RedisTaskJournal(redis_client)


async def test_empty_journal_has_no_events(journal: RedisTaskJournal) -> None:
    assert await journal.recent(10) == []


async def test_event_round_trips_with_all_fields(journal: RedisTaskJournal) -> None:
    full = event(
        7, TaskState.RETRY, attempt=2, detail="вебхук ответил 503", retry_in=3.5
    )

    await journal.record(full)

    assert await journal.recent(10) == [full]


async def test_optional_fields_stay_empty(journal: RedisTaskJournal) -> None:
    await journal.record(event(1, TaskState.STARTED))

    (stored,) = await journal.recent(10)

    assert (stored.attempt, stored.detail, stored.retry_in) == (None, None, None)


async def test_newest_event_comes_first(journal: RedisTaskJournal) -> None:
    await journal.record(event(1))
    await journal.record(event(2))
    await journal.record(event(3))

    assert [e.check_id for e in await journal.recent(10)] == [3, 2, 1]


async def test_limit_returns_the_newest(journal: RedisTaskJournal) -> None:
    for check_id in range(1, 6):
        await journal.record(event(check_id))

    assert [e.check_id for e in await journal.recent(2)] == [5, 4]


async def test_journal_keeps_only_the_latest_events(
    redis_client: RedisClient,
) -> None:
    journal = RedisTaskJournal(redis_client, max_events=3)

    for check_id in range(1, 8):
        await journal.record(event(check_id))

    assert [e.check_id for e in await journal.recent(100)] == [7, 6, 5]


async def test_journal_expires(
    journal: RedisTaskJournal, redis_client: RedisClient
) -> None:
    await journal.record(event())

    ttl = await redis_client.ttl(JOURNAL_KEY)

    assert 0 < ttl <= JOURNAL_TTL_SECONDS


async def test_corrupted_entry_is_skipped_not_fatal(
    journal: RedisTaskJournal,
    redis_client: RedisClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await journal.record(event(1))
    await redis_client.lpush(JOURNAL_KEY, "не json")  # type: ignore[misc]
    await redis_client.lpush(JOURNAL_KEY, json.dumps({"check_id": 2}))  # type: ignore[misc]

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        events = await journal.recent(10)

    assert [e.check_id for e in events] == [1]
    assert "Битая запись" in caplog.text


async def test_unavailable_redis_does_not_break_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Журнал витрина: из-за него нельзя уронить ни запрос, ни задачу.
    dead = create_redis("redis://:x@127.0.0.1:1/0")

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        await RedisTaskJournal(dead).record(event())
    await dead.aclose()

    assert "Журнал задач недоступен" in caplog.text
