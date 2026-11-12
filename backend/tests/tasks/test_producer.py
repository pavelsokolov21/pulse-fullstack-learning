import logging
import threading
from typing import Any, cast

import pytest
from celery import Celery

from pulse.tasks.names import NOTIFY_CHECK_FAILED
from pulse.tasks.producer import CeleryTaskProducer, NullTaskProducer


class FakeApp:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.thread: int | None = None
        self.closed = False

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.thread = threading.get_ident()
        if self.error is not None:
            raise self.error
        self.calls.append((name, kwargs))

    def close(self) -> None:
        self.closed = True


def producer(app: FakeApp) -> CeleryTaskProducer:
    return CeleryTaskProducer(cast(Celery, app))


async def test_task_is_sent_by_name_with_the_check_id() -> None:
    app = FakeApp()

    sent = await producer(app).notify_check_failed(42)

    assert sent is True
    assert app.calls == [(NOTIFY_CHECK_FAILED, {"args": [42]})]


async def test_blocking_publish_does_not_run_in_the_event_loop_thread() -> None:
    # send_task синхронный и ходит в сеть: в потоке цикла событий он замораживал бы
    # все остальные запросы на время обращения к брокеру.
    app = FakeApp()

    await producer(app).notify_check_failed(1)

    assert app.thread is not None
    assert app.thread != threading.get_ident()


async def test_broker_failure_is_reported_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FakeApp(error=ConnectionError("broker down"))

    with caplog.at_level(logging.ERROR, logger="pulse.tasks"):
        sent = await producer(app).notify_check_failed(42)

    assert sent is False
    assert "Не удалось поставить уведомление 42" in caplog.text


def test_close_closes_the_celery_app() -> None:
    app = FakeApp()

    producer(app).close()

    assert app.closed


async def test_null_producer_sends_nothing() -> None:
    assert await NullTaskProducer().notify_check_failed(1) is False
