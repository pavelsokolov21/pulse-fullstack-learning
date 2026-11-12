"""Что задача и продьюсер пишут в журнал, который показывает страница с задачами."""

import asyncio
import logging
from typing import Any, cast

import pytest
from celery import Celery

import pulse.tasks.notifications as task_module
from pulse.domain.notifications import (
    DeliveryOutcome,
    PermanentDeliveryError,
    RetryableDeliveryError,
    TaskEvent,
    TaskState,
)
from pulse.repositories.redis_client import RedisClient
from pulse.repositories.task_journal import RedisTaskJournal
from pulse.tasks.notifications import MAX_RETRIES, notify_check_failed
from pulse.tasks.producer import CeleryTaskProducer

# До автоматической подмены из conftest: настоящая функция записи.
REAL_JOURNAL = task_module._journal  # pyright: ignore[reportPrivateUsage]


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[int, TaskState, int, dict[str, Any]]] = []

    def __call__(
        self, check_id: int, state: TaskState, attempt: int, **extra: Any
    ) -> None:
        self.events.append((check_id, state, attempt, extra))

    @property
    def states(self) -> list[TaskState]:
        return [state for _, state, _, _ in self.events]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(task_module, "_journal", recorder)
    return recorder


def delay_of(seconds: float):
    def fake(*args: Any, **kwargs: Any) -> float:
        return seconds

    return fake


def deliver_script(
    monkeypatch: pytest.MonkeyPatch, *script: Exception | DeliveryOutcome
) -> None:
    steps = list(script)

    async def deliver(check_id: int) -> DeliveryOutcome:
        step = steps.pop(0) if steps else DeliveryOutcome.SENT
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(task_module, "_deliver", deliver)
    monkeypatch.setattr(task_module, "backoff_delay", delay_of(0.0))


def run(check_id: int = 5):
    return notify_check_failed.apply(args=(check_id,))


def test_success_is_started_then_sent(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    deliver_script(monkeypatch, DeliveryOutcome.SENT)

    run()

    assert recorder.states == [TaskState.STARTED, TaskState.SENT]
    assert {check_id for check_id, *_ in recorder.events} == {5}


@pytest.mark.parametrize(
    ("outcome", "state"),
    [
        (DeliveryOutcome.DUPLICATE, TaskState.DUPLICATE),
        (DeliveryOutcome.SKIPPED, TaskState.SKIPPED),
    ],
)
def test_other_outcomes_have_their_own_state(
    monkeypatch: pytest.MonkeyPatch,
    recorder: Recorder,
    outcome: DeliveryOutcome,
    state: TaskState,
) -> None:
    deliver_script(monkeypatch, outcome)

    run()

    assert recorder.states == [TaskState.STARTED, state]


def test_retry_records_the_reason_the_delay_and_the_attempt(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    deliver_script(monkeypatch, RetryableDeliveryError("вебхук ответил 503"))
    monkeypatch.setattr(task_module, "backoff_delay", delay_of(4.5))

    run()

    assert recorder.states == [
        TaskState.STARTED,
        TaskState.RETRY,
        TaskState.STARTED,
        TaskState.SENT,
    ]
    retry = recorder.events[1]
    assert retry[2] == 1
    assert retry[3] == {"detail": "вебхук ответил 503", "retry_in": 4.5}
    # Номер попытки на дорожке растёт вместе с повторами.
    assert [attempt for _, _, attempt, _ in recorder.events] == [1, 1, 2, 2]


def test_exhausted_retries_end_with_a_single_failure_and_no_fake_retry(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    deliver_script(monkeypatch, *[RetryableDeliveryError("down")] * 20)

    run()

    # Повторов пять, шестая попытка последняя: «повтор через N с» после неё соврал бы.
    assert recorder.states.count(TaskState.RETRY) == MAX_RETRIES
    assert recorder.states.count(TaskState.STARTED) == MAX_RETRIES + 1
    assert recorder.states[-1] == TaskState.FAILED
    assert recorder.states.count(TaskState.FAILED) == 1
    failure = recorder.events[-1]
    assert failure[2] == MAX_RETRIES + 1
    assert failure[3]["detail"] == "down"


def test_permanent_error_is_recorded_as_a_failure_without_retries(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    deliver_script(monkeypatch, PermanentDeliveryError("вебхук отказал: 403"))

    run()

    assert recorder.states == [TaskState.STARTED, TaskState.FAILED]
    assert "вебхук отказал: 403" in recorder.events[-1][3]["detail"]


def test_bug_in_the_task_is_recorded_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    deliver_script(monkeypatch, TypeError("баг"))

    run()

    assert recorder.states == [TaskState.STARTED, TaskState.FAILED]
    assert recorder.events[-1][3]["detail"] == "баг"


def test_broken_journal_does_not_fail_the_task(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    deliver_script(monkeypatch, DeliveryOutcome.SENT)

    async def broken(event: TaskEvent) -> None:
        raise RuntimeError("redis сломался")

    monkeypatch.setattr(task_module, "_journal", REAL_JOURNAL)
    monkeypatch.setattr(task_module, "_record", broken)

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        result = run()

    assert (result.state, result.result) == ("SUCCESS", "sent")
    assert "Не удалось записать событие задачи" in caplog.text


async def test_real_journal_write_reaches_redis(
    monkeypatch: pytest.MonkeyPatch, redis_client: RedisClient
) -> None:
    # Без подмен: тот же путь, что у воркера (свой клиент Redis, свой цикл событий).
    monkeypatch.setattr(task_module, "_journal", REAL_JOURNAL)
    await asyncio.to_thread(
        REAL_JOURNAL, 9, TaskState.RETRY, 2, detail="503", retry_in=1.5
    )

    (stored,) = await RedisTaskJournal(redis_client).recent(10)
    assert (stored.check_id, stored.state, stored.attempt) == (9, TaskState.RETRY, 2)
    assert (stored.detail, stored.retry_in) == ("503", 1.5)


# --- продьюсер -------------------------------------------------------------


class MemoryJournal:
    def __init__(self) -> None:
        self.events: list[TaskEvent] = []

    async def record(self, event: TaskEvent) -> None:
        self.events.append(event)

    async def recent(self, limit: int) -> list[TaskEvent]:
        return self.events[:limit]


class App:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def send_task(self, name: str, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error


async def test_producer_records_that_the_task_is_queued() -> None:
    journal = MemoryJournal()

    await CeleryTaskProducer(cast(Celery, App()), journal).notify_check_failed(42)

    (queued,) = journal.events
    assert (queued.check_id, queued.state) == (42, TaskState.QUEUED)
    assert queued.at.tzinfo is not None


async def test_producer_records_a_failure_when_the_broker_is_down() -> None:
    journal = MemoryJournal()
    producer = CeleryTaskProducer(cast(Celery, App(ConnectionError("down"))), journal)

    sent = await producer.notify_check_failed(42)

    assert sent is False
    (failed,) = journal.events
    assert (failed.check_id, failed.state) == (42, TaskState.FAILED)
    assert failed.detail == "не удалось поставить в очередь"
