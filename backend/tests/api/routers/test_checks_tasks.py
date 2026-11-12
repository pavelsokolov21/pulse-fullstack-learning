"""Запуск проверки ставит в очередь уведомление, но только о неудачной."""

from collections.abc import Callable, Iterator
from typing import cast

import httpx
import pytest
from celery import Celery
from fastapi.testclient import TestClient

from pulse.api.deps import get_http_client, get_task_producer
from pulse.api.main import app
from pulse.tasks.producer import CeleryTaskProducer, TaskProducer

Handler = Callable[[httpx.Request], httpx.Response]


class RecordingProducer:
    def __init__(self) -> None:
        self.check_ids: list[int] = []

    async def notify_check_failed(self, check_id: int) -> bool:
        self.check_ids.append(check_id)
        return True


class BrokenApp:
    def send_task(self, name: str, **kwargs: object) -> None:
        raise ConnectionError("broker down")


class Setup:
    def __init__(self) -> None:
        self.handler: Handler = lambda request: httpx.Response(200)
        self.producer: TaskProducer = RecordingProducer()
        self.web = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)

    @property
    def recorded(self) -> list[int]:
        return cast(RecordingProducer, self.producer).check_ids


@pytest.fixture
def setup() -> Setup:
    return Setup()


@pytest.fixture
def client(setup: Setup) -> Iterator[TestClient]:
    app.dependency_overrides[get_http_client] = lambda: setup.web
    app.dependency_overrides[get_task_producer] = lambda: setup.producer
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def run_check(client: TestClient):
    created = client.post("/monitors", json={"url": "a.test", "interval": "5m"})
    return client.post(f"/monitors/{created.json()['id']}/checks")


def test_failed_check_queues_a_notification_with_its_id(
    client: TestClient, setup: Setup
) -> None:
    setup.handler = lambda request: httpx.Response(503)

    response = run_check(client)

    assert response.status_code == 201
    assert setup.recorded == [response.json()["id"]]


def test_network_failure_queues_a_notification(
    client: TestClient, setup: Setup
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    setup.handler = refuse

    response = run_check(client)

    assert response.json()["ok"] is False
    assert setup.recorded == [response.json()["id"]]


def test_successful_check_queues_nothing(client: TestClient, setup: Setup) -> None:
    assert run_check(client).status_code == 201

    assert setup.recorded == []


def test_broken_broker_does_not_fail_the_request(
    client: TestClient, setup: Setup
) -> None:
    # Проверка уже записана: отказывать клиенту из-за очереди значило бы потерять
    # и ответ, и сообщение о сбое сайта.
    setup.handler = lambda request: httpx.Response(503)
    setup.producer = CeleryTaskProducer(cast(Celery, BrokenApp()))

    response = run_check(client)

    assert response.status_code == 201
    assert response.json()["ok"] is False


def test_without_a_broker_the_api_still_works(client: TestClient, setup: Setup) -> None:
    from pulse.tasks.producer import NullTaskProducer

    setup.handler = lambda request: httpx.Response(503)
    setup.producer = NullTaskProducer()

    assert run_check(client).status_code == 201
