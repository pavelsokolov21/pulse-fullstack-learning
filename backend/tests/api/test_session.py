"""Жизненный цикл сессии базы в запросе: когда коммит, когда откат."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy import text

from pulse.api.deps import SessionDep
from pulse.api.main import create_app
from pulse.api.settings import ApiSettings

INSERT_USER = text(
    "INSERT INTO users (email, password_hash, role)"
    " VALUES ('probe@example.com', 'h', 'viewer')"
)
Db = psycopg.Connection[tuple[object, ...]]


def probe_app() -> FastAPI:
    """Приложение с тремя пробными маршрутами поверх настоящей зависимости сессии."""
    app = create_app(ApiSettings(**dict[str, Any]()))

    @app.post("/probe/ok")
    async def ok(session: SessionDep) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        await session.execute(INSERT_USER)
        return {"status": "done"}

    @app.post("/probe/fail")
    async def fail(session: SessionDep) -> None:  # pyright: ignore[reportUnusedFunction]
        await session.execute(INSERT_USER)
        raise RuntimeError("после записи что-то пошло не так")

    @app.post("/probe/commit-fails")
    async def commit_fails(session: SessionDep) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        await session.execute(INSERT_USER)

        async def broken_commit() -> None:
            raise RuntimeError("коммит не удался")

        session.commit = broken_commit
        return {"status": "done"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(probe_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def users_count(db: Db) -> int:
    row = db.execute("SELECT count(*) FROM users").fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


def test_successful_request_is_committed(client: TestClient, admin_db: Db) -> None:
    assert client.post("/probe/ok").status_code == status.HTTP_200_OK

    # Читает другое соединение: значит, запись зафиксирована, а не висит в транзакции.
    assert users_count(admin_db) == 1


def test_failed_request_is_rolled_back(client: TestClient, admin_db: Db) -> None:
    response = client.post("/probe/fail")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert users_count(admin_db) == 0


def test_commit_happens_before_the_response_is_sent(
    client: TestClient, admin_db: Db
) -> None:
    # Если коммит падает, клиент не должен получить успех: с `scope="request"`
    # (по умолчанию) коммит шёл бы уже после отправки 200.
    response = client.post("/probe/commit-fails")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "done" not in response.text
    assert users_count(admin_db) == 0


def test_requests_do_not_share_a_session(client: TestClient, admin_db: Db) -> None:
    client.post("/probe/fail")

    # Предыдущий запрос откатился, следующий начинает с чистой сессии.
    assert client.post("/probe/ok").status_code == status.HTTP_200_OK
    assert users_count(admin_db) == 1
