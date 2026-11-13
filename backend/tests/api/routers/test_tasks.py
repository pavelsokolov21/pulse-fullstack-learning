"""Журнал задач для страницы «Фоновые задачи»: снимок и поток SSE."""

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import pulse.api.routers.tasks as tasks_router
from pulse.api.deps import get_task_journal
from pulse.api.main import app
from pulse.domain.notifications import (
    JournalEntry,
    JournalUnavailableError,
    TaskEvent,
    TaskState,
)

AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def entry(
    number: int, state: TaskState = TaskState.QUEUED, check_id: int = 7
) -> JournalEntry:
    return JournalEntry(f"{number}-0", TaskEvent(check_id, state, AT))


class FakeJournal:
    """Журнал в памяти. `batches`: что по очереди вернёт `after` (живые события)."""

    def __init__(self, *snapshot: JournalEntry) -> None:
        self.snapshot = list(snapshot)  # новые первыми, как у настоящего
        self.batches: list[list[JournalEntry] | Exception] = []
        self.latest_limits: list[int] = []
        self.after_cursors: list[str] = []

    async def record(self, event: TaskEvent) -> None:
        raise NotImplementedError

    async def latest(self, limit: int) -> list[JournalEntry]:
        self.latest_limits.append(limit)
        return self.snapshot[:limit]

    async def after(self, entry_id: str, block_ms: int) -> list[JournalEntry]:
        self.after_cursors.append(entry_id)
        await asyncio.sleep(0.005)
        if not self.batches:
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


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


# --- снимок -----------------------------------------------------------------


def test_empty_journal_returns_an_empty_list(client: TestClient) -> None:
    response = client.get("/tasks/events")

    assert response.status_code == 200
    assert response.json() == []


def test_events_are_returned_as_the_journal_gave_them(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.snapshot = [
        JournalEntry(
            "2-0", TaskEvent(7, TaskState.RETRY, AT, 2, "вебхук ответил 503", 3.5)
        ),
        entry(1),
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

    assert journal.latest_limits == [5]


def test_default_limit_is_one_hundred(client: TestClient, journal: FakeJournal) -> None:
    client.get("/tasks/events")

    assert journal.latest_limits == [100]


@pytest.mark.parametrize("limit", ["0", "-1", "301", "abc"])
def test_invalid_limit_returns_422(client: TestClient, limit: str) -> None:
    assert client.get("/tasks/events", params={"limit": limit}).status_code == 422


def test_maximum_limit_is_accepted(client: TestClient, journal: FakeJournal) -> None:
    assert client.get("/tasks/events", params={"limit": 300}).status_code == 200
    assert journal.latest_limits == [300]


# --- поток SSE --------------------------------------------------------------

Frame = dict[str, str]


@pytest.fixture(autouse=True)
def short_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Поток закрывается сам через долю секунды: тест читает его целиком."""
    monkeypatch.setattr(tasks_router, "MAX_STREAM_SECONDS", 0.3)
    monkeypatch.setattr(tasks_router, "KEEPALIVE_SECONDS", 3600.0)


def parse(body: str) -> list[Frame]:
    """Кадры потока: поля `event`/`id`/`data` или `comment`."""
    frames: list[Frame] = []
    for block in body.split("\n\n"):
        if not block:
            continue
        frame: Frame = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                frame["comment"] = line[1:].strip()
            else:
                name, _, value = line.partition(": ")
                frame[name] = value
        frames.append(frame)
    return frames


def events(frames: list[Frame]) -> list[Frame]:
    return [f for f in frames if "event" in f]


def test_stream_has_the_sse_headers(client: TestClient) -> None:
    response = client.get("/tasks/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Без no-transform прокси со сжатием копит ответ, и события идут пачками.
    assert "no-cache" in response.headers["cache-control"]
    assert "no-transform" in response.headers["cache-control"]
    assert response.headers["x-accel-buffering"] == "no"


def test_stream_opens_with_a_comment_so_headers_are_flushed(
    client: TestClient,
) -> None:
    frames = parse(client.get("/tasks/stream").text)

    assert frames[0] == {"comment": "stream open"}


def test_new_subscriber_gets_the_snapshot_oldest_first(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.snapshot = [entry(3, TaskState.SENT), entry(2, TaskState.STARTED), entry(1)]

    frames = events(parse(client.get("/tasks/stream").text))

    assert [f["id"] for f in frames] == ["1-0", "2-0", "3-0"]
    assert [json.loads(f["data"])["state"] for f in frames] == [
        "queued",
        "started",
        "sent",
    ]


def test_frame_carries_the_event_name_the_id_and_json_data(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.snapshot = [
        JournalEntry(
            "9-3", TaskEvent(7, TaskState.RETRY, AT, 1, "вебхук ответил 503", 2.5)
        )
    ]

    (frame,) = events(parse(client.get("/tasks/stream").text))

    assert frame["event"] == "task"
    assert frame["id"] == "9-3"
    assert json.loads(frame["data"]) == {
        "check_id": 7,
        "state": "retry",
        "at": "2026-05-01T12:00:00Z",
        "attempt": 1,
        "detail": "вебхук ответил 503",
        "retry_in": 2.5,
    }


def test_live_events_follow_the_snapshot_and_advance_the_cursor(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.snapshot = [entry(1)]
    journal.batches = [[entry(2, TaskState.STARTED)], [entry(3, TaskState.SENT)]]

    frames = events(parse(client.get("/tasks/stream").text))

    assert [f["id"] for f in frames] == ["1-0", "2-0", "3-0"]
    # Чтение начинается после снимка и сдвигается за каждой записью.
    assert journal.after_cursors[:3] == ["1-0", "2-0", "3-0"]


def test_empty_journal_is_read_from_the_beginning(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.batches = [[entry(1)]]

    frames = events(parse(client.get("/tasks/stream").text))

    assert journal.after_cursors[0] == "0-0"
    assert [f["id"] for f in frames] == ["1-0"]


def test_reconnect_resumes_after_last_event_id_without_a_snapshot(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.snapshot = [entry(2), entry(1)]  # в снимок не должно попасть ничего
    journal.batches = [[entry(3)]]

    frames = events(
        parse(client.get("/tasks/stream", headers={"Last-Event-ID": "2-0"}).text)
    )

    assert journal.latest_limits == []
    assert journal.after_cursors[0] == "2-0"
    assert [f["id"] for f in frames] == ["3-0"]


@pytest.mark.parametrize("garbage", ["abc", "1", "1-", "-1", "1-0; DROP", "$", ""])
def test_malformed_last_event_id_is_ignored_and_a_snapshot_is_sent(
    client: TestClient, journal: FakeJournal, garbage: str
) -> None:
    journal.snapshot = [entry(1)]

    frames = events(
        parse(client.get("/tasks/stream", headers={"Last-Event-ID": garbage}).text)
    )

    # Чужую строку в Redis не передаём: заголовок приходит от клиента.
    assert frames[0]["id"] == "1-0"
    assert journal.after_cursors[0] == "1-0"


def test_stream_ends_by_itself_after_the_time_limit(client: TestClient) -> None:
    # Токен проверяется один раз, при подключении. Закрытие по сроку и переподключение
    # с новым токеном ограничивают жизнь отозванного доступа.
    response = client.get("/tasks/stream")

    assert response.status_code == 200  # дошли до конца, а не зависли


def test_quiet_stream_sends_keepalive_comments(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks_router, "KEEPALIVE_SECONDS", 0.05)

    frames = parse(client.get("/tasks/stream").text)

    assert [f for f in frames if f.get("comment") == "ping"]


def test_stream_with_events_does_not_need_keepalive(
    client: TestClient, journal: FakeJournal
) -> None:
    journal.batches = [[entry(n)] for n in range(1, 4)]

    frames = parse(client.get("/tasks/stream").text)

    assert not [f for f in frames if f.get("comment") == "ping"]


def test_unavailable_journal_closes_the_stream_instead_of_failing(
    client: TestClient, journal: FakeJournal, caplog: pytest.LogCaptureFixture
) -> None:
    journal.batches = [[entry(1)], JournalUnavailableError()]

    with caplog.at_level(logging.WARNING, logger="pulse.tasks"):
        response = client.get("/tasks/stream")

    # Заголовки уже ушли: статус 200, прочитанное осталось, дальше переподключение.
    assert response.status_code == 200
    assert [f["id"] for f in events(parse(response.text))] == ["1-0"]
    assert "Журнал задач недоступен, поток закрыт" in caplog.text
