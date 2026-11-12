"""Ручка журнала задач для страницы «Фоновые задачи»."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from pulse.api.deps import get_task_journal
from pulse.api.main import app
from pulse.domain.notifications import TaskEvent, TaskState

AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


class FakeJournal:
    def __init__(self, *events: TaskEvent) -> None:
        self.events = list(events)
        self.limits: list[int] = []

    async def record(self, event: TaskEvent) -> None:
        self.events.insert(0, event)

    async def recent(self, limit: int) -> list[TaskEvent]:
        self.limits.append(limit)
        return self.events[:limit]


@pytest.fixture
def journal() -> FakeJournal:
    return FakeJournal()


@pytest.fixture
def client(journal: FakeJournal) -> Iterator[TestClient]:
    app.dependency_overrides[get_task_journal] = lambda: journal
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_task_journal, None)


def test_empty_journal_returns_an_empty_list(client: TestClient) -> None:
    response = client.get("/tasks/events")

    assert response.status_code == 200
    assert response.json() == []


def test_events_are_returned_as_the_journal_gave_them(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.events = [
        TaskEvent(7, TaskState.RETRY, AT, 2, "вебхук ответил 503", 3.5),
        TaskEvent(7, TaskState.QUEUED, AT),
    ]

    body = client.get("/tasks/events").json()

    assert body == [
        {
            "check_id": 7,
            "state": "retry",
            "at": "2026-05-01T12:00:00Z",
            "attempt": 2,
            "detail": "вебхук ответил 503",
            "retry_in": 3.5,
        },
        {
            "check_id": 7,
            "state": "queued",
            "at": "2026-05-01T12:00:00Z",
            "attempt": None,
            "detail": None,
            "retry_in": None,
        },
    ]


def test_limit_is_passed_to_the_journal(
    client: TestClient, journal: FakeJournal
) -> None:
    client.get("/tasks/events", params={"limit": 5})

    assert journal.limits == [5]


def test_default_limit_is_one_hundred(client: TestClient, journal: FakeJournal) -> None:
    client.get("/tasks/events")

    assert journal.limits == [100]


@pytest.mark.parametrize("limit", ["0", "-1", "301", "abc"])
def test_invalid_limit_returns_422(client: TestClient, limit: str) -> None:
    assert client.get("/tasks/events", params={"limit": limit}).status_code == 422


def test_maximum_limit_is_accepted(client: TestClient, journal: FakeJournal) -> None:
    assert client.get("/tasks/events", params={"limit": 300}).status_code == 200
    assert journal.limits == [300]
