"""GET /monitors: у каждого монитора в списке есть его последняя проверка."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import app

Db = psycopg.Connection[tuple[object, ...]]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def monitor(client: TestClient, url: str) -> int:
    response = client.post("/monitors", json={"url": url, "interval": "5m"})
    assert response.status_code == 201
    return int(response.json()["id"])


def add_check(
    db: Db, monitor_id: int, checked_at: str, status: int | None = 200
) -> int:
    row = db.execute(
        "INSERT INTO checks (monitor_id, url, status_code, duration_ms, error,"
        " checked_at) VALUES (%s, 'https://a.test', %s, 12.5, %s, %s) RETURNING id",
        (
            monitor_id,
            status,
            None if status is not None else "Network Error: ConnectError",
            checked_at,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


def test_monitor_without_checks_has_null_last_check(client: TestClient) -> None:
    monitor(client, "a.test")

    items = client.get("/monitors").json()["items"]

    assert items[0]["last_check"] is None


def test_list_item_carries_the_newest_check(client: TestClient, admin_db: Db) -> None:
    monitor_id = monitor(client, "a.test")
    add_check(admin_db, monitor_id, "2026-01-01T10:00:00Z")
    newest = add_check(admin_db, monitor_id, "2026-01-01T12:00:00Z", status=503)
    add_check(admin_db, monitor_id, "2026-01-01T11:00:00Z")

    last = client.get("/monitors").json()["items"][0]["last_check"]

    assert last["id"] == newest
    assert last["monitor_id"] == monitor_id
    assert last["status_code"] == 503
    assert last["ok"] is False
    assert last["latency_ms"] == 12.5
    assert last["checked_at"].startswith("2026-01-01T12:00:00")


def test_failed_check_without_response_keeps_the_error(
    client: TestClient, admin_db: Db
) -> None:
    monitor_id = monitor(client, "a.test")
    add_check(admin_db, monitor_id, "2026-01-01T10:00:00Z", status=None)

    last = client.get("/monitors").json()["items"][0]["last_check"]

    assert last["status_code"] is None
    assert last["error"] == "Network Error: ConnectError"
    assert last["ok"] is False


def test_each_monitor_gets_its_own_last_check(client: TestClient, admin_db: Db) -> None:
    first = monitor(client, "a.test")
    second = monitor(client, "b.test")
    first_check = add_check(admin_db, first, "2026-01-01T10:00:00Z")
    second_check = add_check(admin_db, second, "2026-01-01T09:00:00Z", status=404)

    items = {m["id"]: m for m in client.get("/monitors").json()["items"]}

    assert items[first]["last_check"]["id"] == first_check
    assert items[second]["last_check"]["id"] == second_check
