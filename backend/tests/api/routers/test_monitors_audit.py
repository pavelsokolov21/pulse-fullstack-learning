"""Журнал аудита через API: настоящие коммиты, связь с request-id, атомарность."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import app

Db = psycopg.Connection[tuple[object, ...]]

# Триггер ломает запись журнала уже внутри транзакции изменения. Так проверяется
# настоящая атомарность: монитор и запись журнала либо оба сохранены, либо оба нет.
BREAK_AUDIT = """
CREATE FUNCTION audit_boom() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'audit is broken'; END $$;
CREATE TRIGGER audit_boom BEFORE INSERT ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_boom();
"""
FIX_AUDIT = "DROP TRIGGER audit_boom ON audit_log; DROP FUNCTION audit_boom();"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def failing_client() -> Iterator[TestClient]:
    # 500 нужен как ответ, а не как исключение внутри теста.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def broken_audit(admin_db: Db) -> Iterator[None]:
    admin_db.execute(BREAK_AUDIT)
    try:
        yield
    finally:
        admin_db.execute(FIX_AUDIT)


def rows(db: Db, sql: str) -> list[tuple[object, ...]]:
    return db.execute(sql).fetchall()  # type: ignore[arg-type]


def post(client: TestClient, url: str = "a.test"):
    return client.post("/monitors", json={"url": url, "interval": "5m", "timeout": 3})


def test_create_writes_the_entry_in_the_same_commit(
    client: TestClient, admin_db: Db
) -> None:
    created = post(client).json()

    entries = rows(
        admin_db, "SELECT action, entity_type, entity_id, actor_id FROM audit_log"
    )
    assert entries == [
        ("monitor.create", "monitor", created["id"], created["owner_id"])
    ]


def test_entry_carries_the_request_id_of_the_response(
    client: TestClient, admin_db: Db
) -> None:
    response = post(client)

    [(request_id,)] = rows(admin_db, "SELECT request_id FROM audit_log")
    assert request_id == response.headers["x-request-id"]


def test_update_and_delete_are_logged(client: TestClient, admin_db: Db) -> None:
    monitor_id = post(client).json()["id"]

    assert (
        client.patch(f"/monitors/{monitor_id}", json={"timeout": 4}).status_code == 200
    )
    assert client.delete(f"/monitors/{monitor_id}").status_code == 204

    actions = [r[0] for r in rows(admin_db, "SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["monitor.create", "monitor.update", "monitor.delete"]


def test_failed_requests_leave_no_entries(client: TestClient, admin_db: Db) -> None:
    post(client)

    assert post(client).status_code == 409  # дубликат
    assert client.patch("/monitors/999", json={"timeout": 4}).status_code == 404
    assert client.delete("/monitors/999").status_code == 404
    assert client.post("/monitors", json={"nonsense": 1}).status_code == 422

    assert rows(admin_db, "SELECT count(*) FROM audit_log") == [(1,)]  # только create


def test_create_is_rolled_back_when_the_audit_write_fails(
    failing_client: TestClient, admin_db: Db, broken_audit: None
) -> None:
    response = post(failing_client)

    assert response.status_code == 500
    assert rows(admin_db, "SELECT count(*) FROM monitors") == [(0,)]
    assert rows(admin_db, "SELECT count(*) FROM audit_log") == [(0,)]


def test_update_is_rolled_back_when_the_audit_write_fails(
    failing_client: TestClient, admin_db: Db
) -> None:
    monitor_id = post(failing_client).json()["id"]
    admin_db.execute(BREAK_AUDIT)
    try:
        response = failing_client.patch(f"/monitors/{monitor_id}", json={"timeout": 4})
    finally:
        admin_db.execute(FIX_AUDIT)

    assert response.status_code == 500
    assert rows(admin_db, "SELECT timeout_seconds FROM monitors") == [(3.0,)]


def test_delete_is_rolled_back_when_the_audit_write_fails(
    failing_client: TestClient, admin_db: Db
) -> None:
    monitor_id = post(failing_client).json()["id"]
    admin_db.execute(BREAK_AUDIT)
    try:
        response = failing_client.delete(f"/monitors/{monitor_id}")
    finally:
        admin_db.execute(FIX_AUDIT)

    assert response.status_code == 500
    assert rows(admin_db, "SELECT count(*) FROM monitors") == [(1,)]
